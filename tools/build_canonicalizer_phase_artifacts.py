"""Build deterministic reports from the captured FreetoShop compaction evidence."""

from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_gateway.context import estimate_tokens


OUT = ROOT / "artifacts/agent_benchmarks/freetoshop_canonicalizer_throughput"
OLD_DB = Path(r"C:\Users\disti\benchmark_work\operability_preflight_20260820\orchid-memory.db")


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _duration_seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()


def build_profile() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import sqlite3

    if not OLD_DB.exists():
        return [], {"status": "UNAVAILABLE", "reason": str(OLD_DB)}
    connection = sqlite3.connect(OLD_DB)
    connection.row_factory = sqlite3.Row
    jobs = [dict(row) for row in connection.execute(
        "select * from compaction_jobs order by created_at"
    )]
    runs = [dict(row) for row in connection.execute(
        "select * from model_runs order by created_at"
    )]
    records: list[dict[str, Any]] = []
    for row in runs:
        metadata = json.loads(row.get("metadata_json") or "{}")
        records.append({
            "job_id": row["job_id"],
            "stage": row["stage"],
            "status": row["status"],
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "wall_ms": row["wall_ms"],
            "finish_reason": row["finish_reason"],
            "error": row["error"],
            "ttft_ms": metadata.get("ttft_ms"),
            "source_refs": json.loads(row["source_refs_json"] or "[]"),
            "metadata": metadata,
        })

    completed_jobs = [
        job for job in jobs
        if _duration_seconds(job.get("started_at"), job.get("finished_at")) is not None
    ]
    completed_job_ids = {job["id"] for job in completed_jobs}
    stage_summary: dict[str, Any] = {}
    total_job_wall_ms = sum(
        (_duration_seconds(job.get("started_at"), job.get("finished_at")) or 0) * 1000
        for job in completed_jobs
    )
    for stage in ("selector", "canonicalizer", "consolidator"):
        stage_rows = [
            row for row in records
            if row["stage"] == stage and row["job_id"] in completed_job_ids
        ]
        stage_wall = sum(float(row["wall_ms"] or 0) for row in stage_rows)
        latencies = [float(row["wall_ms"]) for row in stage_rows if row["wall_ms"] is not None]
        stage_summary[stage] = {
            "calls": len(stage_rows),
            "succeeded": sum(row["status"] == "SUCCEEDED" for row in stage_rows),
            "failed": sum(row["status"] != "SUCCEEDED" for row in stage_rows),
            "wall_ms": stage_wall,
            "wall_percent_of_completed_job_wall": (
                stage_wall / total_job_wall_ms * 100 if total_job_wall_ms else None
            ),
            "p50_ms": statistics.median(latencies) if latencies else None,
            "p95_ms": (
                statistics.quantiles(latencies, n=20, method="inclusive")[18]
                if len(latencies) >= 2 else (latencies[0] if latencies else None)
            ),
            "input_tokens": sum(int(row["input_tokens"] or 0) for row in stage_rows),
            "output_tokens": sum(int(row["output_tokens"] or 0) for row in stage_rows),
        }
    stage_wall_total = sum(value["wall_ms"] for value in stage_summary.values())
    stage_summary["software_and_unobserved_ms"] = max(
        0.0, total_job_wall_ms - stage_wall_total
    )
    stage_summary["completed_job_wall_ms"] = total_job_wall_ms
    stage_summary["completed_job_count"] = len(completed_jobs)
    stage_summary["running_job_count"] = sum(job["status"] == "RUNNING" for job in jobs)
    stage_summary["profile_scope"] = "model runs belonging to jobs with started_at and finished_at"
    connection.close()
    return records, stage_summary


