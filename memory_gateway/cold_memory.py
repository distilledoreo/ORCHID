from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, replace
from typing import Any, Protocol

from .db import SQLiteStore, content_hash
from .cold_telemetry import ColdMemoryTelemetrySink


_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_./:-]*")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "be",
    "but",
    "by",
    "can",
    "do",
    "fix",
    "for",
    "from",
    "have",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "or",
    "please",
    "the",
    "that",
    "this",
    "to",
    "was",
    "what",
    "with",
    "why",
    "you",
}
_RANKING_STOPWORDS = _STOPWORDS | {
    "after",
    "also",
    "at",
    "does",
    "during",
    "each",
    "every",
    "happens",
    "its",
    "now",
    "over",
    "still",
    "under",
    "when",
    "which",
}


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _terms(text: str, *, limit: int = 48) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for raw in _TOKEN_RE.findall(text):
        token = raw.strip("._:/-").lower()
        if len(token) < 2 or token in _STOPWORDS or token in seen:
            continue
        seen.add(token)
        terms.append(token)
        if len(terms) >= limit:
            break
    return terms


def _expanded_terms(text: str, *, limit: int = 256) -> list[str]:
    """Return lexical terms plus deterministic identifier components.

    This is used only by the Phase 1.2 ranking policy. Query construction and
    the FTS MATCH expression remain unchanged, so identifier decomposition
    cannot broaden the retrieval candidate set.
    """

    terms: list[str] = []
    seen: set[str] = set()
    for raw in _TOKEN_RE.findall(text):
        pieces = [raw]
        if any(marker in raw for marker in ("_", ".", "/", ":", "-")):
            pieces.extend(part for part in re.split(r"[_./:-]+", raw) if part)
        for piece in pieces:
            token = piece.strip("._:/-").lower()
            if len(token) < 2 or token in _RANKING_STOPWORDS or token in seen:
                continue
            seen.add(token)
            terms.append(token)
            if len(terms) >= limit:
                return terms
    return terms


def _preview(text: str, limit: int = 240) -> str:
    return " ".join(text.split())[:limit]


@dataclass(frozen=True)
class RetrievalQueryTrace:
    """Bounded, deterministic query-construction evidence for Phase 1.1."""

    latest_user: str = ""
    latest_tool: str = ""
    active_content: str = ""
    input_terms: tuple[str, ...] = ()
    identifier_terms: tuple[str, ...] = ()
    ordinary_terms: tuple[str, ...] = ()
    constructed_query: str = ""
    construction_ms: float = 0.0


def build_retrieval_query_trace(
    request_messages: list[dict[str, Any]],
    *,
    active_content: str | None = None,
    latest_tool_result: str | None = None,
) -> RetrievalQueryTrace:
    started = time.perf_counter_ns()
    latest_user = next(
        (
            str(message.get("content", ""))
            for message in reversed(request_messages)
            if message.get("role") == "user" and message.get("content")
        ),
        "",
    )
    latest_tool = latest_tool_result or next(
        (
            str(message.get("content", ""))
            for message in reversed(request_messages)
            if message.get("role") == "tool" and message.get("content")
        ),
        "",
    )
    # Identifiers are deliberately first: exact names are the main V1 win for
    # coding retrieval. Natural-language terms fill the remaining budget.
    signals = [latest_user, latest_tool, active_content or ""]
    identifiers: list[str] = []
    ordinary: list[str] = []
    seen: set[str] = set()
    for signal in signals:
        for raw in _TOKEN_RE.findall(signal):
            token = raw.strip("._:/-").lower()
            if len(token) < 2 or token in _STOPWORDS or token in seen:
                continue
            seen.add(token)
            if any(marker in raw for marker in ("_", ".", "/", ":", "-")):
                identifiers.append(token)
            else:
                ordinary.append(token)
    constructed_query = " ".join(identifiers[:20] + ordinary[:28])
    return RetrievalQueryTrace(
        latest_user=latest_user,
        latest_tool=latest_tool,
        active_content=active_content or "",
        input_terms=tuple(_terms(latest_user, limit=96)),
        identifier_terms=tuple(identifiers[:20]),
        ordinary_terms=tuple(ordinary[:28]),
        constructed_query=constructed_query,
        construction_ms=(time.perf_counter_ns() - started) / 1_000_000,
    )


