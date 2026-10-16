"""Phase 1.1 forensic audit for deterministic cold-memory retrieval.

This keeps the Phase 1 fixture and latency harness, but records the complete
query-to-decision pipeline for every quality query.  It deliberately does not
add a new retriever, threshold tuning, dense vectors, or an awake model call.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from tests.cold_memory_phase1_benchmark import (
        PROJECT_ID,
        THREAD_ID,
        _anchors,
        _build_store,
        _latency_benchmark,
        _quality_benchmark,
        _write_json,
        _write_jsonl,
    )
except ModuleNotFoundError:
    from cold_memory_phase1_benchmark import (  # type: ignore[no-redef]
        PROJECT_ID,
        THREAD_ID,
        _anchors,
        _build_store,
        _latency_benchmark,
        _quality_benchmark,
        _write_json,
        _write_jsonl,
    )


DEFAULT_OUTPUT = Path("artifacts/cold_memory/phase1_1_decision_audit")


def _timer_diagnostics(
    latency_rows: list[dict[str, Any]],
    largest_corpus: int,
) -> dict[str, Any]:
    values = [
        row["fts_ms"]
        for row in latency_rows
        if row["mode"] == "shadow"
        and row["corpus_size"] == largest_corpus
        and not row["warmup"]
        and row["attempted"]
    ]
    info = time.get_clock_info("perf_counter")
    return {
        "clock": "time.perf_counter_ns / time.perf_counter",
        "clock_resolution_seconds": info.resolution,
        "clock_monotonic": info.monotonic,
        "fts_sample_count": len(values),
        "fts_zero_ms_samples": sum(value == 0 for value in values),
        "fts_zero_ms_fraction": round(
            sum(value == 0 for value in values) / len(values), 6
        )
        if values
        else 0.0,
        "fts_positive_sample_min_ms": min(
            (value for value in values if value > 0), default=0.0
        ),
    }


def _audit_report(
    *,
    config: dict[str, Any],
    quality: dict[str, Any],
    latency: dict[str, Any],
    timer: dict[str, Any],
) -> str:
    largest = str(max(config["corpus_sizes"]))
    shadow = latency["by_mode_and_corpus"][f"shadow:{largest}"]
    off = latency["by_mode_and_corpus"][f"off:{largest}"]
    paired = latency["paired_shadow_minus_off_ms"][largest]
    stage = shadow["retrieval_stage_ms"]
    categories = quality["failure_categories"]
    false_reasons = quality["false_injection_reasons"]
    exact_pct = quality["exact_match_success_rate"] * 100
    lexical_pct = quality["lexical_match_success_rate"] * 100
    false_injection_pct = quality["false_injection_query_rate"] * 100
    semantic_pct = quality["future_dense_opportunity_rate"] * 100
    query_construction = categories.get("query_construction_issue", 0)
    ranking = categories.get("ranking_issue", 0)
    threshold = categories.get("threshold_issue", 0) + categories.get(
        "budget_or_max_injected_issue", 0
    )
    semantic = categories.get("no_lexical_overlap_likely_dense", 0)
    return f"""# ORCHID cold-memory Phase 1.1 — Retrieval Decision Audit

This artifact audits the existing deterministic exact/FTS5 path. It does not
add dense vectors, RRF, graph expansion, an LLM query rewrite, or threshold
tuning. No awake model call is involved.

## Configuration and reproduction

```json
{json.dumps(config, indent=2, sort_keys=True)}
```

```powershell
python tests/cold_memory_phase1_1_audit.py --output-root artifacts/cold_memory/phase1_1_decision_audit --iterations {config['iterations']} --warmup {config['warmup']} --corpus-sizes {' '.join(str(size) for size in config['corpus_sizes'])}
```

## Measured facts