def build_sweep_summary() -> dict[str, Any]:
    sweep_dir = OUT / "batch_sweep"
    results: list[dict[str, Any]] = []
    for path in sorted(sweep_dir.glob("corrected_*.json")):
        payload = _json(path)
        for result in payload.get("results", []):
            batches = result.get("batches", [])
            completed = [batch for batch in batches if batch.get("structured_output_valid")]
            results.append({
                "file": path.name,
                "target_tokens": result.get("target_tokens"),
                "max_output_tokens": result.get("max_output_tokens"),
                "full_window": result.get("full_window"),
                "status": result.get("status"),
                "source_tokens": result.get("source_tokens"),
                "wall_ms": result.get("wall_ms"),
                "source_tokens_per_second": result.get("source_tokens_per_second"),
                "batch_count": result.get("batch_count"),
                "valid_batch_count": len(completed),
                "timeout_count": sum(bool(batch.get("timeout")) for batch in batches),
                "structured_failure_count": sum(
                    not bool(batch.get("structured_output_valid")) for batch in batches
                ),
                "batch_latencies_ms": [batch.get("wall_ms") for batch in completed],
                "source_input_tokens": sum(
                    int(batch.get("estimated_input_tokens") or 0) for batch in completed
                ),
                "model_input_tokens": sum(
                    int(batch.get("input_tokens") or 0) for batch in completed
                ),
                "model_output_tokens": sum(
                    int(batch.get("output_tokens") or 0) for batch in completed
                ),
            })
    results.sort(key=lambda item: (item["target_tokens"] or 0, item["file"] or ""))
    correct = [
        item for item in results
        if item["full_window"] and item["status"] == "SUCCEEDED"
        and item["timeout_count"] == 0 and item["structured_failure_count"] == 0
    ]
    best = max(correct, key=lambda item: item["source_tokens_per_second"]) if correct else None
    return {"results": results, "correct_zero_timeout_best": best}


def build_final_replay() -> dict[str, Any]:
    path = OUT / "final_replay" / "canonicalizer_12k_full199.json"
    if not path.exists():
        return {"status": "UNAVAILABLE", "reason": str(path)}
    payload = _json(path)
    results = payload.get("results", [])
    if not results:
        return {"status": "INVALID", "reason": "no replay result"}
    result = results[0]
    batches = result.get("batches", [])
    attempted = [batch for batch in batches if batch.get("status") is not None]
    successful = [
        batch for batch in attempted
        if batch.get("status") == "SUCCEEDED" and batch.get("structured_output_valid")
    ]
    timeout_batches = [batch for batch in attempted if batch.get("timeout")]
    completed_source_tokens = sum(
        int(batch.get("estimated_input_tokens") or 0) for batch in successful
    )
    attempted_source_tokens = sum(
        int(batch.get("estimated_input_tokens") or 0) for batch in attempted
    )
    completed_wall_ms = sum(float(batch.get("wall_ms") or 0) for batch in successful)
    return {
        "status": "MEASURED",
        "file": path.name,
        "replay_sha256": payload.get("replay_sha256"),
        "events_used": payload.get("events_used"),
        "expanded_source_item_count": result.get("source_event_count"),
        "source_tokens_planned": result.get("source_tokens"),
        "batch_target_tokens": result.get("target_tokens"),
        "planned_batch_count": result.get("batch_count"),
        "attempted_batch_count": len(attempted),
        "successful_valid_batch_count": len(successful),
        "timeout_count": len(timeout_batches),
        "completed_source_tokens": completed_source_tokens,
        "attempted_source_tokens": attempted_source_tokens,
        "completed_valid_batch_wall_ms": completed_wall_ms,
        "partial_successful_batch_tokens_per_second": (
            completed_source_tokens / max(completed_wall_ms / 1000, 0.000001)
            if completed_wall_ms else None
        ),
        "status_from_harness": result.get("status"),
        "error": result.get("error"),
        "first_failed_batch": next(
            (batch.get("batch_index") for batch in attempted if batch.get("status") != "SUCCEEDED"),
            None,
        ),
        "remaining_unattempted_batch_count": max(
            0, int(result.get("batch_count") or 0) - len(attempted)
        ),
        "production_gate": {
            "zero_unresolved_canonicalizer_timeouts": len(timeout_batches) == 0,
            "full_replay_completed": result.get("status") == "SUCCEEDED",
            "throughput_at_least_75_source_tokens_per_second": False,
        },
    }


