"""Collect post-run evidence for the corrected FreetoShop all-Solar A/B."""

from __future__ import annotations

import json
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

from tools.benchmark_freetoshop_corrected_all_solar import (
    ARTIFACT,
    DB_PATH,
    ORACLE_ROOT,
    PROVIDER_CONTEXT_TOKENS,
    STAGE,
    git_snapshot,
    inspect_state,
    load_json,
    now,
    run_git,
    sha256_file,
    write_json,
)


PRICING_USD_PER_MILLION = {
    "input": float(os.environ.get("BENCHMARK_INPUT_USD_PER_MILLION", "0")),
    "output": float(os.environ.get("BENCHMARK_OUTPUT_USD_PER_MILLION", "0")),
}
HIDDEN_TEST_NAME = (
    "paints with clone stamp from Alt-set source with undo, reopen, and aligned persistence"
)
NPM_EXECUTABLE = os.environ.get(
    "NPM_EXECUTABLE", "npm.cmd" if os.name == "nt" else "npm"
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"_invalid_json": line[:500]})
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _assistant_text(message: dict[str, Any]) -> str:
    values: list[str] = []
    for item in message.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            values.append(item["text"])
        if item.get("type") == "thinking" and isinstance(item.get("thinking"), str):
            values.append(item["thinking"])
    return "\n".join(values)


def rpc_summary(path: Path) -> dict[str, Any]:
    rows = load_jsonl(path)
    counts = Counter(str(row.get("type", "unknown")) for row in rows)
    assistant_rows = [
        row
        for row in rows
        if row.get("type") == "message_end"
        and isinstance(row.get("message"), dict)
        and row["message"].get("role") == "assistant"
    ]
    usage_rows = [
        row["message"]["usage"]
        for row in assistant_rows
        if isinstance(row["message"].get("usage"), dict)
        and float(row["message"]["usage"].get("totalTokens") or 0) > 0
    ]

    def total(field: str) -> float:
        return sum(float(usage.get(field) or 0) for usage in usage_rows)

    tool_commands: list[str] = []
    tool_names: Counter[str] = Counter()
    for row in rows:
        if row.get("type") != "tool_execution_start":
            continue
        tool_name = str(row.get("toolName") or row.get("name") or "unknown")
        tool_names[tool_name] += 1
        arguments = row.get("args") or row.get("arguments") or {}
        if isinstance(arguments, dict):
            command = arguments.get("command") or arguments.get("cmd")
            if isinstance(command, str):
                tool_commands.append(command)
    test_commands = [
        command
        for command in tool_commands
        if any(token in command.lower() for token in ("test", "typecheck", "build", "playwright"))
    ]
    errors: list[str] = []
    for row in assistant_rows:
        message = row["message"]
        if message.get("stopReason") == "error":
            errors.append(str(message.get("errorMessage") or message.get("error") or "assistant error"))
    errors.extend(
        str(row.get("error") or row.get("message") or "rpc error")
        for row in rows
        if row.get("type") == "error" or row.get("error")
    )
    assistant_timeline = [
        {
            "timestamp_ms": int(row["message"].get("timestamp") or 0),
            "stop_reason": row["message"].get("stopReason"),
            "input_tokens": (row["message"].get("usage") or {}).get("input"),
            "output_tokens": (row["message"].get("usage") or {}).get("output"),
            "text": _assistant_text(row["message"])[:8_000],
        }
        for row in assistant_rows
    ]
    return {
        "path": str(path),
        "counting_rule": "assistant message_end records with nonzero usage; streaming message_update records excluded",
        "rpc_lines": len(rows),
        "event_counts": dict(counts),
        "assistant_messages": len(assistant_rows),
        "usage_responses": len(usage_rows),
        "tool_calls": counts.get("tool_execution_start", 0),
        "tool_failures": sum(
            1
            for row in rows
            if row.get("type") == "tool_execution_end" and row.get("isError")
        ),
        "tool_names": dict(tool_names),
        "test_commands": test_commands,
        "pi_native_compactions": counts.get("compaction_end", 0),
        "compaction_events": [
            row
            for row in rows
            if row.get("type") in {"compaction_start", "compaction_end"}
        ],
        "provider_errors": len(errors),
        "errors": errors[:100],
        "input_tokens": total("input"),
        "output_tokens": total("output"),
        "total_tokens": total("totalTokens"),
        "cache_read_tokens": total("cacheRead"),
        "cache_write_tokens": total("cacheWrite"),
        "reasoning_tokens": total("reasoning"),
        "largest_context_tokens": max(
            (float(usage.get("input") or 0) for usage in usage_rows), default=0
        ),
        "smallest_context_tokens": min(
            (float(usage.get("input") or 0) for usage in usage_rows), default=0
        ),
        "mean_context_tokens": (
            total("input") / len(usage_rows) if usage_rows else 0
        ),
        "reported_cost_usd": sum(
            float((usage.get("cost") or {}).get("total") or 0)
            for usage in usage_rows
            if isinstance(usage.get("cost"), dict)
        ),
        "calculated_cost_usd": (
            total("input") * PRICING_USD_PER_MILLION["input"]
            + total("output") * PRICING_USD_PER_MILLION["output"]
        )
        / 1_000_000,
        "assistant_timeline": assistant_timeline,
    }


