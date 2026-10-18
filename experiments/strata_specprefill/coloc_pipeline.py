"""P3 pilot: measured pipeline TTFT with both models resident (8k, P100).

Server: target-only 16k-ctx config with 8192 MiB reserve (shrunk expert
cache leaves room for the student). Same process loads the student, runs a
chunked trace (block 4096, peak ~3.2GB), then immediately posts the selected
request. Timing is client-start (before student work) to server first-token:
a true measured pipeline number replacing the sequential estimate.

Usage (student venv, P100):
  .../.venv/bin/python -m experiments.strata_specprefill.coloc_pipeline --port 18589
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

from .run import Server  # noqa: E402


def post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url + "/v1/strata/generate",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)


def main() -> None:
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--port", type=int, default=18589)
    ap.add_argument("--config", type=Path,
                    default=ROOT / "data/strata_specprefill/longctx-coloc-alone.json")
    ap.add_argument("--cases", nargs="+",
                    default=["held-large_read-8k", "held-adversarial-8k"])
    args = ap.parse_args()

    import torch
    from transformers import AutoModelForImageTextToText, AutoTokenizer

    from experiments.strata_specprefill.chunked_student import trace_chunked

    sets = json.load(open(ROOT / "data/strata_specprefill/longctx-sets-v1.json"))
    by_id = {c["id"]: c for s in ("dev", "held") for c in sets[s]}
    outdir = ROOT / "data/strata_specprefill/coloc-pipeline-8k-v1"
    outdir.mkdir(parents=True, exist_ok=False)
    url = f"http://127.0.0.1:{args.port}"

    class A:
        pass
    a = A()
    a.port = args.port
    a.url = url
    a.server_python = Path("/home/distilledoreo/AI/Strata/.venv/bin/python")
    a.strata_root = Path("/home/distilledoreo/AI/Strata")
    a.output = outdir

    SP = "/media/distilledoreo/Storage/AI/state-projection/models/Qwen3.5-0.8B"
    torch.set_num_threads(8)
    stok = AutoTokenizer.from_pretrained(SP, local_files_only=True)
    print("loading student...", flush=True)
    model = AutoModelForImageTextToText.from_pretrained(
        SP, local_files_only=True, dtype=torch.float16,
        attn_implementation="sdpa").to("cuda").eval()
    eos = model.generation_config.eos_token_id
    stop_ids = set(eos if isinstance(eos, list) else [eos])
    stop_ids.add(stok.eos_token_id)
    torch.cuda.synchronize()
    print(f"student resident, allocated={torch.cuda.memory_allocated() / 2**20:.0f}MiB",
          flush=True)

    import sys
    sys.path.insert(0, "/home/distilledoreo/AI/Strata/tools")
    sys.path.insert(0, "/home/distilledoreo/AI/Strata")
    import strata_tokenizer as stt
    tdir = Path("/home/distilledoreo/AI/Strata-data/packs/swift-iq3_s/tokenizer")
    vocab = json.loads((tdir / "vocab.json").read_text())
    tarr = [None] * len(vocab)
    for k, v in vocab.items():
        tarr[v] = k
    ttok = stt.Tokenizer(tarr, (tdir / "merges.txt").read_text().split("\n"),
                         json.loads((tdir / "token_type.json").read_text()))

    rows = []
    with Server(a, args.config, "coloc"):
        post(url, {"token_ids": by_id[args.cases[0]]["input_ids"][:512],
                   "max_tokens": 4, "sampling": {"temperature": 0}})
        for cid in args.cases:
            case = by_id[cid]
            ids = case["input_ids"]
            t0 = time.perf_counter()  # pipeline start: before student work
            tr = trace_chunked(model, torch, stop_ids, ids, 0.30, prefill_block=4096)
            pos = tr["positions"]
            resp = post(url, {"token_ids": [ids[p] for p in pos], "position_ids": pos,
                              "original_prompt_length": len(ids),
                              "max_tokens": 8, "sampling": {"temperature": 0}})
            from .core import validate_selected_response
            validate_selected_response(ids, pos, resp)
            first = resp["timings"]["first_token_monotonic_s"]
            txt = ttok.decode(resp["token_ids"])
            clean = re.sub(r"<\|[^>]+?\|>", "", txt)
            rows.append({
                "case": cid, "arm": "keep30-colocated",
                "original_tokens": len(ids), "retained_tokens": len(pos),
                "student_s": tr["student_selection_s"],
                "server_ttft_s": resp["timings"]["ttft_s"],
                "measured_pipeline_ttft_s": first - t0,
                "correct": re.findall(r"\d{5}", clean) == [case["answer"]],
                "decoded": txt,
            })
            print(json.dumps({k: rows[-1][k] for k in (
                "case", "retained_tokens", "student_s", "server_ttft_s",
                "measured_pipeline_ttft_s", "correct")}), flush=True)
    (outdir / "requests.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (outdir / "COMPLETE.json").write_text(json.dumps({"requests": len(rows)}))
    print(f"DONE {len(rows)}", flush=True)


if __name__ == "__main__":
    main()
