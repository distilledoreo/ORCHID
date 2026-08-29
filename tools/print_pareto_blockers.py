#!/usr/bin/env python3
"""Print Pareto release prerequisite blockers as JSON or human-readable text."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from memory_gateway.pareto_release import pareto_prerequisite_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root (default: parent of tools/)",
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="text",
        help="Output format",
    )
    args = parser.parse_args()
    report = pareto_prerequisite_report(args.root)
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"pareto_prerequisites={'ready' if report['ready'] else 'blocked'}")
        print(f"benchmark_memory_format={report['benchmark_memory_format']}")
        gate = report.get("consolidator_gate") or {}
        print(
            "consolidator_live_gate="
            + ("ready" if gate.get("ready") else "blocked")
            + f" accepted={gate.get('accepted')} rejected={gate.get('rejected')}"
        )
        for key, ok in (report.get("checks") or {}).items():
            print(f"{key}={'ready' if ok else 'blocked'}")
        dirty_count = report.get("worktree_dirty_count")
        if dirty_count:
            print(f"worktree_dirty_count={dirty_count}")
            summary = report.get("worktree_dirty_summary") or {}
            by_prefix = summary.get("by_prefix") or {}
            if by_prefix:
                parts = [f"{key}={value}" for key, value in sorted(by_prefix.items())]
                print("worktree_dirty_by_prefix=" + ",".join(parts))
        for blocker in report.get("blockers") or ():
            print(f"blocker={blocker}")
        for index, step in enumerate(report.get("next_steps") or (), start=1):
            print(f"next_step_{index}={step}")
        reference = report.get("reference_run")
        if isinstance(reference, dict):
            print(
                "reference_run="
                + str(reference.get("artifact"))
                + f" substitution_present={reference.get('substitution_present')}"
                + f" promotions={reference.get('promotions')}"
                + f" post_final_turns={reference.get('post_final_promotion_turns')}"
            )
            failed = reference.get("failed_checks") or []
            if failed:
                print("reference_failed_checks=" + ",".join(failed))
            if reference.get("substitution_present") is False:
                print(
                    "reference_note=historical baseline without foreground substitution telemetry; "
                    "triplet runs require substitution_present=true"
                )
        probe = report.get("substitution_probe")
        if isinstance(probe, dict) and probe.get("present"):
            print(
                "substitution_probe="
                + str(probe.get("artifact"))
                + f" ready={probe.get('ready')}"
                + f" active_requests={probe.get('active_requests')}"
                + f" peak_suppressed_tokens={probe.get('peak_suppressed_covered_tokens')}"
            )
    return 0 if report.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