def repository_summary(path: Path, baseline: str) -> dict[str, Any]:
    changed = run_git(path, "diff", "--name-only", baseline, "--")
    status = run_git(path, "status", "--short")
    changed_files = sorted(
        {
            line.strip()
            for line in changed.splitlines()
            if line.strip()
        }
        | {
            line[3:].strip()
            for line in status.splitlines()
            if len(line) > 3 and line[3:].strip() != "BENCHMARK_GOAL.md"
        }
    )
    return {
        **git_snapshot(path),
        "baseline": baseline,
        "changed_files": changed_files,
        "changed_file_count": len(changed_files),
        "diff_stat": run_git(path, "diff", "--stat", baseline, "--"),
        "commits_after_baseline": run_git(path, "log", "--oneline", f"{baseline}..HEAD"),
    }


def copy_repository(source: Path, destination: Path) -> None:
    ignored = shutil.ignore_patterns(".git", "node_modules", "test-results", ".freebuff")
    shutil.copytree(source, destination, dirs_exist_ok=True, ignore=ignored)


def run_validation(arm: str, path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"arm": arm, "cwd": str(path), "commands": {}}
    for command_name in ("typecheck", "build"):
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                [NPM_EXECUTABLE, "run", command_name],
                cwd=path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=1_800,
                env={**__import__("os").environ, "CI": "1"},
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            output_path = ARTIFACT / "quality" / f"{arm.lower()}_{command_name}.log"
            output_path.write_text(output, encoding="utf-8", errors="replace")
            result["commands"][command_name] = {
                "exit_code": completed.returncode,
                "wall_ms": (time.perf_counter() - started) * 1_000,
                "output_path": str(output_path),
                "tail": output[-4_000:],
            }
        except Exception as error:
            result["commands"][command_name] = {
                "error": str(error),
                "wall_ms": (time.perf_counter() - started) * 1_000,
            }
    return result


