"""Run the benchmark-only frozen structural-noise prefilter arm.

This adapter deliberately keeps the existing selector -> Solar apparatus. It
freezes the 1.2K source-item universe, makes a deterministic KEEP/DROP
decision, and monkeypatches only the selector decision seam. No Qwen selector
client is constructed for this arm.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import phase3_2_pipeline_ablation as selector_module  # noqa: E402
import phase3_3_direct_consolidation as harness  # noqa: E402

import memory_gateway.pipeline_adapters as adapters  # noqa: E402
from memory_gateway.compaction import (  # noqa: E402
    Event,
    SourceItem,
    expand_source_items,
    source_item_parent_event_id,
)

RULESET_JSON = ROOT / "artifacts/agent_benchmarks/structural_noise_prefilter_v1/RULESET.json"
RULESET_HASH = ROOT / "artifacts/agent_benchmarks/structural_noise_prefilter_v1/RULESET_HASH.txt"
PHASE_ROOT = ROOT / "artifacts/agent_benchmarks/structural_noise_prefilter_v1"
SELECTOR_TARGET = 1_200
WAIT_NAMES = frozenset({"wait", "wait_agent", "poll", "sleep"})
WAIT_ARGUMENT_KEYS = frozenset(
    {"cell_id", "duration_ms", "max_tokens", "poll_interval_ms", "timeout_ms", "yield_time_ms"}
)
MISSING = object()

TRACE_CONFIG: dict[str, dict[str, Path | str]] = {
    "panoref": {
        "replay": ROOT / "artifacts/agent_benchmarks/panoref_direct_generalization_bounded_v2/frozen_panoref_replay/events.jsonl",
        "oracle": ROOT / "artifacts/agent_benchmarks/panoref_direct_generalization_bounded_v2/semantic_oracle",
        "raw_plan": ROOT / "artifacts/agent_benchmarks/panoref_direct_generalization_bounded_v2/arm_direct_raw_solar/batch_plan.json",
        "oracle_version": "panoref-structural-noise-prefilter-v1",
        "thread_id": "phase3-3-panoref-structural-noise-prefilter",
        "qwen_summary": ROOT / "artifacts/agent_benchmarks/panoref_selector_control_repeats_v1/repeat_01/arm_b_selector_solar/SUMMARY.json",
    },
    "freetoshop": {
        "replay": ROOT / "artifacts/agent_benchmarks/freetoshop_operability_hardening/frozen_freetoshop_replay/events.jsonl",
        "oracle": ROOT / "artifacts/agent_benchmarks/freetoshop_direct_consolidation/semantic_oracle",
        "raw_plan": ROOT / "artifacts/agent_benchmarks/freetoshop_direct_consolidation/arm_c_raw_solar/batch_plan.json",
        "oracle_version": "freetoshop-selector-structural-noise-prefilter-v1",
        "thread_id": "phase3-3-freetoshop-structural-noise-prefilter",
        "qwen_summary": ROOT / "artifacts/agent_benchmarks/freetoshop_direct_consolidation/arm_b_selector_solar/SUMMARY.json",
    },
}


def sha256_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_events(path: Path) -> list[Event]:
    events: list[Event] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        events.append(
            Event(
                id=row["event_id"],
                sequence=int(row["sequence"]),
                content=row["content"],
                content_hash=row["content_hash"],
                event_type=row["event_type"],
                role=row.get("role"),
            )
        )
    return events


def parse_content(event: Event) -> dict[str, Any] | None:
    try:
        value = json.loads(event.content)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def tool_call_records(event: Event) -> list[dict[str, Any]]:
    value = parse_content(event)
    if value is None:
        return []
    if event.event_type in {"function_call", "custom_tool_call"}:
        return [value]
    if event.event_type == "assistant_message":
        calls = value.get("tool_calls")
        return [call for call in calls if isinstance(call, dict)] if isinstance(calls, list) else []
    return []


def call_record_fields(record: dict[str, Any]) -> tuple[str | None, Any, str | None]:
    if "function" in record and isinstance(record.get("function"), dict):
        function = record["function"]
        name = function.get("name")
        arguments = function.get("arguments", MISSING)
        call_id = record.get("call_id") or record.get("id")
        return name if isinstance(name, str) else None, arguments, call_id if isinstance(call_id, str) else None
    name = record.get("name")
    arguments = record.get("arguments", record.get("input", MISSING))
    call_id = record.get("call_id") or record.get("id")
    return name if isinstance(name, str) else None, arguments, call_id if isinstance(call_id, str) else None


def parse_arguments(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def explicit_wait_call(event: Event) -> tuple[str, str] | None:
    records = tool_call_records(event)
    if len(records) != 1:
        return None
    name, raw_arguments, call_id = call_record_fields(records[0])
    arguments = parse_arguments(raw_arguments)
    if name not in WAIT_NAMES or arguments is None:
        return None
    if not set(arguments).issubset(WAIT_ARGUMENT_KEYS):
        return None
    return name, call_id or f"event:{event.id}"


def output_payload(event: Event) -> tuple[bool, Any, str | None]:
    value = parse_content(event)
    if value is None:
        return False, MISSING, None
    if event.event_type in {"function_call_output", "custom_tool_call_output"}:
        if "output" not in value:
            return False, MISSING, None
        payload = value["output"]
        call_id = value.get("call_id")
        return True, payload, call_id if isinstance(call_id, str) else None
    if event.event_type == "tool_result":
        if "content" not in value:
            return False, MISSING, None
        payload = value["content"]
        call_id = value.get("tool_call_id") or value.get("call_id")
        return True, payload, call_id if isinstance(call_id, str) else None
    return False, MISSING, None


def is_exactly_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def is_typed_wait_result(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return False
    return (
        isinstance(parsed, dict)
        and set(parsed) == {"message", "timed_out"}
        and isinstance(parsed["message"], str)
        and isinstance(parsed["timed_out"], bool)
    )


def classify_events(events: list[Event]) -> dict[str, dict[str, Any]]:
    wait_calls: dict[str, str] = {}
    for event in events:
        match = explicit_wait_call(event)
        if match:
            _, call_id = match
            wait_calls[call_id] = event.id

    decisions: dict[str, dict[str, Any]] = {}
    for event in events:
        match = explicit_wait_call(event)
        if match:
            decisions[event.id] = {"decision": "DROP", "rule": "explicit_mechanical_wait_call", "call_name": match[0]}
            continue
        present, payload, call_id = output_payload(event)
        if not present:
            continue
        if is_exactly_empty(payload):
            decisions[event.id] = {"decision": "DROP", "rule": "empty_typed_tool_output"}
            continue
        if call_id in wait_calls and is_typed_wait_result(payload):
            decisions[event.id] = {"decision": "DROP", "rule": "typed_wait_protocol_result", "linked_call_event_id": wait_calls[call_id]}
    return decisions


def item_detail(item: SourceItem, decision: dict[str, Any] | None, event: Event) -> dict[str, Any]:
    return {
        "item_id": item.id,
        "parent_event_id": source_item_parent_event_id(item),
        "sequence": item.sequence,
        "event_type": item.event_type,
        "role": item.role,
        "source_tokens": harness.source_token_count([item]),
        "content_chars": len(item.content),
        "decision": decision.get("decision", "KEEP") if decision else "KEEP",
        "rule": decision.get("rule") if decision else None,
        "content_preview": item.content[:240] if decision else None,
        "parent_content_is_full_item": item.id == event.id,
    }


def qwen_selected_ids(config: dict[str, Path | str]) -> set[str]:
    summary_path = config["qwen_summary"]
    if not isinstance(summary_path, Path) or not summary_path.exists():
        return set()
    summary = load_json(summary_path)
    selector = summary.get("selector") or {}
    return {item_id for item_id in selector.get("selected_ids", []) if isinstance(item_id, str)}


def analyze_trace(trace: str) -> dict[str, Any]:
    config = TRACE_CONFIG[trace]
    replay = config["replay"]
    assert isinstance(replay, Path)
    events = load_events(replay)
    items = list(expand_source_items(events, selector_budget_tokens=SELECTOR_TARGET, safety_margin=0.8))
    decisions_by_event = classify_events(events)
    details = [item_detail(item, decisions_by_event.get(source_item_parent_event_id(item)), next(event for event in events if event.id == source_item_parent_event_id(item))) for item in items]
    removed = [row for row in details if row["decision"] == "DROP"]
    selected_ids = {row["item_id"] for row in details if row["decision"] == "KEEP"}
    qwen_selected = qwen_selected_ids(config)
    universe = {row["item_id"] for row in details}
    qwen_discarded = universe - qwen_selected if qwen_selected else set()
    deterministic_ids = {row["item_id"] for row in removed}
    deterministic_only = deterministic_ids & qwen_selected if qwen_selected else set()
    qwen_only = qwen_discarded - deterministic_ids if qwen_selected else set()
    return {
        "trace": trace,
        "replay_sha256": sha256_bytes(replay),
        "event_count": len(events),
        "source_item_count": len(items),
        "source_tokens": harness.source_token_count(items),
        "removed_item_count": len(removed),
        "removed_source_tokens": sum(row["source_tokens"] for row in removed),
        "removed_fraction": sum(row["source_tokens"] for row in removed) / max(harness.source_token_count(items), 1),
        "selected_source_tokens": sum(row["source_tokens"] for row in details if row["decision"] == "KEEP"),
        "rule_counts": dict(Counter(row["rule"] for row in removed)),
        "event_type_counts": dict(Counter(row["event_type"] for row in removed)),
        "role_counts": dict(Counter(row["role"] for row in removed)),
        "all_items": details,
        "removed_items": removed,
        "qwen_selected_item_count": len(qwen_selected),
        "qwen_discarded_item_count": len(qwen_discarded),
        "deterministic_qwen_overlap_item_count": len(deterministic_ids & qwen_discarded),
        "deterministic_only_item_count": len(deterministic_only),
        "qwen_only_item_count": len(qwen_only),
        "deterministic_only_items": [row for row in details if row["item_id"] in deterministic_only],
        "qwen_only_items": [row for row in details if row["item_id"] in qwen_only],
        "potential_useful_content_false_positive_items": [],
        "manual_audit_required": True,
    }


def prepare_output_root(out: Path, config: dict[str, Path | str]) -> None:
    if out.exists():
        raise RuntimeError(f"refusing to overwrite existing run root: {out}")
    replay = config["replay"]
    oracle = config["oracle"]
    raw_plan = config["raw_plan"]
    assert isinstance(replay, Path) and isinstance(oracle, Path) and isinstance(raw_plan, Path)
    required = [replay, oracle / "manifest.json", oracle / "deterministic_checks.jsonl", raw_plan]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("missing frozen benchmark inputs: " + ", ".join(missing))
    (out / "semantic_oracle").mkdir(parents=True)
    (out / "arm_c_raw_solar").mkdir(parents=True)
    for name in ("manifest.json", "deterministic_checks.jsonl", "checkpoints.jsonl", "JUDGE_RUBRIC.md"):
        source = oracle / name
        if source.exists():
            shutil.copy2(source, out / "semantic_oracle" / name)
    shutil.copy2(raw_plan, out / "arm_c_raw_solar/batch_plan.json")
    (out / "FREEZE_COMPLETE").write_text("structural-noise-prefilter-v1\n", encoding="utf-8")


def verify_ruleset() -> str:
    rules = load_json(RULESET_JSON)
    canonical = json.dumps(rules, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    actual = hashlib.sha256(canonical).hexdigest()
    expected = RULESET_HASH.read_text(encoding="utf-8").strip()
    if actual != expected:
        raise RuntimeError(f"ruleset hash mismatch: expected {expected}, computed {actual}")
    return actual


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", choices=sorted(TRACE_CONFIG), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--audit-only", action="store_true")
    return parser.parse_args()


async def run(trace: str, out: Path) -> None:
    ruleset_sha256 = verify_ruleset()
    config = TRACE_CONFIG[trace]
    audit = analyze_trace(trace)
    if out.exists():
        raise RuntimeError(f"refusing to overwrite existing run root: {out}")
    prepare_output_root(out, config)
    write_json(out / "STRUCTURAL_AUDIT.json", {"ruleset_sha256": ruleset_sha256, **audit})

    replay = config["replay"]
    assert isinstance(replay, Path)
    events = load_events(replay)
    original_expand = expand_source_items
    frozen_items = tuple(original_expand(events, selector_budget_tokens=SELECTOR_TARGET, safety_margin=0.8))
    decisions_by_event = classify_events(events)
    selected_items = tuple(item for item in frozen_items if source_item_parent_event_id(item) not in decisions_by_event)
    selected_ids = tuple(item.id for item in selected_items)
    if len(selected_ids) != len(set(selected_ids)):
        raise RuntimeError("structural prefilter selected duplicate source IDs")
    chunk_plan = {
        "target_tokens": SELECTOR_TARGET,
        "source_item_count": len(frozen_items),
        "source_tokens": harness.source_token_count(frozen_items),
        "selected_item_count": len(selected_items),
        "selected_source_tokens": harness.source_token_count(selected_items),
        "chunks": [[item.id for item in chunk] for chunk in adapters._chunk_events(list(frozen_items), target_tokens=SELECTOR_TARGET)],
        "selected_ids": list(selected_ids),
    }
    chunk_plan["chunk_plan_hash"] = canonical_digest(chunk_plan)
    write_json(out / "SELECTOR_CHUNK_PLAN.json", chunk_plan)

    def frozen_expand(events_arg, *, selector_budget_tokens, safety_margin):
        return frozen_items

    selector_module.SELECTOR_TARGET = SELECTOR_TARGET
    selector_module.expand_source_items = frozen_expand
    adapters.expand_source_items = frozen_expand
    harness.expand_source_items = frozen_expand
    harness.SELECTOR_TARGET = SELECTOR_TARGET
    harness.REPLAY = replay
    harness.OUT = out
    harness.THREAD_ID = str(config["thread_id"])
    harness.ORACLE_VERSION = str(config["oracle_version"])
    original_load_events = harness.load_events
    harness.load_events = lambda path=replay: original_load_events(path)

    async def run_selector_structural(events_arg):
        selection = selector_module.SelectionResult(
            selected_event_ids=selected_ids,
            input_hash=adapters.compute_source_event_hash(None, events_arg) if hasattr(adapters, "compute_source_event_hash") else selector_module.compute_source_event_hash(None, events_arg),
            output_hash=adapters._hash({"selected_event_ids": list(selected_ids)}),
            model_identity="deterministic-structural-noise-prefilter",
            prompt_version="structural-noise-prefilter-v1",
            generation_settings={},
        )
        info = {
            "enabled": False,
            "bypass": "STRUCTURAL_NOISE_PREFILTER",
            "inference": False,
            "workers": 0,
            "calls": 0,
            "wall_ms": 0,
            "request_wall_ms_sum": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "selected_count": len(selected_ids),
            "selected_ids": list(selected_ids),
            "removed_count": len(frozen_items) - len(selected_items),
            "removed_source_tokens": harness.source_token_count(frozen_items) - harness.source_token_count(selected_items),
            "telemetry": [],
        }
        return selection, list(frozen_items), [], info, None

    harness.run_selector = run_selector_structural
    metadata = {
        "run_id": out.name,
        "phase": "structural_noise_prefilter_generalization_test",
        "trace": trace,
        "condition": "ARM_C_STRUCTURAL_PREFILTER_SELECTOR_PIPELINE",
        "apparatus": "tools/phase3_3_direct_consolidation.py",
        "selector_chunk_target_tokens": SELECTOR_TARGET,
        "selector_worker_count": 0,
        "selector_execution": "benchmark_only_deterministic_structural_prefilter",
        "selector_inference": False,
        "ruleset_sha256": ruleset_sha256,
        "chunk_plan_hash": chunk_plan["chunk_plan_hash"],
        "immutable_source_item_count": len(frozen_items),
        "immutable_source_tokens": harness.source_token_count(frozen_items),
        "selected_source_item_count": len(selected_items),
        "selected_source_tokens": harness.source_token_count(selected_items),
        "removed_item_count": len(frozen_items) - len(selected_items),
        "removed_source_tokens": harness.source_token_count(frozen_items) - harness.source_token_count(selected_items),
        "fixture_replay_sha256": sha256_bytes(replay),
        "oracle_manifest_sha256": sha256_bytes(config["oracle"] / "manifest.json"),
        "oracle_checks_sha256": sha256_bytes(config["oracle"] / "deterministic_checks.jsonl"),
        "raw_plan_sha256": sha256_bytes(config["raw_plan"]),
        "apparatus_sha256": sha256_bytes(ROOT / "tools/phase3_3_direct_consolidation.py"),
        "selector_apparatus_sha256": sha256_bytes(ROOT / "tools/phase3_2_pipeline_ablation.py"),
        "runner_sha256": sha256_bytes(Path(__file__)),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "solar_model": harness.SOLAR_MODEL,
        "solar_endpoint": harness.SOLAR_ENDPOINT,
        "solar_timeout_seconds": harness.SOLAR_TIMEOUT,
        "direct_target_tokens": harness.DIRECT_TARGET,
        "generation": harness.GENERATION,
        "canonicalizer": False,
        "solar_concurrency": False,
        "no_internal_transport_retry": True,
        "tuning_between_runs": False,
    }
    metadata["benchmark_config_hash"] = canonical_digest(metadata)
    write_json(out / "RUN_METADATA.json", metadata)
    try:
        await harness.execute_arm("ARM_B_SELECTOR_SOLAR")
        evaluation = harness.evaluate_arm("ARM_B_SELECTOR_SOLAR")
        write_json(out / "RUN_EVALUATION.json", evaluation)
        print(json.dumps({"run_id": out.name, "status": "COMPLETE", "evaluation": evaluation}, sort_keys=True))
    except Exception as exc:
        write_json(out / "RUN_FAILURE.json", {"run_id": out.name, "status": "FAILED", "error": str(exc), "traceback": traceback.format_exc()})
        raise


def main() -> None:
    args = parse_args()
    ruleset_sha256 = verify_ruleset()
    out = (ROOT / args.out).resolve() if not args.out.is_absolute() else args.out
    if args.audit_only:
        if out.exists():
            raise RuntimeError(f"refusing to overwrite audit root: {out}")
        audit = analyze_trace(args.trace)
        out.mkdir(parents=True)
        write_json(out / "STRUCTURAL_AUDIT.json", {"ruleset_sha256": ruleset_sha256, **audit})
        print(json.dumps({"trace": args.trace, "status": "AUDIT_COMPLETE", **{key: audit[key] for key in ("source_item_count", "source_tokens", "removed_item_count", "removed_source_tokens", "rule_counts")}}, sort_keys=True))
        return
    asyncio.run(run(args.trace, out))


if __name__ == "__main__":
    main()
