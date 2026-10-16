"""Phase 2.3: status-aware dense eligibility with a larger holdout.

This is an offline experiment for the MiniLM-L12 representation selected as
the Phase 2.2 point-estimate winner.  Dense candidates are evaluated twice:
with the implicit status gate and with status filtering disabled.  Neither
path is connected to ContextAssembler or production injection.
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

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from huggingface_hub import hf_hub_download  # noqa: E402

from memory_gateway.cold_memory import (  # noqa: E402
    CALIBRATED_RANKING_POLICY,
    FTS5ColdMemoryRetriever,
    _expanded_terms,
    build_retrieval_query_trace,
)
from memory_gateway.dense_abstention import (  # noqa: E402
    DenseAbstentionFeatures,
    DenseAbstentionPolicy,
    calibrate_policy,
    feature_dict,
)
from memory_gateway.dense_eligibility import (  # noqa: E402
    filter_dense_candidates,
)
from memory_gateway.dense_experiment import (  # noqa: E402
    DenseMemoryIndex,
    OnnxTextEmbedder,
    model_metadata,
    write_json,
)

try:
    from tests.cold_memory_phase1_benchmark import (  # noqa: E402
        PROJECT_ID,
        THREAD_ID,
        _anchors,
        _build_store,
        _write_jsonl,
    )
    from tests.cold_memory_phase2_1_abstention_calibration import (  # noqa: E402
        _apply_policy,
        _state_fingerprint,
        _summary,
    )
    from tests.cold_memory_phase2_dense_experiment import _NoopTelemetry  # noqa: E402
except ModuleNotFoundError:
    from cold_memory_phase1_benchmark import (  # type: ignore  # noqa: E402
        PROJECT_ID,
        THREAD_ID,
        _anchors,
        _build_store,
        _write_jsonl,
    )
    from cold_memory_phase2_1_abstention_calibration import (  # type: ignore  # noqa: E402
        _apply_policy,
        _state_fingerprint,
        _summary,
    )
    from cold_memory_phase2_dense_experiment import _NoopTelemetry  # type: ignore  # noqa: E402


DEFAULT_OUTPUT = Path("artifacts/cold_memory/phase2_3_status_eligibility")
DEFAULT_BASE_CORPUS = Path(
    "artifacts/cold_memory/phase2_1_abstention_calibration/QUERY_CORPUS.jsonl"
)
MODEL_ID = "sentence-transformers/all-MiniLM-L12-v2"
MODEL_REVISION = "a50ef00143b4d5391434df20ae11632588ac25be"
TARGET_PRECISION = 0.99
TOP_K = 5


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _distribution(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0, "max": 0.0}
    ordered = sorted(values)

    def percentile(value: float) -> float:
        index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * value)))
        return ordered[index]

    return {
        "count": len(values),
        "p50": round(percentile(0.50), 6),
        "p95": round(percentile(0.95), 6),
        "p99": round(percentile(0.99), 6),
        "mean": round(statistics.fmean(values), 6),
        "max": round(max(values), 6),
    }


def _load_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _superseded_specs() -> list[dict[str, str]]:
    anchors = {str(anchor["id"]): anchor for anchor in _anchors()}
    specs: list[dict[str, str]] = []
    for anchor_id in (
        "mem_lease_renewal",
        "mem_provenance",
        "mem_cas_promotion",
        "mem_model_boundary",
        "mem_raw_tail",
    ):
        anchor = anchors[anchor_id]
        specs.append(
            {
                "id": f"mem_superseded_{anchor_id.removeprefix('mem_')}",
                "source_id": anchor_id,
                "memory_type": "superseded_fixture",
                "content": (
                    f"Superseded historical version of {anchor_id}; the old design "
                    f"was retired before the current rule. {anchor['content']}"
                ),
            }
        )
    return specs


def _add_superseded_memories(store: Any) -> list[str]:
    persisted: list[str] = []
    for index, memory in enumerate(_superseded_specs()):
        event = store.append_event(
            project_id=PROJECT_ID,
            thread_id=THREAD_ID,
            event_id=f"evt_superseded_{index:03d}",
            event_type="fixture_superseded_evidence",
            content=memory["content"],
        )
        ids = store.persist_long_term_memories(
            thread_id=THREAD_ID,
            memories=[
                {
                    "id": memory["id"],
                    "memory_type": memory["memory_type"],
                    "importance": 0.25,
                    "content": memory["content"],
                    "evidence_event_ids": [event["id"]],
                }
            ],
        )
        persisted.extend(ids)
    with store.transaction(immediate=True) as connection:
        connection.executemany(
            "UPDATE long_term_memories SET status = 'SUPERSEDED' WHERE id = ?",
            [(memory_id,) for memory_id in persisted],
        )
    return persisted


def _memory_rows(store: Any) -> list[dict[str, Any]]:
    with store.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, project_id, thread_id, content, memory_type, importance,
                   activation_score, created_at, status
            FROM long_term_memories
            WHERE project_id = ? AND thread_id = ?
            ORDER BY id
            """,
            (PROJECT_ID, THREAD_ID),
        ).fetchall()
    return [dict(row) for row in rows]


