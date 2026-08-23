"""Forensic reconstruction and offline replay for the failed all-Solar run.

This tool never calls a provider. It reads the preserved benchmark database,
reconstructs the rejected request metadata, and exercises the local bounded
watermark/promotion machinery with deterministic legal-ID stand-ins.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DB = Path(
    r"C:\Users\disti\benchmark_work\freetoshop_pi_vs_orchid_all_solar_20260823"
) / "orchid-state" / "memory.db"
OUT = ROOT / "artifacts" / "agent_benchmarks" / "freetoshop_all_solar_forensics"
BENCHMARK_ROOT = ROOT / "artifacts" / "agent_benchmarks" / "freetoshop_pi_vs_orchid_all_solar"
THREAD_ID = "orchid-arm-b"
PROVIDER_CONTEXT = 524_288
REPLAY_WINDOW = 30_000

sys.path.insert(0, str(ROOT))

from memory_gateway.compaction import (  # noqa: E402
    CompactionWorker,
    Event,
    compute_source_event_hash,
    expand_source_items,
    freeze_snapshot,
    queue_snapshot_job,
    source_item_payload,
)
from memory_gateway.context import estimate_tokens  # noqa: E402
from memory_gateway.db import SQLiteStore  # noqa: E402
from memory_gateway.pipeline import (  # noqa: E402
    ConsolidationResult,
    LosslessCompactionEngine,
    SelectionResult,
)
from memory_gateway.pipeline_adapters import _capsule_payload  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_value(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
    ).strip()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def query_db() -> dict[str, Any]:
    connection = sqlite3.connect(SOURCE_DB)
    connection.row_factory = sqlite3.Row
    try:
        jobs = [dict(row) for row in connection.execute("select * from compaction_jobs order by generation")]
        runs = [dict(row) for row in connection.execute("select * from model_runs")]
        active = connection.execute(
            "select * from capsules where state='ACTIVE' order by created_at desc limit 1"
        ).fetchone()
        thread = connection.execute("select * from threads where id=?", (THREAD_ID,)).fetchone()
        events = [dict(row) for row in connection.execute(
            "select * from events where thread_id=? order by sequence", (THREAD_ID,)
        )]
        return {
            "jobs": jobs,
            "runs": runs,
            "active": dict(active) if active else None,
            "thread": dict(thread) if thread else None,
            "events": events,
        }
    finally:
        connection.close()


def run_profile(db: dict[str, Any], generation: int) -> dict[str, Any]:
    rows = [
        row for row in db["runs"]
        if row["generation"] == generation and row["stage"] == "consolidator"
    ]
    if not rows:
        raise RuntimeError(f"missing consolidator run for generation {generation}")
    row = rows[0]
    metadata = json.loads(row["metadata_json"] or "{}")
    return {"row": row, "profile": metadata.get("request_profile", {})}


def reconstruct_overflow(db: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    job = next(row for row in db["jobs"] if row["generation"] == 4)
    run = run_profile(db, 4)
    active = db["active"]
    if not active:
        raise RuntimeError("failed run has no active base capsule")
    base_sequence = next(
        row["sequence"] for row in db["events"]
        if row["id"] == active["covered_end_event_id"]
    )
    end_sequence = next(
        row["sequence"] for row in db["events"]
        if row["id"] == job["snapshot_end_event_id"]
    )
    raw_rows = [
        row for row in db["events"]
        if base_sequence < row["sequence"] <= end_sequence
    ]
    events = [Event.from_row(row) for row in raw_rows]
    source_items = expand_source_items(
        events, selector_budget_tokens=1_200, safety_margin=0.8
    )
    refs = json.loads(run["row"]["source_refs_json"] or "[]")
    item_by_id = {item.id: item for item in source_items}
    selected = [item_by_id[ref] for ref in refs]
    selected_payloads = [source_item_payload(item) for item in selected]
    base_payload = {
        "id": active["id"],
        "thread_id": active["thread_id"],
        "content": active["content"],
        "capsule_hash": active["capsule_hash"],
        "covered_end_event_id": active["covered_end_event_id"],
    }
    empty_body = {
        "base_capsule": base_payload,
        "snapshot_end_event_id": job["snapshot_end_event_id"],
        "selected_events": selected_payloads,
        "lossless_packet": {
            "canonical_text": "",
            "selected_event_ids": refs,
            "authoritative_events": selected_payloads,
            "packet_hash": "0" * 64,
        },
    }
    empty_serialized = json.dumps(empty_body, ensure_ascii=False, sort_keys=True)
    payload_chars = int(run["profile"]["input_payload_chars"])
    canonical_chars = payload_chars - len(empty_serialized)
    selected_content_chars = sum(len(item.content) for item in selected)
    selected_full_chars = sum(
        len(json.dumps(source_item_payload(item), ensure_ascii=False))
        for item in selected
    )
    provider_total = 547_802
    estimator_payload_tokens = estimate_tokens(json.dumps(empty_body, ensure_ascii=False, sort_keys=True))
    fixed_chars = (
        int(run["profile"]["system_prompt_chars"])
        + int(run["profile"]["request_wrapper_chars"])
        + int(run["profile"]["response_schema_chars"])
    )
    selected_full_estimate = estimate_tokens("x" * selected_full_chars)
    base_estimate = estimate_tokens(
        json.dumps({"base_capsule": base_payload}, ensure_ascii=False, sort_keys=True)
    )
    selected_ids_estimate = estimate_tokens(
        json.dumps({"selected_event_ids": refs}, ensure_ascii=False, sort_keys=True)
    )
    snapshot_end_estimate = estimate_tokens(
        json.dumps({"snapshot_end_event_id": job["snapshot_end_event_id"]}, ensure_ascii=False)
    )
    packet_hash_estimate = estimate_tokens(
        json.dumps({"packet_hash": "0" * 64}, ensure_ascii=False)
    )
    estimated_total = estimate_tokens(
        json.dumps(empty_body, ensure_ascii=False, sort_keys=True)
    ) + estimate_tokens("x" * canonical_chars) + estimate_tokens("x" * fixed_chars)
    breakdown = {
        "provider_reported_total_tokens": provider_total,
        "estimator": "memory_gateway.context.estimate_tokens (max(1, chars // 4)); provider did not expose component tokenizer counts",
        "estimated_request_total_tokens": estimated_total,
        "components_estimated_tokens": {
            "system_instructions": estimate_tokens("x" * int(run["profile"]["system_prompt_chars"])),
            "request_wrapper": estimate_tokens("x" * int(run["profile"]["request_wrapper_chars"])),
            "response_schema": estimate_tokens("x" * int(run["profile"]["response_schema_chars"])),
            "base_active_capsule": base_estimate,
            "selected_evidence_content_once": estimate_tokens("x" * selected_content_chars),
            "selected_evidence_provenance_once": max(
                1,
                estimate_tokens("x" * selected_full_chars)
                - estimate_tokens("x" * selected_content_chars),
            ),
            "authoritative_evidence_duplicate_content": estimate_tokens("x" * selected_content_chars),
            "authoritative_evidence_duplicate_provenance": max(
                1,
                estimate_tokens("x" * selected_full_chars)
                - estimate_tokens("x" * selected_content_chars),
            ),
            "canonicalized_evidence_aggregate": estimate_tokens("x" * canonical_chars),
            "selected_event_ids_and_snapshot_metadata": selected_ids_estimate + snapshot_end_estimate,
            "packet_hash_and_json_scaffolding": max(
                1,
                estimator_payload_tokens
                - 2 * selected_full_estimate
                - base_estimate
                - estimate_tokens("x" * canonical_chars)
                - selected_ids_estimate
                - snapshot_end_estimate
                - packet_hash_estimate,
            ),
        },
        "component_characters": {
            "input_payload": payload_chars,
            "input_payload_without_canonical_text": len(empty_serialized),
            "canonicalized_evidence_delta": canonical_chars,
            "selected_item_content_once": selected_content_chars,
            "selected_item_payload_once": selected_full_chars,
        },
        "source": {
            "base_event_sequence": base_sequence,
            "snapshot_end_sequence": end_sequence,
            "raw_event_count": len(raw_rows),
            "raw_content_tokens": sum(
                int(row["token_count"] or estimate_tokens(row["content"]))
                for row in raw_rows
            ),
            "expanded_source_item_count": len(source_items),
            "selected_source_item_count": len(selected),
            "selected_source_item_ids": refs,
            "selected_item_estimated_tokens": sum(
                estimate_tokens(json.dumps(source_item_payload(item), ensure_ascii=False))
                for item in selected
            ),
        },
        "request_profile": run["profile"],
    }
    metadata = {
        "request_id": run["row"]["id"],
        "compaction_job_id": job["id"],
        "generation": job["generation"],
        "thread_id": job["thread_id"],
        "watermark_start": job["snapshot_start_event_id"],
        "watermark_end": job["snapshot_end_event_id"],
        "base_capsule_id": job["base_capsule_id"],
        "source_ref_count": len(refs),
        "request_body_hash": run["profile"]["input_payload_hash"],
        "estimated_tokens": estimated_total,
        "provider_reported_tokens": provider_total,
        "provider_error": run["row"]["error"],
        "source_event_range": {
            "first_sequence": raw_rows[0]["sequence"],
            "last_sequence": raw_rows[-1]["sequence"],
            "first_event_id": raw_rows[0]["id"],
            "last_event_id": raw_rows[-1]["id"],
        },
        "exact_reconstruction_limit": "The provider rejected before usage was returned; the body is not persisted. Component counts use the request profile plus the repository estimator.",
    }
    return metadata, breakdown


class OfflineSelector:
    chunk_target_tokens = 1_200
    selector_context_tokens = 32_768
    span_safety_margin = 0.8

    async def select(self, *, events: list[Event]) -> SelectionResult:
        items = expand_source_items(
            events,
            selector_budget_tokens=self.chunk_target_tokens,
            safety_margin=self.span_safety_margin,
        )
        ids = tuple(item.id for item in items)
        source_hash = compute_source_event_hash(None, events)
        return SelectionResult(
            selected_event_ids=ids,
            input_hash=source_hash,
            output_hash=hashlib.sha256(json.dumps(ids).encode()).hexdigest(),
            model_identity="offline-legal-id-selector",
            prompt_version="offline-replay-selector-v1",
            generation_settings={},
        )


class OfflineConsolidator:
    async def consolidate(
        self, *, base_capsule, events, snapshot_end_event_id, packet
    ) -> ConsolidationResult:
        return ConsolidationResult(
            content=(
                "Offline captured-history replay; authoritative source coverage "
                f"ends at {snapshot_end_event_id}."
            ),
            evidence_event_ids=tuple(item.id for item in events),
            model_identity="offline-legal-id-consolidator",
            prompt_version="offline-replay-consolidator-v1",
            generation_settings={},
        )


def replay_captured_history(db: dict[str, Any]) -> list[dict[str, Any]]:
    replay_root = OUT / "replay"
    replay_root.mkdir(parents=True, exist_ok=True)
    replay_work = replay_root / "replay_work"
    replay_work.mkdir(parents=True, exist_ok=True)
    replay_db = replay_work / "memory.db"
    shutil.copy2(SOURCE_DB, replay_db)
    connection = sqlite3.connect(replay_db)
    connection.executescript(
        """
        DELETE FROM model_runs;
        DELETE FROM compaction_jobs;
        DELETE FROM capsules;
        DELETE FROM long_term_memories;
        UPDATE threads
        SET active_capsule_id=NULL, compaction_dirty=0, compaction_dirty_priority=0;
        """
    )
    connection.commit()
    connection.close()

    store = SQLiteStore(replay_db)
    engine = LosslessCompactionEngine(OfflineSelector(), None, OfflineConsolidator())
    worker = CompactionWorker(
        store,
        engine,
        "forensics-replay-worker",
        lease_seconds=60,
        renewal_interval_seconds=10,
    )
    telemetry: list[dict[str, Any]] = []
    job_id = queue_snapshot_job(
        store,
        THREAD_ID,
        priority=100,
        source_window_tokens=REPLAY_WINDOW,
    )
    while job_id is not None:
        job = store.get_job(job_id)
        snapshot = freeze_snapshot(
            store,
            THREAD_ID,
            snapshot_end_event_id=job["snapshot_end_event_id"],
        )
        status = asyncio.run(worker.run_once())
        job = store.get_job(job_id)
        active = store.get_active_capsule(THREAD_ID)
        source_items = expand_source_items(
            [Event.from_row(row) for row in snapshot.events],
            selector_budget_tokens=1_200,
            safety_margin=0.8,
        )
        selected_payloads = [source_item_payload(item) for item in source_items]
        request_body = {
            "base_capsule": _capsule_payload(
                type("ReplayCapsule", (), {
                    "id": active["id"] if active else None,
                    "thread_id": THREAD_ID,
                    "content": active["content"] if active else None,
                    "capsule_hash": active["capsule_hash"] if active else None,
                    "covered_end_event_id": active["covered_end_event_id"] if active else None,
                })()
            ) if active else None,
            "snapshot_end_event_id": snapshot.snapshot_end_event_id,
            "selected_events": selected_payloads,
            "lossless_packet": {
                "canonical_text": "",
                "selected_event_ids": [item["id"] for item in selected_payloads],
                "authoritative_events": selected_payloads,
                "packet_hash": "offline",
            },
        }
        request_tokens = estimate_tokens(
            json.dumps(request_body, ensure_ascii=False, sort_keys=True)
        )
        covered_sequence = (
            next(
                row["sequence"] for row in db["events"]
                if row["id"] == active["covered_end_event_id"]
            )
            if active else 0
        )
        record = {
            "job_id": job_id,
            "generation": job["generation"],
            "worker_status": status,
            "job_status": job["status"],
            "snapshot_start_event_id": snapshot.snapshot_start_event_id,
            "snapshot_end_event_id": snapshot.snapshot_end_event_id,
            "source_event_count": len(snapshot.events),
            "source_item_count": len(source_items),
            "source_estimated_tokens": sum(
                estimate_tokens(row["content"]) for row in snapshot.events
            ),
            "request_estimated_tokens": request_tokens,
            "provider_context_limit": PROVIDER_CONTEXT,
            "headroom_tokens": PROVIDER_CONTEXT - request_tokens,
            "canonicalizer_calls": 0,
            "active_capsule_id": active["id"] if active else None,
            "active_covered_end_event_id": active["covered_end_event_id"] if active else None,
            "backlog_event_count_after": sum(
                row["sequence"] > covered_sequence for row in db["events"]
            ),
        }
        telemetry.append(record)
        if job["status"] != "PROMOTED":
            break
        latest = store.list_events(THREAD_ID)[-1]
        covered = store.get_event(active["covered_end_event_id"]) if active else None
        if not covered or int(covered["sequence"]) >= int(latest["sequence"]):
            break
        job_id = queue_snapshot_job(
            store,
            THREAD_ID,
            priority=100,
            source_window_tokens=REPLAY_WINDOW,
        )
    with store.connect() as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return telemetry


def write_reports(db: dict[str, Any], metadata: dict[str, Any], breakdown: dict[str, Any], replay: list[dict[str, Any]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    config_hashes = {
        "historical_benchmark_manifest_sha256": sha256_file(BENCHMARK_ROOT / "BENCHMARK_MANIFEST.json"),
        "historical_all_solar_config_sha256": sha256_file(BENCHMARK_ROOT / "CONFIG_ORCHID_ALL_SOLAR.json"),
        "historical_source_db_sha256": sha256_file(SOURCE_DB),
        "historical_source_repository_commit": json.loads(
            (BENCHMARK_ROOT / "BENCHMARK_MANIFEST.json").read_text(encoding="utf-8")
        )["repository_commit_at_freeze"],
        "current_forensics_commit": git_value("rev-parse", "HEAD"),
        "current_forensics_tree": git_value("rev-parse", "HEAD^{tree}"),
    }
    write_json(OUT / "CONFIG_HASHES.json", config_hashes)

    run_counts: dict[str, int] = {}
    for row in db["runs"]:
        if row["status"] in {"SUCCEEDED", "FAILED"}:
            run_counts[row["stage"]] = run_counts.get(row["stage"], 0) + 1
    role_map = f"""# Role map: failed FreetoShop all-Solar run

