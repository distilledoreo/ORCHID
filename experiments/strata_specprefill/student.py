"""Capture post-RoPE Q/K rows from the student's six full-attention layers."""

import math
import time

from .core import importance, select_positions, token_hash, validate_input


class Student:
    def __init__(self, path, allowed_ids, device="cuda"):
        import torch
        from transformers import AutoModelForImageTextToText, AutoTokenizer

        torch.set_num_threads(8)
        self.torch = torch
        self.device = device
        self.allowed_ids = allowed_ids
        self.tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
        self.model = (
            AutoModelForImageTextToText.from_pretrained(
                path, local_files_only=True, dtype=torch.float16, attn_implementation="sdpa"
            )
            .to(device)
            .eval()
        )
        eos = self.model.generation_config.eos_token_id
        self.stop_ids = set(eos if isinstance(eos, list) else [eos])
        self.stop_ids.add(self.tokenizer.eos_token_id)

    def trace(self, ids, keep_ratio, lookahead=8, pool_kernel=13, chunk_size=32, return_scores=False):
        from transformers.modeling_utils import ALL_ATTENTION_FUNCTIONS

        validate_input(ids, self.allowed_ids)
        if lookahead < 1:
            raise ValueError("lookahead counts query rows and must be positive")
        torch = self.torch
        original = ALL_ATTENTION_FUNCTIONS["sdpa"]
        current = []
        rows, layers, sampled = [], [], []
        capture_events = []
        prompt_length = len(ids)

        def capture(module, query, key, value, attention_mask, scaling=None, **kwargs):
            if self.device == "cuda":
                start, end = (
                    torch.cuda.Event(enable_timing=True),
                    torch.cuda.Event(enable_timing=True),
                )
                start.record()
            keys = key[:, :, :prompt_length].repeat_interleave(
                query.shape[1] // key.shape[1], dim=1
            )
            # Match upstream: matmul in model dtype, divide by sqrt(head_dim), then FP32 softmax.
            logits = query[:, :, -1:] @ keys.transpose(-1, -2)
            logits = logits / math.sqrt(query.shape[-1])
            current.append((module.layer_idx, logits.squeeze(0)))
            if self.device == "cuda":
                end.record()
                capture_events.append((start, end))
            return original(module, query, key, value, attention_mask, scaling=scaling, **kwargs)

        if self.device == "cuda":
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        ALL_ATTENTION_FUNCTIONS.register("sdpa", capture)
        try:
            with torch.inference_mode():
                inputs = torch.tensor([ids], dtype=torch.long, device=self.device)
                output = self.model(input_ids=inputs, use_cache=True, logits_to_keep=1)
                for step in range(lookahead):
                    layer_ids = [i for i, _ in current]
                    if not current or (layers and layer_ids != layers):
                        raise RuntimeError("Missing or inconsistent full-attention Q/K capture")
                    layers = layer_ids
                    rows.append(torch.stack([logits for _, logits in current]))
                    current.clear()
                    token = output.logits[:, -1:].argmax(-1)
                    sampled.append(token.item())
                    if step + 1 == lookahead or sampled[-1] in self.stop_ids:
                        break
                    output = self.model(
                        input_ids=token,
                        past_key_values=output.past_key_values,
                        use_cache=True,
                        logits_to_keep=1,
                    )
                # [layers, heads, query rows, keys]. Generated keys are deliberately excluded.
                logits = torch.cat(rows, dim=2)
                scores = importance(logits, pool_kernel)
                positions = select_positions(scores, keep_ratio, chunk_size)
                if self.device == "cuda":
                    torch.cuda.synchronize()
                elapsed = time.perf_counter() - started
                return {
                    "positions": positions,
                    "input_hash": token_hash(ids),
                    "input_tokens": len(ids),
                    "query_rows": len(rows),
                    "attention_layer_ids": layers,
                    "draft_sampled_ids": sampled,
                    "student_selection_s": elapsed,
                    "qk_capture_gpu_ms": sum(a.elapsed_time(b) for a, b in capture_events),
                    "peak_student_allocator_mib": torch.cuda.max_memory_allocated() / 2**20
                    if self.device == "cuda"
                    else None,
                    "score_hash": token_hash(scores.float().tolist()),
                    **({"scores": scores.float().tolist()} if return_scores else {}),
                }
        finally:
            ALL_ATTENTION_FUNCTIONS.register("sdpa", original)