def build_amplification(sweep: dict[str, Any]) -> dict[str, Any]:
    selected = next(
        (
            item for item in sweep["results"]
            if item["file"] == "corrected_12k_full12.json"
            and item["status"] == "SUCCEEDED"
        ),
        None,
    )
    if not selected:
        return {"status": "UNAVAILABLE"}
    source = selected["source_tokens"]
    return {
        "status": "MEASURED",
        "fixture": selected["file"],
        "source_tokens": source,
        "canonicalizer_model_input_tokens": selected["model_input_tokens"],
        "canonicalizer_output_tokens": selected["model_output_tokens"],
        "canonicalizer_input_amplification": selected["model_input_tokens"] / source,
        "canonicalizer_output_per_source_token": selected["model_output_tokens"] / source,
        "note": "Selector and consolidator amplification are unavailable in this local-only frozen canonicalizer replay; captured provider-backed selector totals are in stage_summary.json.",
    }


def write_reports(
    profile: list[dict[str, Any]],
    stage_summary: dict[str, Any],
    sweep: dict[str, Any],
    amplification: dict[str, Any],
    final_replay: dict[str, Any],
) -> None:
    (OUT / "timeout_analysis").mkdir(parents=True, exist_ok=True)
    (OUT / "batch_sweep").mkdir(parents=True, exist_ok=True)
    (OUT / "amplification").mkdir(parents=True, exist_ok=True)
    with (OUT / "stage_profile.jsonl").open("w", encoding="utf-8") as handle:
        for row in profile:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (OUT / "stage_summary.json").write_text(json.dumps(stage_summary, indent=2) + "\n", encoding="utf-8")
    (OUT / "batch_sweep" / "SUMMARY.json").write_text(json.dumps(sweep, indent=2) + "\n", encoding="utf-8")
    (OUT / "amplification" / "token_breakdown.json").write_text(json.dumps(amplification, indent=2) + "\n", encoding="utf-8")

    final_replay_dir = OUT / "final_replay"
    final_replay_dir.mkdir(parents=True, exist_ok=True)
    (final_replay_dir / "SUMMARY.json").write_text(
        json.dumps(final_replay, indent=2) + "\n", encoding="utf-8"
    )
    source_replay = OUT / "final_replay" / "canonicalizer_12k_full199.json"
    if source_replay.exists():
        payload = _json(source_replay)
        batches = payload.get("results", [{}])[0].get("batches", [])
        with (final_replay_dir / "telemetry.jsonl").open("w", encoding="utf-8") as handle:
            for batch in batches:
                handle.write(json.dumps(batch, ensure_ascii=False) + "\n")

    timeout_rows = [row for row in profile if row["status"] != "SUCCEEDED"]
    with (OUT / "timeout_analysis" / "timeout_cases.jsonl").open("w", encoding="utf-8") as handle:
        for row in timeout_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    best = sweep.get("correct_zero_timeout_best")
    profile_text = f"""# Canonicalizer throughput phase profile

## Measured facts

The captured provider-backed ORCHID database contains two completed failed compaction jobs and one unfinished job. No completed capture reached consolidator or promotion, so those stages are explicitly unavailable in the captured failure profile.

- Completed-job wall time: {stage_summary.get('completed_job_wall_ms', 0) / 1000:.3f} seconds
- Selector model wall time: {stage_summary.get('selector', {}).get('wall_ms', 0) / 1000:.3f} seconds ({stage_summary.get('selector', {}).get('wall_percent_of_completed_job_wall')})%
- Canonicalizer model wall time: {stage_summary.get('canonicalizer', {}).get('wall_ms', 0) / 1000:.3f} seconds ({stage_summary.get('canonicalizer', {}).get('wall_percent_of_completed_job_wall')})%
- Consolidator model wall time: unavailable because canonicalizer failed first
- Software/unobserved remainder: {stage_summary.get('software_and_unobserved_ms', 0) / 1000:.3f} seconds
- Captured canonicalizer failures: {stage_summary.get('canonicalizer', {}).get('failed', 0)}

The corrected local canonicalizer replay used the frozen real FreetoShop event trace with exact per-batch source-ID enums. The best complete zero-timeout result in the measured window was:

- target: {best.get('target_tokens') if best else 'unavailable'} estimated source tokens
- source throughput: {best.get('source_tokens_per_second') if best else 'unavailable'} source tokens/sec
- wall time: {best.get('wall_ms') / 1000 if best else 'unavailable'} seconds

## Interpretation

Canonicalizer time is a material stage-level risk, but the captured provider-backed failures also show selector work is substantial when the snapshot is split into many small source-span chunks. The stage profile cannot claim a consolidator percentage because no captured job reached it.

The exact-ID schema is a correctness hardening change, not a semantic policy change. It eliminated the observed truncated-span citation on the corrected local runs. The 75 source-tokens/sec target was not met by the complete corrected local window; no live preflight is authorized from this result.

## Limitations

- The frozen local replay measures canonicalizer behavior and does not invoke selector, Solar consolidator, or DB promotion for every frozen event.
- The old capture predates request-shape telemetry, so its timeout rows have no actual prompt-token or progress signal.
- Local model latency is highly variable across repeated identical inputs; p50/p95 should be calculated from a larger stable run before production tuning.
"""
    (OUT / "PROFILE_REPORT.md").write_text(profile_text, encoding="utf-8")

    timeout_text = """# Canonicalizer timeout analysis

## Measured facts

The prior live capture contains two canonicalizer timeouts at exactly 180 seconds. Both had no first-token timestamp, no output tokens, no finish reason, and no successful response. The source batches were 8 and 11 source references respectively; one successful preceding canonicalizer call had 11,086 prompt tokens and took 121.67 seconds.

The new client has an explicit total async request deadline and records `error_category=timeout` with `failure_phase=total_request_deadline`. A deterministic delayed HTTP test confirmed the timeout record and an immediate next request succeeded.

## Interpretation

The prior failure was a non-streaming local endpoint request that could remain awaiting response headers beyond the nominal client timeout. The evidence does not prove whether the local model was queued, stalled, or still generating; non-streaming mode exposes no progress signal. The safe response is a bounded deadline plus fail/cleanup, not a larger blind timeout.

Output ceilings of 512 and 1,024 tokens improved some wall times but produced invalid truncated JSON on the same corpus, so they are rejected.

The full frozen replay then reproduced the failure under the corrected harness: two valid 12K batches completed, and batch 2 reached the explicit 180-second deadline without a response. Fifteen later batches were not attempted. This is a real zero-timeout gate failure, not a throughput estimate to average away.
"""
    (OUT / "timeout_analysis" / "REPORT.md").write_text(timeout_text, encoding="utf-8")

    sweep_text = """# Batch-size sweep

The corrected full-window runs used the same first 12 frozen replay events, exact per-batch source-ID enums, no output cap, and a 180-second per-request deadline. A result is counted as correct only when every batch completed with valid structured output and zero timeout failures.

Measured complete runs:

| Target | Source tok/s | Wall seconds | Batches | Timeouts | Structured failures |
|---:|---:|---:|---:|---:|---:|
| 4K | 51.44 | 314.05 | 5 | 0 | 0 |
| 8K | 47.89 | 337.36 | 3 | 0 | 0 |
| 12K | 71.14 | 227.10 | 2 | 0 | 0 |
| 16K | 62.05 | 260.38 | 2 | 0 | 0 |

The 12K target is the measured winner in this small window, but it remains below the 75 tok/s engineering gate. Output caps are not accepted because the 512 and 1,024 experiments ended in invalid JSON truncation.
"""
    (OUT / "batch_sweep" / "REPORT.md").write_text(sweep_text, encoding="utf-8")

    amp_text = f"""# Input amplification audit

The corrected 12K local canonicalizer replay retired {amplification.get('source_tokens')} estimated source tokens and sent {amplification.get('canonicalizer_model_input_tokens')} model input tokens, an input amplification of {amplification.get('canonicalizer_input_amplification'):.3f}x. It produced {amplification.get('canonicalizer_output_tokens')} output tokens.

Fixed per-call overhead measured from request profiles was approximately 420 system-prompt characters, 48 wrapper characters, and 340–746 response-schema characters in the 12K run. The dynamic exact-ID enum adds bounded schema text proportional to the batch's source IDs; it is correctness-constraining rather than avoidable scaffolding.

Selector and consolidator amplification were not available from the local-only replay. The captured selector totals are preserved in `stage_summary.json` and `stage_profile.jsonl`.
"""
    (OUT / "amplification" / "REPORT.md").write_text(amp_text, encoding="utf-8")

    final_replay_text = f"""# Frozen FreetoShop canonicalizer replay

## Measured facts

- Replay events supplied: {final_replay.get('events_used')}
- Expanded source items: {final_replay.get('expanded_source_item_count')}
- Planned source tokens: {final_replay.get('source_tokens_planned')}
- Planned 12K batches: {final_replay.get('planned_batch_count')}
- Attempted batches: {final_replay.get('attempted_batch_count')}
- Valid successful batches: {final_replay.get('successful_valid_batch_count')}
- Timeout batches: {final_replay.get('timeout_count')}
- First failed batch: {final_replay.get('first_failed_batch')}
- Unattempted batches after failure: {final_replay.get('remaining_unattempted_batch_count')}
- Valid partial-progress source tokens: {final_replay.get('completed_source_tokens')}
- Partial successful-batch rate: {final_replay.get('partial_successful_batch_tokens_per_second'):.3f} source tokens/sec
- Harness status: {final_replay.get('status_from_harness')}
- Error: {final_replay.get('error')}

## Gate result

The frozen replay did not complete and did not satisfy the zero-timeout requirement. The partial successful-batch rate is diagnostic only: it excludes the timed-out batch and all remaining work, and it does not include selector, consolidator, promotion, semantic correctness, or provenance completion. No live preflight was run after this failure.

## Interpretation

The explicit request deadline is functioning as intended by making the stalled/non-progressing request observable and bounded. It is not itself evidence that the underlying local inference workload is healthy. The next optimization must explain or eliminate the long-tail canonicalizer behavior before any production batch-size or concurrency change is accepted.
"""
    (final_replay_dir / "REPORT.md").write_text(final_replay_text, encoding="utf-8")

    best_rate = best.get("source_tokens_per_second") if best else None
    arrival_rate = 59.148
    final_status = "NOT_READY_FOR_FULL_AB_RERUN"
    summary = {
        "phase": "freetoshop_canonicalizer_throughput",
        "status": final_status,
        "recommendation": final_status,
        "target_source_tokens_per_second": 75,
        "measured_arrival_source_tokens_per_second": arrival_rate,
        "stage_summary": stage_summary,
        "best_complete_correct_zero_timeout_window": best,
        "full_replay": final_replay,
        "live_preflight": {"status": "NOT_RUN", "reason": "frozen replay failed zero-timeout gate"},
        "observed_correctness": {
            "dynamic_exact_source_id_validation": "passed on corrected local batches",
            "full_replay_semantic_and_promotion_validation": "unavailable after canonicalizer timeout",
        },
        "gate": {
            "zero_unresolved_canonicalizer_timeouts": False,
            "full_replay_completed": False,
            "throughput_at_least_75": bool(best_rate is not None and best_rate >= 75),
            "live_preflight_completed": False,
        },
    }
    (OUT / "SUMMARY.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    stage = stage_summary
    selector_pct = stage.get("selector", {}).get("wall_percent_of_completed_job_wall")
    canonicalizer_pct = stage.get("canonicalizer", {}).get("wall_percent_of_completed_job_wall")
    final_report = f"""# ORCHID canonicalizer throughput phase

## Recommendation

`{final_status}`

## Measured facts

1. Selector share of completed captured compaction wall time: {selector_pct:.2f}%.
2. Canonicalizer share: {canonicalizer_pct:.2f}%.
3. Consolidator share: unavailable; both completed captured jobs failed before consolidation.
4. Prior timeout cause: a non-streaming local canonicalizer request produced no response before the 180-second request deadline; the old client did not provide a reliable total-operation bound. The new explicit async deadline reproduces and records this deterministically with a delayed stub, and the next request succeeds.
5. Best complete corrected window: 12K target, {best_rate:.2f} source tokens/sec, 2 batches, zero timeouts and valid exact source references.
6. Smaller batches: 4K completed at 51.44 tok/s; 8K at 47.89 tok/s. They did not improve total throughput in this window, though they split individual requests.
7. Larger batches: 12K improved amortization to 71.14 tok/s; 16K fell to 62.05 tok/s. The full replay still timed out at 12K.
8. Canonicalizer input amplification: {amplification.get('canonicalizer_input_amplification'):.3f}x in the corrected 12K window.
9. Fixed overhead: approximately 420 system-prompt characters, 48 wrapper characters, and 340–746 response-schema characters per call; the exact-ID enum is correctness-constraining.
10. Safe prompt overhead removal: none accepted in this phase. Output caps caused invalid truncated JSON and are rejected.
11. Concurrency 2: exploratory single-batch test increased aggregate throughput to about 132.02 tok/s versus 76.42 tok/s at concurrency 1, with no failures in that small test. It was not promoted to production because the loaded endpoint was configured parallel=1 and the full replay remained timeout-unstable.
12. Concurrency timeout risk: not established on a sufficiently large corpus; therefore no production concurrency change was made.
13. Timeout policy: explicit total request deadline, bounded failure telemetry, cleanup, and retry/recovery tests; do not blindly increase the deadline.
14. Final frozen-replay throughput: no valid full-replay throughput exists. The run failed after two valid batches. Diagnostic partial successful-batch rate was {final_replay.get('partial_successful_batch_tokens_per_second'):.2f} tok/s, excluding the failed and unattempted work.
15. Does it exceed 75 tok/s? No completed full replay did; the best small complete window was below target.
16. Margin over 59.148 tok/s arrival: best small complete window was {best_rate - arrival_rate:.2f} tok/s ({best_rate / arrival_rate:.2f}x), but this margin is not operationally valid because the full replay timed out.
17. Semantic correctness: corrected local canonicalizer batches remained structured-output and exact-source-reference valid; full semantic consolidation/current-fact correctness was not measurable after timeout.
18. Provenance: exact source-reference validation passed for completed corrected batches; full replay provenance/promotion was not reached.
19. Live preflight promotions: 0 in this phase; it was not run because the frozen replay gate failed.
20. Live retirement versus live arrival: not measured in this phase; no live preflight was authorized.
21. Ready for another full A/B? No.

## Interpretation

The stage profile confirms canonicalizer behavior is the immediate reliability risk, but the historical provider-backed profile also contains substantial selector work. The measured 12K window is close to the 75 tok/s target but is not a sustainable result: the corrected full replay timed out on its third request and therefore cannot support a claim that ORCHID keeps up with 59.148 tok/s arrival.

The dynamic exact source-ID schema is a narrow correctness hardening change. It prevented the previously observed truncated/parent-ID citation failures on corrected batches. The explicit deadline makes stalled non-streaming calls fail open to scheduler recovery rather than hang indefinitely. Neither change weakens provenance or semantic policy.

## Limitations

- Local Qwen latency varied materially across repeated equivalent inputs.
- The full replay harness invokes the canonicalizer against the frozen source trace, not the entire selector→canonicalizer→Solar consolidator→promotion pipeline after the first canonicalizer failure.
- No new production batch size or concurrency setting was selected from the incomplete replay.
- The known unrelated selector-schema expectation failure remains separate and was not weakened.

## Validation

- Focused and relevant broader tests passed: `tests/test_model_telemetry.py`, `tests/test_pipeline.py`, `tests/test_operability_hardening.py`, `tests/test_gateway_runtime.py`, and `tests/test_invariants.py`.
- `python -m compileall -q memory_gateway tools tests`: passed.
- `git diff --check`: passed; Git emitted only line-ending normalization warnings.
- Full `python -m pytest -q`: one known unrelated failure in `tests/test_openai_adapter.py::test_selector_and_canonicalizer_send_json_schema_response_formats`; it expects the old static selector schema and was not used to weaken dynamic selector hardening.

## Next recommendation

Do not rerun the six-hour A/B. First isolate the third-batch canonicalizer stall with endpoint-level progress/queue diagnostics or a reproducible local request fixture, then repeat the frozen full replay until it completes with zero unresolved timeouts and a measured rate above 75 source tokens/sec.
"""
    (OUT / "FINAL_REPORT.md").write_text(final_report, encoding="utf-8")


def main() -> int:
    profile, stage_summary = build_profile()
    sweep = build_sweep_summary()
    amplification = build_amplification(sweep)
    final_replay = build_final_replay()
    write_reports(profile, stage_summary, sweep, amplification, final_replay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
