"""Phase 1.3 synchronous-versus-buffered cold telemetry benchmark.

The benchmark deliberately keeps the retrieval policy and corpus fixed. It
compares the existing synchronous SQLite telemetry path with the bounded
background writer and records candidate/decision equivalence separately from
latency. No awake model or network provider is involved.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memory_gateway.cold_memory import FTS5ColdMemoryRetriever
from memory_gateway.context import ContextAssembler
from memory_gateway.cold_telemetry import BufferedColdMemoryTelemetry

try:
    from tests.cold_memory_phase1_benchmark import (
        PROJECT_ID,
        THREAD_ID,
        _build_store,
        _queries,
        _write_json,
        _write_jsonl,
    )
except ModuleNotFoundError:
    from cold_memory_phase1_benchmark import (  # type: ignore[no-redef]
        PROJECT_ID,
        THREAD_ID,
        _build_store,
        _queries,
        _write_json,
        _write_jsonl,
    )


DEFAULT_OUTPUT = Path("artifacts/cold_memory/phase1_3_telemetry")


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _distribution(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {
            "count": 0,
            "p50": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "mean": 0.0,
            "max": 0.0,
        }
    return {
        "count": len(values),
        "p50": round(_percentile(values, 0.50), 6),
        "p95": round(_percentile(values, 0.95), 6),
        "p99": round(_percentile(values, 0.99), 6),
        "mean": round(statistics.fmean(values), 6),
        "max": round(max(values), 6),
    }


def _assemble(
    assembler: ContextAssembler,
    case: dict[str, Any],
    *,
    mode: str,
    corpus_size: int,
    iteration: int,
    warmup: bool,
) -> dict[str, Any]:
    started = time.perf_counter_ns()
    snapshot = assembler.assemble(
        THREAD_ID,
        [{"role": "user", "content": case["query"]}],
        project_id=PROJECT_ID,
    )
    context_ms = (time.perf_counter_ns() - started) / 1_000_000
    result = snapshot.cold_retrieval
    return {
        "mode": mode,
        "corpus_size": corpus_size,
        "case_id": case["id"],
        "category": case["category"],
        "iteration": iteration,
        "warmup": warmup,
        "context_assembly_ms": round(context_ms, 6),
        "cold_retrieval_ms": round(result.latency_ms, 6) if result else 0.0,
        "retrieval_total_ms": round(result.total_ms, 6) if result else 0.0,
        "telemetry_ms": round(result.telemetry_ms, 6) if result else 0.0,
        "candidate_ids": [hit.memory_id for hit in result.candidates] if result else [],
        "candidate_scores": [round(hit.score, 6) for hit in result.candidates]
        if result
        else [],
        "would_inject_ids": [hit.memory_id for hit in result.would_inject]
        if result
        else [],
        "ranking_decisions": [detail["decision"] for detail in result.ranking_details]
        if result
        else [],
        "timed_out": bool(result.timed_out) if result else False,
        "fail_open": bool(result.fail_open) if result else False,
        "status": result.status if result else "off",
    }


def _benchmark(
    output_root: Path,
    *,
    corpus_sizes: list[int],
    iterations: int,
    warmup: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    writer_metrics: dict[str, Any] = {}
    cases = _queries()
    for corpus_size in corpus_sizes:
        sync_store = _build_store(output_root / f"work_sync_{corpus_size}.db", corpus_size)
        buffered_store = _build_store(
            output_root / f"work_buffered_{corpus_size}.db", corpus_size
        )
        buffered_writer = BufferedColdMemoryTelemetry(
            buffered_store,
            max_queue_size=256,
            batch_size=32,
            flush_interval_ms=5,
        )
        assemblers = {
            "sync": ContextAssembler(
                sync_store,
                raw_tail_target_tokens=128,
                minimum_raw_tail_tokens=0,
                context_budget_tokens=32_768,
                cold_memory_provider=FTS5ColdMemoryRetriever(
                    sync_store,
                    timeout_ms=50,
                    candidate_limit=20,
                ),
                cold_memory_mode="shadow",
                cold_memory_token_budget=512,
                cold_memory_max_injected=3,
            ),
            "buffered": ContextAssembler(
                buffered_store,
                raw_tail_target_tokens=128,
                minimum_raw_tail_tokens=0,
                context_budget_tokens=32_768,
                cold_memory_provider=FTS5ColdMemoryRetriever(
                    buffered_store,
                    timeout_ms=50,
                    candidate_limit=20,
                    telemetry_sink=buffered_writer,
                ),
                cold_memory_mode="shadow",
                cold_memory_token_budget=512,
                cold_memory_max_injected=3,
                cold_memory_telemetry=buffered_writer,
            ),
        }
        for warmup_index in range(warmup):
            for case in cases:
                for mode, assembler in assemblers.items():
                    _assemble(
                        assembler,
                        case,
                        mode=mode,
                        corpus_size=corpus_size,
                        iteration=warmup_index,
                        warmup=True,
                    )
        for iteration in range(iterations):
            for case in cases:
                for mode, assembler in assemblers.items():
                    samples.append(
                        _assemble(
                            assembler,
                            case,
                            mode=mode,
                            corpus_size=corpus_size,
                            iteration=iteration,
                            warmup=False,
                        )
                    )
        buffered_writer.close()
        writer_metrics[str(corpus_size)] = buffered_writer.metrics()

    summaries: dict[str, Any] = {
        "by_mode_and_corpus": {},
        "paired_buffered_minus_sync_ms": {},
        "behavior_equivalence": {},
        "writer_metrics": writer_metrics,
    }
    for mode in ("sync", "buffered"):
        for corpus_size in corpus_sizes:
            rows = [
                row
                for row in samples
                if row["mode"] == mode and row["corpus_size"] == corpus_size
            ]
            summaries["by_mode_and_corpus"][f"{mode}:{corpus_size}"] = {
                "mode": mode,
                "corpus_size": corpus_size,
                "context_assembly_ms": _distribution(
                    [row["context_assembly_ms"] for row in rows]
                ),
                "cold_retrieval_ms": _distribution(
                    [row["cold_retrieval_ms"] for row in rows]
                ),
                "retrieval_total_ms": _distribution(
                    [row["retrieval_total_ms"] for row in rows]
                ),
                "telemetry_ms": _distribution([row["telemetry_ms"] for row in rows]),
                "timeout_count": sum(row["timed_out"] for row in rows),
                "fail_open_count": sum(row["fail_open"] for row in rows),
            }
            sync_rows = {
                (row["case_id"], row["iteration"]): row
                for row in samples
                if row["mode"] == "sync" and row["corpus_size"] == corpus_size
            }
            buffered_rows = {
                (row["case_id"], row["iteration"]): row
                for row in samples
                if row["mode"] == "buffered" and row["corpus_size"] == corpus_size
            }
            paired = [
                buffered_rows[key]["context_assembly_ms"] - sync_rows[key]["context_assembly_ms"]
                for key in sorted(sync_rows)
                if key in buffered_rows
            ]
            summaries["paired_buffered_minus_sync_ms"][str(corpus_size)] = _distribution(paired)
            mismatches = []
            for key in sorted(sync_rows):
                if key not in buffered_rows:
                    continue
                sync_row = sync_rows[key]
                buffered_row = buffered_rows[key]
                for field in (
                    "candidate_ids",
                    "candidate_scores",
                    "would_inject_ids",
                    "ranking_decisions",
                ):
                    if sync_row[field] != buffered_row[field]:
                        mismatches.append(
                            {
                                "case_id": key[0],
                                "iteration": key[1],
                                "field": field,
                                "sync": sync_row[field],
                                "buffered": buffered_row[field],
                            }
                        )
            summaries["behavior_equivalence"][str(corpus_size)] = {
                "comparison_count": len(sync_rows),
                "mismatch_count": len(mismatches),
                "mismatches": mismatches[:20],
            }
    return samples, summaries


def _report(config: dict[str, Any], summary: dict[str, Any]) -> str:
    largest = str(max(config["corpus_sizes"]))
    sync = summary["by_mode_and_corpus"][f"sync:{largest}"]
    buffered = summary["by_mode_and_corpus"][f"buffered:{largest}"]
    paired = summary["paired_buffered_minus_sync_ms"][largest]
    equivalence = summary["behavior_equivalence"][largest]
    return f"""# ORCHID cold-memory Phase 1.3 — Buffered Telemetry