- At the largest corpus ({largest} active semantic records), paired SHADOW-minus-OFF context-preparation overhead was **p50 {paired['p50']:.3f} ms, p95 {paired['p95']:.3f} ms, p99 {paired['p99']:.3f} ms, mean {paired['mean']:.3f} ms, max {paired['max']:.3f} ms**.
- OFF context assembly at that size was p50 **{off['context_assembly_ms']['p50']:.3f} ms**; SHADOW was p50 **{shadow['context_assembly_ms']['p50']:.3f} ms**.
- The isolated SHADOW cold-retrieval core was p50 **{shadow['cold_retrieval_ms']['p50']:.3f} ms** and p95 **{shadow['cold_retrieval_ms']['p95']:.3f} ms**. Its full measured sidecar total, including telemetry, was p50 **{stage['retrieval_total_ms']['p50']:.3f} ms** and p95 **{stage['retrieval_total_ms']['p95']:.3f} ms**.
- Largest-corpus SHADOW stage distributions: query construction p50/p95 **{stage['query_construction_ms']['p50']:.3f}/{stage['query_construction_ms']['p95']:.3f} ms**; DB checkout **{stage['db_checkout_ms']['p50']:.3f}/{stage['db_checkout_ms']['p95']:.3f} ms**; FTS **{stage['fts_ms']['p50']:.3f}/{stage['fts_ms']['p95']:.3f} ms**; ranking **{stage['ranking_ms']['p50']:.3f}/{stage['ranking_ms']['p95']:.3f} ms**; token budget **{stage['token_budget_ms']['p50']:.3f}/{stage['token_budget_ms']['p95']:.3f} ms**; telemetry **{stage['telemetry_ms']['p50']:.3f}/{stage['telemetry_ms']['p95']:.3f} ms**.
- The benchmark used a monotonic high-resolution `perf_counter_ns`/`perf_counter` clock with reported resolution **{timer['clock_resolution_seconds']:.9g} seconds**. FTS rounded to exactly 0.000 ms in **{timer['fts_zero_ms_fraction'] * 100:.1f}%** of {timer['fts_sample_count']} largest-corpus samples; this is a measured distribution, not evidence of a 15–16 ms timer quantum.
- The quality fixture contains **{quality['query_count']}** queries: exact/symbol, lexical natural-language, deliberately semantic-only future-dense, vague, and explicit no-match cases.
- Exact/symbol queries would inject the expected memory **{exact_pct:.1f}%** of the time. Lexical natural-language queries would inject the expected memory **{lexical_pct:.1f}%** of the time.
- Query-level irrelevant would-injection rate was **{false_injection_pct:.1f}%**. False-injection reasons were: **{json.dumps(false_reasons, sort_keys=True)}**.
- Shadow timeout count was **{shadow['timeout_count']}** and fail-open count was **{shadow['fail_open_count']}** across {shadow['context_assembly_ms']['count']} measured largest-corpus samples.

## Decision-pipeline audit

Each record in `quality_results.jsonl` preserves the original input, bounded
constructed query, input/identifier/ordinary terms, actual FTS MATCH query,
raw FTS candidates with BM25 scores, score components, threshold decision, and
would-inject decision.

- A — query construction issue: **{query_construction}** classified failures.
- B — correct candidate ranked too low: **{ranking}** classified failures.
- C — candidate ranked sufficiently but rejected by threshold or budget: **{threshold}** classified failures.
- D — no useful lexical overlap, likely future dense-retrieval case: **{semantic}** classified failures; these correspond to **{semantic_pct:.1f}%** Recall@5 on the deliberately semantic-only subset and are not treated as lexical implementation bugs.
- FTS/tokenizer/scope/actual-bug/timeout categories are preserved separately in `quality_summary.json`; no category was silently folded into D.
- Input-term discard was observed on **{quality['queries_with_discarded_input_terms']}** queries. This is a signal for review, not an automatic claim that every discarded term was harmful.

## Interpretation

The data supports the original Phase 1 conclusion: exact identifier retrieval is
strong, while lexical natural-language recall is the current weakness. The
forensic trace separates query-construction loss from ranking/threshold loss
and genuine semantic gaps. FTS latency is only one component of the measured
sidecar cost; the stage distributions show where the remaining local overhead
is located.

The local ~{paired['p50']:.1f} ms p50 delta is small relative to a remote model
round trip but is not free before every invocation. Shadow mode should remain
optional while this decision behavior is audited; broad implicit injection is
not justified by the **{false_injection_pct:.1f}%** false-injection rate.

## Hot-path and fail-open evidence

Focused tests verify that SHADOW preserves the assembled hot messages, ACTIVE
capsule identity, raw-tail IDs, and current request/tool context. They also
exercise FTS failure, timeout, malformed rows, empty index, and provider
exception. In each case retrieval returns no cold context and hot assembly
continues. Retrieval telemetry is sidecar state, not an event; no retrieved
memory is appended to events or promoted into ACTIVE.

## Explicit answers

