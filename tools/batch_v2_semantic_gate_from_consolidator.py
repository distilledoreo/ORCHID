#!/usr/bin/env python3
"""Run CapsuleEnvelopeV2 semantic gates on real consolidator memory items."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from memory_gateway.admission.consolidator_batch import (  # noqa: E402
    assemble_offline_document_from_checkpoints,
    load_offline_document,
    run_consolidator_chunk_gate,
)
from memory_gateway.capsule_admission import build_capsule_verifier  # noqa: E402
from memory_gateway.config import RuntimeConfig  # noqa: E402
from memory_gateway.selector_contract import load_golden_request_hashes  # noqa: E402


def _load_chunks(path: Path | None) -> list[dict]:
    if path is None:
        return load_golden_request_hashes()["chunks"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "chunks" in payload:
        return list(payload["chunks"])
    if isinstance(payload, list):
        return payload
    raise ValueError("input must be a frozen selector hash document or chunk array")


def _select_chunks(
    chunks: list[dict],
    *,
    offset: int | None,
    limit: int | None,
    chunk_index: int | None,
) -> list[dict]:
    if chunk_index is not None:
        selected = [chunk for chunk in chunks if int(chunk.get("chunk_index", -1)) == chunk_index]
        if not selected:
            raise SystemExit(f"no frozen chunk with chunk_index={chunk_index}")
        return selected
    start = offset or 0
    end = None if limit is None else start + limit
    return chunks[start:end]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        help="Offline consolidator generations JSON (items[] required)",
    )
    parser.add_argument(
        "--selector-chunks",
        type=Path,
        help="Frozen selector chunks for live consolidator mode",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write gate report JSON (default: stdout)",
    )
    parser.add_argument(
        "--run-live",
        action="store_true",
        help="Call live selector+consolidator on frozen chunks (requires ORCHID_* env)",
    )
    parser.add_argument(
        "--assemble-checkpoints",
        type=Path,
        help="Run admission only on generation checkpoints in this directory",
    )
    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        help="Persist or reuse per-chunk consolidator generations while running live",
    )
    parser.add_argument(
        "--shadow-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Validate envelope but keep legacy promotion content (default: true)",
    )
    parser.add_argument(
        "--use-cloud-verifier",
        action="store_true",
        help="Use CloudCapsuleVerifier from ORCHID_BACKEND_* env when configured",
    )
    parser.add_argument(
        "--offset",
        type=int,
        help="Skip the first N frozen selector chunks",
    )
    parser.add_argument(
        "--chunk-index",
        type=int,
        help="Process only the frozen chunk with this chunk_index",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Process only the first N chunks/generations after offset/filter",
    )
    parser.add_argument(
        "--require-all-checkpoints",
        action="store_true",
        help="When assembling, require every frozen chunk to have a checkpoint file",
    )
    args = parser.parse_args()

    verifier = None
    if args.use_cloud_verifier:
        config = RuntimeConfig.from_env()
        config = RuntimeConfig(**{**config.__dict__, "memory_format": "v2_shadow"})
        verifier = build_capsule_verifier(config)

    document = None
    chunks = None
    config = None
    if args.assemble_checkpoints is not None:
        document = assemble_offline_document_from_checkpoints(
            args.assemble_checkpoints,
            require_all=args.require_all_checkpoints,
            shadow_only=args.shadow_only,
            require_items=True,
        )
    elif args.input is not None:
        document = load_offline_document(args.input)
        if args.limit is not None:
            document["generations"] = list(document.get("generations") or [])[: args.limit]
    elif args.run_live:
        chunks = _select_chunks(
            _load_chunks(args.selector_chunks),
            offset=args.offset,
            limit=args.limit,
            chunk_index=args.chunk_index,
        )
        config = RuntimeConfig.from_env()
    else:
        raise SystemExit(
            "provide --input, --assemble-checkpoints, or --run-live with consolidator env"
        )

    report = asyncio.run(
        run_consolidator_chunk_gate(
            chunks=chunks,
            document=document,
            config=config,
            verifier=verifier,
            shadow_only=args.shadow_only,
            require_items=True,
            checkpoint_dir=args.checkpoint_dir,
        )
    )
    if args.assemble_checkpoints is not None:
        report["mode"] = "checkpoint_assembly"
        report["input"] = str(args.assemble_checkpoints)
    else:
        report["input"] = (
            str(args.input)
            if args.input
            else str(args.selector_chunks or "artifacts/selector_contract/FROZEN_REQUEST_HASHES.json")
        )
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)
    return 0 if report["rejected"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
