"""Phase 2.1 dense ACCEPT/AMBIGUOUS/ABSTAIN calibration.

The experiment calibrates a transparent dense decision gate on one half of a
larger deterministic labeled corpus and evaluates the untouched half.  It is
offline-only: FTS remains unchanged, dense results never reach ContextAssembler,
and no event, ACTIVE capsule, or retrieval telemetry row is written by the
query loop.
"""

from __future__ import annotations

import argparse
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
    evaluate_policy,
    feature_dict,
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
    from tests.cold_memory_phase2_dense_experiment import (
        MODEL_ID,
        MODEL_REVISION,
        _NoopTelemetry,
        _download_model,
        _hot_path_state,
        _memory_rows,
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
    from cold_memory_phase2_dense_experiment import (  # type: ignore
        MODEL_ID,
        MODEL_REVISION,
        _NoopTelemetry,
        _download_model,
        _hot_path_state,
        _memory_rows,
    )


DEFAULT_OUTPUT = Path("artifacts/cold_memory/phase2_1_abstention_calibration")
DEFAULT_MODEL_ROOT = Path("artifacts/cold_memory/phase2_0_dense_experiment/model")
TARGET_PRECISION = 0.99


_POSITIVE_TEMPLATES: dict[str, tuple[str, ...]] = {
    "mem_lease_renewal": (
        "Which old ownership bug let work continue after a worker lost control?",
        "How did abandoned workers incorrectly keep another job alive?",
        "What concurrency problem allowed a former worker to extend a lease?",
        "Why was an expired worker able to renew work belonging to someone else?",
        "What earlier handoff defect confused job ownership and lease extension?",
        "Which incident involved stale workers retaining authority over a job?",
        "How could a worker that no longer owned work prolong that work?",
        "What went wrong when ownership changed while a lease was renewed?",
        "Which race allowed old worker authority to survive beyond its lease?",
        "Why did the abandoned worker case threaten another worker's job?",
        "What historical bug connected expired ownership with extending a lease?",
        "How was stale ownership allowed to renew a job it had lost?",
        "Which worker handoff failure made lease renewal unsafe?",
        "What concurrency lesson came from an old worker renewing someone else's job?",
        "Why must lease extension check who still owns the work?",
        "Which earlier defect let an obsolete worker keep control of a task?",
        "How did the system mishandle lease renewal after a worker handoff?",
        "What old bug involved a worker renewing after its ownership expired?",
        "Which ownership safeguard was missing from the abandoned-worker path?",
        "Why was a stale worker able to prolong a task after control moved?",
    ),
    "mem_provenance": (
        "Why must a remembered conclusion keep the original proof behind it?",
        "How do durable memories preserve the records that support them?",
        "What prevents a summary from replacing the evidence it came from?",
        "Why should a distilled decision remain linked to its source history?",
        "How is historical proof kept available after memory compression?",
        "What design keeps source records attached to a semantic recollection?",
        "Why are original events still needed after a memory is summarized?",
        "How can we verify a remembered fact against the immutable past?",
        "What preserves the evidence trail behind a retired memory?",
        "Why is a semantic summary not authoritative on its own?",
        "How does the system retain proof for a compact historical memory?",
        "Which rule keeps source material available after distillation?",
        "Why must a memory point back to the events that established it?",
        "How are remembered decisions tied to their original observations?",
        "What makes the raw event history the final authority behind a memory?",
        "Why cannot compact semantic text stand in for its underlying records?",
        "How does provenance survive when a durable memory is created?",
        "Which safeguard lets an old conclusion be checked against source evidence?",
        "Why keep exact historical material beside a semantic memory?",
        "How are retired memories grounded in immutable event history?",
    ),
    "mem_cas_promotion": (
        "How was live state protected when two updates used different histories?",
        "What stops concurrent work from replacing the current snapshot incorrectly?",
        "Why can a stale update not overwrite the active state?",
        "How does the system guard live memory during competing promotions?",
        "What protects the current capsule when two workers finish out of order?",
        "Which mechanism rejects a promotion based on obsolete lineage?",
        "How is active state kept safe when compaction results race?",
        "Why does an older snapshot fail to replace a newer live one?",
        "What prevents concurrent promotion from losing the current capsule?",
        "How are stale writers stopped from changing active memory?",
        "Which safety check handles competing updates to live state?",
        "Why must a promotion compare its lineage before becoming current?",
        "How does the active capsule survive a stale compaction result?",
        "What design prevents an out-of-date worker from winning a promotion race?",
        "Why is compare-and-swap needed when state changes overlap?",
        "How does the system preserve the newest valid state across concurrent work?",
        "Which rule keeps an old lineage from replacing the active snapshot?",
        "What protects capsule state from a promotion that started too early?",
        "How are racing active-state writes made safe?",
        "Why cannot every completed compaction result become the live capsule?",
    ),
    "mem_model_boundary": (
        "How does the system choose useful old context without another model rewriting the request?",
        "What deterministic signals decide which history is worth searching?",
        "How can files and symbols guide a history lookup without an LLM?",
        "What lightweight path selects relevant prior context?",
        "Why is query selection based on existing request and tool signals?",
        "How does retrieval decide what earlier context matters before inference?",
        "Which non-model inputs are used to construct a historical lookup?",
        "How are the current request and coding entities turned into search terms?",
        "What keeps query selection deterministic and outside the model critical path?",
        "Why can retrieval use file paths and symbols without query rewriting?",
        "How does the gateway form a search request from context it already has?",
        "Which boundary prevents a query model from slowing down retrieval?",
        "How are tool observations used when deciding what old memory to search?",
        "What is the simple way to identify relevant history without generation?",
        "Why does the search query come from existing runtime signals?",
        "How can current coding terms select old context deterministically?",
        "Which design keeps an LLM out of query decomposition?",
        "How does the system choose memory lookup terms from the active task?",
        "What makes historical query construction predictable rather than learned?",
        "How is earlier context selected using the request, tools, and symbols?",
    ),
    "mem_raw_tail": (
        "Why are the newest exchanges kept word for word instead of summarized?",
        "How does context preserve the exact wording of fresh conversation?",
        "What keeps recent details from being paraphrased away?",
        "Why should the latest interaction remain available in its original form?",
        "How is fresh conversational evidence protected while memory is assembled?",
        "What prevents a summary from losing the newest user or tool detail?",
        "Why retain a verbatim tail beside the active capsule?",
        "How does the system keep current observations exact?",
        "What design protects recent wording from compaction?",
        "Why are newest events preserved independently of semantic memory?",
        "How can the latest exchange stay precise while older context is distilled?",
        "Which layer keeps current conversation details untouched?",
        "Why does the recent history need original text rather than a summary?",
        "How are fresh tool results kept exact during context construction?",
        "What protects the newest user instruction from lossy compression?",
        "Why is recent raw history assembled separately from ACTIVE memory?",
        "How does ORCHID avoid paraphrasing away current details?",
        "Which context component preserves the latest exchange verbatim?",
        "Why must fresh observations survive alongside a compact capsule?",
        "How is exact recent wording maintained when older memory sleeps?",
    ),
}


def _negative_cases() -> list[dict[str, Any]]:
    near_miss = [
        "How should an abandoned worker return a job to the queue?",
        "Which scheduler selects the next owner after a lease expires?",
        "What retry policy should a worker use after losing a task?",
        "How is a job canceled when its worker disappears?",
        "Which queue stores work waiting for a new owner?",
        "How do we rotate database connections during a deployment?",
        "Which API refreshes a token after it expires?",
        "How should a dashboard render a loading state?",
        "Which dependency supplies vector search for the project?",
        "How does an HTTP cache preserve ETags during compaction?",
    ]
    wrong_fact = [
        "What exact lease duration did the old worker receive?",
        "Which person approved the abandoned-worker fix?",
        "What queue name was used by the ownership handoff?",
        "Which database stored the original lease bug?",
        "What was the source event sequence for the handoff?",
        "Which author wrote the provenance rule?",
        "What timestamp marks the first source-evidence decision?",
        "Which archive format stores the original proof?",
        "What network protocol carries provenance links?",
        "Which team owns the historical evidence database?",
        "What exact revision number won the capsule promotion race?",
        "Which worker ID performed the successful CAS?",
        "What hardware protects the active snapshot?",
        "Which network lock prevents stale promotion?",
        "What UI button triggers active-state compare-and-swap?",
        "Which model rewrites every retrieval query?",
        "What temperature does deterministic query construction use?",
        "Which hosted API decomposes file and symbol searches?",
        "What prompt version decides which history matters?",
        "Which scheduler invokes the query model?",
        "What exact number of tokens does the raw tail retain?",
        "Which user preference determines the tail size?",
        "What color marks a verbatim recent event?",
        "Which UI panel shows raw-tail contents?",
        "What database column stores the newest wording?",
    ]
    superseded = [
        "What old queue-based lease mechanism replaced the ownership check?",
        "Which former worker protocol was superseded by the current handoff rule?",
        "What legacy scheduler is still used for abandoned jobs?",
        "How did the retired lease design renew work before the current safeguard?",
        "Which obsolete worker authority policy remains active today?",
        "What earlier source format replaced event provenance?",
        "Which old summary file is now the authority for historical evidence?",
        "How did the deprecated memory format retain proof?",
        "What superseded archive should be queried instead of source events?",
        "Which legacy provenance database is currently authoritative?",
        "What old promotion winner should bypass the current active capsule?",
        "Which deprecated write path still replaces live state?",
        "How does the superseded capsule protocol win a race today?",
        "What previous lineage rule overrides the current snapshot?",
        "Which obsolete CAS implementation should production use now?",
        "What retired query model still rewrites requests before search?",
        "Which deprecated LLM decides retrieval terms today?",
        "How does the old learned query planner select history now?",
        "What superseded search prompt controls current lookup?",
        "Which legacy query decomposition service is enabled?",
        "What old summary replaces the current raw conversation tail?",
        "Which deprecated tail format stores fresh details now?",
        "How does the superseded compaction rule paraphrase newest events?",
        "What legacy buffer is authoritative for current wording?",
        "Which old raw-tail policy should current context use?",
    ]
    no_memory = [
        "Which React component owns the dashboard state?",
        "What Postgres isolation level prevents duplicate writes?",
        "How is an API bearer token refreshed after expiry?",
        "Which graph edge links a file to its owning component?",
        "What embedding dimension should a future vector index use?",
        "How do we compact an HTTP cache without losing ETags?",
        "Which retry policy handles a DNS outage?",
        "How should a mobile app display an offline banner?",
        "What is the color palette for the deployment dashboard?",
        "Which Kubernetes service exposes the metrics endpoint?",
        "How do we rotate TLS certificates without downtime?",
        "Which package parses YAML configuration files?",
        "What is the maximum upload size for the web client?",
        "How should a browser cache a service worker?",
        "Which SQL index speeds up customer email lookup?",
        "What queue system handles image processing?",
        "How do we back up object storage across regions?",
        "Which UI displays the current CPU graph?",
        "What retry delay should a DNS client use?",
        "How does a React hook subscribe to websocket updates?",
        "Which Postgres extension provides full text search?",
        "What CDN serves static JavaScript bundles?",
        "How should a CLI format a missing file error?",
        "Which cloud region hosts the staging bucket?",
        "What logging format does the billing service emit?",
        "How do we rotate database connection pools during deploys?",
        "Which component owns the dashboard filter state?",
        "What bearer token endpoint handles refresh?",
        "Which graph database stores dependency edges?",
        "What cache eviction policy handles large images?",
        "How should a DNS outage trigger retries?",
        "Which font is used by the settings page?",
        "What metric records browser first paint?",
        "How do we configure an SMTP relay?",
        "Which endpoint returns the health check?",
        "What is the staging database hostname?",
        "How does the mobile client synchronize contacts?",
        "Which worker renders thumbnail previews?",
        "What compression format is used for backups?",
        "How should an API report a malformed JSON body?",
        "Which library validates JWT signatures?",
        "What timeout does the image proxy use?",
        "How do we migrate a Redis cache between clusters?",
        "Which CSS class controls the modal width?",
        "What database stores user notification preferences?",
        "How should a webhook retry after a 500 response?",
        "Which graph traversal finds package dependencies?",
        "What is the production hostname for the frontend?",
        "How do we rotate a cloud access key?",
        "Which alert fires when disk usage is high?",
        "What serializer handles binary upload metadata?",
    ]
    near_miss.extend(
        [
            f"{prefix} Which metric should monitor {topic} in production?"
            for prefix in (
                "During an incident review,",
                "For operational monitoring,",
                "In a deployment checklist,",
                "For a health dashboard,",
                "When measuring regressions,",
                "For a reliability report,",
                "In a service-level review,",
                "While planning observability,",
            )
            for topic in (
                "worker ownership handoffs",
                "source evidence retention",
                "live capsule promotion",
                "deterministic history lookup",
                "verbatim recent conversation",
            )
        ]
    )
    wrong_fact.extend(
        [
            f"{prefix} which ticket number records the decision about {topic}?"
            for prefix in (
                "Can you tell me",
                "The audit asks",
                "The release notes ask",
                "A project report asks",
                "The incident log asks",
            )
            for topic in (
                "worker ownership handoffs",
                "source evidence retention",
                "live capsule promotion",
                "deterministic history lookup",
                "verbatim recent conversation",
            )
        ]
    )
    superseded.extend(
        [
            f"{prefix} which deprecated service still owns {topic} today?"
            for prefix in (
                "Historically, which record says",
                "For a migration audit, which note says",
                "In an old deployment plan, which service says",
                "When reviewing legacy systems, which service says",
                "For a backward-compatibility check, which service says",
            )
            for topic in (
                "worker ownership handoffs",
                "source evidence retention",
                "live capsule promotion",
                "deterministic history lookup",
                "verbatim recent conversation",
            )
        ]
    )
    groups = (
        ("near_miss_negative", near_miss),
        ("wrong_fact_negative", wrong_fact),
        ("superseded_negative", superseded),
        ("no_cold_memory_negative", no_memory),
    )
    output: list[dict[str, Any]] = []
    for category, queries in groups:
        for index, query in enumerate(queries):
            output.append(
                {
                    "id": f"{category}_{index:03d}",
                    "category": category,
                    "query": query,
                    "expected_ids": [],
                    "split": "calibration" if index % 2 == 0 else "holdout",
                }
            )
    return output


def _query_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for target_id, queries in _POSITIVE_TEMPLATES.items():
        for index, query in enumerate(queries):
            cases.append(
                {
                    "id": f"positive_{target_id}_{index:03d}",
                    "category": "clear_semantic_positive",
                    "query": query,
                    "expected_ids": [target_id],
                    "split": "calibration" if index % 2 == 0 else "holdout",
                }
            )
    cases.extend(_negative_cases())
    return cases


def _state_fingerprint(store: Any) -> dict[str, Any]:
    return _hot_path_state(store)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * percentile)))
    return ordered[index]


