#!/usr/bin/env python3
"""Generate a unified benchmark scorecard from raw telemetry artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from memory_gateway.benchmark_scorecard import (  # noqa: E402
    compare_to_published,
    generate_scorecard,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "artifact",
        type=Path,
        help="benchmark artifact directory",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write scorecard JSON (default: <artifact>/SCORECARD.json)",
    )
    parser.add_argument(
        "--replay-published",
        type=Path,
        help="compare regenerated economics to a published economics/SUMMARY.json",
    )
    parser.add_argument(
        "--allow-missing-hidden-eval",
        action="store_true",
        help="do not mark runs invalid when hidden evaluation is absent",
    )
    parser.add_argument(
        "--fail-on-invalid",
        action="store_true",
        help="exit non-zero when validity or replay checks fail",
    )
    args = parser.parse_args()

    artifact = args.artifact.resolve()
    scorecard = generate_scorecard(
        artifact,
        require_hidden_eval=not args.allow_missing_hidden_eval,
    )
    output = args.output or (artifact / "SCORECARD.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(scorecard, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    result = {
        "artifact": str(artifact),
        "scorecard": str(output),
        "valid": scorecard["validity"]["valid"],
        "validity_reasons": scorecard["validity"]["reasons"],
    }
    if args.replay_published:
        replay = compare_to_published(
            scorecard,
            published_economics_path=args.replay_published.resolve(),
        )
        result["replay"] = replay
        if args.fail_on_invalid and not replay["all_ok"]:
            print(json.dumps(result, indent=2))
            raise SystemExit(2)
    if args.fail_on_invalid and not scorecard["validity"]["valid"]:
        print(json.dumps(result, indent=2))
        raise SystemExit(1)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
