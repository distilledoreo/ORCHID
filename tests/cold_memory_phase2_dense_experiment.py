"""Phase 2.0 dense-retrieval incremental-value experiment.

This is an offline harness.  It builds a dense index from the same RETIRE
fixture used by the calibrated Phase 1 benchmark, then compares dense search
with the unchanged calibrated FTS path.  It does not import dense code from
the gateway, does not write retrieval telemetry, and never assembles or
injects a dense result into an ORCHID context.

The five frozen semantic cases are deliberately evaluated separately from
lexical guardrails.  Dense retrieval receives credit only when it recovers a
case that calibrated FTS did not recover.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memory_gateway.cold_memory import (  # noqa: E402
    CALIBRATED_RANKING_POLICY,
    FTS5ColdMemoryRetriever,
    build_retrieval_query_trace,
)
from memory_gateway.dense_experiment import (  # noqa: E402
    DenseMemoryIndex,
    OnnxTextEmbedder,
    model_metadata,
    write_json,
)

try:
    from tests.cold_memory_phase1_2_calibration import _acceptance_holdout_queries
    from tests.cold_memory_phase1_benchmark import (
        PROJECT_ID,
        THREAD_ID,
        _anchors,
        _build_store,
        _write_jsonl,
    )
except ModuleNotFoundError:
    from cold_memory_phase1_2_calibration import _acceptance_holdout_queries  # type: ignore
    from cold_memory_phase1_benchmark import (  # type: ignore
        PROJECT_ID,
        THREAD_ID,
        _anchors,
        _build_store,
        _write_jsonl,
    )


DEFAULT_OUTPUT = Path("artifacts/cold_memory/phase2_0_dense_experiment")
MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
MODEL_FILENAMES = ("onnx/model.onnx", "tokenizer.json")

# These are copied from the frozen Phase 1.1 decision audit.  They are not
# retuned by this harness.
FROZEN_SEMANTIC_CASES = [
    {
        "id": "dense_worker_handoff",
        "category": "frozen_semantic",
        "expected_ids": ["mem_lease_renewal"],
        "query": "that old concurrency defect involving a handoff",
    },
    {
        "id": "dense_source_fidelity",
        "category": "frozen_semantic",
        "expected_ids": ["mem_provenance"],
        "query": "the earlier reason exact source material had to remain available",
    },
    {
        "id": "dense_active_safety",
        "category": "frozen_semantic",
        "expected_ids": ["mem_cas_promotion"],
        "query": "the previous approach to keeping live state safe while work overlaps",
    },
    {
        "id": "dense_query_model",
        "category": "frozen_semantic",
        "expected_ids": ["mem_model_boundary"],
        "query": "the lightweight way the system decides what earlier context matters",
    },
    {
        "id": "dense_old_summary",
        "category": "frozen_semantic",
        "expected_ids": ["mem_raw_tail"],
        "query": "why the newest conversational details still need their original wording",
    },
]


def _paraphrase_holdout_cases() -> list[dict[str, Any]]:
    """A fixed semantic holdout, separate from the five frozen cases."""

    return [
        {"id": "para_lease_abandoned", "expected_ids": ["mem_lease_renewal"], "query": "Which earlier incident let an abandoned worker extend work it no longer owned?"},
        {"id": "para_lease_stale", "expected_ids": ["mem_lease_renewal"], "query": "What mistake allowed a stale worker to keep a job alive?"},
        {"id": "para_lease_handoff", "expected_ids": ["mem_lease_renewal"], "query": "The ownership handoff bug around abandoned work"},
        {"id": "para_provenance_summary", "expected_ids": ["mem_provenance"], "query": "Why can summaries not replace the original records behind a memory?"},
        {"id": "para_provenance_source", "expected_ids": ["mem_provenance"], "query": "How do we preserve source evidence after distillation?"},
        {"id": "para_provenance_proof", "expected_ids": ["mem_provenance"], "query": "Which mechanism keeps historical proof attached to a remembered decision?"},
        {"id": "para_cas_concurrent", "expected_ids": ["mem_cas_promotion"], "query": "How is a newer state prevented from overwriting the live snapshot after concurrent work?"},
        {"id": "para_cas_race", "expected_ids": ["mem_cas_promotion"], "query": "What protects the current capsule when two promotions race?"},
        {"id": "para_cas_lineage", "expected_ids": ["mem_cas_promotion"], "query": "The safeguard against stale lineage replacing the active state"},
        {"id": "para_model_history", "expected_ids": ["mem_model_boundary"], "query": "How does the system choose relevant history without asking another model to rewrite the search?"},
        {"id": "para_model_signal", "expected_ids": ["mem_model_boundary"], "query": "What non-LLM signal path selects old context?"},
        {"id": "para_model_lookup", "expected_ids": ["mem_model_boundary"], "query": "How are files and symbols turned into a history lookup?"},
        {"id": "para_tail_summary", "expected_ids": ["mem_raw_tail"], "query": "Why preserve the latest conversation wording instead of relying only on a summary?"},
        {"id": "para_tail_exact", "expected_ids": ["mem_raw_tail"], "query": "What keeps fresh details exact during context construction?"},
        {"id": "para_tail_verbatim", "expected_ids": ["mem_raw_tail"], "query": "The newest exchange should not be paraphrased away"},
    ]


def _hard_negative_cases() -> list[dict[str, Any]]:
    """Queries with no expected memory, including related distractors."""

    queries = [
        "How do we rotate database connection pools during deploys?",
        "Which React component owns the dashboard state?",
        "What Postgres isolation level prevents duplicate writes?",
        "How is an API bearer token refreshed after expiry?",
        "Which graph edge links a file to its owning component?",
        "What embedding dimension should the future vector index use?",
        "How do we compact an HTTP cache without losing ETags?",
        "Which retry policy handles a DNS outage?",
        "How should a stale worker reclaim work rather than extend its lease?",
        "How does a graph preserve provenance between decisions?",
        "Which vector reranker chooses between competing memories?",
        "What UI displays the newest conversation tail?",
        "Which database transaction isolation protects capsule writes?",
        "How does a model rewrite a user query before search?",
        "What scheduler promotes a memory directly into ACTIVE?",
    ]
    return [
        {
            "id": f"negative_{index:02d}",
            "category": "hard_negative",
            "expected_ids": [],
            "query": query,
        }
        for index, query in enumerate(queries, start=1)
    ]


class _NoopTelemetry:
    """Prevent durable retrieval telemetry from contaminating this experiment."""

    def record_memory_retrieval(self, **_: Any) -> None:
        return None

    def record_cold_retrieval_run(self, **_: Any) -> str:
        return "phase2-dense-noop"


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile)))
    return ordered[index]


def _distribution(values: list[float]) -> dict[str, float | int]:
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


def _memory_rows(store: Any) -> list[dict[str, Any]]:
    with store.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, content, memory_type, importance, activation_score,
                   created_at, status
            FROM long_term_memories
            WHERE project_id = ? AND thread_id = ? AND status = 'ACTIVE'
            ORDER BY id
            """,
            (PROJECT_ID, THREAD_ID),
        ).fetchall()
    return [dict(row) for row in rows]