def _distribution(values: list[float]) -> dict[str, float | int]:
    return {
        "count": len(values),
        "p50": round(_percentile(values, 0.50), 6),
        "p95": round(_percentile(values, 0.95), 6),
        "p99": round(_percentile(values, 0.99), 6),
        "mean": round(statistics.fmean(values), 6) if values else 0.0,
        "max": round(max(values), 6) if values else 0.0,
    }


def _dense_features(
    *,
    original_query: str,
    trace: Any,
    dense_index: DenseMemoryIndex,
    embedder: OnnxTextEmbedder,
    memory_by_id: dict[str, dict[str, Any]],
) -> tuple[DenseAbstentionFeatures, list[dict[str, Any]], float]:
    started = time.perf_counter_ns()
    query_embedding = embedder.embed([trace.constructed_query])[0]
    candidates = dense_index.search(query_embedding, top_k=5)
    query_ms = (time.perf_counter_ns() - started) / 1_000_000
    scores = (dense_index.embeddings @ query_embedding).astype(float)
    top1 = float(candidates[0].score) if candidates else 0.0
    top2 = float(candidates[1].score) if len(candidates) > 1 else 0.0
    other_scores = np.delete(scores, int(np.argmax(scores))) if len(scores) else np.asarray([])
    background_mean = float(other_scores.mean()) if len(other_scores) else 0.0
    background_std = float(other_scores.std()) if len(other_scores) else 1.0
    corpus_percentile = (
        sum(float(score) <= top1 for score in other_scores) / len(other_scores)
        if len(other_scores)
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
        and top1_row.get("project_id", PROJECT_ID) == PROJECT_ID
        and top1_row.get("thread_id", THREAD_ID) == THREAD_ID
        else 0.0
    )
    activation_prior = (
        min(
            1.0,
            max(float(top1_row.get("activation_score", 0.0)), float(top1_row.get("importance", 0.0))),
        )
        if top1_row
        else 0.0
    )
    features = DenseAbstentionFeatures(
        top1_score=top1,
        top2_score=top2,
        margin=top1 - top2,
        corpus_percentile=corpus_percentile,
        corpus_zscore=(top1 - background_mean) / max(background_std, 1e-9),
        lexical_overlap=lexical_overlap,
        lexical_overlap_ratio=lexical_ratio,
        identifier_agreement=identifier_agreement,
        scope_agreement=scope_agreement,
        activation_prior=activation_prior,
        candidate_count=len(candidates),
    )
    candidate_rows = [
        {"memory_id": candidate.memory_id, "score": round(float(candidate.score), 8)}
        for candidate in candidates
    ]
    return features, candidate_rows, query_ms