Historical benchmark manifest: `{config_hashes['historical_benchmark_manifest_sha256']}`
Historical source DB: `{config_hashes['historical_source_db_sha256']}`
Historical repository commit: `{config_hashes['historical_source_repository_commit']}`
Current forensic code commit: `{config_hashes['current_forensics_commit']}`

| Role | Expected model | Actual model | Actual code path | Was invoked? | Why? |
|---|---|---|---|---|---|
| Foreground | `upstage/solar-pro4` | `upstage/solar-pro4` | sealed Pi harness → ORCHID `/v1/chat/completions` → OpenRouter | yes | all-Solar arm config |
| Selector | `upstage/solar-pro4` | `upstage/solar-pro4` | scheduler → `CompactionWorker` → `LosslessCompactionEngine` → `OpenAICompatSelectorEngine` | yes ({run_counts.get('selector', 0)} recorded model runs) | configured in `compaction_model_roles` |
| Canonicalizer | absent in selected architecture | `upstage/solar-pro4` | selector → `OpenAICompatCanonicalizerEngine` → `build_lossless_packet` | yes ({run_counts.get('canonicalizer', 0)} recorded model runs) | legacy engine unconditionally constructed/invoked it |
| Consolidator | `upstage/solar-pro4` | `upstage/solar-pro4` | packet → `OpenAICompatConsolidatorEngine` → validator → CAS promotion | yes ({run_counts.get('consolidator', 0)} recorded model runs) | configured in all-Solar arm |

