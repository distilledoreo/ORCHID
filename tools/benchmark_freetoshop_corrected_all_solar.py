"""Freeze, verify, and monitor the corrected FreetoShop Pi-vs-ORCHID A/B.

This is benchmark-only apparatus.  It preserves the historical FreetoShop
start state while observing a fresh ORCHID database.  It never retries or
repairs either benchmark arm.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from memory_gateway.compaction import Event, estimate_tokens, expand_source_items, source_item_payload


ROOT = SCRIPT_ROOT
ARTIFACT = Path(
    os.environ.get(
        "ORCHID_CORRECTED_ARTIFACT",
        str(
            ROOT
            / "artifacts/agent_benchmarks/freetoshop_pi_vs_orchid_corrected_all_solar"
        ),
    )
)
RUN_ID = os.environ.get("ORCHID_CORRECTED_RUN_ID", ARTIFACT.name)
STAGE = Path(
    os.environ.get(
        "ORCHID_CORRECTED_STAGE",
        (
            rf"C:\Users\disti\benchmark_work\{RUN_ID}"
            if os.name == "nt"
            else str(ROOT.parent / "benchmark_work" / RUN_ID)
        ),
    )
)
HISTORICAL_ARTIFACT = (
    ROOT / "artifacts/agent_benchmarks/freetoshop_pi_vs_orchid_20260820"
)
HISTORICAL_STAGE = Path(
    os.environ.get(
        "FREETOSHOP_HISTORICAL_STAGE",
        r"C:\Users\disti\benchmark_work\freetoshop_pi_vs_orchid_20260820",
    )
)
ORACLE_ROOT = Path(
    os.environ.get(
        "FREETOSHOP_ORACLE_ROOT",
        r"C:\Users\disti\Documents\App Development\FreetoShop",
    )
)
DB_PATH = STAGE / "orchid-state" / "memory.db"
THREAD_ID = "orchid-arm-b"
PROVIDER_BASE_URL = os.environ.get(
    "BENCHMARK_PROVIDER_BASE_URL", "https://openrouter.ai/api/v1"
).rstrip("/")
MODEL_ID = os.environ.get("BENCHMARK_MODEL_ID", "upstage/solar-pro4")
PROVIDER_CONTEXT_TOKENS = int(os.environ.get("BENCHMARK_CONTEXT_TOKENS", "524288"))
RESERVED_OUTPUT_TOKENS = 8_192
FIXED_OVERHEAD_TOKENS = 4_096
SAFETY_MARGIN = 0.7
REQUEST_AMPLIFICATION = 3.0
if PROVIDER_CONTEXT_TOKENS <= RESERVED_OUTPUT_TOKENS + FIXED_OVERHEAD_TOKENS:
    raise ValueError(
        "BENCHMARK_CONTEXT_TOKENS must exceed reserved output plus fixed overhead"
    )
SOURCE_WINDOW_TOKENS = int(
    max(1, PROVIDER_CONTEXT_TOKENS - RESERVED_OUTPUT_TOKENS - FIXED_OVERHEAD_TOKENS)
    * SAFETY_MARGIN
    / REQUEST_AMPLIFICATION
)
CORRECTED_BASE_COMMIT = "98f0b0e5017db5d746257e40668e71ce777925e6"
PI_EXECUTABLE = os.environ.get("PI_EXECUTABLE", "pi.cmd" if os.name == "nt" else "pi")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_git(cwd: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", *arguments], cwd=cwd, text=True, stderr=subprocess.STDOUT
    ).strip()


def git_snapshot(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "head": run_git(path, "rev-parse", "HEAD"),
        "tree": run_git(path, "rev-parse", "HEAD^{tree}"),
        "status": run_git(path, "status", "--short"),
    }


def _user_api_key_available() -> bool:
    if os.environ.get("BENCHMARK_PROVIDER_API_KEY") or os.environ.get("OPENROUTER_API_KEY"):
        return True
    if os.name != "nt":
        return False
    command = (
        "[Environment]::GetEnvironmentVariable('OPENROUTER_API_KEY','User')"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(completed.stdout.strip())


def freeze() -> None:
    if ARTIFACT.exists():
        raise SystemExit(f"refusing to overwrite benchmark artifact: {ARTIFACT}")
    if STAGE.exists():
        raise SystemExit(f"refusing to reuse benchmark staging root: {STAGE}")
    tracked_status = run_git(ROOT, "status", "--short", "--untracked-files=no")
    if tracked_status:
        raise SystemExit("tracked implementation changes must be committed before freeze")

    historical_manifest_path = HISTORICAL_ARTIFACT / "BENCHMARK_MANIFEST.json"
    goal_path = HISTORICAL_ARTIFACT / "BENCHMARK_GOAL.md"
    if not historical_manifest_path.exists() or not goal_path.exists():
        raise SystemExit("historical sealed FreetoShop manifest/goal is unavailable")
    historical = load_json(historical_manifest_path)
    expected_start_tree = historical["canonical_source_fingerprint"]["start_tree"]
    input_snapshots = {
        "PI_NATIVE": git_snapshot(HISTORICAL_STAGE / "arm-a-pi"),
        "ORCHID": git_snapshot(HISTORICAL_STAGE / "arm-b-orchid"),
    }
    for name, snapshot in input_snapshots.items():
        if snapshot["tree"] != expected_start_tree or snapshot["status"]:
            raise SystemExit(
                f"{name} historical input is not the clean sealed source tree"
            )
    oracle_head = run_git(ORACLE_ROOT, "rev-parse", "HEAD")
    oracle_tree = run_git(ORACLE_ROOT, "rev-parse", "HEAD^{tree}")
    oracle_source = ORACLE_ROOT / "tests/e2e/editor.spec.ts"
    retained_hidden = load_json(
        ROOT
        / "artifacts/agent_benchmarks/freetoshop_pi_vs_orchid_corrected_all_solar/quality/hidden_eval.json"
    )
    if not oracle_source.exists() or sha256_file(oracle_source) != retained_hidden["source_sha256"]:
        raise SystemExit("hidden evaluator source does not match the retained sealed hash")
    recovery_path = HISTORICAL_STAGE / "RECOVERY_MANIFEST.json"
    recovery = load_json(recovery_path) if recovery_path.exists() else None
    if oracle_head != historical["target_commit"] and not recovery:
        raise SystemExit(
            "hidden evaluator is reconstructed but no recovery manifest is present"
        )

    for relative in (
        "pi_native",
        "orchid",
        "memory_transitions",
        "quality",
        "economics",
        "checkpoints",
        "gateway",
        "controller",
        "prelaunch",
    ):
        (ARTIFACT / relative).mkdir(parents=True, exist_ok=True)
    goal_bytes = goal_path.read_bytes()
    (ARTIFACT / "BENCHMARK_GOAL.md").write_bytes(goal_bytes)

    pi_config = {
        "arm": "PI_NATIVE",
        "foreground_provider": PROVIDER_BASE_URL,
        "foreground_model": MODEL_ID,
        "native_compaction": True,
        "pi_version": "0.84.2",
        "thinking": "medium",
        "retry_policy": "sealed Pi policy; controller performs no retry or rescue",
    }
    orchid_config = {
        "arm": "ORCHID",
        "foreground_provider": PROVIDER_BASE_URL,
        "foreground_model": MODEL_ID,
        "pipeline": [
            "bounded_immutable_source_window",
            "solar_selector",
            "software_owned_semantic_packet",
            "solar_consolidator",
            "validation",
            "active_cas_promotion",
        ],
        "selector_model": MODEL_ID,
        "consolidator_model": MODEL_ID,
        "canonicalizer_enabled": False,
        "source_window_tokens": SOURCE_WINDOW_TOKENS,
        "provider_context_tokens": PROVIDER_CONTEXT_TOKENS,
        "reserved_output_tokens": RESERVED_OUTPUT_TOKENS,
        "fixed_overhead_tokens": FIXED_OVERHEAD_TOKENS,
        "safety_margin": SAFETY_MARGIN,
        "request_amplification": REQUEST_AMPLIFICATION,
        "selector_chunk_target_tokens": 1_200,
        "temperature": 0,
        "top_p": 1,
        "reasoning_effort": "none",
        "structured_output": True,
        "model_timeout_seconds": 120,
        "cold_memory_mode": "off",
        "retry_policy": "existing model-client policy; controller performs no retry or rescue",
        "pricing_usd_per_million_tokens": {
            "input": float(os.environ.get("BENCHMARK_INPUT_USD_PER_MILLION", "0")),
            "output": float(os.environ.get("BENCHMARK_OUTPUT_USD_PER_MILLION", "0")),
        },
    }
    write_json(ARTIFACT / "CONFIG_PI_NATIVE.json", pi_config)
    write_json(ARTIFACT / "CONFIG_ORCHID.json", orchid_config)

    implementation_head = run_git(ROOT, "rev-parse", "HEAD")
    implementation_tree = run_git(ROOT, "rev-parse", "HEAD^{tree}")
    implementation_files = [
        "memory_gateway/compaction.py",
        "memory_gateway/config.py",
        "memory_gateway/context.py",
        "memory_gateway/foreground_telemetry.py",
        "memory_gateway/gateway.py",
        "memory_gateway/pipeline.py",
        "memory_gateway/pipeline_adapters.py",
        "memory_gateway/scheduler.py",
        "tools/benchmark_freetoshop_corrected_all_solar.py",
        "tools/freetoshop_corrected_all_solar_driver.mjs",
        "tools/pi_continuation_policy.mjs",
        "tools/recover_freetoshop_benchmark_inputs.py",
        "tools/start_freetoshop_corrected_all_solar.ps1",
        "tools/start_freetoshop_corrected_all_solar.sh",
    ]
    implementation_hashes = {
        relative: sha256_file(ROOT / relative) for relative in implementation_files
    }
    manifest = {
        "benchmark": "Corrected FreetoShop Pi-vs-ORCHID All-Solar A/B",
        "run_id": RUN_ID,
        "status": "FROZEN",
        "frozen_at": now(),
        "design": "sealed contemporaneous paired run, n=1",
        "corrected_base_commit": CORRECTED_BASE_COMMIT,
        "implementation_commit": implementation_head,
        "implementation_tree": implementation_tree,
        "implementation_file_sha256": implementation_hashes,
        "historical_source_manifest": str(historical_manifest_path),
        "historical_source_manifest_sha256": sha256_file(historical_manifest_path),
        "start_commit": input_snapshots["PI_NATIVE"]["head"],
        "start_tree": expected_start_tree,
        "original_start_commit": historical["start_commit"],
        "target_commit_controller_private": oracle_head,
        "target_tree_controller_private": oracle_tree,
        "original_target_commit_controller_private": historical["target_commit"],
        "original_target_tree_controller_private": historical["canonical_source_fingerprint"]["target_tree"],
        "input_recovery": recovery,
        "hidden_evaluator_sha256": retained_hidden["source_sha256"],
        "public_goal": {
            "path": "BENCHMARK_GOAL.md",
            "sha256": hashlib.sha256(goal_bytes).hexdigest(),
            "byte_identical_between_arms": True,
        },
        "sealed_arms": {
            "PI_NATIVE": {
                "baseline_commit": input_snapshots["PI_NATIVE"]["head"],
                "original_baseline_commit": historical["sealed_arms"]["arm_a_pi"]["baseline_commit"],
                "tree": input_snapshots["PI_NATIVE"]["tree"],
                "model": f"benchmark/{MODEL_ID}",
                "native_compaction": True,
            },
            "ORCHID": {
                "baseline_commit": input_snapshots["ORCHID"]["head"],
                "original_baseline_commit": historical["sealed_arms"]["arm_b_orchid"]["baseline_commit"],
                "tree": input_snapshots["ORCHID"]["tree"],
                "model": f"orchid/{MODEL_ID}",
                "native_compaction": False,
                "selector_model": MODEL_ID,
                "consolidator_model": MODEL_ID,
                "canonicalizer_enabled": False,
            },
        },
        "early_gate": {
            "required_clean_promotions": 3,
            "failure_action": "stop both arms and preserve evidence",
        },
        "run_policy": {
            "max_wall_clock_ms_per_arm": 21_600_000,
            "checkpoint_interval_ms": 900_000,
            "launch": "paired",
            "manual_rescue": "forbidden",
            "controller_restart_limit": 0,
            "tuning_after_launch": "forbidden",
            "provider_preflight_calls": 0,
        },
        "predeclared_stop_conditions": [
            "ORCHID fails the 3-promotion gate",
            "unrecoverable continuity stall",
            "repository corruption",
            "benchmark leakage",
            "asymmetric provider outage or throttling",
            "unrecoverable provider failure",
            "benchmark process exits unexpectedly",
        ],
        "stage": str(STAGE),
        "oracle_root_controller_private": str(ORACLE_ROOT),
    }
    write_json(ARTIFACT / "BENCHMARK_MANIFEST.json", manifest)
    write_json(
        ARTIFACT / "CONFIG_HASHES.json",
        {
            "benchmark_goal_sha256": sha256_file(ARTIFACT / "BENCHMARK_GOAL.md"),
            "benchmark_manifest_sha256": sha256_file(
                ARTIFACT / "BENCHMARK_MANIFEST.json"
            ),
            "config_pi_native_sha256": sha256_file(
                ARTIFACT / "CONFIG_PI_NATIVE.json"
            ),
            "config_orchid_sha256": sha256_file(ARTIFACT / "CONFIG_ORCHID.json"),
            "implementation_commit": implementation_head,
            "implementation_tree": implementation_tree,
            "implementation_file_sha256": implementation_hashes,
        },
    )
    for path in (
        ARTIFACT / "memory_transitions/pi_compactions.jsonl",
        ARTIFACT / "memory_transitions/orchid_promotions.jsonl",
        ARTIFACT / "memory_transitions/orchid_runtime.jsonl",
        ARTIFACT / "quality/milestone_scores.jsonl",
    ):
        path.write_text("", encoding="utf-8")
    print(json.dumps({"artifact": str(ARTIFACT), "stage": str(STAGE)}, indent=2))


def configure_pi_agent_models(path: Path, *, orchid_proxy: bool) -> None:
    """Bind a copied sealed Pi agent config to the selected benchmark provider."""

    models_path = path / "models.json"
    payload = load_json(models_path)
    existing_providers = payload.get("providers") or {}
    if not existing_providers:
        raise SystemExit(f"Pi model configuration has no provider: {models_path}")
    existing_provider = next(iter(existing_providers.values()))
    existing_models = existing_provider.get("models") or []
    if not existing_models:
        raise SystemExit(f"Pi model configuration has no model: {models_path}")
    provider_id = "orchid" if orchid_proxy else "benchmark"
    provider = {
        **existing_provider,
        "name": "ORCHID local proxy" if orchid_proxy else "Sealed benchmark provider",
        "baseUrl": "http://127.0.0.1:7332/v1" if orchid_proxy else PROVIDER_BASE_URL,
        "apiKey": "$BENCHMARK_PROVIDER_API_KEY",
        "models": [
            {
                **existing_models[0],
                "id": MODEL_ID,
                "name": MODEL_ID,
                "contextWindow": PROVIDER_CONTEXT_TOKENS,
                "maxTokens": RESERVED_OUTPUT_TOKENS,
                "cost": {
                    "input": float(os.environ.get("BENCHMARK_INPUT_USD_PER_MILLION", "0")),
                    "output": float(os.environ.get("BENCHMARK_OUTPUT_USD_PER_MILLION", "0")),
                    "cacheRead": 0,
                    "cacheWrite": 0,
                },
            }
        ],
    }
    payload["providers"] = {provider_id: provider}
    write_json(models_path, payload)


def prepare() -> None:
    manifest = load_json(ARTIFACT / "BENCHMARK_MANIFEST.json")
    if STAGE.exists():
        raise SystemExit(f"refusing to reuse benchmark staging root: {STAGE}")
    STAGE.mkdir(parents=True)
    sources = {
        "arm-a-pi": HISTORICAL_STAGE / "arm-a-pi",
        "arm-b-orchid": HISTORICAL_STAGE / "arm-b-orchid",
        "pi-agent-a": HISTORICAL_STAGE / "pi-agent-a",
        "pi-agent-b": HISTORICAL_STAGE / "pi-agent-b",
    }
    for source in sources.values():
        if not source.exists():
            raise SystemExit(f"historical staging input is missing: {source}")
    ignored = shutil.ignore_patterns("node_modules", "test-results", ".freebuff")
    shutil.copytree(sources["arm-a-pi"], STAGE / "arm-a-pi", ignore=ignored)
    shutil.copytree(sources["arm-b-orchid"], STAGE / "arm-b-orchid", ignore=ignored)
    shutil.copytree(sources["pi-agent-a"], STAGE / "pi-agent-a")
    shutil.copytree(sources["pi-agent-b"], STAGE / "pi-agent-b")
    configure_pi_agent_models(STAGE / "pi-agent-a", orchid_proxy=False)
    configure_pi_agent_models(STAGE / "pi-agent-b", orchid_proxy=True)

    for arm_name, manifest_name in (
        ("arm-a-pi", "PI_NATIVE"),
        ("arm-b-orchid", "ORCHID"),
    ):
        arm = STAGE / arm_name
        baseline = manifest["sealed_arms"][manifest_name]["baseline_commit"]
        subprocess.run(
            ["git", "reset", "--hard", baseline],
            cwd=arm,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "clean", "-fd"],
            cwd=arm,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "remote", "remove", "origin"],
            cwd=arm,
            check=False,
            capture_output=True,
            text=True,
        )
        (arm / "BENCHMARK_GOAL.md").write_bytes(
            (ARTIFACT / "BENCHMARK_GOAL.md").read_bytes()
        )
        historical_modules = HISTORICAL_STAGE / arm_name / "node_modules"
        if historical_modules.exists() and os.name == "nt":
            subprocess.run(
                [
                    "cmd",
                    "/c",
                    "mklink",
                    "/J",
                    str(arm / "node_modules"),
                    str(historical_modules),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        elif historical_modules.exists():
            (arm / "node_modules").symlink_to(
                historical_modules.resolve(), target_is_directory=True
            )
    for name in ("sessions", "orchid-state"):
        (STAGE / name).mkdir()
    write_json(
        ARTIFACT / "PREPARED_STAGE.json",
        {
            "prepared_at": now(),
            "stage": str(STAGE),
            "pi_native": git_snapshot(STAGE / "arm-a-pi"),
            "orchid": git_snapshot(STAGE / "arm-b-orchid"),
        },
    )
    print(json.dumps({"prepared": str(STAGE)}, indent=2))


def verify() -> None:
    manifest = load_json(ARTIFACT / "BENCHMARK_MANIFEST.json")
    checks: dict[str, Any] = {}
    checks["implementation_commit"] = run_git(ROOT, "rev-parse", "HEAD") == manifest[
        "implementation_commit"
    ]
    checks["implementation_tree"] = run_git(ROOT, "rev-parse", "HEAD^{tree}") == manifest[
        "implementation_tree"
    ]
    checks["tracked_worktree_clean"] = not run_git(
        ROOT, "status", "--short", "--untracked-files=no"
    )
    for relative, expected in manifest["implementation_file_sha256"].items():
        checks[f"hash:{relative}"] = sha256_file(ROOT / relative) == expected
    prepared = load_json(ARTIFACT / "PREPARED_STAGE.json")
    for arm, manifest_name in (("pi_native", "PI_NATIVE"), ("orchid", "ORCHID")):
        checks[f"{arm}_head"] = prepared[arm]["head"] == manifest["sealed_arms"][
            manifest_name
        ]["baseline_commit"]
        checks[f"{arm}_tree"] = prepared[arm]["tree"] == manifest["sealed_arms"][
            manifest_name
        ]["tree"]
        staged_status = {
            line.strip() for line in prepared[arm]["status"].splitlines() if line.strip()
        }
        checks[f"{arm}_sealed_status"] = staged_status <= {"?? BENCHMARK_GOAL.md"}
        staged_goal = STAGE / ("arm-a-pi" if arm == "pi_native" else "arm-b-orchid") / "BENCHMARK_GOAL.md"
        checks[f"{arm}_goal_hash"] = (
            staged_goal.exists()
            and sha256_file(staged_goal) == manifest["public_goal"]["sha256"]
        )
    pipeline_text = (ROOT / "memory_gateway/pipeline_adapters.py").read_text(
        encoding="utf-8"
    )
    consolidator_start = pipeline_text.index("class OpenAICompatConsolidatorEngine")
    consolidator_end = pipeline_text.index("def build_lossless_engine", consolidator_start)
    consolidator_text = pipeline_text[consolidator_start:consolidator_end]
    checks["single_authoritative_selected_evidence_representation"] = (
        '"selected_events"' not in consolidator_text
        and '"authoritative_events"' in consolidator_text
    )
    checks["canonicalizer_disabled"] = (
        load_json(ARTIFACT / "CONFIG_ORCHID.json")["canonicalizer_enabled"] is False
    )
    checks["safe_source_window_exact"] = (
        load_json(ARTIFACT / "CONFIG_ORCHID.json")["source_window_tokens"]
        == SOURCE_WINDOW_TOKENS
    )
    checks["cold_memory_off"] = (
        load_json(ARTIFACT / "CONFIG_ORCHID.json")["cold_memory_mode"] == "off"
    )
    checks["fresh_database"] = not DB_PATH.exists()
    checks["api_key_available_without_disclosure"] = _user_api_key_available()
    checks["provider_base_url"] = PROVIDER_BASE_URL == load_json(
        ARTIFACT / "CONFIG_ORCHID.json"
    )["foreground_provider"]
    checks["provider_model"] = MODEL_ID == load_json(
        ARTIFACT / "CONFIG_ORCHID.json"
    )["foreground_model"]
    checks["provider_context_tokens"] = PROVIDER_CONTEXT_TOKENS == load_json(
        ARTIFACT / "CONFIG_ORCHID.json"
    )["provider_context_tokens"]
    checks["pi_version"] = (
        subprocess.check_output([PI_EXECUTABLE, "--version"], text=True).strip()
        == "0.84.2"
    )
    result = {
        "verified_at": now(),
        "provider_requests_made": 0,
        "checks": checks,
        "all_ok": all(checks.values()),
    }
    write_json(ARTIFACT / "prelaunch/RESULTS.json", result)
    print(json.dumps(result, indent=2))
    if not result["all_ok"]:
        raise SystemExit("prelaunch verification failed")


def _json(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _sequence(connection: sqlite3.Connection, event_id: str | None) -> int:
    if not event_id:
        return 0
    row = connection.execute("SELECT sequence FROM events WHERE id = ?", (event_id,)).fetchone()
    return int(row[0]) if row else 0


def _request_estimate(model_run: dict[str, Any]) -> int | None:
    metadata = _json(model_run.get("metadata_json"), {})
    profile = metadata.get("request_profile") or {}
    fields = (
        "input_payload_chars",
        "system_prompt_chars",
        "response_schema_chars",
        "request_wrapper_chars",
    )
    if not any(profile.get(field) is not None for field in fields):
        return None
    return math.ceil(sum(int(profile.get(field) or 0) for field in fields) / 4)


def inspect_state() -> dict[str, Any]:
    if not DB_PATH.exists():
        return {
            "timestamp": now(),
            "status": "WAITING_FOR_DATABASE",
            "gate": {"status": "PENDING", "clean_promotions": 0, "required": 3},
        }
    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        jobs = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM compaction_jobs WHERE thread_id = ? ORDER BY generation",
                (THREAD_ID,),
            )
        ]
        thread_row = connection.execute(
            "SELECT * FROM threads WHERE id = ?", (THREAD_ID,)
        ).fetchone()
        thread = dict(thread_row) if thread_row else {}
        total_events = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(token_count),0), MIN(created_at), MAX(created_at) "
            "FROM events WHERE thread_id = ?",
            (THREAD_ID,),
        ).fetchone()
        generations: list[dict[str, Any]] = []
        previous_end_sequence = 0
        covered_sequences: set[int] = set()
        for job in jobs:
            start_sequence = _sequence(connection, job.get("snapshot_start_event_id"))
            end_sequence = _sequence(connection, job.get("snapshot_end_event_id"))
            source_rows = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM events WHERE thread_id = ? AND sequence BETWEEN ? AND ? "
                    "ORDER BY sequence",
                    (THREAD_ID, start_sequence, end_sequence),
                )
            ] if start_sequence and end_sequence else []
            event_objects = [Event.from_row(row) for row in source_rows]
            source_envelope_tokens = sum(
                estimate_tokens(json.dumps(source_item_payload(event), ensure_ascii=False))
                for event in event_objects
            )
            source_stored_tokens = sum(int(row.get("token_count") or 0) for row in source_rows)
            base_row = None
            if job.get("base_capsule_id"):
                base_row = connection.execute(
                    "SELECT * FROM capsules WHERE id = ?", (job["base_capsule_id"],)
                ).fetchone()
            base_capsule_tokens = estimate_tokens(base_row["content"]) if base_row else 0
            effective_window = max(1_000, SOURCE_WINDOW_TOKENS - base_capsule_tokens)
            bounded_window = source_envelope_tokens <= effective_window or len(source_rows) == 1

            model_runs = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM model_runs WHERE job_id = ? ORDER BY created_at,id",
                    (job["id"],),
                )
            ]
            by_stage = Counter(str(row["stage"]) for row in model_runs)
            consolidator_runs = [row for row in model_runs if row["stage"] == "consolidator"]
            selector_runs = [row for row in model_runs if row["stage"] == "selector"]
            selected_refs = []
            for run in consolidator_runs:
                selected_refs.extend(_json(run.get("source_refs_json"), []))
            selected_refs_unique = len(selected_refs) == len(set(selected_refs))
            selected_tokens = 0
            try:
                source_items = expand_source_items(
                    event_objects, selector_budget_tokens=1_200, safety_margin=0.8
                )
                selected_set = set(selected_refs)
                selected_tokens = sum(
                    estimate_tokens(json.dumps(source_item_payload(item), ensure_ascii=False))
                    for item in source_items
                    if item.id in selected_set
                )
            except ValueError:
                selected_tokens = 0

            capsule_row = connection.execute(
                "SELECT * FROM capsules WHERE thread_id = ? AND snapshot_end_event_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (THREAD_ID, job.get("snapshot_end_event_id")),
            ).fetchone()
            capsule = dict(capsule_row) if capsule_row else None
            capsule_metadata = _json(capsule.get("model_metadata_json") if capsule else None, {})
            legal_source_ids = set(selected_refs)
            legal_event_ids = {row["id"] for row in source_rows}
            evidence_source_ids = set(capsule_metadata.get("evidence_source_ids") or [])
            evidence_event_ids = set(capsule_metadata.get("evidence_event_ids") or [])
            exact_legal_provenance = (
                evidence_source_ids.issubset(legal_source_ids)
                and evidence_event_ids.issubset(legal_event_ids)
            )
            request_actuals = [
                int(row["input_tokens"])
                for row in model_runs
                if row.get("input_tokens") is not None
            ]
            request_estimates = [
                estimate
                for estimate in (_request_estimate(row) for row in model_runs)
                if estimate is not None
            ]
            requests_all_below_limit = bool(model_runs) and all(
                value < PROVIDER_CONTEXT_TOKENS for value in request_actuals
            ) and len(request_actuals) == len(model_runs)
            model_runs_succeeded = bool(model_runs) and all(
                row.get("status") == "SUCCEEDED" for row in model_runs
            )
            start_is_next = start_sequence == previous_end_sequence + 1
            range_sequences = set(range(start_sequence, end_sequence + 1)) if start_sequence else set()
            no_overlap = not (range_sequences & covered_sequences)
            if job["status"] == "PROMOTED":
                covered_sequences.update(range_sequences)
                previous_end_sequence = end_sequence
            promotion_exact = bool(
                job["status"] == "PROMOTED"
                and capsule
                and capsule.get("covered_start_event_id") == job.get("snapshot_start_event_id")
                and capsule.get("covered_end_event_id") == job.get("snapshot_end_event_id")
                and capsule.get("state") in {"ACTIVE", "SUPERSEDED"}
            )
            unknown_provenance = "unknown evidence" in str(job.get("error") or "").lower()
            clean = all(
                (
                    job["status"] == "PROMOTED",
                    bounded_window,
                    by_stage.get("canonicalizer", 0) == 0,
                    by_stage.get("selector", 0) > 0,
                    by_stage.get("consolidator", 0) == 1,
                    model_runs_succeeded,
                    requests_all_below_limit,
                    selected_refs_unique,
                    exact_legal_provenance,
                    start_is_next,
                    no_overlap,
                    promotion_exact,
                    not unknown_provenance,
                )
            )
            generations.append(
                {
                    "generation": job["generation"],
                    "job_id": job["id"],
                    "status": job["status"],
                    "watermark_start": job.get("snapshot_start_event_id"),
                    "watermark_end": job.get("snapshot_end_event_id"),
                    "start_sequence": start_sequence,
                    "end_sequence": end_sequence,
                    "source_events": len(source_rows),
                    "source_tokens": source_stored_tokens,
                    "source_envelope_tokens": source_envelope_tokens,
                    "effective_source_window_tokens": effective_window,
                    "bounded_source_window": bounded_window,
                    "selected_items": len(selected_refs),
                    "selected_tokens": selected_tokens,
                    "selector_calls": len(selector_runs),
                    "selector_input_tokens": sum(int(row.get("input_tokens") or 0) for row in selector_runs),
                    "selector_output_tokens": sum(int(row.get("output_tokens") or 0) for row in selector_runs),
                    "selector_latency_ms": sum(float(row.get("wall_ms") or 0) for row in selector_runs),
                    "consolidator_calls": len(consolidator_runs),
                    "consolidator_input_tokens": sum(int(row.get("input_tokens") or 0) for row in consolidator_runs),
                    "consolidator_output_tokens": sum(int(row.get("output_tokens") or 0) for row in consolidator_runs),
                    "consolidator_latency_ms": sum(float(row.get("wall_ms") or 0) for row in consolidator_runs),
                    "max_actual_request_tokens": max(request_actuals, default=None),
                    "max_estimated_request_tokens": max(request_estimates, default=None),
                    "provider_context_tokens": PROVIDER_CONTEXT_TOKENS,
                    "context_headroom_tokens": (
                        PROVIDER_CONTEXT_TOKENS - max(request_actuals)
                        if request_actuals else None
                    ),
                    "capsule_chars": len(capsule.get("content") or "") if capsule else None,
                    "capsule_tokens": estimate_tokens(capsule.get("content") or "") if capsule else None,
                    "validation_result": "PASS" if capsule and capsule.get("validated_at") else "NOT_PASSED",
                    "promotion_result": job["status"],
                    "failure_category": job.get("error"),
                    "canonicalizer_calls": by_stage.get("canonicalizer", 0),
                    "exact_legal_provenance": exact_legal_provenance,
                    "unknown_provenance_ids": unknown_provenance,
                    "watermark_progression_exact": start_is_next and no_overlap,
                    "duplicate_selected_source_coverage": not selected_refs_unique,
                    "model_runs_succeeded": model_runs_succeeded,
                    "requests_below_provider_limit": requests_all_below_limit,
                    "promotion_cas_exact": promotion_exact,
                    "started_at": job.get("started_at"),
                    "finished_at": job.get("finished_at"),
                    "clean_promotion": clean,
                }
            )

        status_counts = Counter(job["status"] for job in jobs)
        active_scheduler_jobs = status_counts["QUEUED"] + status_counts["RUNNING"]
        scheduler_bounded = active_scheduler_jobs <= 1
        canonicalizer_calls = sum(
            generation["canonicalizer_calls"] for generation in generations
        )
        promoted = [generation for generation in generations if generation["status"] == "PROMOTED"]
        clean_promotions = [generation for generation in promoted if generation["clean_promotion"]]
        failures = [job for job in jobs if job["status"] in {"FAILED", "STALE"}]
        invariant_failures = [generation for generation in promoted if not generation["clean_promotion"]]
        overflow = any(
            (generation["max_actual_request_tokens"] or 0) >= PROVIDER_CONTEXT_TOKENS
            for generation in generations
        )
        if len(clean_promotions) >= 3 and not failures and not invariant_failures:
            gate_status = "PASSED"
        elif failures or invariant_failures or not scheduler_bounded or canonicalizer_calls or overflow:
            gate_status = "FAILED"
        else:
            gate_status = "PENDING"
        active_capsule = None
        if thread.get("active_capsule_id"):
            row = connection.execute(
                "SELECT * FROM capsules WHERE id = ?", (thread["active_capsule_id"],)
            ).fetchone()
            if row:
                active_capsule = dict(row)
        active_end_sequence = _sequence(
            connection,
            active_capsule.get("covered_end_event_id") if active_capsule else None,
        )
        backlog = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(token_count),0) FROM events "
            "WHERE thread_id = ? AND sequence > ?",
            (THREAD_ID, active_end_sequence),
        ).fetchone()
        return {
            "timestamp": now(),
            "status": "OBSERVED",
            "thread_id": THREAD_ID,
            "event_count": int(total_events[0]),
            "arrival_tokens": int(total_events[1]),
            "first_event_at": total_events[2],
            "last_event_at": total_events[3],
            "active_capsule_id": thread.get("active_capsule_id"),
            "active_capsule_tokens": estimate_tokens(active_capsule.get("content") or "") if active_capsule else 0,
            "active_watermark": active_capsule.get("covered_end_event_id") if active_capsule else None,
            "raw_backlog_events": int(backlog[0]),
            "raw_backlog_tokens": int(backlog[1]),
            "estimated_foreground_tokens": (
                (estimate_tokens(active_capsule.get("content") or "") if active_capsule else 0)
                + int(backlog[1])
            ),
            "scheduler": {
                "status_counts": dict(status_counts),
                "queued_or_running": active_scheduler_jobs,
                "bounded": scheduler_bounded,
                "dirty": bool(thread.get("compaction_dirty")),
                "dirty_priority": int(thread.get("compaction_dirty_priority") or 0),
            },
            "canonicalizer_calls": canonicalizer_calls,
            "generations": generations,
            "gate": {
                "status": gate_status,
                "required": 3,
                "promotions": len(promoted),
                "clean_promotions": len(clean_promotions),
                "failures": len(failures),
                "invariant_failures": len(invariant_failures),
            },
        }
    finally:
        connection.close()


def monitor(interval_seconds: float) -> None:
    runtime_path = ARTIFACT / "memory_transitions/orchid_runtime.jsonl"
    promotion_path = ARTIFACT / "memory_transitions/orchid_promotions.jsonl"
    gate_path = ARTIFACT / "memory_transitions/GATE_STATUS.json"
    stop_path = ARTIFACT / "controller/STOP_REQUEST.json"
    observed_promotion_ids: set[str] = set()
    passed_once = False
    previous_state: dict[str, Any] | None = None
    while True:
        state = inspect_state()
        append_jsonl(runtime_path, state)
        gate = state["gate"]
        write_json(gate_path, {**gate, "observed_at": state["timestamp"]})
        for generation in state.get("generations", []):
            if (
                generation["status"] == "PROMOTED"
                and generation["job_id"] not in observed_promotion_ids
            ):
                append_jsonl(
                    promotion_path,
                    {
                        "observed_at": state["timestamp"],
                        "foreground_before": {
                            "observed_at": previous_state.get("timestamp"),
                            "active_capsule_tokens": previous_state.get("active_capsule_tokens"),
                            "raw_backlog_tokens": previous_state.get("raw_backlog_tokens"),
                            "estimated_foreground_tokens": previous_state.get("estimated_foreground_tokens"),
                        } if previous_state else None,
                        "foreground_after": {
                            "active_capsule_tokens": state.get("active_capsule_tokens"),
                            "raw_backlog_tokens": state.get("raw_backlog_tokens"),
                            "estimated_foreground_tokens": state.get("estimated_foreground_tokens"),
                        },
                        **generation,
                    },
                )
                observed_promotion_ids.add(generation["job_id"])
        if gate["status"] == "PASSED":
            passed_once = True
        failure_reason = None
        if gate["status"] == "FAILED":
            failure_reason = (
                "orchid_operational_failure_after_gate"
                if passed_once
                else "orchid_failed_three_clean_promotion_gate"
            )
        if failure_reason and not stop_path.exists():
            write_json(
                stop_path,
                {"requested_at": now(), "reason": failure_reason, "state": state},
            )
        if (ARTIFACT / "driver_summary.json").exists():
            return
        previous_state = state
        time.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("freeze", "prepare", "verify", "inspect", "monitor"))
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    arguments = parser.parse_args()
    if arguments.command == "freeze":
        freeze()
    elif arguments.command == "prepare":
        prepare()
    elif arguments.command == "verify":
        verify()
    elif arguments.command == "inspect":
        print(json.dumps(inspect_state(), indent=2))
    else:
        monitor(arguments.interval_seconds)


if __name__ == "__main__":
    main()
