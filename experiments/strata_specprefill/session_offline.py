"""Per-turn session offline sweep: chunked student trace for every probed turn.

Each turn prompt is an independent cumulative history (no KV reuse across
turns — matches the cache-off baseline that future caching work compares
against). Saves per-turn scores/signals/positions at 30% + adaptive rate.

Usage (student venv, P100):
  .../.venv/bin/python -m experiments.strata_specprefill.session_offline
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RATES = [0.30, 1.0]


def main() -> None:
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--sets", type=Path, default=ROOT / "data/strata_specprefill/session-sets-v1.json")
    ap.add_argument("--output", type=Path,
                    default=ROOT / "data/strata_specprefill/session-offline-v1.json")
    ap.add_argument("--student", default="/media/distilledoreo/Storage/AI/state-projection/models/Qwen3.5-0.8B")
    ap.add_argument("--block", type=int, default=4096)
    args = ap.parse_args()

    import torch
    from transformers import AutoModelForImageTextToText, AutoTokenizer

    from experiments.strata_specprefill.adaptive_signals import (
        adaptive_rate_by_mass,
        signals_from_scores,
    )
    from experiments.strata_specprefill.chunked_student import trace_chunked
    from experiments.strata_specprefill.core import select_positions, token_hash

    sessions = json.loads(args.sets.read_text())
    torch.set_num_threads(8)
    stok = AutoTokenizer.from_pretrained(args.student, local_files_only=True)
    model = AutoModelForImageTextToText.from_pretrained(
        args.student, local_files_only=True, dtype=torch.float16,
        attn_implementation="sdpa").to("cuda").eval()
    eos = model.generation_config.eos_token_id
    stop_ids = set(eos if isinstance(eos, list) else [eos])
    stop_ids.add(stok.eos_token_id)

    out = []
    for ses in sessions:
        for t in ses["turns"]:
            ids = t["input_ids"]
            block = args.block if len(ids) > args.block else None
            if block is not None:
                tr = trace_chunked(model, torch, stop_ids, ids, 1.0, prefill_block=block)
                scores = tr["scores"]
                sel_s, peak = tr["student_selection_s"], tr["peak_student_allocator_mib"]
                method = "chunked"
            else:
                # Short turn: reuse the standard full-forward trace path via longctx helper.
                import math

                from transformers.modeling_utils import ALL_ATTENTION_FUNCTIONS

                from experiments.strata_specprefill import longctx_offline as _  # noqa: F401
                from experiments.strata_specprefill.core import importance
                original = ALL_ATTENTION_FUNCTIONS["sdpa"]
                cur, rows = [], []
                pl = len(ids)

                def cap(module, q, k, v, mask, scaling=None, **kw):
                    keys = k[:, :, :pl].repeat_interleave(q.shape[1] // k.shape[1], dim=1)
                    cur.append((module.layer_idx,
                                ((q[:, :, -1:] @ keys.transpose(-1, -2))
                                 / math.sqrt(q.shape[-1])).squeeze(0)))
                    return original(module, q, k, v, mask, scaling=scaling, **kw)

                ALL_ATTENTION_FUNCTIONS.register("sdpa", cap)
                import time
                torch.cuda.synchronize()
                torch.cuda.reset_peak_memory_stats()
                t0 = time.perf_counter()
                try:
                    with torch.inference_mode():
                        outp = model(input_ids=torch.tensor([ids], dtype=torch.long,
                                                            device="cuda"),
                                     use_cache=True, logits_to_keep=1)
                        samp = []
                        for _ in range(8):
                            rows.append(torch.stack([lg for _, lg in cur]))
                            cur.clear()
                            tk = outp.logits[:, -1:].argmax(-1)
                            samp.append(tk.item())
                            if len(samp) >= 8 or samp[-1] in stop_ids:
                                break
                            outp = model(input_ids=tk,
                                         past_key_values=outp.past_key_values,
                                         use_cache=True, logits_to_keep=1)
                        scores_t = importance(torch.cat(rows, dim=2), 13)
                        torch.cuda.synchronize()
                        scores = scores_t.float().tolist()
                        sel_s = time.perf_counter() - t0
                        peak = torch.cuda.max_memory_allocated() / 2**20
                finally:
                    ALL_ATTENTION_FUNCTIONS.register("sdpa", original)
                method = "full"
            sig = signals_from_scores(scores, 32)
            arate = adaptive_rate_by_mass(sig, 0.80)
            per_rate = {}
            for r in (0.30, arate if arate != 0.30 else 1.0):
                pos = select_positions(torch.tensor(scores, dtype=torch.float16), r, 32)
                per_rate[str(r)] = {"retained_tokens": len(pos), "positions": pos}
            if "0.3" not in per_rate:
                pos = select_positions(torch.tensor(scores, dtype=torch.float16), 0.30, 32)
                per_rate["0.3"] = {"retained_tokens": len(pos), "positions": pos}
            out.append({
                "session": ses["id"], "after_msg": t["after_msg"], "answer": t["answer"],
                "evidence": t["evidence"], "input_tokens": len(ids),
                "input_hash": token_hash(ids), "method": method,
                "adaptive_rate": arate, "student_s": sel_s, "peak_mib": peak,
                "signals": {k: v for k, v in sig.items() if k != "sorted_means"},
                "per_rate": per_rate, "score_hash": token_hash(scores),
            })
            print(json.dumps({"session": ses["id"], "after_msg": t["after_msg"],
                              "tokens": len(ids), "method": method,
                              "adapt": arate, "student_s": round(sel_s, 2)}), flush=True)
    args.output.write_text(json.dumps(out, indent=2))
    print(f"saved {args.output} turns={len(out)}", flush=True)


if __name__ == "__main__":
    main()
