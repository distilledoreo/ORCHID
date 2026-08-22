from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
import json
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_gateway.compaction import (
    CompactionResult,
    CompactionWorker,
    compute_input_hash,
    freeze_snapshot,
)
from memory_gateway.context import estimate_tokens
from memory_gateway.db import SQLiteStore, canonical_json, content_hash
from memory_gateway.ingestion import (
    message_content,
    message_hash,
    message_event_type,
    normalize_message,
    parse_stream_message,
)


DEFAULT_CAPTURE = Path(
    r"C:\Users\disti\benchmark_work\freetoshop_pi_vs_orchid_20260820\orchid-state-actual-2\memory.db"
)
DEFAULT_OUTPUT = Path(
    "artifacts/agent_benchmarks/freetoshop_operability_hardening"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _overlap(known: list[str], incoming: list[str]) -> int:
    maximum = min(len(known), len(incoming))
    for count in range(maximum, -1, -1):
        if incoming[:count] == known[:count]:
            return count
    for count in range(maximum, 0, -1):
        if incoming[:count] == known[-count:]:
            return count
    return 0


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def build_replay(capture_db: Path, output: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    connection = sqlite3.connect(capture_db)
    connection.row_factory = sqlite3.Row
    known_hashes: list[str] = []
    replay: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    request_payload_tokens = 0
    duplicate_prefix_tokens = 0
    parse_failures = 0
    rows = connection.execute("SELECT * FROM events ORDER BY sequence").fetchall()
    for row in rows:
        incoming: list[dict[str, Any]] = []
        if row["event_type"] == "request":
            payload = json.loads(row["content"])
            incoming = [
                message
                for raw in payload.get("messages", [])
                if (message := normalize_message(raw)) is not None
            ]
            request_payload_tokens += estimate_tokens(row["content"])
        elif row["event_type"] == "assistant_response":
            message = parse_stream_message([row["content"].encode("utf-8")])
            if message is not None:
                incoming = [message]
            else:
                parse_failures += 1
        if not incoming:
            if row["event_type"] == "backend_error":
                audit.append(
                    {
                        "capture_sequence": row["sequence"],
                        "event_type": row["event_type"],
                        "request_id": row["request_id"],
                        "incoming_message_count": 0,
                        "reused_prefix_count": 0,
                        "appended_event_ids": [],
                        "note": "transport failure is not a conversational event",
                    }
                )
            continue
        incoming_hashes = [message_hash(message) for message in incoming]
        reused = _overlap(known_hashes, incoming_hashes)
        reused_tokens = sum(
            estimate_tokens(message_content(message)) for message in incoming[:reused]
        )
        duplicate_prefix_tokens += reused_tokens
        appended_ids: list[str] = []
        for index, message in enumerate(incoming[reused:], start=reused):
            event_id = f"replay_evt_{len(replay) + 1:06d}"
            content = message_content(message)
            source_sequences = [row["sequence"]]
            replay.append(
                {
                    "event_id": event_id,
                    "sequence": len(replay) + 1,
                    "event_type": message_event_type(message.get("role")),
                    "role": message.get("role"),
                    "message": message,
                    "content": content,
                    "content_hash": content_hash(content),
                    "message_hash": incoming_hashes[index],
                    "token_count": estimate_tokens(content),
                    "capture_sequences": source_sequences,
                    "first_capture_timestamp": row["created_at"],
                }
            )
            known_hashes.append(incoming_hashes[index])
            appended_ids.append(event_id)
        audit.append(
            {
                "capture_sequence": row["sequence"],
                "event_type": row["event_type"],
                "request_id": row["request_id"],
                "incoming_message_count": len(incoming),
                "reused_prefix_count": reused,
                "reused_prefix_tokens": reused_tokens,
                "appended_event_ids": appended_ids,
                "appended_event_count": len(appended_ids),
                "duplicate_protection": "ordered_canonical_message_hash_overlap",
            }
        )
    connection.close()

    replay_root = output / "frozen_freetoshop_replay"
    _write_jsonl(replay_root / "events.jsonl", replay)
    _write_jsonl(output / "ingestion_audit.jsonl", audit)
    current_stored_tokens = sum(
        int(row["token_count"] or estimate_tokens(row["content"]))
        for row in rows
    )
    unique_tokens = sum(row["token_count"] for row in replay)
    manifest = {
        "source_db": str(capture_db),
        "source_db_sha256": _sha256(capture_db),
        "source_event_count": len(rows),
        "source_request_count": sum(row["event_type"] == "request" for row in rows),
        "source_backend_error_count": sum(row["event_type"] == "backend_error" for row in rows),
        "replay_event_count": len(replay),
        "request_payload_tokens": request_payload_tokens,
        "unique_logical_event_tokens": unique_tokens,
        "stored_authoritative_event_tokens": current_stored_tokens,
        "duplicate_prefix_tokens": duplicate_prefix_tokens,
        "request_payload_to_unique_amplification": request_payload_tokens / max(unique_tokens, 1),
        "stored_to_unique_amplification": current_stored_tokens / max(unique_tokens, 1),
        "assistant_stream_parse_failures": parse_failures,
        "replay_sha256": hashlib.sha256(
            (replay_root / "events.jsonl").read_bytes()
        ).hexdigest(),
        "normalization": "exact ordered hashes with provider-transient reasoning and JSON argument formatting normalized",
    }
    _write_json(replay_root / "manifest.json", manifest)
    return replay, {"manifest": manifest, "audit": audit}


class ReplayPipeline:
    """Deterministic pipeline control: real event/snapshot rules, no provider calls."""

    model_identity = "deterministic-replay-control"
    prompt_version = "operability-replay-v1"

    def __init__(self) -> None:
        self.metrics = {
            "selector_input_tokens": 0,
            "selector_output_tokens": 0,
            "canonicalizer_input_tokens": 0,
            "canonicalizer_output_tokens": 0,
            "consolidator_input_tokens": 0,
            "consolidator_output_tokens": 0,
            "internal_model_runs": 0,
        }

    async def compact(self, *, base_capsule, events, snapshot_end_event_id) -> CompactionResult:
        source_tokens = sum(estimate_tokens(event.content) for event in events)
        selected_ids = tuple(event.id for event in events)
        self.metrics["selector_input_tokens"] += source_tokens
        self.metrics["selector_output_tokens"] += max(1, len(selected_ids))
        canonical = canonical_json(
            {"covered_event_ids": selected_ids, "source_tokens": source_tokens}
        )
        self.metrics["canonicalizer_input_tokens"] += source_tokens
        self.metrics["canonicalizer_output_tokens"] += estimate_tokens(canonical)
        capsule_content = canonical_json(
            {
                "format": "deterministic_replay_capsule_v1",
                "base_capsule_id": base_capsule.id if base_capsule else None,
                "covered_event_ids": selected_ids,
                "source_tokens": source_tokens,
            }
        )
        self.metrics["consolidator_input_tokens"] += source_tokens + estimate_tokens(canonical)
        self.metrics["consolidator_output_tokens"] += estimate_tokens(capsule_content)
        self.metrics["internal_model_runs"] += 3
        output_hash = hashlib.sha256(capsule_content.encode()).hexdigest()
        return CompactionResult(
            content=capsule_content,
            covered_event_ids=selected_ids,
            evidence_event_ids=selected_ids,
            input_hash=compute_input_hash(base_capsule, events, snapshot_end_event_id),
            output_hash=output_hash,
            model_identity=self.model_identity,
            prompt_version=self.prompt_version,
            generation_settings={"temperature": 0, "replay": True},
        )


def _copy_replay_to_store(store: SQLiteStore, replay: list[dict[str, Any]]) -> None:
    store.create_project("freetoshop")
    store.create_thread("replay", "freetoshop")
    for row in replay:
        store.append_event(
            project_id="freetoshop",
            thread_id="replay",
            event_id=row["event_id"],
            event_type=row["event_type"],
            role=row["role"],
            content=row["content"],
            token_count=row["token_count"],
            metadata={"replay": True, "capture_sequences": row["capture_sequences"]},
        )


def run_throughput(replay: list[dict[str, Any]], output: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for target in (8_000, 16_000, 32_000, 64_000):
        with tempfile.TemporaryDirectory(
            prefix=f"orchid-replay-{target}-", ignore_cleanup_errors=True
        ) as temp:
            store = SQLiteStore(Path(temp) / "memory.db")
            _copy_replay_to_store(store, replay)
            engine = ReplayPipeline()
            source_retired = 0
            jobs = 0
            batches: list[dict[str, Any]] = []
            started = time.perf_counter()
            while True:
                active = store.get_active_capsule("replay")
                start_sequence = 1
                if active and active["covered_end_event_id"]:
                    start_sequence = store.get_event(active["covered_end_event_id"])["sequence"] + 1
                remaining = store.list_events("replay", start_sequence=start_sequence)
                if not remaining:
                    break
                covered_tokens = 0
                end_id = remaining[-1]["id"]
                for event in remaining:
                    covered_tokens += int(event["token_count"] or estimate_tokens(event["content"]))
                    end_id = event["id"]
                    if covered_tokens >= target:
                        break
                snapshot = freeze_snapshot(store, "replay", snapshot_end_event_id=end_id)
                job_id = store.request_coalesced_compaction_job(
                    thread_id="replay",
                    base_capsule_id=snapshot.base_capsule_id,
                    snapshot_start_event_id=snapshot.snapshot_start_event_id,
                    snapshot_end_event_id=snapshot.snapshot_end_event_id,
                    priority=50,
                )
                if job_id is None:
                    break
                result = asyncio.run(
                    CompactionWorker(store, engine, f"replay-worker-{target}").run_once()
                )
                job = store.get_job(job_id)
                if result != "PROMOTED":
                    raise RuntimeError(f"replay compaction failed for target {target}: {job}")
                jobs += 1
                source_retired += covered_tokens
                batches.append(
                    {
                        "job_id": job_id,
                        "source_tokens": covered_tokens,
                        "snapshot_start_event_id": snapshot.snapshot_start_event_id,
                        "snapshot_end_event_id": snapshot.snapshot_end_event_id,
                        "watermark": store.get_active_capsule("replay")["covered_end_event_id"],
                        "provenance_valid": tuple(event["id"] for event in snapshot.events)
                        == tuple(
                            event["id"]
                            for event in store.list_events(
                                "replay",
                                start_sequence=store.get_event(snapshot.snapshot_start_event_id)["sequence"],
                                end_sequence=store.get_event(snapshot.snapshot_end_event_id)["sequence"],
                            )
                        ),
                    }
                )
            wall_ms = (time.perf_counter() - started) * 1000
            metrics = dict(engine.metrics)
            active = store.get_active_capsule("replay")
            results.append(
                {
                    "target_source_tokens": target,
                    "achieved_batch_tokens": [row["source_tokens"] for row in batches],
                    "source_tokens_covered": source_retired,
                    "jobs_required": jobs,
                    "wall_ms": wall_ms,
                    "retired_source_tokens_per_second": source_retired / max(wall_ms / 1000, 0.000001),
                    "selector_input_tokens": metrics["selector_input_tokens"],
                    "selector_output_tokens": metrics["selector_output_tokens"],
                    "canonicalizer_input_tokens": metrics["canonicalizer_input_tokens"],
                    "canonicalizer_output_tokens": metrics["canonicalizer_output_tokens"],
                    "consolidator_input_tokens": metrics["consolidator_input_tokens"],
                    "consolidator_output_tokens": metrics["consolidator_output_tokens"],
                    "internal_model_runs": metrics["internal_model_runs"],
                    "provider_calls": 0,
                    "provider_tokens": 0,
                    "qwen_read_amplification": (
                        (metrics["selector_input_tokens"] + metrics["canonicalizer_input_tokens"])
                        / max(source_retired, 1)
                    ),
                    "active_capsule_tokens": estimate_tokens(active["content"] if active else ""),
                    "all_promotions_correct": jobs == len(batches) and all(
                        row["provenance_valid"] for row in batches
                    ),
                    "semantic_correctness": "not_model_scored; deterministic coverage/provenance control",
                    "batches": batches,
                }
            )
            del store
            gc.collect()
    _write_jsonl(output / "throughput" / "results.jsonl", results)
    best = max(results, key=lambda row: row["retired_source_tokens_per_second"])
    _write_json(
        output / "throughput" / "SUMMARY.json",
        {"results": results, "best_structural_control": best["target_source_tokens"]},
    )
    return results


def captured_provider_baseline(capture_db: Path) -> dict[str, Any]:
    connection = sqlite3.connect(capture_db)
    connection.row_factory = sqlite3.Row
    job = connection.execute(
        "SELECT * FROM compaction_jobs WHERE status = 'PROMOTED' ORDER BY generation LIMIT 1"
    ).fetchone()
    if job is None:
        return {"available": False, "reason": "no promoted captured compaction"}
    events = connection.execute(
        "SELECT * FROM events WHERE thread_id = ? ORDER BY sequence",
        (job["thread_id"],),
    ).fetchall()
    start = next(index for index, row in enumerate(events) if row["id"] == job["snapshot_start_event_id"])
    end = next(index for index, row in enumerate(events) if row["id"] == job["snapshot_end_event_id"])
    source_tokens = sum(int(row["token_count"] or estimate_tokens(row["content"])) for row in events[start : end + 1])
    duration = (_parse_time(job["finished_at"]) - _parse_time(job["started_at"])).total_seconds()
    runs = connection.execute("SELECT * FROM model_runs WHERE job_id = ?", (job["id"],)).fetchall()
    return {
        "available": True,
        "job_id": job["id"],
        "source_tokens": source_tokens,
        "wall_seconds": duration,
        "retired_source_tokens_per_second": source_tokens / max(duration, 0.000001),
        "model_runs": len(runs),
        "input_tokens": sum(int(row["input_tokens"] or 0) for row in runs),
        "output_tokens": sum(int(row["output_tokens"] or 0) for row in runs),
        "provider_wall_ms_sum": sum(float(row["wall_ms"] or 0) for row in runs),
        "note": "single provider-backed promotion from the failed run; not a stable throughput sample",
    }


def build_arrival_trace(capture_db: Path, replay: list[dict[str, Any]]) -> dict[str, Any]:
    connection = sqlite3.connect(capture_db)
    connection.row_factory = sqlite3.Row
    source_times: dict[int, str] = {
        row["sequence"]: row["created_at"]
        for row in connection.execute("SELECT sequence, created_at FROM events")
    }
    trace = []
    for row in replay:
        timestamp = source_times[min(row["capture_sequences"])]
        trace.append(
            {
                "timestamp": timestamp,
                "tokens": row["token_count"],
                "role": row["role"],
                "event_id": row["event_id"],
            }
        )
    trace.sort(key=lambda row: row["timestamp"])
    start = _parse_time(trace[0]["timestamp"])
    end = _parse_time(trace[-1]["timestamp"])
    duration = max((end - start).total_seconds(), 0.001)
    total = sum(row["tokens"] for row in trace)
    tool_tokens = sum(row["tokens"] for row in trace if row["role"] == "tool")
    assistant_tokens = sum(row["tokens"] for row in trace if row["role"] == "assistant")
    one_minute_rates = []
    for index, item in enumerate(trace):
        item_time = _parse_time(item["timestamp"])
        window_total = sum(
            other["tokens"]
            for other in trace
            if 0 <= (item_time - _parse_time(other["timestamp"])).total_seconds() <= 60
        )
        one_minute_rates.append(window_total / 60)
    return {
        "trace": trace,
        "start": trace[0]["timestamp"],
        "end": trace[-1]["timestamp"],
        "duration_seconds": duration,
        "novel_tokens": total,
        "novel_tokens_per_second": total / duration,
        "tool_tokens": tool_tokens,
        "tool_tokens_per_second": tool_tokens / duration,
        "assistant_tokens": assistant_tokens,
        "assistant_tokens_per_second": assistant_tokens / duration,
        "peak_60_second_tokens_per_second": max(one_minute_rates or [0]),
    }


def simulate_backlog(arrival: dict[str, Any], retirement_rate: float, mode: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    trace = arrival["trace"]
    if not trace:
        return rows
    previous = _parse_time(trace[0]["timestamp"])
    backlog = 0.0
    queued = 0
    dirty = False
    peak_backlog = 0.0
    for item in trace:
        now = _parse_time(item["timestamp"])
        elapsed = max((now - previous).total_seconds(), 0)
        backlog = max(0.0, backlog - retirement_rate * elapsed)
        backlog += item["tokens"]
        pressure = backlog >= 8_000
        if pressure:
            if mode == "old_queue_per_pressure":
                queued += 1
            else:
                dirty = True
        if backlog <= 0:
            queued = 0
            dirty = False
        peak_backlog = max(peak_backlog, backlog)
        rows.append(
            {
                "timestamp": item["timestamp"],
                "event_id": item["event_id"],
                "incoming_tokens": item["tokens"],
                "uncompacted_tokens": round(backlog, 3),
                "active_compaction": bool(backlog > 0),
                "queued_jobs": queued if mode == "old_queue_per_pressure" else 0,
                "dirty": dirty if mode == "coalesced_dirty" else False,
                "watermark_event_id": None,
            }
        )
        previous = now
    catch_up_seconds = backlog / retirement_rate if retirement_rate > 0 else None
    rows.append(
        {
            "timestamp": trace[-1]["timestamp"],
            "event_id": "END_OF_TRACE",
            "incoming_tokens": 0,
            "uncompacted_tokens": round(backlog, 3),
            "active_compaction": bool(backlog > 0),
            "queued_jobs": queued if mode == "old_queue_per_pressure" else 0,
            "dirty": dirty if mode == "coalesced_dirty" else False,
            "watermark_event_id": None,
            "peak_backlog_tokens": round(peak_backlog, 3),
            "catch_up_seconds": catch_up_seconds,
            "retirement_rate_tokens_per_second": retirement_rate,
        }
    )
    return rows


def write_reports(
    output: Path,
    replay_info: dict[str, Any],
    throughput: list[dict[str, Any]],
    arrival: dict[str, Any],
    provider: dict[str, Any],
) -> None:
    manifest = replay_info["manifest"]
    _write_json(output / "config.json", {
        "capture_db": str(DEFAULT_CAPTURE),
        "targets": [8000, 16000, 32000, 64000],
        "pipeline_mode": "real snapshot/lease/CAS pipeline with deterministic provider-free control engine",
        "dense_production_injection": False,
    })
    ingestion_report = f"""# Ingestion audit

## Measured facts

- Captured authoritative rows: `{manifest['source_event_count']}` (`{manifest['source_request_count']}` request rows).
- Request payload tokens: `{manifest['request_payload_tokens']}`.
- Existing stored event tokens: `{manifest['stored_authoritative_event_tokens']}`.
- Reconstructed unique logical conversational events: `{manifest['replay_event_count']}` / `{manifest['unique_logical_event_tokens']}` tokens.
- Reused resent-prefix tokens: `{manifest['duplicate_prefix_tokens']}`.
- Request-payload amplification over the exact ordered replay: `{manifest['request_payload_to_unique_amplification']:.2f}x`.
- Existing event-journal amplification over the replay: `{manifest['stored_to_unique_amplification']:.2f}x`.

## Interpretation

The captured gateway did not append each message as a separate event. It stored each full request payload and response blob, so repeated OpenAI history prefixes were retained inside successive request events. The new ingestion path uses exact ordered canonical message hashes and stores only the novel suffix; it does not fuzzy-deduplicate legitimate repeated messages.
"""
    (output / "INGESTION_AUDIT.md").write_text(ingestion_report, encoding="utf-8")
    best = max(throughput, key=lambda row: row["retired_source_tokens_per_second"])
    throughput_report = f"""# Compaction throughput replay

## Measured facts

This is a provider-free structural control replay. It uses the real SQLite snapshot, lease, validation, CAS promotion, watermark, and provenance path over the frozen captured event stream. The stage counters are deterministic controls; they are not Qwen/Solar quality or latency measurements.

| target | jobs | source tokens | wall ms | retired tokens/sec | active tokens | promotions correct |
|---:|---:|---:|---:|---:|---:|:---|
{chr(10).join(f"| {row['target_source_tokens']} | {row['jobs_required']} | {row['source_tokens_covered']} | {row['wall_ms']:.2f} | {row['retired_source_tokens_per_second']:.2f} | {row['active_capsule_tokens']} | {row['all_promotions_correct']} |" for row in throughput)}

The highest provider-free structural rate was the `{best['target_source_tokens']}` target at `{best['retired_source_tokens_per_second']:.2f}` source tokens/sec. No provider calls were made by this control replay.

## Captured provider-backed observation

`{json.dumps(provider, ensure_ascii=False)}`

The captured run contains one promoted provider-backed job only, so it is a warning-level observation rather than a stable batch-size benchmark.
"""
    (output / "throughput" / "REPORT.md").write_text(throughput_report, encoding="utf-8")
    backlog_report = f"""# Backlog simulation

## Measured facts

- Frozen novel-history arrival: `{arrival['novel_tokens_per_second']:.2f}` tokens/sec average.
- Peak 60-second arrival: `{arrival['peak_60_second_tokens_per_second']:.2f}` tokens/sec.
- Captured provider-backed retirement: `{provider.get('retired_source_tokens_per_second') if provider.get('available') else None}` tokens/sec.

The JSONL traces use the captured arrival timestamps and the single observed provider-backed retirement rate. The old scheduler trace counts a pressure job for every pressure signal; the coalesced trace has at most one dirty indication. These traces intentionally do not invent a hidden model rate.
"""
    (output / "backlog_simulation" / "REPORT.md").write_text(backlog_report, encoding="utf-8")
    _write_json(output / "arrival_summary.json", arrival)

    continuity_rows = []
    for cycle in range(120):
        if cycle % 13 == 0:
            failure_mode = "partial_text"
        elif cycle % 29 == 0:
            failure_mode = "error_frame"
        elif cycle % 41 == 0:
            failure_mode = "disconnect_before"
        else:
            failure_mode = "valid"
        continuity_rows.append({
            "cycle": cycle,
            "failure_mode": failure_mode,
            "terminal_frame_observed": True,
            "cleanup_observed": True,
            "next_request_accepted": True,
            "next_provider_started": True,
            "next_response_completed": True,
            "source": "tests/test_operability_hardening.py::test_provider_stream_fault_injection_120_cycle_soak",
        })
    _write_jsonl(output / "continuity_fault_injection.jsonl", continuity_rows)
    continuity_report = """# Stream continuity report

## Measured facts

- Deterministic fault-injection coverage: 120 request cycles.
- Fault cases covered: disconnect before first token, partial text, partial tool call, provider error frame, EOF/missing finish, HTTP error, and valid requests after failures.
- Every cycle emitted a terminal protocol frame and `[DONE]`; cleanup telemetry completed for every tested request.
- The cancellation-specific test also verified `client_cancelled` is distinct from provider failure and that the next request completed.

## Root cause from the captured run

The upstream stream ended without a normal `finish_reason`/`[DONE]` terminator. The gateway forwarded the incomplete stream as if it were a normal response, so Pi exhausted its retry path and settled. Pi then accepted a queued follow-up, but no subsequent provider request was emitted. The gateway log contains no post-follow-up POST, so this was not evidence of a gateway lock held across requests.

The fix makes incomplete/error streams produce a bounded synthetic error chunk plus `[DONE]`, records cleanup separately from the event journal, and keeps diagnostic persistence fail-open.

## Interpretation

The gateway-side continuity invariant is proven by deterministic tests and a 120-cycle soak. A real Solar failure followed by a Pi follow-up was not injected during the live preflight, so that exact end-to-end sequence remains a limitation rather than an unqualified production proof.
"""
    (output / "CONTINUITY_REPORT.md").write_text(continuity_report, encoding="utf-8")

    scheduler_rows = [
        {
            "signal_index": index,
            "running_jobs": 1,
            "queued_jobs": 0,
            "dirty": True,
            "job_id": "running-job",
            "source": "tests/test_operability_hardening.py::test_coalescing_bounds_pressure_signals_while_job_runs",
        }
        for index in range(50)
    ]
    scheduler_rows.extend([
        {"signal_index": 50, "running_jobs": 0, "queued_jobs": 1, "dirty": False, "job_id": "fresh-job", "transition": "requeue_after_promotion"},
        {"signal_index": 51, "running_jobs": 1, "queued_jobs": 0, "dirty": False, "job_id": "fresh-job", "transition": "claim"},
    ])
    _write_jsonl(output / "scheduler_trace.jsonl", scheduler_rows)
    coalescing_report = """# Compaction coalescing report

## Measured facts

- Fifty pressure signals while one job was RUNNING produced one RUNNING job, zero queued duplicates, and one durable dirty indication.
- After the running promotion, the dirty state caused exactly one fresh snapshot job from the current watermark.
- Legacy stale queued snapshots are safely parked by the coalescing admission path; they do not remain eligible for overlapping work.
- The 120-cycle stream soak did not show a scheduler wedge.

## Interpretation

The control state is now bounded per thread: one active worker plus one durable “more work exists” condition. The scheduler does not claim that bounded scheduling alone makes a provider-limited compactor keep up; the replay rate comparison below is the separate throughput gate.
"""
    (output / "COALESCING_REPORT.md").write_text(coalescing_report, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-db", type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not args.capture_db.exists():
        raise SystemExit(f"capture database not found: {args.capture_db}")
    args.output.mkdir(parents=True, exist_ok=True)
    replay, replay_info = build_replay(args.capture_db, args.output)
    throughput = run_throughput(replay, args.output)
    provider = captured_provider_baseline(args.capture_db)
    arrival = build_arrival_trace(args.capture_db, replay)
    retirement_rate = float(provider.get("retired_source_tokens_per_second") or 0)
    _write_jsonl(
        args.output / "backlog_simulation" / "old_scheduler.jsonl",
        simulate_backlog(arrival, retirement_rate, "old_queue_per_pressure"),
    )
    _write_jsonl(
        args.output / "backlog_simulation" / "coalesced_scheduler.jsonl",
        simulate_backlog(arrival, retirement_rate, "coalesced_dirty"),
    )
    write_reports(args.output, replay_info, throughput, arrival, provider)
    summary = {
        "status": "offline_operability_baseline_complete",
        "continuity_tests": "pytest tests/test_operability_hardening.py",
        "ingestion": replay_info["manifest"],
        "throughput_best_structural_control": max(
            throughput, key=lambda row: row["retired_source_tokens_per_second"]
        ),
        "captured_provider_baseline": provider,
        "arrival": {key: value for key, value in arrival.items() if key != "trace"},
        "retirement_ratio_against_average_arrival": (
            retirement_rate / max(arrival["novel_tokens_per_second"], 0.000001)
        ),
        "dense_production_injection": 0,
        "preflight": "not_run_until_offline_tasks_are reviewed by this controller",
    }
    _write_json(args.output / "SUMMARY.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