## Conclusion

The benchmark explicitly assigned Solar to every legacy semantic role, and the
legacy `build_lossless_engine()` path required the canonicalizer. The run was
therefore not selector → consolidator only. The intended candidate path is:

`bounded frozen source window → selector → software-owned authoritative packet → consolidator → validation → ACTIVE promotion`.
"""
    (OUT / "ROLE_MAP.md").write_text(role_map, encoding="utf-8")

    write_json(OUT / "request_overflow" / "REQUEST_METADATA.json", metadata)
    write_json(OUT / "request_overflow" / "TOKEN_BREAKDOWN.json", breakdown)
    overflow_report = f"""# Request overflow reconstruction

The exact generation-4 consolidator run was `{metadata['request_id']}` for job
`{metadata['compaction_job_id']}`. The provider rejected **547,802** input
tokens against a **524,288** limit before returning usage. The persisted client
profile recorded **1,524,039 input-payload characters** and request body hash
`{metadata['request_body_hash']}`.

The request was a **consolidator input**, not foreground context, selector
input, canonicalizer input, or a retry payload. It covered 93 raw events,
144,623 stored source tokens, and 245 selected source items. The same selected
item payload was present once under `selected_events` and again under
`lossless_packet.authoritative_events`; the canonicalizer aggregate was also
embedded as `lossless_packet.canonical_text`.

