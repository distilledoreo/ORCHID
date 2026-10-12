"""Gap-free protocol assembly. Coverage advances only when ACTIVE advances."""

from __future__ import annotations

import time

from .context import (
    ContextSnapshot, SubstitutionReport, _message_tokens,
    _protocol_continuity_indices, capsule_context_message, estimate_tokens,
)
from .db import canonical_json
from .ingestion import message_hash
from .message_views import event_messages
from .transport.prompt_cache import build_prompt_cache_report


def assemble_stable(assembler, thread_id, inbound, *, project_id=None,
                    request_event_ids=None, overhead_tokens=0, reserved_output_tokens=0):
    started = time.perf_counter()
    store = assembler.store
    active = store.get_active_capsule(thread_id)
    boundary = assembler._covered_sequence(active) or 0
    events = store.list_events(thread_id)
    entries = []
    legacy_hashes = []
    for event in events:
        messages = event_messages(event)
        if event["event_type"] == "request":
            hashes = [message_hash(m) for m in messages]
            overlap = max((n for n in range(min(len(legacy_hashes), len(hashes)) + 1)
                           if n == 0 or legacy_hashes[-n:] == hashes[:n]), default=0)
            messages = messages[overlap:]
        legacy_hashes.extend(message_hash(m) for m in messages)
        entries.extend((event, message) for message in messages)
    instructions = [dict(m) for m in inbound if m.get("role") in {"system", "developer"}]
    # Gateway uses the identities assigned atomically by ingestion. Standalone
    # assembly resolves occurrences from the latest matching history backward.
    alignment = [None] * len(inbound)
    if request_event_ids is not None:
        by_id = {e["id"]: e for e in events}
        alignment = [by_id.get(event_id) for event_id in request_event_ids]
    else:
        cursor = len(entries) - 1
        for i in range(len(inbound) - 1, -1, -1):
            found = cursor
            while found >= 0 and message_hash(entries[found][1]) != message_hash(inbound[i]):
                found -= 1
            if found >= 0:
                alignment[i] = entries[found][0]
                cursor = found - 1
    instruction_ids = {event["id"] for i, event in enumerate(alignment)
                       if event and inbound[i].get("role") in {"system", "developer"}}
    keep = {i for i, (event, _) in enumerate(entries)
            if event["sequence"] > boundary and event["id"] not in instruction_ids}
    mates = _protocol_continuity_indices([m for _, m in entries], keep)
    keep |= mates
    from .coding import render_message
    conversation = [render_message(store, event, message, active, store.coding_policy)
                    for i, (event, message) in enumerate(entries) if i in keep]
    unmatched = [dict(m) for i, m in enumerate(inbound)
                 if (i >= len(alignment) or alignment[i] is None)
                 and m.get("role") not in {"system", "developer"}]
    conversation += unmatched
    # Keep the original task once for legacy capsules that may omit instructions.
    root_messages = []
    root = store.get_root_task_event(thread_id)
    if active and root and root["sequence"] <= boundary and root["id"] not in {entries[i][0]["id"] for i in keep}:
        root_messages = [dict(render_message(store, root, m, active, store.coding_policy),
                              metadata={"orchid_root_task_event_id": root["id"]}) for m in event_messages(root)]
    memory = [capsule_context_message(active["content"])] if active else []
    cold_messages = []
    cold_result = None
    if assembler.cold_memory_provider and assembler.cold_memory_mode != "off" and project_id:
        from .cold_memory import build_retrieval_query_trace
        trace = build_retrieval_query_trace(inbound, active_content=active["content"] if active else None)
        hot = sum(_message_tokens(m) for m in instructions + memory + root_messages + conversation)
        available = assembler.cold_memory_token_budget
        if assembler.context_budget_tokens is not None:
            available = min(available, max(0, assembler.context_budget_tokens - hot - overhead_tokens - reserved_output_tokens))
        try:
            cold_result = assembler.cold_memory_provider.retrieve(
                project_id=project_id, thread_id=thread_id, query=trace.constructed_query,
                token_budget=available, max_injected=assembler.cold_memory_max_injected,
                mode=assembler.cold_memory_mode, query_trace=trace)
            if assembler.cold_memory_mode == "inject" and cold_result.would_inject:
                injectable = list(cold_result.would_inject)
                while injectable:
                    rendered = assembler._render_cold_memories(tuple(injectable))
                    if _message_tokens({"role": "system", "content": rendered}) <= available:
                        cold_messages = [{"role": "system", "content": rendered}]
                        break
                    injectable.pop()
        except Exception:
            pass  # Retrieval cannot remove any hot context.
    messages = instructions + memory + root_messages + cold_messages + conversation
    # Account for the complete serialized request, protocol boundaries, tool
    # schemas supplied by the caller, and reserved completion capacity.
    final = estimate_tokens(canonical_json(messages)) + 4 * len(messages)
    total = final + overhead_tokens + reserved_output_tokens
    components = {"instructions": sum(_message_tokens(m) for m in instructions),
                  "capsule": sum(_message_tokens(m) for m in memory),
                  "conversation": sum(_message_tokens(m) for m in conversation),
                  "raw_tail": 0, "root_task": sum(_message_tokens(m) for m in root_messages),
                  "retrieved_memory": sum(_message_tokens(m) for m in cold_messages),
                  "request_overhead": overhead_tokens, "reserved_output": reserved_output_tokens}
    components["protocol_serialization"] = total - sum(components.values())
    uncovered = [e for e in events if e["sequence"] > boundary]
    ids = tuple(dict.fromkeys(entries[i][0]["id"] for i in sorted(keep)))
    suppressed = [m for i, m in enumerate(inbound) if i < len(alignment) and alignment[i]
                  and alignment[i]["sequence"] <= boundary and m.get("role") not in {"system", "developer"}]
    inbound_tokens = sum(_message_tokens(m) for m in inbound)
    cold_tokens = sum(_message_tokens(m) for m in cold_messages)
    report = SubstitutionReport(
        active=bool(active), inbound_request_messages=len(inbound), inbound_request_tokens=inbound_tokens,
        suppressed_covered_messages=len(suppressed), suppressed_covered_tokens=sum(_message_tokens(m) for m in suppressed),
        retained_request_messages=len(inbound) - len(suppressed),
        retained_request_tokens=inbound_tokens - sum(_message_tokens(m) for m in suppressed),
        protocol_continuity_messages=len(mates), protocol_continuity_tokens=sum(_message_tokens(entries[i][1]) for i in mates),
        active_tokens=sum(_message_tokens(m) for m in memory),
        uncovered_tail_tokens=sum(_message_tokens(m) for i, (_, m) in enumerate(entries) if i in keep),
        covered_raw_tokens_forwarded=sum(_message_tokens(entries[i][1]) for i in mates) + sum(_message_tokens(m) for m in root_messages),
        final_foreground_tokens=total)
    cache = build_prompt_cache_report(
        developer_system=instructions, capsule_messages=memory, raw_tail_messages=[],
        cold_memory_messages=cold_messages, root_task_messages=root_messages, conversation_messages=conversation,
        previous_stable_prefix_hash=assembler._last_stable_prefix_hash.get(thread_id),
        previous_block_hashes=assembler._last_prompt_block_hashes.get(thread_id))
    assembler._last_stable_prefix_hash[thread_id] = cache.stable_prefix_hash
    assembler._last_prompt_block_hashes[thread_id] = dict(cache.block_hashes)
    return ContextSnapshot(
        active_capsule_id=active["id"] if active else None, raw_tail_event_ids=ids,
        capsule_tokens=report.active_tokens, raw_tail_tokens=report.uncovered_tail_tokens,
        request_tokens=inbound_tokens, cold_memory_tokens=cold_tokens, estimated_tokens=total, messages=messages,
        hard_limit_exceeded=assembler.foreground_hard_limit_tokens is not None and total > assembler.foreground_hard_limit_tokens,
        target_exceeded=assembler.context_budget_tokens is not None and total > assembler.context_budget_tokens,
        foreground_target_tokens=assembler.context_budget_tokens, foreground_hard_limit_tokens=assembler.foreground_hard_limit_tokens,
        organizer_lag_events=len(uncovered), organizer_lag_tokens=sum(e["token_count"] or estimate_tokens(e["content"]) for e in uncovered),
        assembly_wall_ms=(time.perf_counter() - started) * 1000, substitution=report, prompt_cache=cache,
        cold_retrieval=cold_result,
        component_tokens=components)
