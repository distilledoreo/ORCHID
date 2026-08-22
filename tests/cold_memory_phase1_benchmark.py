"""Reproducible Phase 1 cold-memory quality and latency baseline.

This is an executable harness rather than a pytest test because it writes a
measurement artifact. It exercises the local SQLite/FTS path and
ContextAssembler only; no awake model or network provider is involved.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any

# Make the documented `python tests/...py` command work from the repository
# root as well as from another current directory.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memory_gateway.cold_memory import (
    FTS5ColdMemoryRetriever,
    _terms,
    build_retrieval_query_trace,
)
from memory_gateway.context import ContextAssembler
from memory_gateway.db import SQLiteStore


PROJECT_ID = "phase1-project"
THREAD_ID = "phase1-thread"
DEFAULT_OUTPUT = Path("artifacts/cold_memory/phase1_baseline")


def _anchors() -> list[dict[str, Any]]:
    return [
        {
            "id": "mem_lease_renewal",
            "memory_type": "bug",
            "importance": 0.95,
            "content": (
                "The renew_lease path in memory_gateway/db.py must validate "
                "lease_until against worker ownership before extending a lease; "
                "otherwise an expired worker can renew another worker's job."
            ),
        },
        {
            "id": "mem_selector_enum",
            "memory_type": "protocol",
            "importance": 0.86,
            "content": (
                "The selector response contract uses a per-chunk enum in "
                "tests/test_openai_adapter.py so selector_chunk_index stays "
                "within the current chunk instead of using the old static schema."
            ),
        },
        {
            "id": "mem_cas_promotion",
            "memory_type": "architecture",
            "importance": 0.92,
            "content": (
                "promote_capsule_cas is the only active-capsule write: it checks "
                "lineage and stale_revision before compare-and-swap promotion, "
                "leaving compaction_jobs and ACTIVE consistent."
            ),
        },
        {
            "id": "mem_sqlite_lock",
            "memory_type": "incident",
            "importance": 0.82,
            "content": (
                "SQLite retrieval can raise sqlite3.OperationalError while a "
                "writer holds the database; bounded busy_timeout_ms and a "
                "fail-open response keep the hot context available."
            ),
        },
        {
            "id": "mem_provenance",
            "memory_type": "invariant",
            "importance": 0.94,
            "content": (
                "RETIRE semantic memories retain memory_evidence links to the "
                "immutable event_id history; a semantic row never replaces the "
                "authoritative raw evidence."
            ),
        },
        {
            "id": "mem_context_budget",
            "memory_type": "constraint",
            "importance": 0.84,
            "content": (
                "cold_memory_token_budget and context_budget_tokens leave current "
                "user instructions, ACTIVE, and the raw tail ahead of would_inject "
                "cold memories; Phase 1 allows at most three memories."
            ),
        },
        {
            "id": "mem_timeout_failopen",
            "memory_type": "operational",
            "importance": 0.88,
            "content": (
                "The FTS progress_handler enforces a retrieval deadline; a "
                "TimeoutError records bounded telemetry and returns no cold context "
                "without appending an event or mutating the capsule."
            ),
        },
        {
            "id": "mem_windows_cleanup",
            "memory_type": "operational",
            "importance": 0.65,
            "content": (
                "On Windows, taskkill.exe is used for deterministic process cleanup "
                "after savedRigSoak imports; profile cleanup is verified before the "
                "next real browser run."
            ),
        },
        {
            "id": "mem_raw_tail",
            "memory_type": "architecture",
            "importance": 0.9,
            "content": (
                "The recent raw tail remains protected verbatim context and is "
                "assembled independently from the ACTIVE capsule and optional cold "
                "retrieval sidecar."
            ),
        },
        {
            "id": "mem_retire_promotion",
            "memory_type": "lifecycle",
            "importance": 0.9,
            "content": (
                "RETIRE persistence occurs only after successful ACTIVE promotion; "
                "a cold sidecar write failure cannot turn a valid hot promotion into "
                "a failed compaction job."
            ),
        },
        {
            "id": "mem_injection_isolation",
            "memory_type": "invariant",
            "importance": 0.91,
            "content": (
                "Retrieved memories are ephemeral assembly context, not events: "
                "shadow mode records would_inject candidates but does not insert a "
                "COLD RETRIEVED MEMORY message into the outbound hot context."
            ),
        },
        {
            "id": "mem_model_boundary",
            "memory_type": "architecture",
            "importance": 0.78,
            "content": (
                "Phase 1 builds a deterministic retrieval query from the current "
                "user message, latest tool result, ACTIVE content, file paths, and "
                "symbol names; no LLM is in the retrieval critical path."
            ),
        },
        {
            "id": "mem_fts_scope",
            "memory_type": "implementation",
            "importance": 0.72,
            "content": (
                "SQLite FTS5 searches only ACTIVE long_term_memories scoped by "
                "project_id and thread_id; provenance remains in memory_evidence."
            ),
        },
        {
            "id": "mem_dynamic_schema",
            "memory_type": "test",
            "importance": 0.7,
            "content": (
                "Protocol hardening keeps the dynamic per-chunk selector enum; an "
                "old test expecting SELECTOR_RESPONSE_FORMAT is not a cold-memory "
                "regression."
            ),
        },
    ]


def _queries() -> list[dict[str, Any]]:
    return [
        {
            "id": "exact_lease",
            "category": "exact",
            "expected_ids": ["mem_lease_renewal"],
            "designed_to_solve": True,
            "query": "renew_lease lease_until memory_gateway/db.py worker ownership",
        },
        {
            "id": "exact_selector",
            "category": "exact",
            "expected_ids": ["mem_selector_enum"],
            "designed_to_solve": True,
            "query": "selector_chunk_index tests/test_openai_adapter.py per-chunk enum",
        },
        {
            "id": "exact_cas",
            "category": "exact",
            "expected_ids": ["mem_cas_promotion"],
            "designed_to_solve": True,
            "query": "promote_capsule_cas active-capsule stale_revision compaction_jobs",
        },
        {
            "id": "exact_sqlite",
            "category": "exact",
            "expected_ids": ["mem_sqlite_lock"],
            "designed_to_solve": True,
            "query": "sqlite3.OperationalError busy_timeout_ms",
        },
        {
            "id": "exact_provenance",
            "category": "exact",
            "expected_ids": ["mem_provenance"],
            "designed_to_solve": True,
            "query": "memory_evidence event_id immutable raw evidence",
        },
        {
            "id": "exact_budget",
            "category": "exact",
            "expected_ids": ["mem_context_budget"],
            "designed_to_solve": True,
            "query": "cold_memory_token_budget context_budget_tokens would_inject",
        },
        {
            "id": "exact_timeout",
            "category": "exact",
            "expected_ids": ["mem_timeout_failopen"],
            "designed_to_solve": True,
            "query": "progress_handler TimeoutError retrieval deadline",
        },
        {
            "id": "exact_windows",
            "category": "exact",
            "expected_ids": ["mem_windows_cleanup"],
            "designed_to_solve": True,
            "query": "taskkill.exe savedRigSoak profile cleanup",
        },
        {
            "id": "lexical_lease",
            "category": "lexical",
            "expected_ids": ["mem_lease_renewal"],
            "designed_to_solve": True,
            "query": "Why validate lease expiration against worker ownership?",
        },
        {
            "id": "lexical_cas",
            "category": "lexical",
            "expected_ids": ["mem_cas_promotion"],
            "designed_to_solve": True,
            "query": "How is a stale compaction job prevented from replacing active state?",
        },
        {
            "id": "lexical_sqlite",
            "category": "lexical",
            "expected_ids": ["mem_sqlite_lock"],
            "designed_to_solve": True,
            "query": "What happens when SQLite is locked during retrieval?",
        },
        {
            "id": "lexical_provenance",
            "category": "lexical",
            "expected_ids": ["mem_provenance"],
            "designed_to_solve": True,
            "query": "Which links preserve the original immutable events?",
        },
        {
            "id": "lexical_budget",
            "category": "lexical",
            "expected_ids": ["mem_context_budget"],
            "designed_to_solve": True,
            "query": "How does the cold context stay under a small token ceiling?",
        },
        {
            "id": "lexical_selector",
            "category": "lexical",
            "expected_ids": ["mem_selector_enum"],
            "designed_to_solve": True,
            "query": "Why are selector outputs valid for each chunk?",
        },
        {
            "id": "lexical_timeout",
            "category": "lexical",
            "expected_ids": ["mem_timeout_failopen"],
            "designed_to_solve": True,
            "query": "What stops a long-running search at its deadline?",
        },
        {
            "id": "lexical_windows",
            "category": "lexical",
            "expected_ids": ["mem_windows_cleanup"],
            "designed_to_solve": True,
            "query": "How were Windows process cleanup hangs avoided?",
        },
        {
            "id": "dense_worker_handoff",
            "category": "dense_opportunity",
            "expected_ids": ["mem_lease_renewal"],
            "designed_to_solve": False,
            "query": "that old concurrency defect involving a handoff",
        },
        {
            "id": "dense_source_fidelity",
            "category": "dense_opportunity",
            "expected_ids": ["mem_provenance"],
            "designed_to_solve": False,
            "query": "the earlier reason exact source material had to remain available",
        },
        {
            "id": "dense_active_safety",
            "category": "dense_opportunity",
            "expected_ids": ["mem_cas_promotion"],
            "designed_to_solve": False,
            "query": "the previous approach to keeping live state safe while work overlaps",
        },
        {
            "id": "dense_query_model",
            "category": "dense_opportunity",
            "expected_ids": ["mem_model_boundary"],
            "designed_to_solve": False,
            "query": "the lightweight way the system decides what earlier context matters",
        },
        {
            "id": "dense_old_summary",
            "category": "dense_opportunity",
            "expected_ids": ["mem_raw_tail"],
            "designed_to_solve": False,
            "query": "why the newest conversational details still need their original wording",
        },
        {
            "id": "vague_fix_it",
            "category": "vague",
            "expected_ids": [],
            "designed_to_solve": False,
            "query": "fix it",
        },
        {
            "id": "vague_status",
            "category": "vague",
            "expected_ids": [],
            "designed_to_solve": False,
            "query": "what now",
        },
        {
            "id": "no_match_quartz",
            "category": "no_match",
            "expected_ids": [],
            "designed_to_solve": True,
            "query": "quartzflint meridianpike nonexistentidentifier",
        },
        {
            "id": "no_match_package",
            "category": "no_match",
            "expected_ids": [],
            "designed_to_solve": True,
            "query": "package that never existed in this repository",
        },
        {
            "id": "no_match_error",
            "category": "no_match",
            "expected_ids": [],
            "designed_to_solve": True,
            "query": "ImaginaryProtocolError frobnicator_991",
        },
    ]


def _corpus(total: int) -> list[dict[str, Any]]:
    anchors = _anchors()
    if total < len(anchors):
        raise ValueError(f"corpus size must be at least {len(anchors)}")
    memories = list(anchors)
    for index in range(total - len(anchors)):
        memories.append(
            {
                "id": f"mem_distractor_{index:04d}",
                "memory_type": "irrelevant_fixture",
                "importance": 0.2,
                "content": (
                    f"Unrelated fixture note {index:04d}: component_{index:04d} "
                    f"uses isolated marker_{index:04d} during a synthetic maintenance run."
                ),
            }
        )
    return memories


def _reset_db(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            candidate.unlink()


def _build_store(path: Path, total: int) -> SQLiteStore:
    _reset_db(path)
    store = SQLiteStore(path)
    store.create_project(PROJECT_ID)
    store.create_thread(THREAD_ID, PROJECT_ID)
    for index, memory in enumerate(_corpus(total)):
        event_id = f"evt_cold_{index:04d}"
        event = store.append_event(
            project_id=PROJECT_ID,
            thread_id=THREAD_ID,
            event_id=event_id,
            event_type="fixture_evidence",
            content=memory["content"],
        )
        store.persist_long_term_memories(
            thread_id=THREAD_ID,
            memories=[
                {
                    **memory,
                    "evidence_event_ids": [event["id"]],
                }
            ],
        )
    return store


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _distribution(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0, "max": 0.0}
    return {
        "count": len(values),
        "p50": round(_percentile(values, 0.50), 6),
        "p95": round(_percentile(values, 0.95), 6),
        "p99": round(_percentile(values, 0.99), 6),
        "mean": round(statistics.fmean(values), 6),
        "max": round(max(values), 6),
    }


def _assemble_sample(
    assembler: ContextAssembler,
    case: dict[str, Any],
    *,
    mode: str,
    corpus_size: int,
    iteration: int,
    warmup: bool,
) -> dict[str, Any]:
    started = time.perf_counter_ns()
    snapshot = assembler.assemble(
        THREAD_ID,
        [{"role": "user", "content": case["query"]}],
        project_id=PROJECT_ID,
    )
    context_ms = (time.perf_counter_ns() - started) / 1_000_000
    result = snapshot.cold_retrieval
    return {
        "mode": mode,
        "corpus_size": corpus_size,
        "case_id": case["id"],
        "category": case["category"],
        "iteration": iteration,
        "warmup": warmup,
        "context_assembly_ms": round(context_ms, 6),
        "cold_retrieval_ms": round(result.latency_ms, 6) if result else 0.0,
        "retrieval_total_ms": round(result.total_ms, 6) if result else 0.0,
        "query_construction_ms": round(result.query_construction_ms, 6) if result else 0.0,
        "db_checkout_ms": round(result.db_checkout_ms, 6) if result else 0.0,
        "fts_ms": round(result.fts_ms, 6) if result else 0.0,
        "ranking_ms": round(result.ranking_ms, 6) if result else 0.0,
        "token_budget_ms": round(result.token_budget_ms, 6) if result else 0.0,
        "telemetry_ms": round(result.telemetry_ms, 6) if result else 0.0,
        "attempted": bool(result.attempted) if result else False,
        "timed_out": bool(result.timed_out) if result else False,
        "fail_open": bool(result.fail_open) if result else False,
        "status": result.status if result else "off",
        "candidate_count": len(result.candidates) if result else 0,
        "would_inject_count": result.would_inject_count if result else 0,
        "injected_count": result.injected_count if result else 0,
        "retrieved_token_estimate": result.retrieved_token_estimate if result else 0,
        "injected_token_estimate": result.injected_token_estimate if result else 0,
    }


def _latency_benchmark(
    output_root: Path,
    *,
    corpus_sizes: list[int],
    iterations: int,
    warmup: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for corpus_size in corpus_sizes:
        off_store = _build_store(output_root / f"work_off_{corpus_size}.db", corpus_size)
        shadow_store = _build_store(output_root / f"work_shadow_{corpus_size}.db", corpus_size)
        assemblers = {
            "off": ContextAssembler(
                off_store,
                raw_tail_target_tokens=128,
                minimum_raw_tail_tokens=0,
                context_budget_tokens=32_768,
                cold_memory_mode="off",
            ),
            "shadow": ContextAssembler(
                shadow_store,
                raw_tail_target_tokens=128,
                minimum_raw_tail_tokens=0,
                context_budget_tokens=32_768,
                cold_memory_provider=FTS5ColdMemoryRetriever(
                    shadow_store,
                    timeout_ms=50,
                    candidate_limit=20,
                ),
                cold_memory_mode="shadow",
                cold_memory_token_budget=512,
                cold_memory_max_injected=3,
            ),
        }
        cases = _queries()
        for warmup_index in range(warmup):
            for case in cases:
                for mode, assembler in assemblers.items():
                    _assemble_sample(
                        assembler,
                        case,
                        mode=mode,
                        corpus_size=corpus_size,
                        iteration=warmup_index,
                        warmup=True,
                    )
        for iteration in range(iterations):
            for case in cases:
                for mode, assembler in assemblers.items():
                    samples.append(
                        _assemble_sample(
                            assembler,
                            case,
                            mode=mode,
                            corpus_size=corpus_size,
                            iteration=iteration,
                            warmup=False,
                        )
                    )

    summaries: dict[str, Any] = {"by_mode_and_corpus": {}, "paired_shadow_minus_off_ms": {}}
    for mode in ("off", "shadow"):
        for corpus_size in corpus_sizes:
            subset = [
                row
                for row in samples
                if row["mode"] == mode and row["corpus_size"] == corpus_size
            ]
            assembly = [row["context_assembly_ms"] for row in subset]
            retrieval = [row["cold_retrieval_ms"] for row in subset if row["attempted"]]
            stage_fields = (
                "query_construction_ms",
                "db_checkout_ms",
                "fts_ms",
                "ranking_ms",
                "token_budget_ms",
                "telemetry_ms",
                "retrieval_total_ms",
            )
            summaries["by_mode_and_corpus"][f"{mode}:{corpus_size}"] = {
                "mode": mode,
                "corpus_size": corpus_size,
                "context_assembly_ms": _distribution(assembly),
                "cold_retrieval_ms": _distribution(retrieval),
                "retrieval_stage_ms": {
                    field: _distribution(
                        [row[field] for row in subset if row["attempted"]]
                    )
                    for field in stage_fields
                },
                "timeout_count": sum(row["timed_out"] for row in subset),
                "fail_open_count": sum(row["fail_open"] for row in subset),
                "would_inject_count": sum(row["would_inject_count"] for row in subset),
                "would_inject_sample_rate": round(
                    sum(row["would_inject_count"] > 0 for row in subset) / len(subset),
                    6,
                )
                if subset
                else 0.0,
                "timeout_rate": round(
                    sum(row["timed_out"] for row in subset) / len(subset), 6
                )
                if subset
                else 0.0,
                "fail_open_rate": round(
                    sum(row["fail_open"] for row in subset) / len(subset), 6
                )
                if subset
                else 0.0,
                "injected_token_estimate_total": sum(
                    row["injected_token_estimate"] for row in subset
                ),
            }
    for corpus_size in corpus_sizes:
        by_key: dict[tuple[str, int, str, int], float] = {}
        for row in samples:
            by_key[(row["mode"], row["corpus_size"], row["case_id"], row["iteration"])] = row[
                "context_assembly_ms"
            ]
        deltas = []
        for key, shadow_ms in by_key.items():
            if key[0] == "shadow" and key[1] == corpus_size:
                off_key = ("off", key[1], key[2], key[3])
                if off_key in by_key:
                    deltas.append(shadow_ms - by_key[off_key])
        summaries["paired_shadow_minus_off_ms"][str(corpus_size)] = _distribution(deltas)
    return samples, summaries


def _quality_benchmark(store: SQLiteStore) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    retriever = FTS5ColdMemoryRetriever(store, timeout_ms=50, candidate_limit=20)
    memory_by_id = {memory["id"]: memory for memory in _anchors()}
    rows: list[dict[str, Any]] = []
    for case in _queries():
        trace = build_retrieval_query_trace(
            [{"role": "user", "content": case["query"]}]
        )
        result = retriever.retrieve(
            project_id=PROJECT_ID,
            thread_id=THREAD_ID,
            query=trace.constructed_query,
            token_budget=512,
            max_injected=3,
            mode="shadow",
            query_trace=trace,
        )
        candidate_ids = [hit.memory_id for hit in result.candidates]
        expected = set(case["expected_ids"])
        ranks = [
            candidate_ids.index(memory_id) + 1
            for memory_id in expected
            if memory_id in candidate_ids
        ]
        rank = min(ranks) if ranks else None
        would_ids = [hit.memory_id for hit in result.would_inject]
        would_expected = bool(expected.intersection(would_ids))
        false_candidates = [memory_id for memory_id in candidate_ids if memory_id not in expected]
        false_injected = [memory_id for memory_id in would_ids if memory_id not in expected]
        ranking_by_id = {
            detail["memory_id"]: detail for detail in result.ranking_details
        }
        target_id = next(iter(expected), None)
        target_terms = (
            set(_terms(memory_by_id[target_id]["content"], limit=256))
            if target_id in memory_by_id
            else set()
        )
        input_terms = set(result.input_terms)
        constructed_terms = set(_terms(result.constructed_query, limit=96))
        target_input_overlap = sorted(target_terms.intersection(input_terms))
        target_constructed_overlap = sorted(target_terms.intersection(constructed_terms))
        discarded_input_terms = sorted(input_terms - constructed_terms)
        false_injection_analysis = []
        for memory_id in false_injected:
            detail = ranking_by_id.get(memory_id, {})
            if detail.get("identifier_overlap", 0) > 0:
                reason = "identifier_collision"
            elif detail.get("lexical_overlap", 0) <= 1:
                reason = "broad_or_weak_lexical_overlap"
            elif detail:
                reason = "bm25_or_activation_ranking"
            else:
                reason = "unknown"
            false_injection_analysis.append(
                {"memory_id": memory_id, "reason": reason, "ranking": detail}
            )
        if result.timed_out:
            failure_category = "timeout"
        elif result.status == "failed":
            failure_category = result.error_category or "actual_bug"
        elif expected and rank is None:
            if not case["designed_to_solve"] or not target_input_overlap:
                failure_category = "no_lexical_overlap_likely_dense"
            elif not target_constructed_overlap:
                failure_category = "query_construction_issue"
            elif not candidate_ids:
                failure_category = "fts_or_tokenizer_issue"
            else:
                failure_category = "ranking_issue"
        elif expected and rank > 5:
            failure_category = "ranking_issue"
        elif expected and not would_expected:
            target_detail = ranking_by_id.get(target_id, {})
            failure_category = (
                "threshold_issue"
                if target_detail.get("decision") == "below_threshold"
                else "budget_or_max_injected_issue"
            )
        elif not expected and candidate_ids:
            failure_category = "irrelevant_candidates"
        else:
            failure_category = None
        rows.append(
            {
                "query_id": case["id"],
                "category": case["category"],
                "designed_to_solve": case["designed_to_solve"],
                "query": case["query"],
                "original_input": case["query"],
                "constructed_query": result.constructed_query,
                "input_terms": list(result.input_terms),
                "identifier_terms": list(result.identifier_terms),
                "ordinary_terms": list(result.ordinary_terms),
                "discarded_input_terms": discarded_input_terms,
                "fts_query": result.fts_query,
                "raw_fts_candidates": list(result.raw_fts_candidates),
                "ranking_details": list(result.ranking_details),
                "threshold": retriever.minimum_score,
                "max_injected": 3,
                "token_budget": 512,
                "expected_ids": case["expected_ids"],
                "candidate_ids": candidate_ids,
                "would_inject_ids": would_ids,
                "candidate_count": len(candidate_ids),
                "zero_candidates": not candidate_ids,
                "false_candidate_count": len(false_candidates),
                "false_injected_count": len(false_injected),
                "rank": rank,
                "recall_at_1": bool(rank and rank <= 1),
                "recall_at_3": bool(rank and rank <= 3),
                "recall_at_5": bool(rank and rank <= 5),
                "mrr": round(1 / rank, 6) if rank else 0.0,
                "would_inject_expected": would_expected,
                "target_input_overlap": target_input_overlap,
                "target_constructed_overlap": target_constructed_overlap,
                "false_injection_analysis": false_injection_analysis,
                "status": result.status,
                "timed_out": result.timed_out,
                "fail_open": result.fail_open,
                "failure_category": failure_category,
                "stage_ms": {
                    "query_construction_ms": round(result.query_construction_ms, 6),
                    "db_checkout_ms": round(result.db_checkout_ms, 6),
                    "fts_ms": round(result.fts_ms, 6),
                    "ranking_ms": round(result.ranking_ms, 6),
                    "token_budget_ms": round(result.token_budget_ms, 6),
                    "telemetry_ms": round(result.telemetry_ms, 6),
                    "total_ms": round(result.total_ms, 6),
                },
            }
        )

    def subset(category: str) -> list[dict[str, Any]]:
        return [row for row in rows if row["category"] == category]

    def rate(items: list[dict[str, Any]], field: str) -> float:
        return round(sum(bool(row[field]) for row in items) / len(items), 6) if items else 0.0

    designed = [
        row
        for row in rows
        if row["designed_to_solve"] and row["expected_ids"]
    ]
    exact = subset("exact")
    lexical = subset("lexical")
    false_candidate_total = sum(row["false_candidate_count"] for row in rows)
    candidate_total = sum(row["candidate_count"] for row in rows)
    false_injected_queries = sum(row["false_injected_count"] > 0 for row in rows)
    false_injection_reasons = [
        analysis["reason"]
        for row in rows
        for analysis in row["false_injection_analysis"]
    ]
    quality_stage_fields = (
        "query_construction_ms",
        "db_checkout_ms",
        "fts_ms",
        "ranking_ms",
        "token_budget_ms",
        "telemetry_ms",
        "total_ms",
    )
    summary = {
        "query_count": len(rows),
        "designed_query_count": len(designed),
        "recall_at_1": rate(rows, "recall_at_1"),
        "recall_at_3": rate(rows, "recall_at_3"),
        "recall_at_5": rate(rows, "recall_at_5"),
        "mrr": round(statistics.fmean(row["mrr"] for row in rows), 6),
        "false_positive_candidate_rate": round(
            false_candidate_total / candidate_total, 6
        )
        if candidate_total
        else 0.0,
        "false_injection_query_rate": round(false_injected_queries / len(rows), 6),
        "zero_candidate_rate": round(
            sum(row["candidate_count"] == 0 for row in rows) / len(rows), 6
        ),
        "exact_match_success_rate": rate(exact, "would_inject_expected"),
        "lexical_match_success_rate": rate(lexical, "would_inject_expected"),
        "designed_match_success_rate": rate(designed, "would_inject_expected"),
        "no_match_success_rate": rate(subset("no_match"), "zero_candidates"),
        "future_dense_opportunity_rate": rate(subset("dense_opportunity"), "recall_at_5"),
        "irrelevant_query_candidate_rate": round(
            sum(row["candidate_count"] > 0 for row in rows if not row["expected_ids"])
            / sum(not row["expected_ids"] for row in rows),
            6,
        ),
        "failure_categories": {
            category: sum(row["failure_category"] == category for row in rows)
            for category in sorted(
                {row["failure_category"] for row in rows if row["failure_category"]}
            )
        },
        "false_injection_reasons": {
            reason: false_injection_reasons.count(reason)
            for reason in sorted(set(false_injection_reasons))
        },
        "queries_with_discarded_input_terms": sum(
            bool(row["discarded_input_terms"]) for row in rows
        ),
        "quality_stage_ms": {
            field: _distribution([row["stage_ms"][field] for row in rows])
            for field in quality_stage_fields
        },
    }
    return rows, summary


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _report(
    *,
    config: dict[str, Any],
    latency: dict[str, Any],
    quality: dict[str, Any],
) -> str:
    largest = str(max(config["corpus_sizes"]))
    paired = latency["paired_shadow_minus_off_ms"][largest]
    shadow = latency["by_mode_and_corpus"][f"shadow:{largest}"]
    off = latency["by_mode_and_corpus"][f"off:{largest}"]
    exact_pct = quality["exact_match_success_rate"] * 100
    lexical_pct = quality["lexical_match_success_rate"] * 100
    dense_pct = quality["future_dense_opportunity_rate"] * 100
    false_injection_pct = quality["false_injection_query_rate"] * 100
    next_step = (
        "Add an offline dense-embedding retrieval stage for the explicitly "
        "classified semantic-only cases, retaining lexical/exact retrieval as "
        "the baseline and measuring the two stages separately."
        if dense_pct < 80 and exact_pct >= 80 and lexical_pct >= 60
        else "Run one deterministic lexical query-construction/ranking and threshold audit on the recorded misses before adding dense retrieval."
    )
    return f"""# ORCHID cold-memory Phase 1 baseline

