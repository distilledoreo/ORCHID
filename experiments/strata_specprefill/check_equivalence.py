"""P2: validate chunked prefill does not alter selection rankings (8k).

Loads the student once, runs a full forward trace and a chunked trace on the
same 8k prompt, and compares layers, query rows, sampled drafts, score
correlation, and selected positions at every rate. Exit nonzero on mismatch.

Usage (student venv, P100):
  .../.venv/bin/python -m experiments.strata_specprefill.check_equivalence
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RATES = [0.05, 0.10, 0.20, 0.30, 0.50, 0.75, 1.0]


def full_scores(model, torch, stop_ids, ids, lookahead=8, pool_kernel=13):
    from transformers.modeling_utils import ALL_ATTENTION_FUNCTIONS

    from experiments.strata_specprefill.core import importance

    original = ALL_ATTENTION_FUNCTIONS["sdpa"]
    current, rows, layers, sampled = [], [], [], []
    prompt_length = len(ids)

    def capture(module, query, key, value, attention_mask, scaling=None, **kwargs):
        keys = key[:, :, :prompt_length].repeat_interleave(
            query.shape[1] // key.shape[1], dim=1)
        logits = query[:, :, -1:] @ keys.transpose(-1, -2) / math.sqrt(query.shape[-1])
        current.append((module.layer_idx, logits.squeeze(0)))
        return original(module, query, key, value, attention_mask, scaling=scaling, **kwargs)

    ALL_ATTENTION_FUNCTIONS.register("sdpa", capture)
    try:
        with torch.inference_mode():
            output = model(input_ids=torch.tensor([ids], dtype=torch.long, device="cuda"),
                           use_cache=True, logits_to_keep=1)
            for _ in range(lookahead):
                lids = [i for i, _ in current]
                if not current or (layers and lids != layers):
                    raise RuntimeError("capture failed")
                layers = lids
                rows.append(torch.stack([lg for _, lg in current]))
                current.clear()
                t = output.logits[:, -1:].argmax(-1)
                sampled.append(t.item())
                if len(sampled) >= lookahead or sampled[-1] in stop_ids:
                    break
                output = model(input_ids=t, past_key_values=output.past_key_values,
                               use_cache=True, logits_to_keep=1)
            scores = importance(torch.cat(rows, dim=2), pool_kernel)
            return scores.float().tolist(), layers, len(rows), sampled
    finally:
        ALL_ATTENTION_FUNCTIONS.register("sdpa", original)


def main() -> None:
    import torch
    from transformers import AutoModelForImageTextToText, AutoTokenizer

    from experiments.strata_specprefill.chunked_student import trace_chunked
    from experiments.strata_specprefill.core import select_positions

    data = json.load(open(ROOT / "data/strata_specprefill/longctx-sets-v1.json"))
    case = next(c for c in data["dev"] if c["id"] == "dev-large_read-8k")
    ids = case["input_ids"]
    print(f"case {case['id']} tokens={len(ids)}", flush=True)

    SP = "/media/distilledoreo/Storage/AI/state-projection/models/Qwen3.5-0.8B"
    torch.set_num_threads(8)
    stok = AutoTokenizer.from_pretrained(SP, local_files_only=True)
    model = AutoModelForImageTextToText.from_pretrained(
        SP, local_files_only=True, dtype=torch.float16,
        attn_implementation="sdpa").to("cuda").eval()
    eos = model.generation_config.eos_token_id
    stop_ids = set(eos if isinstance(eos, list) else [eos])
    stop_ids.add(stok.eos_token_id)

    t0 = time.perf_counter()
    fscores, flayers, frows, fsampled = full_scores(model, torch, stop_ids, ids)
    ft = time.perf_counter() - t0
    fpeak = torch.cuda.max_memory_allocated() / 2**20
    print(f"full: rows={frows} layers={flayers} sampled={fsampled} t={ft:.2f}s peak={fpeak:.0f}MiB",
          flush=True)

    c = trace_chunked(model, torch, stop_ids, ids, 1.0, prefill_block=4096)
    print(f"chunked: rows={c['query_rows']} layers={c['attention_layer_ids']} "
          f"sampled={c['draft_sampled_ids']} t={c['student_selection_s']:.2f}s "
          f"peak={c['peak_student_allocator_mib']:.0f}MiB", flush=True)

    assert c["attention_layer_ids"] == flayers, "layer mismatch"
    assert c["query_rows"] == frows, "query-row mismatch"
    assert c["draft_sampled_ids"] == fsampled, "draft mismatch"

    import statistics
    diffs = [abs(a - b) for a, b in zip(fscores, c["scores"])]
    mf = statistics.mean(fscores)
    print(f"score max_abs_diff={max(diffs):.3e} mean_abs_diff={statistics.mean(diffs):.3e} "
          f"score_mean={mf:.4f}", flush=True)

    print(f"chunked path: replayed={c['replayed_last_token']} seen_key_lens={c['seen_key_lens']}",
          flush=True)
    st = torch.tensor(fscores, dtype=torch.float16)
    sc = torch.tensor(c["scores"], dtype=torch.float16)
    mismatches = 0
    for r in RATES:
        pf = select_positions(st, r, 32)
        pc = select_positions(sc, r, 32)
        spf, spc = set(pf), set(pc)
        jacc = len(spf & spc) / len(spf | spc)
        same = pf == pc
        mismatches += not same
        print(f"rate {r}: full_ret={len(pf)} chunked_ret={len(pc)} equal={same} "
              f"jaccard={jacc:.4f} overlap={len(spf & spc)}/{len(spf | spc)}", flush=True)
    # Evidence check: does the answer span survive under both selections?
    data2 = json.load(open(ROOT / "data/strata_specprefill/longctx-offline-8k-dev-v1.json"))
    ref = next(x for x in data2["results"] if x["id"] == case["id"])
    print(f"answer_span_retained@30 (full-trace reference): "
          f"{ref['per_rate']['0.3']['answer_span_fully_retained']}", flush=True)
    print(f"score len full={len(fscores)} chunked={len(c['scores'])}", flush=True)
    if mismatches == 0 and max(diffs) < 1e-2:
        print("EQUIVALENCE OK (exact)", flush=True)
    else:
        print(f"EQUIVALENCE PARTIAL: {mismatches} rate(s) differ by position; "
              f"see jaccard above; exact equivalence not established", flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
