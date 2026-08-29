"""Run one benchmark-only PanoRef selector concurrency arm.

The source-item universe and 1.2K chunk plan are frozen before execution.
Only the number of selector worker tasks varies; Solar remains serial and uses
the existing Phase 3.3 control path.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import phase3_2_pipeline_ablation as selector_module  # noqa: E402
import run_panoref_selector_control as control  # noqa: E402

import memory_gateway.pipeline_adapters as adapters  # noqa: E402
from memory_gateway.compaction import compute_source_event_hash  # noqa: E402

SELECTOR_SYSTEM_PROMPT = (
    "You select durable-memory source items. Return JSON only with exactly "
    '{"selected_event_ids":["source-item-id"]}. Select only whole-event or '
    "source-span IDs present in the input; do not summarize or rewrite items."
)


def digest_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_object(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def client_metrics(client: object) -> dict[str, object]:
    telemetry = dict(getattr(client, "last_telemetry", None) or {})
    return {
        "status": telemetry.get("status"),
        "input_tokens": telemetry.get("input_tokens"),
        "output_tokens": telemetry.get("output_tokens"),
        "reasoning_tokens": telemetry.get("reasoning_tokens"),
        "wall_ms": telemetry.get("wall_ms"),
        "ttft_ms": telemetry.get("ttft_ms"),
        "finish_reason": telemetry.get("finish_reason"),
        "error": telemetry.get("error"),
        "error_category": telemetry.get("error_category"),
        "request_profile": telemetry.get("request_profile"),
    }


async def run(target_workers: int, out: Path) -> None:
    if target_workers <= 0:
        raise ValueError("workers must be positive")
    if out.exists():
        raise RuntimeError(f"refusing to overwrite existing arm root: {out}")

    harness = control.harness
    target_tokens = 1_200
    original_expand = adapters.expand_source_items
    events_for_freeze = harness.load_events(control.REPLAY)
    frozen_items = tuple(original_expand(events_for_freeze, selector_budget_tokens=target_tokens, safety_margin=0.8))
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
    harness.THREAD_ID = "phase3-3-panoref-selector-concurrency-sweep"
    harness.ORACLE_VERSION = "panoref-selector-concurrency-sweep-v1"
    original_load_events = harness.load_events
    harness.load_events = lambda path=control.REPLAY: original_load_events(path)
    write_json(out / "SELECTOR_CHUNK_PLAN.json", {**chunk_plan, "chunk_plan_hash": chunk_plan_hash})

    selector_state: dict[str, object] = {"info": None}

    async def run_selector_concurrent(events):
        clients = [
            selector_module.make_client(
                endpoint=selector_module.LOCAL_ENDPOINT,
                model=selector_module.LOCAL_MODEL,
                api_key=None,
                prompt_version="selector-v1",
                system_prompt=SELECTOR_SYSTEM_PROMPT,
                timeout=selector_module.LOCAL_TIMEOUT,
            )
            for _ in range(target_workers)
        ]
        rows: dict[int, dict[str, object]] = {}
        selected_ids: set[str] = set()
        errors: list[tuple[int, str]] = []
        selector_started = time.perf_counter()

        async def worker(worker_index: int) -> None:
            client = clients[worker_index]
            for chunk_index in range(worker_index, len(chunks), target_workers):
                chunk = chunks[chunk_index]
                chunk_ids = [item.id for item in chunk]
                payload = {
                    "events": [adapters._event_payload(item) for item in chunk],
                    "event_ids_in_order": chunk_ids,
                    "source_ids_in_order": chunk_ids,
                }
                error_text: str | None = None
                selected: list[str] = []
                try:
                    adapters._set_call_context(
                        client,
                        stage="selector",
                        source_refs=tuple(chunk_ids),
                        selector_chunk_index=chunk_index,
                    )
                    adapters._check_call_ownership(client)
                    client.response_format = adapters.selector_response_format_for_chunk(chunk_ids)
                    response = await client.complete_json(payload)
                    adapters._check_call_ownership(client)
                    adapters._require_exact_response_keys(response, {"selected_event_ids"}, "selector")
                    selected = response.get("selected_event_ids")
                    if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
                        raise RuntimeError("selector response must contain selected_event_ids as a string array")
                    if not set(selected).issubset(set(chunk_ids)):
                        raise RuntimeError("selector returned ID outside current chunk")
                    selected_ids.update(selected)
                except Exception as exc:
                    adapters._mark_call_failed(client, exc)
                    error_text = str(exc)[:2000]
                    errors.append((chunk_index, error_text))
                finally:
                    record = dict(getattr(client, "last_telemetry", None) or {})
                    record.update({
                        "worker_index": worker_index,
                        "chunk_index": chunk_index,
                        "chunk_event_ids": tuple(item.id for item in chunk),
                        "chunk_parent_event_ids": tuple(
                            item.id if hasattr(item, "sequence") and not hasattr(item, "parent_event_id") else item.parent_event_id
                            for item in chunk
                        ),
                        "chunk_event_count": len(chunk),
                        "chunk_estimated_tokens": sum(
                            selector_module.estimate_tokens(json.dumps(adapters._event_payload(item), ensure_ascii=False))
                            for item in chunk
                        ),
                        "selected_event_ids": list(selected),
                        "error_text": error_text,
                    })
                    rows[chunk_index] = record
                if error_text is not None:
                    return

        await asyncio.gather(*(worker(index) for index in range(target_workers)))
        telemetry = [rows[index] for index in sorted(rows)]
        selector_info = {
            "enabled": True,
            "workers": target_workers,
            "chunk_plan_hash": chunk_plan_hash,
            "calls": len(telemetry),
            "wall_ms": (time.perf_counter() - selector_started) * 1000,
            "request_wall_ms_sum": sum(float(row.get("wall_ms") or 0) for row in telemetry),
            "telemetry": telemetry,
        }
        selector_state["info"] = selector_info
        if errors:
            first_error = sorted(errors)[0][1]
            return None, list(frozen_items), clients, selector_info, first_error
        ordered_selected = tuple(item.id for item in frozen_items if item.id in selected_ids)
        selection = selector_module.SelectionResult(
            selected_event_ids=ordered_selected,
            input_hash=compute_source_event_hash(None, events),
            output_hash=adapters._hash({"selected_event_ids": list(ordered_selected)}),
            model_identity=clients[0].model,
            prompt_version=clients[0].prompt_version,
            generation_settings=dict(clients[0].generation_settings),
        )
        return selection, list(frozen_items), clients, selector_info, None

    harness.run_selector = run_selector_concurrent
    metadata = {
        "run_id": out.name,
        "phase": "selector_concurrency_sweep",
        "trace": "panoref",
        "condition": "ARM_B_SELECTOR_SOLAR",
        "selector_chunk_target_tokens": target_tokens,
        "selector_worker_count": target_workers,
        "selector_execution": "serial" if target_workers == 1 else "parallel",
        "chunk_plan_hash": chunk_plan_hash,
        "immutable_source_item_target_tokens": target_tokens,
        "immutable_source_item_count": len(frozen_items),
        "immutable_source_tokens": harness.source_token_count(frozen_items),
        "immutable_source_item_ids_sha256": digest_object([item.id for item in frozen_items]),
        "benchmark_seam": "benchmark-only runtime selector worker fan-out; frozen phase3_3 source universe",
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
        "selector_model": selector_module.LOCAL_MODEL,
        "selector_endpoint": selector_module.LOCAL_ENDPOINT,
        "selector_timeout_seconds": selector_module.LOCAL_TIMEOUT,
        "solar_model": harness.SOLAR_MODEL,
        "solar_endpoint": harness.SOLAR_ENDPOINT,
        "solar_timeout_seconds": harness.SOLAR_TIMEOUT,
        "direct_target_tokens": harness.DIRECT_TARGET,
        "generation": harness.GENERATION,
        "canonicalizer": False,
        "solar_concurrency": False,
        "no_internal_transport_retry": True,
        "tuning_between_arms": False,
    }
    metadata["benchmark_config_hash"] = digest_object(metadata)
    write_json(out / "RUN_METADATA.json", metadata)

    try:
        await harness.execute_arm("ARM_B_SELECTOR_SOLAR")
        evaluation = harness.evaluate_arm("ARM_B_SELECTOR_SOLAR")
        write_json(out / "RUN_EVALUATION.json", evaluation)
        print(json.dumps({
            "run_id": out.name,
            "status": "COMPLETE",
            "workers": target_workers,
            "semantic_pass": evaluation.get("semantic_pass"),
            "semantic_fail": evaluation.get("semantic_fail"),
        }, sort_keys=True))
    except Exception as exc:
        write_json(out / "RUN_FAILURE.json", {
            "run_id": out.name,
            "status": "FAILED",
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "selector": selector_state.get("info"),
        })
        raise


if __name__ == "__main__":
    args = parse_args()
    output = (ROOT / args.out).resolve() if not args.out.is_absolute() else args.out
    asyncio.run(run(args.workers, output))