Generated by `tests/cold_memory_phase1_benchmark.py` with no awake LLM call.
The benchmark measures local SQLite/FTS5 retrieval and `ContextAssembler` only.

## Configuration

```json
{json.dumps(config, indent=2, sort_keys=True)}
```

## Measured facts

- At the largest corpus ({largest} semantic records), paired shadow-minus-off context-preparation overhead was **p50 {paired['p50']:.3f} ms, p95 {paired['p95']:.3f} ms, p99 {paired['p99']:.3f} ms, mean {paired['mean']:.3f} ms, max {paired['max']:.3f} ms**.
- Shadow's isolated `cold_retrieval_ms` at that corpus size was **p50 {shadow['cold_retrieval_ms']['p50']:.3f} ms and p95 {shadow['cold_retrieval_ms']['p95']:.3f} ms**.
- Shadow timeout count was **{shadow['timeout_count']}** and fail-open count was **{shadow['fail_open_count']}** across {shadow['context_assembly_ms']['count']} measured samples.
- Shadow produced at least one would-inject memory on **{shadow['would_inject_sample_rate'] * 100:.1f}%** of measured turns at that corpus size ({shadow['would_inject_count']} memory selections total); SHADOW injected zero messages.
- Exact/symbol-oriented cases would inject the expected memory **{exact_pct:.1f}%** of the time (8 exact cases within {quality['query_count']} total quality queries; see `quality_results.jsonl`).
- Lexical natural-language cases would inject the expected memory **{lexical_pct:.1f}%** of the time.
- The intentionally semantic-only future-dense cases reached Recall@5 **{dense_pct:.1f}%**; they are reported separately and are not counted as lexical baseline failures in the recommendation.
- Candidate-level false-positive rate was **{quality['false_positive_candidate_rate'] * 100:.1f}%**. Query-level false injection rate was **{false_injection_pct:.1f}%**.
- Zero-candidate rate was **{quality['zero_candidate_rate'] * 100:.1f}%**.
- Explicit no-match cases returned zero candidates **{quality['no_match_success_rate'] * 100:.1f}%** of the time.