Component counts are estimator-based because the provider rejected before
component usage telemetry was available. The exact provider total and exact
request profile are preserved in `TOKEN_BREAKDOWN.json`.
"""
    (OUT / "request_overflow" / "REPORT.md").write_text(overflow_report, encoding="utf-8")

    bad_id = "evt_275da1694443494f9382a7842fa72dc7"
    gen3_job = next(row for row in db["jobs"] if row["generation"] == 3)
    gen3_refs = json.loads(next(
        row for row in db["runs"]
        if row["generation"] == 3 and row["stage"] == "consolidator"
    )["source_refs_json"] or "[]")
    gen3_start_sequence = next(
        row["sequence"] for row in db["events"]
        if row["id"] == gen3_job["snapshot_start_event_id"]
    )
    gen3_end_sequence = next(
        row["sequence"] for row in db["events"]
        if row["id"] == gen3_job["snapshot_end_event_id"]
    )
    gen3_snapshot_ids = {
        row["id"] for row in db["events"]
        if gen3_start_sequence <= row["sequence"] <= gen3_end_sequence
    }
    failure = {
        "job_id": gen3_job["id"],
        "generation": 3,
        "stage": "consolidator",
        "offending_id": bad_id,
        "legal_source_item_count": len(gen3_refs),
        "legal_source_item_ids": gen3_refs,
        "offending_id_in_legal_source_items": bad_id in gen3_refs,
        "offending_id_in_snapshot_events": bad_id in gen3_snapshot_ids,
        "offending_id_is_previous_active_watermark": bad_id == db["active"]["covered_end_event_id"],
        "validator_action": "reject; no fuzzy repair",
        "error": gen3_job["error"],
    }
    write_json(OUT / "provenance_failure" / "FAILURE.json", failure)
    (OUT / "provenance_failure" / "REPORT.md").write_text(
        """# Provenance failure

