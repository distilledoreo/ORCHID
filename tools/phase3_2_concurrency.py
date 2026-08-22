"""Controlled local Qwen concurrency experiment for Phase 3.2."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_gateway.compaction import expand_source_items
from memory_gateway.context import estimate_tokens
from memory_gateway.pipeline_adapters import _chunk_events, _event_payload
import phase3_2_stall_analysis as stall_analysis
from phase3_2_stall_analysis import (
    DEFAULT_REPLAY,
    TARGET_TOKENS,
    call_canonicalizer,
    load_events,
)


def lmstudio_status() -> str:
    try:
        return subprocess.check_output(["lms", "ps"], text=True, stderr=subprocess.STDOUT, timeout=10)
    except Exception as exc:
        return f"unavailable: {exc}"


async def measure(batch: list[Any], concurrency: int, repetitions: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    for repetition in range(repetitions):
        request_started = time.perf_counter()
        results = await asyncio.gather(
            *(call_canonicalizer(batch, label=f"c{concurrency}_r{repetition}_i{index}") for index in range(concurrency))
        )
        rows.append({
            "repetition": repetition,
            "wall_ms": (time.perf_counter() - request_started) * 1000,
            "results": results,
        })
    source_tokens = sum(
        int(result.get("source_tokens") or 0)
        for row in rows
        for result in row["results"]
    )
    elapsed_seconds = max((time.perf_counter() - started), 0.000001)
    return {
        "concurrency": concurrency,
        "repetitions": repetitions,
        "wall_ms": (time.perf_counter() - started) * 1000,
        "aggregate_source_tokens": source_tokens,
        "aggregate_source_tokens_per_second": source_tokens / elapsed_seconds,
        "timeouts": sum(result.get("timeout", False) for row in rows for result in row["results"]),
        "failures": sum(result.get("status") != "SUCCEEDED" for row in rows for result in row["results"]),
        "rows": rows,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    # The LM Studio loader creates a distinct model identifier for a separate
    # parallel-slot configuration (for example qwen...:2).  Set the imported
    # harness module's global so its client factory uses the requested slot.
    stall_analysis.MODEL = args.model
    replay = Path(args.replay)
    events = load_events(replay)
    items = list(expand_source_items(events, selector_budget_tokens=1_200, safety_margin=0.8))
    batch = _chunk_events(items, target_tokens=args.target)[0]
    batch_source_tokens = sum(estimate_tokens(json.dumps(_event_payload(item), ensure_ascii=False)) for item in batch)
    result = {
        "replay": str(replay.resolve()),
        "replay_sha256": hashlib.sha256(replay.read_bytes()).hexdigest(),
        "target_tokens": args.target,
        "model": args.model,
        "source_refs": [item.id for item in batch],
        "source_tokens": batch_source_tokens,
        "endpoint_status_before": lmstudio_status(),
        "measurements": [],
    }
    for concurrency in (1, 2):
        measurement = await measure(batch, concurrency, args.repetitions)
        result["measurements"].append(measurement)
        print(json.dumps(measurement, ensure_ascii=False), flush=True)
    result["endpoint_status_after"] = lmstudio_status()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", default=str(DEFAULT_REPLAY))
    parser.add_argument("--output", default=str(ROOT / "artifacts/agent_benchmarks/freetoshop_pipeline_ablation/concurrency/results.json"))
    parser.add_argument("--target", type=int, default=4_000)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--model", default="qwen3.5-4b@q6_k")
    args = parser.parse_args()
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