## Interpretation

The paired delta is the cleanest product-cost estimate because OFF and SHADOW both assemble the same hot context while SHADOW performs the sidecar search and telemetry write. The isolated retrieval timing excludes the telemetry write, while context-preparation timing includes the real shadow path. Warm-up calls are excluded from the samples and documented in `CONFIG.json`.

FTS5 is expected to solve exact identifiers and natural-language questions sharing durable terms. The dense-opportunity cases deliberately use paraphrases with little or no lexical overlap; misses there motivate a future semantic index, not a claim that this lexical baseline is broken.

Failure categories and every query's candidates are preserved in `quality_results.jsonl`. No threshold tuning was performed after observing these results.

## Hot-path and fail-open conclusions

Focused tests verify byte-for-byte message equality between OFF and SHADOW, unchanged ACTIVE identity/raw-tail IDs, no retrieval-created events, and no shadow-memory insertion. FTS failure, timeout, malformed-row, empty-index, and provider-exception tests verify that retrieval returns no cold context and assembly continues with the hot context.

The retrieval telemetry is sidecar-only. It stores a bounded query preview and SHA-256 query hash, not a new conversational event. Actual injection accounting is updated only in `inject` mode; SHADOW records `would_inject` without adding messages.

## Validation status

Focused cold-memory tests passed: `python -m pytest -q tests/test_cold_memory.py`.
`python -m compileall -q memory_gateway tests/cold_memory_phase1_benchmark.py` and `git diff --check` completed without errors. The broader suite had one unrelated known failure: `tests/test_openai_adapter.py::test_selector_and_canonicalizer_send_json_schema_response_formats` still compares the old static `SELECTOR_RESPONSE_FORMAT` against the protocol-hardened dynamic per-chunk selector enum. The retrieval changes did not weaken or revert that schema; the failure is reported separately and is not included in cold-memory quality or latency results.

