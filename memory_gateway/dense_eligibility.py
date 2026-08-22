"""Deterministic status/scope eligibility for offline dense experiments.

This module does not connect dense retrieval to gateway context assembly.  It
only centralizes the policy used by Phase 2.3 to distinguish memories that may
be silently injected from memories that remain available to explicit history
search.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from memory_gateway.dense_experiment import DenseMemoryCandidate


DenseSearchMode = Literal["implicit", "explicit"]
IMPLICIT_DENSE_STATUSES = frozenset({"ACTIVE"})
EXPLICIT_DENSE_STATUSES = frozenset({"ACTIVE", "SUPERSEDED"})


def is_dense_memory_eligible(
    memory: Mapping[str, Any],
    *,
    project_id: str,
    thread_id: str,
    mode: DenseSearchMode = "implicit",
) -> bool:
    """Return whether a memory can participate in the requested dense path."""

    if mode not in {"implicit", "explicit"}:
        raise ValueError(f"unsupported dense search mode: {mode}")
    if str(memory.get("project_id", "")) != project_id:
        return False
    if str(memory.get("thread_id", "")) != thread_id:
        return False
    statuses = (
        IMPLICIT_DENSE_STATUSES
        if mode == "implicit"
        else EXPLICIT_DENSE_STATUSES
    )
    return str(memory.get("status", "")).upper() in statuses


def filter_dense_candidates(
    candidates: Sequence[DenseMemoryCandidate],
    memory_by_id: Mapping[str, Mapping[str, Any]],
    *,
    project_id: str,
    thread_id: str,
    mode: DenseSearchMode = "implicit",
) -> tuple[DenseMemoryCandidate, ...]:
    """Filter ranked candidates without changing their score or ordering."""

    return tuple(
        candidate
        for candidate in candidates
        if (
            (memory := memory_by_id.get(candidate.memory_id)) is not None
            and is_dense_memory_eligible(
                memory,
                project_id=project_id,
                thread_id=thread_id,
                mode=mode,
            )
        )
    )