def build_retrieval_query(
    request_messages: list[dict[str, Any]],
    *,
    active_content: str | None = None,
    latest_tool_result: str | None = None,
) -> str:
    """Build a bounded, model-free query from signals already in the gateway."""
    return build_retrieval_query_trace(
        request_messages,
        active_content=active_content,
        latest_tool_result=latest_tool_result,
    ).constructed_query


def _fts_query(query: str) -> str:
    terms = _terms(query)
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)


@dataclass(frozen=True)
class ColdMemoryHit:
    memory_id: str
    content: str
    memory_type: str
    importance: float
    activation_score: float
    bm25_score: float
    score: float
    created_at: str


@dataclass(frozen=True)
class ColdMemoryRankingPolicy:
    """Deterministic ranking and injection policy for lexical retrieval."""

    name: str
    minimum_score: float
    minimum_margin: float = 0.0
    secondary_score_gap: float | None = None
    fts_rank_weight: float = 0.0
    lexical_weight: float = 0.0
    identifier_weight: float = 0.0
    distinctive_weight: float = 0.0
    activation_weight: float = 0.0
    lexical_saturation: int = 3
    require_identifier_coverage: bool = False
    minimum_identifier_coverage: float = 1.0
    minimum_lexical_overlap: int = 0
    minimum_distinctive_overlap: int = 0


BASELINE_RANKING_POLICY = ColdMemoryRankingPolicy(
    name="phase1_baseline",
    minimum_score=0.35,
)

# Phase 1.2 calibration keeps retrieval lexical and deliberately conservative:
# FTS rank is treated as relevance (rank 1 is strongest), exact identifiers
# must be covered when present, and ordinary-language matches need two terms.
CALIBRATED_RANKING_POLICY = ColdMemoryRankingPolicy(
    name="phase1_2_calibrated",
    minimum_score=0.60,
    minimum_margin=0.05,
    secondary_score_gap=0.10,
    fts_rank_weight=0.40,
    lexical_weight=0.15,
    identifier_weight=0.15,
    distinctive_weight=0.25,
    activation_weight=0.05,
    lexical_saturation=3,
    require_identifier_coverage=True,
    minimum_identifier_coverage=0.66,
    minimum_lexical_overlap=2,
    minimum_distinctive_overlap=1,
)


@dataclass(frozen=True)
class ColdMemorySearchResult:
    query: str
    candidates: tuple[ColdMemoryHit, ...] = ()
    would_inject: tuple[ColdMemoryHit, ...] = ()
    latency_ms: float = 0.0
    status: str = "ok"
    error: str | None = None
    attempted: bool = True
    timed_out: bool = False
    fail_open: bool = False
    query_hash: str = ""
    query_preview: str = ""
    exact_candidate_count: int = 0
    lexical_candidate_count: int = 0
    unique_candidate_count: int = 0
    threshold_candidate_count: int = 0
    would_inject_count: int = 0
    injected_count: int = 0
    retrieved_token_estimate: int = 0
    injected_token_estimate: int = 0
    telemetry_run_id: str | None = None
    error_category: str | None = None
    input_preview: str = ""
    source_previews: tuple[tuple[str, str], ...] = ()
    constructed_query: str = ""
    input_terms: tuple[str, ...] = ()
    identifier_terms: tuple[str, ...] = ()
    ordinary_terms: tuple[str, ...] = ()
    fts_query: str = ""
    raw_fts_candidates: tuple[dict[str, Any], ...] = ()
    ranking_details: tuple[dict[str, Any], ...] = ()
    query_construction_ms: float = 0.0
    db_checkout_ms: float = 0.0
    fts_ms: float = 0.0
    ranking_ms: float = 0.0
    token_budget_ms: float = 0.0
    telemetry_ms: float = 0.0
    total_ms: float = 0.0


class ColdMemoryProvider(Protocol):
    def retrieve(
        self,
        *,
        project_id: str,
        thread_id: str,
        query: str,
        token_budget: int,
        max_injected: int,
        mode: str,
        query_trace: RetrievalQueryTrace | None = None,
    ) -> ColdMemorySearchResult:
        ...


