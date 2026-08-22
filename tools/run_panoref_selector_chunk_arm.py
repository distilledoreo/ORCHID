"""Run one serial PanoRef selector chunk-size arm.

This benchmark-only wrapper reuses the preserved Phase 3.3 control path.  The
1.2K source-item universe is frozen at runtime in both selector module bindings
so the only varied input is selector chunk boundaries.
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
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import memory_gateway.pipeline_adapters as adapters  # noqa: E402
import phase3_2_pipeline_ablation as selector_module  # noqa: E402
import run_panoref_selector_control as control  # noqa: E402


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
    parser.add_argument("--target", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


async def run(target: int, out: Path) -> None:
    if out.exists():
        raise RuntimeError(f"refusing to overwrite existing arm root: {out}")

    harness = control.harness
    baseline_target = int(selector_module.SELECTOR_TARGET)
    original_expand = adapters.expand_source_items
    events_for_freeze = harness.load_events(control.REPLAY)
    frozen_items = tuple(original_expand(events_for_freeze, selector_budget_tokens=baseline_target, safety_margin=0.8))

    def frozen_expand(events, *, selector_budget_tokens, safety_margin):
        return frozen_items

    # Freeze both the selector's request universe and the source-item list
    # returned alongside its result.  Phase 3.3's own source_items helper is
    # left at its imported 1.2K target for frozen raw-plan validation.
    selector_module.SELECTOR_TARGET = target
    selector_module.expand_source_items = frozen_expand
    adapters.expand_source_items = frozen_expand

    control.OUT = out
    control.prepare_control_root()
    harness.REPLAY = control.REPLAY
    harness.OUT = out
    harness.THREAD_ID = "phase3-3-panoref-selector-chunk-sweep"
    harness.ORACLE_VERSION = "panoref-selector-chunk-sweep-v1"
    original_load_events = harness.load_events
    harness.load_events = lambda path=control.REPLAY: original_load_events(path)

    metadata = {
        "run_id": out.name,
        "phase": "selector_chunk_size_sweep",
        "trace": "panoref",
        "condition": "ARM_B_SELECTOR_SOLAR",
        "selector_chunk_target_tokens": target,
        "baseline_selector_chunk_target_tokens": baseline_target,
        "immutable_source_item_target_tokens": baseline_target,
        "immutable_source_item_count": len(frozen_items),
        "immutable_source_tokens": harness.source_token_count(frozen_items),
        "immutable_source_item_ids_sha256": digest_object([item.id for item in frozen_items]),
        "benchmark_seam": "runtime-freeze-phase3_2.expand_source_items-and-memory_gateway.pipeline_adapters.expand_source_items",
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
        "selector_override_module": "tools/phase3_2_pipeline_ablation.py:SELECTOR_TARGET",
        "selector_model": selector_module.LOCAL_MODEL,
        "selector_endpoint": selector_module.LOCAL_ENDPOINT,
        "selector_timeout_seconds": selector_module.LOCAL_TIMEOUT,
        "solar_model": harness.SOLAR_MODEL,
        "solar_endpoint": harness.SOLAR_ENDPOINT,
        "solar_timeout_seconds": harness.SOLAR_TIMEOUT,
        "direct_target_tokens": harness.DIRECT_TARGET,
        "generation": harness.GENERATION,
        "canonicalizer": False,
        "concurrency": False,
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
            "selector_chunk_target_tokens": target,
            "semantic_pass": evaluation.get("semantic_pass"),
            "semantic_fail": evaluation.get("semantic_fail"),
        }, sort_keys=True))
    except Exception as exc:
        write_json(out / "RUN_FAILURE.json", {
            "run_id": out.name,
            "status": "FAILED",
            "error": str(exc),
            "traceback": traceback.format_exc(),
        })
        raise


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run(args.target, (ROOT / args.out).resolve() if not args.out.is_absolute() else args.out))