def _evaluate_query(
    *,
    case: dict[str, Any],
    retriever: FTS5ColdMemoryRetriever,
    dense_index: DenseMemoryIndex,
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
    features, dense_candidates, query_ms = _dense_features(
        original_query=query,
        trace=trace,
        dense_index=dense_index,
        embedder=embedder,
        memory_by_id=memory_by_id,
    )
    return {
        "query_id": case["id"],
        "category": case["category"],
        "split": case["split"],
        "query": query,
        "constructed_query": trace.constructed_query,
        "expected_ids": list(case["expected_ids"]),
        "features": feature_dict(features),
        "dense_candidates": dense_candidates,
        "dense_top1_id": dense_candidates[0]["memory_id"] if dense_candidates else None,
        "dense_top1_score": dense_candidates[0]["score"] if dense_candidates else None,
        "dense_query_ms": round(query_ms, 6),
        "fts_candidate_ids": [hit.memory_id for hit in fts_result.candidates],
        "fts_would_inject_ids": [hit.memory_id for hit in fts_result.would_inject],
        "fts_would_inject_expected": bool(
            set(case["expected_ids"]) & {hit.memory_id for hit in fts_result.would_inject}
        ),
        "fts_timed_out": fts_result.timed_out,
        "fts_fail_open": fts_result.fail_open,
    }


def _apply_policy(rows: list[dict[str, Any]], policy: DenseAbstentionPolicy) -> None:
    for row in rows:
        row["decision"] = policy.decide(DenseAbstentionFeatures(**row["features"]))
        row["accepted_expected"] = bool(row["decision"] == "ACCEPT" and row["expected_ids"])
        row["false_accept"] = bool(row["decision"] == "ACCEPT" and not row["expected_ids"])


def _summary(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    accepted = [row for row in rows if row["decision"] == "ACCEPT"]
    positives = [row for row in rows if row["expected_ids"]]
    true_accepts = sum(row["accepted_expected"] for row in rows)
    false_accepts = sum(row["false_accept"] for row in rows)
    non_accepts = [row for row in rows if row["decision"] != "ACCEPT"]
    by_category: dict[str, Any] = {}
    for category in sorted({row["category"] for row in rows}):
        category_rows = [row for row in rows if row["category"] == category]
        category_positives = [row for row in category_rows if row["expected_ids"]]
        category_accepts = [row for row in category_rows if row["decision"] == "ACCEPT"]
        category_true_accepts = sum(row["accepted_expected"] for row in category_rows)
        by_category[category] = {
            "query_count": len(category_rows),
            "positive_count": len(category_positives),
            "accept_count": len(category_accepts),
            "false_accept_count": sum(row["false_accept"] for row in category_rows),
            "accept_precision": (
                category_true_accepts / len(category_accepts)
                if category_accepts
                else 0.0
            ),
            "accept_recall": (
                category_true_accepts / len(category_positives)
                if category_positives
                else 0.0
            ),
        }
    return {
        "name": name,
        "query_count": len(rows),
        "positive_count": len(positives),
        "negative_count": len(rows) - len(positives),
        "accept_count": len(accepted),
        "ambiguous_count": sum(row["decision"] == "AMBIGUOUS" for row in rows),
        "abstain_count": sum(row["decision"] == "ABSTAIN" for row in rows),
        "accept_precision": true_accepts / len(accepted) if accepted else 0.0,
        "abstention_precision": true_accepts / len(accepted) if accepted else 0.0,
        "accept_recall": true_accepts / len(positives) if positives else 0.0,
        "false_accept_count": false_accepts,
        "false_accept_rate": false_accepts / len(rows) if rows else 0.0,
        "positive_accept_rate": true_accepts / len(positives) if positives else 0.0,
        "positive_ambiguous_rate": sum(
            row["decision"] == "AMBIGUOUS" for row in positives
        ) / len(positives) if positives else 0.0,
        "positive_abstain_rate": sum(
            row["decision"] == "ABSTAIN" for row in positives
        ) / len(positives) if positives else 0.0,
        "negative_non_accept_rate": sum(
            row["decision"] != "ACCEPT" for row in rows if not row["expected_ids"]
        ) / (len(rows) - len(positives)) if len(rows) != len(positives) else 0.0,
        "non_accept_negative_share": sum(
            not row["expected_ids"] for row in non_accepts
        ) / len(non_accepts) if non_accepts else 0.0,
        "failure_categories": {
            category: sum(
                row["decision"] != "ACCEPT" and row["category"] == category
                for row in rows
            )
            for category in sorted({row["category"] for row in rows})
        },
        "by_category": by_category,
        "dense_query_ms": _distribution([row["dense_query_ms"] for row in rows]),
    }


def _resolve_model(output_root: Path, model_root: Path, revision: str) -> tuple[Path, Path, dict[str, Any]]:
    model_path = model_root / "model.onnx"
    tokenizer_path = model_root / "tokenizer.json"
    if model_path.exists() and tokenizer_path.exists():
        return model_path, tokenizer_path, {"source": "phase2_0_model_root", "model_root": str(model_root)}
    model_path, tokenizer_path, source = _download_model(output_root, revision)
    source["source"] = "downloaded_for_phase2_1"
    return model_path, tokenizer_path, source


def _report(
    *,
    config: dict[str, Any],
    policy: dict[str, Any],
    summaries: dict[str, Any],
    holdout_rows: list[dict[str, Any]],
    state_unchanged: bool,
) -> str:
    holdout = summaries["holdout"]
    calibration = summaries["calibration"]
    negatives = [row for row in holdout_rows if not row["expected_ids"]]
    positive = [row for row in holdout_rows if row["expected_ids"]]
    return f"""# ORCHID Phase 2.1 — Dense Abstention Calibration

## Scope

This is an offline calibration of three dense decisions: `ACCEPT`,
`AMBIGUOUS`, and `ABSTAIN`. The calibrated policy uses only deterministic
features: top-1 score, top-1/top-2 margin, score distribution statistics,
lexical overlap, identifier agreement, scope agreement, and activation prior.
It is not imported by the gateway and does not change FTS, ContextAssembler,
RRF, reranking, or injection policy.

## Measured facts

- Labeled semantic corpus: `{config['semantic_query_count']}` queries; calibration `{config['calibration_query_count']}`, untouched holdout `{config['holdout_query_count']}`.
- Holdout positive queries: `{len(positive)}`; holdout negatives: `{len(negatives)}`.
- Calibrated policy: `{policy['policy']}`; target precision `{policy['target_precision']:.1%}`; target met on calibration = `{policy['target_met_on_calibration']}`.
- Calibration accept precision/recall: `{calibration['accept_precision']:.1%}` / `{calibration['accept_recall']:.1%}`.
- Holdout accept precision (abstention precision): `{holdout['accept_precision']:.1%}`; accepted `{holdout['accept_count']}` of `{holdout['query_count']}`; holdout recall `{holdout['accept_recall']:.1%}`.
- Holdout decision counts: ACCEPT `{holdout['accept_count']}`, AMBIGUOUS `{holdout['ambiguous_count']}`, ABSTAIN `{holdout['abstain_count']}`.
- Holdout positive outcomes: accept `{holdout['positive_accept_rate']:.1%}`, ambiguous `{holdout['positive_ambiguous_rate']:.1%}`, abstain `{holdout['positive_abstain_rate']:.1%}`.
- Holdout dense query latency: p50 `{holdout['dense_query_ms']['p50']:.3f}` ms, p95 `{holdout['dense_query_ms']['p95']:.3f}` ms, p99 `{holdout['dense_query_ms']['p99']:.3f}` ms, mean `{holdout['dense_query_ms']['mean']:.3f}` ms, max `{holdout['dense_query_ms']['max']:.3f}` ms over `{holdout['dense_query_ms']['count']}` samples.
- Holdout negative non-accept rate: `{holdout['negative_non_accept_rate']:.1%}`; false accepts: `{holdout['false_accept_count']}`.
- Offline hot-path state unchanged: `{state_unchanged}`.

## Interpretation

`ACCEPT` is the only state eligible for a future injection experiment. The
current run injected nothing. `AMBIGUOUS` means dense produced a plausible
candidate but the calibrated evidence was insufficient; `ABSTAIN` means no
candidate cleared even the weak plausibility floor.

The holdout is the decision gate. Calibration metrics are not evidence of
generalization. The corpus includes clear positives, near-miss negatives,
same-topic wrong-fact negatives, superseded-history negatives, and unrelated
no-memory queries. Exact/symbol guardrails remain separate lexical behavior.

## Recommendation

Do not enable dense `ACCEPT` in production yet unless holdout precision clears
the registered target. The next useful experiment is a larger labeled corpus
or a separately validated confidence model only if this deterministic gate
fails to achieve the required precision. Do not add RRF while dense still
cannot reliably abstain.

## Artifacts and reproduction

- `SUMMARY.json` — metrics and policy.
- `POLICY.json` — selected thresholds and calibration search evidence.
- `QUERY_CORPUS.jsonl` — frozen labeled query definitions and split.
- `quality_results.jsonl` — per-query features, candidates, FTS comparison, and decision.
- `latency_samples.jsonl` — holdout dense query timings.
- `CONFIG.json`, `MODEL.json`, `CORPUS.json` — reproducibility metadata.

```text
python tests/cold_memory_phase2_1_abstention_calibration.py --output {config['output_root']} --corpus-size {config['corpus_size']} --model-root {config['model_root']} --warmup {config['warmup']} --iterations {config['iterations']}
```

## Preserved non-goals

No dense result entered events or ACTIVE, no production retrieval mode was
changed, and no vectors were fused with FTS.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model-root", type=Path, default=DEFAULT_MODEL_ROOT)
    parser.add_argument("--corpus-size", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--revision", default=MODEL_REVISION)
    args = parser.parse_args()
    if args.corpus_size < len(_anchors()):
        parser.error(f"--corpus-size must be at least {len(_anchors())}")

    output_root = args.output
    output_root.mkdir(parents=True, exist_ok=True)
    model_path, tokenizer_path, model_source = _resolve_model(
        output_root, args.model_root, args.revision
    )
    embedder = OnnxTextEmbedder(
        model_path=model_path,
        tokenizer_path=tokenizer_path,
        max_length=256,
    )
    store = _build_store(output_root / "work_quality.db", args.corpus_size)
    state_before = _state_fingerprint(store)
    memories = _memory_rows(store)
    memory_by_id = {str(memory["id"]): memory for memory in memories}
    dense_index = DenseMemoryIndex.build(memories, embedder)
    dense_index.save(output_root / "dense_embeddings.npz")
    write_json(output_root / "memory_records.json", memories)
    write_json(output_root / "CORPUS.json", {
        "project_id": PROJECT_ID,
        "thread_id": THREAD_ID,
        "memory_count": len(memories),
        "anchor_count": len(_anchors()),
        "distractor_count": len(memories) - len(_anchors()),
        "source": "tests/cold_memory_phase1_benchmark.py::_corpus",
        "status_filter": "ACTIVE",
    })
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
    cases = _query_cases()
    rows = [
        _evaluate_query(
            case=case,
            retriever=retriever,
            dense_index=dense_index,
            embedder=embedder,
            memory_by_id=memory_by_id,
        )
        for case in cases
    ]
    calibration_rows = [row for row in rows if row["split"] == "calibration"]
    holdout_rows = [row for row in rows if row["split"] == "holdout"]
    policy_result = calibrate_policy(
        [{"expected_ids": row["expected_ids"], "features": row["features"]} for row in calibration_rows],
        target_precision=TARGET_PRECISION,
    )
    policy_values = DenseAbstentionPolicy(**policy_result["policy"])
    _apply_policy(rows, policy_values)
    calibration_summary = _summary(calibration_rows, "calibration")
    holdout_summary = _summary(holdout_rows, "holdout")
    external_guardrail = [
        dict(case, category="lexical_guardrail", split="external_guardrail")
        for case in _acceptance_holdout_queries()
    ]
    guardrail_rows = [
        _evaluate_query(
            case=case,
            retriever=retriever,
            dense_index=dense_index,
            embedder=embedder,
            memory_by_id=memory_by_id,
        )
        for case in external_guardrail
    ]
    _apply_policy(guardrail_rows, policy_values)
    state_after = _state_fingerprint(store)
    state_unchanged = state_before == state_after
    all_quality_rows = rows + guardrail_rows
    latency_cases = holdout_rows
    for _ in range(args.warmup):
        for row in latency_cases:
            _evaluate_query(
                case={
                    "id": row["query_id"],
                    "category": row["category"],
                    "query": row["query"],
                    "expected_ids": row["expected_ids"],
                    "split": row["split"],
                },
                retriever=retriever,
                dense_index=dense_index,
                embedder=embedder,
                memory_by_id=memory_by_id,
            )
    latency_samples: list[dict[str, Any]] = []
    for iteration in range(args.iterations):
        for row in latency_cases:
            measured = _evaluate_query(
                case={
                    "id": row["query_id"],
                    "category": row["category"],
                    "query": row["query"],
                    "expected_ids": row["expected_ids"],
                    "split": row["split"],
                },
                retriever=retriever,
                dense_index=dense_index,
                embedder=embedder,
                memory_by_id=memory_by_id,
            )
            latency_samples.append({
                "query_id": row["query_id"],
                "iteration": iteration,
                "dense_query_ms": measured["dense_query_ms"],
                "decision": row["decision"],
            })
    holdout_summary["dense_query_ms"] = _distribution(
        [row["dense_query_ms"] for row in latency_samples]
    )

    config = {
        "phase": "2.1",
        "output_root": str(output_root),
        "corpus_size": args.corpus_size,
        "warmup": args.warmup,
        "iterations": args.iterations,
        "model_id": MODEL_ID,
        "model_revision": args.revision,
        "model_root": str(args.model_root),
        "fts_policy": CALIBRATED_RANKING_POLICY.name,
        "fts_unchanged": True,
        "target_accept_precision": TARGET_PRECISION,
        "semantic_query_count": len(cases),
        "calibration_query_count": len(calibration_rows),
        "holdout_query_count": len(holdout_rows),
        "external_guardrail_query_count": len(guardrail_rows),
        "dense_production_injection": False,
        "features": [
            "top1_score",
            "top2_score",
            "margin",
            "corpus_percentile",
            "corpus_zscore",
            "lexical_overlap",
            "identifier_agreement",
            "scope_agreement",
            "activation_prior",
        ],
    }
    summaries = {
        "calibration": calibration_summary,
        "holdout": holdout_summary,
        "lexical_guardrail": _summary(guardrail_rows, "lexical_guardrail"),
    }
    summary = {
        "config": config,
        "model": metadata,
        "policy": policy_result,
        "summaries": summaries,
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
    write_json(output_root / "POLICY.json", policy_result)
    write_json(output_root / "SUMMARY.json", summary)
    _write_jsonl(output_root / "QUERY_CORPUS.jsonl", cases + external_guardrail)
    _write_jsonl(output_root / "quality_results.jsonl", all_quality_rows)
    _write_jsonl(output_root / "latency_samples.jsonl", latency_samples)
    (output_root / "REPORT.md").write_text(
        _report(
            config=config,
            policy=policy_result,
            summaries=summaries,
            holdout_rows=holdout_rows,
            state_unchanged=state_unchanged,
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