def run_hidden_evaluator() -> dict[str, Any]:
    source = ORACLE_ROOT / "tests/e2e/editor.spec.ts"
    result: dict[str, Any] = {
        "evaluator": "controller-private-clone-stamp-black-box-v1",
        "oracle_commit": run_git(ORACLE_ROOT, "rev-parse", "HEAD"),
        "source_sha256": sha256_file(source),
        "clone_test_name": HIDDEN_TEST_NAME,
        "started_at": now(),
        "arms": {},
    }
    for arm_id, directory in (
        ("arm_a_pi", STAGE / "arm-a-pi"),
        ("arm_b_orchid", STAGE / "arm-b-orchid"),
    ):
        injected = directory / "tests/e2e/benchmark-hidden-clone-stamp.spec.ts"
        if injected.exists():
            raise RuntimeError(f"refusing to overwrite hidden evaluator injection: {injected}")
        shutil.copyfile(source, injected)
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                [
                    NPM_EXECUTABLE,
                    "exec",
                    "--",
                    "playwright",
                    "test",
                    "tests/e2e/benchmark-hidden-clone-stamp.spec.ts",
                    "--grep",
                    HIDDEN_TEST_NAME,
                ],
                cwd=directory,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=900,
                env={**__import__("os").environ, "CI": "1"},
            )
            result["arms"][arm_id] = {
                "exit_code": completed.returncode,
                "timed_out": False,
                "duration_ms": (time.perf_counter() - started) * 1_000,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        except subprocess.TimeoutExpired as error:
            result["arms"][arm_id] = {
                "exit_code": None,
                "timed_out": True,
                "duration_ms": (time.perf_counter() - started) * 1_000,
                "stdout": error.stdout or "",
                "stderr": error.stderr or "",
            }
        finally:
            injected.unlink(missing_ok=True)
    result["ended_at"] = now()
    return result


def database_details() -> dict[str, Any]:
    state = inspect_state()
    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        capsules = [
            dict(row)
            for row in connection.execute(
                "SELECT id,base_capsule_id,snapshot_start_event_id,snapshot_end_event_id,"
                "covered_start_event_id,covered_end_event_id,state,created_at,validated_at,"
                "LENGTH(content) content_chars,content,model_metadata_json,error "
                "FROM capsules ORDER BY created_at"
            )
        ]
        model_runs = [
            dict(row)
            for row in connection.execute(
                "SELECT generation,stage,status,COUNT(*) calls,"
                "COALESCE(SUM(input_tokens),0) input_tokens,"
                "COALESCE(SUM(output_tokens),0) output_tokens,"
                "COALESCE(SUM(wall_ms),0) wall_ms "
                "FROM model_runs GROUP BY generation,stage,status "
                "ORDER BY generation,stage,status"
            )
        ]
        stages = [
            dict(row)
            for row in connection.execute(
                "SELECT stage,COUNT(*) calls,COALESCE(SUM(input_tokens),0) input_tokens,"
                "COALESCE(SUM(output_tokens),0) output_tokens,COALESCE(SUM(wall_ms),0) wall_ms "
                "FROM model_runs GROUP BY stage ORDER BY stage"
            )
        ]
        return {**state, "capsules": capsules, "model_runs": model_runs, "model_runs_by_stage": stages}
    finally:
        connection.close()


def _timestamp(value: str | None) -> float | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def throughput_summary(database: dict[str, Any], driver: dict[str, Any]) -> dict[str, Any]:
    runtime = load_jsonl(ARTIFACT / "memory_transitions/orchid_runtime.jsonl")
    runtime = [row for row in runtime if row.get("status") == "OBSERVED"]
    started = _timestamp(driver.get("started_at")) or 0
    ended = _timestamp(driver.get("ended_at")) or started
    run_seconds = max(ended - started, 0.000001)
    first_event = _timestamp(database.get("first_event_at"))
    last_event = _timestamp(database.get("last_event_at"))
    arrival_seconds = max((last_event or ended) - (first_event or started), 0.000001)
    promoted = [
        item for item in database.get("generations", []) if item.get("status") == "PROMOTED"
    ]
    retired_tokens = sum(int(item.get("source_tokens") or 0) for item in promoted)
    service_seconds = sum(
        max(
            (_timestamp(item.get("finished_at")) or 0)
            - (_timestamp(item.get("started_at")) or 0),
            0,
        )
        for item in promoted
    )
    arrival_rate = float(database.get("arrival_tokens") or 0) / arrival_seconds
    service_rate = retired_tokens / max(service_seconds, 0.000001)
    sustained_rate = retired_tokens / run_seconds
    burst_rates: list[float] = []
    for previous, current in zip(runtime, runtime[1:]):
        previous_time = _timestamp(previous.get("timestamp"))
        current_time = _timestamp(current.get("timestamp"))
        if previous_time is None or current_time is None or current_time <= previous_time:
            continue
        token_delta = int(current.get("arrival_tokens") or 0) - int(previous.get("arrival_tokens") or 0)
        if token_delta >= 0:
            burst_rates.append(token_delta / (current_time - previous_time))
    return {
        "run_seconds": run_seconds,
        "arrival_observation_seconds": arrival_seconds,
        "arrival_tokens": int(database.get("arrival_tokens") or 0),
        "retired_tokens": retired_tokens,
        "arrival_tokens_per_second": arrival_rate,
        "semantic_service_tokens_per_second": service_rate,
        "sustained_retirement_tokens_per_second": sustained_rate,
        "service_retirement_ratio": service_rate / arrival_rate if arrival_rate else None,
        "sustained_retirement_ratio": sustained_rate / arrival_rate if arrival_rate else None,
        "peak_arrival_burst_tokens_per_second": max(burst_rates, default=0),
        "peak_backlog_tokens": max(
            (int(row.get("raw_backlog_tokens") or 0) for row in runtime), default=0
        ),
        "end_backlog_tokens": int(database.get("raw_backlog_tokens") or 0),
        "runtime_samples": len(runtime),
    }


def transition_quality(
    orchid_rpc: dict[str, Any], database: dict[str, Any]
) -> list[dict[str, Any]]:
    timeline = orchid_rpc["assistant_timeline"]
    transitions: list[dict[str, Any]] = []
    for generation in database.get("generations", []):
        if generation.get("status") != "PROMOTED":
            continue
        finished = _timestamp(generation.get("finished_at"))
        if finished is None:
            continue
        before = [row for row in timeline if row["timestamp_ms"] / 1_000 <= finished]
        after = [row for row in timeline if row["timestamp_ms"] / 1_000 > finished]
        before_row = before[-1] if before else None
        after_row = after[0] if after else None
        transitions.append(
            {
                "generation": generation["generation"],
                "finished_at": generation.get("finished_at"),
                "capsule_tokens": generation.get("capsule_tokens"),
                "foreground_before_after": next(
                    (
                        {
                            "before": row.get("foreground_before"),
                            "after": row.get("foreground_after"),
                        }
                        for row in load_jsonl(
                            ARTIFACT / "memory_transitions/orchid_promotions.jsonl"
                        )
                        if row.get("job_id") == generation.get("job_id")
                    ),
                    None,
                ),
                "assistant_before": before_row,
                "assistant_after": after_row,
            }
        )
    return transitions


def collect() -> None:
    driver_path = ARTIFACT / "driver_summary.json"
    if not driver_path.exists():
        raise SystemExit("benchmark driver has not stopped; refusing finalization")
    driver = load_json(driver_path)
    manifest = load_json(ARTIFACT / "BENCHMARK_MANIFEST.json")
    pi_path = STAGE / "arm-a-pi"
    orchid_path = STAGE / "arm-b-orchid"

    pi_rpc = rpc_summary(ARTIFACT / "pi_native/telemetry.jsonl")
    orchid_rpc = rpc_summary(ARTIFACT / "orchid/telemetry.jsonl")
    pi_repo = repository_summary(pi_path, manifest["sealed_arms"]["PI_NATIVE"]["baseline_commit"])
    orchid_repo = repository_summary(orchid_path, manifest["sealed_arms"]["ORCHID"]["baseline_commit"])
    copy_repository(pi_path, ARTIFACT / "pi_native/repository_final")
    copy_repository(orchid_path, ARTIFACT / "orchid/repository_final")
    write_json(
        ARTIFACT / "repository_snapshots.json",
        {"pi_native": pi_repo, "orchid": orchid_repo},
    )

    validation = {
        "PI_NATIVE": run_validation("PI_NATIVE", pi_path),
        "ORCHID": run_validation("ORCHID", orchid_path),
    }
    write_json(ARTIFACT / "quality/validation.json", validation)
    hidden = run_hidden_evaluator()
    write_json(ARTIFACT / "quality/hidden_eval.json", hidden)

    database = database_details()
    throughput = throughput_summary(database, driver)
    transitions = transition_quality(orchid_rpc, database)
    write_json(ARTIFACT / "quality/transition_evidence.json", transitions)
    pi_compactions = [
        row
        for row in load_jsonl(ARTIFACT / "pi_native/telemetry.jsonl")
        if row.get("type") == "compaction_end"
    ]
    write_jsonl(ARTIFACT / "memory_transitions/pi_compactions.jsonl", pi_compactions)
    write_json(
        ARTIFACT / "memory_transitions/SUMMARY.json",
        {
            "pi_compactions": len(pi_compactions),
            "orchid_promotions": database.get("gate", {}).get("promotions", 0),
            "orchid_clean_promotions": database.get("gate", {}).get("clean_promotions", 0),
            "canonicalizer_calls": database.get("canonicalizer_calls", 0),
            "throughput": throughput,
            "generations": database.get("generations", []),
        },
    )

    memory_input = sum(
        int(row.get("input_tokens") or 0) for row in database.get("model_runs_by_stage", [])
    )
    memory_output = sum(
        int(row.get("output_tokens") or 0) for row in database.get("model_runs_by_stage", [])
    )
    memory_cost = (
        memory_input * PRICING_USD_PER_MILLION["input"]
        + memory_output * PRICING_USD_PER_MILLION["output"]
    ) / 1_000_000
    economics = {
        "provider": os.environ.get("BENCHMARK_PROVIDER_BASE_URL", "unknown"),
        "pricing_usd_per_million_tokens": PRICING_USD_PER_MILLION,
        "counting_rule": pi_rpc["counting_rule"],
        "pi_foreground": {
            key: pi_rpc[key]
            for key in (
                "usage_responses",
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "reported_cost_usd",
                "calculated_cost_usd",
                "largest_context_tokens",
                "mean_context_tokens",
            )
        },
        "orchid_foreground": {
            key: orchid_rpc[key]
            for key in (
                "usage_responses",
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "reported_cost_usd",
                "calculated_cost_usd",
                "largest_context_tokens",
                "mean_context_tokens",
            )
        },
        "orchid_memory_pipeline": database.get("model_runs_by_stage", []),
        "orchid_memory_input_tokens": memory_input,
        "orchid_memory_output_tokens": memory_output,
        "orchid_memory_cost_usd": memory_cost,
        "orchid_total_provider_equivalent_cost_usd": orchid_rpc["calculated_cost_usd"] + memory_cost,
        "cumulative_foreground_context_token_area": {
            "pi_native": pi_rpc["input_tokens"],
            "orchid": orchid_rpc["input_tokens"],
            "absolute_difference_pi_minus_orchid": pi_rpc["input_tokens"] - orchid_rpc["input_tokens"],
            "fraction_saved_vs_pi": (
                1 - orchid_rpc["input_tokens"] / pi_rpc["input_tokens"]
                if pi_rpc["input_tokens"] else None
            ),
            "caveat": "raw cumulative and per-response means are both reported because arm trajectories and response counts may diverge",
        },
    }
    write_json(ARTIFACT / "economics/SUMMARY.json", economics)

    pi_summary = {**pi_rpc, "repository": pi_repo, "validation": validation["PI_NATIVE"], "hidden_evaluator": hidden["arms"].get("arm_a_pi")}
    orchid_summary = {**orchid_rpc, "repository": orchid_repo, "validation": validation["ORCHID"], "hidden_evaluator": hidden["arms"].get("arm_b_orchid"), "gateway": database, "throughput": throughput}
    write_json(ARTIFACT / "pi_native/SUMMARY.json", pi_summary)
    write_json(ARTIFACT / "orchid/SUMMARY.json", orchid_summary)
    write_jsonl(
        ARTIFACT / "quality/milestone_scores.jsonl",
        [
            {
                "arm": "PI_NATIVE",
                "assistant_messages": pi_rpc["assistant_messages"],
                "tool_calls": pi_rpc["tool_calls"],
                "tool_failures": pi_rpc["tool_failures"],
                "files_changed": pi_repo["changed_file_count"],
                "typecheck_exit": validation["PI_NATIVE"]["commands"].get("typecheck", {}).get("exit_code"),
                "build_exit": validation["PI_NATIVE"]["commands"].get("build", {}).get("exit_code"),
                "hidden_exit": hidden["arms"].get("arm_a_pi", {}).get("exit_code"),
            },
            {
                "arm": "ORCHID",
                "assistant_messages": orchid_rpc["assistant_messages"],
                "tool_calls": orchid_rpc["tool_calls"],
                "tool_failures": orchid_rpc["tool_failures"],
                "files_changed": orchid_repo["changed_file_count"],
                "typecheck_exit": validation["ORCHID"]["commands"].get("typecheck", {}).get("exit_code"),
                "build_exit": validation["ORCHID"]["commands"].get("build", {}).get("exit_code"),
                "hidden_exit": hidden["arms"].get("arm_b_orchid", {}).get("exit_code"),
            },
        ],
    )
    summary = {
        "status": "EVIDENCE_COLLECTED_PENDING_FINAL_INTERPRETATION",
        "collected_at": now(),
        "driver": driver,
        "pi_native": pi_summary,
        "orchid": orchid_summary,
        "economics": economics,
        "quality_transitions": transitions,
    }
    write_json(ARTIFACT / "SUMMARY.json", summary)
    print(
        json.dumps(
            {
                "artifact": str(ARTIFACT),
                "status": summary["status"],
                "pi_typecheck": validation["PI_NATIVE"]["commands"].get("typecheck", {}).get("exit_code"),
                "orchid_typecheck": validation["ORCHID"]["commands"].get("typecheck", {}).get("exit_code"),
                "pi_hidden": hidden["arms"].get("arm_a_pi", {}).get("exit_code"),
                "orchid_hidden": hidden["arms"].get("arm_b_orchid", {}).get("exit_code"),
                "promotions": database.get("gate", {}).get("promotions"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    collect()