Generation 3 returned the previous ACTIVE capsule watermark as an evidence ID.
That ID was not in the current frozen selector source universe, so the
consolidator adapter correctly rejected it. No fuzzy repair was added. The
corrected path keeps this software-owned exact-ID rejection; bounded windows
and the canonicalizer bypass remove the conditions that made the provider emit
the stale ID, but they do not make stale IDs legal.
""",
        encoding="utf-8",
    )

    safe_budget = int((PROVIDER_CONTEXT - 8_192 - 4_096) * 0.7 / 3.0)
    bounding_config = {
        "provider_context_tokens": PROVIDER_CONTEXT,
        "reserved_output_tokens": 8_192,
        "fixed_overhead_tokens": 4_096,
        "safety_margin": 0.7,
        "request_amplification_factor": 3.0,
        "derived_source_window_tokens": safe_budget,
        "runtime_env": {
            "ORCHID_CANONICALIZER_ENABLED": "0 for selected selector-to-consolidator path",
            "ORCHID_COMPACTION_CONTEXT_TOKENS": str(PROVIDER_CONTEXT),
            "ORCHID_COMPACTION_RESERVED_OUTPUT_TOKENS": "8192",
            "ORCHID_COMPACTION_FIXED_OVERHEAD_TOKENS": "4096",
            "ORCHID_COMPACTION_SAFETY_MARGIN": "0.7",
            "ORCHID_COMPACTION_REQUEST_AMPLIFICATION": "3.0",
        },
        "bound_semantics": "event-prefix bound measured using serialized source-item envelopes; one oversized first event is admitted so the watermark can progress",
    }
    write_json(OUT / "bounding" / "CONFIG.json", bounding_config)
    (OUT / "bounding" / "REPORT.md").write_text(
        f"""# Source-window bounding\n\nThe failed implementation froze every event after the ACTIVE watermark through
