"""Run one benchmark-only PanoRef selector KEEP-100% control arm.

The frozen 1.2K source-item universe and chunk plan are reused from the
selector apparatus.  Qwen is bypassed only for the selector decision: every
legal source item is mechanically marked KEEP, then the existing Phase 3.3
Solar, validation, promotion, and oracle path runs unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import phase3_2_pipeline_ablation as selector_module  # noqa: E402
import run_panoref_selector_control as control  # noqa: E402

import memory_gateway.pipeline_adapters as adapters  # noqa: E402
from memory_gateway.compaction import compute_source_event_hash  # noqa: E402


def digest_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_object(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


async def run(out: Path) -> None:
    if out.exists():
        raise RuntimeError(f"refusing to overwrite existing arm root: {out}")

    harness = control.harness
    target_tokens = 1_200
    original_expand = adapters.expand_source_items
    events_for_freeze = harness.load_events(control.REPLAY)
    frozen_items = tuple(
        original_expand(
            events_for_freeze,
            selector_budget_tokens=target_tokens,
            safety_margin=0.8,
        )
    )
    frozen_ids = tuple(item.id for item in frozen_items)
    if len(frozen_ids) != len(set(frozen_ids)):
        raise RuntimeError("frozen source-item universe contains duplicate IDs")
    chunks = adapters._chunk_events(list(frozen_items), target_tokens=target_tokens)
    chunk_plan = {
        "target_tokens": target_tokens,
        "source_item_count": len(frozen_items),
        "source_tokens": harness.source_token_count(frozen_items),
        "chunks": [[item.id for item in chunk] for chunk in chunks],
    }
    chunk_plan_hash = digest_object(chunk_plan)

    def frozen_expand(events, *, selector_budget_tokens, safety_margin):
        return frozen_items

    # Freeze both selector bindings. Phase 3.3's own source_items helper stays
    # at its imported 1.2K target for frozen raw-plan validation.
    selector_module.SELECTOR_TARGET = target_tokens
    selector_module.expand_source_items = frozen_expand
    adapters.expand_source_items = frozen_expand

    control.OUT = out
    control.prepare_control_root()
    harness.REPLAY = control.REPLAY
    harness.OUT = out
    harness.THREAD_ID = "phase3-3-panoref-selector-keep-100"
    harness.ORACLE_VERSION = "panoref-selector-keep-100-v1"
    original_load_events = harness.load_events
    harness.load_events = lambda path=control.REPLAY: original_load_events(path)
    write_json(out / "SELECTOR_CHUNK_PLAN.json", {**chunk_plan, "chunk_plan_hash": chunk_plan_hash})

    selector_state: dict[str, object] = {"info": None}

    async def run_selector_keep100(events):
        # This is the hypothesis under test. No selector client is created and
        # no selector inference or retry path is entered.
        selected_ids = tuple(item.id for item in frozen_items)
        selection = selector_module.SelectionResult(
            selected_event_ids=selected_ids,
            input_hash=compute_source_event_hash(None, events),
            output_hash=adapters._hash({"selected_event_ids": list(selected_ids)}),
            model_identity="mechanical-keep-100",
            prompt_version="selector-bypass-keep-100-v1",
            generation_settings={},
        )
        selector_info = {
            "enabled": False,
            "bypass": "KEEP_100_PERCENT",
            "inference": False,
            "workers": 0,
            "chunk_plan_hash": chunk_plan_hash,
            "calls": 0,
            "wall_ms": 0,
            "request_wall_ms_sum": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "selected_count": len(selected_ids),
            "selected_ids": list(selected_ids),
            "telemetry": [],
        }
        selector_state["info"] = selector_info
        return selection, list(frozen_items), [], selector_info, None

    harness.run_selector = run_selector_keep100
    metadata = {
        "run_id": out.name,
        "phase": "selector_value_isolation_keep_100",
        "trace": "panoref",
        "condition": "ARM_B_SELECTOR_SOLAR",
        "selector_chunk_target_tokens": target_tokens,
        "selector_worker_count": 0,
        "selector_execution": "bypass_keep_100",
        "selector_bypass": "KEEP_100_PERCENT",
        "selector_inference": False,
        "selector_decision": "mechanically_keep_all_legal_source_items",
        "selector_calls": 0,
        "chunk_plan_hash": chunk_plan_hash,
        "immutable_source_item_target_tokens": target_tokens,
        "immutable_source_item_count": len(frozen_items),
        "immutable_source_tokens": harness.source_token_count(frozen_items),
        "immutable_source_item_ids_sha256": digest_object(list(frozen_ids)),
        "benchmark_seam": "benchmark-only mechanical selector decision bypass; frozen phase3_3 source universe",
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "fixture_replay_sha256": digest_bytes(control.REPLAY),
        "oracle_manifest_sha256": digest_bytes(control.ORACLE / "manifest.json"),
        "oracle_checks_sha256": digest_bytes(control.ORACLE / "deterministic_checks.jsonl"),
        "raw_plan_sha256": digest_bytes(control.RAW_PLAN),
        "apparatus_sha256": digest_bytes(ROOT / "tools/phase3_3_direct_consolidation.py"),
        "selector_apparatus_sha256": digest_bytes(ROOT / "tools/phase3_2_pipeline_ablation.py"),
        "control_adapter_sha256": digest_bytes(ROOT / "tools/run_panoref_selector_control.py"),
        "pipeline_adapter_sha256": digest_bytes(ROOT / "memory_gateway/pipeline_adapters.py"),
        "runner_sha256": digest_bytes(Path(__file__)),
        "selector_model": None,
        "selector_endpoint": None,
        "selector_timeout_seconds": None,
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
    metadata["benchmark_config_hash"] = digest_object(metadata)
    write_json(out / "RUN_METADATA.json", metadata)

    try:
        await harness.execute_arm("ARM_B_SELECTOR_SOLAR")
        evaluation = harness.evaluate_arm("ARM_B_SELECTOR_SOLAR")
        write_json(out / "RUN_EVALUATION.json", evaluation)
        print(
            json.dumps(
                {
                    "run_id": out.name,
                    "status": "COMPLETE",
                    "selector_calls": 0,
                    "semantic_pass": evaluation.get("semantic_pass"),
                    "semantic_fail": evaluation.get("semantic_fail"),
                },
                sort_keys=True,
            )
        )
    except Exception as exc:
        write_json(
            out / "RUN_FAILURE.json",
            {
                "run_id": out.name,
                "status": "FAILED",
                "selector_calls": 0,
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "selector": selector_state.get("info"),
            },
        )
        raise


if __name__ == "__main__":
    args = parse_args()
    output = (ROOT / args.out).resolve() if not args.out.is_absolute() else args.out
    asyncio.run(run(output))
