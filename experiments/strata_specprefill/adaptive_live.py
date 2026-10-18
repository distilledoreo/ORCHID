"""Isolated live sweep for adaptive retention (dev or held split).

Reuses run.py Server/post/load_frontend + Student.trace exactly (same engine,
configs, timing definition, controls). Output isolated under
data/strata_specprefill/adaptive-live-<split>-v1. No production changes.

Example:
  .../data/specprefill_repurpose/.venv/bin/python -m experiments.strata_specprefill.adaptive_live \
    --split dev --keep-rates 0.05 0.1 0.2 0.3 0.5 0.75 1.0
"""

from __future__ import annotations

import argparse
import gc
import json
import random
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

from .run import Server, load_frontend  # noqa: E402


def check_correct(case: dict, text: str) -> bool:
    clean = re.sub(r"<\|[^> scree]+\|>", "", text)
    clean = re.sub(r"<\|[^>]+?\|>", "", text)
    if case.get("kind") == "coding":
        nums = re.findall(r"\d{5}", clean)
        return nums == [case["answer"]]
    words = re.findall(r"[A-Za-z]+\d+", clean)
    return words == [case["answer"]]


def main() -> None:
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--split", default="dev", choices=("dev", "held"))
    ap.add_argument("--keep-rates", type=float, nargs="+",
                    default=[0.05, 0.10, 0.20, 0.30, 0.50, 0.75, 1.0])
    ap.add_argument("--strata-root", type=Path, default=Path("/home/distilledoreo/AI/Strata"))
    ap.add_argument("--server-python", type=Path,
                    default=Path("/home/distilledoreo/AI/Strata/.venv/bin/python"))
    ap.add_argument("--student", type=Path,
                    default=Path("/media/distilledoreo/Storage/AI/state-projection/models/Qwen3.5-0.8B"))
    ap.add_argument("--alone-config", type=Path,
                    default=ROOT / "data/strata_specprefill/alone-config.json")
    ap.add_argument("--colocated-config", type=Path,
                    default=ROOT / "data/strata_specprefill/colocated-config.json")
    ap.add_argument("--sets", type=Path,
                    default=ROOT / "data/strata_specprefill/adaptive-sets-v1.json")
    ap.add_argument("--output", type=Path, default=None)
    ap.add_argument("--port", type=int, default=18587)
    args = ap.parse_args()

    from .core import compatible_ids

    if args.output is None:
        args.output = ROOT / f"data/strata_specprefill/adaptive-live-{args.split}-v1"
    args.output.mkdir(parents=True, exist_ok=False)
    args.url = f"http://127.0.0.1:{args.port}"
    alone = json.loads(args.alone_config.read_text())
    tok, template, vocab = load_frontend(args.strata_root, Path(alone["tokenizer"]))
    allowed = compatible_ids(vocab, json.loads((args.student / "tokenizer.json").read_text()))
    sets = json.loads(args.sets.read_text())
    cases = sets[args.split]

    from .run import keep_arm

    arms = ["full"] + [keep_arm(r) for r in args.keep_rates]
    # run_arm uses case["messages"] via prepare() + case["answer"] with lookup-only
    # correctness; wrap it to also support coding answers.
    import experiments.strata_specprefill.run as runmod
    orig_run_arm = runmod.run_arm

    def patched_run_arm(a, case, arm, epoch, t, tpl, student=None):
        res = orig_run_arm(a, case, arm, epoch, t, tpl, student)
        # Re-score coding cases with digit-exact check; keep lookup scoring as-is.
        if case.get("kind") == "coding":
            res["correct"] = check_correct(case, res["output_text"])
        return res

    runmod.run_arm = patched_run_arm

    from .student import Student

    results: list[dict] = []

    def collect(case, arm, epoch, student=None):
        res = patched_run_arm(args, case, arm, epoch, tok, template, student)
        results.append(res)
        with (args.output / "requests.jsonl").open("a") as h:
            h.write(json.dumps({k: res.get(k) for k in (
                "epoch", "case", "arm", "original_tokens", "retained_tokens",
                "correct", "pipeline_ttft_s", "server_ttft_s", "pre_request_s",
                "total_s", "output_text", "trace",
            )}) + "\n")
        print(json.dumps({k: res[k] for k in (
            "epoch", "case", "arm", "retained_tokens", "correct", "pipeline_ttft_s")}),
            flush=True)

    with Server(args, args.alone_config, "standalone"):
        warm = patched_run_arm(args, cases[0], "full", "warmup", tok, template)
        (args.output / "standalone-warmup.json").write_text(json.dumps(
            {k: warm.get(k) for k in ("case", "arm", "pipeline_ttft_s", "correct")}))
        for case in cases:
            collect(case, "full", "standalone")
    with Server(args, args.colocated_config, "colocated"):
        student = Student(args.student, allowed)
        warmups = [patched_run_arm(args, cases[0], arm, "warmup", tok, template, student)
                   for arm in arms]
        (args.output / "colocated-warmups.json").write_text(json.dumps(
            [{k: w.get(k) for k in ("arm", "pipeline_ttft_s", "correct")} for w in warmups]))
        control = next(w for w in warmups if w["arm"] == "keep100")
        if warmups[0]["output_ids"] != control["output_ids"]:
            raise RuntimeError("100% equivalence failed; stop before sparse comparison")
        tasks = [(c, a) for c in cases for a in arms]
        random.Random(731).shuffle(tasks)
        for case, arm in tasks:
            collect(case, arm, "colocated", student)
        del student
        gc.collect()
    for case in cases:
        rows = {r["arm"]: r for r in results
                if r["case"] == case["id"] and r["epoch"] == "colocated"}
        if rows["full"]["output_ids"] != rows["keep100"]["output_ids"]:
            raise RuntimeError(f"100% mismatch on {case['id']}")
    (args.output / "COMPLETE.json").write_text(json.dumps(
        {"requests": len(results), "split": args.split, "arms": arms,
         "t": time.time()}))
    print(f"DONE split={args.split} requests={len(results)}")


if __name__ == "__main__":
    main()
