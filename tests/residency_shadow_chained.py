"""Chained 1-200 residency shadow replay using frozen residency_shadow_v1 contract.

Shadow ACTIVE from generation N-1 feeds forward as base capsule for generation N.
Read-only against frozen experiment DBs; outputs isolated under
artifacts/degradation/residency_shadow_chained_1_200/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sqlite3
import statistics
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from endurance_harness import THREAD_ID  # noqa: E402
from memory_gateway.compaction import Capsule  # noqa: E402
from memory_gateway.context import estimate_tokens  # noqa: E402
from memory_gateway.db import content_hash  # noqa: E402
from memory_gateway.pipeline import build_lossless_packet  # noqa: E402
from memory_gateway.structured_client import OpenAICompatStructuredClient  # noqa: E402
from memory_gateway.telemetry import deterministic_input_hash  # noqa: E402
from residency_shadow_sample import (  # noqa: E402
    PROMPT_VERSION,
    build_consolidator_payload,
    call_shadow_with_retries,
    classify_additional_log,
    consolidator_api_key,
    evaluate_active,
    model_run,
    prompt_bundle_hash,
    read_system_prompt,
    reconstruct_input,
    replay_canonicalizer,
    resolve_output_capsule_id,
    shadow_response_format,
    sha256_text,
    validate_shadow_refs,
)

EXPECTED_BUNDLE_HASH = "124048d4f7475a2ec70141d8483026ee2d77817b93682ee38a65f0dcac89068c"
SAMPLE_DIR = _ROOT / "artifacts" / "degradation" / "residency_shadow_sample"
OUT_DIR = _ROOT / "artifacts" / "degradation" / "residency_shadow_chained_1_200"
DEGRADATION_DB = _ROOT / "data" / "live_endurance_degradation.db"
HARDENED_DB = _ROOT / "data" / "live_endurance_protocol_hardened.db"
CHECKPOINT_GENERATIONS = {1, 10, 25, 50, 75, 100, 125, 150, 175, 200}
FORK_GENERATION = 63


@dataclass
class ShadowState:
    generation: int
    shadow_capsule_id: str
    parent_shadow_capsule_id: str | None
    active_content: str
    active_hash: str
    covered_end_event_id: str | None


def verify_frozen_contract() -> str:
    bundle_hash = prompt_bundle_hash()
    if bundle_hash != EXPECTED_BUNDLE_HASH:
        raise SystemExit(
            f"Prompt bundle hash mismatch: expected {EXPECTED_BUNDLE_HASH} got {bundle_hash}. STOP."
        )
    stored = (SAMPLE_DIR / "prompt_bundle_hash.txt").read_text(encoding="utf-8").strip()
    if stored != EXPECTED_BUNDLE_HASH:
        raise SystemExit(f"Stored prompt_bundle_hash.txt mismatch: {stored}")
    return bundle_hash


def source_db_path(generation: int) -> Path:
    return DEGRADATION_DB if generation < FORK_GENERATION else HARDENED_DB


def open_connections() -> dict[str, sqlite3.Connection]:
    conns: dict[str, sqlite3.Connection] = {}
    for path in (DEGRADATION_DB, HARDENED_DB):
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conns[str(path)] = conn
    return conns


def conn_for_generation(conns: dict[str, sqlite3.Connection], generation: int) -> sqlite3.Connection:
    return conns[str(source_db_path(generation))]


def make_shadow_capsule(
    generation: int,
    content: str,
    parent_id: str | None,
    covered_end_event_id: str | None,
) -> Capsule:
    active_hash = content_hash(content)
    capsule_id = f"shadow_cap_{generation:04d}_{active_hash[:12]}"
    return Capsule(
        id=capsule_id,
        thread_id=THREAD_ID,
        content=content,
        capsule_hash=active_hash,
        covered_end_event_id=covered_end_event_id,
    )


def shadow_capsule_row(capsule: Capsule) -> sqlite3.Row:
    return {
        "id": capsule.id,
        "thread_id": capsule.thread_id,
        "content": capsule.content,
        "capsule_hash": capsule.capsule_hash,
        "covered_end_event_id": capsule.covered_end_event_id,
    }


def cumulative_raw_history_tokens(conn: sqlite3.Connection, snapshot_end_event_id: str | None) -> int:
    if not snapshot_end_event_id:
        return 0
    end = conn.execute(
        "SELECT sequence FROM events WHERE id = ?", (snapshot_end_event_id,)
    ).fetchone()
    if not end:
        return 0
    rows = conn.execute(
        """
        SELECT content FROM events
        WHERE thread_id = ? AND sequence <= ?
        ORDER BY sequence
        """,
        (THREAD_ID, end[0]),
    ).fetchall()
    return sum(estimate_tokens(row[0] or "") for row in rows)


def classify_residue_recursive(
    prev_active: str | None,
    shadow_active: str,
    shadow_residue: list[str],
    prev_residue_count: int,
) -> dict[str, Any]:
    inherited_markers = classify_additional_log(prev_active or "")
    current_markers = classify_additional_log(shadow_active)
    inherited_total = sum(inherited_markers.values())
    current_total = sum(current_markers.values())
    return {
        "inherited_from_previous_active_markers": inherited_total,
        "current_active_markers": current_total,
        "shadow_residue_count": len(shadow_residue),
        "prev_shadow_residue_count": prev_residue_count,
        "residue_removed_vs_previous": max(0, prev_residue_count - len(shadow_residue)),
        "newly_introduced_marker_delta": max(0, current_total - inherited_total),
        "persistent_low_residue": len(shadow_residue) <= 8 and prev_residue_count <= 8,
    }


def load_resume_state() -> tuple[int, ShadowState | None, list[dict[str, Any]], list[dict[str, Any]]]:
    lineage_path = OUT_DIR / "shadow_lineage.jsonl"
    metrics_path = OUT_DIR / "metrics.jsonl"
    lineage: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    if lineage_path.exists():
        lineage = [json.loads(line) for line in lineage_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if metrics_path.exists():
        metrics = [json.loads(line) for line in metrics_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lineage:
        return 1, None, lineage, metrics
    last = lineage[-1]
    state = ShadowState(
        generation=last["generation"],
        shadow_capsule_id=last["shadow_capsule_id"],
        parent_shadow_capsule_id=last.get("parent_shadow_capsule_id"),
        active_content=last["active"]["content"],
        active_hash=last["active"]["hash"],
        covered_end_event_id=last.get("covered_end_event_id"),
    )
    return last["generation"] + 1, state, lineage, metrics


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_checkpoint(generation: int, record: dict[str, Any]) -> None:
    checkpoint_dir = OUT_DIR / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    path = checkpoint_dir / f"gen_{generation:04d}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")


def estimate_preflight_budget(conns: dict[str, sqlite3.Connection]) -> dict[str, Any]:
    total_chars = 0
    sample_latencies: list[float] = []
    sample_path = _ROOT / "artifacts" / "degradation" / "residency_shadow_sample" / "results.jsonl"
    if sample_path.exists():
        for line in sample_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            tel = row.get("model_telemetry", {})
            if tel.get("wall_ms"):
                sample_latencies.append(float(tel["wall_ms"]))
    for generation in range(1, 201):
        conn = conn_for_generation(conns, generation)
        rebuilt = reconstruct_input(conn, generation)
        if isinstance(rebuilt, dict):
            continue
        total_chars += len(rebuilt.output_capsule_content)
    avg_latency_s = (sum(sample_latencies) / len(sample_latencies) / 1000) if sample_latencies else 2.5
    approx_input_tokens = total_chars // 4
    approx_output_tokens = 200 * 800
    return {
        "planned_gemini_calls": 200,
        "approx_input_tokens": approx_input_tokens,
        "approx_output_tokens": approx_output_tokens,
        "approx_total_tokens": approx_input_tokens + approx_output_tokens,
        "sample_mean_latency_seconds": round(avg_latency_s, 2),
        "estimated_runtime_hours": round(200 * avg_latency_s / 3600, 2),
        "note": "Includes canonicalizer replay per generation (local LM Studio) plus Gemini shadow call.",
    }


async def process_chained_generation(
    conns: dict[str, sqlite3.Connection],
    generation: int,
    prev_state: ShadowState | None,
    *,
    shadow_key: str,
    prev_shadow_residue: int = 0,
) -> dict[str, Any]:
    conn = conn_for_generation(conns, generation)
    rebuilt = reconstruct_input(conn, generation)
    if isinstance(rebuilt, dict):
        return {"generation": generation, "status": "UNAVAILABLE", "reason": rebuilt.get("reason")}

    canon_run = model_run(conn, rebuilt.job_id, "canonicalizer")
    cons_run = model_run(conn, rebuilt.job_id, "consolidator")
    if not canon_run or not cons_run:
        return {
            "generation": generation,
            "status": "UNAVAILABLE",
            "reason": "missing canonicalizer or consolidator model_run",
        }

    import os

    canon_key = os.environ.get("ORCHID_CANONICALIZER_API_KEY")
    try:
        canonical, canon_hash = await replay_canonicalizer(
            canon_run["endpoint"],
            canon_run["model"],
            canon_key,
            rebuilt.selected_events,
            canon_run.get("output_hash"),
        )
        packet = build_lossless_packet(canonical, rebuilt.selected_events)
    except Exception as error:
        return {
            "generation": generation,
            "status": "CANONICALIZER_FAILED",
            "reason": str(error),
        }

    shadow_base: Capsule | None = None
    if prev_state is not None:
        shadow_base = make_shadow_capsule(
            generation - 1,
            prev_state.active_content,
            prev_state.parent_shadow_capsule_id,
            prev_state.covered_end_event_id,
        )
    payload = build_consolidator_payload(
        shadow_capsule_row(shadow_base) if shadow_base else None,
        rebuilt.selected_events,
        packet,
        rebuilt.snapshot_end_event_id,
    )
    input_hash = deterministic_input_hash(payload)
    allowed_refs = (["base_capsule"] if shadow_base else []) + [
        item.id for item in rebuilt.selected_events
    ]

    client = OpenAICompatStructuredClient(
        endpoint=cons_run["endpoint"],
        model=cons_run["model"],
        prompt_version=PROMPT_VERSION,
        system_prompt=read_system_prompt(),
        generation_settings={
            "temperature": 0,
            "top_p": 1,
            "stream": False,
            "reasoning_effort": "none",
        },
        timeout=180.0,
        api_key=shadow_key,
    )
    client.response_format = shadow_response_format(allowed_refs)

    wall_start = time.perf_counter()
    try:
        shadow_response, telemetry = await call_shadow_with_retries(client, payload)
    except Exception as error:
        return {
            "generation": generation,
            "status": "TRANSPORT_ABORT",
            "reason": str(error),
            "model_input_hash": input_hash,
        }
    wall_ms = (time.perf_counter() - wall_start) * 1000

    ref_errors = validate_shadow_refs(shadow_response, set(allowed_refs))
    if ref_errors:
        return {
            "generation": generation,
            "status": "PROVENANCE_VALIDATION_FAILED",
            "reason": "; ".join(ref_errors[:5]),
            "model_input_hash": input_hash,
            "shadow_response": shadow_response,
            "telemetry": telemetry,
        }

    active_content = shadow_response.get("active", {}).get("content", "").strip()
    if not active_content:
        return {
            "generation": generation,
            "status": "SEMANTIC_FAILURE",
            "reason": "empty ACTIVE content",
            "model_input_hash": input_hash,
            "shadow_response": shadow_response,
        }

    prev_active = prev_state.active_content if prev_state else ""
    evaluation = evaluate_active(
        generation,
        rebuilt.output_capsule_content,
        active_content,
        prev_active,
    )
    if evaluation["current_fact_loss"]:
        return {
            "generation": generation,
            "status": "SEMANTIC_FAILURE",
            "failure_type": "current_fact_loss",
            "reason": evaluation["current_fact_loss_detail"],
            "evaluation": evaluation,
            "shadow_response": shadow_response,
            "model_input_hash": input_hash,
        }
    if evaluation["invented_state"]:
        return {
            "generation": generation,
            "status": "SEMANTIC_FAILURE",
            "failure_type": "invented_state",
            "reason": evaluation["invented_state_detail"],
            "evaluation": evaluation,
            "shadow_response": shadow_response,
            "model_input_hash": input_hash,
        }
    if evaluation["shadow_resurrection_count"] > 0:
        return {
            "generation": generation,
            "status": "SEMANTIC_FAILURE",
            "failure_type": "semantic_resurrection",
            "reason": f"resurrection_count={evaluation['shadow_resurrection_count']}",
            "evaluation": evaluation,
            "shadow_response": shadow_response,
            "model_input_hash": input_hash,
        }

    from live_endurance import residue_hits  # noqa: E402
    from endurance_harness import expected_state_after  # noqa: E402

    shadow_residue_list = residue_hits(active_content, expected_state_after(generation))
    residue_recursive = classify_residue_recursive(
        prev_active,
        active_content,
        shadow_residue_list,
        prev_shadow_residue,
    )

    shadow_capsule = make_shadow_capsule(
        generation,
        active_content,
        prev_state.shadow_capsule_id if prev_state else None,
        rebuilt.snapshot_end_event_id,
    )
    retire = shadow_response.get("retire", [])
    raw_only = shadow_response.get("raw_only", [])
    retire_text = json.dumps(retire, ensure_ascii=False)
    raw_text = json.dumps(raw_only, ensure_ascii=False)
    cumulative_raw = cumulative_raw_history_tokens(conn, rebuilt.snapshot_end_event_id)

    prev_tokens = estimate_tokens(prev_active) if prev_state else 0
    shadow_tokens = evaluation["shadow_active_tokens"]
    original_tokens = evaluation["original_capsule_tokens"]

    metrics = {
        "generation": generation,
        "original_capsule_chars": evaluation["original_capsule_chars"],
        "original_capsule_tokens": original_tokens,
        "shadow_active_chars": evaluation["shadow_active_chars"],
        "shadow_active_tokens": shadow_tokens,
        "active_token_delta_vs_previous": shadow_tokens - prev_tokens,
        "active_token_ratio_vs_original": round(shadow_tokens / original_tokens, 4) if original_tokens else None,
        "cumulative_raw_history_tokens": cumulative_raw,
        "shadow_active_over_raw_history": round(shadow_tokens / cumulative_raw, 6) if cumulative_raw else None,
        "original_over_raw_history": round(original_tokens / cumulative_raw, 6) if cumulative_raw else None,
        "original_residue_count": evaluation["original_residue_count"],
        "shadow_residue_count": evaluation["shadow_residue_count"],
        "current_fact_loss": evaluation["current_fact_loss"],
        "semantic_resurrection_count": evaluation["shadow_resurrection_count"],
        "invented_state": evaluation["invented_state"],
        "retire_entry_count": len(retire),
        "retire_tokens": estimate_tokens(retire_text),
        "raw_only_entry_count": len(raw_only),
        "raw_only_tokens": estimate_tokens(raw_text),
        "model_input_tokens": telemetry.get("input_tokens"),
        "model_output_tokens": telemetry.get("output_tokens"),
        "model_reasoning_tokens": telemetry.get("reasoning_tokens", 0),
        "wall_ms": wall_ms,
        "transport_retry_count": max(0, int(telemetry.get("transport_attempt_count", 1)) - 1),
        "residue_recursive": residue_recursive,
    }

    lineage = {
        "generation": generation,
        "shadow_capsule_id": shadow_capsule.id,
        "parent_shadow_capsule_id": prev_state.shadow_capsule_id if prev_state else None,
        "original_capsule_id": rebuilt.output_capsule_id,
        "original_base_capsule_id": rebuilt.base_capsule_id,
        "covered_end_event_id": rebuilt.snapshot_end_event_id,
        "canonical_evidence_hash": packet.packet_hash,
        "model_input_hash": input_hash,
        "raw_response_hash": telemetry.get("raw_response_hash"),
        "reconstruction_status": "CANONICALIZER_OUTPUT_VERIFIED",
        "active": {
            "content": active_content,
            "hash": shadow_capsule.capsule_hash,
            "tokens": shadow_tokens,
            "evidence_refs": shadow_response.get("active", {}).get("evidence_refs", []),
        },
        "retire": retire,
        "raw_only": raw_only,
        "model_telemetry": {
            "model": cons_run["model"],
            "endpoint": cons_run["endpoint"].split("?")[0],
            "prompt_version": PROMPT_VERSION,
            "input_hash": input_hash,
            "raw_response_hash": telemetry.get("raw_response_hash"),
            "input_tokens": telemetry.get("input_tokens"),
            "output_tokens": telemetry.get("output_tokens"),
            "reasoning_tokens": telemetry.get("reasoning_tokens", 0),
            "wall_ms": wall_ms,
            "finish_reason": telemetry.get("finish_reason"),
            "transport_attempt_count": telemetry.get("transport_attempt_count"),
        },
        "validation": evaluation,
        "metrics": metrics,
    }

    return {
        "generation": generation,
        "status": "SUCCEEDED",
        "shadow_state": ShadowState(
            generation=generation,
            shadow_capsule_id=shadow_capsule.id,
            parent_shadow_capsule_id=prev_state.shadow_capsule_id if prev_state else None,
            active_content=active_content,
            active_hash=shadow_capsule.capsule_hash,
            covered_end_event_id=rebuilt.snapshot_end_event_id,
        ),
        "lineage_record": lineage,
        "metrics_record": metrics,
        "prev_shadow_residue": evaluation["shadow_residue_count"],
    }


def linear_slope(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    return num / den if den else None


def interval_stats(metrics: list[dict[str, Any]], start: int, end: int) -> dict[str, Any]:
    band = [m for m in metrics if start <= m["generation"] <= end]
    if not band:
        return {}
    tokens = [m["shadow_active_tokens"] for m in band]
    return {
        "start_generation": start,
        "end_generation": end,
        "starting_active_tokens": band[0]["shadow_active_tokens"],
        "ending_active_tokens": band[-1]["shadow_active_tokens"],
        "absolute_growth": band[-1]["shadow_active_tokens"] - band[0]["shadow_active_tokens"],
        "slope_tokens_per_generation": linear_slope(
            [float(m["generation"]) for m in band],
            [float(m["shadow_active_tokens"]) for m in band],
        ),
        "peak_active": max(tokens),
        "minimum_active": min(tokens),
        "mean_active": round(statistics.mean(tokens), 2),
        "median_active": round(statistics.median(tokens), 2),
        "ending_cumulative_raw_history_tokens": band[-1].get("cumulative_raw_history_tokens"),
    }


def build_summary(
    metrics: list[dict[str, Any]],
    lineage: list[dict[str, Any]],
    *,
    completed_through: int,
    experiment_passed: bool,
    first_failure: dict[str, Any] | None,
    runtime_seconds: float,
) -> dict[str, Any]:
    if not metrics:
        return {
            "completed_through_generation": completed_through,
            "experiment_passed": experiment_passed,
            "first_failure_generation": first_failure.get("generation") if first_failure else None,
            "first_failure_type": first_failure.get("status") if first_failure else None,
            "prompt_version": PROMPT_VERSION,
            "prompt_bundle_hash": EXPECTED_BUNDLE_HASH,
            "notes": ["no metrics recorded"],
        }

    gen200 = next((m for m in metrics if m["generation"] == 200), metrics[-1])
    late = [m for m in metrics if m["generation"] >= 126]
    late_tokens = [m["shadow_active_tokens"] for m in late]
    late_slope = linear_slope(
        [float(m["generation"]) for m in late],
        [float(m["shadow_active_tokens"]) for m in late],
    ) if late else None

    fact_loss = sum(1 for m in metrics if m.get("current_fact_loss"))
    resurrection = sum(m.get("semantic_resurrection_count", 0) for m in metrics)
    invented = sum(1 for m in metrics if m.get("invented_state"))
    retire_entries = sum(m.get("retire_entry_count", 0) for m in metrics)
    raw_entries = sum(m.get("raw_only_entry_count", 0) for m in metrics)

    orig_gen200 = gen200["original_capsule_tokens"]
    shadow_gen200 = gen200["shadow_active_tokens"]
    reduction = (orig_gen200 - shadow_gen200) / orig_gen200 if orig_gen200 else None

    peak_active = max(m["shadow_active_tokens"] for m in metrics)
    late_mean = statistics.mean(late_tokens) if late_tokens else None
    late_stdev = statistics.pstdev(late_tokens) if len(late_tokens) > 1 else 0.0

    orig_residue_200 = gen200.get("original_residue_count")
    shadow_residue_200 = gen200.get("shadow_residue_count")

    recursive_prevented = all(
        m.get("residue_recursive", {}).get("shadow_residue_count", 99) <= 12 for m in metrics if m["generation"] >= 50
    )
    plateau = (
        late_slope is not None
        and abs(late_slope) < 0.15
        and late_mean is not None
        and late_stdev < 15
    )

    gemini_in = sum(m.get("model_input_tokens") or 0 for m in metrics)
    gemini_out = sum(m.get("model_output_tokens") or 0 for m in metrics)
    gemini_reason = sum(m.get("model_reasoning_tokens") or 0 for m in metrics)

    passed = experiment_passed and completed_through >= 200 and fact_loss == 0 and resurrection == 0 and invented == 0

    return {
        "completed_through_generation": completed_through,
        "experiment_passed": passed,
        "first_failure_generation": first_failure.get("generation") if first_failure else None,
        "first_failure_type": first_failure.get("status") if first_failure else None,
        "prompt_version": PROMPT_VERSION,
        "prompt_bundle_hash": EXPECTED_BUNDLE_HASH,
        "current_fact_loss_count": fact_loss,
        "semantic_resurrection_count": resurrection,
        "invented_state_count": invented,
        "original_gen200_tokens": orig_gen200 if completed_through >= 200 else None,
        "shadow_gen200_active_tokens": shadow_gen200 if completed_through >= 200 else None,
        "gen200_active_reduction_fraction": round(reduction, 4) if reduction is not None and completed_through >= 200 else None,
        "shadow_peak_active_tokens": peak_active,
        "shadow_late_run_mean_tokens": round(late_mean, 2) if late_mean is not None else None,
        "shadow_late_run_stddev_tokens": round(late_stdev, 2) if late_tokens else None,
        "shadow_late_run_growth_slope": round(late_slope, 4) if late_slope is not None else None,
        "original_gen200_residue": orig_residue_200 if completed_through >= 200 else None,
        "shadow_gen200_residue": shadow_residue_200 if completed_through >= 200 else None,
        "retire_entry_count": retire_entries,
        "raw_only_entry_count": raw_entries,
        "recursive_residue_accumulation_prevented": recursive_prevented if completed_through >= 50 else None,
        "active_working_set_plateau_observed": plateau if completed_through >= 126 else None,
        "gemini_total_input_tokens": gemini_in,
        "gemini_total_output_tokens": gemini_out,
        "gemini_total_reasoning_tokens": gemini_reason,
        "runtime_seconds": round(runtime_seconds, 1),
        "growth_intervals": {
            "1_50": interval_stats(metrics, 1, 50),
            "51_100": interval_stats(metrics, 51, 100),
            "101_150": interval_stats(metrics, 101, 150),
            "151_200": interval_stats(metrics, 151, 200),
            "1_200": interval_stats(metrics, 1, min(200, completed_through)),
        },
        "production_residency_policy_recommended": passed,
        "retire_policy_tuning_recommended_before_ssd": retire_entries < len(metrics) * 0.05,
        "ssd_memory_implementation_recommended_next": False,
        "confidence": "high" if passed and plateau else ("medium" if passed else "low"),
        "notes": [],
    }


def write_report(summary: dict[str, Any], metrics: list[dict[str, Any]]) -> None:
    lines = [
        "# Chained Residency Shadow Replay 1-200",
        "",
        "## Methodology",
        "",
        "Verified logical-input chained shadow replay using frozen `residency_shadow_v1`.",
        "Shadow ACTIVE from generation N-1 feeds forward as base capsule for generation N.",
        "Canonicalizer output_hash replay verified per generation; not byte-identical consolidator replay.",
        "",
        f"**Prompt bundle hash:** `{EXPECTED_BUNDLE_HASH}`",
        f"**Completed through generation:** {summary.get('completed_through_generation')}",
        f"**Experiment passed:** {summary.get('experiment_passed')}",
        "",
        "## Current-state correctness",
        "",
        f"- Current fact loss: **{summary.get('current_fact_loss_count')}**",
        f"- Semantic resurrection: **{summary.get('semantic_resurrection_count')}**",
        f"- Invented state: **{summary.get('invented_state_count')}**",
        "",
        "## ACTIVE growth",
        "",
        f"- Gen-200 shadow ACTIVE tokens: **{summary.get('shadow_gen200_active_tokens')}**",
        f"- Gen-200 original tokens: **{summary.get('original_gen200_tokens')}**",
        f"- Gen-200 reduction: **{100 * (summary.get('gen200_active_reduction_fraction') or 0):.1f}%**",
        f"- Peak shadow ACTIVE: **{summary.get('shadow_peak_active_tokens')}**",
        f"- Late-run mean ACTIVE (126-200): **{summary.get('shadow_late_run_mean_tokens')}**",
        f"- Late-run growth slope: **{summary.get('shadow_late_run_growth_slope')}** tok/gen",
        f"- Plateau observed: **{summary.get('active_working_set_plateau_observed')}**",
        "",
        "## Residue",
        "",
        f"- Gen-200 original residue: **{summary.get('original_gen200_residue')}**",
        f"- Gen-200 shadow residue: **{summary.get('shadow_gen200_residue')}**",
        f"- Recursive accumulation prevented: **{summary.get('recursive_residue_accumulation_prevented')}**",
        "",
        "## RETIRE / RAW_ONLY",
        "",
        f"- Total RETIRE entries: **{summary.get('retire_entry_count')}**",
        f"- Total RAW_ONLY groups: **{summary.get('raw_only_entry_count')}**",
        "",
        "## Gemini usage",
        "",
        f"- Input tokens: **{summary.get('gemini_total_input_tokens')}**",
        f"- Output tokens: **{summary.get('gemini_total_output_tokens')}**",
        f"- Runtime: **{summary.get('runtime_seconds')}s**",
        "",
        "## Recommendations",
        "",
        f"- Production residency semantics justified: **{summary.get('production_residency_policy_recommended')}**",
        f"- RETIRE policy tuning before SSD: **{summary.get('retire_policy_tuning_recommended_before_ssd')}**",
        "",
        "## Per-generation metrics (selected)",
        "",
        "| Gen | Orig | Shadow | Residue O/S | RETIRE | RAW |",
        "|-----|------|--------|-------------|--------|-----|",
    ]
    for m in metrics:
        if m["generation"] in CHECKPOINT_GENERATIONS or m["generation"] == summary.get("completed_through_generation"):
            lines.append(
                f"| {m['generation']} | {m['original_capsule_tokens']} | {m['shadow_active_tokens']} | "
                f"{m['original_residue_count']}/{m['shadow_residue_count']} | {m['retire_entry_count']} | {m['raw_only_entry_count']} |"
            )
    (OUT_DIR / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def try_write_plots(metrics: list[dict[str, Any]]) -> None:
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except ImportError:
        return
    gens = [m["generation"] for m in metrics]
    orig = [m["original_capsule_tokens"] for m in metrics]
    shadow = [m["shadow_active_tokens"] for m in metrics]
    residue_o = [m["original_residue_count"] for m in metrics]
    residue_s = [m["shadow_residue_count"] for m in metrics]
    raw_hist = [m.get("cumulative_raw_history_tokens") or 0 for m in metrics]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(gens, orig, label="original capsule", alpha=0.8)
    ax.plot(gens, shadow, label="shadow ACTIVE", alpha=0.8)
    ax.set_xlabel("generation")
    ax.set_ylabel("tokens")
    ax.legend()
    ax.set_title("ACTIVE tokens by generation")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "active_tokens_by_generation.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(gens, residue_o, label="original residue", alpha=0.8)
    ax.plot(gens, residue_s, label="shadow residue", alpha=0.8)
    ax.set_xlabel("generation")
    ax.set_ylabel("residue count")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT_DIR / "residue_by_generation.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(gens, [s / r if r else 0 for s, r in zip(shadow, raw_hist)], label="shadow/raw", alpha=0.8)
    ax.plot(gens, [o / r if r else 0 for o, r in zip(orig, raw_hist)], label="original/raw", alpha=0.8)
    ax.set_xlabel("generation")
    ax.set_ylabel("ratio")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT_DIR / "active_vs_raw_history.png")
    plt.close(fig)


async def async_main(resume: bool) -> int:
    bundle_hash = verify_frozen_contract()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conns = open_connections()

    start_gen, prev_state, lineage, metrics = (1, None, [], [])
    if resume:
        start_gen, prev_state, lineage, metrics = load_resume_state()
        if start_gen > 200:
            print("Already completed through generation 200.")
            return 0

    budget = estimate_preflight_budget(conns)
    print("=== CHAINED RESIDENCY SHADOW 1-200 — PRE-FLIGHT ===")
    print(json.dumps(budget, indent=2))
    print(f"Prompt version: {PROMPT_VERSION}")
    print(f"Verified prompt bundle hash: {bundle_hash}")
    if resume and start_gen > 1:
        print(f"Resuming from generation {start_gen}")

    shadow_key = consolidator_api_key()
    if not shadow_key:
        print("No consolidator API key available.", file=sys.stderr)
        return 2

    if start_gen == 1:
        manifest = {
            "experiment_name": "residency_shadow_chained_1_200",
            "prompt_version": PROMPT_VERSION,
            "prompt_bundle_hash": bundle_hash,
            "gemini_model": "gemini-3.7-flash",
            "reconstruction_classification": "CANONICALIZER_OUTPUT_VERIFIED logical-input chained replay",
            "source_dbs": {
                "generations_1_62": str(DEGRADATION_DB),
                "generations_63_200": str(HARDENED_DB),
            },
            "planned_generations": 200,
            "start_timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": str(uuid.uuid4()),
        }
        (OUT_DIR / "RUN_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    lineage_path = OUT_DIR / "shadow_lineage.jsonl"
    metrics_path = OUT_DIR / "metrics.jsonl"
    run_start = time.perf_counter()
    first_failure: dict[str, Any] | None = None
    prev_shadow_residue = metrics[-1]["shadow_residue_count"] if metrics else 0
    attempted = len(metrics)
    succeeded = len(metrics)

    for generation in range(start_gen, 201):
        print(f"Generation {generation}/200...", flush=True)
        result = await process_chained_generation(
            conns,
            generation,
            prev_state,
            shadow_key=shadow_key,
            prev_shadow_residue=prev_shadow_residue,
        )
        attempted += 1
        status = result.get("status")
        if status == "SUCCEEDED":
            succeeded += 1
            append_jsonl(lineage_path, result["lineage_record"])
            append_jsonl(metrics_path, result["metrics_record"])
            lineage.append(result["lineage_record"])
            metrics.append(result["metrics_record"])
            prev_state = result["shadow_state"]
            prev_shadow_residue = result["prev_shadow_residue"]
            m = result["metrics_record"]
            print(
                f"  OK orig={m['original_capsule_tokens']} shadow={m['shadow_active_tokens']} "
                f"residue {m['original_residue_count']}->{m['shadow_residue_count']}",
                flush=True,
            )
            if generation in CHECKPOINT_GENERATIONS:
                write_checkpoint(generation, result["lineage_record"])
        else:
            print(f"  STOP {status}: {result.get('reason', '')}", flush=True)
            first_failure = result
            fail_path = OUT_DIR / f"failure_gen_{generation:04d}.json"
            fail_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
            break

    runtime = time.perf_counter() - run_start
    completed = metrics[-1]["generation"] if metrics else (start_gen - 1)
    experiment_passed = first_failure is None and completed >= 200
    summary = build_summary(
        metrics,
        lineage,
        completed_through=completed,
        experiment_passed=experiment_passed,
        first_failure=first_failure,
        runtime_seconds=runtime,
    )
    (OUT_DIR / "SUMMARY.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_report(summary, metrics)
    try_write_plots(metrics)

    print("\n=== CHAINED SHADOW REPLAY COMPLETE ===")
    print(f"chained shadow replay completed: {'yes' if experiment_passed else 'no'}")
    print(f"completed through generation: {completed}")
    if first_failure:
        print(f"first failure generation/type: {first_failure.get('generation')}/{first_failure.get('status')}")
    print(f"prompt version: {PROMPT_VERSION}")
    print(f"verified prompt bundle hash: {bundle_hash}")
    print(f"Gemini calls attempted/succeeded: {attempted}/{succeeded}")
    print(f"current fact loss count: {summary.get('current_fact_loss_count')}")
    print(f"genuine resurrection count: {summary.get('semantic_resurrection_count')}")
    print(f"invented state count: {summary.get('invented_state_count')}")
    print(f"original gen-200 capsule tokens: {summary.get('original_gen200_tokens')}")
    print(f"shadow gen-200 ACTIVE tokens: {summary.get('shadow_gen200_active_tokens')}")
    if summary.get("gen200_active_reduction_fraction") is not None:
        print(f"gen-200 ACTIVE reduction %: {100 * summary['gen200_active_reduction_fraction']:.1f}")
    print(f"shadow peak ACTIVE tokens: {summary.get('shadow_peak_active_tokens')}")
    print(f"late-run mean ACTIVE tokens: {summary.get('shadow_late_run_mean_tokens')}")
    print(f"late-run ACTIVE growth slope: {summary.get('shadow_late_run_growth_slope')}")
    print(f"original gen-200 residue: {summary.get('original_gen200_residue')}")
    print(f"shadow gen-200 residue: {summary.get('shadow_gen200_residue')}")
    print(f"RETIRE entry count: {summary.get('retire_entry_count')}")
    print(f"RAW_ONLY entry count: {summary.get('raw_only_entry_count')}")
    print(f"recursive residue accumulation prevented: {summary.get('recursive_residue_accumulation_prevented')}")
    print(f"ACTIVE plateau observed: {summary.get('active_working_set_plateau_observed')}")
    print(f"Gemini total input/output/reasoning tokens: {summary.get('gemini_total_input_tokens')}/{summary.get('gemini_total_output_tokens')}/{summary.get('gemini_total_reasoning_tokens')}")
    print(f"total runtime: {summary.get('runtime_seconds')}s")
    print(f"production residency semantics justified: {summary.get('production_residency_policy_recommended')}")
    print(f"RETIRE policy tuning recommended before SSD: {summary.get('retire_policy_tuning_recommended_before_ssd')}")
    print("artifact paths:")
    for name in ("RUN_MANIFEST.json", "shadow_lineage.jsonl", "metrics.jsonl", "SUMMARY.json", "REPORT.md"):
        print(f"  {OUT_DIR / name}")
    return 0 if experiment_passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Chained residency shadow replay 1-200")
    parser.add_argument("--resume", action="store_true", help="Resume from last successful generation")
    args = parser.parse_args()
    return asyncio.run(async_main(resume=args.resume))


if __name__ == "__main__":
    raise SystemExit(main())
