"""Phase 1.2 deterministic ranking/threshold calibration.

The Phase 1.1 quality traces are treated as frozen calibration evidence. An
exploratory development set records the queries used while shaping the
policy, while a separately fixed acceptance holdout is evaluated only after
the policy is frozen. Neither set changes query construction, FTS candidate
generation, or telemetry behavior.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memory_gateway.cold_memory import (
    BASELINE_RANKING_POLICY,
    CALIBRATED_RANKING_POLICY,
    FTS5ColdMemoryRetriever,
    RetrievalQueryTrace,
    build_retrieval_query_trace,
)
from memory_gateway.db import SQLiteStore

try:
    from tests.cold_memory_phase1_benchmark import (
        PROJECT_ID,
        THREAD_ID,
        _anchors,
        _build_store,
        _queries,
        _write_json,
        _write_jsonl,
    )
except ModuleNotFoundError:
    from cold_memory_phase1_benchmark import (  # type: ignore[no-redef]
        PROJECT_ID,
        THREAD_ID,
        _anchors,
        _build_store,
        _queries,
        _write_json,
        _write_jsonl,
    )


DEFAULT_OUTPUT = Path("artifacts/cold_memory/phase1_2_calibration")
DEFAULT_FROZEN = Path(
    "artifacts/cold_memory/phase1_1_decision_audit/quality_results.jsonl"
)


def _development_holdout_queries() -> list[dict[str, Any]]:
    """Exploratory perturbations used while shaping the policy."""

    return [
        {
            "id": "holdout_lease_prose",
            "category": "identifier_with_prose",
            "expected_ids": ["mem_lease_renewal"],
            "query": "Please remind me why renew_lease checks lease_until before worker ownership changes.",
        },
        {
            "id": "holdout_selector_reordered",
            "category": "identifier_reordered",
            "expected_ids": ["mem_selector_enum"],
            "query": "Within tests/test_openai_adapter.py, selector_chunk_index must stay in the current per-chunk enum.",
        },
        {
            "id": "holdout_cas_symbol_prose",
            "category": "identifier_with_prose",
            "expected_ids": ["mem_cas_promotion"],
            "query": "Which operation uses promote_capsule_cas when stale_revision and active capsule lineage are involved?",
        },
        {
            "id": "holdout_sqlite_distinctive",
            "category": "lexical_perturbation",
            "expected_ids": ["mem_sqlite_lock"],
            "query": "A writer holds SQLite: explain the busy timeout and OperationalError behavior during retrieval.",
        },
        {
            "id": "holdout_provenance_reordered",
            "category": "lexical_perturbation",
            "expected_ids": ["mem_provenance"],
            "query": "Keep immutable event evidence linked to RETIRE semantic memory; what provenance rule is this?",
        },
        {
            "id": "holdout_budget_identifier",
            "category": "identifier_with_prose",
            "expected_ids": ["mem_context_budget"],
            "query": "Why do cold_memory_token_budget and context_budget_tokens keep the user request ahead of retrieval?",
        },
        {
            "id": "holdout_timeout_symbol",
            "category": "identifier_with_prose",
            "expected_ids": ["mem_timeout_failopen"],
            "query": "If the FTS progress_handler reaches its deadline, what operational behavior follows?",
        },
        {
            "id": "holdout_windows_symbol",
            "category": "identifier_with_prose",
            "expected_ids": ["mem_windows_cleanup"],
            "query": "After savedRigSoak imports, how is taskkill.exe process cleanup verified on Windows?",
        },
        {
            "id": "holdout_raw_tail_terms",
            "category": "lexical_perturbation",
            "expected_ids": ["mem_raw_tail"],
            "query": "Which design keeps recent raw tail wording protected while ACTIVE and cold retrieval are assembled?",
        },
        {
            "id": "holdout_retire_lifecycle",
            "category": "lexical_perturbation",
            "expected_ids": ["mem_retire_promotion"],
            "query": "When is RETIRE persistence allowed relative to successful active capsule promotion?",
        },
        {
            "id": "holdout_model_boundary",
            "category": "lexical_perturbation",
            "expected_ids": ["mem_model_boundary"],
            "query": "How does deterministic retrieval use user, tool, file, and symbol signals without an LLM?",
        },
        {
            "id": "holdout_fts_scope_identifiers",
            "category": "identifier_with_prose",
            "expected_ids": ["mem_fts_scope"],
            "query": "Which FTS5 rule restricts ACTIVE cold memories to project_id and thread_id?",
        },
        {
            "id": "holdout_injection_ephemeral",
            "category": "lexical_perturbation",
            "expected_ids": ["mem_injection_isolation"],
            "query": "Retrieved memory is ephemeral context, not a new event: which invariant says that?",
        },
        {
            "id": "holdout_noisy_exact_symbol",
            "category": "identifier_with_noise",
            "expected_ids": ["mem_lease_renewal"],
            "query": "Unrelated UI build note: renew_lease must still validate lease_until ownership before extending work.",
        },
        {
            "id": "holdout_noisy_cas_symbol",
            "category": "identifier_with_noise",
            "expected_ids": ["mem_cas_promotion"],
            "query": "For an unrelated documentation task, check promote_capsule_cas and stale_revision active-state safety.",
        },
        {
            "id": "holdout_no_match_marker",
            "category": "negative",
            "expected_ids": [],
            "query": "nonexistent_package_marker_771 never existed in this project",
        },
        {
            "id": "holdout_vague_turn",
            "category": "vague",
            "expected_ids": [],
            "query": "what happened with that",
        },
        {
            "id": "holdout_common_ambiguous",
            "category": "negative",
            "expected_ids": [],
            "query": "active context state decision history",
        },
    ]


def _acceptance_holdout_queries() -> list[dict[str, Any]]:
    """Final holdout fixed before the acceptance run and never used to tune."""

    return [
        {
            "id": "accept_lease_prose",
            "category": "identifier_with_prose",
            "expected_ids": ["mem_lease_renewal"],
            "query": "Can you explain why renew_lease checks lease_until before ownership is extended?",
        },
        {
            "id": "accept_selector_question",
            "category": "identifier_reordered",
            "expected_ids": ["mem_selector_enum"],
            "query": "Why is selector_chunk_index constrained by tests/test_openai_adapter.py to a per-chunk enum?",
        },
        {
            "id": "accept_cas_stale",
            "category": "identifier_with_prose",
            "expected_ids": ["mem_cas_promotion"],
            "query": "When a stale_revision update touches the live capsule, which promote_capsule_cas rule applies?",
        },
        {
            "id": "accept_sqlite_writer",
            "category": "identifier_with_prose",
            "expected_ids": ["mem_sqlite_lock"],
            "query": "What happens to retrieval when sqlite3.OperationalError meets a busy_timeout_ms writer lock?",
        },
        {
            "id": "accept_provenance_evidence",
            "category": "identifier_with_prose",
            "expected_ids": ["mem_provenance"],
            "query": "How do memory_evidence and event_id preserve immutable RETIRE provenance?",
        },
        {
            "id": "accept_budget_room",
            "category": "identifier_with_prose",
            "expected_ids": ["mem_context_budget"],
            "query": "Which cold_memory_token_budget and context_budget_tokens rules leave room for the user request?",
        },
        {
            "id": "accept_progress_deadline",
            "category": "identifier_with_prose",
            "expected_ids": ["mem_timeout_failopen"],
            "query": "What does progress_handler do when a cold FTS search reaches its timeout deadline?",
        },
        {
            "id": "accept_taskkill_cleanup",
            "category": "identifier_with_prose",
            "expected_ids": ["mem_windows_cleanup"],
            "query": "How does taskkill.exe support verified savedRigSoak cleanup on Windows?",
        },
        {
            "id": "accept_raw_tail_verbatim",
            "category": "lexical_perturbation",
            "expected_ids": ["mem_raw_tail"],
            "query": "Why is the recent raw tail kept verbatim beside ACTIVE and the cold sidecar?",
        },
        {
            "id": "accept_retire_after_active",
            "category": "lexical_perturbation",
            "expected_ids": ["mem_retire_promotion"],
            "query": "What lifecycle rule permits a RETIRE write only after active promotion succeeds?",
        },
        {
            "id": "accept_deterministic_boundary",
            "category": "lexical_perturbation",
            "expected_ids": ["mem_model_boundary"],
            "query": "Which boundary builds a deterministic query from files and symbols without calling an LLM?",
        },
        {
            "id": "accept_scope_identifiers",
            "category": "identifier_with_prose",
            "expected_ids": ["mem_fts_scope"],
            "query": "How does the FTS5 index apply project_id and thread_id scope to ACTIVE memories?",
        },
        {
            "id": "accept_event_isolation",
            "category": "lexical_perturbation",
            "expected_ids": ["mem_injection_isolation"],
            "query": "Why does ephemeral retrieved context stay outside the conversational event ledger?",
        },
        {
            "id": "accept_noisy_lease",
            "category": "identifier_with_noise",
            "expected_ids": ["mem_lease_renewal"],
            "query": "Ignore the unrelated UI color note; renew_lease lease_until ownership still matters here.",
        },
        {
            "id": "accept_noisy_cas",
            "category": "identifier_with_noise",
            "expected_ids": ["mem_cas_promotion"],
            "query": "For an unrelated documentation task, check promote_capsule_cas before stale_revision changes live state.",
        },
        {
            "id": "accept_no_match_marker",
            "category": "negative",
            "expected_ids": [],
            "query": "unrecorded_release_marker_883 never existed in this repository",
        },
        {
            "id": "accept_vague_turn",
            "category": "vague",
            "expected_ids": [],
            "query": "what should happen next",
        },
        {
            "id": "accept_common_ambiguous",
            "category": "negative",
            "expected_ids": [],
            "query": "active context history state decision",
        },
    ]


def _load_frozen(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if len(rows) != len(_queries()):
        raise ValueError(
            f"frozen calibration trace count {len(rows)} != expected {len(_queries())}"
        )
    return rows


def _frozen_trace(row: dict[str, Any]) -> RetrievalQueryTrace:
    return RetrievalQueryTrace(
        latest_user=row["original_input"],
        input_terms=tuple(row["input_terms"]),
        identifier_terms=tuple(row["identifier_terms"]),
        ordinary_terms=tuple(row["ordinary_terms"]),
        constructed_query=row["constructed_query"],
    )


def _evaluate(
    store: SQLiteStore,
    cases: list[dict[str, Any]],
    *,
    policy,
    frozen_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    retriever = FTS5ColdMemoryRetriever(
        store,
        timeout_ms=50,
        candidate_limit=20,
        ranking_policy=policy,
    )
    frozen_by_id = {row["query_id"]: row for row in frozen_rows or []}
    output: list[dict[str, Any]] = []
    for case in cases:
        frozen = frozen_by_id.get(case["id"])
        trace = (
            _frozen_trace(frozen)
            if frozen is not None
            else build_retrieval_query_trace(
                [{"role": "user", "content": case["query"]}]
            )
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
        candidates = [hit.memory_id for hit in result.candidates]
        expected = set(case["expected_ids"])
        ranks = [
            candidates.index(memory_id) + 1
            for memory_id in expected
            if memory_id in candidates
        ]
        rank = min(ranks) if ranks else None
        would = [hit.memory_id for hit in result.would_inject]
        false_injected = [memory_id for memory_id in would if memory_id not in expected]
        output.append(
            {
                "query_id": case["id"],
                "category": case["category"],
                "policy": policy.name,
                "query": case["query"],
                "original_input": trace.latest_user,
                "constructed_query": result.constructed_query,
                "input_terms": list(result.input_terms),
                "identifier_terms": list(result.identifier_terms),
                "ordinary_terms": list(result.ordinary_terms),
                "fts_query": result.fts_query,
                "expected_ids": case["expected_ids"],
                "candidate_ids": candidates,
                "would_inject_ids": would,
                "candidate_count": len(candidates),
                "false_injected_ids": false_injected,
                "false_injected_count": len(false_injected),
                "rank": rank,
                "would_inject_expected": bool(expected.intersection(would)),
                "recall_at_1": bool(rank and rank <= 1),
                "recall_at_3": bool(rank and rank <= 3),
                "recall_at_5": bool(rank and rank <= 5),
                "status": result.status,
                "timed_out": result.timed_out,
                "fail_open": result.fail_open,
                "ranking_details": list(result.ranking_details),
                "raw_fts_candidates": list(result.raw_fts_candidates),
                "stage_ms": {
                    "ranking_ms": round(result.ranking_ms, 6),
                    "total_ms": round(result.total_ms, 6),
                },
            }
        )
    return output


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def rate(items: list[dict[str, Any]], key: str) -> float:
        return round(sum(bool(row[key]) for row in items) / len(items), 6) if items else 0.0

    expected = [row for row in rows if row["expected_ids"]]
    exact = [
        row
        for row in rows
        if row["category"] == "exact"
        or row["category"].startswith("identifier")
    ]
    lexical = [
        row
        for row in rows
        if row["category"] in {"lexical", "lexical_perturbation"}
    ]
    negatives = [row for row in rows if not row["expected_ids"]]
    return {
        "query_count": len(rows),
        "expected_query_count": len(expected),
        "recall_at_1": rate(expected, "recall_at_1"),
        "recall_at_3": rate(expected, "recall_at_3"),
        "recall_at_5": rate(expected, "recall_at_5"),
        "would_inject_expected_rate": rate(expected, "would_inject_expected"),
        "exact_would_inject_expected_rate": rate(exact, "would_inject_expected"),
        "lexical_would_inject_expected_rate": rate(lexical, "would_inject_expected"),
        "false_injection_query_rate": round(
            sum(row["false_injected_count"] > 0 for row in rows) / len(rows), 6
        )
        if rows
        else 0.0,
        "false_injected_memory_count": sum(row["false_injected_count"] for row in rows),
        "negative_would_inject_rate": round(
            sum(bool(row["would_inject_ids"]) for row in negatives) / len(negatives), 6
        )
        if negatives
        else 0.0,
        "zero_candidate_rate": round(
            sum(not row["candidate_ids"] for row in rows) / len(rows), 6
        )
        if rows
        else 0.0,
        "timeout_count": sum(row["timed_out"] for row in rows),
        "fail_open_count": sum(row["fail_open"] for row in rows),
    }


def _report(
    *,
    config: dict[str, Any],
    calibration_before: dict[str, Any],
    calibration_after: dict[str, Any],
    development_before: dict[str, Any],
    development_after: dict[str, Any],
    acceptance_before: dict[str, Any],
    acceptance_after: dict[str, Any],
    recovered: list[str],
    unresolved: list[str],
    baseline_replay_consistency: dict[str, Any],
) -> str:
    safe = (
        calibration_after["exact_would_inject_expected_rate"]
        >= calibration_before["exact_would_inject_expected_rate"]
        and calibration_after["false_injection_query_rate"]
        <= calibration_before["false_injection_query_rate"]
        and acceptance_after["false_injection_query_rate"]
        <= acceptance_before["false_injection_query_rate"]
        and acceptance_after["exact_would_inject_expected_rate"]
        >= acceptance_before["exact_would_inject_expected_rate"]
    )
    return f"""# ORCHID cold-memory Phase 1.2 — Deterministic Calibration

