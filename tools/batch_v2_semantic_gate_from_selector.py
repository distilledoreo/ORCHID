#!/usr/bin/env python3
"""Run CapsuleEnvelopeV2 semantic gates across frozen selector chunk payloads."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from memory_gateway.capsule_admission import build_capsule_verifier  # noqa: E402
from memory_gateway.config import RuntimeConfig  # noqa: E402
from memory_gateway.selector_contract import load_golden_request_hashes  # noqa: E402
from memory_gateway.v2_offline_gate import run_selector_chunk_gate  # noqa: E402


def _load_chunks(path: Path | None) -> list[dict]:
    if path is None:
        return load_golden_request_hashes()["chunks"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "chunks" in payload:
        return list(payload["chunks"])
    if isinstance(payload, list):
        return payload
    raise ValueError("input must be a frozen selector hash document or chunk array")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        help="Frozen selector hash JSON (default: artifacts/selector_contract/FROZEN_REQUEST_HASHES.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write gate report JSON (default: stdout)",
    )
    parser.add_argument(
        "--shadow-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Validate envelope but keep legacy promotion content (default: true)",
    )
    parser.add_argument(
        "--require-items",
        action="store_true",
        help="Require consolidator memory items instead of legacy synthesis",
    )
    parser.add_argument(
        "--use-cloud-verifier",
        action="store_true",
        help="Use CloudCapsuleVerifier from ORCHID_BACKEND_* env when configured",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Process only the first N selector chunks",
    )
    args = parser.parse_args()

    chunks = _load_chunks(args.input)
    if args.limit is not None:
        chunks = chunks[: args.limit]

    verifier = None
    if args.use_cloud_verifier:
        config = RuntimeConfig.from_env()
        config = RuntimeConfig(
            **{
                **config.__dict__,
                "memory_format": "v2_shadow",
            }
        )
        verifier = build_capsule_verifier(config)

    report = asyncio.run(
        run_selector_chunk_gate(
            chunks=chunks,
            verifier=verifier,
            shadow_only=args.shadow_only,
            require_items=args.require_items,
        )
    )
    report["input"] = str(args.input) if args.input else "artifacts/selector_contract/FROZEN_REQUEST_HASHES.json"
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)
    return 0 if report["rejected"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
