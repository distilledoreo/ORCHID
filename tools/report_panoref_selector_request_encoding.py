"""Build the read-only report for the frozen request-encoding benchmark."""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.benchmark_panoref_selector_prefix_reuse import build_chunk_lookup, load_profile_inputs

OUT = ROOT / "artifacts/agent_benchmarks/panoref_selector_request_encoding_v1"
PLAN_PATH = OUT / "REQUEST_ENCODING_PLAN.json"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def rows(run_id: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (OUT / "runs" / run_id / "telemetry.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def selected(row: dict[str, Any]) -> set[str]:
    return set(row.get("selected_event_ids_original") or [])


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * p
    low = int(index)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (index - low)


def metrics(run_id: str) -> dict[str, Any]:
    data = rows(run_id)
    succeeded = [row for row in data if row["status"] == "SUCCEEDED"]
    walls = [float(row["metrics"]["total_wall_ms"]) for row in succeeded]
    ttft = [float(row["metrics"]["ttft_ms"]) for row in succeeded if row["metrics"]["ttft_ms"] is not None]
    decode = [float(row["metrics"]["decode_ms_client_observed"]) for row in succeeded if row["metrics"]["decode_ms_client_observed"] is not None]
    return {
        "run_id": run_id,
        "variant": data[0]["variant"] if data else None,
        "calls": len(data),
        "succeeded": len(succeeded),
        "errors": len(data) - len(succeeded),
        "timeouts": sum(row["error_category"] == "timeout" for row in data),
        "selector_wall_seconds": sum(walls) / 1000,
        "ttft_p50_ms": percentile(ttft, 0.5),
        "ttft_p95_ms": percentile(ttft, 0.95),
        "wall_p50_ms": percentile(walls, 0.5),
        "wall_p95_ms": percentile(walls, 0.95),
        "decode_p50_ms": percentile(decode, 0.5),
        "decode_p95_ms": percentile(decode, 0.95),
        "input_tokens": sum(float(row["metrics"]["input_tokens"] or 0) for row in succeeded),
        "output_tokens": sum(float(row["metrics"]["output_tokens"] or 0) for row in succeeded),
    }


def comparison(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> dict[str, Any]:
    by_left = {row["chunk_index"]: row for row in left}
    by_right = {row["chunk_index"]: row for row in right}
    common = sorted(set(by_left) & set(by_right))
    matches = [index for index in common if selected(by_left[index]) == selected(by_right[index])]
    return {
        "common_calls": len(common),
        "decision_matches": len(matches),
        "decision_match_rate": len(matches) / len(common) if common else None,
        "mismatches": [
            {
                "chunk_index": index,
                "left": sorted(selected(by_left[index])),
                "right": sorted(selected(by_right[index])),
            }
            for index in common
            if index not in matches
        ],
        "wall_delta_right_minus_left_ms": [
            float(by_right[index]["metrics"]["total_wall_ms"])
            - float(by_left[index]["metrics"]["total_wall_ms"])
            for index in common
        ],
    }


def main() -> None:
    plan = read_json(PLAN_PATH)
    profile_config, _sample, items = load_profile_inputs()
    lookup = build_chunk_lookup(profile_config, items)
    historical_summary = read_json(Path(plan["historical_selector_summary"]))
    historical_ids = set(historical_summary["selector"]["selected_ids"])

    run_ids = {
        "keep_opaque_gate": "keep_opaque_gate_24",
        "keep_short_gate": "keep_short_ids_gate_24",
        "keep_opaque_full": "keep_opaque_full_148",
        "keep_short_full": "keep_short_ids_full_148",
        "drop_opaque_gate": "drop_opaque_gate_24",
    }
    summaries = {key: metrics(run_id) for key, run_id in run_ids.items()}
    all_rows = {key: rows(run_id) for key, run_id in run_ids.items()}

    historical_comparisons: dict[str, Any] = {}
    for key, data in all_rows.items():
        matches = []
        mismatches = []
        for row in data:
            expected = {event_id for event_id in lookup[row["chunk_index"]]["event_ids"] if event_id in historical_ids}
            actual = selected(row)
            matches.append(actual == expected)
            if actual != expected:
                mismatches.append({"chunk_index": row["chunk_index"], "actual": sorted(actual), "historical": sorted(expected)})
        historical_comparisons[key] = {
            "matches": sum(matches),
            "total": len(matches),
            "mismatches": mismatches,
        }

    paired_gate = comparison(all_rows["keep_opaque_gate"], all_rows["keep_short_gate"])
    paired_full = comparison(all_rows["keep_opaque_full"], all_rows["keep_short_full"])
    paired_drop = comparison(all_rows["keep_opaque_gate"], all_rows["drop_opaque_gate"])
    for pair in (paired_gate, paired_full, paired_drop):
        deltas = pair.pop("wall_delta_right_minus_left_ms")
        pair["wall_delta_right_minus_left_ms"] = {
            "median": statistics.median(deltas) if deltas else None,
            "mean": statistics.mean(deltas) if deltas else None,
            "right_faster_calls": sum(delta < 0 for delta in deltas),
            "left_faster_calls": sum(delta > 0 for delta in deltas),
        }

    composition = read_json(OUT / "PROMPT_COMPOSITION.json")["variants"]
    audit = {
        "schema_version": "panoref-selector-request-encoding-audit-v1",
        "plan_sha256": plan["plan_sha256"],
        "source_commit": plan["source_commit"],
        "replay_sha256": plan["replay_sha256"],
        "historical_selector_summary_sha256": plan["historical_selector_summary_sha256"],
        "runs": summaries,
        "historical_comparison": historical_comparisons,
        "paired_comparison": {
            "keep_gate_opaque_vs_short": paired_gate,
            "keep_full_opaque_vs_short": paired_full,
            "keep_gate_opaque_vs_drop": paired_drop,
        },
        "drop_full_run": False,
        "drop_full_run_reason": "DROP_OPAQUE failed the 24-call semantic gate: 0/24 paired decision matches.",
        "observations": {
            "short_id_mapping": "Exact per-chunk string map, reversible in software; no fuzzy repair.",
            "historical_row_shape_note": "Historical summary stores selector.selected_ids globally, not per telemetry row; historical comparisons were recomputed by chunk intersection.",
            "composition_note": "Provider totals are actual usage.prompt_tokens/completion_tokens. Component token shares are the frozen repository estimate_tokens proxy, not a model-tokenizer decomposition.",
        },
        "composition": composition,
    }
    write_json(OUT / "DECISION_AUDIT.json", audit)

    table_rows = []
    for key in ("keep_opaque_gate", "keep_short_gate", "keep_opaque_full", "keep_short_full", "drop_opaque_gate"):
        summary = summaries[key]
        hist = historical_comparisons[key]
        pair = {
            "keep_opaque_gate": paired_gate,
            "keep_short_gate": paired_gate,
            "keep_opaque_full": paired_full,
            "keep_short_full": paired_full,
            "drop_opaque_gate": paired_drop,
        }[key]
        table_rows.append(
            f"| {key} | {summary['calls']} | {summary['succeeded']}/{summary['calls']} | {summary['selector_wall_seconds']:.1f} s | {summary['ttft_p50_ms']:.1f} ms | {summary['wall_p50_ms']:.1f} ms | {summary['decode_p50_ms']:.1f} ms | {summary['input_tokens']:.0f} | {summary['output_tokens']:.0f} | {hist['matches']}/{hist['total']} | {pair['decision_matches']}/{pair['common_calls']} | {summary['errors']} |"
        )

    report = f"""# PanoRef selector request-encoding benchmark

This is a benchmark-only result. No production ORCHID path, selector chunking,
backend concurrency, Solar stage, or oracle was changed.

- Source commit: `{plan['source_commit']}`
- Plan hash: `{plan['plan_sha256']}`
- Frozen replay SHA-256: `{plan['replay_sha256']}`
- Frozen historical selector summary SHA-256: `{plan['historical_selector_summary_sha256']}`
- Model/backend: `{plan['model']}` / `{plan['endpoint']}`
- Trace: 148 serial 1.2K selector chunks; gate is 24 evenly spaced chunks
- Retries: none; failed calls retained

## Measured arms

| Arm | Calls | Success | Selector wall | TTFT p50 | Wall p50 | Decode p50 | Input tokens | Output tokens | Frozen decision match | Paired decision match | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(table_rows)}

`Frozen decision match` recomputes the historical decision per chunk by
intersecting that chunk's legal IDs with the historical global selector set.
The historical telemetry rows do not contain a per-row selection field.

## Request composition

The frozen serializer is evidence-dominated. Across all 148 chunks:

| Variant | Evidence chars | Schema chars | System chars | Request-ID chars | Actual provider input tokens |
|---|---:|---:|---:|---:|---:|
| KEEP opaque | {composition['KEEP_OPAQUE']['totals']['evidence_json_chars']:.0f} | {composition['KEEP_OPAQUE']['totals']['schema_chars']:.0f} | {composition['KEEP_OPAQUE']['totals']['system_prompt_chars']:.0f} | {composition['KEEP_OPAQUE']['totals']['request_id_chars']:.0f} | {summaries['keep_opaque_full']['input_tokens']:.0f} |
| KEEP short IDs | {composition['KEEP_SHORT_IDS']['totals']['evidence_json_chars']:.0f} | {composition['KEEP_SHORT_IDS']['totals']['schema_chars']:.0f} | {composition['KEEP_SHORT_IDS']['totals']['system_prompt_chars']:.0f} | {composition['KEEP_SHORT_IDS']['totals']['request_id_chars']:.0f} | {summaries['keep_short_full']['input_tokens']:.0f} |
| DROP opaque | {composition['DROP_OPAQUE']['totals']['evidence_json_chars']:.0f} | {composition['DROP_OPAQUE']['totals']['schema_chars']:.0f} | {composition['DROP_OPAQUE']['totals']['system_prompt_chars']:.0f} | {composition['DROP_OPAQUE']['totals']['request_id_chars']:.0f} | {summaries['drop_opaque_gate']['input_tokens']:.0f} / gate |

Short IDs reduced actual full-trace input usage by
`{summaries['keep_opaque_full']['input_tokens'] - summaries['keep_short_full']['input_tokens']:.0f}`
tokens ({(1 - summaries['keep_short_full']['input_tokens'] / summaries['keep_opaque_full']['input_tokens']) * 100:.2f}%).
The observed short-ID output reduction is much larger, but it is coupled to
the six semantic divergences and is therefore not an acceptable semantics-
preserving win.

## Semantic findings

- Short-ID KEEP: 24/24 paired gate matches; full trace 142/148 paired matches.
- Full short-ID divergences: chunks 71, 85, 90, 94, 129, and 133.
- DROP-only KEEP derivation: 0/24 paired matches; no full DROP run was allowed.
- No selector protocol failures, unknown-ID failures, or timeouts occurred in
  any executed arm.

## Interpretation

Short IDs clearly reduce serialized/request tokens and measured wall on this
backend, but the exact-ID representation is not decision-equivalent across the
full trace. The gate was too small to expose the six full-trace divergences.
DROP-only did not preserve the selector's semantic job in the first gate and
was rejected before expansion.

This phase therefore does not validate either request variant as a safe
semantics-preserving optimization. The full-arm timing is descriptive only;
the two 148-call runs occurred serially in different backend latency regimes.

Next hypothesis only: isolate the six short-ID divergence chunks with a
semantics-preserving request representation (for example, preserve original
IDs in evidence while shortening only schema/output aliases) before any
production consideration.
"""
    (OUT / "REPORT.md").write_text(report, encoding="utf-8")
    write_json(OUT / "SUMMARY.json", {
        "schema_version": "panoref-selector-request-encoding-summary-v1",
        "plan_sha256": plan["plan_sha256"],
        "source_commit": plan["source_commit"],
        "summary": summaries,
        "historical_comparison": historical_comparisons,
        "paired_comparison": audit["paired_comparison"],
        "drop_full_run": False,
        "decision": "NO_SAFE_REQUEST_ENCODING_WIN_VALIDATED",
    })
    print(OUT / "REPORT.md")
    print(OUT / "SUMMARY.json")
    print(OUT / "DECISION_AUDIT.json")


if __name__ == "__main__":
    main()
