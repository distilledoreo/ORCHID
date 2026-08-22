"""Run repeated direct 1K/2K consolidation on the frozen PanoRef slice."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import disposable_recursive_raw_solar as harness  # noqa: E402


FIXTURE = ROOT / "artifacts/agent_benchmarks/panoref_direct_generalization_bounded_v2"
REPLAY = FIXTURE / "frozen_panoref_replay/events.jsonl"
RAW_PLAN = FIXTURE / "arm_direct_raw_solar/batch_plan.json"
ORACLE = FIXTURE / "semantic_oracle/deterministic_checks.jsonl"
OUT = FIXTURE / "direct_repeats"
BASE_SYSTEM_PROMPT = harness.SYSTEM_PROMPT
BUDGETS = (1_000, 2_000)
REPEATS = 3


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def budget_instruction(budget: int) -> str:
    return (
        "\n\nCAPSULE BUDGET: Keep the replacement plain-text capsule at or below "
        f"approximately {budget} estimated tokens. This is a hard budget, not a "
        "target: compress aggressively and omit lower-priority detail before "
        "exceeding it. Preserve, in priority order, current task intent, current "
        "facts and decisions, unresolved blockers, supersession, and "
        "continuation-critical constraints. Return only the replacement capsule."
    )


def configure_harness(output: Path, budget: int) -> dict[str, Any]:
    harness.REPLAY = REPLAY
    harness.PHASE33 = FIXTURE
    harness.RAW_PLAN = RAW_PLAN
    harness.ORACLE = ORACLE
    harness.OUT = output
    harness.SYSTEM_PROMPT = BASE_SYSTEM_PROMPT + budget_instruction(budget)
    return {
        "replay_sha256": sha256(REPLAY),
        "raw_plan_sha256": sha256(RAW_PLAN),
        "oracle_sha256": sha256(ORACLE),
        "oracle_manifest_sha256": sha256(FIXTURE / "semantic_oracle/manifest.json"),
        "system_prompt_sha256": hashlib.sha256(harness.SYSTEM_PROMPT.encode()).hexdigest(),
        "base_system_prompt_sha256": hashlib.sha256(BASE_SYSTEM_PROMPT.encode()).hexdigest(),
        "budget": budget,
    }


def restore_harness() -> None:
    harness.REPLAY = ROOT / (
        "artifacts/agent_benchmarks/freetoshop_operability_hardening/"
        "frozen_freetoshop_replay/events.jsonl"
    )
    harness.PHASE33 = ROOT / "artifacts/agent_benchmarks/freetoshop_direct_consolidation"
    harness.RAW_PLAN = harness.PHASE33 / "arm_c_raw_solar/batch_plan.json"
    harness.ORACLE = harness.PHASE33 / "semantic_oracle/deterministic_checks.jsonl"
    harness.OUT = ROOT / "artifacts/agent_benchmarks/freetoshop_recursive_raw_solar"
    harness.SYSTEM_PROMPT = BASE_SYSTEM_PROMPT


def evaluate_output(output: Path) -> dict[str, Any]:
    telemetry = harness.primary_capture()
    capsules = {row["generation"]: row for row in load_jsonl(output / "capsules.jsonl")}
    oracle = load_jsonl(ORACLE)
    semantic: list[dict[str, Any]] = []
    for row in telemetry:
        if row.get("status") != "SUCCEEDED":
            continue
        capsule = capsules.get(row["generation"])
        if capsule:
            semantic.extend(harness.evaluate_text(
                capsule["content"],
                int(row["source_end_sequence"]),
                oracle,
                int(row["generation"]),
            ))
    write_jsonl(output / "semantic_eval.jsonl", semantic)
    summary = load_json(output / "SUMMARY.json")
    successful = [row for row in telemetry if row.get("status") == "SUCCEEDED"]
    final_generation = max((int(row["generation"]) for row in successful), default=-1)
    final_rows = [row for row in semantic if int(row["generation"]) == final_generation]
    summary.update({
        "semantic_evaluated": sum(row.get("status") in {"PASS", "FAIL"} for row in semantic),
        "semantic_pass": sum(row.get("status") == "PASS" for row in semantic),
        "semantic_fail": sum(row.get("status") == "FAIL" for row in semantic),
        "final_generation": final_generation,
        "final_semantic_evaluated": len(final_rows),
        "final_semantic_pass": sum(row.get("status") == "PASS" for row in final_rows),
        "final_semantic_fail": sum(row.get("status") == "FAIL" for row in final_rows),
        "full_trace_completed": summary.get("completed_generations") == summary.get("planned_generations"),
        "failure_generation": next((row.get("generation") for row in telemetry if row.get("status") != "SUCCEEDED"), None),
    })
    write_json(output / "SUMMARY.json", summary)
    return summary


def miss_summary(run_summaries: list[dict[str, Any]], *, full_only: bool = False) -> dict[str, Any]:
    by_check: dict[str, int] = {}
    by_category: dict[str, int] = {}
    missing_terms: dict[str, int] = {}
    for run in run_summaries:
        if full_only and not run.get("full_trace_completed"):
            continue
        final_generation = run.get("final_generation")
        for row in load_jsonl(Path(run["output"]) / "semantic_eval.jsonl"):
            if full_only and final_generation is not None and int(row.get("generation", -1)) != int(final_generation):
                continue
            if row.get("status") != "FAIL":
                continue
            by_check[row["check_id"]] = by_check.get(row["check_id"], 0) + 1
            by_category[row["category"]] = by_category.get(row["category"], 0) + 1
            for term in list(row.get("missing_all") or []) + list(row.get("missing_any") or []):
                missing_terms[term] = missing_terms.get(term, 0) + 1
    return {
        "by_check": dict(sorted(by_check.items(), key=lambda item: (-item[1], item[0]))),
        "by_category": dict(sorted(by_category.items())),
        "missing_terms": dict(sorted(missing_terms.items(), key=lambda item: (-item[1], item[0]))),
    }


async def run_one(budget: int, repeat: int) -> dict[str, Any]:
    output = OUT / f"budget_{budget}" / f"repeat_{repeat:02d}"
    if output.exists():
        raise RuntimeError(f"refusing to overwrite preserved run root: {output}")
    output.mkdir(parents=True)
    hashes = configure_harness(output, budget)
    runner_error: str | None = None
    try:
        await harness.run()
        summary = evaluate_output(output)
    except Exception as exc:
        runner_error = f"{type(exc).__name__}: {exc}"[:2000]
        summary_path = output / "SUMMARY.json"
        if summary_path.exists():
            summary = load_json(summary_path)
        else:
            summary = {"status": "FAILED", "completed_generations": 0, "planned_generations": None}
            write_json(summary_path, summary)
    finally:
        restore_harness()
    metadata = {
        "condition": "panoref_bounded_direct_raw_solar_plain_text",
        "budget": budget,
        "repeat": repeat,
        "no_retry_within_run": True,
        "replay": str(REPLAY),
        "raw_plan": str(RAW_PLAN),
        "oracle": str(ORACLE),
        "runner_error": runner_error,
        **hashes,
    }
    write_json(output / "RUN_METADATA.json", metadata)
    return {"output": str(output), **summary, **metadata}


def report(runs: list[dict[str, Any]], freeze: dict[str, Any]) -> str:
    lines = [
        "# PanoRef bounded direct 1K/2K repeat experiment",
        "",
        "Independent workload: frozen terminal slice of a completed PanoRef coding trajectory.",
        "The direct raw-to-Solar apparatus, timeout, prompt policy, no-retry rule, and 12K source batching are unchanged from the FreetoShop repeat harness.",
        "The slice preserves the authoritative review, final request, and corrective/audit/validation tail; it is not the full 1.3M-token PanoRef session.",
        "",
        f"Frozen replay SHA256: `{freeze['replay_sha256']}`; raw plan `{freeze['raw_plan_hash']}`; {freeze['planned_source_tokens']:,} source tokens; {freeze['spot_check_count']} oracle checks.",
        "",
        "| Budget | Full traces | Generations | Median wall s | Median retired tok/s | Final semantic P/F (full only) | All applicable semantic P/F |",
        "|---:|:---:|:---:|---:|---:|---:|---:|",
    ]
    for budget in BUDGETS:
        group = [run for run in runs if run["budget"] == budget]
        complete = [run for run in group if run.get("full_trace_completed")]
        walls = [float(run.get("wall_ms") or 0) / 1000 for run in complete]
        rates = [float(run.get("source_tokens_per_second") or 0) for run in complete]
        final_pass = sum(int(run.get("final_semantic_pass") or 0) for run in complete)
        final_fail = sum(int(run.get("final_semantic_fail") or 0) for run in complete)
        all_pass = sum(int(run.get("semantic_pass") or 0) for run in group)
        all_fail = sum(int(run.get("semantic_fail") or 0) for run in group)
        generations = ", ".join(f"{run.get('completed_generations', 0)}/{run.get('planned_generations', '?')}" for run in group)
        lines.append(
            f"| {budget} | {len(complete)}/{len(group)} | {generations} | "
            f"{statistics.median(walls) if walls else 0:.1f} | {statistics.median(rates) if rates else 0:.2f} | "
            f"{final_pass}/{final_fail} | {all_pass}/{all_fail} |"
        )
    lines.extend([
        "",
        "## Reliability",
        "",
        "A full trace means every planned direct generation completed. Partial semantic rows are reported separately and are not treated as full-trace semantic evidence.",
        "",
        "## Miss pattern",
        "",
        "Full-trace-only misses are primary; partial-prefix rows are retained as diagnostics.",
        "",
        "### Full-trace misses",
        "",
        "```json",
        json.dumps(miss_summary(runs, full_only=True), indent=2, sort_keys=True),
        "```",
        "",
        "### All applicable prefix diagnostics",
        "",
        "```json",
        json.dumps(miss_summary(runs), indent=2, sort_keys=True),
        "```",
        "",
        "## Provenance",
        "",
        "- No provider retry occurred inside a run.",
        "- Each repeat has a fresh output root and immutable replay, plan, oracle, and prompt hashes.",
        "- This experiment does not tune FreetoShop or change ORCHID production code.",
        "",
    ])
    return "\n".join(lines)


async def main_async() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=REPEATS)
    args = parser.parse_args()
    if args.repeats != REPEATS:
        raise SystemExit(f"refusing non-registered repeat count; expected {REPEATS}")
    if not REPLAY.exists() or not RAW_PLAN.exists() or not ORACLE.exists():
        raise SystemExit("bounded PanoRef fixture is incomplete; run freeze_panoref_bounded_trace.py first")
    if OUT.exists():
        raise SystemExit(f"refusing to overwrite preserved repeat root: {OUT}")
    freeze = load_json(FIXTURE / "FREEZE_METADATA.json")
    runs: list[dict[str, Any]] = []
    for budget in BUDGETS:
        for repeat in range(1, args.repeats + 1):
            runs.append(await run_one(budget, repeat))
    write_json(OUT / "RUN_MATRIX.json", {"budgets": list(BUDGETS), "repeats": args.repeats, "runs": runs})
    text = report(runs, freeze)
    (OUT / "REPORT.md").write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    asyncio.run(main_async())
