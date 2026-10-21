"""Offline single-trace sweep: one student forward per prompt, simulate all rates.

No Strata target calls. Computes importance scores once, then
select_positions() for 5/10/20/30/50/75/100% plus cheap signals.
Run in the student venv (torch 2.7.1+cu126) on P100:
  /media/.../data/specprefill_repurpose/.venv/bin/python -m experiments.strata_specprefill.adaptive_offline
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETS = ROOT / "data/strata_specprefill/adaptive-sets-v1.json"
DEFAULT_OUT = ROOT / "data/strata_specprefill/adaptive-offline-v1.json"
STUDENT_PATH = "/media/distilledoreo/Storage/AI/state-projection/models/Qwen3.5-0.8B"
RATES = [0.05, 0.10, 0.20, 0.30, 0.50, 0.75, 1.0]


def main() -> None:
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--sets", type=Path, default=DEFAULT_SETS)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--student", default=STUDENT_PATH)
    ap.add_argument("--split", default="dev", choices=("dev", "held", "both"))
    args = ap.parse_args()

    import torch
    from transformers import AutoModelForImageTextToText, AutoTokenizer
    from transformers.modeling_utils import ALL_ATTENTION_FUNCTIONS

    from experiments.strata_specprefill.adaptive_signals import signals_from_scores
    from experiments.strata_specprefill.core import select_positions, token_hash

    data = json.loads(args.sets.read_text())
    splits = ("dev", "held") if args.split == "both" else (args.split,)

    torch.set_num_threads(8)
    tok = AutoTokenizer.from_pretrained(args.student, local_files_only=True)
    model = (
        AutoModelForImageTextToText.from_pretrained(
            args.student, local_files_only=True, dtype=torch.float16,
            attn_implementation="sdpa",
        )
        .to("cuda")
        .eval()
    )
    eos = model.generation_config.eos_token_id
    stop_ids = set(eos if isinstance(eos, list) else [eos])
    stop_ids.add(tok.eos_token_id)
    original = ALL_ATTENTION_FUNCTIONS["sdpa"]

    results: list[dict] = []
    first_call_s: float | None = None
    for split in splits:
        for case in data[split]:
            ids: list[int] = case["input_ids"]
            prompt_len = len(ids)
            current: list = []
            layers: list = []
            rows: list = []
            started_total = time.perf_counter()
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()

            def capture(module, query, key, value, attention_mask, scaling=None, **kwargs):
                keys = key[:, :, :prompt_len].repeat_interleave(
                    query.shape[1] // key.shape[1], dim=1
                )
                logits = query[:, :, -1:] @ keys.transpose(-1, -2)
                logits = logits / math.sqrt(query.shape[-1])
                current.append((module.layer_idx, logits.squeeze(0)))
                return original(module, query, key, value, attention_mask, scaling=scaling, **kwargs)

            ALL_ATTENTION_FUNCTIONS.register("sdpa", capture)
            try:
                started = time.perf_counter()
                with torch.inference_mode():
                    inputs = torch.tensor([ids], dtype=torch.long, device="cuda")
                    output = model(input_ids=inputs, use_cache=True, logits_to_keep=1)
                    sampled = []
                    # Same as Student.trace: at most 8 total query rows incl last prompt.
                    for _ in range(8):
                        layer_ids = [i for i, _ in current]
                        if not current or (layers and layer_ids != layers):
                            raise RuntimeError("Missing Q/K capture")
                        layers = layer_ids
                        rows.append(torch.stack([lg for _, lg in current]))
                        current.clear()
                        token = output.logits[:, -1:].argmax(-1)
                        sampled.append(token.item())
                        if len(sampled) >= 8 or sampled[-1] in stop_ids:
                            break
                        output = model(
                            input_ids=token, past_key_values=output.past_key_values,
                            use_cache=True, logits_to_keep=1,
                        )
                    from experiments.strata_specprefill.core import importance

                    logits4d = torch.cat(rows, dim=2)
                    scores = importance(logits4d, 13)
                    torch.cuda.synchronize()
                    elapsed = time.perf_counter() - started
            finally:
                ALL_ATTENTION_FUNCTIONS.register("sdpa", original)
            if first_call_s is None:
                first_call_s = elapsed
            scores_list = scores.float().tolist()
            sig = signals_from_scores(scores_list, 32)
            per_rate = {}
            for r in RATES:
                pos = select_positions(scores, r, 32)
                per_rate[str(r)] = {"retained_tokens": len(pos), "positions": pos}
            results.append(
                {
                    "split": split,
                    "id": case["id"],
                    "kind": case.get("kind"),
                    "answer": case["answer"],
                    "input_tokens": prompt_len,
                    "input_hash": token_hash(ids),
                    "query_rows": len(rows),
                    "attention_layers": layers,
                    "student_selection_s": elapsed,
                    "peak_mib": torch.cuda.max_memory_allocated() / 2**20,
                    "signals": {k: v for k, v in sig.items() if k != "sorted_means"},
                    "sorted_means_head": sig["sorted_means"][:8],
                    "per_rate": per_rate,
                    "score_hash": token_hash(scores_list),
                    "total_s": time.perf_counter() - started_total,
                }
            )
            print(
                json.dumps(
                    {
                        "split": split, "id": case["id"],
                        "tokens": prompt_len, "query_rows": len(rows),
                        "student_s": round(elapsed, 3),
                        "retain": {str(r): per_rate[str(r)]["retained_tokens"] for r in RATES},
                        "gini": round(sig["gini"], 3),
                        "top30": round(sig["top_mass"]["0.3"], 3),
                    }
                ),
                flush=True,
            )
    args.output.write_text(json.dumps(
        {"rates": RATES, "first_call_s": first_call_s, "results": results}, indent=2,
    ))
    print(f"saved {args.output} n={len(results)} first_call_s={first_call_s:.2f}")


if __name__ == "__main__":
    main()