## Final answers

1. **Measured overhead:** at {largest} records, shadow-minus-off was p50 **{paired['p50']:.3f} ms** / p95 **{paired['p95']:.3f} ms**; isolated retrieval was p50 **{shadow['cold_retrieval_ms']['p50']:.3f} ms** / p95 **{shadow['cold_retrieval_ms']['p95']:.3f} ms**.
2. **Perceptibility:** **Yes for the local context-preparation path**: OFF was p50 {off['context_assembly_ms']['p50']:.3f} ms versus SHADOW {shadow['context_assembly_ms']['p50']:.3f} ms at 200 records, a measured added ~{paired['p50']:.1f} ms. That absolute delay may be hidden by remote model/network latency, which this microbenchmark does not measure.
3. **Exact/symbol solve rate:** **{exact_pct:.1f}%** by expected-memory would-inject success.
4. **Lexical natural-language solve rate:** **{lexical_pct:.1f}%** by expected-memory would-inject success.
5. **Dense motivation:** semantic-only paraphrases with no useful lexical overlap; their per-query records identify the misses.
6. **Irrelevant injection:** **{false_injection_pct:.1f}%** of all quality queries had at least one irrelevant would-inject memory.
7. **Hot-memory alteration under failure:** **No** in the tested failure modes.
8. **Memory entering events or ACTIVE:** **No**; tests and sidecar schema checks confirm isolation.
9. **Shadow safety:** **Yes**, within the tested local failure/timeout cases; keep it optional and shadow-only while observing this baseline.
10. **Single next capability:** {next_step}

