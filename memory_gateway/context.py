from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any

from .cold_memory import (
    ColdMemoryProvider,
    ColdMemorySearchResult,
    build_retrieval_query_trace,
)
from .db import SQLiteStore, content_hash
from .cold_telemetry import ColdMemoryTelemetrySink


def estimate_tokens(text: str) -> int:
    """Conservative dependency-free estimate used only for budgeting."""
    return max(1, len(text) // 4)


@dataclass(frozen=True)
class ContextSnapshot:
    active_capsule_id: str | None
    raw_tail_event_ids: tuple[str, ...]
    capsule_tokens: int
    raw_tail_tokens: int
    request_tokens: int
    cold_memory_tokens: int
    estimated_tokens: int
    messages: list[dict[str, Any]]
    cold_retrieval: ColdMemorySearchResult | None = None


class ContextAssembler:
    def __init__(
        self,
        store: SQLiteStore,
        *,
        raw_tail_target_tokens: int = 16_000,
        minimum_raw_tail_tokens: int = 12_000,
        overlap_tokens: int = 3_000,
        context_budget_tokens: int | None = None,
        cold_memory_provider: ColdMemoryProvider | None = None,
        cold_memory_mode: str = "off",
        cold_memory_token_budget: int = 512,
        cold_memory_max_injected: int = 3,
        cold_memory_telemetry: ColdMemoryTelemetrySink | None = None,
    ):
        self.store = store
        self.raw_tail_target_tokens = raw_tail_target_tokens
        self.minimum_raw_tail_tokens = minimum_raw_tail_tokens
        self.overlap_tokens = overlap_tokens
        if cold_memory_mode not in {"off", "shadow", "inject"}:
            raise ValueError("cold_memory_mode must be off, shadow, or inject")
        self.context_budget_tokens = context_budget_tokens
        self.cold_memory_provider = cold_memory_provider
        self.cold_memory_mode = cold_memory_mode
        self.cold_memory_token_budget = cold_memory_token_budget
        self.cold_memory_max_injected = cold_memory_max_injected
        self.cold_memory_telemetry = cold_memory_telemetry

    def _tail(self, thread_id: str) -> list[dict[str, Any]]:
        events = self.store.list_events(thread_id)
        selected: list[dict[str, Any]] = []
        total = 0
        target = self.raw_tail_target_tokens
        for event in reversed(events):
            event_tokens = event["token_count"] or estimate_tokens(event["content"])
            if selected and total + event_tokens > target:
                break
            selected.append(event)
            total += event_tokens
        selected.reverse()
        if total < self.minimum_raw_tail_tokens and len(events) > len(selected):
            for event in reversed(events[: -len(selected)] if selected else events):
                selected.insert(0, event)
                total += event["token_count"] or estimate_tokens(event["content"])
                if total >= self.minimum_raw_tail_tokens:
                    break
        return selected

    @staticmethod
    def _render_events(events: list[dict[str, Any]]) -> str:
        rendered: list[str] = []
        for event in events:
            role = event["role"] or event["event_type"]
            rendered.append(f"[{event['id']} seq={event['sequence']}] {role}\n{event['content']}")
        return "\n\n".join(rendered)

    def assemble(
        self,
        thread_id: str,
        request_messages: list[dict[str, Any]],
        *,
        project_id: str | None = None,
    ) -> ContextSnapshot:
        active = self.store.get_active_capsule(thread_id)
        tail = self._tail(thread_id)
        messages = list(request_messages)
        prefix: list[dict[str, Any]] = []
        if active:
            prefix.append(
                {
                    "role": "system",
                    "content": (
                        "PERSISTENT MEMORY CAPSULE\n"
                        f"capsule_id={active['id']}\n"
                        f"covered_through={active['covered_end_event_id']}\n\n"
                        f"{active['content']}"
                    ),
                }
            )
        if tail:
            prefix.append(
                {
                    "role": "system",
                    "content": (
                        "RECENT RAW EVENT TAIL (authoritative verbatim context)\n\n"
                        + self._render_events(tail)
                    ),
                }
            )
        request_tokens = sum(
            estimate_tokens(str(message.get("content", "")))
            for message in messages
        )
        capsule_tokens = estimate_tokens(active["content"]) if active else 0
        tail_tokens = sum(
            event["token_count"] or estimate_tokens(event["content"])
            for event in tail
        )
        cold_result: ColdMemorySearchResult | None = None
        cold_tokens = 0
        if (
            self.cold_memory_provider is not None
            and self.cold_memory_mode != "off"
            and project_id is not None
        ):
            latest_tool_result = next(
                (
                    str(event["content"])
                    for event in reversed(self.store.list_events(thread_id))
                    if event["event_type"] == "tool_result"
                    or event["role"] == "tool"
                ),
                None,
            )
            query_trace = build_retrieval_query_trace(
                messages,
                active_content=active["content"] if active else None,
                latest_tool_result=latest_tool_result,
            )
            query = query_trace.constructed_query
            hot_tokens = capsule_tokens + tail_tokens + request_tokens
            available = self.cold_memory_token_budget
            if self.context_budget_tokens is not None:
                available = min(
                    available,
                    max(0, self.context_budget_tokens - hot_tokens),
                )
            retrieval_started = time.perf_counter()
            try:
                cold_result = self.cold_memory_provider.retrieve(
                    project_id=project_id,
                    thread_id=thread_id,
                    query=query,
                    token_budget=available,
                    max_injected=self.cold_memory_max_injected,
                    mode=self.cold_memory_mode,
                    query_trace=query_trace,
                )
            except Exception as error:
                cold_result = ColdMemorySearchResult(
                    query=query,
                    status="failed",
                    error=str(error)[:240],
                    latency_ms=(time.perf_counter() - retrieval_started) * 1000,
                    fail_open=True,
                    query_hash=content_hash(query),
                    query_preview=" ".join(query.split())[:240],
                    input_preview=" ".join(query_trace.latest_user.split())[:240],
                    source_previews=(
                        ("user", " ".join(query_trace.latest_user.split())[:240]),
                        ("tool", " ".join(query_trace.latest_tool.split())[:240]),
                        ("active", " ".join(query_trace.active_content.split())[:240]),
                    ),
                    constructed_query=query_trace.constructed_query,
                    input_terms=query_trace.input_terms,
                    identifier_terms=query_trace.identifier_terms,
                    ordinary_terms=query_trace.ordinary_terms,
                    query_construction_ms=query_trace.construction_ms,
                    error_category="provider_exception",
                )
                try:
                    telemetry = self.cold_memory_telemetry
                    if telemetry is not None:
                        run_id = telemetry.record_cold_retrieval_run(
                            project_id=project_id,
                            thread_id=thread_id,
                            query=query,
                            candidate_ids=(),
                            scores=(),
                            would_inject_ids=(),
                            mode=self.cold_memory_mode,
                            latency_ms=cold_result.latency_ms,
                            status=cold_result.status,
                            error=cold_result.error,
                            attempted=True,
                            fail_open=True,
                            query_hash=cold_result.query_hash,
                            query_preview=cold_result.query_preview,
                            input_preview=cold_result.input_preview,
                            source_previews=dict(cold_result.source_previews),
                            constructed_query=cold_result.constructed_query,
                            input_terms=cold_result.input_terms,
                            identifier_terms=cold_result.identifier_terms,
                            ordinary_terms=cold_result.ordinary_terms,
                            query_construction_ms=cold_result.query_construction_ms,
                            total_ms=cold_result.total_ms,
                            error_category=cold_result.error_category,
                        )
                    else:
                        run_id = self.store.record_cold_retrieval_run(
                            project_id=project_id,
                            thread_id=thread_id,
                            query=query,
                            candidate_ids=(),
                            scores=(),
                            would_inject_ids=(),
                            mode=self.cold_memory_mode,
                            latency_ms=cold_result.latency_ms,
                            status=cold_result.status,
                            error=cold_result.error,
                            attempted=True,
                            fail_open=True,
                            query_hash=cold_result.query_hash,
                            query_preview=cold_result.query_preview,
                            input_preview=cold_result.input_preview,
                            source_previews=dict(cold_result.source_previews),
                            constructed_query=cold_result.constructed_query,
                            input_terms=cold_result.input_terms,
                            identifier_terms=cold_result.identifier_terms,
                            ordinary_terms=cold_result.ordinary_terms,
                            query_construction_ms=cold_result.query_construction_ms,
                            total_ms=cold_result.total_ms,
                            error_category=cold_result.error_category,
                        )
                    cold_result = replace(cold_result, telemetry_run_id=run_id)
                except Exception:
                    # A provider failure and its telemetry failure are both
                    # fail-open sidecar conditions.
                    pass
            if self.cold_memory_mode == "inject" and cold_result.would_inject:
                injectable = list(cold_result.would_inject)
                while injectable and estimate_tokens(
                    self._render_cold_memories(tuple(injectable))
                ) > available:
                    injectable.pop()
                if injectable:
                    cold_result = replace(
                        cold_result,
                        would_inject=tuple(injectable),
                    )
                else:
                    cold_result = replace(cold_result, would_inject=())
            if self.cold_memory_mode == "inject" and cold_result.would_inject:
                rendered = self._render_cold_memories(cold_result.would_inject)
                prefix.append({"role": "system", "content": rendered})
                cold_tokens = estimate_tokens(rendered)
                try:
                    if self.cold_memory_telemetry is not None:
                        self.cold_memory_telemetry.record_memory_retrieval(
                            memory_ids=(),
                            injected_ids=tuple(
                                hit.memory_id for hit in cold_result.would_inject
                            ),
                        )
                    else:
                        self.store.record_memory_retrieval(
                            memory_ids=(),
                            injected_ids=tuple(
                                hit.memory_id for hit in cold_result.would_inject
                            ),
                        )
                except Exception:
                    # Reinforcement is optional and must not affect assembly.
                    pass
                cold_result = replace(
                    cold_result,
                    injected_count=len(cold_result.would_inject),
                    injected_token_estimate=cold_tokens,
                )
                if cold_result.telemetry_run_id:
                    try:
                        if self.cold_memory_telemetry is not None:
                            self.cold_memory_telemetry.update_cold_retrieval_run(
                                cold_result.telemetry_run_id,
                                injected_ids=tuple(
                                    hit.memory_id for hit in cold_result.would_inject
                                ),
                                injected_token_estimate=cold_tokens,
                            )
                        else:
                            self.store.update_cold_retrieval_run(
                                cold_result.telemetry_run_id,
                                injected_ids=tuple(
                                    hit.memory_id for hit in cold_result.would_inject
                                ),
                                injected_token_estimate=cold_tokens,
                            )
                    except Exception:
                        # Telemetry remains non-authoritative and fail-open.
                        pass
        system_messages = [message for message in messages if message.get("role") in {"system", "developer"}]
        other_messages = [message for message in messages if message.get("role") not in {"system", "developer"}]
        assembled = system_messages + prefix + other_messages
        return ContextSnapshot(
            active_capsule_id=active["id"] if active else None,
            raw_tail_event_ids=tuple(event["id"] for event in tail),
            capsule_tokens=capsule_tokens,
            raw_tail_tokens=tail_tokens,
            request_tokens=request_tokens,
            cold_memory_tokens=cold_tokens,
            estimated_tokens=capsule_tokens + tail_tokens + request_tokens + cold_tokens,
            messages=assembled,
            cold_retrieval=cold_result,
        )

    @staticmethod
    def _render_cold_memories(memories: tuple[Any, ...]) -> str:
        rendered = ["COLD RETRIEVED MEMORY (non-authoritative context)"]
        for memory in memories:
            rendered.append(
                f"\n[{memory.memory_id} type={memory.memory_type} score={memory.score:.3f}]\n"
                f"{memory.content}"
            )
        return "\n".join(rendered)
