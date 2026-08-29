#!/usr/bin/env python3
"""Generate and validate CapsuleEnvelopeV2 candidates offline.

Reads a JSON document describing one or more compaction generations with
authoritative source events and optional consolidator memory items, then
runs deterministic CapsuleEnvelopeV2 admission without calling a provider.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from memory_gateway.v2_offline_gate import process_v2_document, summarize_reports  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Offline generation JSON document")
    parser.add_argument(
        "--output",
        type=Path,
        help="Write report JSON to this path (default: stdout)",
    )
    parser.add_argument(
        "--allow-rejections",
        action="store_true",
        help="Exit 0 even when one or more generations are rejected",
    )
    args = parser.parse_args()
    document = json.loads(args.input.read_text(encoding="utf-8"))
    reports = asyncio.run(process_v2_document(document))
    summary = summarize_reports(reports)
    payload = {
        "input": str(args.input),
        **summary,
        "reports": reports,
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)
    if args.allow_rejections:
        return 0
    return 0 if summary["rejected"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
