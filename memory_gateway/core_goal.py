"""Sealed, unpaid readiness contract for ORCHID's core product goal.

The contract is intentionally smaller than the Pareto release contract. It
freezes the bounded foreground target, lossless replay, selector behavior, and
zero-wait intent before cost, cache, or hardware optimization begins.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from pathlib import Path
from typing import Any

from .canonicalizer_gate import KEEP, gate_from_semantic_eval
from .config import RuntimeConfig
from .frozen_trace_economics import (
    DEFAULT_FREETOSHOP_REPLAY,
    replay_freetoshop_frozen_trace,
)
from .selector_contract import (
    GOLDEN_SAMPLE_PATH,
    contract_document,
    contract_sha256,
    verify_golden_hashes,
)

SCHEMA_VERSION = "orchid.core_goal_manifest.v1"
STATUS_SCHEMA_VERSION = "orchid.core_goal_status.v1"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = ROOT / "artifacts/core_goal/CORE_GOAL_MANIFEST.json"
CANONICALIZER_EVIDENCE_PATH = (
    ROOT
    / "artifacts/agent_benchmarks/freetoshop_direct_consolidation"
    / "semantic_eval/SUMMARY.json"
)

FOREGROUND_TARGET_TOKENS = 32_000
FOREGROUND_EMERGENCY_CEILING_TOKENS = 48_000
MINIMUM_HISTORY_MULTIPLE = 5
MAX_COMPACTION_WAIT_MS = 0
MINIMUM_BACKGROUND_SERVICE_RATIO = 1.5
CORE_CANONICALIZER_ENABLED = True

EXPECTED_REPLAY_EVENTS = 199
EXPECTED_PROJECTED_SOURCE_ITEMS = 216
EXPECTED_SELECTOR_CHUNKS = 63


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def core_goal_manifest_body(root: Path = ROOT) -> dict[str, Any]:
    replay_path = root / _relative(DEFAULT_FREETOSHOP_REPLAY, ROOT)
    selector_golden_path = root / _relative(GOLDEN_SAMPLE_PATH, ROOT)
    canonicalizer_evidence = root / _relative(CANONICALIZER_EVIDENCE_PATH, ROOT)
    return {
        "schema_version": SCHEMA_VERSION,
        "goal": (
            "bounded main-model context backed by lossless long-term memory, "
            "continuous secondary-GPU organization, retrieval, and zero "
            "foreground compaction waits"
        ),
        "context": {
            "foreground_target_tokens": FOREGROUND_TARGET_TOKENS,
            "foreground_emergency_ceiling_tokens": (
                FOREGROUND_EMERGENCY_CEILING_TOKENS
            ),
            "minimum_history_multiple": MINIMUM_HISTORY_MULTIPLE,
        },
        "foreground_slo": {
            "maximum_compaction_wait_ms": MAX_COMPACTION_WAIT_MS,
            "background_only_memory_maintenance": True,
        },
        "service_envelope": {
            "minimum_background_to_arrival_ratio": (
                MINIMUM_BACKGROUND_SERVICE_RATIO
            ),
            "headroom_duration_percentile": "p99",
        },
        "storage": {
            "append_only_raw_journal": True,
            "raw_history_authoritative": True,
            "memory_views_rebuildable": True,
        },
        "selector": {
            "frozen": True,
            "role": "organizer",
            "contract_sha256": contract_sha256(),
            "contract": contract_document(),
            "golden_requests_path": _relative(selector_golden_path, root),
            "golden_requests_sha256": file_sha256(selector_golden_path),
        },
        "canonicalizer": {
            "enabled": CORE_CANONICALIZER_ENABLED,
            "frozen_gate_verdict": KEEP,
            "evidence_path": _relative(canonicalizer_evidence, root),
            "evidence_sha256": file_sha256(canonicalizer_evidence),
        },
        "frozen_replay": {
            "path": _relative(replay_path, root),
            "sha256": file_sha256(replay_path),
            "expected_raw_events": EXPECTED_REPLAY_EVENTS,
            "expected_projected_source_items": EXPECTED_PROJECTED_SOURCE_ITEMS,
            "expected_selector_chunks": EXPECTED_SELECTOR_CHUNKS,
            "raw_event_admission": "exactly_once",
            "source_item_delivery": "exactly_once",
        },
        "deployment": {
            "main_model_device": "primary_gpu",
            "selector_device": "secondary_gpu",
            "selector_related_work_serial": True,
        },
    }


def core_goal_manifest_document(root: Path = ROOT) -> dict[str, Any]:
    body = core_goal_manifest_body(root)
    return {**body, "manifest_sha256": digest(body)}


def write_core_goal_manifest(path: Path, *, root: Path = ROOT) -> dict[str, Any]:
    document = core_goal_manifest_document(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return document


def verify_core_goal_manifest(
    path: Path = DEFAULT_MANIFEST_PATH,
    *,
    root: Path = ROOT,
) -> dict[str, Any]:
    if not path.exists():
        return {
            "ok": False,
            "checks": {"present": False, "self_hash": False, "sealed": False},
            "reasons": [f"missing core-goal manifest: {path}"],
        }
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {
            "ok": False,
            "checks": {"present": True, "self_hash": False, "sealed": False},
            "reasons": [f"invalid core-goal manifest: {error}"],
        }
    if not isinstance(document, dict):
        return {
            "ok": False,
            "checks": {"present": True, "self_hash": False, "sealed": False},
            "reasons": ["core-goal manifest must be a JSON object"],
        }
    body = {key: value for key, value in document.items() if key != "manifest_sha256"}
    self_hash_ok = document.get("manifest_sha256") == digest(body)
    expected = core_goal_manifest_document(root)
    sealed = document == expected
    reasons: list[str] = []
    if not self_hash_ok:
        reasons.append("core-goal manifest self-hash mismatch")
    if not sealed:
        reasons.append("core-goal manifest differs from the current sealed contract")
    checks = {"present": True, "self_hash": self_hash_ok, "sealed": sealed}
    return {"ok": all(checks.values()), "checks": checks, "reasons": reasons}


def _runtime_config_default(name: str) -> Any:
    field = next(
        (
            item
            for item in fields(RuntimeConfig)
            if item.name == name
        ),
        None,
    )
    if field is None:
        return None
    return field.default


def core_goal_status(
    root: Path = ROOT,
    *,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    path = manifest_path or root / "artifacts/core_goal/CORE_GOAL_MANIFEST.json"
    manifest = verify_core_goal_manifest(path, root=root)
    selector = verify_golden_hashes()
    replay_path = root / _relative(DEFAULT_FREETOSHOP_REPLAY, ROOT)
    replay = replay_freetoshop_frozen_trace(replay_path)
    exact_once = replay["organizer_exact_once"]
    canonicalizer_path = root / _relative(CANONICALIZER_EVIDENCE_PATH, ROOT)
    canonicalizer = gate_from_semantic_eval(canonicalizer_path)
    replay_hash = file_sha256(replay_path)
    expected_manifest = core_goal_manifest_document(root)
    expected_replay = expected_manifest["frozen_replay"]
    checks = {
        "manifest_sealed": bool(manifest["ok"]),
        "selector_contract_frozen": bool(selector["ok"]),
        "frozen_replay_hash": replay_hash == expected_replay["sha256"],
        "frozen_replay_event_count": (
            replay["events"] == expected_replay["expected_raw_events"]
        ),
        "organizer_exact_once": bool(exact_once["exact_once"]),
        "projected_source_item_count": (
            exact_once["source_items"]
            == expected_replay["expected_projected_source_items"]
        ),
        "selector_chunk_count": (
            exact_once["selector_chunks"]
            == expected_replay["expected_selector_chunks"]
        ),
        "canonicalizer_gate_keep": canonicalizer.verdict == KEEP,
        "canonicalizer_runtime_default_enabled": (
            _runtime_config_default("canonicalizer_enabled")
            is CORE_CANONICALIZER_ENABLED
        ),
        "foreground_target_runtime_default": (
            _runtime_config_default("foreground_target_tokens")
            == FOREGROUND_TARGET_TOKENS
        ),
        "foreground_ceiling_runtime_default": (
            _runtime_config_default("foreground_hard_limit_tokens")
            == FOREGROUND_EMERGENCY_CEILING_TOKENS
        ),
    }
    blockers = [key for key, ok in checks.items() if not ok]
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "core_contract_ready": not blockers,
        "paid_benchmark_ready": False,
        "manifest": manifest,
        "checks": checks,
        "blockers": blockers,
        "organizer": {
            "raw_events": exact_once["raw_events"],
            "source_items": exact_once["source_items"],
            "selector_chunks": exact_once["selector_chunks"],
            "exact_once": exact_once["exact_once"],
        },
        "selector_contract_sha256": contract_sha256(),
        "frozen_replay_sha256": replay_hash,
        "canonicalizer_verdict": canonicalizer.verdict,
        "next_unpaid_steps": [
            "make accepted stable-record envelopes the primary runtime path",
            "prove byte-identical full-pipeline recovery at every crash boundary",
            "repeat the zero-wait proof under live two-GPU failure injection",
            "pass frozen semantic recall and hidden-quality gates",
            "establish the measured secondary-GPU service envelope",
        ],
    }