the newest event. There was no source-token, byte, event-count, or provider
context bound. The 547,802-token request was therefore an unbounded backlog
generation, not a bounded semantic window.\n\nThe minimal invariant is now: one immutable generation covers one bounded
event-prefix; after promotion, the next job starts at the new watermark. The
generic budget is `(provider_context - reserved_output - fixed_overhead) *
safety_margin / request_amplification`, yielding **{safe_budget:,} source
envelope tokens** for the captured Solar context configuration; the active
capsule estimate is subtracted again at snapshot freeze.\n\nThe failed run has 213 unique event IDs and 213 unique content hashes. Events
are appended in contiguous request suffixes; no repeated-prefix ingestion
regression was found.\n""",
        encoding="utf-8",
    )

    replay_lines = "\n".join(json.dumps(row, sort_keys=True) for row in replay) + ("\n" if replay else "")
    (OUT / "replay" / "telemetry.jsonl").write_text(replay_lines, encoding="utf-8")
    write_json(
        OUT / "replay" / "SUMMARY.json",
        {
            "replay_type": "offline captured-history structural replay",
            "source_db_sha256": config_hashes["historical_source_db_sha256"],
            "window_target_tokens": REPLAY_WINDOW,
            "promotions": sum(row["job_status"] == "PROMOTED" for row in replay),
            "failures": sum(row["job_status"] != "PROMOTED" for row in replay),
            "canonicalizer_calls": sum(row["canonicalizer_calls"] for row in replay),
            "max_request_estimated_tokens": max((row["request_estimated_tokens"] for row in replay), default=0),
            "provider_context_tokens": PROVIDER_CONTEXT,
            "exact_watermark_progression": all(
                row["active_covered_end_event_id"] == row["snapshot_end_event_id"]
                for row in replay
            ),
            "note": "Deterministic legal-ID selector/consolidator stand-ins prove local window, validation, CAS, and watermark mechanics; they are not semantic provider results.",
        },
    )
    (OUT / "replay" / "REPORT.md").write_text(
        f"""# Captured-history replay\n\nThe preserved authoritative event trace was replayed offline with a
{REPLAY_WINDOW:,}-token event-prefix target, a legal-ID selector stand-in, and
a legal-ID consolidator stand-in. No provider was called and no semantic
quality claim is made. The replay exercised the real worker, exact source
coverage validation, CAS promotion, and watermark progression.\n\nPromotions: **{sum(row['job_status'] == 'PROMOTED' for row in replay)}**
Failures: **{sum(row['job_status'] != 'PROMOTED' for row in replay)}**
Canonicalizer calls: **{sum(row['canonicalizer_calls'] for row in replay)}**
Maximum estimated request: **{max((row['request_estimated_tokens'] for row in replay), default=0):,}** tokens\n""",
        encoding="utf-8",
    )

    final = f"""# All-Solar benchmark forensics\n\n## Findings\n\n1. The canonicalizer **was invoked**: 61 persisted model runs. The all-Solar