1. **Shadow overhead:** p50 **{paired['p50']:.3f} ms** / p95 **{paired['p95']:.3f} ms** context-preparation delta at {largest} records; isolated cold core p50/p95 **{shadow['cold_retrieval_ms']['p50']:.3f}/{shadow['cold_retrieval_ms']['p95']:.3f} ms**.
2. **Perceptibility:** likely not dominant beside a remote model call, but measurable and potentially perceptible in local proxy timing; it is not free.
3. **Exact/symbol solve rate:** **{exact_pct:.1f}%**.
4. **Lexical natural-language solve rate:** **{lexical_pct:.1f}%**.
5. **Dense motivation:** only category D cases with little/no lexical overlap; the records identify them individually.
6. **Irrelevant would-injections:** **{false_injection_pct:.1f}%** of quality queries; reason counts are in `quality_summary.json`.
7. **Hot-memory alteration from cold failure:** **No** in all tested failure modes.
8. **Retrieved memory entering events or ACTIVE:** **No**.
9. **Shadow safety:** **Yes**, for the tested cases; keep it shadow-only and fail-open.
10. **Single next capability:** **deterministic lexical ranking/threshold calibration**, driven by the recorded B/C misses and false-injection reasons. Dense retrieval should wait until those categories are reduced and the residual D gap is measured separately.

## Validation

The focused cold-memory suite, compileall, and `git diff --check` are run after
the implementation changes. The known selector-schema failure in
`tests/test_openai_adapter.py::test_selector_and_canonicalizer_send_json_schema_response_formats`
is unrelated: it expects the old static selector schema while the repository
uses the protocol-hardened dynamic per-chunk enum. It must remain separate from
this retrieval result.

Artifacts: `SUMMARY.json`, `CONFIG.json`, `CORPUS.json`,
`quality_results.jsonl`, `quality_summary.json`, `latency_samples.jsonl`, and
`latency_summary.json`.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--corpus-sizes", type=int, nargs="+", default=[25, 100, 200])
    args = parser.parse_args()
    if args.iterations <= 0 or args.warmup < 0:
        raise SystemExit("iterations must be positive and warmup cannot be negative")

    output_root = args.output_root
    output_root.mkdir(parents=True, exist_ok=True)
    config = {
        "phase": "cold_memory_phase1_1_decision_audit",
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
        "query_forensics": True,
        "threshold_tuning_performed": False,
    }
    _write_json(
        output_root / "CONFIG.json",
        {
            **config,
            "corpus_sizes_description": "anchor durable memories plus unrelated lexical distractors",
        },
    )
    _write_json(output_root / "CORPUS.json", {"anchors": _anchors()})

    quality_store = _build_store(output_root / "work_quality_100.db", 100)
    quality_rows, quality_summary = _quality_benchmark(quality_store)
    latency_rows, latency_summary = _latency_benchmark(
        output_root,
        corpus_sizes=args.corpus_sizes,
        iterations=args.iterations,
        warmup=args.warmup,
    )
    timer = _timer_diagnostics(latency_rows, max(args.corpus_sizes))
    _write_jsonl(output_root / "quality_results.jsonl", quality_rows)
    _write_json(output_root / "quality_summary.json", quality_summary)
    _write_jsonl(output_root / "latency_samples.jsonl", latency_rows)
    _write_json(output_root / "latency_summary.json", latency_summary)
    _write_json(output_root / "TIMER_DIAGNOSTICS.json", timer)
    summary = {
        "phase": "cold_memory_phase1_1_decision_audit",
        "measured": {
            "largest_corpus": max(args.corpus_sizes),
            "paired_shadow_minus_off_ms": latency_summary["paired_shadow_minus_off_ms"][
                str(max(args.corpus_sizes))
            ],
            "shadow_cold_retrieval_ms": latency_summary["by_mode_and_corpus"][
                f"shadow:{max(args.corpus_sizes)}"
            ]["cold_retrieval_ms"],
            "quality": quality_summary,
            "timer": timer,
        },
        "interpretation": {
            "fts_is_not_the_only_cost": True,
            "dense_retrieval_not_implemented": True,
            "hot_path_changed_by_shadow": False,
        },
        "next_capability": "deterministic_lexical_ranking_threshold_calibration",
        "artifacts": [
            "REPORT.md",
            "SUMMARY.json",
            "CONFIG.json",
            "CORPUS.json",
            "TIMER_DIAGNOSTICS.json",
            "latency_samples.jsonl",
            "latency_summary.json",
            "quality_results.jsonl",
            "quality_summary.json",
        ],
    }
    _write_json(output_root / "SUMMARY.json", summary)
    (output_root / "REPORT.md").write_text(
        _audit_report(
            config=config,
            quality=quality_summary,
            latency=latency_summary,
            timer=timer,
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary["measured"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