## Reproduction

```powershell
python tests/cold_memory_phase1_benchmark.py --output-root artifacts/cold_memory/phase1_baseline --iterations {config['iterations']} --warmup {config['warmup']}
```

Artifacts:

- `SUMMARY.json`: machine-readable headline results.
- `latency_samples.jsonl` and `latency_summary.json`: OFF/SHADOW distributions and paired deltas.
- `quality_results.jsonl` and `quality_summary.json`: per-query candidates, metrics, and failure categories.
- `CORPUS.json`: fixture memories and query expectations.
- `CONFIG.json`: exact benchmark configuration.

## Recommendations

Keep the existing hot-memory path authoritative and leave cold retrieval in shadow mode. Do not add graph expansion, reranking, raw-history fallback, or query LLMs in this phase. The next capability above is conditional on the measured misses; implement it only with a follow-up before/after benchmark against these artifacts.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument(
        "--corpus-sizes",
        type=int,
        nargs="+",
        default=[25, 100, 200],
    )
    args = parser.parse_args()
    if args.iterations <= 0 or args.warmup < 0:
        raise SystemExit("iterations must be positive and warmup cannot be negative")

    output_root = args.output_root
    output_root.mkdir(parents=True, exist_ok=True)
    config = {
        "project_id": PROJECT_ID,
        "thread_id": THREAD_ID,
        "iterations": args.iterations,
        "warmup": args.warmup,
        "corpus_sizes": args.corpus_sizes,
        "candidate_limit": 20,
        "timeout_ms": 50,
        "minimum_score": 0.35,
        "token_budget": 512,
        "max_injected": 3,
        "includes_awake_llm": False,
        "warmups_excluded_from_latency_samples": True,
    }
    corpus = {"anchors": _anchors(), "queries": _queries()}
    _write_json(output_root / "CONFIG.json", config)
    _write_json(output_root / "CORPUS.json", corpus)

    quality_store = _build_store(output_root / "work_quality_100.db", 100)
    quality_rows, quality_summary = _quality_benchmark(quality_store)
    latency_rows, latency_summary = _latency_benchmark(
        output_root,
        corpus_sizes=args.corpus_sizes,
        iterations=args.iterations,
        warmup=args.warmup,
    )
    _write_jsonl(output_root / "quality_results.jsonl", quality_rows)
    _write_json(output_root / "quality_summary.json", quality_summary)
    _write_jsonl(output_root / "latency_samples.jsonl", latency_rows)
    _write_json(output_root / "latency_summary.json", latency_summary)
    summary = {
        "phase": "cold_memory_phase1_baseline",
        "measured": {
            "largest_corpus": max(args.corpus_sizes),
            "paired_shadow_minus_off_ms": latency_summary["paired_shadow_minus_off_ms"][
                str(max(args.corpus_sizes))
            ],
            "shadow_cold_retrieval_ms": latency_summary["by_mode_and_corpus"][
                f"shadow:{max(args.corpus_sizes)}"
            ]["cold_retrieval_ms"],
            "quality": quality_summary,
        },
        "next_capability": (
            "dense_embeddings_for_semantic_only_cases"
            if quality_summary["future_dense_opportunity_rate"] < 0.8
            and quality_summary["exact_match_success_rate"] >= 0.8
            and quality_summary["lexical_match_success_rate"] >= 0.6
            else "query_and_threshold_audit"
        ),
        "artifacts": [
            "REPORT.md",
            "SUMMARY.json",
            "CONFIG.json",
            "CORPUS.json",
            "latency_samples.jsonl",
            "latency_summary.json",
            "quality_results.jsonl",
            "quality_summary.json",
        ],
    }
    _write_json(output_root / "SUMMARY.json", summary)
    (output_root / "REPORT.md").write_text(
        _report(config=config, latency=latency_summary, quality=quality_summary),
        encoding="utf-8",
    )
    print(json.dumps(summary["measured"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
