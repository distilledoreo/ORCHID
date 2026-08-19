from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .db import SQLiteStore


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
    estimated_tokens: int
    messages: list[dict[str, Any]]


class ContextAssembler:
    def __init__(
        self,
        store: SQLiteStore,
        *,
        raw_tail_target_tokens: int = 16_000,
        minimum_raw_tail_tokens: int = 12_000,
        overlap_tokens: int = 3_000,
    ):
        self.store = store
        self.raw_tail_target_tokens = raw_tail_target_tokens
        self.minimum_raw_tail_tokens = minimum_raw_tail_tokens
        self.overlap_tokens = overlap_tokens

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
        system_messages = [message for message in messages if message.get("role") in {"system", "developer"}]
        other_messages = [message for message in messages if message.get("role") not in {"system", "developer"}]
        assembled = system_messages + prefix + other_messages
        capsule_tokens = estimate_tokens(active["content"]) if active else 0
        tail_tokens = sum(event["token_count"] or estimate_tokens(event["content"]) for event in tail)
        request_tokens = sum(estimate_tokens(str(message.get("content", ""))) for message in messages)
        return ContextSnapshot(
            active_capsule_id=active["id"] if active else None,
            raw_tail_event_ids=tuple(event["id"] for event in tail),
            capsule_tokens=capsule_tokens,
            raw_tail_tokens=tail_tokens,
            request_tokens=request_tokens,
            estimated_tokens=capsule_tokens + tail_tokens + request_tokens,
            messages=assembled,
        )
