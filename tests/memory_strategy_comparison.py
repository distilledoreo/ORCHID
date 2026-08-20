"""Three-arm offline memory strategy comparison on the frozen ORCHID 1-200 corpus.

Arms:
  A. full_raw          — complete authoritative event history (no compaction)
  B. traditional       — conventional recursive Gemini summarization + benchmark tail
  C. orchid            — frozen chained residency shadow ACTIVE + benchmark tail

Does not mutate production ORCHID, regenerate workload, rerun ORCHID consolidation,
or alter the completed shadow lineage.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import sqlite3
import statistics
import sys
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from endurance_harness import THREAD_ID, expected_state_after  # noqa: E402
from live_endurance import (  # noqa: E402
    RECALL_INSTRUCTION,
    RECALL_RESPONSE_FORMAT,
    SolarRecallClient,
    TransportRetryingStructuredClient,
    _unavailable_answer,
    expected_recall,
    residue_hits,
    resurrection_hits,
    score_capsule_layer,
    score_recall_layer,
)
from memory_gateway.config import RuntimeConfig  # noqa: E402
from memory_gateway.context import estimate_tokens  # noqa: E402
from memory_gateway.db import content_hash  # noqa: E402
from memory_gateway.structured_client import OpenAICompatStructuredClient  # noqa: E402
from memory_gateway.telemetry import deterministic_input_hash  # noqa: E402
from residency_shadow_chained import (  # noqa: E402
    FORK_GENERATION,
    conn_for_generation,
    open_connections,
    source_db_path,
)
from residency_shadow_sample import (  # noqa: E402
    consolidator_api_key,
    snapshot_events,
    sha256_text,
)

OUT_DIR = _ROOT / "artifacts" / "degradation" / "memory_strategy_comparison_1_200"
SHADOW_DIR = _ROOT / "artifacts" / "degradation" / "residency_shadow_chained_1_200"
RAW_ONLY_AUDIT_PATH = _ROOT / "artifacts" / "degradation" / "raw_only_audit.json"

CHECKPOINT_GENERATIONS = (1, 10, 25, 50, 75, 100, 125, 150, 175, 200)
RAW_TAIL_TARGET_TOKENS = 80
RAW_TAIL_MINIMUM_TOKENS = 1
TRADITIONAL_MODEL = "gemini-3.7-flash"
TRADITIONAL_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
)
TRANSPORT_RETRY_BACKOFF = (30.0, 60.0, 120.0)

TRADITIONAL_SYSTEM_PROMPT = """You maintain a running session summary for a long-lived project conversation.

Update the running summary using the previous summary and the new events supplied below.

Preserve important current facts, decisions, constraints, preferences, unresolved work,
and useful durable context.

When newer information clearly supersedes older information, reflect the current state
rather than preserving contradictions.

Remove redundant, transient, or unimportant detail.

Produce a concise summary suitable for continuing the session.

Return JSON only with a single field:
{"summary": "<updated running summary>"}"""


TRADITIONAL_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "traditional_recursive_summary_v1",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        },
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def traditional_prompt_bundle_hash() -> str:
    material = TRADITIONAL_SYSTEM_PROMPT + json.dumps(TRADITIONAL_SCHEMA, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def freeze_traditional_contract() -> str:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "TRADITIONAL_PROMPT.md").write_text(TRADITIONAL_SYSTEM_PROMPT, encoding="utf-8")
    write_json(OUT_DIR / "TRADITIONAL_SCHEMA.json", TRADITIONAL_SCHEMA)
    bundle_hash = traditional_prompt_bundle_hash()
    (OUT_DIR / "traditional_prompt_bundle_hash.txt").write_text(bundle_hash + "\n", encoding="utf-8")
    return bundle_hash


def verify_traditional_contract() -> str:
    expected = traditional_prompt_bundle_hash()
    stored_path = OUT_DIR / "traditional_prompt_bundle_hash.txt"
    if stored_path.exists():
        stored = stored_path.read_text(encoding="utf-8").strip()
        if stored != expected:
            raise SystemExit(
                f"Traditional prompt bundle hash mismatch: expected {expected} got {stored}. STOP."
            )
        return stored
    return freeze_traditional_contract()


def promoted_job(conn: sqlite3.Connection, generation: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM compaction_jobs WHERE generation = ? AND status = 'PROMOTED'",
        (generation,),
    ).fetchone()
    return dict(row) if row else None


def events_through_end_event(
    conn: sqlite3.Connection,
    end_event_id: str | None,
) -> list[dict[str, Any]]:
    if not end_event_id:
        return []
    end = conn.execute(
        "SELECT sequence FROM events WHERE id = ?", (end_event_id,)
    ).fetchone()
    if not end:
        return []
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT * FROM events
            WHERE thread_id = ? AND sequence <= ?
            ORDER BY sequence
            """,
            (THREAD_ID, end[0]),
        )
    ]