This phase changes only lexical candidate scoring and injection gating. Query
construction, FTS candidate generation, telemetry, hot-memory assembly, and
the five semantic-only cases remain unchanged. No dense retrieval or LLM is
used.

## Calibration protocol

- The 26 Phase 1.1 quality traces were loaded from the frozen artifact at
  `phase1_1_decision_audit/quality_results.jsonl`.
- The development set contains {config['development_query_count']} exploratory
  perturbation and negative queries. It was inspected while shaping the
  policy and is not presented as an untouched holdout.
- The acceptance holdout contains {config['acceptance_holdout_query_count']}
  separately fixed queries with reordered prose, identifiers surrounded by
  noise, common terms mixed with distinctive terms, and vague/negative cases.
- The calibrated policy is fixed in `CONFIG.json` before the acceptance
  holdout comparison; no acceptance result was used to change it.
- Baseline replay matched frozen candidate IDs on {baseline_replay_consistency['candidate_ids_match']}/{baseline_replay_consistency['query_count']} queries and frozen would-inject IDs on {baseline_replay_consistency['would_inject_ids_match']}/{baseline_replay_consistency['query_count']} queries.
- Identifier decomposition is ranking-only; the FTS MATCH query is unchanged.

## Policy

```json
{json.dumps(config['calibrated_policy'], indent=2, sort_keys=True)}
```

