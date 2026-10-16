#!/usr/bin/env python3
"""Generate a Pareto-Dominance release report from paired FreetoShop artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from memory_gateway.benchmark_scorecard import generate_scorecard  # noqa: E402
from memory_gateway.pareto_release import (  # noqa: E402
    evaluate_paired_release,
    evaluate_single_run,
)


def _write_markdown(report: dict, path: Path) -> None:
    lines = [
        "# ORCHID Pareto-Dominance Release Report",
        "",
        f"- **Release pass:** `{report.get('release_pass')}`",
        f"- **Runs evaluated:** {report.get('runs_evaluated')} / {report.get('required_runs')}",
        "",
        "## Aggregate checks",
        "",
    ]
    for name, ok in (report.get("aggregate_checks") or {}).items():
        lines.append(f"- `{name}`: {'PASS' if ok else 'FAIL'}")
    medians = report.get("medians") or {}
    lines.extend(
        [
            "",
            "## Median paired deltas",
            "",
            f"- API cost savings fraction: `{medians.get('cost_savings_fraction')}`",
            f"- Prompt/KV savings fraction: `{medians.get('prompt_savings_fraction')}`",
            f"- Peak context savings fraction: `{medians.get('peak_context_savings_fraction')}`",
            "",
            "## Anti-overfit evidence",
            "",
        ]
    )
    anti = report.get("anti_overfit") or {}
    for key in (
        "selector_contract_sha256",
        "selector_frozen_hashes_verified",
        "orchid_config_hashes_stable",
        "all_manifest_selector_contract_match",
    ):
        lines.append(f"- `{key}`: `{anti.get(key)}`")
    if report.get("reasons"):
        lines.extend(["", "## Failure reasons", ""])
        lines.extend(f"- {reason}" for reason in report["reasons"])
    lines.extend(["", "## Per-run summary", ""])
    for run in report.get("per_run") or []:
        lines.append(f"### {run.get('run_id') or run.get('artifact')}")
        lines.append("")
        metrics = run.get("metrics") or {}
        lines.append(
            f"- promotions={metrics.get('promotions')}, "
            f"post_final_turns={metrics.get('post_final_promotion_turns')}, "
            f"cost_savings={metrics.get('cost_savings_fraction')}, "
            f"prompt_savings={metrics.get('prompt_savings_fraction')}"
        )
        failed = [name for name, ok in (run.get("checks") or {}).items() if not ok]
        if failed:
            lines.append(f"- failed checks: {', '.join(failed)}")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "artifacts",
        nargs="+",
        type=Path,
        help="Paired benchmark artifact directories (need 3 for release gate)",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        help="Write machine-readable release report JSON",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        help="Write human-readable release report markdown",
    )
    parser.add_argument(
        "--single-run",
        action="store_true",
        help="Evaluate only the first artifact as a single paired run",
    )
    args = parser.parse_args()

    if args.single_run:
        artifact = args.artifacts[0].resolve()
        scorecard = generate_scorecard(artifact, require_hidden_eval=False)
        report = {
            "schema_version": "orchid.pareto_release.single_run.v1",
            "release_pass": False,
            "single_run": evaluate_single_run(scorecard, artifact=artifact),
        }
        report["release_pass"] = report["single_run"]["all_pass"]
    else:
        report = evaluate_paired_release([path.resolve() for path in args.artifacts])

    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(payload + "\n", encoding="utf-8")
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        _write_markdown(report, args.output_md)
    print(payload)
    return 0 if report.get("release_pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