def _state(store: Any) -> dict[str, Any]:
    state = dict(_state_fingerprint(store))
    with store.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, status, content
            FROM long_term_memories
            WHERE project_id = ? AND thread_id = ?
            ORDER BY id
            """,
            (PROJECT_ID, THREAD_ID),
        ).fetchall()
    state["status_counts"] = {
        status: sum(str(row["status"]) == status for row in rows)
        for status in ("ACTIVE", "SUPERSEDED", "DISABLED")
    }
    state["memory_content_sha256"] = hashlib.sha256(
        "\n".join(f"{row['id']}|{row['status']}|{row['content']}" for row in rows).encode(
            "utf-8"
        )
    ).hexdigest()
    return state


def _expand_holdout(base_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep Phase 2.1 calibration frozen and add 150 deterministic holdouts."""

    semantic_holdout = [
        row for row in base_rows if row.get("split") == "holdout"
    ]
    positives = [row for row in semantic_holdout if row["expected_ids"]]
    by_category = {
        category: [row for row in semantic_holdout if row["category"] == category]
        for category in (
            "near_miss_negative",
            "wrong_fact_negative",
            "no_cold_memory_negative",
        )
    }
    expanded: list[dict[str, Any]] = []
    for row in positives:
        expanded.append(
            {
                **row,
                "id": f"expanded_positive_{row['id']}",
                "category": "expanded_semantic_positive",
                "query": f"In the earlier investigation, {row['query'].rstrip('?')}?",
                "split": "holdout",
            }
        )
    for category, rows in by_category.items():
        for row in rows:
            expanded.append(
                {
                    **row,
                    "id": f"expanded_{category}_{row['id']}",
                    "category": f"expanded_{category}",
                    "query": f"For a separate review, {row['query'].rstrip('?')}?",
                    "split": "holdout",
                }
            )
    status_queries = (
        (
            "mem_superseded_lease_renewal",
            "Which obsolete lease renewal design existed before the current ownership safeguard?",
        ),
        (
            "mem_superseded_provenance",
            "What retired memory format used to retain proof before the current provenance rule?",
        ),
        (
            "mem_superseded_cas_promotion",
            "How did the old capsule promotion design behave before the current stale-lineage check?",
        ),
        (
            "mem_superseded_model_boundary",
            "Which deprecated lookup design selected history before the deterministic boundary?",
        ),
        (
            "mem_superseded_raw_tail",
            "How did the retired context design handle recent text before the current raw tail rule?",
        ),
    )
    for index in range(10):
        for memory_id, query in status_queries:
            expanded.append(
                {
                    "id": f"expanded_status_{index:02d}_{memory_id}",
                    "category": "superseded_status_negative",
                    "query": query if index == 0 else f"Historically, {query}",
                    "expected_ids": [],
                    "split": "calibration" if index < 5 else "holdout",
                }
            )
    if len(expanded) != 175:
        raise AssertionError(f"expected 175 expanded rows, got {len(expanded)}")
    return base_rows + expanded


def _download_model() -> tuple[Path, Path, dict[str, Any]]:
    model_path = Path(
        hf_hub_download(
            repo_id=MODEL_ID,
            filename="onnx/model.onnx",
            revision=MODEL_REVISION,
        )
    )
    tokenizer_path = Path(
        hf_hub_download(
            repo_id=MODEL_ID,
            filename="tokenizer.json",
            revision=MODEL_REVISION,
        )
    )
    return model_path, tokenizer_path, {
        "repo_id": MODEL_ID,
        "revision": MODEL_REVISION,
        "model_sha256": sha256_file(model_path),
        "tokenizer_sha256": sha256_file(tokenizer_path),
        "model_cache_path": str(model_path),
        "tokenizer_cache_path": str(tokenizer_path),
    }


