#!/usr/bin/env python3
"""Freeze the selector contract and golden request-body hashes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from memory_gateway.compaction import Event, expand_source_items  # noqa: E402
from memory_gateway.pipeline_adapters import _chunk_events, _event_payload  # noqa: E402
from memory_gateway.selector_contract import (  # noqa: E402
    build_chunk_payload,
    contract_document,
    contract_sha256,
    request_payload_hash,
    response_schema_hash,
    verify_golden_hashes,
)


def load_events(path: Path) -> list[Event]:
    result: list[Event] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        result.append(
            Event(
                id=row["event_id"],
                sequence=int(row["sequence"]),
                content=row["content"],
                content_hash=row["content_hash"],
                event_type=row["event_type"],
                role=row.get("role"),
            )
        )
    return result


def freeze(
    *,
    replay: Path,
    sample: Path,
    output: Path,
) -> dict[str, object]:
    events = load_events(replay)
    items = list(expand_source_items(events, selector_budget_tokens=1_200, safety_margin=0.8))
    chunks = _chunk_events(items, target_tokens=1_200)
    sample_payload = json.loads(sample.read_text(encoding="utf-8"))
    indices = sample_payload["chunk_indices"]
    records = []
    for position, index in enumerate(indices):
        chunk = chunks[index]
        event_ids = [event.id for event in chunk]
        events_payload = [_event_payload(event) for event in chunk]
        payload = build_chunk_payload(events=events_payload, event_ids=event_ids)
        reference = sample_payload["chunks"][position]
        req_hash = request_payload_hash(payload)
        schema_hash = response_schema_hash(event_ids)
        if req_hash != reference["request_payload_hash"]:
            raise SystemExit(
                f"request hash mismatch at chunk {index}: {req_hash} != {reference['request_payload_hash']}"
            )
        if schema_hash != reference["response_schema_hash"]:
            raise SystemExit(
                f"schema hash mismatch at chunk {index}: {schema_hash} != {reference['response_schema_hash']}"
            )
        records.append(
            {
                "chunk_index": index,
                "event_ids": event_ids,
                "events": events_payload,
                "request_payload_hash": req_hash,
                "response_schema_hash": schema_hash,
            }
        )
    document = {
        "schema_version": "orchid.selector_contract.frozen_request_hashes.v1",
        "source_replay": str(replay.resolve()),
        "source_sample": str(sample.resolve()),
        "contract": contract_document(),
        "contract_sha256": contract_sha256(),
        "chunk_indices": indices,
        "chunks": records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("freeze", "verify"),
        help="freeze golden hashes or verify current contract",
    )
    parser.add_argument(
        "--replay",
        type=Path,
        default=SCRIPT_ROOT
        / "artifacts/agent_benchmarks/panoref_direct_generalization_bounded_v2/frozen_panoref_replay/events.jsonl",
    )
    parser.add_argument(
        "--sample",
        type=Path,
        default=SCRIPT_ROOT
        / "artifacts/agent_benchmarks/panoref_selector_microprofile_v1/FROZEN_SAMPLE.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=SCRIPT_ROOT / "artifacts/selector_contract/FROZEN_REQUEST_HASHES.json",
    )
    args = parser.parse_args()
    if args.command == "freeze":
        document = freeze(replay=args.replay, sample=args.sample, output=args.output)
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "contract_sha256": document["contract_sha256"],
                    "chunk_count": len(document["chunks"]),
                },
                indent=2,
            )
        )
        return
    result = verify_golden_hashes()
    print(json.dumps(result, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
