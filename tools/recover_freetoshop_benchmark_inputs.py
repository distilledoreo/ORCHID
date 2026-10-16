"""Recover sealed FreetoShop benchmark inputs from committed evidence.

The original staging repositories were deleted, but the corrected run retained a
complete final worktree, RPC edit log, baseline tree hash, Pi configurations, and
an exact copy of the controller-private browser evaluator. This tool reverses the
successful tracked edits, creates fresh Git repositories, and refuses output
unless the reconstructed source tree matches the original sealed tree hash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORRECTED = (
    ROOT
    / "artifacts/agent_benchmarks/freetoshop_pi_vs_orchid_corrected_all_solar"
)
HISTORICAL_MANIFEST = (
    ROOT
    / "artifacts/agent_benchmarks/freetoshop_pi_vs_orchid_20260820/BENCHMARK_MANIFEST.json"
)
SOURCE_SNAPSHOT = CORRECTED / "pi_native/repository_final"
SOURCE_TELEMETRY = CORRECTED / "pi_native/telemetry.jsonl"
RECOVERED_EVALUATOR = (
    SOURCE_SNAPSHOT / "tests/e2e/benchmark-hidden-clone-stamp.spec.ts"
)
EXPECTED_EVALUATOR = CORRECTED / "quality/hidden_eval.json"
PRIVATE_CONFIG = ROOT / "benchmark_controller_private"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(path: Path, *arguments: str, env: dict[str, str] | None = None) -> str:
    return subprocess.check_output(
        ["git", *arguments], cwd=path, text=True, stderr=subprocess.STDOUT, env=env
    ).strip()


def successful_mutations() -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in SOURCE_TELEMETRY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    starts: dict[str, dict[str, Any]] = {}
    successful: set[str] = set()
    for row in rows:
        call_id = row.get("toolCallId")
        if not call_id:
            continue
        if row.get("type") == "tool_execution_start":
            starts[call_id] = row
        elif row.get("type") == "tool_execution_end" and not row.get("isError"):
            successful.add(call_id)
    return [
        row
        for call_id, row in starts.items()
        if call_id in successful and row.get("toolName") in {"edit", "write"}
    ]


def reconstruct_source(destination: Path, expected_tree: str) -> dict[str, Any]:
    shutil.copytree(
        SOURCE_SNAPSHOT,
        destination,
        ignore=shutil.ignore_patterns("dist", "*.tsbuildinfo", "test-results"),
    )
    mutations = successful_mutations()
    reversed_edits = 0
    removed_writes: set[str] = set()
    for row in reversed(mutations):
        arguments = row.get("args") or {}
        relative = str(arguments.get("path") or "")
        if not relative:
            raise RuntimeError("mutation is missing a path")
        target = destination / relative
        if row["toolName"] == "write":
            removed_writes.add(relative)
            continue
        text = target.read_text(encoding="utf-8")
        for edit in reversed(arguments.get("edits") or []):
            new = edit["newText"]
            old = edit["oldText"]
            if text.count(new) != 1:
                raise RuntimeError(
                    f"cannot uniquely reverse successful edit in {relative}"
                )
            text = text.replace(new, old, 1)
            reversed_edits += 1
        target.write_text(text, encoding="utf-8", newline="")
    for relative in sorted(removed_writes):
        (destination / relative).unlink(missing_ok=True)
    for relative in (
        "BENCHMARK_GOAL.md",
        "tests/e2e/benchmark-hidden-clone-stamp.spec.ts",
    ):
        (destination / relative).unlink(missing_ok=True)

    # The sealed source was committed on Windows and every retained tree entry
    # was mode 100644. The mounted artifact files appear executable on Linux,
    # so normalize modes before computing the cross-platform Git tree hash.
    for file_path in destination.rglob("*"):
        if file_path.is_file():
            file_path.chmod(file_path.stat().st_mode & ~0o111)

    subprocess.run(["git", "init", "-q"], cwd=destination, check=True)
    subprocess.run(
        ["git", "config", "core.autocrlf", "true"], cwd=destination, check=True
    )
    subprocess.run(["git", "add", "-A"], cwd=destination, check=True)
    actual_tree = git(destination, "write-tree")
    if actual_tree != expected_tree:
        raise RuntimeError(
            f"reconstructed tree mismatch: expected {expected_tree}, got {actual_tree}"
        )
    return {
        "expected_tree": expected_tree,
        "actual_tree": actual_tree,
        "reversed_edits": reversed_edits,
        "removed_written_paths": sorted(removed_writes),
    }


def commit(path: Path, message: str) -> str:
    commit_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "ORCHID benchmark recovery",
        "GIT_AUTHOR_EMAIL": "benchmark-recovery@invalid",
        "GIT_COMMITTER_NAME": "ORCHID benchmark recovery",
        "GIT_COMMITTER_EMAIL": "benchmark-recovery@invalid",
        "GIT_AUTHOR_DATE": "2026-08-28T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-08-28T00:00:00+00:00",
    }
    subprocess.run(
        ["git", "commit", "-q", "-m", message], cwd=path, check=True, env=commit_env
    )
    return git(path, "rev-parse", "HEAD")


def make_agent_config(destination: Path, arm: str) -> None:
    destination.mkdir(parents=True)
    shutil.copyfile(PRIVATE_CONFIG / f"{arm}_models.json", destination / "models.json")
    shutil.copyfile(PRIVATE_CONFIG / f"{arm}_settings.json", destination / "settings.json")


def make_oracle(destination: Path, expected_hash: str) -> str:
    if sha256(RECOVERED_EVALUATOR) != expected_hash:
        raise RuntimeError("retained evaluator does not match recorded hidden source hash")
    test_path = destination / "tests/e2e/editor.spec.ts"
    test_path.parent.mkdir(parents=True)
    shutil.copyfile(RECOVERED_EVALUATOR, test_path)
    subprocess.run(["git", "init", "-q"], cwd=destination, check=True)
    subprocess.run(["git", "add", "-A"], cwd=destination, check=True)
    return commit(destination, "Recover exact sealed browser evaluator")


def recover(destination: Path) -> None:
    if destination.exists():
        raise SystemExit(f"refusing to overwrite recovery destination: {destination}")
    historical = load_json(HISTORICAL_MANIFEST)
    expected_tree = historical["canonical_source_fingerprint"]["start_tree"]
    expected_evaluator = load_json(EXPECTED_EVALUATOR)["source_sha256"]
    destination.mkdir(parents=True)
    baseline = destination / "_baseline"
    source_evidence = reconstruct_source(baseline, expected_tree)
    baseline_commit = commit(baseline, "Recover sealed FreetoShop baseline tree")
    for arm_name in ("arm-a-pi", "arm-b-orchid"):
        shutil.copytree(baseline, destination / arm_name)
    make_agent_config(destination / "pi-agent-a", "arm_a")
    make_agent_config(destination / "pi-agent-b", "arm_b")
    oracle_commit = make_oracle(destination / "oracle", expected_evaluator)
    manifest = {
        "status": "RECOVERED_AND_TREE_VERIFIED",
        "recovered_at": datetime.now(UTC).isoformat(),
        "source_artifact": str(SOURCE_SNAPSHOT),
        "telemetry": str(SOURCE_TELEMETRY),
        "original_start_commit": historical["start_commit"],
        "original_pi_baseline_commit": historical["sealed_arms"]["arm_a_pi"]["baseline_commit"],
        "original_orchid_baseline_commit": historical["sealed_arms"]["arm_b_orchid"]["baseline_commit"],
        "recovered_baseline_commit": baseline_commit,
        "source_evidence": source_evidence,
        "original_oracle_commit": load_json(EXPECTED_EVALUATOR)["oracle_commit"],
        "recovered_oracle_commit": oracle_commit,
        "evaluator_sha256": expected_evaluator,
    }
    write_json(destination / "RECOVERY_MANIFEST.json", manifest)
    print(json.dumps({"destination": str(destination), **manifest}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "destination",
        type=Path,
        help="Fresh directory that will contain both arms, Pi configs, and oracle",
    )
    arguments = parser.parse_args()
    recover(arguments.destination.resolve())


if __name__ == "__main__":
    main()
