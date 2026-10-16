"""Aggregate sealed request records without changing controller thresholds."""

import argparse
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path


def mean(values):
    values = [v for v in values if v is not None]
    return statistics.mean(values) if values else None


def load(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def detection(rows):
    tp = fp = fn = tn = 0
    confusion = defaultdict(int)
    for row in rows:
        needed = not row["initially_sufficient"]
        predict = row["decision"] != "SUFFICIENT"
        tp += needed and predict
        fp += not needed and predict
        fn += needed and not predict
        tn += not needed and not predict
        actual = "SUFFICIENT" if not needed else ("INSUFFICIENT" if row.get("case", "").startswith("unanswerable-") else "RETRIEVE")
        confusion[actual + "->" + row["decision"]] += 1
    return {"cases": len(rows), "needed_retrieval": tp + fn, "false_sufficient": fn,
            "false_sufficient_rate": fn / (tp + fn) if tp + fn else None,
            "false_insufficient": fp, "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "false_retrieve": sum(r["decision"] == "RETRIEVE" and r["initially_sufficient"] for r in rows),
            "false_retrieve_rate": sum(r["decision"] == "RETRIEVE" and r["initially_sufficient"] for r in rows) / (fp + tn) if fp + tn else None,
            "three_way_confusion": dict(confusion)}


def calibration(rows):
    if not rows:
        return {}
    pairs = [(r["probabilities"][0], float(r["initially_sufficient"])) for r in rows]
    brier = mean([(p - y) ** 2 for p, y in pairs])
    ece = 0
    for index in range(10):
        group = [(p, y) for p, y in pairs if min(9, int(p * 10)) == index]
        if group:
            ece += len(group) / len(pairs) * abs(mean([p for p, _ in group]) - mean([y for _, y in group]))
    return {"brier_sufficient": brier, "ece_10_bins": ece, "calibrated": False,
            "note": "Descriptive validation only; no thresholds tuned on held-out data."}


def paired(rows, arm):
    by_case = defaultdict(dict)
    for row in rows:
        if row.get("end_to_end_ttft_ms") is not None:
            by_case[row["case"]][row["arm"]] = row
    pairs = [(group["A"], group[arm]) for group in by_case.values() if "A" in group and arm in group]
    differences = [a["end_to_end_ttft_ms"] - b["end_to_end_ttft_ms"] for a, b in pairs]
    if not differences:
        return {"pairs": 0}
    rng = random.Random(20261015)
    bootstrap = sorted(mean([rng.choice(differences) for _ in differences]) for _ in range(2000))
    return {"pairs": len(pairs), "mean_improvement_ms": mean(differences),
            "median_improvement_ms": statistics.median(differences),
            "bootstrap_95_interval_ms": [bootstrap[49], bootstrap[1949]],
            "relative_mean_improvement": sum(differences) / sum(a["end_to_end_ttft_ms"] for a, _ in pairs)}


def arm_summary(rows):
    answered = [r for r in rows if "main" in r and "main_error" not in r]
    gates = [dict(r["retrieval"]["decisions"][0], initially_sufficient=r["initially_sufficient"], case=r["case"])
             for r in rows if r.get("retrieval") and r["retrieval"].get("decisions")]
    source_recall = [r["final_evidence_recall"] for r in rows if r["category"] != "unanswerable"]
    result = {"requests": len(rows), "main_successes": len(answered), "main_errors": len(rows) - len(answered),
              "answer_accuracy": mean([r["main"]["correct"] for r in answered]),
              "required_evidence_recall": mean(source_recall),
              "initial_evidence_recall": mean([r["initial_evidence_recall"] for r in rows if r["category"] != "unanswerable"]),
              "before_fallback_retrieval_recall": mean([r["retrieval"].get("before_fallback_evidence_recall") for r in rows if r.get("retrieval") and r["category"] != "unanswerable"]),
              "mean_actual_main_input_tokens": mean([r["main"].get("usage", {}).get("prompt_tokens") for r in answered]),
              "mean_context_tokens": mean([r["main_context_tokens"] for r in rows]),
              "mean_main_ttft_ms": mean([r["main"]["ttft_ms"] for r in answered]),
              "mean_end_to_end_ttft_ms": mean([r.get("end_to_end_ttft_ms") for r in answered]),
              "mean_total_request_ms": mean([r.get("total_request_ms") for r in answered]),
              "mean_preprocessing_ms": mean([r["preprocessing_ms"] for r in rows]),
              "mean_ingestion_ms": mean([r.get("ingestion_ms") for r in rows]),
              "mean_router_ms": mean([r["router"]["wall_ms"] for r in rows if r.get("router")]),
              "mean_qk_extraction_ms": mean([r["router"]["attention_extraction_ms"] for r in rows if r.get("router")]),
              "support_input_tokens_reported": sum(((r.get("router") or {}).get("input_tokens") or 0) + (r.get("legacy", {}).get("input_tokens") or 0) for r in rows),
              "support_output_tokens_reported": sum(((r.get("router") or {}).get("output_tokens") or 0) + (r.get("legacy", {}).get("output_tokens") or 0) for r in rows),
              "legacy_usage_censored_requests": sum(bool(r.get("legacy", {}).get("usage_censored")) for r in rows),
              "legacy_completed_requests": sum(r.get("legacy", {}).get("status") == "succeeded" for r in rows),
              "retrieval_rate": mean([bool((r.get("retrieval") or {}).get("rounds")) for r in rows]),
              "full_fallback_rate": mean([(r.get("retrieval") or {}).get("full_fallback", False) for r in rows]),
              "mean_retrieval_ms": mean([sum(round_["elapsed_ms"] for round_ in (r.get("retrieval") or {}).get("rounds", [])) for r in rows]),
              "mean_evaluator_ms": mean([sum(d.get("elapsed_ms", 0) for d in (r.get("retrieval") or {}).get("decisions", [])) for r in rows]),
              "unsupported_non_unknown_answer_rate": mean([r["main"]["answer"] != "UNKNOWN" and
                  (not r["main"]["correct"] or r["finally_sufficient"] is False) for r in answered]),
              "cache_tokens_reported": sum(r["main"].get("usage", {}).get("prompt_tokens_details", {}).get("cached_tokens", 0) for r in answered),
              "evaluator": detection(gates)}
    return result


def generate(root):
    root = Path(root)
    requests, classifiers = load(root / "requests.jsonl"), load(root / "classifier.jsonl")
    if not requests:
        raise ValueError("No measured requests")
    groups = defaultdict(list)
    for row in requests:
        groups[row["split"], row["category"], row["arm"]].append(row)
    summary = {"by_category": {"|".join(key): arm_summary(rows) for key, rows in groups.items()},
               "by_split": {}, "paired": {}, "classifier": {}}
    for split in ("development", "held_out"):
        rows = [r for r in requests if r["split"] == split]
        summary["by_split"][split] = {arm: arm_summary([r for r in rows if r["arm"] == arm]) for arm in "ABCDEF"}
        summary["paired"][split] = {arm: paired(rows, arm) for arm in "BCDEF"}
        classified = [r for r in classifiers if r["split"] == split]
        summary["classifier"][split] = {"detection": detection(classified), "calibration": calibration(classified),
                                        "mean_ms": mean([r["elapsed_ms"] for r in classified]),
                                        "input_tokens": sum(r["input_tokens"] for r in classified)}
    test = summary["by_split"]["held_out"]
    a, e, f = test["A"], test["E"], test["F"]
    compressible = [r for r in requests if r["split"] == "held_out" and r["category"] != "dense"]
    by_arm = {arm: arm_summary([r for r in compressible if r["arm"] == arm]) for arm in "AEF"}
    actual_reduction = (1 - by_arm["E"]["mean_actual_main_input_tokens"] / by_arm["A"]["mean_actual_main_input_tokens"]
                        if by_arm["A"]["mean_actual_main_input_tokens"] and by_arm["E"]["mean_actual_main_input_tokens"] else None)
    tests = {"evidence_recall_at_least_95_percent": (e["required_evidence_recall"] or 0) >= 0.95,
             "false_sufficient_below_1_percent": e["evaluator"].get("false_sufficient_rate") is not None and e["evaluator"]["false_sufficient_rate"] < 0.01,
             "compressible_input_reduction_at_least_30_percent": actual_reduction is not None and actual_reduction >= 0.3,
             "accuracy_loss_at_most_2_points": a["answer_accuracy"] is not None and e["answer_accuracy"] is not None and a["answer_accuracy"] - e["answer_accuracy"] <= 0.02,
             "positive_paired_net_ttft": summary["paired"]["held_out"]["E"].get("mean_improvement_ms", 0) > 0}
    complete = len(requests) == 128 * 6 and all(r.get("main") and not r.get("main_error") for r in requests)
    simpler = bool(e["answer_accuracy"] is not None and f["answer_accuracy"] is not None
                   and f["answer_accuracy"] >= e["answer_accuracy"] - 0.02
                   and f["mean_end_to_end_ttft_ms"] <= e["mean_end_to_end_ttft_ms"])
    finding = "Proceed" if complete and all(tests.values()) else "Revise"
    if complete and (not tests["positive_paired_net_ttft"] or simpler):
        finding = "Reject"
    summary.update(finding=finding, complete=complete, decision_targets=tests,
                   lexical_not_inferior_and_faster=simpler, compressible_main_input_reduction=actual_reduction)
    matrix = load(root / "ablations.jsonl")
    ablations = defaultdict(list)
    for row in matrix:
        if row["split"] != "held_out":
            continue
        key = row.get("condition") or f"lookahead={row['lookahead']},chunk={row['chunk_size']},pool={row['pooling']},reference={row['reference_order']},keep={row['keep_ratio']}"
        ablations[key].append(row)
    summary["ablations"] = {key: {"cases": len(rows), "evidence_recall": mean([r["evidence_recall"] for r in rows]),
                                 "retained_tokens": mean([r["retained_tokens"] for r in rows])} for key, rows in ablations.items()}
    post = [r for r in requests if r["split"] == "held_out" and r["arm"] == "C"]
    summary["post_answer_recovery"] = {"requests": len(post), "main_requested_more": sum("post_answer_final" in r for r in post),
        "final_accuracy": mean([(r.get("post_answer_final") or r.get("main", {})).get("correct") for r in post]),
        "mean_total_ms": mean([r.get("post_answer_total_ms", r.get("total_request_ms")) for r in post])}
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    lines = ["# Controlled SpecPrefill / evidence experiment", "", f"**Finding: {finding}.**", "",
             f"Completed measurement set: {complete}. Requests: {len(requests)}; expected 768 across 128 cases.", "",
             "Primary condition: 50% eligible-source budget, 64 main-token chunks, last prompt plus up to eight new query positions; 13-token aggregate-then-pool smoothing. No original position IDs restored.", "",
             "| Held-out arm | Accuracy | Evidence recall | Actual input tokens | Main TTFT ms | End-to-end TTFT ms | Full fallback |", "|---|---:|---:|---:|---:|---:|---:|"]
    def fmt(value, percent=False):
        return "n/a" if value is None else (f"{value:.1%}" if percent else f"{value:.1f}")
    for arm in "ABCDEF":
        values = test[arm]
        lines.append(f"| {arm} | {fmt(values['answer_accuracy'], True)} | {fmt(values['required_evidence_recall'], True)} | {fmt(values['mean_actual_main_input_tokens'])} | {fmt(values['mean_main_ttft_ms'])} | {fmt(values['mean_end_to_end_ttft_ms'])} | {fmt(values['full_fallback_rate'], True)} |")
    lines += ["", "A=full; B=existing three-stage compactor with an explicit eight-second foreground deadline and full fallback; C=attention only; D=attention plus one unconditional FTS retrieval; E=attention plus deterministic sufficiency/recovery; F=lexical plus the same gate.", "",
              "The B deadline is an experimental foreground budget, not the historical asynchronous compactor's default timeout. Its failures and censored provider usage are reported; do not treat these as completed summaries or as a cloud-provider comparison.", "",
              "| Held-out category | Full accuracy | Attention + gate accuracy | Lexical + gate accuracy | Attention + gate evidence recall |", "|---|---:|---:|---:|---:|"]
    categories = sorted({r["category"] for r in requests})
    for category in categories:
        vals = [summary["by_category"].get(f"held_out|{category}|{arm}", {}) for arm in "AEF"]
        lines.append(f"| {category} | {fmt(vals[0].get('answer_accuracy'), True)} | {fmt(vals[1].get('answer_accuracy'), True)} | {fmt(vals[2].get('answer_accuracy'), True)} | {fmt(vals[1].get('required_evidence_recall'), True)} |")
    lines += ["", "## Decision targets", ""] + [f"- {name}: {'PASS' if passed else 'FAIL'}." for name, passed in tests.items()]
    lines += ["", "## Interpretation and limits", "",
              "Recommend the simpler lexical selector over attention under these measurements." if simpler else "Attention must justify its added full-context read and Q/K capture cost against the lexical control.", "",
              "Evidence recall is a supporting-span metric, not an answer-quality substitute. Final correctness uses sealed exact-answer rubrics. Full fallback can raise recall while removing savings. Unsupported-answer rates in summary.json combine correctness with the required-evidence rubric; raw request records also retain their narrower evidence-coverage proxy.", "",
              "The untrained three-label classifier is evaluated separately. Its softmax scores are uncalibrated; held-out Brier/ECE are descriptive and did not select thresholds. The small held-out sample cannot establish a sub-1% population error bound, even if an empirical count is zero.", "",
              "Q/K extraction uses FP32 selected-query scoring and SDPA for normal model execution. The 0.8B hybrid contributes six full-attention layers; recurrent layers have no attention rows. Transformers uses reference GDN kernels. Attention and main inference share the P100; the legacy 4B uses the RX 580. This is not an RX-580 attention implementation or an engine-level position-restoration experiment.", "",
              "TTFT is measured to the first nonempty content delta, using a wall clock beginning before immutable ingestion. Main-only timing is separate. All arms use the same main model and generation settings; alternating arm order and per-request nonces prevent long-prefix cache reuse. Provider cache counters, router/evaluator/retrieval costs and censoring are retained in JSON. Startup/model load is outside request timings.", "",
              "Ablation JSON measures evidence retention across 10/30/50/70/100% budgets, 0/8 look-ahead, raw tokens and 32/64/128-token chunks, aggregate-first and reference pool-first smoothing. It does not claim end-task answer accuracy for every grid point. Post-answer recovery uses UNKNOWN as the main model's request for more evidence; it is a bounded control, not a general agent tool-use evaluation.", "",
              "No production behavior was changed. Stop at this experiment; do not enable the controller or start a compensating kernel/engine optimization based on these results.", ""]
    (root / "REPORT.md").write_text("\n".join(lines))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    args = parser.parse_args()
    result = generate(args.directory)
    print(json.dumps({"finding": result["finding"], "complete": result["complete"]}))