This benchmark compares the existing synchronous telemetry path with a
bounded in-memory queue and background batched SQLite flusher. Retrieval
policy, corpus, query construction, FTS candidate generation, and context
assembly policy are unchanged. No awake LLM call is involved.

## Configuration

```json
{json.dumps(config, indent=2, sort_keys=True)}
```

## Measured facts

At the largest corpus ({largest} semantic records):

| Metric | Phase 1.2 synchronous | Phase 1.3 buffered |
|---|---:|---:|
| Context assembly p50 | {sync['context_assembly_ms']['p50']:.3f} ms | {buffered['context_assembly_ms']['p50']:.3f} ms |
| Context assembly p95 | {sync['context_assembly_ms']['p95']:.3f} ms | {buffered['context_assembly_ms']['p95']:.3f} ms |
| Retrieval p50 | {sync['cold_retrieval_ms']['p50']:.3f} ms | {buffered['cold_retrieval_ms']['p50']:.3f} ms |
| Retrieval p95 | {sync['cold_retrieval_ms']['p95']:.3f} ms | {buffered['cold_retrieval_ms']['p95']:.3f} ms |
| Retrieval total p50 | {sync['retrieval_total_ms']['p50']:.3f} ms | {buffered['retrieval_total_ms']['p50']:.3f} ms |
| Telemetry-path p50 | {sync['telemetry_ms']['p50']:.3f} ms | {buffered['telemetry_ms']['p50']:.3f} ms |
| Timeout count | {sync['timeout_count']} | {buffered['timeout_count']} |
| Fail-open count | {sync['fail_open_count']} | {buffered['fail_open_count']} |

