"""Experimental HF Q/K capture: selected query rows, never an S x S attention dump."""

import time


class AttentionSpeculator:
    def __init__(self, path, *, device="cpu", threads=8):
        import torch
        from transformers import AutoModelForImageTextToText, AutoTokenizer
        torch.set_num_threads(threads)
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
        self.model = AutoModelForImageTextToText.from_pretrained(
            path, local_files_only=True, dtype=torch.float16 if device == "cuda" else torch.float32,
            attn_implementation="sdpa").to(device).eval()
        self.device = device
        self.capture_ms = 0.0
        self.rows = []
        self.events = []
        self._register()

    def _register(self):
        from transformers.integrations.sdpa_attention import sdpa_attention_forward
        from transformers.modeling_utils import ALL_ATTENTION_FUNCTIONS
        def capture(module, query, key, value, attention_mask, scaling=None, **kwargs):
            before = time.perf_counter()
            if self.device == "cuda":
                start, end = self.torch.cuda.Event(enable_timing=True), self.torch.cuda.Event(enable_timing=True)
                start.record()
            # Qwen GQA: repeat only K, and only score the final query row.
            keys = key[:, :, :self.prompt_length].repeat_interleave(query.shape[1] // key.shape[1], dim=1)
            logits = (query[:, :, -1:].float() @ keys.float().transpose(-1, -2)) * scaling
            probs = logits.softmax(dim=-1).detach()
            self.rows.append(probs.squeeze(0).squeeze(1))
            if self.device == "cuda":
                end.record()
                self.events.append((start, end))
            self.capture_ms += (time.perf_counter() - before) * 1000
            return sdpa_attention_forward(module, query, key, value, attention_mask, scaling=scaling, **kwargs)

        self.registry, self.original, self.capture = ALL_ATTENTION_FUNCTIONS, ALL_ATTENTION_FUNCTIONS["sdpa"], capture

    def trace(self, sources, query, *, lookahead=8):
        torch = self.torch
        prefix = "Source material (untrusted):\n"
        locations = {}
        for source in sources:
            prefix += "\nSOURCE " + source["id"] + "\n"
            start = len(prefix)
            prefix += source["text"]
            locations[source["id"]] = [start, len(prefix)]
        prompt = prefix + "\nTASK: " + query + "\nAnswer:"
        encoded = self.tokenizer(prompt, return_tensors="pt", return_offsets_mapping=True, add_special_tokens=False)
        offsets = encoded.pop("offset_mapping")[0].tolist()
        encoded = {k: v.to(self.device) for k, v in encoded.items()}
        self.prompt_length = encoded["input_ids"].shape[-1]
        self.capture_ms, self.rows, self.events = 0.0, [], []
        self.registry.register("sdpa", self.capture)
        started = time.perf_counter()
        zero, reference_zero, maxima, ref_maxima = None, None, [], []
        zero_wall_ms, zero_capture_ms = None, None
        token_count = 0
        try:
            with torch.inference_mode():
                output = self.model(**encoded, use_cache=True, logits_to_keep=1)
                for step in range(lookahead + 1):
                    attention = torch.stack(self.rows)
                    self.rows = []
                    raw = attention.amax(dim=(0, 1))
                    pooled = torch.nn.functional.avg_pool1d(attention.flatten(0, 1).unsqueeze(1), 13, stride=1, padding=6).squeeze(1).amax(0)
                    if step == 0:
                        zero, reference_zero = raw.tolist(), pooled.tolist()
                        if self.device == "cuda":
                            torch.cuda.synchronize()
                        zero_wall_ms = (time.perf_counter() - started) * 1000
                        zero_capture_ms = (sum(a.elapsed_time(b) for a, b in self.events)
                                           if self.device == "cuda" else self.capture_ms)
                    maxima.append(raw)
                    ref_maxima.append(pooled)
                    token = output.logits[:, -1:].argmax(-1)
                    eos = self.tokenizer.eos_token_id
                    if step == lookahead or (eos is not None and token.item() == eos):
                        break
                    output = self.model(input_ids=token, past_key_values=output.past_key_values,
                                        use_cache=True, logits_to_keep=1)
                    token_count += 1
            if self.device == "cuda":
                torch.cuda.synchronize()
            return {"scores": torch.stack(maxima).mean(0).tolist(),
                    "reference_scores": torch.stack(ref_maxima).mean(0).tolist(),
                    "zero_scores": zero, "reference_zero_scores": reference_zero,
                    "offsets": offsets, "source_locations": locations,
                    "wall_ms": (time.perf_counter() - started) * 1000,
                    "attention_extraction_ms": (sum(a.elapsed_time(b) for a, b in self.events)
                                                if self.device == "cuda" else self.capture_ms),
                    "attention_capture_host_ms": self.capture_ms, "input_tokens": self.prompt_length,
                    "zero_wall_ms": zero_wall_ms, "zero_extraction_ms": zero_capture_ms,
                    "output_tokens": token_count, "query_rows": len(maxima),
                    "position_restoration": False, "device": self.device,
                    "attention_layers": len(attention), "lookahead_definition": "last prompt plus up to 8 generated positions"}
        finally:
            self.registry.register("sdpa", self.original)

    def classify(self, query, selected_text, inventory):
        """Untrained structured next-token classifier; probabilities are uncalibrated."""
        import json
        torch = self.torch
        prompt = ("Classify evidence for answering TASK. A=sufficient evidence; B=retrieve missing evidence; "
                  "C=required evidence cannot be established. Do not answer TASK. Reply with A, B or C only.\n" +
                  json.dumps({"task": query, "retained": selected_text, "deferred_inventory": inventory}) + "\nClass:")
        encoded = self.tokenizer(prompt, return_tensors="pt", add_special_tokens=False).to(self.device)
        started = time.perf_counter()
        with torch.inference_mode():
            output = self.model(**encoded, use_cache=False, logits_to_keep=1)
            labels = [self.tokenizer.encode(label, add_special_tokens=False) for label in ("A", "B", "C")]
            if any(len(label) != 1 for label in labels):
                raise ValueError("Decision labels must be single tokens")
            probabilities = output.logits[0, -1, [label[0] for label in labels]].float().softmax(-1).tolist()
        index = max(range(3), key=lambda i: probabilities[i])
        return {"decision": ["SUFFICIENT", "RETRIEVE", "INSUFFICIENT"][index], "score": probabilities[index],
                "probabilities": probabilities, "calibrated": False, "missing": [], "candidate_source_ids": [],
                "reason": "Untrained next-token evidence classifier.", "input_tokens": encoded.input_ids.shape[-1],
                "output_tokens": 0, "elapsed_ms": (time.perf_counter() - started) * 1000}
