"""Chunked-prefill student trace: same scores without a full-length prefill.

P100 (no flash) OOMs on a 16k full prefill inside the model's own SDPA
(7.98 GiB ask). This module builds the KV cache in `prefill_block` chunks
with capture armed: if the backend passes past+current keys, the final block
directly yields row 0 (last prompt query) with no replay; otherwise tracing fails rather than replaying a token into a completed cache.
Single-query score capture is small; model attention memory still depends on
both block size and accumulated prefix length. The lookahead loop is unchanged.

Returns the same dict shape as `Student.trace` plus `scores`,
`replayed_last_token`, and `seen_key_lens` for equivalence checks (P2).
"""

from __future__ import annotations

import copy
import math
import time

from .core import importance, select_positions, token_hash


def trace_chunked(model, torch, stop_ids, ids, keep_ratio, prefill_block=8192,
                  lookahead=8, pool_kernel=13, chunk_size=32, device="cuda", prefix_state=None):
    from transformers.modeling_utils import ALL_ATTENTION_FUNCTIONS

    if lookahead < 1:
        raise ValueError("lookahead counts query rows and must be positive")
    if prefill_block < 1:
        raise ValueError("prefill_block must be positive")
    original = ALL_ATTENTION_FUNCTIONS["sdpa"]
    current = []
    rows, layers, sampled = [], [], []
    seen_key_lens = []
    prompt_length = len(ids)

    def record(module, query, key):
        keys = key[:, :, :prompt_length].repeat_interleave(
            query.shape[1] // key.shape[1], dim=1
        )
        logits = query[:, :, -1:] @ keys.transpose(-1, -2)
        logits = logits / math.sqrt(query.shape[-1])
        current.append((module.layer_idx, logits.squeeze(0)))

    def capture_prefill(module, query, key, value, attention_mask, scaling=None, **kwargs):
        seen_key_lens.append(int(key.shape[2]))
        # Final block only: backend passes past+current keys covering the prompt.
        if int(key.shape[2]) == prompt_length and int(query.shape[2]) >= 1:
            record(module, query, key)
        return original(module, query, key, value, attention_mask, scaling=scaling, **kwargs)

    def capture_decode(module, query, key, value, attention_mask, scaling=None, **kwargs):
        # Generated lookahead query attends to the unchanged prompt keys.
        record(module, query, key)
        return original(module, query, key, value, attention_mask, scaling=scaling, **kwargs)

    if device == "cuda":
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    reused = 0
    past = None
    if prefix_state is not None:
        previous = prefix_state.get("ids", [])
        if previous and len(previous) < len(ids) and ids[:len(previous)] == previous:
            reused = len(previous)
            past = prefix_state["cache"]
        prefix_state.clear()  # Fail closed if any model/capture operation fails.
    ALL_ATTENTION_FUNCTIONS.register("sdpa", capture_prefill)
    try:
        with torch.inference_mode():
            blocks = [ids[i:i + prefill_block] for i in range(reused, len(ids), prefill_block)]
            output = None
            for b in blocks:
                inputs = torch.tensor([b], dtype=torch.long, device=device)
                output = model(
                    input_ids=inputs,
                    past_key_values=output.past_key_values if output is not None else past,
                    use_cache=True,
                    logits_to_keep=1,
                )
            # Lookahead/decodes use the unconditional capture from here on.
            ALL_ATTENTION_FUNCTIONS.register("sdpa", capture_decode)
            replayed = False
            if not current:
                raise RuntimeError("Backend did not capture the last prompt query; token replay is unsafe")
            if prefix_state is not None:
                # Hybrid recurrent state cannot be cropped after speculative lookahead.
                # Keep authoritative prompt state, and mutate a separate branch for drafts.
                prefix_state.update(ids=list(ids), cache=output.past_key_values)
                output.past_key_values = copy.deepcopy(output.past_key_values)
            for _ in range(lookahead):
                layer_ids = [i for i, _ in current]
                if not current or (layers and layer_ids != layers):
                    raise RuntimeError("Missing or inconsistent full-attention Q/K capture")
                layers = layer_ids
                rows.append(torch.stack([lg for _, lg in current]))
                current.clear()
                token = output.logits[:, -1:].argmax(-1)
                sampled.append(token.item())
                if len(sampled) >= lookahead or sampled[-1] in stop_ids:
                    break
                output = model(
                    input_ids=token,
                    past_key_values=output.past_key_values,
                    use_cache=True,
                    logits_to_keep=1,
                )
            logits = torch.cat(rows, dim=2)
            scores = importance(logits, pool_kernel)
            positions = select_positions(scores, keep_ratio, chunk_size)
            if device == "cuda":
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
                "peak_student_allocator_mib": torch.cuda.max_memory_allocated() / 2**20 if device == "cuda" else None,
                "score_hash": token_hash(scores.float().tolist()),
                "scores": scores.float().tolist(),
                "prefill_block": prefill_block,
                "reused_prefix_tokens": reused,
                "prefill_tokens": len(ids) - reused,
                "method": "chunked-prefill",
                "replayed_last_token": replayed,
                "seen_key_lens": sorted(set(seen_key_lens)),
            }
    except BaseException:
        if prefix_state is not None:
            prefix_state.clear()
        raise
    finally:
        ALL_ATTENTION_FUNCTIONS.register("sdpa", original)