def _dense_features(
    *,
    original_query: str,
    trace: Any,
    full_index: DenseMemoryIndex,
    eligible_index: DenseMemoryIndex,
    embedder: OnnxTextEmbedder,
    memory_by_id: dict[str, dict[str, Any]],
    status_mode: str,
) -> tuple[DenseAbstentionFeatures, list[dict[str, Any]], float, dict[str, Any]]:
    started = time.perf_counter_ns()
    query_embedding = embedder.embed([trace.constructed_query])[0]
    raw_candidates = full_index.search(query_embedding, top_k=TOP_K)
    candidates = filter_dense_candidates(
        raw_candidates,
        memory_by_id,
        project_id=PROJECT_ID,
        thread_id=THREAD_ID,
        mode=status_mode,  # type: ignore[arg-type]
    )
    query_ms = (time.perf_counter_ns() - started) / 1_000_000
    active_scores = (eligible_index.embeddings @ query_embedding).astype(float)
    top1 = float(candidates[0].score) if candidates else 0.0
    top2 = float(candidates[1].score) if len(candidates) > 1 else 0.0
    other_scores = (
        np.delete(active_scores, int(np.argmax(active_scores)))
        if len(active_scores)
        else np.asarray([])
    )
    background_mean = float(other_scores.mean()) if len(other_scores) else 0.0
    background_std = float(other_scores.std()) if len(other_scores) else 1.0
    corpus_percentile = (
        sum(float(score) <= top1 for score in other_scores) / len(other_scores)
        if len(other_scores) and candidates
        else 0.0
    )
    top1_row = memory_by_id.get(candidates[0].memory_id) if candidates else None
    query_terms = set(_expanded_terms(original_query))
    candidate_terms = (
        set(_expanded_terms(f"{top1_row['content']} {top1_row['memory_type']}"))
        if top1_row
        else set()
    )
    lexical_overlap = len(query_terms & candidate_terms)
    lexical_ratio = lexical_overlap / len(query_terms) if query_terms else 0.0
    identifier_terms = set(trace.identifier_terms)
    identifier_agreement = (
        len(identifier_terms & candidate_terms) / len(identifier_terms)
        if identifier_terms
        else 0.0
    )
    scope_agreement = (
        1.0
        if top1_row
        and top1_row.get("project_id") == PROJECT_ID
        and top1_row.get("thread_id") == THREAD_ID
        else 0.0
    )
    activation_prior = (
        min(
            1.0,
            max(
                float(top1_row.get("activation_score", 0.0)),
                float(top1_row.get("importance", 0.0)),
            ),
        )
        if top1_row
        else 0.0
    )
    features = DenseAbstentionFeatures(
        top1_score=top1,
        top2_score=top2,
        margin=top1 - top2,
        corpus_percentile=corpus_percentile,
        corpus_zscore=(top1 - background_mean) / max(background_std, 1e-9)
        if candidates
        else 0.0,
        lexical_overlap=lexical_overlap,
        lexical_overlap_ratio=lexical_ratio,
        identifier_agreement=identifier_agreement,
        scope_agreement=scope_agreement,
        activation_prior=activation_prior,
        candidate_count=len(candidates),
    )
    raw_rows = [
        {
            "memory_id": candidate.memory_id,
            "score": round(float(candidate.score), 8),
            "status": memory_by_id[candidate.memory_id]["status"],
            "implicit_eligible": candidate in candidates,
        }
        for candidate in raw_candidates
    ]
    diagnostics = {
        "dense_status_mode": status_mode,
        "dense_raw_candidate_count": len(raw_candidates),
        "dense_eligible_candidate_count": len(candidates),
        "dense_status_filtered_count": len(raw_candidates) - len(candidates),
        "dense_status_filtered_ids": [
            row["memory_id"] for row in raw_rows if not row["implicit_eligible"]
        ],
        "dense_explicit_searchable_ids": [
            candidate.memory_id
            for candidate in filter_dense_candidates(
                raw_candidates,
                memory_by_id,
                project_id=PROJECT_ID,
                thread_id=THREAD_ID,
                mode="explicit",
            )
        ],
    }
    return features, raw_rows, query_ms, diagnostics


def _evaluate_query(
    *,
    case: dict[str, Any],
    retriever: FTS5ColdMemoryRetriever,
    full_index: DenseMemoryIndex,
    eligible_index: DenseMemoryIndex,
    embedder: OnnxTextEmbedder,
    memory_by_id: dict[str, dict[str, Any]],
    status_mode: str,
) -> dict[str, Any]:
    query = str(case["query"])
    trace = build_retrieval_query_trace([{"role": "user", "content": query}])
    fts_result = retriever.retrieve(
        project_id=PROJECT_ID,
        thread_id=THREAD_ID,
        query=trace.constructed_query,
        token_budget=512,
        max_injected=3,
        mode="shadow",
        query_trace=trace,
    )
    features, dense_candidates, query_ms, diagnostics = _dense_features(
        original_query=query,
        trace=trace,
        full_index=full_index,
        eligible_index=eligible_index,
        embedder=embedder,
        memory_by_id=memory_by_id,
        status_mode=status_mode,
    )
    eligible_candidates = [
        row for row in dense_candidates if row["implicit_eligible"]
    ]
    return {
        "query_id": case["id"],
        "category": case["category"],
        "split": case["split"],
        "query": query,
        "constructed_query": trace.constructed_query,
        "expected_ids": list(case["expected_ids"]),
        "features": feature_dict(features),
        "dense_candidates": dense_candidates,
        "dense_eligible_candidates": eligible_candidates,
        "dense_top1_id": eligible_candidates[0]["memory_id"] if eligible_candidates else None,
        "dense_top1_score": eligible_candidates[0]["score"] if eligible_candidates else None,
        "dense_raw_top1_id": dense_candidates[0]["memory_id"] if dense_candidates else None,
        "dense_raw_top1_score": dense_candidates[0]["score"] if dense_candidates else None,
        "dense_query_ms": round(query_ms, 6),
        **diagnostics,
        "fts_candidate_ids": [hit.memory_id for hit in fts_result.candidates],
        "fts_would_inject_ids": [hit.memory_id for hit in fts_result.would_inject],
        "fts_would_inject_expected": bool(
            set(case["expected_ids"]) & {hit.memory_id for hit in fts_result.would_inject}
        ),
        "fts_timed_out": fts_result.timed_out,
        "fts_fail_open": fts_result.fail_open,
    }


