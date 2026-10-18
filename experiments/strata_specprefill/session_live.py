"""Per-turn session live runner: full vs fixed-30% vs adaptive at every turn.

Cache-off baseline for future caching comparisons: each turn is an
independent request with its cumulative history (no KV reuse across turns).
Frozen offline positions; greedy max 8 tokens; target-tokenizer scoring.

Usage (any venv with stdlib only + running server deps):
  .../.venv/bin/python -m experiments.strata_specprefill.session_live \
    --port 18592 --config .../longctx-16kctx-alone.json
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


def post(url: str, body: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(url + "/v1/strata/generate",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def main() -> None:
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--port", type=int, default=18592)
    ap.add_argument("--config", type=Path,
                    default=ROOT / "data/strata_specprefill/longctx-16kctx-alone.json")
    ap.add_argument("--sets", type=Path,
                    default=ROOT / "data/strata_specprefill/session-sets-v1.json")
    ap.add_argument("--offline", type=Path,
                    default=ROOT / "data/strata_specprefill/session-offline-v1.json")
    ap.add_argument("--output", type=Path,
                    default=ROOT / "data/strata_specprefill/session-live-v1")
    ap.add_argument("--only", default=None,
                    help="Subset rerun filter: 'SID:AFTER,...' (e.g. 'ses2-task_shift:17').")
    args = ap.parse_args()
    only = None
    if args.only:
        only = set()
        for item in args.only.split(","):
            sid, after = item.split(":")
            only.add((sid.strip(), int(after)))

    sets = {s["id"]: s for s in json.loads(args.sets.read_text())}
    off = {(o["session"], o["after_msg"]): o
           for o in json.loads(args.offline.read_text())}
    args.output.mkdir(parents=True, exist_ok=False)
    url = f"http://127.0.0.1:{args.port}"

    class A:
        pass
    a = A()
    a.port = args.port
    a.url = url
    a.server_python = Path("/home/distilledoreo/AI/Strata/.venv/bin/python")
    a.strata_root = Path("/home/distilledoreo/AI/Strata")
    a.output = args.output

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

    def score(turn: dict, txt: str) -> bool:
        clean = re.sub(r"<\|[^>]+?\|>", "", txt).strip()
        ans = turn["answer"]
        if ans is None:
            return True
        if turn.get("kind") == "import-line":
            # Exact line required: a bare module echo must fail.
            lines = [ln.strip() for ln in clean.splitlines() if ln.strip()]
            return lines[:1] == [str(ans)]
        if turn.get("kind") == "module-name":
            # Exact module name required (v3 provenance probe).
            return clean == str(ans)
        if re.fullmatch(r"\d{5}", str(ans)):
            return re.findall(r"\d{5}", clean) == [str(ans)]
        if re.fullmatch(r"\d+", str(ans)):
            nums = re.findall(r"\d+", clean)
            return nums[:1] == [str(ans)]
        return str(ans) in clean

    def ev_retained(turn: dict, body_ids: list[int]) -> dict:
        # Evidence présence via target-tokenizer decode of the retained IDs.
        text = ttok.decode(body_ids)
        return {e: (str(e) in text) for e in (turn["evidence"] or [])}

    with Server(a, args.config, "session"):
        first_sid = next(iter(sets))
        warm = post(url, {"token_ids": sets[first_sid]["turns"][0]["input_ids"][:256],
                          "max_tokens": 4, "sampling": {"temperature": 0}})
        del warm
        (args.output / "warmup.json").write_text(json.dumps({"ok": True}))
        from experiments.strata_specprefill.retention_policy import decide, enforce_floor
        plan = []
        for sid, ses in sets.items():
            for t in ses["turns"]:
                if only is not None and (sid, t["after_msg"]) not in only:
                    continue
                o = off[(sid, t["after_msg"])]
                validate_frozen_selection(t["input_ids"], o)
                plan.append((sid, t, o, "full", None))
                plan.append((sid, t, o, "keep30", 0.30))
                arm, rate = decide(len(t["input_ids"]), o["adaptive_rate"])
                if rate is not None:
                    retained = o["per_rate"][str(rate)]["retained_tokens"]
                    arm, rate = enforce_floor(retained, rate)
                if rate is not None:
                    plan.append((sid, t, o, f"policy-{arm}", rate))
                elif rate is None:
                    plan.append((sid, t, o, "policy-full", None))
        import random
        random.Random(731).shuffle(plan)
        rows = []
        for sid, turn, o, arm, rate in plan:
            ids = turn["input_ids"]
            # Module names need more than the 8-token number budget (v3 t25
            # truncated 'ubusnyf_321_a' to 'ubusnyf_321' on every arm).
            max_tokens = 16 if turn.get("kind") == "module-name" else 8
            body = {"token_ids": ids, "max_tokens": max_tokens,
                    "sampling": {"temperature": 0}}
            if rate is not None:
                pos = o["per_rate"][str(rate)]["positions"]
                body.update(token_ids=[ids[p] for p in pos], position_ids=pos,
                            original_prompt_length=len(ids))
            t0 = time.perf_counter()
            resp = post(url, body)
            t1 = time.perf_counter()
            if rate is not None:
                validate_selected_response(ids, pos, resp)
            txt = ttok.decode(resp["token_ids"])
            row = {
                "session": sid, "after_msg": turn["after_msg"], "arm": arm, "rate": rate,
                "original_tokens": len(ids), "retained_tokens": len(body["token_ids"]),
                "output_ids": resp["token_ids"], "decoded": txt,
                "correct": score(turn, txt),
                "evidence_retained": ev_retained(turn, body["token_ids"]),
                "server_ttft_s": resp["timings"]["ttft_s"],
                "student_s": o["student_s"] if rate is not None else 0,
                "pipeline_seq_ttft_s": ((o["student_s"] if rate is not None else 0)
                                        + resp["timings"]["ttft_s"]),
                "wall_s": t1 - t0,
            }
            rows.append(row)
            with (args.output / "requests.jsonl").open("a") as h:
                h.write(json.dumps(row) + "\n")
            print(json.dumps({k: row[k] for k in (
                "session", "after_msg", "arm", "retained_tokens", "correct",
                "server_ttft_s", "pipeline_seq_ttft_s")}), flush=True)
    (args.output / "COMPLETE.json").write_text(json.dumps({"requests": len(rows)}))
    print(f"DONE {len(rows)}", flush=True)


if __name__ == "__main__":
    main()
