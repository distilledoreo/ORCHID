from __future__ import annotations

from memory_gateway.assembly import ContextSnapshot, SubstitutionReport
from memory_gateway.transport import build_prompt_cache_report
from memory_gateway.transport.foreground import (
    finalize_record,
    record_provider_latency,
    substitution_record,
)


def _snapshot(*, with_cache: bool) -> ContextSnapshot:
    prompt_cache = None
    if with_cache:
        prompt_cache = build_prompt_cache_report(
            developer_system=[],
            capsule_messages=[{"role": "system", "content": "capsule"}],
            raw_tail_messages=[],
            cold_memory_messages=[],
            root_task_messages=[],
            conversation_messages=[{"role": "user", "content": "hello"}],
        )
    substitution = SubstitutionReport(
        active=True,
        inbound_request_messages=1,
        inbound_request_tokens=10,
        suppressed_covered_messages=0,
        suppressed_covered_tokens=0,
        retained_request_messages=1,
        retained_request_tokens=10,
        protocol_continuity_messages=0,
        protocol_continuity_tokens=0,
        active_tokens=5,
        uncovered_tail_tokens=5,
        covered_raw_tokens_forwarded=0,
        final_foreground_tokens=15,
    )
    return ContextSnapshot(
        active_capsule_id="cap-1",
        raw_tail_event_ids=("e1",),
        capsule_tokens=5,
        raw_tail_tokens=5,
        request_tokens=10,
        cold_memory_tokens=0,
        estimated_tokens=15,
        messages=[{"role": "user", "content": "hello"}],
        substitution=substitution,
        prompt_cache=prompt_cache,
    )


def test_substitution_record_includes_prompt_cache_when_present() -> None:
    record = substitution_record(
        request_id="req-1",
        thread_id="thread-1",
        inbound=[{"role": "user", "content": "hello"}],
        snapshot=_snapshot(with_cache=True),
        stream=False,
    )
    assert "prompt_cache" in record
    assert record["prompt_cache"]["stable_prefix_tokens"] > 0
    assert record["compaction_wait_ms"] == 0
    assert record["memory_maintenance_awaited"] is False
    assert record["emergency_spill_tokens"] == 0
    assert record["component_tokens"] == {}


def test_substitution_record_omits_prompt_cache_when_absent() -> None:
    record = substitution_record(
        request_id="req-1",
        thread_id="thread-1",
        inbound=[{"role": "user", "content": "hello"}],
        snapshot=_snapshot(with_cache=False),
        stream=False,
    )
    assert "prompt_cache" not in record


def test_finalize_record_computes_cache_estimate_error() -> None:
    capsule = [{"role": "system", "content": "capsule"}]
    first = build_prompt_cache_report(
        developer_system=[],
        capsule_messages=capsule,
        raw_tail_messages=[],
        cold_memory_messages=[],
        root_task_messages=[],
        conversation_messages=[{"role": "user", "content": "hello"}],
    )
    prompt_cache = build_prompt_cache_report(
        developer_system=[],
        capsule_messages=capsule,
        raw_tail_messages=[],
        cold_memory_messages=[],
        root_task_messages=[],
        conversation_messages=[{"role": "user", "content": "hello again"}],
        previous_stable_prefix_hash=first.stable_prefix_hash,
    )
    substitution = SubstitutionReport(
        active=True,
        inbound_request_messages=1,
        inbound_request_tokens=10,
        suppressed_covered_messages=0,
        suppressed_covered_tokens=0,
        retained_request_messages=1,
        retained_request_tokens=10,
        protocol_continuity_messages=0,
        protocol_continuity_tokens=0,
        active_tokens=5,
        uncovered_tail_tokens=5,
        covered_raw_tokens_forwarded=0,
        final_foreground_tokens=15,
    )
    snapshot = ContextSnapshot(
        active_capsule_id="cap-1",
        raw_tail_event_ids=("e1",),
        capsule_tokens=5,
        raw_tail_tokens=5,
        request_tokens=10,
        cold_memory_tokens=0,
        estimated_tokens=15,
        messages=[{"role": "user", "content": "hello again"}],
        substitution=substitution,
        prompt_cache=prompt_cache,
    )
    record = substitution_record(
        request_id="req-1",
        thread_id="thread-1",
        inbound=[{"role": "user", "content": "hello again"}],
        snapshot=snapshot,
        stream=False,
    )
    finalized = finalize_record(
        record,
        {"usage": {"prompt_tokens": 100, "completion_tokens": 10, "cache_read_tokens": 42}},
    )
    assert finalized["provider_cache_read_tokens"] == 42
    assert finalized["provider_input_includes_cache"] is True
    assert finalized["provider_uncached_input_tokens"] == 58
    assert finalized["cache_estimate_error_tokens"] == (
        42 - prompt_cache.estimated_cache_read_tokens
    )


def test_record_provider_latency_uses_direct_monotonic_measurements() -> None:
    record: dict = {}
    assert record_provider_latency(
        record,
        provider_wall_ms=123.5,
        time_to_first_event_ms=12.25,
    )["direct_provider_latency"] == {
        "measurement": "gateway_monotonic_clock",
        "provider_wall_ms": 123.5,
        "time_to_first_event_ms": 12.25,
        "success": True,
    }
