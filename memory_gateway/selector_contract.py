"""Frozen RX-580 selector contract for Pareto-Dominance benchmarks.

The selector is a frozen accuracy component. Its model, prompt, chunking,
schema, ordering, settings, and serial behavior must remain unchanged across
roadmap phases. This module is the single source of truth for contract
constants and deterministic request-body fingerprinting.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .pipeline_adapters import selector_response_format_for_chunk

SCHEMA_VERSION = "orchid.selector_contract.v1"

SELECTOR_MODEL = "qwen3.5-4b@q6_k"
SELECTOR_PROMPT_VERSION = "selector-v1"
SELECTOR_SYSTEM_PROMPT = (
    "You select durable-memory source items. Return JSON only with exactly "
    '{"selected_event_ids":["source-item-id"]}. Select only whole-event or '
    "source-span IDs present in the input; do not summarize or rewrite items."
)
CHUNK_TARGET_TOKENS = 1_200
SELECTOR_TIMEOUT_SECONDS = 180.0
SELECTOR_CONTEXT_TOKENS = 32_768
SPAN_SAFETY_MARGIN = 0.8
SERIAL_EXECUTION = True

GENERATION_SETTINGS: dict[str, Any] = {
    "temperature": 0,
    "top_p": 1,
    "stream": False,
    "reasoning_effort": "none",
}

GOLDEN_SAMPLE_PATH = (
    Path(__file__).resolve().parents[1]
    / "artifacts/selector_contract/FROZEN_REQUEST_HASHES.json"
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def build_chunk_payload(
    *,
    events: list[dict[str, Any]],
    event_ids: list[str],
) -> dict[str, Any]:
    return {
        "events": events,
        "event_ids_in_order": event_ids,
        "source_ids_in_order": event_ids,
    }


def request_payload_hash(chunk_payload: dict[str, Any]) -> str:
    return digest(chunk_payload)


def response_schema_hash(event_ids: list[str]) -> str:
    return digest(selector_response_format_for_chunk(event_ids))


def contract_document() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "model": SELECTOR_MODEL,
        "prompt_version": SELECTOR_PROMPT_VERSION,
        "system_prompt": SELECTOR_SYSTEM_PROMPT,
        "chunk_target_tokens": CHUNK_TARGET_TOKENS,
        "timeout_seconds": SELECTOR_TIMEOUT_SECONDS,
        "selector_context_tokens": SELECTOR_CONTEXT_TOKENS,
        "span_safety_margin": SPAN_SAFETY_MARGIN,
        "serial_execution": SERIAL_EXECUTION,
        "generation_settings": GENERATION_SETTINGS,
        "response_schema_name": "selector_response_v1",
        "chunk_payload_keys": [
            "events",
            "event_ids_in_order",
            "source_ids_in_order",
        ],
    }


def contract_sha256() -> str:
    return digest(contract_document())


def load_golden_request_hashes() -> dict[str, Any]:
    if not GOLDEN_SAMPLE_PATH.exists():
        raise FileNotFoundError(
            f"frozen selector request hashes are missing: {GOLDEN_SAMPLE_PATH}"
        )
    return json.loads(GOLDEN_SAMPLE_PATH.read_text(encoding="utf-8"))


def verify_golden_hashes() -> dict[str, Any]:
    golden = load_golden_request_hashes()
    mismatches: list[dict[str, Any]] = []
    for chunk in golden["chunks"]:
        payload = build_chunk_payload(
            events=chunk["events"],
            event_ids=chunk["event_ids"],
        )
        actual_request = request_payload_hash(payload)
        actual_schema = response_schema_hash(chunk["event_ids"])
        if actual_request != chunk["request_payload_hash"]:
            mismatches.append(
                {
                    "chunk_index": chunk["chunk_index"],
                    "field": "request_payload_hash",
                    "expected": chunk["request_payload_hash"],
                    "actual": actual_request,
                }
            )
        if actual_schema != chunk["response_schema_hash"]:
            mismatches.append(
                {
                    "chunk_index": chunk["chunk_index"],
                    "field": "response_schema_hash",
                    "expected": chunk["response_schema_hash"],
                    "actual": actual_schema,
                }
            )
    contract = contract_document()
    if contract_sha256() != golden.get("contract_sha256"):
        mismatches.append(
            {
                "field": "contract_sha256",
                "expected": golden.get("contract_sha256"),
                "actual": contract_sha256(),
            }
        )
    for key, value in golden.get("contract", {}).items():
        if contract.get(key) != value:
            mismatches.append(
                {
                    "field": f"contract.{key}",
                    "expected": value,
                    "actual": contract.get(key),
                }
            )
    return {
        "ok": not mismatches,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }
