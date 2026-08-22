"""Phase 2.4 reranker-only shadow evaluation.

MiniLM-L12 dense candidates and Phase 2.3 status eligibility are fixed.  A
small ONNX cross-encoder scores only the top five candidates for queries that
do not already have a confident calibrated FTS hit.  Nothing is injected into
gateway context.
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

from huggingface_hub import hf_hub_download  # noqa: E402

from memory_gateway.cold_memory import (  # noqa: E402
    CALIBRATED_RANKING_POLICY,
    FTS5ColdMemoryRetriever,
    build_retrieval_query_trace,
)
from memory_gateway.dense_experiment import (  # noqa: E402
    DenseMemoryIndex,
    OnnxTextEmbedder,
)
from memory_gateway.dense_reranker import (  # noqa: E402
    OnnxCrossEncoderReranker,
    RerankerPolicy,
    apply_reranker_policy,
    calibrate_reranker_policy,
)
from memory_gateway.db import SQLiteStore  # noqa: E402

try:
    from tests.cold_memory_phase1_benchmark import PROJECT_ID, THREAD_ID, _write_jsonl  # noqa: E402
    from tests.cold_memory_phase2_3_status_eligibility import _state  # noqa: E402
    from tests.cold_memory_phase2_dense_experiment import _NoopTelemetry  # noqa: E402
except ModuleNotFoundError:
    from cold_memory_phase1_benchmark import PROJECT_ID, THREAD_ID, _write_jsonl  # type: ignore  # noqa: E402
    from cold_memory_phase2_3_status_eligibility import _state  # type: ignore  # noqa: E402
    from cold_memory_phase2_dense_experiment import _NoopTelemetry  # type: ignore  # noqa: E402


DEFAULT_OUTPUT = Path("artifacts/cold_memory/phase2_4_reranker_shadow")
DEFAULT_PHASE23 = Path("artifacts/cold_memory/phase2_3_status_eligibility")
L12_MODEL_ID = "sentence-transformers/all-MiniLM-L12-v2"
L12_REVISION = "a50ef00143b4d5391434df20ae11632588ac25be"
RERANKER_MODEL_ID = "cross-encoder/ms-marco-MiniLM-L-2-v2"
RERANKER_REVISION = "1b5cd67b15209f24824c50370e0397743aa9b787"
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


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _extra_holdout_cases() -> list[dict[str, Any]]:
    """Add calibration/holdout negatives without changing the base corpus."""

    current_irrelevant = {
        "mem_lease_renewal": (
            "Which dashboard color is associated with renew_lease?",
            "Which UI component displays lease_until ownership?",
            "What product announcement describes the worker ownership screen?",
            "Which CSS class styles the expired-worker warning?",
            "What user-facing label appears beside the lease renewal button?",
            "Which navigation tab contains the renew_lease control?",
            "What tooltip text explains the lease_until indicator?",
        ),
        "mem_provenance": (
            "Which dashboard icon represents memory_evidence?",
            "What UI color is used for an immutable event_id?",
            "Which product owner approves the provenance screen?",
            "What frontend route renders the evidence timeline?",
            "Which CSS class formats the source-history panel?",
            "Which navigation tab contains the memory_evidence view?",
            "What tooltip explains the immutable event_id field?",
        ),
        "mem_cas_promotion": (
            "Which dashboard widget shows stale_revision warnings?",
            "What UI label names the capsule promotion button?",
            "Which product requirement covers the capsule settings screen?",
            "What CSS color marks a compare-and-swap success?",
            "Which frontend component owns the promotion toast?",
            "Which navigation tab contains the stale_revision warning?",
            "What tooltip describes the capsule promotion action?",
        ),
        "mem_model_boundary": (
            "Which dashboard theme displays deterministic query terms?",
            "What UI button starts an LLM query rewrite?",
            "Which product owner reviews the file-path search screen?",
            "What CSS class styles the retrieval settings panel?",
            "Which frontend route lists model boundary preferences?",
            "Which navigation tab contains deterministic query settings?",
            "What tooltip describes the file-path search control?",
        ),
        "mem_raw_tail": (
            "Which dashboard color represents the recent raw tail?",
            "What UI button summarizes the newest conversation?",
            "Which product owner reviews the verbatim tail panel?",
            "What CSS class styles the latest tool-result display?",
            "Which frontend route renders raw-tail preferences?",
            "Which navigation tab contains recent raw-tail settings?",
            "What tooltip explains the newest tool-result display?",
        ),
    }
    near_duplicate = {
        "mem_lease_renewal": (
            "Which rejected near-duplicate rule said an expired worker may renew any job?",
            "Which obsolete-looking note claimed lease_until can be ignored during renewal?",
            "What non-existent variant allowed ownership to transfer after renewal?",
            "Which fabricated lease rule reversed the current worker ownership safeguard?",
            "What rejected duplicate memory said stale workers should keep control?",
            "Which invented lease note allowed an expired worker to renew another worker's job?",
            "What fake ownership policy made stale renewal authoritative?",
        ),
        "mem_provenance": (
            "Which rejected near-duplicate rule said evidence can be discarded after summary?",
            "Which obsolete-looking note claimed memory_evidence replaces event history?",
            "What non-existent variant detached a remembered decision from its events?",
            "Which fabricated provenance rule made a semantic row authoritative?",
            "What rejected duplicate memory said raw evidence was unnecessary?",
            "Which invented evidence note removed the event_id provenance link?",
            "What fake history policy treated the summary as the only record?",
        ),
        "mem_cas_promotion": (
            "Which rejected near-duplicate rule let stale_revision overwrite the capsule?",
            "Which obsolete-looking note claimed every completed job may promote state?",
            "What non-existent variant removed lineage checks from promotion?",
            "Which fabricated CAS rule allowed an older snapshot to win?",
            "What rejected duplicate memory made competing writers unconditional?",
            "Which invented promotion note ignored the stale_revision compare?",
            "What fake capsule policy let an old writer overwrite newer state?",
        ),
        "mem_model_boundary": (
            "Which rejected near-duplicate rule asked an LLM to rewrite every query?",
            "Which obsolete-looking note claimed files and symbols were ignored?",
            "What non-existent variant made query construction model-generated?",
            "Which fabricated boundary rule put a query model in the critical path?",
            "What rejected duplicate memory removed deterministic lookup signals?",
            "Which invented search note routed every request through an LLM?",
            "What fake boundary policy discarded exact file and symbol terms?",
        ),
        "mem_raw_tail": (
            "Which rejected near-duplicate rule summarized the newest raw events?",
            "Which obsolete-looking note claimed the raw tail could be discarded?",
            "What non-existent variant paraphrased every current tool result?",
            "Which fabricated tail rule let ACTIVE replace recent verbatim history?",
            "What rejected duplicate memory made fresh details optional?",
            "Which invented history note discarded the newest raw observations?",
            "What fake tail policy treated ACTIVE as a substitute for fresh events?",
        ),
    }
    cases: list[dict[str, Any]] = []
    for category, groups in (
        ("current_irrelevant_negative", current_irrelevant),
        ("near_duplicate_negative", near_duplicate),
    ):
        for memory_id, queries in groups.items():
            for index, query in enumerate(queries):
                cases.append(
                    {
                        "id": f"phase24_{category}_{memory_id}_{index:02d}",
                        "category": category,
                        "query": query,
                        "expected_ids": [],
                        "split": "calibration" if index < 2 else "holdout",
                    }
                )
    assert len(cases) == 70
    assert sum(row["split"] == "calibration" for row in cases) == 20
    assert sum(row["split"] == "holdout" for row in cases) == 50
    return cases


def _memory_rows(db_path: Path) -> dict[str, dict[str, Any]]:
    with SQLiteStore(db_path).connect() as connection:
        rows = connection.execute(
            """
            SELECT id, project_id, thread_id, content, memory_type, status
            FROM long_term_memories
            WHERE project_id = ? AND thread_id = ?
            ORDER BY id
            """,
            (PROJECT_ID, THREAD_ID),
        ).fetchall()
    return {str(row["id"]): dict(row) for row in rows}


def _new_dense_row(
    *,
    case: dict[str, Any],
    retriever: FTS5ColdMemoryRetriever,
    active_index: DenseMemoryIndex,
    embedder: OnnxTextEmbedder,
    memory_by_id: dict[str, dict[str, Any]],
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
    started = time.perf_counter_ns()
    query_embedding = embedder.embed([trace.constructed_query])[0]
    candidates = active_index.search(query_embedding, top_k=TOP_K)
    dense_query_ms = (time.perf_counter_ns() - started) / 1_000_000
    dense_rows = [
        {
            "memory_id": candidate.memory_id,
            "score": round(float(candidate.score), 8),
            "status": memory_by_id[candidate.memory_id]["status"],
            "implicit_eligible": True,
        }
        for candidate in candidates
    ]
    return {
        "query_id": case["id"],
        "category": case["category"],
        "split": case["split"],
        "query": query,
        "constructed_query": trace.constructed_query,
        "expected_ids": list(case["expected_ids"]),
        "dense_candidates": dense_rows,
        "dense_eligible_candidates": dense_rows,
        "dense_query_ms": round(dense_query_ms, 6),
        "fts_candidate_ids": [hit.memory_id for hit in fts_result.candidates],
        "fts_would_inject_ids": [hit.memory_id for hit in fts_result.would_inject],
        "fts_would_inject_expected": bool(
            set(case["expected_ids"]) & {hit.memory_id for hit in fts_result.would_inject}
        ),
        "fts_timed_out": fts_result.timed_out,
        "fts_fail_open": fts_result.fail_open,
    }


def _score_row(
    row: dict[str, Any],
    *,
    reranker: OnnxCrossEncoderReranker,
    memory_by_id: dict[str, dict[str, Any]],
    top_k: int,
) -> None:
    candidates = list(row.get("dense_eligible_candidates", ()))[:top_k]
    if row.get("fts_would_inject_ids"):
        row.update(
            {
                "reranker_evaluated": False,
                "reranker_bypass_reason": "confident_lexical_hit",
                "reranker_candidates": [],
                "reranker_candidate_count": 0,
                "reranker_top1_id": None,
                "reranker_top1_score": None,
                "reranker_margin": 0.0,
                "reranker_ms": 0.0,
            }
        )
        return
    row["reranker_evaluated"] = True
    row["reranker_bypass_reason"] = None
    if not candidates:
        row.update(
            {
                "reranker_candidates": [],
                "reranker_candidate_count": 0,
                "reranker_top1_id": None,
                "reranker_top1_score": None,
                "reranker_margin": 0.0,
                "reranker_ms": 0.0,
            }
        )
        return
    pairs = [
        (row["query"], memory_by_id[candidate["memory_id"]]["content"])
        for candidate in candidates
    ]
    started = time.perf_counter_ns()
    scores = reranker.score_pairs(pairs)
    reranker_ms = (time.perf_counter_ns() - started) / 1_000_000
    reranked = [
        {
            "memory_id": candidate["memory_id"],
            "dense_score": candidate["score"],
            "reranker_score": round(float(score), 8),
            "status": candidate.get("status", "ACTIVE"),
        }
        for candidate, score in zip(candidates, scores)
    ]
    reranked.sort(key=lambda item: (-item["reranker_score"], item["memory_id"]))
    top1 = reranked[0]["reranker_score"]
    top2 = reranked[1]["reranker_score"] if len(reranked) > 1 else top1
    row.update(
        {
            "reranker_candidates": reranked,
            "reranker_candidate_count": len(reranked),
            "reranker_top1_id": reranked[0]["memory_id"],
            "reranker_top1_score": top1,
            "reranker_margin": round(top1 - top2, 8),
            "reranker_ms": round(reranker_ms, 6),
        }
    )


def _summary(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    accepted = [row for row in rows if row.get("decision") == "ACCEPT"]
    positives = [row for row in rows if row["expected_ids"]]
    true_accepts = sum(bool(row.get("accepted_expected")) for row in rows)
    false_accepts = sum(bool(row.get("false_accept")) for row in rows)
    by_category: dict[str, Any] = {}
    for category in sorted({row["category"] for row in rows}):
        category_rows = [row for row in rows if row["category"] == category]
        category_accepted = [row for row in category_rows if row.get("decision") == "ACCEPT"]
        category_true = sum(bool(row.get("accepted_expected")) for row in category_rows)
        category_positive = sum(bool(row["expected_ids"]) for row in category_rows)
        by_category[category] = {
            "query_count": len(category_rows),
            "positive_count": category_positive,
            "accept_count": len(category_accepted),
            "false_accept_count": sum(bool(row.get("false_accept")) for row in category_rows),
            "accept_precision": category_true / len(category_accepted) if category_accepted else 0.0,
            "accept_recall": category_true / category_positive if category_positive else 0.0,
        }
    return {
        "name": name,
        "query_count": len(rows),
        "positive_count": len(positives),
        "negative_count": len(rows) - len(positives),
        "accept_count": len(accepted),
        "ambiguous_count": sum(row.get("decision") == "AMBIGUOUS" for row in rows),
        "abstain_count": sum(row.get("decision") == "ABSTAIN" for row in rows),
        "accept_precision": true_accepts / len(accepted) if accepted else 0.0,
        "accept_recall": true_accepts / len(positives) if positives else 0.0,
        "false_accept_count": false_accepts,
        "false_accept_rate": false_accepts / len(rows) if rows else 0.0,
        "by_category": by_category,
    }


def _combined_summary(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    copied: list[dict[str, Any]] = []
    for row in rows:
        expected = set(row["expected_ids"])
        lexical_ids = set(row.get("fts_would_inject_ids", ()))
        combined_accept = bool(lexical_ids) or row.get("decision") == "ACCEPT"
        combined_true = bool(expected & lexical_ids) or bool(row.get("accepted_expected"))
        copied.append(
            {
                **row,
                "decision": "ACCEPT" if combined_accept else row.get("decision"),
                "accepted_expected": combined_true,
                "false_accept": combined_accept and not combined_true,
            }
        )
    return _summary(copied, name)


def _candidate_recall(rows: list[dict[str, Any]]) -> float:
    positives = [row for row in rows if row["expected_ids"]]
    recovered = sum(
        bool(set(row["expected_ids"]) & {candidate["memory_id"] for candidate in row.get("dense_eligible_candidates", [])})
        for row in positives
    )
    return recovered / len(positives) if positives else 0.0


def _latency_samples(
    rows: list[dict[str, Any]],
    *,
    reranker: OnnxCrossEncoderReranker,
    memory_by_id: dict[str, dict[str, Any]],
    warmup: int,
    iterations: int,
    top_k: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    measured_rows = [
        row for row in rows if row.get("reranker_evaluated") and row.get("reranker_candidates")
    ]

    def measure(row: dict[str, Any]) -> float:
        candidates = row["dense_eligible_candidates"][:top_k]
        pairs = [
            (row["query"], memory_by_id[candidate["memory_id"]]["content"])
            for candidate in candidates
        ]
        started = time.perf_counter_ns()
        reranker.score_pairs(pairs)
        return (time.perf_counter_ns() - started) / 1_000_000

    for _ in range(warmup):
        for row in measured_rows:
            measure(row)
    samples: list[dict[str, Any]] = []
    for iteration in range(iterations):
        for row in measured_rows:
            samples.append(
                {
                    "query_id": row["query_id"],
                    "iteration": iteration,
                    "reranker_ms": round(measure(row), 6),
                }
            )
    return samples, _distribution([row["reranker_ms"] for row in samples])


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
    holdout = summary["reranker_holdout"]
    primary = summary["primary_metric"]
    recall = f"{primary['holdout_accept_recall']:.1%}" if primary["eligible"] else "—"
    precision = f"{holdout['accept_precision']:.1%}" if holdout["accept_count"] else "—"
    lines = [
        "# ORCHID Phase 2.4 — Reranker-Only Shadow Evaluation",
        "",
        "## Scope",
        "",
        f"Dense candidates were fixed to MiniLM-L12 (`{L12_REVISION}`), top-{TOP_K}, after Phase 2.3 scope/status eligibility. `{RERANKER_MODEL_ID}` (`{RERANKER_REVISION}`) scored only queries without a confident FTS would-inject result. No reranker result entered ORCHID context.",
        "",
        "## Primary metric",
        "",
        f"Maximum reranker ACCEPT recall at >=99% precision on the untouched reranker-eligible holdout: **{recall}**.",
        "",
        "| Path | Precision | Recall | ACCEPT | False ACCEPTs |",
        "|---|---:|---:|---:|---:|",
        f"| Reranker-only eligible queries | {precision} | {recall} | {holdout['accept_count']} | {holdout['false_accept_count']} |",
        f"| Combined FTS bypass + reranker | {summary['combined_holdout']['accept_precision']:.1%} | {summary['combined_holdout']['accept_recall']:.1%} | {summary['combined_holdout']['accept_count']} | {summary['combined_holdout']['false_accept_count']} |",
        "",
        "## Measured facts",
        "",
        f"- Holdout: `{config['holdout_count']}` rows, `{config['reranker_holdout_evaluated_count']}` reranker-evaluated, `{config['lexical_bypass_holdout_count']}` lexical bypasses; calibration: `{config['calibration_count']}` rows, `{config['reranker_calibration_evaluated_count']}` evaluated.",
        f"- Dense candidate Recall@5 before reranking: `{summary['dense_candidate_recall_at_5']:.1%}` over holdout positives.",
        f"- Reranker latency over `{summary['latency']['count']}` samples: p50 `{summary['latency']['p50']:.3f}` ms, p95 `{summary['latency']['p95']:.3f}` ms, p99 `{summary['latency']['p99']:.3f}` ms.",
        f"- Hot-state fingerprint unchanged: `{config['hot_path_state_unchanged']}`; no events, ACTIVE mutations, or production injections.",
        "",
        "## Failure-type breakdown",
        "",
    ]
    for category, metrics in holdout["by_category"].items():
        lines.append(
            f"- `{category}`: {metrics['query_count']} evaluated, ACCEPT precision {metrics['accept_precision']:.1%}, recall {metrics['accept_recall']:.1%}, false ACCEPTs {metrics['false_accept_count']}."
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The reranker is evaluated as a relevance judge, not as a way to claim dense candidate recall. Lexical hits are bypassed and remain the authoritative Phase 1 path. Abstention is reported as zero recall contribution; it is not treated as success.",
            "",
            "## Recommendation",
            "",
            "",
        ]
    )
    if primary["eligible"]:
        lines.append(
            f"The reranker clears the point-estimate gate at {recall} recall. Keep it shadow-only and require a larger reproducibility run before any fusion or injection experiment."
        )
    else:
        lines.append(
            "The reranker does not clear the precision-constrained recall gate. Pause dense implicit retrieval; do not add RRF, graph expansion, or more threshold heuristics. Keep dense/reranker available only for explicit memory search or revisit representation/training data."
        )
    lines.extend(
        [
            "",
            "## Reproduction",
            "",
            "```text",
            f"python tests/cold_memory_phase2_4_reranker_shadow.py --phase23 {config['phase23_root']} --output {config['output_root']} --warmup {config['warmup']} --iterations {config['iterations']} --top-k {config['top_k']}",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase23", type=Path, default=DEFAULT_PHASE23)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--top-k", type=int, default=TOP_K)
    args = parser.parse_args()
    if args.top_k <= 0 or args.top_k > 10:
        parser.error("--top-k must be between 1 and 10")
    phase23 = args.phase23
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    base_corpus = _load_jsonl(phase23 / "QUERY_CORPUS.jsonl")
    base_quality = _load_jsonl(phase23 / "quality_results.jsonl")
    if {row["id"] for row in base_corpus} != {row["query_id"] for row in base_quality}:
        parser.error("Phase 2.3 corpus and quality rows do not have matching IDs")
    extra_cases = _extra_holdout_cases()
    query_rows = base_corpus + extra_cases
    expanded_corpus = output / "QUERY_CORPUS.jsonl"
    _write_jsonl(expanded_corpus, query_rows)
    db_path = phase23 / "work_status.db"
    memory_by_id = _memory_rows(db_path)
    model_path = Path(
        hf_hub_download(
            repo_id=L12_MODEL_ID,
            filename="onnx/model.onnx",
            revision=L12_REVISION,
        )
    )
    tokenizer_path = Path(
        hf_hub_download(
            repo_id=L12_MODEL_ID,
            filename="tokenizer.json",
            revision=L12_REVISION,
        )
    )
    dense_embedder = OnnxTextEmbedder(
        model_path=model_path,
        tokenizer_path=tokenizer_path,
        max_length=256,
    )
    active_index = DenseMemoryIndex.load(
        phase23 / "dense_embeddings_implicit_eligible.npz"
    )
    retriever = FTS5ColdMemoryRetriever(
        SQLiteStore(db_path),
        timeout_ms=50,
        candidate_limit=20,
        ranking_policy=CALIBRATED_RANKING_POLICY,
        telemetry_sink=_NoopTelemetry(),
    )
    state_before = _state(SQLiteStore(db_path))
    extra_rows = [
        _new_dense_row(
            case=case,
            retriever=retriever,
            active_index=active_index,
            embedder=dense_embedder,
            memory_by_id=memory_by_id,
        )
        for case in extra_cases
    ]
    all_rows = list(base_quality) + extra_rows
    model_file, tokenizer_file = model_path, tokenizer_path
    reranker_model_path = Path(
        hf_hub_download(
            repo_id=RERANKER_MODEL_ID,
            filename="onnx/model.onnx",
            revision=RERANKER_REVISION,
        )
    )
    reranker_tokenizer_path = Path(
        hf_hub_download(
            repo_id=RERANKER_MODEL_ID,
            filename="tokenizer.json",
            revision=RERANKER_REVISION,
        )
    )
    reranker = OnnxCrossEncoderReranker(
        model_path=reranker_model_path,
        tokenizer_path=reranker_tokenizer_path,
        max_length=256,
    )
    for row in all_rows:
        _score_row(
            row,
            reranker=reranker,
            memory_by_id=memory_by_id,
            top_k=args.top_k,
        )
    calibration_rows = [row for row in all_rows if row["split"] == "calibration"]
    holdout_rows = [row for row in all_rows if row["split"] == "holdout"]
    reranker_calibration = [row for row in calibration_rows if row["reranker_evaluated"]]
    reranker_holdout = [row for row in holdout_rows if row["reranker_evaluated"]]
    policy_result = calibrate_reranker_policy(
        reranker_calibration,
        target_precision=TARGET_PRECISION,
    )
    policy = RerankerPolicy(**policy_result["policy"])
    apply_reranker_policy(all_rows, policy)
    calibration_summary = _summary(reranker_calibration, "reranker_calibration")
    holdout_summary = _summary(reranker_holdout, "reranker_holdout")
    combined_holdout = _combined_summary(holdout_rows, "combined_holdout")
    latency_rows, latency = _latency_samples(
        reranker_holdout,
        reranker=reranker,
        memory_by_id=memory_by_id,
        warmup=args.warmup,
        iterations=args.iterations,
        top_k=args.top_k,
    )
    state_after = _state(SQLiteStore(db_path))
    state_unchanged = state_before == state_after
    summary = {
        "model": {
            "dense_model_id": L12_MODEL_ID,
            "dense_revision": L12_REVISION,
            "dense_model_sha256": sha256_file(model_file),
            "dense_tokenizer_sha256": sha256_file(tokenizer_file),
            "reranker_model_id": RERANKER_MODEL_ID,
            "reranker_revision": RERANKER_REVISION,
            "reranker_model_sha256": sha256_file(reranker_model_path),
            "reranker_tokenizer_sha256": sha256_file(reranker_tokenizer_path),
            "reranker_max_length": 256,
        },
        "policy": policy_result,
        "calibration": calibration_summary,
        "reranker_holdout": holdout_summary,
        "combined_holdout": combined_holdout,
        "dense_candidate_recall_at_5": _candidate_recall(holdout_rows),
        "latency": latency,
        "hot_path_impact": {
            "state_before": state_before,
            "state_after": state_after,
            "state_unchanged": state_unchanged,
            "production_injections": 0,
            "events_appended_by_reranker": 0,
            "active_mutations_by_reranker": 0,
        },
        "primary_metric": _primary_metric(holdout_summary),
    }
    config = {
        "phase": "2.4",
        "phase23_root": str(phase23),
        "output_root": str(output),
        "base_corpus_sha256": sha256_file(phase23 / "QUERY_CORPUS.jsonl"),
        "expanded_corpus_sha256": sha256_file(expanded_corpus),
        "query_count": len(query_rows),
        "calibration_count": sum(row["split"] == "calibration" for row in query_rows),
        "holdout_count": sum(row["split"] == "holdout" for row in query_rows),
        "external_guardrail_count": sum(row["split"] == "external_guardrail" for row in query_rows),
        "calibration_count_added": 20,
        "holdout_count_added": 50,
        "reranker_calibration_evaluated_count": len(reranker_calibration),
        "reranker_holdout_evaluated_count": len(reranker_holdout),
        "lexical_bypass_holdout_count": len(holdout_rows) - len(reranker_holdout),
        "warmup": args.warmup,
        "iterations": args.iterations,
        "top_k": args.top_k,
        "target_accept_precision": TARGET_PRECISION,
        "dense_fixed": True,
        "dense_status_eligibility_fixed": True,
        "reranker_shadow_only": True,
        "fts_bypass_rule": "fts_would_inject_ids non-empty",
        "hot_path_state_unchanged": state_unchanged,
        "production_injection": False,
    }
    summary["config"] = config
    (output / "CONFIG.json").write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "SUMMARY.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "POLICY.json").write_text(json.dumps(policy_result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_jsonl(output / "quality_results.jsonl", all_rows)
    _write_jsonl(output / "latency_samples.jsonl", latency_rows)
    (output / "REPORT.md").write_text(_report(config, summary), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