class FTS5ColdMemoryRetriever:
    """Fail-open lexical retrieval sidecar.

    Dense vectors and fusion are intentionally not part of this baseline. The
    public interface leaves room for adding them without changing assembly.
    """

    def __init__(
        self,
        store: SQLiteStore,
        *,
        candidate_limit: int = 20,
        timeout_ms: int = 50,
        minimum_score: float = 0.35,
        ranking_policy: ColdMemoryRankingPolicy | None = None,
        telemetry_sink: ColdMemoryTelemetrySink | None = None,
    ):
        self.store = store
        self.candidate_limit = candidate_limit
        self.timeout_ms = timeout_ms
        self.ranking_policy = ranking_policy or BASELINE_RANKING_POLICY
        self.telemetry_sink = telemetry_sink
        self.minimum_score = (
            self.ranking_policy.minimum_score
            if ranking_policy is not None
            else minimum_score
        )

    def retrieve(
        self,
        *,
        project_id: str,
        thread_id: str,
        query: str,
        token_budget: int,
        max_injected: int,
        mode: str,
        query_trace: RetrievalQueryTrace | None = None,
    ) -> ColdMemorySearchResult:
        started = time.perf_counter()
        trace = query_trace or RetrievalQueryTrace(
            latest_user=query,
            input_terms=tuple(_terms(query, limit=96)),
            identifier_terms=tuple(
                token
                for raw in _TOKEN_RE.findall(query)
                if any(marker in raw for marker in ("_", ".", "/", ":", "-"))
                for token in _terms(raw, limit=8)
            ),
            ordinary_terms=tuple(_terms(query)),
            constructed_query=query,
        )
        query_hash = content_hash(query)
        query_preview = _preview(query)
        fts_query = _fts_query(query)
        if not fts_query:
            result = ColdMemorySearchResult(
                query=query,
                latency_ms=(time.perf_counter() - started) * 1000,
                status="empty",
                query_hash=query_hash,
                query_preview=query_preview,
                input_preview=_preview(trace.latest_user),
                source_previews=(
                    ("user", _preview(trace.latest_user)),
                    ("tool", _preview(trace.latest_tool)),
                    ("active", _preview(trace.active_content)),
                ),
                constructed_query=trace.constructed_query or query,
                input_terms=trace.input_terms,
                identifier_terms=trace.identifier_terms,
                ordinary_terms=trace.ordinary_terms,
                fts_query=fts_query,
                query_construction_ms=trace.construction_ms,
                total_ms=(time.perf_counter() - started) * 1000,
            )
            return self._logged(project_id, thread_id, result, mode)
        stage_timings: dict[str, float] = {}
        raw_rows: list[dict[str, Any]] = []
        try:
            raw_rows = self.store.search_long_term_memories(
                project_id=project_id,
                thread_id=thread_id,
                fts_query=fts_query,
                limit=self.candidate_limit,
                timeout_seconds=self.timeout_ms / 1000,
                timings=stage_timings,
            )
            query_terms = set(_terms(query))
            identifier_terms = set(trace.identifier_terms) or {
                token
                for raw in _TOKEN_RE.findall(query)
                if any(marker in raw for marker in ("_", ".", "/", ":", "-"))
                for token in _terms(raw, limit=8)
            }
            if self.ranking_policy.name == BASELINE_RANKING_POLICY.name:
                rank_query_terms = query_terms
                distinctive_terms: set[str] = set()
            else:
                rank_query_terms = set(_expanded_terms(query))
                candidate_term_sets = [
                    set(_expanded_terms(f"{row['content']} {row['memory_type']}"))
                    for row in raw_rows
                ]
                term_frequency: dict[str, int] = {}
                for terms in candidate_term_sets:
                    for term in rank_query_terms.intersection(terms):
                        term_frequency[term] = term_frequency.get(term, 0) + 1
                distinctive_terms = {
                    term for term, frequency in term_frequency.items() if frequency <= 2
                }
            ranking_started = time.perf_counter()
            ranked = [
                self._hit_with_detail(
                    row,
                    rank_query_terms,
                    identifier_terms,
                    fts_rank=index + 1,
                    policy=self.ranking_policy,
                    distinctive_terms=distinctive_terms,
                )
                for index, row in enumerate(raw_rows)
            ]
            ranked.sort(key=lambda item: (-item[0].score, item[0].created_at, item[0].memory_id))
            candidates = tuple(item[0] for item in ranked)
            ranking_details = [item[1] for item in ranked]
            stage_timings["ranking_ms"] = (time.perf_counter() - ranking_started) * 1000
            eligible_ids: set[str] = set()
            top_margin_pass = (
                self.ranking_policy.minimum_margin <= 0
                or len(candidates) < 2
                or candidates[0].score - candidates[1].score
                >= self.ranking_policy.minimum_margin
            )
            for index, (detail, hit) in enumerate(zip(ranking_details, candidates)):
                next_score = (
                    candidates[index + 1].score
                    if index + 1 < len(candidates)
                    else None
                )
                margin = hit.score - next_score if next_score is not None else None
                margin_pass = (
                    self.ranking_policy.minimum_margin <= 0
                    or next_score is None
                    or margin is not None
                    and margin >= self.ranking_policy.minimum_margin
                )
                top_score_gap = candidates[0].score - hit.score if candidates else 0.0
                secondary_pass = (
                    self.ranking_policy.secondary_score_gap is None
                    or index == 0
                    or top_score_gap <= self.ranking_policy.secondary_score_gap
                )
                detail["margin_to_next"] = round(margin, 6) if margin is not None else None
                detail["margin_pass"] = margin_pass
                detail["top_margin_pass"] = top_margin_pass
                detail["top_score_gap"] = round(top_score_gap, 6)
                detail["secondary_pass"] = secondary_pass
                threshold_pass = hit.score >= self.minimum_score
                if (
                    threshold_pass
                    and detail.get("evidence_gate", True)
                    and margin_pass
                    and top_margin_pass
                    and secondary_pass
                ):
                    eligible_ids.add(hit.memory_id)
            budget_started = time.perf_counter()
            would_inject = self._within_budget(
                candidates,
                token_budget=token_budget,
                max_injected=max_injected,
                eligible_ids=eligible_ids,
            )
            stage_timings["token_budget_ms"] = (time.perf_counter() - budget_started) * 1000
            selected_ids = {hit.memory_id for hit in would_inject}
            for detail, hit in zip(ranking_details, candidates):
                threshold_pass = hit.score >= self.minimum_score
                evidence_pass = bool(detail.get("evidence_gate", True))
                margin_pass = bool(detail.get("margin_pass", True))
                detail["threshold_pass"] = threshold_pass
                secondary_pass = bool(detail.get("secondary_pass", True))
                detail["decision"] = (
                    "would_inject"
                    if hit.memory_id in selected_ids
                    else "below_threshold"
                    if not threshold_pass
                    else "insufficient_evidence"
                    if not evidence_pass
                    else "insufficient_margin"
                    if not margin_pass or not top_margin_pass or not secondary_pass
                    else "budget_or_max_injected"
                )
            exact_count = sum(
                bool(identifier_terms.intersection(_terms(hit.content, limit=256)))
                for hit in candidates
            )
            total_ms = (time.perf_counter() - started) * 1000
            result = ColdMemorySearchResult(
                query=query,
                candidates=candidates,
                would_inject=would_inject,
                latency_ms=total_ms,
                status="ok" if candidates else "no_match",
                query_hash=query_hash,
                query_preview=query_preview,
                exact_candidate_count=exact_count,
                lexical_candidate_count=len(candidates),
                unique_candidate_count=len({hit.memory_id for hit in candidates}),
                threshold_candidate_count=sum(
                    hit.score >= self.minimum_score for hit in candidates
                ),
                would_inject_count=len(would_inject),
                retrieved_token_estimate=sum(
                    estimate_tokens(hit.content) for hit in candidates
                ),
                input_preview=_preview(trace.latest_user),
                source_previews=(
                    ("user", _preview(trace.latest_user)),
                    ("tool", _preview(trace.latest_tool)),
                    ("active", _preview(trace.active_content)),
                ),
                constructed_query=trace.constructed_query or query,
                input_terms=trace.input_terms,
                identifier_terms=trace.identifier_terms,
                ordinary_terms=trace.ordinary_terms,
                fts_query=fts_query,
                raw_fts_candidates=tuple(
                    {
                        "memory_id": str(row["id"]),
                        "bm25_score": float(row.get("bm25_score") or 0.0),
                        "fts_rank": index + 1,
                    }
                    for index, row in enumerate(raw_rows)
                ),
                ranking_details=tuple(ranking_details),
                query_construction_ms=trace.construction_ms,
                db_checkout_ms=stage_timings.get("db_checkout_ms", 0.0),
                fts_ms=stage_timings.get("fts_ms", 0.0),
                ranking_ms=stage_timings.get("ranking_ms", 0.0),
                token_budget_ms=stage_timings.get("token_budget_ms", 0.0),
                total_ms=total_ms,
            )
            try:
                if self.telemetry_sink is not None:
                    self.telemetry_sink.record_memory_retrieval(
                        memory_ids=tuple(hit.memory_id for hit in candidates),
                    )
                else:
                    self.store.record_memory_retrieval(
                        memory_ids=tuple(hit.memory_id for hit in candidates),
                    )
            except Exception:
                # Reinforcement is advisory and must not turn a valid search
                # into a fail-open result.
                result = replace(
                    result,
                    error_category="reinforcement_write",
                )
        except Exception as error:
            elapsed_ms = (time.perf_counter() - started) * 1000
            timed_out = isinstance(error, TimeoutError) or (
                "interrupted" in str(error).lower()
                and elapsed_ms >= min(float(self.timeout_ms), 1.0)
            )
            error_category = "timeout" if timed_out else self._error_category(error)
            result = ColdMemorySearchResult(
                query=query,
                latency_ms=(time.perf_counter() - started) * 1000,
                status="timeout" if timed_out else "failed",
                error=str(error)[:240],
                attempted=True,
                timed_out=timed_out,
                fail_open=True,
                query_hash=query_hash,
                query_preview=query_preview,
                error_category=error_category,
                input_preview=_preview(trace.latest_user),
                source_previews=(
                    ("user", _preview(trace.latest_user)),
                    ("tool", _preview(trace.latest_tool)),
                    ("active", _preview(trace.active_content)),
                ),
                constructed_query=trace.constructed_query or query,
                input_terms=trace.input_terms,
                identifier_terms=trace.identifier_terms,
                ordinary_terms=trace.ordinary_terms,
                fts_query=fts_query,
                query_construction_ms=trace.construction_ms,
                db_checkout_ms=stage_timings.get("db_checkout_ms", 0.0),
                fts_ms=stage_timings.get("fts_ms", 0.0),
                total_ms=elapsed_ms,
            )
        return self._logged(project_id, thread_id, result, mode)

    @staticmethod
    def _error_category(error: Exception) -> str:
        message = str(error).lower()
        if (
            isinstance(error, TimeoutError)
            or "timed out" in message
            or "deadline" in message
            or "interrupted" in message
        ):
            return "timeout"
        if "locked" in message or "busy" in message:
            return "database_locked"
        if isinstance(error, (KeyError, TypeError, ValueError)):
            return "malformed_row"
        if isinstance(error, sqlite3.Error) or "fts" in message or "no such table" in message:
            return "fts_error"
        return "internal_exception"

    @staticmethod
    def _hit(row: dict[str, Any], query_terms: set[str]) -> ColdMemoryHit:
        return FTS5ColdMemoryRetriever._hit_with_detail(row, query_terms)[0]

    @staticmethod
    def _hit_with_detail(
        row: dict[str, Any],
        query_terms: set[str],
        identifier_terms: set[str] | None = None,
        *,
        fts_rank: int = 1,
        policy: ColdMemoryRankingPolicy = BASELINE_RANKING_POLICY,
        distinctive_terms: set[str] | None = None,
    ) -> tuple[ColdMemoryHit, dict[str, Any]]:
        content_terms = set(
            _terms(f"{row['content']} {row['memory_type']}", limit=256)
            if policy.name == BASELINE_RANKING_POLICY.name
            else _expanded_terms(f"{row['content']} {row['memory_type']}", limit=256)
        )
        strong_identifier_terms = {
            term
            for term in identifier_terms or set()
            if any(marker in term for marker in ("_", ".", "/", ":", "-"))
        }
        identifier_overlap = len(strong_identifier_terms.intersection(content_terms))
        lexical_overlap = len(query_terms.intersection(content_terms))
        distinctive_overlap = len(
            query_terms.intersection(content_terms).intersection(distinctive_terms or set())
        )
        exact_ratio = (
            lexical_overlap / len(query_terms)
            if query_terms
            else 0.0
        )
        bm25_score = float(row.get("bm25_score") or 0.0)
        bm25_component = 1 / (1 + abs(bm25_score))
        activation_prior = min(
            1.0,
            max(float(row["activation_score"]), float(row["importance"])),
        )
        if policy.name == BASELINE_RANKING_POLICY.name:
            # Preserve the Phase 1 score exactly for the comparison baseline.
            score = min(
                1.0,
                0.75 * exact_ratio + 0.2 * bm25_component + 0.05 * activation_prior,
            )
            fts_rank_quality = bm25_component
            lexical_strength = exact_ratio
            identifier_coverage = (
                identifier_overlap / len(identifier_terms)
                if identifier_terms
                else 0.0
            )
            evidence_gate = True
        else:
            fts_rank_quality = 1 / max(1, fts_rank)
            lexical_strength = min(
                1.0,
                lexical_overlap / max(1, policy.lexical_saturation),
            )
            identifier_coverage = (
                identifier_overlap / len(strong_identifier_terms)
                if strong_identifier_terms
                else 0.0
            )
            evidence_gate = (
                identifier_coverage >= policy.minimum_identifier_coverage
                if strong_identifier_terms and policy.require_identifier_coverage
                else (
                    lexical_overlap >= policy.minimum_lexical_overlap
                    and distinctive_overlap >= policy.minimum_distinctive_overlap
                )
            )
            score = min(
                1.0,
                policy.fts_rank_weight * fts_rank_quality
                + policy.lexical_weight * lexical_strength
                + policy.identifier_weight * identifier_coverage
                + policy.distinctive_weight
                * min(
                    1.0,
                    distinctive_overlap / max(1, policy.minimum_distinctive_overlap),
                )
                + policy.activation_weight * activation_prior,
            )
        hit = ColdMemoryHit(
            memory_id=str(row["id"]),
            content=str(row["content"]),
            memory_type=str(row["memory_type"]),
            importance=float(row["importance"]),
            activation_score=float(row["activation_score"]),
            bm25_score=bm25_score,
            score=score,
            created_at=str(row["created_at"]),
        )
        return hit, {
            "memory_id": hit.memory_id,
            "ranking_policy": policy.name,
            "bm25_score": round(bm25_score, 6),
            "fts_rank": fts_rank,
            "fts_rank_quality": round(fts_rank_quality, 6),
            "identifier_overlap": identifier_overlap,
            "strong_identifier_count": len(strong_identifier_terms),
            "identifier_coverage": round(identifier_coverage, 6),
            "lexical_overlap": lexical_overlap,
            "lexical_strength": round(lexical_strength, 6),
            "distinctive_overlap": distinctive_overlap,
            "exact_ratio": round(exact_ratio, 6),
            "bm25_component": round(bm25_component, 6),
            "activation_prior": round(activation_prior, 6),
            "score": round(score, 6),
            "evidence_gate": evidence_gate,
        }

    def _within_budget(
        self,
        candidates: tuple[ColdMemoryHit, ...],
        *,
        token_budget: int,
        max_injected: int,
        eligible_ids: set[str] | None = None,
    ) -> tuple[ColdMemoryHit, ...]:
        if token_budget <= 0 or max_injected <= 0:
            return ()
        selected: list[ColdMemoryHit] = []
        used = 0
        for candidate in candidates:
            if eligible_ids is not None and candidate.memory_id not in eligible_ids:
                continue
            if candidate.score < self.minimum_score:
                continue
            cost = estimate_tokens(candidate.content) + 24
            if used + cost > token_budget:
                continue
            selected.append(candidate)
            used += cost
            if len(selected) >= max_injected:
                break
        return tuple(selected)

    def _logged(
        self,
        project_id: str,
        thread_id: str,
        result: ColdMemorySearchResult,
        mode: str,
    ) -> ColdMemorySearchResult:
        if self.telemetry_sink is not None:
            telemetry_started = time.perf_counter()
            try:
                run_id = self.telemetry_sink.record_cold_retrieval_run(
                    project_id=project_id,
                    thread_id=thread_id,
                    query=result.query,
                    candidate_ids=tuple(hit.memory_id for hit in result.candidates),
                    scores=tuple(hit.score for hit in result.candidates),
                    would_inject_ids=tuple(hit.memory_id for hit in result.would_inject),
                    mode=mode,
                    latency_ms=result.latency_ms,
                    status=result.status,
                    error=result.error,
                    attempted=result.attempted,
                    timed_out=result.timed_out,
                    fail_open=result.fail_open,
                    query_hash=result.query_hash or content_hash(result.query),
                    query_preview=result.query_preview or _preview(result.query),
                    exact_candidate_count=result.exact_candidate_count,
                    lexical_candidate_count=result.lexical_candidate_count,
                    unique_candidate_count=result.unique_candidate_count,
                    threshold_candidate_count=result.threshold_candidate_count,
                    would_inject_count=result.would_inject_count,
                    injected_count=result.injected_count,
                    retrieved_token_estimate=result.retrieved_token_estimate,
                    injected_token_estimate=result.injected_token_estimate,
                    injected_ids=(
                        tuple(hit.memory_id for hit in result.would_inject)
                        if result.injected_count
                        else ()
                    ),
                    error_category=result.error_category,
                    cold_retrieval_ms=result.latency_ms,
                    input_preview=result.input_preview,
                    source_previews=dict(result.source_previews),
                    constructed_query=result.constructed_query or result.query,
                    input_terms=result.input_terms,
                    identifier_terms=result.identifier_terms,
                    ordinary_terms=result.ordinary_terms,
                    fts_query=result.fts_query,
                    raw_fts_candidates=result.raw_fts_candidates,
                    ranking_details=result.ranking_details,
                    query_construction_ms=result.query_construction_ms,
                    db_checkout_ms=result.db_checkout_ms,
                    fts_ms=result.fts_ms,
                    ranking_ms=result.ranking_ms,
                    token_budget_ms=result.token_budget_ms,
                    telemetry_ms=0.0,
                    total_ms=result.total_ms or result.latency_ms,
                )
                enqueue_ms = (time.perf_counter() - telemetry_started) * 1000
                return replace(
                    result,
                    telemetry_run_id=run_id,
                    telemetry_ms=enqueue_ms,
                    total_ms=(result.total_ms or result.latency_ms) + enqueue_ms,
                )
            except Exception:
                # The buffered sink is non-authoritative and fail-open.
                return result
        try:
            telemetry_started = time.perf_counter()
            run_id = self.store.record_cold_retrieval_run(
                project_id=project_id,
                thread_id=thread_id,
                query=result.query,
                candidate_ids=tuple(hit.memory_id for hit in result.candidates),
                scores=tuple(hit.score for hit in result.candidates),
                would_inject_ids=tuple(hit.memory_id for hit in result.would_inject),
                mode=mode,
                latency_ms=result.latency_ms,
                status=result.status,
                error=result.error,
                attempted=result.attempted,
                timed_out=result.timed_out,
                fail_open=result.fail_open,
                query_hash=result.query_hash or content_hash(result.query),
                query_preview=result.query_preview or _preview(result.query),
                exact_candidate_count=result.exact_candidate_count,
                lexical_candidate_count=result.lexical_candidate_count,
                unique_candidate_count=result.unique_candidate_count,
                threshold_candidate_count=result.threshold_candidate_count,
                would_inject_count=result.would_inject_count,
                injected_count=result.injected_count,
                retrieved_token_estimate=result.retrieved_token_estimate,
                injected_token_estimate=result.injected_token_estimate,
                injected_ids=tuple(hit.memory_id for hit in result.would_inject)
                if result.injected_count
                else (),
                error_category=result.error_category,
                cold_retrieval_ms=result.latency_ms,
                input_preview=result.input_preview,
                source_previews=dict(result.source_previews),
                constructed_query=result.constructed_query or result.query,
                input_terms=result.input_terms,
                identifier_terms=result.identifier_terms,
                ordinary_terms=result.ordinary_terms,
                fts_query=result.fts_query,
                raw_fts_candidates=result.raw_fts_candidates,
                ranking_details=result.ranking_details,
                query_construction_ms=result.query_construction_ms,
                db_checkout_ms=result.db_checkout_ms,
                fts_ms=result.fts_ms,
                ranking_ms=result.ranking_ms,
                token_budget_ms=result.token_budget_ms,
                total_ms=result.total_ms or result.latency_ms,
            )
            telemetry_ms = (time.perf_counter() - telemetry_started) * 1000
            return replace(
                result,
                telemetry_run_id=run_id,
                telemetry_ms=telemetry_ms,
                total_ms=(result.total_ms or result.latency_ms) + telemetry_ms,
            )
        except Exception:
            # Observability is also sidecar state. It must not affect hot memory.
            return result
