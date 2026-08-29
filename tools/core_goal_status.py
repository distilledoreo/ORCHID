#!/usr/bin/env python3
"""Seal or inspect ORCHID's unpaid core-goal contract without model calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_gateway.core_goal import (  # noqa: E402
    DEFAULT_MANIFEST_PATH,
    core_goal_status,
    write_core_goal_manifest,
)


def _print_text(report: dict[str, object]) -> None:
    ready = bool(report["core_contract_ready"])
    print(f"core_contract={'ready' if ready else 'blocked'}")
    print("paid_benchmark=blocked")
    print(f"selector_contract_sha256={report['selector_contract_sha256']}")
    print(f"frozen_replay_sha256={report['frozen_replay_sha256']}")
    print(f"canonicalizer_verdict={report['canonicalizer_verdict']}")
    organizer = report["organizer"]
    if isinstance(organizer, dict):
        print(
            "organizer_exact_once="
            + ("ready" if organizer.get("exact_once") else "blocked")
            + f" raw_events={organizer.get('raw_events')}"
            + f" source_items={organizer.get('source_items')}"
            + f" selector_chunks={organizer.get('selector_chunks')}"
        )
    checks = report.get("checks") or {}
    if isinstance(checks, dict):
        for key, ok in checks.items():
            print(f"{key}={'ready' if ok else 'blocked'}")
    blockers = report.get("blockers") or []
    if isinstance(blockers, list):
        for blocker in blockers:
            print(f"blocker={blocker}")
    next_steps = report.get("next_unpaid_steps") or []
    if isinstance(next_steps, list):
        for index, step in enumerate(next_steps, start=1):
            print(f"next_unpaid_step_{index}={step}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("seal", "check"), nargs="?", default="check")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    arguments = parser.parse_args()
    manifest = arguments.manifest or (
        DEFAULT_MANIFEST_PATH
        if arguments.root.resolve() == ROOT.resolve()
        else arguments.root / "artifacts/core_goal/CORE_GOAL_MANIFEST.json"
    )
    if arguments.command == "seal":
        document = write_core_goal_manifest(manifest, root=arguments.root)
        print(
            json.dumps(
                {
                    "manifest": str(manifest),
                    "manifest_sha256": document["manifest_sha256"],
                },
                indent=2,
            )
        )
        return 0
    report = core_goal_status(arguments.root, manifest_path=manifest)
    if arguments.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_text(report)
    return 0 if report["core_contract_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
