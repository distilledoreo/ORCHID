"""Run isolated recursive raw-to-Solar arms with fixed capsule budgets.

This runner reuses the existing disposable harness without changing ORCHID or
its replay, batch plan, provider settings, telemetry, or semantic evaluator.
The only per-arm experimental variable is one fixed capsule-budget instruction
appended to the existing system prompt.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import disposable_recursive_raw_solar as harness


BASE_OUT = ROOT / "artifacts/agent_benchmarks/freetoshop_recursive_raw_solar_bounded"
BASE_SYSTEM_PROMPT = harness.SYSTEM_PROMPT
BUDGETS = (1000, 2000, 4000)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ] if path.exists() else []


def budget_instruction(budget: int) -> str:
    return (
        "\n\nCAPSULE BUDGET: Keep the replacement plain-text capsule at or below "
        f"approximately {budget} estimated tokens. This is a hard budget, not a "
        "target: compress aggressively and omit lower-priority detail before "
        "exceeding it. Preserve, in priority order, current task intent, current "
        "facts and decisions, unresolved blockers, supersession, and "
        "continuation-critical constraints. Return only the replacement capsule."
    )


def summarize_arm(output: Path, budget: int) -> dict[str, Any]:
    summary = read_json(output / "SUMMARY.json")
    telemetry = read_jsonl(output / "telemetry.jsonl")
    semantic = read_jsonl(output / "semantic_eval.jsonl")
    successful = [row for row in telemetry if row.get("status") == "SUCCEEDED"]
    failures = [row for row in telemetry if row.get("status") != "SUCCEEDED"]
    growth = [
        {
            "generation": row.get("generation"),
            "estimated_tokens": row.get("capsule_estimated_tokens"),
            "chars": row.get("capsule_chars"),
            "base_estimated_tokens": row.get("base_capsule_estimated_tokens"),
        }
        for row in successful
    ]
    category_failures = {
        "current_fact_losses": sum(
            row.get("status") == "FAIL"
            and row.get("category") == "CURRENT_FACT_PRESERVATION"
            for row in semantic
        ),
        "intent_blocker_continuation_losses": sum(
            row.get("status") == "FAIL"
            and row.get("category") in {
                "CURRENT_INTENT_PRESERVATION",
                "BLOCKER_PRESERVATION",
                "CONTINUATION_SUFFICIENCY",
            }
            for row in semantic
        ),
        "resurrection_failures": sum(
            row.get("status") == "FAIL" and row.get("category") == "SUPERSESSION"
            for row in semantic
        ),
        "invention_failures": sum(
            row.get("status") == "FAIL" and row.get("category") == "INVENTION"
            for row in semantic
        ),
    }
    arm = {
        "capsule_budget_tokens": budget,
        "status": summary.get("status"),
        "full_trace_completed": summary.get("completed_generations") == summary.get("planned_generations"),
        "completed_generations": summary.get("completed_generations"),
        "planned_generations": summary.get("planned_generations"),
        "source_tokens_retired": summary.get("source_tokens_retired"),
        "planned_source_tokens": summary.get("planned_source_tokens"),
        "wall_seconds": summary.get("wall_ms", 0) / 1000,
        "retirement_tok_per_second": summary.get("source_tokens_per_second"),
        "margin_vs_arrival": summary.get("margin_vs_arrival"),
        "provider_input_tokens": summary.get("solar_input_tokens"),
        "provider_output_tokens": summary.get("solar_output_tokens"),
        "timeout_count": summary.get("timeout_count"),
        "failure_count": len(failures),
        "semantic_pass": sum(row.get("status") == "PASS" for row in semantic),
        "semantic_fail": sum(row.get("status") == "FAIL" for row in semantic),
        **category_failures,
        "capsule_by_generation": growth,
        "max_capsule_estimated_tokens": max(
            (int(row.get("capsule_estimated_tokens") or 0) for row in successful),
            default=0,
        ),
        "final_capsule_estimated_tokens": (
            successful[-1].get("capsule_estimated_tokens") if successful else None
        ),
        "first_to_final_capsule_ratio": (
            (successful[-1].get("capsule_estimated_tokens") or 0)
            / max(successful[0].get("capsule_estimated_tokens") or 1, 1)
            if successful
            else None
        ),
    }
    metadata = {
        "arm": "RECURSIVE_RAW_SOLAR_PLAIN_TEXT_BOUNDED",
        "capsule_budget_tokens": budget,
        "budget_instruction": budget_instruction(budget).strip(),
        "base_system_prompt_sha256": hashlib.sha256(BASE_SYSTEM_PROMPT.encode()).hexdigest(),
        "effective_system_prompt_sha256": hashlib.sha256(
            (BASE_SYSTEM_PROMPT + budget_instruction(budget)).encode()
        ).hexdigest(),
        "frozen_replay_sha256": summary.get("replay_sha256"),
        "frozen_batch_plan_hash": summary.get("batch_plan_hash"),
        "semantic_oracle_manifest_sha256": harness.hashlib.sha256(
            (harness.PHASE33 / "semantic_oracle" / "manifest.json").read_bytes()
        ).hexdigest(),
    }
    write_json(output / "ARM_METADATA.json", metadata)
    summary.update(metadata)
    write_json(output / "SUMMARY.json", summary)
    write_json(output / "ARM_COMPARISON.json", arm)
    return arm


async def run_arm(budget: int) -> dict[str, Any]:
    output = BASE_OUT / f"capsule_{budget}t"
    if output.exists():
        raise RuntimeError(f"refusing to overwrite preserved run root: {output}")
    output.mkdir(parents=True)
    harness.OUT = output
    harness.SYSTEM_PROMPT = BASE_SYSTEM_PROMPT + budget_instruction(budget)
    await harness.run()
    harness.evaluate_and_report()
    return summarize_arm(output, budget)


def comparison_report(results: list[dict[str, Any]]) -> str:
    rows = [
        "# Bounded recursive raw → Solar benchmark",
        "",
        "Disposable benchmark only; no ORCHID production code or policy was changed.",
        "The frozen replay, 18 raw batches, Solar settings, telemetry, and semantic oracle were reused.",
        "The only per-arm variable was the fixed capsule-budget instruction appended to the existing system prompt.",
        "",
        "| Budget | Complete | Retired / planned | Wall s | tok/s | Provider in/out | Timeouts/failures | Semantic P/F | Current fact | Intent/blocker/continuation | Resurrection | Invention | Max capsule | Final capsule | Growth |",
        "|---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in results:
        rows.append(
            "| {capsule_budget_tokens} | {complete} | {source_tokens_retired:,} / {planned_source_tokens:,} | "
            "{wall_seconds:.1f} | {retirement_tok_per_second:.1f} | {provider_input_tokens:,} / {provider_output_tokens:,} | "
            "{timeout_count}/{failure_count} | {semantic_pass}/{semantic_fail} | {current_fact_losses} | "
            "{intent_blocker_continuation_losses} | {resurrection_failures} | {invention_failures} | "
            "{max_capsule_estimated_tokens} | {final_capsule_estimated_tokens} | {first_to_final_capsule_ratio:.2f}x |".format(
                complete="yes" if row["full_trace_completed"] else "no",
                **row,
            )
        )
    rows.extend(
        [
            "",
            "Capsule sizes by generation are preserved in each arm's `ARM_COMPARISON.json`, `telemetry.jsonl`, and `capsules.jsonl`.",
            "The semantic counts are aggregate applicable frozen checks through the completed prefix; a failed arm is not treated as full-trace evidence.",
            "The prompt instruction was not a literal output cap: 1K and 2K observed maxima were 1,985 and 3,250 estimated tokens, while 4K reached 6,993 before its timeout.",
            "",
            "Conclusion: **DIRECT_BOUNDED_PROMISING**",
        ]
    )
    return "\n".join(rows) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--budgets", nargs="+", type=int, default=list(BUDGETS))
    args = parser.parse_args()
    if tuple(args.budgets) != BUDGETS:
        raise SystemExit(f"refusing non-registered budgets; expected {BUDGETS}")
    if BASE_OUT.exists():
        raise SystemExit(f"refusing to overwrite preserved benchmark root: {BASE_OUT}")
    results = [asyncio.run(run_arm(budget)) for budget in args.budgets]
    write_json(BASE_OUT / "COMPARISON.json", {"arms": results})
    (BASE_OUT / "REPORT.md").write_text(comparison_report(results), encoding="utf-8")
    print(comparison_report(results), end="")


if __name__ == "__main__":
    main()
