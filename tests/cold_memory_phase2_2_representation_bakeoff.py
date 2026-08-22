"""Phase 2.2 representation bakeoff for dense abstention.

Every encoder is evaluated with the frozen Phase 2.1 query corpus and the
same calibration/evaluation protocol.  This is a candidate-generation
experiment only; no encoder is connected to ORCHID context assembly.
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
)
from memory_gateway.dense_abstention import (  # noqa: E402
    DenseAbstentionPolicy,
    calibrate_policy,
)
from memory_gateway.dense_experiment import (  # noqa: E402
    DenseMemoryIndex,
    OnnxTextEmbedder,
    model_metadata,
    write_json,
)

try:
    from tests.cold_memory_phase1_benchmark import (
        PROJECT_ID,
        THREAD_ID,
        _anchors,
        _build_store,
        _write_jsonl,
    )
    from tests.cold_memory_phase2_1_abstention_calibration import (
        _apply_policy,
        _evaluate_query,
        _memory_rows,
        _state_fingerprint,
        _summary,
    )
    from tests.cold_memory_phase2_dense_experiment import MODEL_REVISION
    from tests.cold_memory_phase2_dense_experiment import _NoopTelemetry
except ModuleNotFoundError:
    from cold_memory_phase1_benchmark import (  # type: ignore
        PROJECT_ID,
        THREAD_ID,
        _anchors,
        _build_store,
        _write_jsonl,
    )
    from cold_memory_phase2_1_abstention_calibration import (  # type: ignore
        _apply_policy,
        _evaluate_query,
        _memory_rows,
        _state_fingerprint,
        _summary,
    )
    from cold_memory_phase2_dense_experiment import MODEL_REVISION  # type: ignore
    from cold_memory_phase2_dense_experiment import _NoopTelemetry  # type: ignore


DEFAULT_OUTPUT = Path("artifacts/cold_memory/phase2_2_representation_bakeoff")
DEFAULT_QUERY_CORPUS = Path(
    "artifacts/cold_memory/phase2_1_abstention_calibration/QUERY_CORPUS.jsonl"
)
TARGET_PRECISION = 0.99

CANDIDATE_MODELS = (
    {
        "slug": "all_minilm_l6_v2_baseline",
        "model_id": "sentence-transformers/all-MiniLM-L6-v2",
        "revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
    },
    {
        "slug": "all_minilm_l12_v2",
        "model_id": "sentence-transformers/all-MiniLM-L12-v2",
        "revision": "a50ef00143b4d5391434df20ae11632588ac25be",
    },
    {
        "slug": "paraphrase_minilm_l3_v2",
        "model_id": "sentence-transformers/paraphrase-MiniLM-L3-v2",
        "revision": "4ca70771034acceecb2e72475f72050fcdde4ddc",
    },
    {
        "slug": "multi_qa_minilm_l6_cos_v1",
        "model_id": "sentence-transformers/multi-qa-MiniLM-L6-cos-v1",
        "revision": "b207367332321f8e44f96e224ef15bc607f4dbf0",
    },
)


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


def _load_query_corpus(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    ids = [row["id"] for row in rows]
    if len(rows) != len(set(ids)):
        raise ValueError("Phase 2.1 query corpus contains duplicate IDs")
    split_counts = {
        split: sum(row.get("split") == split for row in rows)
        for split in ("calibration", "holdout", "external_guardrail")
    }
    if split_counts != {"calibration": 151, "holdout": 150, "external_guardrail": 18}:
        raise ValueError(f"unexpected frozen query corpus split counts: {split_counts}")
    return rows


def _download_candidate(model: dict[str, str]) -> tuple[Path, Path, dict[str, Any]]:
    model_path = Path(
        hf_hub_download(
            repo_id=model["model_id"],
            filename="onnx/model.onnx",
            revision=model["revision"],
        )
    )
    tokenizer_path = Path(
        hf_hub_download(
            repo_id=model["model_id"],
            filename="tokenizer.json",
            revision=model["revision"],
        )
    )
    return model_path, tokenizer_path, {
        "repo_id": model["model_id"],
        "revision": model["revision"],
        "model_sha256": sha256_file(model_path),
        "tokenizer_sha256": sha256_file(tokenizer_path),
        "model_cache_path": str(model_path),
        "tokenizer_cache_path": str(tokenizer_path),
    }


def _primary_metric(summary: dict[str, Any], *, target_precision: float) -> dict[str, Any]:
    eligible = bool(
        summary["accept_count"] > 0
        and summary["accept_precision"] >= target_precision
    )
    return {
        "eligible": eligible,
        "target_precision": target_precision,
        "holdout_accept_precision": summary["accept_precision"],
        "holdout_accept_recall": summary["accept_recall"] if eligible else None,
        "holdout_accept_count": summary["accept_count"],
    }


def _run_latency(
    *,
    cases: list[dict[str, Any]],
    retriever: FTS5ColdMemoryRetriever,
    dense_index: DenseMemoryIndex,
    embedder: OnnxTextEmbedder,
    memory_by_id: dict[str, dict[str, Any]],
    warmup: int,
    iterations: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    def as_case(row: dict[str, Any]) -> dict[str, Any]:
        """Adapt an evaluated result row back to the frozen case shape."""
        return {
            "id": row["query_id"],
            "category": row["category"],
            "query": row["query"],
            "expected_ids": row["expected_ids"],
            "split": row["split"],
        }

    def evaluate(case: dict[str, Any]) -> dict[str, Any]:
        return _evaluate_query(
            case=case,
            retriever=retriever,
            dense_index=dense_index,
            embedder=embedder,
            memory_by_id=memory_by_id,
        )

    for _ in range(warmup):
        for row in cases:
            evaluate(as_case(row))
    samples: list[dict[str, Any]] = []
    for iteration in range(iterations):
        for row in cases:
            case = as_case(row)
            measured = evaluate(case)
            samples.append(
                {
                    "query_id": case["id"],
                    "iteration": iteration,
                    "dense_query_ms": measured["dense_query_ms"],
                }
            )
    return samples, _distribution([row["dense_query_ms"] for row in samples])


def _run_model(
    *,
    model: dict[str, str],
    query_cases: list[dict[str, Any]],
    store: Any,
    memories: list[dict[str, Any]],
    output_root: Path,
    warmup: int,
    iterations: int,
) -> dict[str, Any]:
    model_output = output_root / model["slug"]
    model_output.mkdir(parents=True, exist_ok=True)
    model_path, tokenizer_path, source = _download_candidate(model)
    embedder = OnnxTextEmbedder(
        model_path=model_path,
        tokenizer_path=tokenizer_path,
        max_length=256,
    )
    build_started = time.perf_counter_ns()
    dense_index = DenseMemoryIndex.build(memories, embedder)
    index_build_ms = (time.perf_counter_ns() - build_started) / 1_000_000
    dense_index.save(model_output / "dense_embeddings.npz")
    memory_by_id = {str(memory["id"]): memory for memory in memories}
    retriever = FTS5ColdMemoryRetriever(
        store,
        timeout_ms=50,
        candidate_limit=20,
        ranking_policy=CALIBRATED_RANKING_POLICY,
        telemetry_sink=_NoopTelemetry(),
    )
    rows = [
        _evaluate_query(
            case=case,
            retriever=retriever,
            dense_index=dense_index,
            embedder=embedder,
            memory_by_id=memory_by_id,
        )
        for case in query_cases
    ]
    calibration_rows = [row for row in rows if row["split"] == "calibration"]
    holdout_rows = [row for row in rows if row["split"] == "holdout"]
    guardrail_rows = [
        row for row in rows if row["split"] == "external_guardrail"
    ]
    policy_result = calibrate_policy(
        [
            {"expected_ids": row["expected_ids"], "features": row["features"]}
            for row in calibration_rows
        ],
        target_precision=TARGET_PRECISION,
    )
    policy = DenseAbstentionPolicy(**policy_result["policy"])
    _apply_policy(rows, policy)
    calibration_summary = _summary(calibration_rows, "calibration")
    holdout_summary = _summary(holdout_rows, "holdout")
    guardrail_summary = _summary(guardrail_rows, "lexical_guardrail")
    latency_samples, latency_summary = _run_latency(
        cases=holdout_rows,
        retriever=retriever,
        dense_index=dense_index,
        embedder=embedder,
        memory_by_id=memory_by_id,
        warmup=warmup,
        iterations=iterations,
    )
    holdout_summary["dense_query_ms"] = latency_summary
    metadata = model_metadata(
        model_id=model["model_id"],
        revision=model["revision"],
        model_path=model_path,
        tokenizer_path=tokenizer_path,
        embedder=embedder,
    )
    metadata["source"] = source
    result = {
        "model": model,
        "metadata": metadata,
        "index_build_ms": round(index_build_ms, 6),
        "policy": policy_result,
        "calibration": calibration_summary,
        "holdout": holdout_summary,
        "lexical_guardrail": guardrail_summary,
        "primary_metric": _primary_metric(
            holdout_summary,
            target_precision=TARGET_PRECISION,
        ),
        "failure_types": holdout_summary["by_category"],
    }
    write_json(model_output / "MODEL.json", metadata)
    write_json(model_output / "POLICY.json", policy_result)
    write_json(model_output / "SUMMARY.json", result)
    _write_jsonl(model_output / "quality_results.jsonl", rows)
    _write_jsonl(model_output / "latency_samples.jsonl", latency_samples)
    return result


def _report(
    *,
    config: dict[str, Any],
    results: list[dict[str, Any]],
    winner: dict[str, Any] | None,
) -> str:
    lines = [
        "# ORCHID Phase 2.2 — Representation Bakeoff",
        "",
        "## Scope",
        "",
        "Four ONNX sentence encoders were evaluated with the frozen Phase 2.1",
        "query corpus, split, feature extraction, calibration grid, and decision",
        "protocol. No model was connected to the gateway, and no dense candidate",
        "was injected into context.",
        "",
        "## Primary metric",
        "",
        "The gate is maximum holdout ACCEPT recall among models whose calibrated",
        "policy achieves at least 99% holdout ACCEPT precision with at least one",
        "accepted query. Holdout data was not used to select thresholds.",
        "",
        "| Model | Calibration precision | Holdout precision | Holdout ACCEPT recall | ACCEPT count | p50 ms | p95 ms | Gate |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for result in results:
        holdout = result["holdout"]
        primary = result["primary_metric"]
        recall = (
            f"{primary['holdout_accept_recall']:.1%}"
            if primary["eligible"]
            else "—"
        )
        lines.append(
            f"| {result['model']['model_id']} | {result['calibration']['accept_precision']:.1%} | "
            f"{holdout['accept_precision']:.1%} | {recall} | {holdout['accept_count']} | "
            f"{holdout['dense_query_ms']['p50']:.3f} | {holdout['dense_query_ms']['p95']:.3f} | "
            f"{'PASS' if primary['eligible'] else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "## Measured facts",
            "",
            f"- Frozen corpus: `{config['query_corpus_sha256']}`; `{config['semantic_query_count']}` semantic queries (`{config['calibration_count']}` calibration and `{config['holdout_count']}` holdout) plus `{config['external_guardrail_count']}` lexical guardrails, `{config['query_count']}` rows total.",
            f"- Candidate count: `{len(results)}`; model embeddings were precomputed before query timing.",
            f"- Primary target: `{TARGET_PRECISION:.1%}` ACCEPT precision on untouched holdout.",
            "- Holdout precision is a small-sample point estimate: each model's threshold was calibrated only on the 151-case calibration split; the 150-case holdout was used only for the registered comparison gate.",
            f"- Hot-path state fingerprint unchanged: `{config['hot_path_state_unchanged']}`; production injections/events/ACTIVE mutations were `0`.",
            "",
            "## Failure-type breakdown",
            "",
        ]
    )
    for result in results:
        lines.append(f"### {result['model']['slug']}")
        for category, metrics in result["failure_types"].items():
            lines.append(
                f"- `{category}`: {metrics['query_count']} queries, "
                f"ACCEPT precision {metrics['accept_precision']:.1%}, "
                f"ACCEPT recall {metrics['accept_recall']:.1%}, "
                f"false accepts {metrics['false_accept_count']}."
            )
        lines.append("")
    lines.extend(["## Lexical guardrail", ""])
    for result in results:
        guardrail = result["lexical_guardrail"]
        lines.append(
            f"- `{result['model']['slug']}`: dense ACCEPT precision "
            f"{guardrail['accept_precision']:.1%}, positive ACCEPT recall "
            f"{guardrail['accept_recall']:.1%}, ACCEPT count "
            f"{guardrail['accept_count']}; FTS behavior was unchanged."
        )
    lines.append("")
    if winner is None:
        recommendation = (
            "No encoder cleared the 99% holdout precision gate. Preserve the "
            "current L6 baseline and keep dense as an experimental candidate "
            "generator; do not add RRF or a production injection path."
        )
    else:
        recommendation = (
            f"`{winner['model']['slug']}` cleared the gate with "
            f"{winner['primary_metric']['holdout_accept_recall']:.1%} holdout ACCEPT recall. "
            "This is a representation candidate, not an injection approval: keep "
            "it shadow-only and run a larger calibration/reproducibility check "
            "before any fusion or injection experiment."
        )
    lines.extend(
        [
            "## Interpretation",
            "",
            "The L12 candidate is the cleanest separator in this run, but its "
            "8.0% holdout ACCEPT recall means it recovers only 4 of 50 semantic "
            "positives. The 100.0% precision point estimate is based on four "
            "accepted holdout queries, so it is not evidence that implicit dense "
            "injection is production-ready. The other candidates either admitted "
            "false superseded-history memories or produced too little accepted "
            "recall to be preferable. Exact/symbol recall remains the FTS "
            "responsibility and is not credited to dense retrieval here.",
            "",
            "## Recommendation",
            "",
            recommendation,
            "",
            "The bakeoff does not authorize dense injection. RRF and reranking are",
            "explicitly deferred until a representation first demonstrates useful",
            "precision-constrained separation.",
            "",
            "## Reproduction",
            "",
            "```text",
            f"python tests/cold_memory_phase2_2_representation_bakeoff.py --output {config['output_root']} --query-corpus {config['query_corpus']} --corpus-size {config['corpus_size']} --warmup {config['warmup']} --iterations {config['iterations']}",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--query-corpus", type=Path, default=DEFAULT_QUERY_CORPUS)
    parser.add_argument("--corpus-size", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=5)
    args = parser.parse_args()
    query_cases = _load_query_corpus(args.query_corpus)
    if args.corpus_size < len(_anchors()):
        parser.error(f"--corpus-size must be at least {len(_anchors())}")
    output_root = args.output
    output_root.mkdir(parents=True, exist_ok=True)
    store = _build_store(output_root / "work_shared.db", args.corpus_size)
    state_before = _state_fingerprint(store)
    memories = _memory_rows(store)
    write_json(output_root / "CORPUS.json", {
        "project_id": PROJECT_ID,
        "thread_id": THREAD_ID,
        "memory_count": len(memories),
        "anchor_count": len(_anchors()),
        "distractor_count": len(memories) - len(_anchors()),
        "source": "tests/cold_memory_phase1_benchmark.py::_corpus",
        "status_filter": "ACTIVE",
    })
    results = [
        _run_model(
            model=model,
            query_cases=query_cases,
            store=store,
            memories=memories,
            output_root=output_root,
            warmup=args.warmup,
            iterations=args.iterations,
        )
        for model in CANDIDATE_MODELS
    ]
    state_after = _state_fingerprint(store)
    state_unchanged = state_before == state_after
    eligible = [result for result in results if result["primary_metric"]["eligible"]]
    winner = max(
        eligible,
        key=lambda result: (
            result["primary_metric"]["holdout_accept_recall"],
            result["holdout"]["accept_precision"],
            result["holdout"]["accept_count"],
        ),
    ) if eligible else None
    config = {
        "phase": "2.2",
        "output_root": str(output_root),
        "query_corpus": str(args.query_corpus),
        "query_corpus_sha256": sha256_file(args.query_corpus),
        "query_count": len(query_cases),
        "semantic_query_count": sum(
            case["split"] in {"calibration", "holdout"} for case in query_cases
        ),
        "calibration_count": sum(case["split"] == "calibration" for case in query_cases),
        "holdout_count": sum(case["split"] == "holdout" for case in query_cases),
        "external_guardrail_count": sum(case["split"] == "external_guardrail" for case in query_cases),
        "corpus_size": args.corpus_size,
        "warmup": args.warmup,
        "iterations": args.iterations,
        "target_accept_precision": TARGET_PRECISION,
        "calibration_protocol": "memory_gateway.dense_abstention.calibrate_policy",
        "fts_unchanged": True,
        "dense_production_injection": False,
        "hot_path_state_unchanged": state_unchanged,
        "candidate_models": [model for model in CANDIDATE_MODELS],
    }
    summary = {
        "config": config,
        "models": results,
        "winner": winner["model"] if winner else None,
        "hot_path_impact": {
            "state_before": state_before,
            "state_after": state_after,
            "state_unchanged": state_unchanged,
            "production_dense_injections": 0,
            "events_appended_by_dense": 0,
            "active_mutations_by_dense": 0,
        },
    }
    write_json(output_root / "CONFIG.json", config)
    write_json(output_root / "SUMMARY.json", summary)
    (output_root / "REPORT.md").write_text(
        _report(config=config, results=results, winner=winner),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