The calibrated score is a bounded deterministic combination of FTS rank,
lexical overlap, strong-identifier coverage, distinctive-token overlap, and
a small activation prior. It requires at least 66% strong-identifier coverage
when query identifiers exist, otherwise at least two lexical terms including one
candidate-distinctive term. A 0.05 minimum adjacent margin and 0.10 maximum
gap for secondary injections prevent weak related memories from joining a
strong primary match.

## Measured before/after

| Set / metric | Baseline | Calibrated |
|---|---:|---:|
| Calibration exact expected injection | {calibration_before['exact_would_inject_expected_rate'] * 100:.1f}% | {calibration_after['exact_would_inject_expected_rate'] * 100:.1f}% |
| Calibration lexical expected injection | {calibration_before['lexical_would_inject_expected_rate'] * 100:.1f}% | {calibration_after['lexical_would_inject_expected_rate'] * 100:.1f}% |
| Calibration false injection queries | {calibration_before['false_injection_query_rate'] * 100:.1f}% | {calibration_after['false_injection_query_rate'] * 100:.1f}% |
| Development exact expected injection | {development_before['exact_would_inject_expected_rate'] * 100:.1f}% | {development_after['exact_would_inject_expected_rate'] * 100:.1f}% |
| Development lexical expected injection | {development_before['lexical_would_inject_expected_rate'] * 100:.1f}% | {development_after['lexical_would_inject_expected_rate'] * 100:.1f}% |
| Development false injection queries | {development_before['false_injection_query_rate'] * 100:.1f}% | {development_after['false_injection_query_rate'] * 100:.1f}% |
| Development negative would-inject rate | {development_before['negative_would_inject_rate'] * 100:.1f}% | {development_after['negative_would_inject_rate'] * 100:.1f}% |
| Acceptance exact expected injection | {acceptance_before['exact_would_inject_expected_rate'] * 100:.1f}% | {acceptance_after['exact_would_inject_expected_rate'] * 100:.1f}% |
| Acceptance lexical expected injection | {acceptance_before['lexical_would_inject_expected_rate'] * 100:.1f}% | {acceptance_after['lexical_would_inject_expected_rate'] * 100:.1f}% |
| Acceptance false injection queries | {acceptance_before['false_injection_query_rate'] * 100:.1f}% | {acceptance_after['false_injection_query_rate'] * 100:.1f}% |
| Acceptance negative would-inject rate | {acceptance_before['negative_would_inject_rate'] * 100:.1f}% | {acceptance_after['negative_would_inject_rate'] * 100:.1f}% |

