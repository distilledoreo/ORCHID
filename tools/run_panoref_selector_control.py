"""Run the preserved Phase 3.3 selector control on the frozen PanoRef slice.

This adapter creates a fresh control root from immutable PanoRef replay/oracle
inputs, then delegates candidate execution and evaluation to the existing
Phase 3.3 selector arm. It does not alter the frozen fixture or production
ORCHID code, and it refuses to overwrite a prior control result.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import phase3_3_direct_consolidation as harness  # noqa: E402


FIXTURE = ROOT / "artifacts/agent_benchmarks/panoref_direct_generalization_bounded_v2"
REPLAY = FIXTURE / "frozen_panoref_replay/events.jsonl"
ORACLE = FIXTURE / "semantic_oracle"
RAW_PLAN = FIXTURE / "arm_direct_raw_solar/batch_plan.json"
OUT = ROOT / "artifacts/agent_benchmarks/panoref_selector_control_v2"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def prepare_control_root() -> None:
    if OUT.exists():
        raise RuntimeError(f"refusing to overwrite existing selector control root: {OUT}")
    required = (REPLAY, ORACLE / "manifest.json", ORACLE / "deterministic_checks.jsonl", RAW_PLAN)
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("missing frozen PanoRef inputs: " + ", ".join(missing))

    (OUT / "semantic_oracle").mkdir(parents=True)
    (OUT / "arm_c_raw_solar").mkdir(parents=True)
    for name in ("manifest.json", "deterministic_checks.jsonl", "checkpoints.jsonl", "JUDGE_RUBRIC.md"):
        source = ORACLE / name
        if source.exists():
            shutil.copy2(source, OUT / "semantic_oracle" / name)
    shutil.copy2(RAW_PLAN, OUT / "arm_c_raw_solar/batch_plan.json")
    (OUT / "FREEZE_COMPLETE").write_text("panoref-direct-generalization-bounded-v1\n", encoding="utf-8")
    write_json(OUT / "CONTROL_METADATA.json", {
        "control": "PanoRef selector -> Solar direct consolidation",
        "apparatus": "tools/phase3_3_direct_consolidation.py",
        "fixture_root": str(FIXTURE),
        "replay": str(REPLAY),
        "replay_sha256": sha256(REPLAY),
        "oracle_manifest_sha256": sha256(ORACLE / "manifest.json"),
        "oracle_checks_sha256": sha256(ORACLE / "deterministic_checks.jsonl"),
        "raw_plan_sha256": sha256(RAW_PLAN),
        "no_internal_transport_retry": True,
        "fresh_output_root": True,
    })


async def run() -> dict[str, object]:
    prepare_control_root()
    harness.REPLAY = REPLAY
    harness.OUT = OUT
    harness.THREAD_ID = "phase3-3-panoref-selector-control"
    harness.ORACLE_VERSION = "panoref-direct-generalization-bounded-v1"
    # Phase 3.3's helper has a default argument bound to its original
    # FreetoShop replay at import time. Rebind that one read-only loader so
    # every existing arm/evaluator call consumes the PanoRef replay.
    original_load_events = harness.load_events
    harness.load_events = lambda path=REPLAY: original_load_events(path)
    await harness.execute_arm("ARM_B_SELECTOR_SOLAR")
    return harness.evaluate_arm("ARM_B_SELECTOR_SOLAR")


if __name__ == "__main__":
    result = asyncio.run(run())
    print(json.dumps(result, indent=2, sort_keys=True))