def _hot_path_state(store: Any) -> dict[str, Any]:
    """Bounded state fingerprint proving the offline run stayed sidecar-only."""

    with store.connect() as connection:
        event_ids = [
            str(row["id"])
            for row in connection.execute(
                "SELECT id FROM events WHERE thread_id = ? ORDER BY sequence",
                (THREAD_ID,),
            ).fetchall()
        ]
        active_memory_ids = [
            str(row["id"])
            for row in connection.execute(
                """
                SELECT id FROM long_term_memories
                WHERE project_id = ? AND thread_id = ? AND status = 'ACTIVE'
                ORDER BY id
                """,
                (PROJECT_ID, THREAD_ID),
            ).fetchall()
        ]
        active_capsule_ids = [
            str(row["id"])
            for row in connection.execute(
                "SELECT id FROM capsules WHERE thread_id = ? AND state = 'ACTIVE' ORDER BY id",
                (THREAD_ID,),
            ).fetchall()
        ]

    def digest(values: list[str]) -> str:
        return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()

    return {
        "event_count": len(event_ids),
        "event_ids_sha256": digest(event_ids),
        "active_memory_count": len(active_memory_ids),
        "active_memory_ids_sha256": digest(active_memory_ids),
        "active_capsule_ids_sha256": digest(active_capsule_ids),
    }


