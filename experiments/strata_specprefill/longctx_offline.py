"""Long-context offline sweep: one student forward per prompt, all rates + evidence audit.

No target calls. Saves per-rate positions for later sequential live validation.
Usage (student venv, P100):
  .../.venv/bin/python -m experiments.strata_specprefill.longctx_offline --length 8 --split dev
Lengths: 8, 16, 32 (k tokens budget filter). Splits: dev, held, both.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RATES = [0.05, 0.10, 0.20, 0.30, 0.50, 0.75, 1.0]


def answer_token_spans(ids: list[int], answer: str, tok) -> list[list[int]]:
    """Find token-subsequence occurrences of the answer string in decoded text.

    Returns list of [start,end) index ranges over ids whose decode contains answer.
    Cheap exact check: encode answer alone, then search ID subsequence.
    """
    try:
        ans_ids = tok.encode(answer, parse_special=False)
    except Exception:
        return []
    if not ans_ids:
        return []
    spans = []
    for i in range(len(ids) - len(ans_ids) + 1):
        if ids[i : i + len(ans_ids)] == ans_ids:
            spans.append([i, i + len(ans_ids)])
    return spans


def main() -> None:
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--length", type=int, default=8, choices=(8, 16, 32))
    ap.add_argument("--split", default="dev", choices=("dev", "held", "both"))
    ap.add_argument("--sets", type=Path, default=ROOT / "data/strata_specprefill/longctx-sets-v1.json")
    ap.add_argument("--output", type=Path, default=None)
    ap.add_argument("--student", default="/media/distilledoreo/Storage/AI/state-projection/models/Qwen3.5-0.8B")
    ap.add_argument("--chunked-block", type=int, default=0,
                    help="If >0, chunked prefill with this block size (P1: avoids full-length prefill OOM)")
    args = ap.parse_args()

    import torch
    from transformers import AutoModelForImageTextToText, AutoTokenizer
    from transformers.modeling_utils import ALL_ATTENTION_FUNCTIONS

    from experiments.strata_specprefill.adaptive_signals import signals_from_scores
    from experiments.strata_specprefill.core import select_positions, token_hash

    data = json.loads(args.sets.read_text())
    splits = ("dev", "held") if args.split == "both" else (args.split,)
    tag = f"{args.length}k-{args.split}"
    if args.output is None:
        args.output = ROOT / f"data/strata_specprefill/longctx-offline-{tag}-v1.json"

    torch.set_num_threads(8)
    stok = AutoTokenizer.from_pretrained(args.student, local_files_only=True)
    model = (
        AutoModelForImageTextToText.from_pretrained(
            args.student, local_files_only=True, dtype=torch.float16,
            attn_implementation="sdpa",
        ).to("cuda").eval()
    )
    eos = model.generation_config.eos_token_id
    stop_ids = set(eos if isinstance(eos, list) else [eos])
    stop_ids.add(stok.eos_token_id)
    original = ALL_ATTENTION_FUNCTIONS["sdpa"]

    cases = [c for s in splits for c in data[s] if c["budget"] == args.length * 1024]
    print(f"cases: {len(cases)} at {args.length}k chunked_block={args.chunked_block or 'off'}",
          flush=True)
    use_chunked = args.chunked_block > 0
    if use_chunked:
        from experiments.strata_specprefill.chunked_student import trace_chunked
    results = []
    for case in cases:
        ids: list[int] = case["input_ids"]
        prompt_len = len(ids)
        if use_chunked:
            t = trace_chunked(model, torch, stop_ids, ids, 1.0,
                              prefill_block=args.chunked_block)
            sig = signals_from_scores(t["scores"], 32)
            per_rate = {}
            for r in RATES:
                pos = select_positions(torch.tensor(t["scores"], dtype=torch.float16), r, 32)
                posset = set(pos)
                spans = answer_token_spans(ids, case["answer"], stok)
                covered = (any(all(i in posset for i in range(a, b)) for a, b in spans)
                           if spans else None)
                per_rate[str(r)] = {"retained_tokens": len(pos), "positions": pos,
                                    "answer_span_fully_retained": covered}
            results.append({
                "id": case["id"], "kind": case.get("kind"), "answer": case["answer"],
                "input_tokens": prompt_len, "input_hash": token_hash(ids),
                "query_rows": t["query_rows"], "attention_layers": t["attention_layer_ids"],
                "student_selection_s": t["student_selection_s"],
                "peak_mib": t["peak_student_allocator_mib"],
                "answer_spans_found": len(spans),
                "signals": {k: v for k, v in sig.items() if k != "sorted_means"},
                "per_rate": per_rate, "score_hash": t["score_hash"],
                "method": "chunked-prefill", "prefill_block": args.chunked_block,
                "replayed": t["replayed_last_token"],
            })
            print(json.dumps({"id": case["id"], "tokens": prompt_len,
                              "q": t["query_rows"],
                              "student_s": round(t["student_selection_s"], 2),
                              "peak_mib": round(t["peak_student_allocator_mib"]),
                              "spans": len(spans),
                              "retain": {str(r): per_rate[str(r)]["retained_tokens"] for r in RATES},
                              "cover30": per_rate["0.3"]["answer_span_fully_retained"]}),
                  flush=True)
            continue
        current: list = []
        layers: list = []
        rows: list = []
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()

        def capture(module, query, key, value, attention_mask, scaling=None, **kwargs):
            keys = key[:, :, :prompt_len].repeat_interleave(
                query.shape[1] // key.shape[1], dim=1)
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
                for _ in range(8):
                    lids = [i for i, _ in current]
                    if not current or (layers and lids != layers):
                        raise RuntimeError("Missing Q/K capture")
                    layers = lids
                    rows.append(torch.stack([lg for _, lg in current]))
                    current.clear()
                    t = output.logits[:, -1:].argmax(-1)
                    sampled.append(t.item())
                    if len(sampled) >= 8 or sampled[-1] in stop_ids:
                        break
                    output = model(input_ids=t, past_key_values=output.past_key_values,
                                   use_cache=True, logits_to_keep=1)
                from experiments.strata_specprefill.core import importance
                scores = importance(torch.cat(rows, dim=2), 13)
                torch.cuda.synchronize()
                elapsed = time.perf_counter() - started
        finally:
            ALL_ATTENTION_FUNCTIONS.register("sdpa", original)
        peak = torch.cuda.max_memory_allocated() / 2**20
        slist = scores.float().tolist()
        sig = signals_from_scores(slist, 32)
        spans = answer_token_spans(ids, case["answer"], stok)
        # NOTE: stok is the student tokenizer; target-tokenizer span check happens
        # live with the target tokenizer. This is a cheap proxy, reported as such.
        per_rate = {}
        for r in RATES:
            pos = select_positions(scores, r, 32)
            posset = set(pos)
            covered = any(all(i in posset for i in range(a, b)) for a, b in spans) if spans else None
            per_rate[str(r)] = {"retained_tokens": len(pos), "positions": pos,
                                "answer_span_fully_retained": covered}
        results.append({
            "id": case["id"], "kind": case.get("kind"), "answer": case["answer"],
            "input_tokens": prompt_len, "input_hash": token_hash(ids),
            "query_rows": len(rows), "attention_layers": layers,
            "student_selection_s": elapsed, "peak_mib": peak,
            "answer_spans_found": len(spans),
            "signals": {k: v for k, v in sig.items() if k != "sorted_means"},
            "per_rate": per_rate, "score_hash": token_hash(slist),
        })
        print(json.dumps({"id": case["id"], "tokens": prompt_len, "q": len(rows),
                          "student_s": round(elapsed, 2), "peak_mib": round(peak),
                          "spans": len(spans),
                          "retain": {str(r): per_rate[str(r)]["retained_tokens"] for r in RATES},
                          "cover30": per_rate["0.3"]["answer_span_fully_retained"]}), flush=True)
    args.output.write_text(json.dumps({"length_k": args.length, "split": args.split,
                                       "rates": RATES, "results": results}, indent=2))
    print(f"saved {args.output}", flush=True)


if __name__ == "__main__":
    main()