The calibrated policy recovered **{len(recovered)}** frozen ranking/threshold
misses: `{', '.join(recovered) if recovered else 'none'}`. It intentionally does
not claim to solve the semantic-only Phase 2 cases. The remaining frozen
ranking/threshold miss is `{', '.join(unresolved) if unresolved else 'none'}`.

## Interpretation

The calibration objective is precision first. Exact/symbol recall is a hard
guardrail; a lexical match is injected only with enough lexical evidence and
candidate distinction. The report preserves all per-candidate score details in
`calibration_comparison.jsonl`, `development_holdout_results.jsonl`, and
`holdout_results.jsonl`, including margins, evidence gates, and decisions.

The calibrated policy is **{'safe for shadow/inject enablement' if safe else 'not safe to enable broadly'}** under the explicit comparison rule in this report. This is not evidence for dense retrieval; the remaining semantic-only cases remain labeled for a later phase.

## Recommendation

{('Adopt the calibrated policy for optional cold-memory modes, keep retrieval shadow-only while observing production traces, and make Phase 1.3 a separate synchronous-telemetry reduction experiment.' if safe else 'Keep the baseline policy and do not enable the calibrated policy until its holdout regressions are resolved.')}

Do not mix telemetry buffering or asynchronous writes into this ranking result.