def _rank_for_expected(
    candidates: list[dict[str, Any]], expected_ids: list[str]
) -> int | None:
    ranks = [
        index + 1
        for index, candidate in enumerate(candidates)
        if candidate["memory_id"] in set(expected_ids)
    ]
    return min(ranks) if ranks else None


def _evaluate_case(
    *,
    case: dict[str, Any],
    retriever: FTS5ColdMemoryRetriever,
    embedder: OnnxTextEmbedder,
    dense_index: DenseMemoryIndex,
    latency_iteration: int | None = None,
) -> dict[str, Any]:
    original_query = str(case["query"])
    trace = build_retrieval_query_trace(
        [{"role": "user", "content": original_query}]
    )
    fts_result = retriever.retrieve(
        project_id=PROJECT_ID,
        thread_id=THREAD_ID,
        query=trace.constructed_query,
        token_budget=512,
        max_injected=3,
        mode="shadow",
        query_trace=trace,
    )
    dense_started = time.perf_counter_ns()
    query_embedding = embedder.embed([trace.constructed_query])[0]
    dense_candidates = dense_index.search(query_embedding, top_k=5)
    dense_query_ms = (time.perf_counter_ns() - dense_started) / 1_000_000
    dense_rows = [
        {"memory_id": candidate.memory_id, "score": round(candidate.score, 8)}
        for candidate in dense_candidates
    ]
    fts_rows = [
        {"memory_id": hit.memory_id, "score": round(hit.score, 8)}
        for hit in fts_result.candidates
    ]
    expected_ids = [str(memory_id) for memory_id in case["expected_ids"]]
    fts_rank = _rank_for_expected(fts_rows, expected_ids)
    dense_rank = _rank_for_expected(dense_rows, expected_ids)
    dense_top_score = dense_rows[0]["score"] if dense_rows else None
    # This is a registered measurement-only gate, not a production injection
    # policy.  It lets the report quantify tempting dense candidates without
    # changing ContextAssembler or the lexical threshold.
    dense_would_inject_ids = (
        [dense_rows[0]["memory_id"]]
        if dense_rows and dense_top_score is not None and dense_top_score >= 0.50
        else []
    )
    false_dense_candidates = [
        row["memory_id"] for row in dense_rows if row["memory_id"] not in expected_ids
    ]
    false_dense_injections = [
        memory_id
        for memory_id in dense_would_inject_ids
        if memory_id not in expected_ids
    ]
    if expected_ids and dense_rank is None:
        dense_failure_category = "no_semantic_match_likely_encoder_gap"
    elif expected_ids and dense_rank > 1:
        dense_failure_category = "dense_ranking_issue"
    elif expected_ids and not dense_would_inject_ids:
        dense_failure_category = "dense_measurement_threshold_issue"
    elif not expected_ids and false_dense_injections:
        dense_failure_category = "dense_false_injection"
    elif not expected_ids and dense_rows:
        dense_failure_category = "dense_candidate_without_abstention"
    else:
        dense_failure_category = None
    return {
        "query_id": case["id"],
        "category": case["category"],
        "query": original_query,
        "constructed_query": trace.constructed_query,
        "expected_ids": expected_ids,
        "fts_candidate_ids": [row["memory_id"] for row in fts_rows],
        "fts_would_inject_ids": [hit.memory_id for hit in fts_result.would_inject],
        "fts_rank": fts_rank,
        "fts_recall_at_5": bool(fts_rank and fts_rank <= 5),
        "fts_would_inject_expected": bool(
            set(expected_ids).intersection(hit.memory_id for hit in fts_result.would_inject)
        ),
        "fts_status": fts_result.status,
        "fts_timed_out": fts_result.timed_out,
        "fts_fail_open": fts_result.fail_open,
        "fts_stage_ms": {
            "query_construction_ms": round(fts_result.query_construction_ms, 6),
            "db_checkout_ms": round(fts_result.db_checkout_ms, 6),
            "fts_ms": round(fts_result.fts_ms, 6),
            "ranking_ms": round(fts_result.ranking_ms, 6),
            "token_budget_ms": round(fts_result.token_budget_ms, 6),
            "total_ms": round(fts_result.total_ms, 6),
        },
        "dense_candidates": dense_rows,
        "dense_has_candidate": bool(dense_rows),
        "dense_top1_id": dense_rows[0]["memory_id"] if dense_rows else None,
        "dense_top1_score": dense_top_score,
        "dense_rank": dense_rank,
        "dense_recall_at_1": bool(dense_rank and dense_rank <= 1),
        "dense_recall_at_3": bool(dense_rank and dense_rank <= 3),
        "dense_recall_at_5": bool(dense_rank and dense_rank <= 5),
        "dense_mrr": round(1 / dense_rank, 6) if dense_rank else 0.0,
        "dense_query_ms": round(dense_query_ms, 6),
        "dense_would_inject_ids_measurement_only": dense_would_inject_ids,
        "dense_false_candidate_ids": false_dense_candidates,
        "dense_false_would_inject_ids_measurement_only": false_dense_injections,
        "dense_failure_category": dense_failure_category,
        "incremental_semantic_recovery": bool(
            expected_ids
            and dense_rank is not None
            and dense_rank <= 5
            and not (
                set(expected_ids)
                & {hit.memory_id for hit in fts_result.would_inject}
            )
        ),
        "incremental_semantic_injection_recovery": bool(
            expected_ids
            and set(expected_ids) & set(dense_would_inject_ids)
            and not (
                set(expected_ids)
                & {hit.memory_id for hit in fts_result.would_inject}
            )
        ),
        "latency_iteration": latency_iteration,
    }


