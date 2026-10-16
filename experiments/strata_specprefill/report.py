"""Export only aggregate smoke results; keep prompts, outputs and paths private."""

import argparse
import hashlib
import json
import statistics
from pathlib import Path


def summarize(root):
    complete = json.loads((root / "COMPLETE.json").read_text())
    protocol = json.loads((root / "protocol.json").read_text())
    frozen_hash = protocol.pop("sha256")
    if hashlib.sha256(json.dumps(protocol, sort_keys=True).encode()).hexdigest() != frozen_hash:
        raise ValueError("Frozen protocol has changed")
    protocol["sha256"] = frozen_hash
    rows = [json.loads(line) for line in (root / "requests.jsonl").read_text().splitlines()]
    if (
        complete["protocol_sha256"] != protocol["sha256"]
        or complete["requests"] != 16
        or len(rows) != 16
    ):
        raise ValueError("Incomplete or mismatched smoke protocol")
    case_ids = {c["id"] for c in protocol["cases"]}
    expected = {(c, "standalone", "full") for c in case_ids} | {
        (c, "colocated", arm) for c in case_ids for arm in ("full", "keep100", "keep50")
    }
    if {(r["case"], r["epoch"], r["arm"]) for r in rows} != expected:
        raise ValueError("Missing or duplicate case/epoch/arm")
    summary = {
        "status": "completed_functional_smoke_not_benchmark",
        "protocol_sha256": protocol["sha256"],
        "cases": 4,
        "measured_requests": 16,
        "warmup_requests_excluded": 4,
        "variants": {},
        "per_case": [],
        "scope": "Same-model/same-hardware method experiment; no ORCHID retrieval/gating or paper reproduction.",
    }
    for epoch, arm in (
        ("standalone", "full"),
        ("colocated", "full"),
        ("colocated", "keep100"),
        ("colocated", "keep50"),
    ):
        selected = [r for r in rows if r["epoch"] == epoch and r["arm"] == arm]
        summary["variants"][epoch + "/" + arm] = {
            "correct": sum(r["correct"] for r in selected),
            "cases": len(selected),
            "mean_pipeline_ttft_s": statistics.mean(r["pipeline_ttft_s"] for r in selected),
            "mean_server_ttft_s": statistics.mean(r["server_ttft_s"] for r in selected),
            "mean_pre_request_s": statistics.mean(r["pre_request_s"] for r in selected),
            "mean_total_s": statistics.mean(r["total_s"] for r in selected),
            "retained_fraction": sum(r["retained_tokens"] for r in selected)
            / sum(r["original_tokens"] for r in selected),
            "mean_student_selection_s": statistics.mean(
                r["trace"]["student_selection_s"] if r["trace"] else 0 for r in selected
            ),
        }
    for case in protocol["cases"]:
        by_arm = {r["epoch"] + "/" + r["arm"]: r for r in rows if r["case"] == case["id"]}
        if by_arm["colocated/full"]["output_ids"] != by_arm["colocated/keep100"]["output_ids"]:
            raise ValueError("Full-retention generation equivalence failed")
        sparse = by_arm["colocated/keep50"]
        summary["per_case"].append(
            {
                "case": case["id"],
                "original_tokens": len(case["input_ids"]),
                "retained_tokens": sparse["retained_tokens"],
                "ttft_s": {arm: row["pipeline_ttft_s"] for arm, row in by_arm.items()},
                "correct": {arm: row["correct"] for arm, row in by_arm.items()},
                "student_s": sparse["trace"]["student_selection_s"],
                "query_rows": sparse["trace"]["query_rows"],
                "attention_layers": sparse["trace"]["attention_layer_ids"],
                "peak_student_allocator_mib": sparse["trace"]["peak_student_allocator_mib"],
                "full_retention_matches": True,
            }
        )
    return summary


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