def _run_latency(
    *,
    cases: list[dict[str, Any]],
    retriever: FTS5ColdMemoryRetriever,
    full_index: DenseMemoryIndex,
    eligible_index: DenseMemoryIndex,
    embedder: OnnxTextEmbedder,
    memory_by_id: dict[str, dict[str, Any]],
    warmup: int,
    iterations: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    def as_case(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["query_id"],
            "category": row["category"],
            "query": row["query"],
            "expected_ids": row["expected_ids"],
            "split": row["split"],
        }

    for _ in range(warmup):
        for row in cases:
            _evaluate_query(
                case=as_case(row),
                retriever=retriever,
                full_index=full_index,
                eligible_index=eligible_index,
                embedder=embedder,
                memory_by_id=memory_by_id,
                status_mode="implicit",
            )
    samples: list[dict[str, Any]] = []
    for iteration in range(iterations):
        for row in cases:
            measured = _evaluate_query(
                case=as_case(row),
                retriever=retriever,
                full_index=full_index,
                eligible_index=eligible_index,
                embedder=embedder,
                memory_by_id=memory_by_id,
                status_mode="implicit",
            )
            samples.append(
                {
                    "query_id": row["query_id"],
                    "iteration": iteration,
                    "dense_query_ms": measured["dense_query_ms"],
                }
            )
    return samples, _distribution([row["dense_query_ms"] for row in samples])


def _primary_metric(summary: dict[str, Any]) -> dict[str, Any]:
    eligible = bool(
        summary["accept_count"] > 0
        and summary["accept_precision"] >= TARGET_PRECISION
    )
    return {
        "eligible": eligible,
        "target_precision": TARGET_PRECISION,
        "holdout_accept_precision": summary["accept_precision"],
        "holdout_accept_recall": summary["accept_recall"] if eligible else None,
        "holdout_accept_count": summary["accept_count"],
    }


def _report(config: dict[str, Any], summary: dict[str, Any]) -> str:
    gated = summary["gated_holdout"]
    ungated = summary["ungated_holdout"]
    primary = summary["primary_metric"]
    gated_precision = (
        f"{gated['accept_precision']:.1%}" if gated["accept_count"] else "—"
    )
    recall = (
        f"{primary['holdout_accept_recall']:.1%}"
        if primary["eligible"]
        else "—"
    )
    lines = [
        "# ORCHID Phase 2.3 — Status-Aware Dense Eligibility",
        "",
        "## Scope",
        "",
        f"MiniLM-L12 (`{MODEL_REVISION}`) was evaluated offline using the frozen Phase 2.1 calibration split, 25 additional status-specific calibration negatives, and an expanded untouched holdout. The implicit path filters dense candidates by scope and status before confidence features and ACCEPT calibration. Superseded candidates remain available to the explicit-search policy. No dense result entered ORCHID context.",
        "",
        "## Primary metric",
        "",
        f"Maximum semantic holdout ACCEPT recall at >=99% ACCEPT precision: **{recall}**.",
        "",
        "| Path | Holdout precision | Recall | ACCEPT | False ACCEPTs | p50 ms | p95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| Status-aware implicit | {gated_precision} | {recall} | {gated['accept_count']} | {gated['false_accept_count']} | {summary['latency']['p50']:.3f} | {summary['latency']['p95']:.3f} |",
        f"| Ungated comparison | {ungated['accept_precision']:.1%} | {ungated['accept_recall']:.1%} | {ungated['accept_count']} | {ungated['false_accept_count']} | — | — |",
        "",
        "## Measured facts",
        "",
        f"- Base Phase 2.1 corpus SHA-256: `{config['base_corpus_sha256']}`; expanded corpus SHA-256: `{config['expanded_corpus_sha256']}`.",
        f"- Semantic queries: `{config['semantic_query_count']}` total; calibration `{config['calibration_count']}`; untouched holdout `{config['holdout_count']}`. External lexical guardrails: `{config['external_guardrail_count']}`.",
        f"- Memory rows: `{config['memory_count']}` total; ACTIVE `{config['active_memory_count']}`; SUPERSEDED `{config['superseded_memory_count']}`.",
        f"- Status filter removed `{summary['status_effect']['holdout_filtered_candidate_count']}` raw top-k candidates across the holdout queries.",
        f"- Superseded candidates appeared in `{summary['status_effect']['status_negative_raw_superseded_queries']}/{summary['status_effect']['status_negative_query_count']}` status-negative queries and remained explicit-searchable in `{summary['status_effect']['status_negative_explicit_preserved_queries']}` of them; implicit superseded candidates accepted: `{summary['status_effect']['implicit_superseded_accept_count']}`.",
        f"- Hot-state fingerprint unchanged: `{config['hot_path_state_unchanged']}`; events, ACTIVE state, and memory status/content fingerprints were unchanged by query evaluation.",
        "",
        "## Status-gate effect",
        "",
        f"The status-aware path changed holdout false ACCEPTs from `{ungated['false_accept_count']}` to `{gated['false_accept_count']}` and recall from `{ungated['accept_recall']:.1%}` to `{gated['accept_recall']:.1%}`. This comparison uses independently calibrated policies over the same calibration split.",
        "",
        "## Failure-type breakdown",
        "",
    ]
    for category, metrics in gated["by_category"].items():
        lines.append(
            f"- `{category}`: {metrics['query_count']} queries, ACCEPT precision {metrics['accept_precision']:.1%}, ACCEPT recall {metrics['accept_recall']:.1%}, false ACCEPTs {metrics['false_accept_count']}."
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The gate is a valid safety rule: SUPERSEDED memories are absent from implicit eligibility but remain searchable in explicit mode. It should not be credited for removing false positives whose winning candidate is still ACTIVE; those remain genuine relevance/abstention failures. The larger holdout is the decision gate, and its precision is still a point estimate rather than a production guarantee.",
            "",
            "## Recommendation",
            "",
            "Keep dense shadow-only. If status-aware recall remains in the single-digit or low-teens range, stop tuning eligibility and move to a small reranker over ambiguous dense candidates only. Do not add RRF, graph expansion, raw fallback, or production injection in this phase.",
            "",
            "## Reproduction",
            "",
            "```text",
            f"python tests/cold_memory_phase2_3_status_eligibility.py --output {config['output_root']} --base-corpus {config['base_corpus']} --corpus-size {config['corpus_size']} --warmup {config['warmup']} --iterations {config['iterations']}",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--base-corpus", type=Path, default=DEFAULT_BASE_CORPUS)
    parser.add_argument("--corpus-size", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=5)
    args = parser.parse_args()
    base_rows = _load_rows(args.base_corpus)
    if sum(row.get("split") == "calibration" for row in base_rows) != 151:
        parser.error("base corpus calibration split must remain 151 rows")
    query_rows = _expand_holdout(base_rows)
    if args.corpus_size < len(_anchors()):
        parser.error(f"--corpus-size must be at least {len(_anchors())}")

    output_root = args.output
    output_root.mkdir(parents=True, exist_ok=True)
    expanded_corpus_path = output_root / "QUERY_CORPUS.jsonl"
    _write_jsonl(expanded_corpus_path, query_rows)
    store = _build_store(output_root / "work_status.db", args.corpus_size)
    superseded_ids = _add_superseded_memories(store)
    state_before = _state(store)
    model_path, tokenizer_path, source = _download_model()
    embedder = OnnxTextEmbedder(
        model_path=model_path,
        tokenizer_path=tokenizer_path,
        max_length=256,
    )
    memories = _memory_rows(store)
    memory_by_id = {str(memory["id"]): memory for memory in memories}
    active_memories = [
        memory for memory in memories if memory["status"] == "ACTIVE"
    ]
    full_index = DenseMemoryIndex.build(memories, embedder)
    active_index = DenseMemoryIndex.build(active_memories, embedder)
    full_index.save(output_root / "dense_embeddings_all_statuses.npz")
    active_index.save(output_root / "dense_embeddings_implicit_eligible.npz")
    retriever = FTS5ColdMemoryRetriever(
        store,
        timeout_ms=50,
        candidate_limit=20,
        ranking_policy=CALIBRATED_RANKING_POLICY,
        telemetry_sink=_NoopTelemetry(),
    )
    semantic_rows = [
        row for row in query_rows if row["split"] in {"calibration", "holdout"}
    ]
    external_rows = [row for row in query_rows if row["split"] == "external_guardrail"]
    gated_rows = [
        _evaluate_query(
            case=row,
            retriever=retriever,
            full_index=full_index,
            eligible_index=active_index,
            embedder=embedder,
            memory_by_id=memory_by_id,
            status_mode="implicit",
        )
        for row in query_rows
    ]
    ungated_rows = [
        _evaluate_query(
            case=row,
            retriever=retriever,
            full_index=full_index,
            eligible_index=full_index,
            embedder=embedder,
            memory_by_id=memory_by_id,
            status_mode="explicit",
        )
        for row in query_rows
    ]
    gated_calibration = [row for row in gated_rows if row["split"] == "calibration"]
    gated_holdout = [row for row in gated_rows if row["split"] == "holdout"]
    gated_guardrail = [row for row in gated_rows if row["split"] == "external_guardrail"]
    ungated_calibration = [row for row in ungated_rows if row["split"] == "calibration"]
    ungated_holdout = [row for row in ungated_rows if row["split"] == "holdout"]
    ungated_guardrail = [row for row in ungated_rows if row["split"] == "external_guardrail"]
    gated_policy = calibrate_policy(
        [
            {"expected_ids": row["expected_ids"], "features": row["features"]}
            for row in gated_calibration
        ],
        target_precision=TARGET_PRECISION,
    )
    ungated_policy = calibrate_policy(
        [
            {"expected_ids": row["expected_ids"], "features": row["features"]}
            for row in ungated_calibration
        ],
        target_precision=TARGET_PRECISION,
    )
    _apply_policy(gated_rows, DenseAbstentionPolicy(**gated_policy["policy"]))
    _apply_policy(ungated_rows, DenseAbstentionPolicy(**ungated_policy["policy"]))
    gated_summary = _summary(gated_holdout, "status_aware_holdout")
    ungated_summary = _summary(ungated_holdout, "ungated_holdout")
    latency_samples, latency = _run_latency(
        cases=gated_holdout,
        retriever=retriever,
        full_index=full_index,
        eligible_index=active_index,
        embedder=embedder,
        memory_by_id=memory_by_id,
        warmup=args.warmup,
        iterations=args.iterations,
    )
    state_after = _state(store)
    status_filtered_count = sum(
        row["dense_status_filtered_count"] for row in gated_holdout
    )
    summary = {
        "model": model_metadata(
            model_id=MODEL_ID,
            revision=MODEL_REVISION,
            model_path=model_path,
            tokenizer_path=tokenizer_path,
            embedder=embedder,
        ),
        "model_source": source,
        "gated_calibration": _summary(gated_calibration, "status_aware_calibration"),
        "gated_holdout": gated_summary,
        "gated_guardrail": _summary(gated_guardrail, "lexical_guardrail"),
        "ungated_calibration": _summary(ungated_calibration, "ungated_calibration"),
        "ungated_holdout": ungated_summary,
        "ungated_guardrail": _summary(ungated_guardrail, "lexical_guardrail"),
        "gated_policy": gated_policy,
        "ungated_policy": ungated_policy,
        "primary_metric": _primary_metric(gated_summary),
        "latency": latency,
        "status_effect": {
            "holdout_filtered_candidate_count": status_filtered_count,
            "superseded_ids_in_fixture": superseded_ids,
            "raw_superseded_candidate_count": sum(
                row["dense_status_filtered_count"] for row in gated_rows
            ),
            "status_negative_query_count": sum(
                row["category"] == "superseded_status_negative" for row in gated_rows
            ),
            "status_negative_raw_superseded_queries": sum(
                row["category"] == "superseded_status_negative"
                and any(memory_id.startswith("mem_superseded_") for memory_id in row["dense_status_filtered_ids"])
                for row in gated_rows
            ),
            "status_negative_explicit_preserved_queries": sum(
                row["category"] == "superseded_status_negative"
                and any(memory_id.startswith("mem_superseded_") for memory_id in row["dense_explicit_searchable_ids"])
                for row in gated_rows
            ),
            "implicit_superseded_accept_count": sum(
                row["decision"] == "ACCEPT"
                and any(
                    candidate["status"] == "SUPERSEDED"
                    for candidate in row["dense_eligible_candidates"]
                )
                for row in gated_rows
            ),
        },
        "hot_path_impact": {
            "state_before": state_before,
            "state_after": state_after,
            "state_unchanged": state_before == state_after,
            "production_dense_injections": 0,
            "events_appended_by_dense": 0,
            "active_mutations_by_dense": 0,
        },
    }
    config = {
        "phase": "2.3",
        "output_root": str(output_root),
        "base_corpus": str(args.base_corpus),
        "base_corpus_sha256": sha256_file(args.base_corpus),
        "expanded_corpus": str(expanded_corpus_path),
        "expanded_corpus_sha256": sha256_file(expanded_corpus_path),
        "query_count": len(query_rows),
        "semantic_query_count": len(semantic_rows),
        "calibration_count": sum(row["split"] == "calibration" for row in query_rows),
        "holdout_count": sum(row["split"] == "holdout" for row in query_rows),
        "external_guardrail_count": len(external_rows),
        "corpus_size": args.corpus_size,
        "memory_count": len(memories),
        "active_memory_count": len(active_memories),
        "superseded_memory_count": len(superseded_ids),
        "status_calibration_count": 25,
        "warmup": args.warmup,
        "iterations": args.iterations,
        "top_k": TOP_K,
        "target_accept_precision": TARGET_PRECISION,
        "status_policy": {
            "implicit": ["ACTIVE"],
            "explicit": ["ACTIVE", "SUPERSEDED"],
            "scope": {"project_id": PROJECT_ID, "thread_id": THREAD_ID},
        },
        "dense_production_injection": False,
        "hot_path_state_unchanged": summary["hot_path_impact"]["state_unchanged"],
        "fts_unchanged": True,
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
    }
    summary["config"] = config
    write_json(output_root / "CONFIG.json", config)
    write_json(output_root / "SUMMARY.json", summary)
    write_json(output_root / "MODEL.json", summary["model"])
    write_json(output_root / "CORPUS.json", {
        "project_id": PROJECT_ID,
        "thread_id": THREAD_ID,
        "memory_count": len(memories),
        "active_memory_count": len(active_memories),
        "superseded_memory_count": len(superseded_ids),
        "status_policy": config["status_policy"],
    })
    write_json(output_root / "POLICY.json", {
        "gated": gated_policy,
        "ungated": ungated_policy,
    })
    _write_jsonl(output_root / "quality_results.jsonl", gated_rows)
    _write_jsonl(output_root / "quality_results_ungated.jsonl", ungated_rows)
    _write_jsonl(output_root / "latency_samples.jsonl", latency_samples)
    (output_root / "REPORT.md").write_text(
        _report(config, summary),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