def _set_summary(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    expected_rows = [row for row in rows if row["expected_ids"]]
    negative_rows = [row for row in rows if not row["expected_ids"]]

    def rate(items: list[dict[str, Any]], field: str) -> float:
        return round(sum(bool(item[field]) for item in items) / len(items), 6) if items else 0.0

    candidate_count = sum(len(row["dense_candidates"]) for row in rows)
    false_candidate_count = sum(len(row["dense_false_candidate_ids"]) for row in rows)
    false_injection_queries = sum(
        bool(row["dense_false_would_inject_ids_measurement_only"]) for row in rows
    )
    failure_categories: dict[str, int] = {}
    for row in rows:
        category = row.get("dense_failure_category")
        if category:
            failure_categories[category] = failure_categories.get(category, 0) + 1
    return {
        "name": name,
        "query_count": len(rows),
        "positive_query_count": len(expected_rows),
        "negative_query_count": len(negative_rows),
        "recall_at_1": rate(expected_rows, "dense_recall_at_1"),
        "recall_at_3": rate(expected_rows, "dense_recall_at_3"),
        "recall_at_5": rate(expected_rows, "dense_recall_at_5"),
        "mrr": round(statistics.fmean(row["dense_mrr"] for row in expected_rows), 6) if expected_rows else 0.0,
        "fts_recall_at_1": round(
            sum(bool(row["fts_rank"] and row["fts_rank"] <= 1) for row in expected_rows) / len(expected_rows), 6
        ) if expected_rows else 0.0,
        "fts_recall_at_5": round(
            sum(bool(row["fts_recall_at_5"]) for row in expected_rows) / len(expected_rows), 6
        ) if expected_rows else 0.0,
        "fts_would_inject_expected_rate": round(
            sum(bool(row["fts_would_inject_expected"]) for row in expected_rows) / len(expected_rows), 6
        ) if expected_rows else 0.0,
        "incremental_semantic_recovery_rate": rate(rows, "incremental_semantic_recovery"),
        "incremental_semantic_injection_recovery_rate": rate(rows, "incremental_semantic_injection_recovery"),
        "false_positive_candidate_rate": round(false_candidate_count / candidate_count, 6) if candidate_count else 0.0,
        "negative_candidate_rate": rate(negative_rows, "dense_has_candidate") if negative_rows else 0.0,
        "measurement_would_inject_rate": round(
            sum(bool(row["dense_would_inject_ids_measurement_only"]) for row in rows) / len(rows), 6
        ) if rows else 0.0,
        "false_would_inject_query_rate": round(false_injection_queries / len(rows), 6) if rows else 0.0,
        "negative_false_would_inject_rate": round(
            sum(bool(row["dense_false_would_inject_ids_measurement_only"]) for row in negative_rows) / len(negative_rows), 6
        ) if negative_rows else 0.0,
        "zero_candidate_rate": round(sum(not row["dense_candidates"] for row in rows) / len(rows), 6) if rows else 0.0,
        "failure_categories": dict(sorted(failure_categories.items())),
        "query_latency_ms": _distribution([row["dense_query_ms"] for row in rows]),
    }


def _latency_run(
    *,
    cases: list[dict[str, Any]],
    retriever: FTS5ColdMemoryRetriever,
    embedder: OnnxTextEmbedder,
    dense_index: DenseMemoryIndex,
    warmup: int,
    iterations: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    for _ in range(warmup):
        for case in cases:
            _evaluate_case(
                case=case,
                retriever=retriever,
                embedder=embedder,
                dense_index=dense_index,
                latency_iteration=None,
            )
    samples: list[dict[str, Any]] = []
    for iteration in range(iterations):
        for case in cases:
            row = _evaluate_case(
                case=case,
                retriever=retriever,
                embedder=embedder,
                dense_index=dense_index,
                latency_iteration=iteration,
            )
            samples.append(
                {
                    "query_id": row["query_id"],
                    "category": row["category"],
                    "iteration": iteration,
                    "warmup": False,
                    "dense_query_ms": row["dense_query_ms"],
                    "fts_total_ms": row["fts_stage_ms"]["total_ms"],
                    "fts_ms": row["fts_stage_ms"]["fts_ms"],
                    "dense_top1_score": row["dense_top1_score"],
                }
            )
    return samples, {
        "warmup_iterations": warmup,
        "iterations": iterations,
        "sample_count": len(samples),
        "dense_query_ms": _distribution([row["dense_query_ms"] for row in samples]),
        "fts_total_ms": _distribution([row["fts_total_ms"] for row in samples]),
        "fts_ms": _distribution([row["fts_ms"] for row in samples]),
    }


def _download_model(output_root: Path, revision: str) -> tuple[Path, Path, dict[str, Any]]:
    from huggingface_hub import hf_hub_download

    model_dir = output_root / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for filename in MODEL_FILENAMES:
        downloaded = Path(
            hf_hub_download(
                repo_id=MODEL_ID,
                filename=filename,
                revision=revision,
            )
        )
        target = model_dir / Path(filename).name
        if not target.exists():
            target.write_bytes(downloaded.read_bytes())
        paths[filename] = target
    return paths["onnx/model.onnx"], paths["tokenizer.json"], {
        "repo_id": MODEL_ID,
        "revision": revision,
        "source_files": {key: str(value) for key, value in paths.items()},
    }


def _report(
    *,
    config: dict[str, Any],
    metadata: dict[str, Any],
    corpus: dict[str, Any],
    summaries: dict[str, Any],
    latency: dict[str, Any],
    rows: list[dict[str, Any]],
    hot_path_impact: dict[str, Any],
) -> str:
    frozen = summaries["frozen_semantic"]
    paraphrase = summaries["paraphrase_holdout"]
    negatives = summaries["hard_negatives"]
    recovered = sum(row["incremental_semantic_recovery"] for row in rows if row["category"] == "frozen_semantic")
    semantic_total = sum(row["category"] in {"frozen_semantic", "paraphrase"} for row in rows)
    p95 = latency["dense_query_ms"]["p95"]
    false_negative = negatives["negative_false_would_inject_rate"]
    frozen_injection_recoveries = sum(
        row["incremental_semantic_injection_recovery"]
        for row in rows
        if row["category"] == "frozen_semantic"
    )
    if frozen_injection_recoveries >= config["decision_gate"]["minimum_frozen_incremental_injection_recoveries"] and false_negative <= config["decision_gate"]["max_negative_false_would_inject_rate"] and p95 <= config["decision_gate"]["max_dense_query_p95_ms"]:
        recommendation = "Proceed to a separate lexical+dense combination experiment, retaining lexical FTS as the authoritative baseline."
    elif not recovered:
        recommendation = "Do not integrate dense retrieval yet; expand the semantic holdout or improve the experiment evidence before adding retrieval complexity."
    else:
        recommendation = "Keep dense retrieval offline/shadow-only; its current quality, contamination, or latency does not clear the registered integration gate."
    return f"""# ORCHID Phase 2.0 — Dense Retrieval Incremental Value Experiment

## Scope

This run tests a precomputed dense index against the unchanged calibrated
Phase 1 FTS path. Dense candidates were never passed to `ContextAssembler`,
never injected into a model context, never written as events, and never used to
modify ACTIVE. The measurement-only dense would-inject gate is a report
instrument, not a production policy.

## Measured facts

- Model: `{metadata['model_id']}` at revision `{metadata['revision']}`; dimension `{metadata['embedding_dimension']}`, attention-mask mean pooling, L2 normalization, cosine dot product.
- Corpus: `{corpus['memory_count']}` ACTIVE RETIRE fixture memories, including `{corpus['anchor_count']}` durable anchors and `{corpus['distractor_count']}` deterministic distractors.
- Frozen semantic cases: `{frozen['query_count']}`; dense Recall@1/3/5 = `{frozen['recall_at_1']:.1%}` / `{frozen['recall_at_3']:.1%}` / `{frozen['recall_at_5']:.1%}`; incremental top-5 recovery = `{recovered}/{5}`.
- Frozen semantic measurement-only would-injection recovery at the registered 0.50 cutoff: `{frozen_injection_recoveries}/{frozen['query_count']}`.
- Paraphrase holdout: `{paraphrase['query_count']}`; dense Recall@1/3/5 = `{paraphrase['recall_at_1']:.1%}` / `{paraphrase['recall_at_3']:.1%}` / `{paraphrase['recall_at_5']:.1%}`; incremental top-5 recovery over FTS would-inject misses = `{paraphrase['incremental_semantic_recovery_rate']:.1%}`.
- Lexical guardrail FTS would-inject success: `{summaries['lexical_guardrail']['fts_would_inject_expected_rate']:.1%}`; dense does not receive credit for these cases.
- Hard negatives: `{negatives['query_count']}`; measurement-only false would-inject rate = `{negatives['negative_false_would_inject_rate']:.1%}`; top-5 candidate rate = `{negatives['negative_candidate_rate']:.1%}`.
- Dense query latency (embedding plus in-memory cosine search, precomputed memory embeddings excluded): p50 `{latency['dense_query_ms']['p50']:.3f}` ms, p95 `{p95:.3f}` ms, p99 `{latency['dense_query_ms']['p99']:.3f}` ms, mean `{latency['dense_query_ms']['mean']:.3f}` ms, max `{latency['dense_query_ms']['max']:.3f}` ms over `{latency['dense_query_ms']['count']}` samples.
- FTS comparison latency: p50 `{latency['fts_total_ms']['p50']:.3f}` ms, p95 `{latency['fts_total_ms']['p95']:.3f}` ms; FTS code and calibrated policy were not changed by this experiment.
- Production dense injections: `0 by design`; production dense failures/timeouts: `not applicable` because the gateway does not import this module.
- Offline hot-path state fingerprint: unchanged = `{hot_path_impact['state_unchanged']}`; event count remained `{hot_path_impact['state_before']['event_count']}`; ACTIVE-memory and ACTIVE-capsule ID fingerprints were unchanged.

## Interpretation

Dense retrieval is credited only for cases where calibrated FTS did not
would-inject the expected memory. Lexical guardrail queries are recorded in
`quality_results.jsonl` but are not counted as semantic wins. A dense candidate
is not itself an injection: the 0.50 measurement threshold exists only to
quantify contamination risk on this experiment's hard negatives.

The five frozen cases are a small engineering gate, not a general claim about
semantic retrieval. The paraphrase holdout and hard negatives provide the more
useful evidence for whether the observed semantic gap is repeatable.

## Decision gate and recommendation

Registered gate: at least one frozen semantic *injection* recovery at the
measurement cutoff, hard-negative false
would-inject rate at most `{config['decision_gate']['max_negative_false_would_inject_rate']:.1%}`, and dense query p95 at most `{config['decision_gate']['max_dense_query_p95_ms']:.1f}` ms. This is an engineering screening gate, not a learned threshold.

**Recommendation:** {recommendation}

## Artifacts and reproduction

- `SUMMARY.json` — machine-readable result and gate values.
- `quality_results.jsonl` — per-query original input, constructed query, FTS candidates, dense scores, incremental recovery, and failure evidence.
- `latency_samples.jsonl` — per-query latency samples after `{latency['warmup_iterations']}` warm-up iterations.
- `CONFIG.json` — pinned corpus, model revision, threshold, and run parameters.
- `CORPUS.json` — indexed memory metadata and fixture description.
- `dense_embeddings.npz` — generated offline index, not imported by ORCHID.

The dense harness uses optional tooling only; the gateway's mandatory runtime
dependencies are unchanged. In an environment without these packages, install
`numpy`, `onnxruntime`, `tokenizers`, and `huggingface_hub` before running the
command below.

Reproduce from the repository root:

```text
python tests/cold_memory_phase2_dense_experiment.py --output {config['output_root']} --corpus-size {config['corpus_size']} --warmup {config['warmup']} --iterations {config['iterations']} --revision {config['model_revision']}
```

## Non-goals preserved

No vectors were added to the gateway, no RRF/fusion/reranker/graph/raw-history
fallback was added, and no ACTIVE promotion, retrieval policy, or event schema
was changed.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--corpus-size", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--revision", default=MODEL_REVISION)
    args = parser.parse_args()
    if args.corpus_size < len(_anchors()):
        parser.error(f"--corpus-size must be at least {len(_anchors())}")
    if args.warmup < 0 or args.iterations <= 0:
        parser.error("--warmup must be non-negative and --iterations must be positive")

    output_root = args.output
    output_root.mkdir(parents=True, exist_ok=True)
    model_path, tokenizer_path, model_source = _download_model(output_root, args.revision)
    embedder = OnnxTextEmbedder(
        model_path=model_path,
        tokenizer_path=tokenizer_path,
        max_length=256,
    )
    store = _build_store(output_root / "work_quality.db", args.corpus_size)
    hot_state_before = _hot_path_state(store)
    memories = _memory_rows(store)
    dense_index = DenseMemoryIndex.build(memories, embedder)
    dense_index.save(output_root / "dense_embeddings.npz")
    write_json(output_root / "memory_records.json", memories)
    corpus = {
        "project_id": PROJECT_ID,
        "thread_id": THREAD_ID,
        "memory_count": len(memories),
        "anchor_count": len(_anchors()),
        "distractor_count": len(memories) - len(_anchors()),
        "source": "tests/cold_memory_phase1_benchmark.py::_corpus",
        "status_filter": "ACTIVE",
    }
    write_json(output_root / "CORPUS.json", corpus)
    metadata = model_metadata(
        model_id=MODEL_ID,
        revision=args.revision,
        model_path=model_path,
        tokenizer_path=tokenizer_path,
        embedder=embedder,
    )
    metadata["source"] = model_source
    write_json(output_root / "MODEL.json", metadata)

    retriever = FTS5ColdMemoryRetriever(
        store,
        timeout_ms=50,
        candidate_limit=20,
        ranking_policy=CALIBRATED_RANKING_POLICY,
        telemetry_sink=_NoopTelemetry(),
    )
    frozen = list(FROZEN_SEMANTIC_CASES)
    paraphrase = [dict(case, category="paraphrase") for case in _paraphrase_holdout_cases()]
    negatives = _hard_negative_cases()
    lexical_guardrail = [dict(case, category="lexical_guardrail") for case in _acceptance_holdout_queries()]
    all_cases = frozen + paraphrase + negatives + lexical_guardrail
    rows = [
        _evaluate_case(
            case=case,
            retriever=retriever,
            embedder=embedder,
            dense_index=dense_index,
        )
        for case in all_cases
    ]
    quality_summaries = {
        "frozen_semantic": _set_summary([row for row in rows if row["category"] == "frozen_semantic"], "frozen_semantic"),
        "paraphrase_holdout": _set_summary([row for row in rows if row["category"] == "paraphrase"], "paraphrase_holdout"),
        "hard_negatives": _set_summary([row for row in rows if row["category"] == "hard_negative"], "hard_negatives"),
        "lexical_guardrail": _set_summary([row for row in rows if row["category"] == "lexical_guardrail"], "lexical_guardrail"),
    }
    latency_cases = frozen + paraphrase + negatives
    latency_samples, latency_summary = _latency_run(
        cases=latency_cases,
        retriever=retriever,
        embedder=embedder,
        dense_index=dense_index,
        warmup=args.warmup,
        iterations=args.iterations,
    )
    hot_state_after = _hot_path_state(store)
    hot_state_unchanged = hot_state_before == hot_state_after
    _write_jsonl(output_root / "quality_results.jsonl", rows)
    _write_jsonl(output_root / "latency_samples.jsonl", latency_samples)
    config = {
        "phase": "2.0",
        "output_root": str(output_root),
        "corpus_size": args.corpus_size,
        "warmup": args.warmup,
        "iterations": args.iterations,
        "model_id": MODEL_ID,
        "model_revision": args.revision,
        "fts_policy": CALIBRATED_RANKING_POLICY.name,
        "fts_unchanged": True,
        "dense_top_k": 5,
        "dense_measurement_threshold": 0.50,
        "dense_production_injection": False,
        "decision_gate": {
            "minimum_frozen_incremental_recoveries": 1,
            "minimum_frozen_incremental_injection_recoveries": 1,
            "max_negative_false_would_inject_rate": 0.10,
            "max_dense_query_p95_ms": 20.0,
        },
        "case_counts": {
            "frozen_semantic": len(frozen),
            "paraphrase_holdout": len(paraphrase),
            "hard_negatives": len(negatives),
            "lexical_guardrail": len(lexical_guardrail),
        },
    }
    hot_path_impact = {
        "gateway_dense_enabled": False,
        "production_dense_injection_count": 0,
        "active_mutation_by_experiment": False,
        "event_append_by_experiment": False,
        "state_before": hot_state_before,
        "state_after": hot_state_after,
        "state_unchanged": hot_state_unchanged,
    }
    summary = {
        "config": config,
        "model": metadata,
        "corpus": corpus,
        "quality": quality_summaries,
        "latency": latency_summary,
        "frozen_semantic_recoveries": sum(
            row["incremental_semantic_recovery"]
            for row in rows
            if row["category"] == "frozen_semantic"
        ),
        "frozen_semantic_injection_recoveries": sum(
            row["incremental_semantic_injection_recovery"]
            for row in rows
            if row["category"] == "frozen_semantic"
        ),
        "hot_path_impact": hot_path_impact,
    }
    write_json(output_root / "CONFIG.json", config)
    write_json(output_root / "SUMMARY.json", summary)
    (output_root / "REPORT.md").write_text(
        _report(
            config=config,
            metadata=metadata,
            corpus=corpus,
            summaries=quality_summaries,
            latency=latency_summary,
            rows=rows,
            hot_path_impact=hot_path_impact,
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
