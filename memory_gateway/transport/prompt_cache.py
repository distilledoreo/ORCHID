"""Prompt block hashing and cache-prefix telemetry for foreground assembly."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from ..context import estimate_tokens
from ..db import canonical_json

# Assembly order: longest reusable prefix first, volatile suffix last.
BLOCK_NAMES = (
    "developer_system",
    "capsule",
    "root_task",
    "cold_memory",
    "raw_tail",
    "conversation",
)
STABLE_PREFIX_BLOCKS = (
    "developer_system",
    "capsule",
    "root_task",
)


def block_content_hash(messages: list[dict[str, Any]]) -> str:
    if not messages:
        return ""
    payload = [dict(message) for message in messages]
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def block_token_estimate(messages: list[dict[str, Any]]) -> int:
    from ..ingestion import message_content

    return sum(estimate_tokens(message_content(message)) for message in messages)


@dataclass(frozen=True)
class PromptCacheReport:
    block_hashes: dict[str, str]
    block_tokens: dict[str, int]
    stable_prefix_tokens: int
    stable_prefix_hash: str
    mutable_suffix_tokens: int
    estimated_cache_read_tokens: int
    estimated_cache_write_tokens: int
    reusable_prefix_tokens: int = 0
    cache_break_block: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_hashes": dict(self.block_hashes),
            "block_tokens": dict(self.block_tokens),
            "stable_prefix_tokens": self.stable_prefix_tokens,
            "stable_prefix_hash": self.stable_prefix_hash,
            "mutable_suffix_tokens": self.mutable_suffix_tokens,
            "estimated_cache_read_tokens": self.estimated_cache_read_tokens,
            "estimated_cache_write_tokens": self.estimated_cache_write_tokens,
            "reusable_prefix_tokens": self.reusable_prefix_tokens,
            "cache_break_block": self.cache_break_block,
        }


def _stable_blocks(block_hashes: dict[str, str]) -> tuple[str, ...]:
    return tuple(name for name in STABLE_PREFIX_BLOCKS if block_hashes.get(name))


def build_prompt_cache_report(
    *,
    developer_system: list[dict[str, Any]],
    capsule_messages: list[dict[str, Any]],
    raw_tail_messages: list[dict[str, Any]],
    cold_memory_messages: list[dict[str, Any]],
    root_task_messages: list[dict[str, Any]],
    conversation_messages: list[dict[str, Any]],
    previous_stable_prefix_hash: str | None = None,
    previous_block_hashes: dict[str, str] | None = None,
) -> PromptCacheReport:
    blocks = {
        "developer_system": developer_system,
        "capsule": capsule_messages,
        "root_task": root_task_messages,
        "cold_memory": cold_memory_messages,
        "raw_tail": raw_tail_messages,
        "conversation": conversation_messages,
    }
    block_hashes = {name: block_content_hash(messages) for name, messages in blocks.items()}
    block_tokens = {name: block_token_estimate(messages) for name, messages in blocks.items()}

    stable_names = _stable_blocks(block_hashes)
    stable_prefix_tokens = sum(block_tokens[name] for name in stable_names)
    stable_payload = {name: block_hashes[name] for name in stable_names if block_hashes[name]}
    stable_prefix_hash = (
        hashlib.sha256(canonical_json(stable_payload).encode("utf-8")).hexdigest()
        if stable_payload
        else ""
    )
    mutable_suffix_tokens = block_tokens["conversation"]

    prefix_reused = (
        previous_stable_prefix_hash is not None
        and stable_prefix_hash != ""
        and previous_stable_prefix_hash == stable_prefix_hash
    )
    reusable_prefix_tokens = 0
    cache_break_block: str | None = None
    if previous_block_hashes is not None:
        for name in BLOCK_NAMES:
            current_hash = block_hashes[name]
            previous_hash = previous_block_hashes.get(name, "")
            if not current_hash and not previous_hash:
                continue
            if current_hash and current_hash == previous_hash:
                reusable_prefix_tokens += block_tokens[name]
                continue
            cache_break_block = name
            break
        estimated_cache_read_tokens = reusable_prefix_tokens
        estimated_cache_write_tokens = max(
            0,
            sum(block_tokens.values()) - reusable_prefix_tokens,
        )
    elif prefix_reused:
        estimated_cache_read_tokens = stable_prefix_tokens
        estimated_cache_write_tokens = mutable_suffix_tokens
        reusable_prefix_tokens = stable_prefix_tokens
    else:
        estimated_cache_read_tokens = 0
        estimated_cache_write_tokens = stable_prefix_tokens + mutable_suffix_tokens

    return PromptCacheReport(
        block_hashes=block_hashes,
        block_tokens=block_tokens,
        stable_prefix_tokens=stable_prefix_tokens,
        stable_prefix_hash=stable_prefix_hash,
        mutable_suffix_tokens=mutable_suffix_tokens,
        estimated_cache_read_tokens=estimated_cache_read_tokens,
        estimated_cache_write_tokens=estimated_cache_write_tokens,
        reusable_prefix_tokens=reusable_prefix_tokens,
        cache_break_block=cache_break_block,
    )


def merge_provider_cache_usage(
    report: PromptCacheReport,
    *,
    provider_cache_read_tokens: int | None,
    provider_cache_write_tokens: int | None,
) -> dict[str, Any]:
    payload = report.to_dict()
    payload["provider_cache_read_tokens"] = provider_cache_read_tokens
    payload["provider_cache_write_tokens"] = provider_cache_write_tokens
    if provider_cache_read_tokens is not None and report.estimated_cache_read_tokens > 0:
        payload["cache_estimate_error_tokens"] = (
            provider_cache_read_tokens - report.estimated_cache_read_tokens
        )
    return payload
