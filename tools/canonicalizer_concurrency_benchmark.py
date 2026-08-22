"""Measure one versus two concurrent canonicalizer requests on the frozen trace."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from canonicalizer_throughput_benchmark import _load_events, _run_one


async def _measure(
    *,
    events: list[Any],
    concurrency: int,
    target: int,
    endpoint: str,
    model: str,
    timeout: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    results = await asyncio.gather(
        *(
            _run_one(
                events=events,
                target=target,
                endpoint=endpoint,
                model=model,
                timeout=timeout,
                max_output_tokens=None,
                full_window=False,
            )
            for _ in range(concurrency)
        )
    )
    wall_ms = (time.perf_counter() - started) * 1000
    source_tokens = sum(int(result.get("source_tokens") or 0) for result in results)
    return {
        "concurrency": concurrency,
        "target_tokens": target,
        "wall_ms": wall_ms,
        "aggregate_source_tokens": source_tokens,
        "aggregate_source_tokens_per_second": source_tokens / max(wall_ms / 1000, 0.000001),
        "statuses": [result.get("status") for result in results],
        "timeouts": sum(
            1
            for result in results
            for batch in result.get("batches", [])
            if batch.get("timeout")
        ),
        "structured_output_failures": sum(
            1
            for result in results
            for batch in result.get("batches", [])
            if not batch.get("structured_output_valid", False)
        ),
        "results": results,
    }


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    replay = Path(args.replay)
    events = _load_events(replay)
    if args.event_limit:
        events = events[: args.event_limit]
    measurements = []
    for concurrency in (1, 2):
        measurement = await _measure(
            events=events,
            concurrency=concurrency,
            target=args.target,
            endpoint=args.endpoint,
            model=args.model,
            timeout=args.timeout,
        )
        measurements.append(measurement)
        print(json.dumps(measurement, ensure_ascii=False), flush=True)
    return {
        "replay": str(replay.resolve()),
        "replay_sha256": hashlib.sha256(replay.read_bytes()).hexdigest(),
        "events_used": len(events),
        "endpoint": args.endpoint,
        "model": args.model,
        "timeout_seconds": args.timeout,
        "measurements": measurements,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replay",
        default=(
            "artifacts/agent_benchmarks/freetoshop_operability_hardening/"
            "frozen_freetoshop_replay/events.jsonl"
        ),
    )
    parser.add_argument(
        "--output",
        default=(
            "artifacts/agent_benchmarks/freetoshop_canonicalizer_throughput/"
            "concurrency/results.json"
        ),
    )
    parser.add_argument("--endpoint", default="http://127.0.0.1:1234/v1")
    parser.add_argument("--model", default="qwen3.5-4b@q6_k")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--target", type=int, default=4000)
    parser.add_argument("--event-limit", type=int, default=12)
    args = parser.parse_args()
    result = asyncio.run(_run(args))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
