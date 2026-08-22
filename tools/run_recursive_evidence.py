"""Run controlled repeated direct-raw evidence on a frozen replay.

This is a disposable benchmark coordinator.  It creates one immutable output
directory per repeat and budget, refuses to overwrite prior evidence, and
never retries a provider call inside a run.  The existing recursive harness,
replay, raw batch plan, and semantic oracle remain the authority.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import disposable_recursive_raw_solar as harness  # noqa: E402


DEFAULT_REPLAY = ROOT / (
    "artifacts/agent_benchmarks/freetoshop_operability_hardening/"
    "frozen_freetoshop_replay/events.jsonl"
)
DEFAULT_PHASE3 = ROOT / "artifacts/agent_benchmarks/freetoshop_direct_consolidation"
DEFAULT_RAW_PLAN = DEFAULT_PHASE3 / "arm_c_raw_solar" / "batch_plan.json"
DEFAULT_ORACLE = DEFAULT_PHASE3 / "semantic_oracle" / "deterministic_checks.jsonl"
DEFAULT_OUT = ROOT / "artifacts/agent_benchmarks/freetoshop_recursive_raw_solar_evidence"
BASE_SYSTEM_PROMPT = harness.SYSTEM_PROMPT


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def configure_harness(
    *,
    replay: Path,
    phase3: Path,
    raw_plan: Path,
    oracle: Path,
    output: Path,
    budget: int,
) -> dict[str, Any]:
    harness.REPLAY = replay
    harness.PHASE33 = phase3
    harness.RAW_PLAN = raw_plan
    harness.ORACLE = oracle
    harness.OUT = output
    harness.SYSTEM_PROMPT = BASE_SYSTEM_PROMPT + budget_instruction(budget)
    return {
        "replay_sha256": sha256(replay),
        "raw_plan_sha256": sha256(raw_plan),
        "oracle_sha256": sha256(oracle),
        "phase3_oracle_manifest_sha256": sha256(phase3 / "semantic_oracle" / "manifest.json"),
        "system_prompt_sha256": hashlib.sha256(harness.SYSTEM_PROMPT.encode()).hexdigest(),
        "base_system_prompt_sha256": hashlib.sha256(BASE_SYSTEM_PROMPT.encode()).hexdigest(),
        "budget": budget,
    }


def restore_harness() -> None:
    harness.REPLAY = DEFAULT_REPLAY
    harness.PHASE33 = DEFAULT_PHASE3
    harness.RAW_PLAN = DEFAULT_RAW_PLAN
    harness.ORACLE = DEFAULT_ORACLE
    harness.OUT = harness.ROOT / "artifacts/agent_benchmarks/freetoshop_recursive_raw_solar"
    harness.SYSTEM_PROMPT = BASE_SYSTEM_PROMPT


async def run_one(
    *,
    repeat: int,
    budget: int,
    output: Path,
    replay: Path,
    phase3: Path,
    raw_plan: Path,
    oracle: Path,
) -> dict[str, Any]:
    if output.exists():
        raise RuntimeError(f"refusing to overwrite preserved run root: {output}")
    output.mkdir(parents=True)
    hashes = configure_harness(
        replay=replay,
        phase3=phase3,
        raw_plan=raw_plan,
        oracle=oracle,
        output=output,
        budget=budget,
    )
    error: str | None = None
    try:
        await harness.run()
        harness.evaluate_and_report()
    except Exception as exc:  # preserve the failed condition without repair/retry
        error = f"{type(exc).__name__}: {exc}"[:2000]
    finally:
        restore_harness()

    summary_path = output / "SUMMARY.json"
    if summary_path.exists():
        summary = load_json(summary_path)
    else:
        summary = {
            "status": "FAILED",
            "error": error or "run ended without a summary",
            "completed_generations": 0,
            "planned_generations": None,
        }
        write_json(summary_path, summary)
    metadata = {
        "repeat": repeat,
        "budget": budget,
        "condition": "recursive_raw_solar_plain_text_budget_instruction",
        "no_retry_within_run": True,
        "replay": str(replay),
        "raw_plan": str(raw_plan),
        "oracle": str(oracle),
        **hashes,
        "runner_error": error,
    }
    write_json(output / "RUN_METADATA.json", metadata)
    return {"repeat": repeat, "budget": budget, "output": str(output), **summary, **metadata}


def numeric_summary(rows: Iterable[dict[str, Any]], key: str) -> dict[str, float | None]:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    if not values:
        return {"median": None, "min": None, "max": None}
    return {
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def semantic_counts(output: Path) -> dict[str, int]:
    rows = load_jsonl(output / "semantic_eval.jsonl")
    passed = sum(row.get("status") == "PASS" for row in rows)
    failed = sum(row.get("status") == "FAIL" for row in rows)
    return {
        "evaluated": passed + failed,
        "pass": passed,
        "fail": failed,
    }


def final_semantic_counts(output: Path) -> dict[str, int]:
    rows = load_jsonl(output / "semantic_eval.jsonl")
    if not rows:
        return {"generation": -1, "evaluated": 0, "pass": 0, "fail": 0}
    generation = max(int(row.get("generation", -1)) for row in rows)
    final_rows = [row for row in rows if int(row.get("generation", -1)) == generation]
    passed = sum(row.get("status") == "PASS" for row in final_rows)
    failed = sum(row.get("status") == "FAIL" for row in final_rows)
    return {
        "generation": generation,
        "evaluated": passed + failed,
        "pass": passed,
        "fail": failed,
    }


def aggregate_misses(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_category: dict[str, int] = {}
    by_check: dict[str, dict[str, Any]] = {}
    by_checkpoint: dict[str, int] = {}
    missing_terms: dict[str, int] = {}
    for run in rows:
        semantic_path = Path(run["output"]) / "semantic_eval.jsonl"
        for miss in load_jsonl(semantic_path):
            if miss.get("status") != "FAIL":
                continue
            category = str(miss.get("category"))
            check_id = str(miss.get("check_id"))
            checkpoint = str(miss.get("checkpoint"))
            by_category[category] = by_category.get(category, 0) + 1
            by_checkpoint[checkpoint] = by_checkpoint.get(checkpoint, 0) + 1
            check = by_check.setdefault(check_id, {"count": 0, "category": category, "checkpoint": checkpoint, "missing_terms": {}})
            check["count"] += 1
            for term in list(miss.get("missing_all") or []) + list(miss.get("missing_any") or []):
                check["missing_terms"][term] = check["missing_terms"].get(term, 0) + 1
                missing_terms[term] = missing_terms.get(term, 0) + 1
    return {
        "by_category": dict(sorted(by_category.items())),
        "by_checkpoint": dict(sorted(by_checkpoint.items())),
        "by_check": dict(sorted(by_check.items())),
        "missing_terms": dict(sorted(missing_terms.items(), key=lambda item: (-item[1], item[0]))),
    }


def selector_baseline(phase3: Path) -> dict[str, Any]:
    summary = load_json(phase3 / "arm_b_selector_solar" / "SUMMARY.json")
    rows = [row for row in load_jsonl(phase3 / "semantic_eval" / "arm_b.jsonl") if row.get("kind") != "STRUCTURAL"]
    final_generation = max((int(row.get("generation", -1)) for row in rows), default=-1)
    final_rows = [row for row in rows if int(row.get("generation", -1)) == final_generation]
    return {
        "source": "historical_same_replay_selector_arm",
        "replay_sha256": summary.get("replay_sha256"),
        "status": summary.get("status"),
        "source_tokens_retired": summary.get("source_tokens_retired"),
        "planned_source_tokens": summary.get("planned_source_tokens"),
        "wall_seconds": float(summary.get("wall_ms") or 0) / 1000,
        "source_tokens_per_second": summary.get("source_tokens_per_second"),
        "solar_input_tokens": summary.get("solar_input_tokens"),
        "solar_output_tokens": summary.get("solar_output_tokens"),
        "semantic_pass": sum(row.get("status") == "PASS" for row in rows),
        "semantic_fail": sum(row.get("status") == "FAIL" for row in rows),
        "final_checkpoint_pass": sum(row.get("status") == "PASS" for row in final_rows),
        "final_checkpoint_fail": sum(row.get("status") == "FAIL" for row in final_rows),
    }


def build_report(
    *,
    rows: list[dict[str, Any]],
    repeats: int,
    budgets: list[int],
    phase3: Path,
    replay: Path,
    raw_plan: Path,
    oracle: Path,
) -> tuple[dict[str, Any], str]:
    enriched_rows = []
    for row in rows:
        counts = semantic_counts(Path(row["output"]))
        final = final_semantic_counts(Path(row["output"]))
        enriched_rows.append({
            **row,
            "semantic_pass": counts["pass"],
            "semantic_fail": counts["fail"],
            "semantic_evaluated": counts["evaluated"],
            "semantic_final": final,
        })
    rows = enriched_rows
    by_budget: dict[str, Any] = {}
    for budget in budgets:
        budget_rows = [row for row in rows if row["budget"] == budget]
        complete = [row for row in budget_rows if row.get("status") == "SUCCEEDED" and row.get("completed_generations") == row.get("planned_generations")]
        complete_semantic = {
            "evaluated": sum(row["semantic_evaluated"] for row in complete),
            "pass": sum(row["semantic_pass"] for row in complete),
            "fail": sum(row["semantic_fail"] for row in complete),
        }
        all_semantic = {
            "evaluated": sum(row["semantic_evaluated"] for row in budget_rows),
            "pass": sum(row["semantic_pass"] for row in budget_rows),
            "fail": sum(row["semantic_fail"] for row in budget_rows),
        }
        final_semantic = {
            "evaluated": sum(row["semantic_final"]["evaluated"] for row in complete),
            "pass": sum(row["semantic_final"]["pass"] for row in complete),
            "fail": sum(row["semantic_final"]["fail"] for row in complete),
        }
        by_budget[str(budget)] = {
            "run_count": len(budget_rows),
            "full_trace_successes": len(complete),
            "full_trace_rate": len(complete) / max(len(budget_rows), 1),
            "timeouts": sum(int(row.get("timeout_count") or 0) for row in budget_rows),
            "failures": sum(row.get("status") != "SUCCEEDED" for row in budget_rows),
            "metrics": {key: numeric_summary(budget_rows, key) for key in ("wall_ms", "source_tokens_per_second", "solar_input_tokens", "solar_output_tokens")},
            "full_trace_metrics": {key: numeric_summary(complete, key) for key in ("wall_ms", "source_tokens_per_second", "solar_input_tokens", "solar_output_tokens")},
            "semantic_all_evaluated": all_semantic,
            "semantic_full_trace_only": complete_semantic,
            "semantic_final_checkpoint_full_trace_only": final_semantic,
            "run_records": budget_rows,
            "misses": aggregate_misses(budget_rows),
        }
    selector = selector_baseline(phase3)
    selector_replay_hash = selector.get("replay_sha256")
    direct_vs_selector = {
        "same_replay": True,
        "same_replay_hash_verified": selector_replay_hash is not None and selector_replay_hash == sha256(replay),
        "same_replay_basis": "historical selector arm is documented as using the same frozen FreetoShop replay; its SUMMARY.json does not persist replay_sha256",
        "same_oracle": all(row.get("oracle_sha256") == sha256(oracle) for row in rows),
        "selector": selector,
        "direct": {
            str(budget): {
                "source": "repeated_direct_raw_runs",
                "median_wall_seconds": by_budget[str(budget)]["full_trace_metrics"]["wall_ms"]["median"] / 1000 if by_budget[str(budget)]["full_trace_metrics"]["wall_ms"]["median"] is not None else None,
                "median_source_tokens_per_second": by_budget[str(budget)]["full_trace_metrics"]["source_tokens_per_second"]["median"],
                "median_solar_input_tokens": by_budget[str(budget)]["full_trace_metrics"]["solar_input_tokens"]["median"],
                "full_trace_run_count": by_budget[str(budget)]["full_trace_successes"],
                "semantic_full_trace": by_budget[str(budget)]["semantic_full_trace_only"],
                "semantic_final_checkpoint_full_trace": by_budget[str(budget)]["semantic_final_checkpoint_full_trace_only"],
                "semantic_all_evaluated": by_budget[str(budget)]["semantic_all_evaluated"],
            }
            for budget in budgets
        },
        "caveat": "The selector arm is the preserved same-replay/oracle historical run, not a same-minute rerun; provider variance remains a limitation. Direct semantic totals include only evaluated prefixes unless marked full-trace-only.",
    }
    aggregate = {
        "condition": "recursive_raw_solar_plain_text_budget_instruction",
        "repeats": repeats,
        "budgets": budgets,
        "replay_sha256": sha256(replay),
        "raw_plan_sha256": sha256(raw_plan),
        "oracle_sha256": sha256(oracle),
        "arms": by_budget,
        "direct_vs_selector": direct_vs_selector,
        "additional_trace_status": "NOT_RUN: no second frozen coding replay and candidate-independent oracle exists in this checkout",
    }
    lines = [
        "# Repeated recursive raw → Solar evidence",
        "",
        "Disposable benchmark only. No ORCHID production code or policy is changed.",
        "",
        f"Frozen replay SHA: `{aggregate['replay_sha256']}`; raw-plan SHA: `{aggregate['raw_plan_sha256']}`; oracle SHA: `{aggregate['oracle_sha256']}`.",
        f"Runs: {repeats} independent repeats per budget; no provider retry within a run.",
        "",
        "## Repeat reliability",
        "",
        "| Budget | Full-trace successes | Success rate | Timeouts | Failures | Median tok/s | Median wall s |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for budget in budgets:
        arm = by_budget[str(budget)]
        lines.append(
            f"| {budget} | {arm['full_trace_successes']}/{arm['run_count']} | {arm['full_trace_rate']:.0%} | {arm['timeouts']} | {arm['failures']} | "
            f"{arm['metrics']['source_tokens_per_second']['median'] or 0:.1f} | {(arm['metrics']['wall_ms']['median'] or 0) / 1000:.1f} |"
        )
    lines += ["", "## Miss taxonomy", ""]
    for budget in budgets:
        label = f"{budget / 1000:g}K" if budget % 1000 == 0 else str(budget)
        lines.append(f"### {label}")
        lines.append("")
        miss = by_budget[str(budget)]["misses"]
        lines.append(f"- By category: `{json.dumps(miss['by_category'], sort_keys=True)}`")
        lines.append(f"- By checkpoint: `{json.dumps(miss['by_checkpoint'], sort_keys=True)}`")
        lines.append(f"- Missing terms: `{json.dumps(miss['missing_terms'], sort_keys=True)}`")
        lines.append("")
    lines += [
        "## Direct versus selector",
        "",
        f"Same replay: **{direct_vs_selector['same_replay']}** (hash independently verified: **{direct_vs_selector['same_replay_hash_verified']}**); same deterministic oracle: **{direct_vs_selector['same_oracle']}**.",
        f"Selector baseline: {selector['status']}, {selector['source_tokens_per_second']:.3f} selected-source tok/s, {selector['wall_seconds']:.1f}s end-to-end, {selector['semantic_pass']}/{selector['semantic_fail']} semantic P/F.",
        "Direct repeated medians:",
        "",
        "| Direct budget | Full-trace runs | Median full-run wall s | Median tok/s | Median Solar input | Final-checkpoint P/F | All evaluated P/F |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for budget in budgets:
        d = direct_vs_selector["direct"][str(budget)]
        full = d["semantic_final_checkpoint_full_trace"]
        all_evaluated = d["semantic_all_evaluated"]
        lines.append(f"| {budget} | {d['full_trace_run_count']}/{repeats} | {d['median_wall_seconds'] or 0:.1f} | {d['median_source_tokens_per_second'] or 0:.3f} | {d['median_solar_input_tokens'] or 0:.0f} | {full['pass']}/{full['fail']} | {all_evaluated['pass']}/{all_evaluated['fail']} |")
    lines += ["", f"Caveat: {direct_vs_selector['caveat']}", "", "## Additional traces", "", aggregate["additional_trace_status"], ""]
    lines.append("The additional-trace result is intentionally not inferred from unrelated retrieval or media artifacts.")
    return aggregate, "\n".join(lines) + "\n"


async def main_async(args: argparse.Namespace) -> None:
    replay = args.replay.resolve()
    phase3 = args.phase3.resolve()
    raw_plan = args.raw_plan.resolve()
    oracle = args.oracle.resolve()
    out_root = args.out_root.resolve()
    for path in (replay, raw_plan, oracle, phase3 / "semantic_oracle" / "manifest.json"):
        if not path.exists():
            raise SystemExit(f"missing frozen input: {path}")
    if out_root.exists():
        raise SystemExit(f"refusing to overwrite preserved evidence root: {out_root}")
    out_root.mkdir(parents=True)
    rows: list[dict[str, Any]] = []
    for repeat in range(1, args.repeats + 1):
        for budget in args.budgets:
            output = out_root / f"repeat_{repeat:02d}" / f"capsule_{budget}t"
            print(f"RUN repeat={repeat} budget={budget} output={output}", flush=True)
            rows.append(await run_one(repeat=repeat, budget=budget, output=output, replay=replay, phase3=phase3, raw_plan=raw_plan, oracle=oracle))
    aggregate, report = build_report(rows=rows, repeats=args.repeats, budgets=args.budgets, phase3=phase3, replay=replay, raw_plan=raw_plan, oracle=oracle)
    write_json(out_root / "REPEAT_SUMMARY.json", aggregate)
    (out_root / "REPEAT_REPORT.md").write_text(report, encoding="utf-8")
    print(report, end="")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, choices=(3, 4, 5), default=3)
    parser.add_argument("--budgets", nargs="+", type=int, choices=(1000, 2000), default=[1000, 2000])
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--replay", type=Path, default=DEFAULT_REPLAY)
    parser.add_argument("--phase3", type=Path, default=DEFAULT_PHASE3)
    parser.add_argument("--raw-plan", type=Path, default=DEFAULT_RAW_PLAN)
    parser.add_argument("--oracle", type=Path, default=DEFAULT_ORACLE)
    args = parser.parse_args()
    if sorted(args.budgets) != [1000, 2000]:
        raise SystemExit("this evidence runner requires exactly budgets 1000 and 2000")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