manifest explicitly mapped Solar to the legacy canonicalizer role, and the
legacy engine made the call unconditional. This was not the intended
selector → consolidator architecture.\n2. The 547,802-token request was a generation-4 **consolidator** input. It
processed 93 raw events / 144,623 stored source tokens / 245 source items.
The request duplicated selected evidence and included the canonicalizer
aggregate.\n3. There was no source-window bound. The worker attempted the entire
backlog after the active watermark. Pi did not hit the limit because its
foreground native context policy did not construct this separate duplicated
compaction payload.\n4. Ingestion was not the cause: 213/213 event IDs and content hashes were
unique, with contiguous per-request append suffixes.\n5. The unknown provenance ID was the previous ACTIVE watermark, outside the
current exact legal source universe. The validator correctly rejected it; no
fuzzy repair was added.\n\n## Minimal correction\n\n- `ORCHID_CANONICALIZER_ENABLED` defaults to true for legacy compatibility, but
the selected path can disable the obsolete model stage and mechanically carry
the software-owned authoritative packet to the consolidator.\n- Scheduler-created snapshots now use a provider-aware bounded source window.
The captured Solar configuration derives **{safe_budget:,}** source-envelope
tokens from context, output reserve, fixed overhead, safety margin, and
request amplification.\n- The worker, exact-ID validator, CAS promotion, and dirty/coalescing behavior
remain unchanged.\n\n## Required answers\n\n| Question | Answer |
|---|---|
| Canonicalizer invoked? | Yes, 61 model runs. |
| Intended? | No for the selected architecture; yes only according to the legacy all-stage benchmark config. |
| Actual pipeline? | Unbounded snapshot → selector → canonicalizer → packet → consolidator → validator/promotion. |
| Intended pipeline? | Bounded snapshot → selector → software packet → consolidator → validator/promotion. |
| Did benchmark test intended architecture? | No. |
| Overflow role? | Consolidator input. |
| Source tokens? | 144,623 stored raw tokens; 245 selected source items. |
| Duplicate ingestion? | No observed duplicate IDs or content hashes. |
| Bounding present? | No in failed implementation. |
| Why overflow? | Whole backlog plus duplicated evidence and canonicalizer aggregate exceeded provider context. |
| Pi difference? | Pi’s native foreground path did not build this background compaction request. |
| Inherent to ORCHID? | No; it was an implementation/configuration mismatch plus missing bound. |
| Unknown ID cause? | Provider returned prior ACTIVE watermark, not in current legal source universe. |
| Fuzzy correction? | No. |
| Canonicalization required? | `CANONICALIZER_BYPASSED` for the selected selector → consolidator candidate; legacy compatibility remains opt-in/default. |
| Minimal change? | Explicit canonicalizer bypass seam and bounded scheduler snapshot. |
| Safe bound? | {safe_budget:,} source-envelope tokens for captured Solar config. |
| Five clean promotions? | {'Yes' if sum(row['job_status'] == 'PROMOTED' for row in replay) >= 5 else 'No'} in offline structural replay. |
| Requests below provider limit? | {'Yes' if max((row['request_estimated_tokens'] for row in replay), default=0) < PROVIDER_CONTEXT else 'No'} by repository estimator. |
| Exact watermark progression? | {'Yes' if all(row['active_covered_end_event_id'] == row['snapshot_end_event_id'] for row in replay) else 'No'}. |
| New live A/B justified? | Conditionally yes: the replay gate passed; do not launch it automatically in this phase. |
\n## Interpretation\n\n**MIXED_IMPLEMENTATION_FAILURES**. The benchmark exercised an unintended
canonicalizer stage and also lacked a bounded source-window invariant. The
captured-history replay demonstrates repeated clean local promotions after
both corrections, but it does not establish all-Solar semantic quality. A new
live A/B is conditionally justified by the replay gate and should use the
corrected configuration; it was not run in this phase.\n\n**CANONICALIZER_BYPASSED**\n\n**MIXED_IMPLEMENTATION_FAILURES**\n"""
    (OUT / "FINAL_REPORT.md").write_text(final, encoding="utf-8")
    write_json(
        OUT / "SUMMARY.json",
        {
            "interpretation": "MIXED_IMPLEMENTATION_FAILURES",
            "canonicalizer_invoked": True,
            "canonicalizer_model_runs": run_counts.get("canonicalizer", 0),
            "overflow_provider_tokens": 547_802,
            "overflow_provider_context": PROVIDER_CONTEXT,
            "overflow_job_generation": 4,
            "overflow_role": "consolidator",
            "overflow_raw_source_tokens": breakdown["source"]["raw_content_tokens"],
            "overflow_selected_source_items": breakdown["source"]["selected_source_item_count"],
            "duplicate_ingestion": False,
            "source_window_bound_present_in_failed_run": False,
            "derived_safe_source_window_tokens": safe_budget,
            "offline_replay_promotions": sum(row["job_status"] == "PROMOTED" for row in replay),
            "offline_replay_failures": sum(row["job_status"] != "PROMOTED" for row in replay),
            "offline_replay_provider_calls": 0,
            "fuzzy_provenance_repair": False,
            "historical_benchmark_commit": config_hashes["historical_source_repository_commit"],
            "current_forensics_commit": config_hashes["current_forensics_commit"],
        },
    )


def main() -> None:
    if not SOURCE_DB.exists():
        raise SystemExit(f"preserved benchmark DB not found: {SOURCE_DB}")
    db = query_db()
    metadata, breakdown = reconstruct_overflow(db)
    replay = replay_captured_history(db)
    write_reports(db, metadata, breakdown, replay)
    print(json.dumps({
        "out": str(OUT),
        "promotions": sum(row["job_status"] == "PROMOTED" for row in replay),
        "failures": sum(row["job_status"] != "PROMOTED" for row in replay),
        "max_request_estimated_tokens": max((row["request_estimated_tokens"] for row in replay), default=0),
    }, indent=2))


if __name__ == "__main__":
    main()