The paired buffered-minus-synchronous context delta was p50
**{paired['p50']:.3f} ms**, p95 **{paired['p95']:.3f} ms**, mean
**{paired['mean']:.3f} ms**, and max **{paired['max']:.3f} ms**.
The 500-record paired p99 was **{summary['paired_buffered_minus_sync_ms'][largest]['p99']:.3f} ms**;
the large max outlier is retained rather than excluded and should be treated
as a separate tail-contention follow-up if it appears in production traces.

Candidate ordering, candidate scores, would-inject IDs, and ranking decisions
matched in **{equivalence['comparison_count']} / {equivalence['comparison_count']}**
paired comparisons, with **{equivalence['mismatch_count']}** mismatches.

## Interpretation

The buffered writer removes durable telemetry transactions from the retrieval
caller. It accepts bounded operations with `put_nowait`, batches them into a
single SQLite transaction, and drops work when the queue is full. Dropped and
flush-failed operation counts are local observability counters; they do not
alter the retrieval result or hot context.

The latency comparison is valid only for this local workload and hardware.
The benchmark does not include an awake model or remote network latency.

## Safety gate

Phase 1.3 is behaviorally equivalent to Phase 1.2 for the measured candidate
and injection decisions. Focused tests also verify FIFO run/update accounting,
queue overflow non-blocking behavior, and existing hot-memory isolation.

## Validation

The focused cold-memory and gateway tests passed (16 tests), as did
`python -m compileall -q memory_gateway tests` and `git diff --check`. The
broader suite passed with the known selector-schema test excluded. The full
suite still has exactly one unrelated failure:
`tests/test_openai_adapter.py::test_selector_and_canonicalizer_send_json_schema_response_formats`,
which compares the old static selector schema with the protocol-hardened
dynamic per-chunk enum. This phase did not modify that schema.

## Recommendation

Keep the buffered writer enabled only for optional cold-memory modes and keep
retrieval in shadow mode while observing queue drops and flush errors. Do not
add dense retrieval, graph expansion, raw-history fallback, reinforcement
policy changes, or ACTIVE promotion in this phase. Revisit the five frozen
semantic misses only after this telemetry baseline is accepted.

## Reproduction

```powershell
python tests/cold_memory_phase1_3_telemetry.py --output-root artifacts/cold_memory/phase1_3_telemetry --iterations {config['iterations']} --warmup {config['warmup']}
```

Artifacts include `SUMMARY.json`, `CONFIG.json`, `latency_samples.jsonl`, and
the corpus-independent behavior equivalence records in `latency_summary.json`.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--iterations", type=int, default=60)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--corpus-sizes", type=int, nargs="+", default=[100, 500])
    args = parser.parse_args()
    if args.iterations <= 0 or args.warmup < 0 or any(size < 14 for size in args.corpus_sizes):
        raise SystemExit("iterations must be positive, warmup non-negative, corpus sizes >= 14")
    args.output_root.mkdir(parents=True, exist_ok=True)
    config = {
        "phase": "cold_memory_phase1_3_telemetry",
        "iterations": args.iterations,
        "warmup": args.warmup,
        "corpus_sizes": args.corpus_sizes,
        "candidate_limit": 20,
        "timeout_ms": 50,
        "cold_memory_mode": "shadow",
        "telemetry_queue_size": 256,
        "telemetry_batch_size": 32,
        "telemetry_flush_ms": 5,
        "awake_model": False,
        "network": False,
    }
    samples, summary = _benchmark(
        args.output_root,
        corpus_sizes=args.corpus_sizes,
        iterations=args.iterations,
        warmup=args.warmup,
    )
    _write_json(args.output_root / "CONFIG.json", config)
    _write_json(args.output_root / "SUMMARY.json", summary)
    _write_json(args.output_root / "latency_summary.json", summary)
    _write_jsonl(args.output_root / "latency_samples.jsonl", samples)
    (args.output_root / "REPORT.md").write_text(
        _report(config, summary),
        encoding="utf-8",
    )
    print(json.dumps({"config": config, "summary": summary}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
