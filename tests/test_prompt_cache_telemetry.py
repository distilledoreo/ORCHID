from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from memory_gateway.transport.prompt_cache import (
    block_content_hash,
    build_prompt_cache_report,
    merge_provider_cache_usage,
)


def test_block_hash_is_stable_for_identical_messages() -> None:
    messages = [{"role": "system", "content": "capsule body"}]
    assert block_content_hash(messages) == block_content_hash(messages)


def test_stable_prefix_reuse_estimates_cache_read() -> None:
    capsule = [{"role": "system", "content": "capsule"}]
    first = build_prompt_cache_report(
        developer_system=[],
        capsule_messages=capsule,
        raw_tail_messages=[],
        cold_memory_messages=[],
        root_task_messages=[],
        conversation_messages=[{"role": "user", "content": "turn 1"}],
    )
    second = build_prompt_cache_report(
        developer_system=[],
        capsule_messages=capsule,
        raw_tail_messages=[],
        cold_memory_messages=[],
        root_task_messages=[],
        conversation_messages=[{"role": "user", "content": "turn 2"}],
        previous_stable_prefix_hash=first.stable_prefix_hash,
    )
    assert second.estimated_cache_read_tokens == first.stable_prefix_tokens
    assert second.estimated_cache_write_tokens == second.mutable_suffix_tokens


@given(st.text(min_size=1, max_size=200))
def test_conversation_changes_do_not_change_capsule_hash(text: str) -> None:
    capsule = [{"role": "system", "content": "stable capsule"}]
    first = build_prompt_cache_report(
        developer_system=[],
        capsule_messages=capsule,
        raw_tail_messages=[],
        cold_memory_messages=[],
        root_task_messages=[],
        conversation_messages=[{"role": "user", "content": text}],
    )
    second = build_prompt_cache_report(
        developer_system=[],
        capsule_messages=capsule,
        raw_tail_messages=[],
        cold_memory_messages=[],
        root_task_messages=[],
        conversation_messages=[{"role": "user", "content": text + "!"}],
    )
    assert first.block_hashes["capsule"] == second.block_hashes["capsule"]
    assert first.block_hashes["conversation"] != second.block_hashes["conversation"]


def test_merge_provider_cache_usage_reports_estimate_error() -> None:
    capsule = [{"role": "system", "content": "capsule"}]
    first = build_prompt_cache_report(
        developer_system=[],
        capsule_messages=capsule,
        raw_tail_messages=[],
        cold_memory_messages=[],
        root_task_messages=[],
        conversation_messages=[{"role": "user", "content": "hello"}],
    )
    report = build_prompt_cache_report(
        developer_system=[],
        capsule_messages=capsule,
        raw_tail_messages=[],
        cold_memory_messages=[],
        root_task_messages=[],
        conversation_messages=[{"role": "user", "content": "hello again"}],
        previous_stable_prefix_hash=first.stable_prefix_hash,
    )
    merged = merge_provider_cache_usage(
        report,
        provider_cache_read_tokens=50,
        provider_cache_write_tokens=10,
    )
    assert merged["provider_cache_read_tokens"] == 50
    assert merged["provider_cache_write_tokens"] == 10
    assert merged["cache_estimate_error_tokens"] == (
        50 - report.estimated_cache_read_tokens
    )


def test_blockwise_prefix_reports_first_cache_break() -> None:
    first = build_prompt_cache_report(
        developer_system=[{"role": "developer", "content": "fixed"}],
        capsule_messages=[{"role": "system", "content": "capsule one"}],
        raw_tail_messages=[],
        cold_memory_messages=[],
        root_task_messages=[{"role": "user", "content": "root"}],
        conversation_messages=[{"role": "assistant", "content": "turn one"}],
    )
    second = build_prompt_cache_report(
        developer_system=[{"role": "developer", "content": "fixed"}],
        capsule_messages=[{"role": "system", "content": "capsule two"}],
        raw_tail_messages=[],
        cold_memory_messages=[],
        root_task_messages=[{"role": "user", "content": "root"}],
        conversation_messages=[{"role": "assistant", "content": "turn two"}],
        previous_block_hashes=first.block_hashes,
    )
    assert second.cache_break_block == "capsule"
    assert second.reusable_prefix_tokens == second.block_tokens["developer_system"]
    assert second.estimated_cache_read_tokens == second.reusable_prefix_tokens


def test_raw_tail_changes_do_not_invalidate_stable_prefix() -> None:
    developer = [{"role": "developer", "content": "control"}]
    capsule = [{"role": "system", "content": "stable capsule"}]
    root = [{"role": "user", "content": "immutable root"}]
    first = build_prompt_cache_report(
        developer_system=developer,
        capsule_messages=capsule,
        raw_tail_messages=[{"role": "system", "content": "tail one"}],
        cold_memory_messages=[],
        root_task_messages=root,
        conversation_messages=[{"role": "user", "content": "turn 1"}],
    )
    second = build_prompt_cache_report(
        developer_system=developer,
        capsule_messages=capsule,
        raw_tail_messages=[{"role": "system", "content": "tail two"}],
        cold_memory_messages=[],
        root_task_messages=root,
        conversation_messages=[{"role": "user", "content": "turn 2"}],
        previous_stable_prefix_hash=first.stable_prefix_hash,
        previous_block_hashes=first.block_hashes,
    )
    assert first.stable_prefix_hash == second.stable_prefix_hash
    assert second.cache_break_block == "raw_tail"
    assert second.reusable_prefix_tokens == (
        second.block_tokens["developer_system"]
        + second.block_tokens["capsule"]
        + second.block_tokens["root_task"]
    )
    assert second.estimated_cache_read_tokens == second.reusable_prefix_tokens

