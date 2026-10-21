"""Sequential long-context live validation (P100, target-only server).

Student positions are frozen from longctx_offline (no student resident:
8k student needs ~7GB, colocated with target does not fit). Each live
request sends exact target IDs + frozen positions; pipeline TTFT is
reported as offline_student_s + live_server_ttft_s (sequential estimate,
documented as such — not a colocated measurement).

Usage:
  .../.venv/bin/python -m experiments.strata_specprefill.longctx_live --length 8 --split dev
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

from .core import validate_frozen_selection, validate_selected_response

ROOT = Path(__file__).resolve().parents[2]

from .run import Server  # noqa: E402


def post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url + "/v1/strata/generate",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)


def correct(case: dict, text: str) -> bool:
    clean = re.sub(r"<\|[^>]+?\|>", "", text)
    return re.findall(r"\d{5}", clean) == [case["answer"]]


def main() -> None:
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--length", type=int, default=8, choices=(8, 16, 32))
    ap.add_argument("--split", default="dev", choices=("dev", "held"))
    ap.add_argument("--config", type=Path,
                    default=ROOT / "data/strata_specprefill/longctx-8k-alone.json")
    ap.add_argument("--port", type=int, default=18588)
    ap.add_argument("--adaptive-mass", type=float, default=0.80)
    ap.add_argument("--sets", type=Path,
                    default=ROOT / "data/strata_specprefill/longctx-sets-v1.json")
    ap.add_argument("--offline", type=Path, default=None,
                    help="Offline sweep file holding frozen per-rate positions")
    ap.add_argument("--output", type=Path, default=None)
    args = ap.parse_args()

    from experiments.strata_specprefill.adaptive_signals import adaptive_rate_by_mass

    sets = json.load(open(args.sets))
    if args.offline is None:
        args.offline = (ROOT / f"data/strata_specprefill/longctx-offline-"
                        f"{args.length}k-{args.split}-v1.json")
    off = json.load(open(args.offline))
    off_by_id = {r["id"]: r for r in off["results"]}
    cases = [c for c in sets[args.split] if c["budget"] == args.length * 1024]
    if args.output is None:
        args.output = ROOT / f"data/strata_specprefill/longctx-live-{args.length}k-{args.split}-v1"
    outdir = args.output
    outdir.mkdir(parents=True, exist_ok=False)
    args.url = f"http://127.0.0.1:{args.port}"

    reqs: list[dict] = []

    class A:  # minimal namespace for Server
        pass
    a = A()
    a.port = args.port
    a.url = args.url
    a.server_python = Path("/home/distilledoreo/AI/Strata/.venv/bin/python")
    a.strata_root = Path("/home/distilledoreo/AI/Strata")
    a.output = outdir

    with Server(a, args.config, "longctx"):
        # warmup: smallest case full prefill, excluded
        post(args.url, {"token_ids": cases[0]["input_ids"], "max_tokens": 8,
                         "sampling": {"temperature": 0}})
        (outdir / "warmup.json").write_text(json.dumps({"case": cases[0]["id"]}))
        plan = []
        for c in cases:
            o = off_by_id[c["id"]]
            arate = adaptive_rate_by_mass(o["signals"], args.adaptive_mass)
            plan.append((c, "full", None))
            plan.append((c, "keep30", 0.30))
            plan.append((c, f"adapt{arate * 100:g}", arate))
        import random
        random.Random(731).shuffle(plan)
        for case, arm, rate in plan:
            ids = case["input_ids"]
            o = off_by_id[case["id"]]
            validate_frozen_selection(ids, o)
            body = {"token_ids": ids, "max_tokens": 8, "sampling": {"temperature": 0}}
            if rate is not None:
                pos = o["per_rate"][str(rate)]["positions"]
                body.update(token_ids=[ids[p] for p in pos], position_ids=pos,
                            original_prompt_length=len(ids))
            t0 = time.perf_counter()
            resp = post(args.url, body)
            t1 = time.perf_counter()
            if rate is not None:
                validate_selected_response(ids, pos, resp)
            # target tokenizer decode happens server-side in text? use raw IDs via strata tokenizer
            text = resp.get("text", "")
            if not text:
                # fallback: decode via response token_ids with target tokenizer files
                text = ""
            row = {
                "case": case["id"], "arm": arm, "rate": rate,
                "original_tokens": len(ids),
                "retained_tokens": len(body["token_ids"]),
                "output_ids": resp["token_ids"],
                "output_text": resp.get("text", ""),
                "server_ttft_s": resp["timings"]["ttft_s"],
                "student_s": o["student_selection_s"] if rate is not None else 0,
                "pipeline_seq_ttft_s": (o["student_selection_s"] if rate is not None else 0)
                + resp["timings"]["ttft_s"],
                "wall_s": t1 - t0,
                "peak_student_mib": o["peak_mib"],
            }
            row["correct"] = None  # scored below with the target tokenizer
            reqs.append(row)
            print(json.dumps({k: row[k] for k in ("case", "arm", "retained_tokens",
                                                 "server_ttft_s", "pipeline_seq_ttft_s")}), flush=True)
    # Score with the real target tokenizer (exact code match after control-strip).
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
    by_id = {c["id"]: c for c in cases}
    for row in reqs:
        txt = ttok.decode(row["output_ids"])
        row["decoded"] = txt
        row["correct"] = correct(by_id[row["case"]], txt)
    (outdir / "requests.jsonl").write_text("\n".join(json.dumps(r) for r in reqs) + "\n")
    (outdir / "COMPLETE.json").write_text(json.dumps({"requests": len(reqs)}))
    acc = {}
    for r in reqs:
        acc.setdefault(r["arm"], []).append(r["correct"])
    print({k: f"{sum(v)}/{len(v)}" for k, v in acc.items()}, flush=True)
    print(f"DONE {len(reqs)}", flush=True)


if __name__ == "__main__":
    main()
