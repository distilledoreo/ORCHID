"""Focused answer-quality replay; stored attention avoids repeating the full grid."""

import argparse
import asyncio
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from .core import digest, render, score_chunks, select
from .report import load
from .run import append, main_answer, make_all_chunks, recall, trace_condition
from .tokenization import load_tokenizer

CONDITIONS = [
    {"id": "full", "chunk": 64, "lookahead": 8, "pool": True, "keep": 1.0},
    {"id": "look8_chunk64_pool", "chunk": 64, "lookahead": 8, "pool": True, "keep": 0.5},
    {"id": "look0_chunk64_pool", "chunk": 64, "lookahead": 0, "pool": True, "keep": 0.5},
    {"id": "look8_chunk64_raw", "chunk": 64, "lookahead": 8, "pool": False, "keep": 0.5},
    {"id": "look8_chunk32_pool", "chunk": 32, "lookahead": 8, "pool": True, "keep": 0.5},
    {"id": "look8_chunk128_pool", "chunk": 128, "lookahead": 8, "pool": True, "keep": 0.5},
    {"id": "look8_token_raw", "chunk": 1, "lookahead": 8, "pool": False, "keep": 0.5},
    {"id": "adaptive_task_threshold", "chunk": 64, "lookahead": 8, "pool": True, "keep": 0.5, "adaptive": True},
]


def protocol(root):
    root = Path(root)
    cases = json.loads((root / "corpus.json").read_text())
    value = {"cases": [c["id"] for c in cases if c["split"] == "held_out" and c["id"].endswith(("-08", "-15"))],
             "conditions": CONDITIONS, "keep_ratio_primary": 0.5,
             "purpose": "Focused answer-quality ablations, not parameter tuning or a second optimization project.",
             "selection": "First and last held-out ID in every category; one ordinary and one longer case. Chosen by ID, not outcomes.",
             "timing": "Stored-score replay: main-only times are observable; end-to-end router TTFT is not measured here.",
             "primary_decision": "Unchanged: all 64 held-out cases in requests.jsonl. This 16-case replay is descriptive."}
    path = root / "quality-protocol.json"
    if path.exists():
        assert json.loads(path.read_text()) == value, "Quality protocol changed"
    else:
        path.write_text(json.dumps(value, indent=2) + "\n")
    return value


async def replay(args):
    import httpx

    from .attention import AttentionSpeculator
    root = Path(args.directory)
    plan = protocol(root)
    output = root / "quality-ablations.jsonl"
    if output.exists():
        raise ValueError("Quality output already exists; do not mix runs")
    cases = json.loads((root / "corpus.json").read_text())
    chosen_cases = [c for c in cases if c["id"] in plan["cases"]]
    assert len(chosen_cases) == 16
    traces = {r["case"]: r for r in load(root / "attention-traces.jsonl")}
    tokenizer = load_tokenizer(args.main_tokenizer)
    # Match primary engine cache sizing: same resident student and warmup.
    student = AttentionSpeculator(args.speculator, device="cuda", threads=8)
    student.trace(cases[0]["sources"], cases[0]["query"], lookahead=8)
    env = dict(os.environ)
    env.pop("STRATA_API_KEY", None)
    log = open("data/specprefill_repurpose/quality-server.log", "w")
    process = subprocess.Popen(json.loads(Path(args.main_launch_json).read_text()), env=env,
                               stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            deadline = time.monotonic() + 600
            while True:
                try:
                    response = await client.get(args.main_url.rstrip("/") + "/models", timeout=2)
                    if response.status_code == 200 and response.json().get("data"):
                        break
                except httpx.HTTPError:
                    pass
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Quality main endpoint unavailable")
                await asyncio.sleep(1)
            for index, case in enumerate(chosen_cases):
                order = CONDITIONS if index % 2 == 0 else CONDITIONS[::-1]
                for condition in order:
                    chunks = make_all_chunks(case, tokenizer, condition["chunk"])
                    scores = score_chunks(chunks, case["query"], trace_condition(traces[case["id"]], condition["lookahead"]), pooling=condition["pool"])
                    selected, _, _ = select(scores, case["sources"], tokenizer, condition["keep"],
                                             adaptive=condition.get("adaptive", False), query=case["query"])
                    context = render(selected, case["sources"], inventory=condition["id"] != "full")
                    row = {"case": case["id"], "category": case["category"], "condition": condition["id"],
                           "evidence_recall": recall(case, selected), "context_hash": digest(context),
                           "selected_spans": [{"source_id": c.source_id, "start": c.start, "end": c.end} for c in selected],
                           "scores_replayed": True, "end_to_end_ttft_measured": False}
                    try:
                        row["main"] = await main_answer(client, args.main_url, args.main_model, case, context,
                                                        digest(case["id"] + "quality" + condition["id"])[:16])
                    except Exception as error:
                        row["main_error"] = type(error).__name__
                    append(output, row)
                print(json.dumps({"quality_cases_complete": index + 1, "of": 16}), flush=True)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    parser.add_argument("--freeze-protocol", action="store_true")
    parser.add_argument("--experiment-enabled", action="store_true")
    parser.add_argument("--speculator")
    parser.add_argument("--main-tokenizer")
    parser.add_argument("--main-launch-json")
    parser.add_argument("--main-url")
    parser.add_argument("--main-model")
    args = parser.parse_args()
    if args.freeze_protocol:
        protocol(args.directory)
    elif args.experiment_enabled and all((args.speculator, args.main_tokenizer, args.main_launch_json, args.main_url, args.main_model)):
        asyncio.run(replay(args))
    else:
        parser.error("Freeze the protocol offline, or explicitly enable the replay and supply all runtime arguments")