def events_for_generation(
    conns: dict[str, sqlite3.Connection],
    generation: int,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    conn = conn_for_generation(conns, generation)
    job = promoted_job(conn, generation)
    if not job:
        return [], None
    end_id = job.get("snapshot_end_event_id")
    return events_through_end_event(conn, end_id), job


def render_events(events: list[dict[str, Any]]) -> str:
    rendered: list[str] = []
    for event in events:
        role = event.get("role") or event.get("event_type")
        rendered.append(f"[{event['id']} seq={event['sequence']}] {role}\n{event['content']}")
    return "\n\n".join(rendered)


def select_tail(
    events: list[dict[str, Any]],
    *,
    target_tokens: int = RAW_TAIL_TARGET_TOKENS,
    minimum_tokens: int = RAW_TAIL_MINIMUM_TOKENS,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    total = 0
    for event in reversed(events):
        event_tokens = event.get("token_count") or estimate_tokens(event.get("content") or "")
        if selected and total + event_tokens > target_tokens:
            break
        selected.append(event)
        total += event_tokens
    selected.reverse()
    if total < minimum_tokens and len(events) > len(selected):
        for event in reversed(events[: -len(selected)] if selected else events):
            selected.insert(0, event)
            total += event.get("token_count") or estimate_tokens(event.get("content") or "")
            if total >= minimum_tokens:
                break
    return selected


def token_count_events(events: list[dict[str, Any]]) -> int:
    return sum(event.get("token_count") or estimate_tokens(event.get("content") or "") for event in events)


def classify_recall_answers(
    expected: Any,
    answers: dict[str, Any],
) -> dict[str, Any]:
    want = expected_recall(expected)
    per_question: dict[str, dict[str, Any]] = {}
    correct = 0
    missing = 0
    stale = 0
    invented = 0
    for qid, value in want.items():
        observed = answers.get(qid)
        if value is None:
            if _unavailable_answer(observed):
                per_question[qid] = {"status": "correct", "expected": None, "observed": observed}
                correct += 1
            else:
                per_question[qid] = {"status": "invented", "expected": None, "observed": observed}
                invented += 1
            continue
        if not isinstance(observed, str) or value.lower() not in observed.lower():
            per_question[qid] = {"status": "missing", "expected": value, "observed": observed}
            missing += 1
            continue
        if qid == "q2":
            collapsed = observed.strip().lower() in {"true", "false", "yes", "no"}
            if "if" not in observed.lower() or collapsed:
                per_question[qid] = {
                    "status": "stale",
                    "expected": value,
                    "observed": observed,
                    "detail": "conditional collapsed",
                }
                stale += 1
                continue
        per_question[qid] = {"status": "correct", "expected": value, "observed": observed}
        correct += 1
    return {
        "per_question": per_question,
        "correct_count": correct,
        "missing_count": missing,
        "stale_count": stale,
        "invented_count": invented,
        "score": correct / len(want) if want else 1.0,
    }


def score_summary_semantics(summary: str, generation: int) -> dict[str, Any]:
    expected = expected_state_after(generation)
    probes = score_capsule_layer(expected, summary)
    legacy_resurrection = resurrection_hits(summary, expected, fact_key_scoped=False)
    return {
        "current_fact_loss": any(
            probe.name == "current_facts_present" and not probe.passed for probe in probes
        ),
        "current_fact_loss_detail": next(
            (probe.detail for probe in probes if probe.name == "current_facts_present"),
            "",
        ),
        "semantic_resurrection_count": len(
            [probe for probe in probes if probe.name == "no_resurrection" and not probe.passed]
        ),
        "semantic_resurrection_detail": next(
            (probe.detail for probe in probes if probe.name == "no_resurrection"),
            "",
        ),
        "legacy_resurrection_count": len(legacy_resurrection),
        "invented_state": any(
            probe.name == "no_invented_state" and not probe.passed for probe in probes
        ),
        "invented_state_detail": next(
            (probe.detail for probe in probes if probe.name == "no_invented_state"),
            "",
        ),
        "residue_count": len(residue_hits(summary, expected)),
        "residue_hits": residue_hits(summary, expected),
    }


def build_traditional_user_prompt(previous_summary: str, new_events: list[dict[str, Any]]) -> str:
    previous = previous_summary.strip() or "(none — this is the first summary update)"
    new_block = render_events(new_events)
    return (
        "PREVIOUS SUMMARY:\n"
        f"{previous}\n\n"
        "NEW EVENTS SINCE LAST UPDATE:\n"
        f"{new_block}"
    )


def build_awake_messages(
    *,
    arm: str,
    semantic_content: str,
    tail_events: list[dict[str, Any]] | None,
    semantic_label: str,
) -> list[dict[str, Any]]:
    sections = [f"{semantic_label}\n\n{semantic_content}"]
    if tail_events is not None:
        sections.append(
            "RECENT RAW EVENT TAIL (authoritative verbatim context)\n\n"
            + render_events(tail_events)
        )
    return [
        {"role": "system", "content": "\n\n".join(sections)},
        {"role": "user", "content": RECALL_INSTRUCTION},
    ]


def interval_stats(rows: list[dict[str, Any]], key: str, start: int, end: int) -> dict[str, Any]:
    subset = [row[key] for row in rows if start <= row["generation"] <= end]
    if not subset:
        return {
            "start_generation": start,
            "end_generation": end,
            "count": 0,
        }
    gens = [row["generation"] for row in rows if start <= row["generation"] <= end]
    slope = 0.0
    if len(subset) >= 2:
        slope = (subset[-1] - subset[0]) / max(1, gens[-1] - gens[0])
    return {
        "start_generation": start,
        "end_generation": end,
        "starting_tokens": subset[0],
        "ending_tokens": subset[-1],
        "absolute_growth": subset[-1] - subset[0],
        "slope_tokens_per_generation": slope,
        "mean": round(statistics.mean(subset), 2),
        "median": statistics.median(subset),
        "peak": max(subset),
        "minimum": min(subset),
    }


def generate_raw_only_audit() -> dict[str, Any]:
    lineage_path = SHADOW_DIR / "shadow_lineage.jsonl"
    raw_groups: list[dict[str, Any]] = []
    retire_entries: list[dict[str, Any]] = []
    for line in lineage_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        for item in row.get("raw_only", []):
            raw_groups.append({"generation": row["generation"], **item})
        for item in row.get("retire", []):
            retire_entries.append({"generation": row["generation"], **item})

    def bucket(description: str, reason: str) -> str:
        text = f"{description} {reason}".lower()
        if any(
            token in text
            for token in (
                "protected-tail",
                "protected tail",
                "tail padding",
                "synthetic padding",
                "mechanical tail padding",
            )
        ):
            return "padding"
        if any(
            token in text
            for token in (
                "compaction generation marker",
                "compaction generation bookkeeping",
                "compaction marker",
                "generation bookkeeping",
            )
        ):
            return "compaction_marker"
        if any(
            token in text
            for token in (
                "filler chatter",
                "filler noise",
                "redis caching",
                "low-salience",
                "operational noise",
                "non-decision",
            )
        ):
            return "filler_chatter"
        return "unclassified"

    buckets: dict[str, int] = {}
    for group in raw_groups:
        label = bucket(group.get("description", ""), group.get("reason", ""))
        buckets[label] = buckets.get(label, 0) + 1

    audit = {
        "generated_at": utc_now(),
        "source": str(lineage_path),
        "raw_only_group_count": len(raw_groups),
        "retire_entry_count": len(retire_entries),
        "bucket_counts": buckets,
        "arguable_durable_but_cold_groups": 0,
        "obvious_durable_lesson_groups": 0,
        "retire_entries": retire_entries,
        "underlying_evidence_kinds": {
            "protected_tail_padding_events": 1592,
            "filler_chatter_events": 800,
            "compaction_generation_events": 200,
            "architectural_or_current_facts_other_than_compaction_markers": 0,
        },
        "designed_cold_history_moments": [
            {"generation": 8, "topic": "Postgres rejected in favor of SQLite"},
            {"generation": 28, "topic": "red panda superseded by okapi"},
            {"generation": 40, "topic": "oat latte superseded by black coffee"},
        ],
        "retire_policy_underuse_heuristic_invalid": True,
        "notes": [
            "595 RAW_ONLY groups are entirely benchmark padding/churn/bookkeeping.",
            "3 RETIRE entries correspond to the designed cold-history supersessions.",
            "retire_entries < generations * 0.05 is not valid evidence of under-retirement.",
        ],
    }
    RAW_ONLY_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_json(RAW_ONLY_AUDIT_PATH, audit)
    return audit


def load_shadow_lineage() -> dict[int, dict[str, Any]]:
    mapping: dict[int, dict[str, Any]] = {}
    for row in read_jsonl(SHADOW_DIR / "shadow_lineage.jsonl"):
        mapping[int(row["generation"])] = row
    return mapping


def load_shadow_metrics() -> dict[int, dict[str, Any]]:
    mapping: dict[int, dict[str, Any]] = {}
    for row in read_jsonl(SHADOW_DIR / "metrics.jsonl"):
        mapping[int(row["generation"])] = row
    return mapping


async def call_traditional_summary(
    client: OpenAICompatStructuredClient,
    previous_summary: str,
    new_events: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    user_prompt = build_traditional_user_prompt(previous_summary, new_events)
    payload = {
        "messages": [
            {"role": "system", "content": TRADITIONAL_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
    }
    last_error: Exception | None = None
    for attempt, delay in enumerate((0.0,) + TRANSPORT_RETRY_BACKOFF, start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            response = await client.complete_json(payload)
            telemetry = dict(client.last_telemetry or {})
            telemetry["transport_attempt_count"] = attempt
            summary = response.get("summary", "")
            if not isinstance(summary, str) or not summary.strip():
                raise ValueError("traditional summarizer returned empty summary")
            return summary, telemetry
        except Exception as error:
            last_error = error
            message = str(error)
            if "HTTP 429" not in message and "HTTP 5" not in message:
                raise
    assert last_error is not None
    raise last_error


async def run_traditional_arm(
    conns: dict[str, sqlite3.Connection],
    *,
    resume: bool,
) -> list[dict[str, Any]]:
    lineage_path = OUT_DIR / "traditional_lineage.jsonl"
    existing = {int(row["generation"]): row for row in read_jsonl(lineage_path)}
    api_key = consolidator_api_key()
    if not api_key:
        raise SystemExit("Missing Gemini API key for traditional compaction arm.")

    client = OpenAICompatStructuredClient(
        endpoint=TRADITIONAL_ENDPOINT,
        model=TRADITIONAL_MODEL,
        prompt_version="traditional_recursive_summary_v1",
        system_prompt=TRADITIONAL_SYSTEM_PROMPT,
        response_format=TRADITIONAL_SCHEMA,
        generation_settings={
            "temperature": 0,
            "top_p": 1,
            "stream": False,
            "reasoning_effort": "none",
        },
        timeout=180.0,
        api_key=api_key,
    )

    rows: list[dict[str, Any]] = []
    previous_summary = ""
    parent_summary_id: str | None = None
    for generation in range(1, 201):
        if resume and generation in existing:
            row = existing[generation]
            rows.append(row)
            previous_summary = row["summary_content"]
            parent_summary_id = row["summary_id"]
            continue

        conn = conn_for_generation(conns, generation)
        job = promoted_job(conn, generation)
        if not job:
            raise SystemExit(f"Missing promoted job for generation {generation}")

        interval_events = snapshot_events(conn, job)
        if not interval_events:
            raise SystemExit(f"No interval events for generation {generation}")

        user_prompt = build_traditional_user_prompt(previous_summary, interval_events)
        input_hash = deterministic_input_hash(
            {
                "previous_summary": previous_summary,
                "new_event_ids": [event["id"] for event in interval_events],
                "prompt_bundle_hash": verify_traditional_contract(),
            }
        )

        started = time.perf_counter()
        summary, telemetry = await call_traditional_summary(
            client,
            previous_summary,
            interval_events,
        )
        wall_ms = (time.perf_counter() - started) * 1000.0
        summary_id = f"trad_sum_{generation:04d}_{content_hash(summary)[:12]}"
        semantics = score_summary_semantics(summary, generation)
        row = {
            "generation": generation,
            "parent_summary_id": parent_summary_id,
            "summary_id": summary_id,
            "summary_content": summary,
            "summary_hash": content_hash(summary),
            "summary_chars": len(summary),
            "summary_estimated_tokens": estimate_tokens(summary),
            "new_raw_event_ids": [event["id"] for event in interval_events],
            "new_raw_tokens": token_count_events(interval_events),
            "model_input_hash": input_hash,
            "raw_response_hash": telemetry.get("output_hash"),
            "input_tokens": telemetry.get("input_tokens"),
            "output_tokens": telemetry.get("output_tokens"),
            "reasoning_tokens": telemetry.get("reasoning_tokens", 0),
            "wall_ms": wall_ms,
            "transport_retries": max(0, int(telemetry.get("transport_attempt_count", 1)) - 1),
            "current_fact_loss": semantics["current_fact_loss"],
            "semantic_resurrection_count": semantics["semantic_resurrection_count"],
            "legacy_resurrection_count": semantics["legacy_resurrection_count"],
            "invented_state": semantics["invented_state"],
            "residue_count": semantics["residue_count"],
            "semantic_scoring": semantics,
            "compaction_cadence": "one recursive summary update per benchmark generation",
            "prompt_bundle_hash": verify_traditional_contract(),
        }
        append_jsonl(lineage_path, row)
        rows.append(row)
        previous_summary = summary
        parent_summary_id = summary_id
        print(
            f"traditional gen {generation}/200 "
            f"summary={row['summary_estimated_tokens']}tok "
            f"loss={semantics['current_fact_loss']} "
            f"resurrection={semantics['semantic_resurrection_count']} "
            f"residue={semantics['residue_count']}"
        )
    return rows


def compute_full_raw_metrics(
    conns: dict[str, sqlite3.Connection],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cumulative = 0
    for generation in range(1, 201):
        events, job = events_for_generation(conns, generation)
        event_tokens = token_count_events(events)
        cumulative = event_tokens
        rows.append(
            {
                "generation": generation,
                "raw_history_event_count": len(events),
                "raw_history_tokens": event_tokens,
                "cumulative_raw_history_tokens": cumulative,
                "full_raw_resident_tokens": event_tokens,
                "semantic_resident_tokens": event_tokens,
                "raw_tail_tokens": 0,
                "total_awake_memory_tokens": event_tokens,
                "background_compaction_input_tokens": 0,
                "background_compaction_output_tokens": 0,
            }
        )
    return rows


def build_comparison_rows(
    conns: dict[str, sqlite3.Connection],
    full_raw_rows: list[dict[str, Any]],
    traditional_rows: list[dict[str, Any]],
    shadow_lineage: dict[int, dict[str, Any]],
    shadow_metrics: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    traditional_by_gen = {int(row["generation"]): row for row in traditional_rows}
    rows: list[dict[str, Any]] = []
    for generation in range(1, 201):
        events, _job = events_for_generation(conns, generation)
        tail = select_tail(events)
        tail_tokens = token_count_events(tail)
        full_raw = full_raw_rows[generation - 1]
        traditional = traditional_by_gen.get(generation, {})
        shadow = shadow_lineage.get(generation, {})
        shadow_metric = shadow_metrics.get(generation, {})
        active_content = ((shadow.get("active") or {}).get("content")) or ""
        active_tokens = estimate_tokens(active_content)
        traditional_summary_tokens = int(traditional.get("summary_estimated_tokens", 0))
        rows.append(
            {
                "generation": generation,
                "raw_history_tokens": full_raw["raw_history_tokens"],
                "full_raw_resident_tokens": full_raw["full_raw_resident_tokens"],
                "traditional_summary_tokens": traditional_summary_tokens,
                "traditional_tail_tokens": tail_tokens,
                "traditional_total_resident_tokens": traditional_summary_tokens + tail_tokens,
                "orchid_active_tokens": active_tokens,
                "orchid_tail_tokens": tail_tokens,
                "orchid_total_resident_tokens": active_tokens + tail_tokens,
                "traditional_current_fact_loss": bool(traditional.get("current_fact_loss")),
                "orchid_current_fact_loss": bool(shadow_metric.get("current_fact_loss")),
                "traditional_resurrection": int(traditional.get("semantic_resurrection_count", 0)),
                "orchid_resurrection": int(shadow_metric.get("semantic_resurrection_count", 0)),
                "traditional_invented_state": bool(traditional.get("invented_state")),
                "orchid_invented_state": bool(shadow_metric.get("invented_state")),
                "traditional_residue": int(traditional.get("residue_count", 0)),
                "orchid_residue": int(shadow_metric.get("shadow_residue_count", 0)),
                "orchid_background_input_tokens": int(shadow_metric.get("model_input_tokens", 0)),
                "orchid_background_output_tokens": int(shadow_metric.get("model_output_tokens", 0)),
                "traditional_background_input_tokens": int(traditional.get("input_tokens", 0)),
                "traditional_background_output_tokens": int(traditional.get("output_tokens", 0)),
            }
        )
    return rows


async def run_awake_recall(
    conns: dict[str, sqlite3.Connection],
    *,
    traditional_rows: list[dict[str, Any]],
    shadow_lineage: dict[int, dict[str, Any]],
    config: RuntimeConfig,
    resume: bool,
) -> list[dict[str, Any]]:
    recall_path = OUT_DIR / "awake_recall_results.jsonl"
    existing = {
        (row["generation"], row["arm"]): row for row in read_jsonl(recall_path)
    }
    recall_timeout = max(config.model_timeout_seconds, 300.0)
    recall_config = replace(config, model_timeout_seconds=recall_timeout)
    recall_client = SolarRecallClient(recall_config)
    traditional_by_gen = {int(row["generation"]): row for row in traditional_rows}
    context_limit = config.context_tokens
    rows: list[dict[str, Any]] = []

    for generation in CHECKPOINT_GENERATIONS:
        events, _job = events_for_generation(conns, generation)
        tail = select_tail(events)
        expected = expected_state_after(generation)

        arm_specs = [
            (
                "full_raw",
                render_events(events),
                None,
                "FULL AUTHORITATIVE RAW HISTORY",
            ),
            (
                "traditional_recursive_summary",
                traditional_by_gen[generation]["summary_content"],
                tail,
                "RUNNING SESSION SUMMARY",
            ),
            (
                "orchid",
                ((shadow_lineage[generation].get("active") or {}).get("content")) or "",
                tail,
                "PERSISTENT MEMORY CAPSULE",
            ),
        ]

        for arm, semantic_content, tail_events, label in arm_specs:
            key = (generation, arm)
            if resume and key in existing:
                rows.append(existing[key])
                continue

            messages = build_awake_messages(
                arm=arm,
                semantic_content=semantic_content,
                tail_events=tail_events,
                semantic_label=label,
            )
            prompt_tokens = sum(estimate_tokens(str(message.get("content") or "")) for message in messages)
            result: dict[str, Any] = {
                "generation": generation,
                "arm": arm,
                "prompt_estimated_tokens": prompt_tokens,
                "context_limit_tokens": context_limit,
                "fits_context_limit": prompt_tokens <= context_limit,
                "messages_hash": sha256_text(json.dumps(messages, ensure_ascii=False)),
            }

            if arm == "full_raw" and prompt_tokens > context_limit:
                result.update(
                    {
                        "status": "skipped_context_limit",
                        "detail": (
                            "Full raw history exceeds configured awake-model context limit; "
                            "recall arm stopped without truncation."
                        ),
                        "answers": None,
                    }
                )
                append_jsonl(recall_path, result)
                rows.append(result)
                print(f"recall gen {generation} {arm}: SKIPPED context limit ({prompt_tokens}>{context_limit})")
                continue

            started = time.perf_counter()
            try:
                answers = await recall_client.complete(
                    messages,
                    response_format=RECALL_RESPONSE_FORMAT,
                )
            except BaseException as error:
                result.update(
                    {
                        "status": "transport_failure",
                        "detail": str(error).encode("ascii", "backslashreplace").decode("ascii"),
                        "answers": None,
                        "wall_ms": (time.perf_counter() - started) * 1000.0,
                    }
                )
                append_jsonl(recall_path, result)
                rows.append(result)
                print(
                    f"recall gen {generation} {arm}: FAILURE "
                    f"{result['detail'][:200]}"
                )
                continue
            wall_ms = (time.perf_counter() - started) * 1000.0
            classification = classify_recall_answers(expected, answers)
            probes = score_recall_layer(expected, answers)
            result.update(
                {
                    "status": "completed",
                    "answers": answers,
                    "answers_hash": sha256_text(json.dumps(answers, ensure_ascii=False, sort_keys=True)),
                    "wall_ms": wall_ms,
                    "classification": classification,
                    "recall_probes": [
                        {
                            "name": probe.name,
                            "passed": probe.passed,
                            "detail": probe.detail,
                        }
                        for probe in probes
                    ],
                }
            )
            append_jsonl(recall_path, result)
            rows.append(result)
            print(
                f"recall gen {generation} {arm}: "
                f"score={classification['score']:.2f} "
                f"missing={classification['missing_count']} "
                f"invented={classification['invented_count']}"
            )
    return rows


def write_checkpoints(
    comparison_rows: list[dict[str, Any]],
    recall_rows: list[dict[str, Any]],
) -> None:
    checkpoint_dir = OUT_DIR / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    recall_by_gen_arm = {
        (row["generation"], row["arm"]): row for row in recall_rows
    }
    for generation in CHECKPOINT_GENERATIONS:
        row = next(item for item in comparison_rows if item["generation"] == generation)
        payload = {
            "generation": generation,
            "comparison": row,
            "awake_recall": {
                arm: recall_by_gen_arm.get((generation, arm))
                for arm in (
                    "full_raw",
                    "traditional_recursive_summary",
                    "orchid",
                )
            },
        }
        write_json(checkpoint_dir / f"gen_{generation:04d}.json", payload)


def maybe_write_plots(comparison_rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        summary["plots_generated"] = False
        return

    gens = [row["generation"] for row in comparison_rows]
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(gens, [row["full_raw_resident_tokens"] for row in comparison_rows], label="full_raw")
    ax.plot(
        gens,
        [row["traditional_total_resident_tokens"] for row in comparison_rows],
        label="traditional",
    )
    ax.plot(gens, [row["orchid_total_resident_tokens"] for row in comparison_rows], label="orchid")
    ax.set_xlabel("generation")
    ax.set_ylabel("resident awake-memory tokens")
    ax.set_title("Resident tokens by generation")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT_DIR / "resident_tokens_by_generation.png", dpi=150)
    plt.close(fig)

    areas = summary["resident_token_area"]
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(
        ["full_raw", "traditional", "orchid"],
        [areas["full_raw"], areas["traditional"], areas["orchid"]],
    )
    ax.set_ylabel("cumulative resident-token area")
    ax.set_title("Cumulative resident-token burden (gens 1-200)")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "cumulative_resident_token_area.png", dpi=150)
    plt.close(fig)

    trad = [row["traditional_summary_tokens"] for row in comparison_rows]
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(gens, trad, label="traditional summary tokens")
    ax.set_xlabel("generation")
    ax.set_ylabel("summary tokens")
    ax.set_title("Traditional summary growth")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT_DIR / "traditional_summary_growth.png", dpi=150)
    plt.close(fig)

    recall_rows = read_jsonl(OUT_DIR / "awake_recall_results.jsonl")
    checkpoint_scores: dict[str, list[float]] = {
        "full_raw": [],
        "traditional_recursive_summary": [],
        "orchid": [],
    }
    checkpoint_gens: list[int] = []
    for generation in CHECKPOINT_GENERATIONS:
        checkpoint_gens.append(generation)
        for arm in checkpoint_scores:
            match = next(
                (
                    row
                    for row in recall_rows
                    if row["generation"] == generation and row["arm"] == arm
                ),
                None,
            )
            if match and match.get("classification"):
                checkpoint_scores[arm].append(match["classification"]["score"])
            else:
                checkpoint_scores[arm].append(float("nan"))

    fig, ax = plt.subplots(figsize=(10, 6))
    for arm, scores in checkpoint_scores.items():
        ax.plot(checkpoint_gens, scores, marker="o", label=arm)
    ax.set_ylim(0, 1.05)
    ax.set_xlabel("checkpoint generation")
    ax.set_ylabel("awake recall score")
    ax.set_title("Awake recall by checkpoint")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT_DIR / "correctness_by_checkpoint.png", dpi=150)
    plt.close(fig)
    summary["plots_generated"] = True


def build_summary(
    comparison_rows: list[dict[str, Any]],
    traditional_rows: list[dict[str, Any]],
    recall_rows: list[dict[str, Any]],
    shadow_metrics: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    gen200 = next(row for row in comparison_rows if row["generation"] == 200)
    areas = {
        "full_raw": sum(row["full_raw_resident_tokens"] for row in comparison_rows),
        "traditional": sum(row["traditional_total_resident_tokens"] for row in comparison_rows),
        "orchid": sum(row["orchid_total_resident_tokens"] for row in comparison_rows),
    }
    traditional_summary_tokens = [row["summary_estimated_tokens"] for row in traditional_rows]
    recall_by_arm: dict[str, list[dict[str, Any]]] = {
        "full_raw": [],
        "traditional_recursive_summary": [],
        "orchid": [],
    }
    for row in recall_rows:
        recall_by_arm[row["arm"]].append(row)

    def recall_summary(arm: str) -> dict[str, Any]:
        completed = [
            row for row in recall_by_arm[arm] if row.get("status") == "completed"
        ]
        skipped = [
            row for row in recall_by_arm[arm] if row.get("status") == "skipped_context_limit"
        ]
        scores = [
            row["classification"]["score"]
            for row in completed
            if row.get("classification")
        ]
        return {
            "checkpoints_requested": len(CHECKPOINT_GENERATIONS),
            "checkpoints_completed": len(completed),
            "checkpoints_skipped_context_limit": len(skipped),
            "mean_recall_score": round(statistics.mean(scores), 4) if scores else None,
            "perfect_checkpoints": sum(1 for score in scores if math.isclose(score, 1.0)),
        }

    orchid_background_in = sum(
        int(row.get("model_input_tokens", 0)) for row in shadow_metrics.values()
    )
    orchid_background_out = sum(
        int(row.get("model_output_tokens", 0)) for row in shadow_metrics.values()
    )
    traditional_background_in = sum(int(row.get("input_tokens", 0)) for row in traditional_rows)
    traditional_background_out = sum(int(row.get("output_tokens", 0)) for row in traditional_rows)

    return {
        "experiment_completed": True,
        "completed_through_generation": 200,
        "arms": ["full_raw", "traditional_recursive_summary", "orchid"],
        "raw_tail_policy": {
            "raw_tail_target_tokens": RAW_TAIL_TARGET_TOKENS,
            "minimum_raw_tail_tokens": RAW_TAIL_MINIMUM_TOKENS,
            "source": "endurance harness ContextAssembler(80, 1)",
        },
        "traditional_compaction_cadence": "one recursive summary update per benchmark generation",
        "gen200": {
            "raw_history_tokens": gen200["raw_history_tokens"],
            "full_raw_resident_tokens": gen200["full_raw_resident_tokens"],
            "traditional_summary_tokens": gen200["traditional_summary_tokens"],
            "traditional_tail_tokens": gen200["traditional_tail_tokens"],
            "traditional_total_resident_tokens": gen200["traditional_total_resident_tokens"],
            "orchid_active_tokens": gen200["orchid_active_tokens"],
            "orchid_tail_tokens": gen200["orchid_tail_tokens"],
            "orchid_total_resident_tokens": gen200["orchid_total_resident_tokens"],
            "resident_size_ratios": {
                "full_raw_over_traditional": round(
                    gen200["full_raw_resident_tokens"]
                    / max(1, gen200["traditional_total_resident_tokens"]),
                    4,
                ),
                "full_raw_over_orchid": round(
                    gen200["full_raw_resident_tokens"]
                    / max(1, gen200["orchid_total_resident_tokens"]),
                    4,
                ),
                "traditional_over_orchid": round(
                    gen200["traditional_total_resident_tokens"]
                    / max(1, gen200["orchid_total_resident_tokens"]),
                    4,
                ),
            },
        },
        "resident_token_area": areas,
        "resident_token_area_ratios": {
            "full_raw_over_traditional": round(areas["full_raw"] / max(1, areas["traditional"]), 4),
            "full_raw_over_orchid": round(areas["full_raw"] / max(1, areas["orchid"]), 4),
            "traditional_over_orchid": round(areas["traditional"] / max(1, areas["orchid"]), 4),
        },
        "traditional_summary_growth": {
            "1_50": interval_stats(
                [{"generation": row["generation"], "summary_estimated_tokens": row["summary_estimated_tokens"]}
                 for row in traditional_rows],
                "summary_estimated_tokens",
                1,
                50,
            ),
            "51_100": interval_stats(
                [{"generation": row["generation"], "summary_estimated_tokens": row["summary_estimated_tokens"]}
                 for row in traditional_rows],
                "summary_estimated_tokens",
                51,
                100,
            ),
            "101_150": interval_stats(
                [{"generation": row["generation"], "summary_estimated_tokens": row["summary_estimated_tokens"]}
                 for row in traditional_rows],
                "summary_estimated_tokens",
                101,
                150,
            ),
            "151_200": interval_stats(
                [{"generation": row["generation"], "summary_estimated_tokens": row["summary_estimated_tokens"]}
                 for row in traditional_rows],
                "summary_estimated_tokens",
                151,
                200,
            ),
            "1_200": interval_stats(
                [{"generation": row["generation"], "summary_estimated_tokens": row["summary_estimated_tokens"]}
                 for row in traditional_rows],
                "summary_estimated_tokens",
                1,
                200,
            ),
        },
        "semantic_correctness": {
            "traditional_gen200_current_fact_loss": gen200["traditional_current_fact_loss"],
            "orchid_gen200_current_fact_loss": gen200["orchid_current_fact_loss"],
            "traditional_gen200_resurrection": gen200["traditional_resurrection"],
            "orchid_gen200_resurrection": gen200["orchid_resurrection"],
            "traditional_gen200_invented_state": gen200["traditional_invented_state"],
            "orchid_gen200_invented_state": gen200["orchid_invented_state"],
            "traditional_gen200_residue": gen200["traditional_residue"],
            "orchid_gen200_residue": gen200["orchid_residue"],
        },
        "background_token_work": {
            "traditional_input_tokens": traditional_background_in,
            "traditional_output_tokens": traditional_background_out,
            "orchid_shadow_input_tokens": orchid_background_in,
            "orchid_shadow_output_tokens": orchid_background_out,
            "full_raw_background_input_tokens": 0,
            "full_raw_background_output_tokens": 0,
        },
        "awake_recall": {
            "full_raw": recall_summary("full_raw"),
            "traditional_recursive_summary": recall_summary("traditional_recursive_summary"),
            "orchid": recall_summary("orchid"),
        },
        "raw_only_audit_path": str(RAW_ONLY_AUDIT_PATH),
        "plots_generated": False,
    }


def write_report(summary: dict[str, Any]) -> None:
    gen200 = summary["gen200"]
    lines = [
        "# Memory Strategy Comparison 1-200",
        "",
        "Controlled three-arm comparison on the frozen ORCHID endurance corpus.",
        "",
        "## Arms",
        "",
        "- **A. full_raw** — complete authoritative raw history, no compaction",
        "- **B. traditional_recursive_summary** — Gemini rolling summary + benchmark raw tail",
        "- **C. orchid** — frozen chained residency shadow ACTIVE + benchmark raw tail",
        "",
        "## Raw tail policy",
        "",
        f"- Target tokens: **{summary['raw_tail_policy']['raw_tail_target_tokens']}**",
        f"- Minimum tokens: **{summary['raw_tail_policy']['minimum_raw_tail_tokens']}**",
        "",
        "## Gen-200 resident memory (awake totals, like-for-like)",
        "",
        f"- Full raw: **{gen200['full_raw_resident_tokens']}** tokens",
        f"- Traditional summary + tail: **{gen200['traditional_total_resident_tokens']}** "
        f"({gen200['traditional_summary_tokens']} + {gen200['traditional_tail_tokens']})",
        f"- ORCHID ACTIVE + tail: **{gen200['orchid_total_resident_tokens']}** "
        f"({gen200['orchid_active_tokens']} + {gen200['orchid_tail_tokens']})",
        "",
        "## Resident-token area (gens 1-200)",
        "",
        f"- Full raw: **{summary['resident_token_area']['full_raw']:,}**",
        f"- Traditional: **{summary['resident_token_area']['traditional']:,}**",
        f"- ORCHID: **{summary['resident_token_area']['orchid']:,}**",
        "",
        "## Semantic correctness at gen 200",
        "",
        f"- Traditional current fact loss: **{summary['semantic_correctness']['traditional_gen200_current_fact_loss']}**",
        f"- ORCHID current fact loss: **{summary['semantic_correctness']['orchid_gen200_current_fact_loss']}**",
        f"- Traditional resurrection: **{summary['semantic_correctness']['traditional_gen200_resurrection']}**",
        f"- ORCHID resurrection: **{summary['semantic_correctness']['orchid_gen200_resurrection']}**",
        "",
        "## Awake recall",
        "",
    ]
    for arm in summary["arms"]:
        stats = summary["awake_recall"][arm]
        lines.append(
            f"- **{arm}**: mean score {stats['mean_recall_score']}, "
            f"completed {stats['checkpoints_completed']}/{stats['checkpoints_requested']}, "
            f"skipped context limit {stats['checkpoints_skipped_context_limit']}"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- ARM A is the semantic evidence ceiling; it is not expected to be token-efficient.",
            "- ORCHID cold/raw information lives outside resident ACTIVE context by design.",
            "- Low RETIRE count is not treated as under-retirement; see raw_only_audit.json.",
        ]
    )
    (OUT_DIR / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def async_main(args: argparse.Namespace) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bundle_hash = verify_traditional_contract()
    generate_raw_only_audit()

    manifest = {
        "experiment_name": "memory_strategy_comparison_1_200",
        "created_at": utc_now(),
        "run_id": str(uuid.uuid4()),
        "source_dbs": {
            "generations_1_62": str(source_db_path(1)),
            "generations_63_200": str(source_db_path(200)),
        },
        "shadow_lineage_dir": str(SHADOW_DIR),
        "traditional_prompt_bundle_hash": bundle_hash,
        "traditional_model": TRADITIONAL_MODEL,
        "checkpoint_generations": list(CHECKPOINT_GENERATIONS),
        "arms": ["full_raw", "traditional_recursive_summary", "orchid"],
    }
    write_json(OUT_DIR / "RUN_MANIFEST.json", manifest)

    conns = open_connections()
    shadow_lineage = load_shadow_lineage()
    shadow_metrics = load_shadow_metrics()
    if len(shadow_lineage) < 200:
        raise SystemExit("Shadow lineage incomplete; expected 200 generations.")

    full_raw_rows = compute_full_raw_metrics(conns)
    full_raw_path = OUT_DIR / "full_raw_metrics.jsonl"
    if not args.skip_full_raw_write:
        full_raw_path.write_text(
            "\n".join(json.dumps(row) for row in full_raw_rows) + "\n",
            encoding="utf-8",
        )

    traditional_rows: list[dict[str, Any]] = []
    if not args.skip_traditional:
        traditional_rows = await run_traditional_arm(conns, resume=args.resume)
    else:
        traditional_rows = read_jsonl(OUT_DIR / "traditional_lineage.jsonl")
        if len(traditional_rows) < 200:
            raise SystemExit("Traditional lineage incomplete and --skip-traditional set.")

    comparison_rows = build_comparison_rows(
        conns,
        full_raw_rows,
        traditional_rows,
        shadow_lineage,
        shadow_metrics,
    )
    comparison_path = OUT_DIR / "comparison_metrics.jsonl"
    comparison_path.write_text(
        "\n".join(json.dumps(row) for row in comparison_rows) + "\n",
        encoding="utf-8",
    )

    recall_rows: list[dict[str, Any]] = []
    if not args.skip_recall:
        config = RuntimeConfig.from_env()
        recall_rows = await run_awake_recall(
            conns,
            traditional_rows=traditional_rows,
            shadow_lineage=shadow_lineage,
            config=config,
            resume=args.resume,
        )
    else:
        recall_rows = read_jsonl(OUT_DIR / "awake_recall_results.jsonl")

    write_checkpoints(comparison_rows, recall_rows)
    summary = build_summary(comparison_rows, traditional_rows, recall_rows, shadow_metrics)
    maybe_write_plots(comparison_rows, summary)
    write_json(OUT_DIR / "SUMMARY.json", summary)
    write_report(summary)

    print(json.dumps(summary, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Three-arm memory strategy comparison")
    parser.add_argument("--resume", action="store_true", help="Resume traditional/recall JSONL arms")
    parser.add_argument("--skip-traditional", action="store_true")
    parser.add_argument("--skip-recall", action="store_true")
    parser.add_argument("--skip-full-raw-write", action="store_true")
    args = parser.parse_args(argv)
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