## Reproduction

```powershell
python tests/cold_memory_phase1_2_calibration.py --output-root artifacts/cold_memory/phase1_2_calibration
```

Focused validation and the known unrelated selector-schema result are recorded
outside this measurement artifact.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--frozen-traces", type=Path, default=DEFAULT_FROZEN)
    args = parser.parse_args()
    output_root = args.output_root
    output_root.mkdir(parents=True, exist_ok=True)

    frozen = _load_frozen(args.frozen_traces)
    calibration_cases = [
        {
            "id": row["query_id"],
            "category": row["category"],
            "expected_ids": row["expected_ids"],
            "query": row["original_input"],
        }
        for row in frozen
    ]
    development_cases = _development_holdout_queries()
    acceptance_cases = _acceptance_holdout_queries()
    config = {
        "phase": "cold_memory_phase1_2_calibration",
        "project_id": PROJECT_ID,
        "thread_id": THREAD_ID,
        "calibration_trace_source": str(args.frozen_traces),
        "calibration_query_count": len(calibration_cases),
        "development_query_count": len(development_cases),
        "acceptance_holdout_query_count": len(acceptance_cases),
        "candidate_limit": 20,
        "timeout_ms": 50,
        "token_budget": 512,
        "max_injected": 3,
        "query_construction_changed": False,
        "dense_retrieval": False,
        "telemetry_optimization": False,
        "calibrated_policy": {
            "name": CALIBRATED_RANKING_POLICY.name,
            "minimum_score": CALIBRATED_RANKING_POLICY.minimum_score,
            "minimum_margin": CALIBRATED_RANKING_POLICY.minimum_margin,
            "secondary_score_gap": CALIBRATED_RANKING_POLICY.secondary_score_gap,
            "fts_rank_weight": CALIBRATED_RANKING_POLICY.fts_rank_weight,
            "lexical_weight": CALIBRATED_RANKING_POLICY.lexical_weight,
            "identifier_weight": CALIBRATED_RANKING_POLICY.identifier_weight,
            "distinctive_weight": CALIBRATED_RANKING_POLICY.distinctive_weight,
            "activation_weight": CALIBRATED_RANKING_POLICY.activation_weight,
            "lexical_saturation": CALIBRATED_RANKING_POLICY.lexical_saturation,
            "minimum_identifier_coverage": CALIBRATED_RANKING_POLICY.minimum_identifier_coverage,
            "minimum_lexical_overlap": CALIBRATED_RANKING_POLICY.minimum_lexical_overlap,
            "minimum_distinctive_overlap": CALIBRATED_RANKING_POLICY.minimum_distinctive_overlap,
        },
    }
    _write_json(output_root / "CONFIG.json", config)
    _write_json(
        output_root / "CORPUS.json",
        {
            "anchors": _anchors(),
            "calibration_cases": calibration_cases,
            "development_holdout_cases": development_cases,
            "acceptance_holdout_cases": acceptance_cases,
        },
    )
    _write_jsonl(output_root / "frozen_calibration_traces.jsonl", frozen)

    baseline_store = _build_store(output_root / "work_baseline.db", 100)
    calibrated_store = _build_store(output_root / "work_calibrated.db", 100)
    baseline_calibration = _evaluate(
        baseline_store,
        calibration_cases,
        policy=BASELINE_RANKING_POLICY,
        frozen_rows=frozen,
    )
    calibrated_calibration = _evaluate(
        calibrated_store,
        calibration_cases,
        policy=CALIBRATED_RANKING_POLICY,
        frozen_rows=frozen,
    )
    baseline_development = _evaluate(
        baseline_store,
        development_cases,
        policy=BASELINE_RANKING_POLICY,
    )
    calibrated_development = _evaluate(
        calibrated_store,
        development_cases,
        policy=CALIBRATED_RANKING_POLICY,
    )
    baseline_acceptance = _evaluate(
        baseline_store,
        acceptance_cases,
        policy=BASELINE_RANKING_POLICY,
    )
    calibrated_acceptance = _evaluate(
        calibrated_store,
        acceptance_cases,
        policy=CALIBRATED_RANKING_POLICY,
    )
    baseline_calibration_summary = _summary(baseline_calibration)
    calibrated_calibration_summary = _summary(calibrated_calibration)
    baseline_development_summary = _summary(baseline_development)
    calibrated_development_summary = _summary(calibrated_development)
    baseline_acceptance_summary = _summary(baseline_acceptance)
    calibrated_acceptance_summary = _summary(calibrated_acceptance)
    baseline_replay_consistency = {
        "candidate_ids_match": sum(
            before["candidate_ids"] == frozen_row["candidate_ids"]
            for before, frozen_row in zip(baseline_calibration, frozen)
        ),
        "would_inject_ids_match": sum(
            before["would_inject_ids"] == frozen_row["would_inject_ids"]
            for before, frozen_row in zip(baseline_calibration, frozen)
        ),
        "query_count": len(frozen),
    }
    baseline_by_id = {row["query_id"]: row for row in frozen}
    calibrated_by_id = {row["query_id"]: row for row in calibrated_calibration}
    recovered = [
        query_id
        for query_id, row in baseline_by_id.items()
        if row.get("failure_category") in {"ranking_issue", "threshold_issue"}
        and calibrated_by_id[query_id]["would_inject_expected"]
    ]
    unresolved = [
        query_id
        for query_id, row in baseline_by_id.items()
        if row.get("failure_category") in {"ranking_issue", "threshold_issue"}
        and query_id not in recovered
    ]
    comparison = []
    for before, after in zip(baseline_calibration, calibrated_calibration):
        comparison.append({"baseline": before, "calibrated": after})
    _write_jsonl(output_root / "calibration_comparison.jsonl", comparison)
    _write_jsonl(
        output_root / "development_holdout_results.jsonl",
        [
            {"baseline": before, "calibrated": after}
            for before, after in zip(baseline_development, calibrated_development)
        ],
    )
    _write_jsonl(
        output_root / "holdout_results.jsonl",
        [
            {"baseline": before, "calibrated": after}
            for before, after in zip(baseline_acceptance, calibrated_acceptance)
        ],
    )
    summary = {
        "phase": config["phase"],
        "calibration": {
            "baseline": baseline_calibration_summary,
            "calibrated": calibrated_calibration_summary,
            "baseline_replay_consistency": baseline_replay_consistency,
            "recovered_ranking_or_threshold_misses": recovered,
            "unresolved_ranking_or_threshold_misses": unresolved,
        },
        "development_holdout": {
            "baseline": baseline_development_summary,
            "calibrated": calibrated_development_summary,
        },
        "holdout": {
            "baseline": baseline_acceptance_summary,
            "calibrated": calibrated_acceptance_summary,
        },
        "next_capability": "synchronous_telemetry_reduction",
        "artifacts": [
            "REPORT.md",
            "SUMMARY.json",
            "CONFIG.json",
            "CORPUS.json",
            "frozen_calibration_traces.jsonl",
            "calibration_comparison.jsonl",
            "development_holdout_results.jsonl",
            "holdout_results.jsonl",
        ],
    }
    _write_json(output_root / "SUMMARY.json", summary)
    (output_root / "REPORT.md").write_text(
        _report(
            config=config,
            calibration_before=baseline_calibration_summary,
            calibration_after=calibrated_calibration_summary,
            development_before=baseline_development_summary,
            development_after=calibrated_development_summary,
            acceptance_before=baseline_acceptance_summary,
            acceptance_after=calibrated_acceptance_summary,
            recovered=recovered,
            unresolved=unresolved,
            baseline_replay_consistency=baseline_replay_consistency,
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
