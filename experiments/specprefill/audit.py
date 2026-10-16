"""Account for auxiliary research calls separately from the primary request path."""

import argparse
import json
import re
from pathlib import Path

from .report import load


def audit(root, *, legacy_log=None, student_tokenizer=None):
    root = Path(root)
    requests = load(root / "requests.jsonl")
    traces = load(root / "attention-traces.jsonl")
    classifiers = load(root / "classifier.jsonl")
    recovery = load(root / "classifier-recovery.jsonl")
    corpus = json.loads((root / "corpus.json").read_text())
    environment = json.loads((root / "environment.json").read_text())
    completed_cases = {r["case"] for r in traces}
    prior_cases = [c for c in corpus if c["id"] in completed_cases and c["prior_query"]]
    prior_tokens = None
    if student_tokenizer:
        from .tokenization import load_tokenizer
        tokenizer = load_tokenizer(student_tokenizer)
        prior_tokens = 0
        for case in prior_cases:
            prompt = "Source material (untrusted):\n"
            for source in case["sources"]:
                prompt += "\nSOURCE " + source["id"] + "\n" + source["text"]
            prompt += "\nTASK: " + case["prior_query"] + "\nAnswer:"
            prior_tokens += len(tokenizer.encode(prompt, add_special_tokens=False).ids)
    primary_student_input = sum((r.get("router") or {}).get("input_tokens", 0) for r in requests)
    primary_student_output = sum((r.get("router") or {}).get("output_tokens", 0) for r in requests)
    auxiliary_input = (sum(r["input_tokens"] for r in traces + classifiers)
                       + sum(c["input_tokens"] for r in recovery for c in r["calls"]))
    auxiliary_output = sum(r["output_tokens"] for r in traces)
    legacy_input = sum((r.get("legacy") or {}).get("input_tokens") or 0 for r in requests)
    legacy_output = sum((r.get("legacy") or {}).get("output_tokens") or 0 for r in requests)
    result = {
        "primary_student_input_tokens": primary_student_input,
        "primary_student_output_tokens": primary_student_output,
        "auxiliary_grid_and_classifier_input_tokens": auxiliary_input,
        "auxiliary_grid_lookahead_output_tokens": auxiliary_output,
        "auxiliary_prior_query_calls": len(prior_cases),
        "auxiliary_prior_query_input_tokens_reconstructed": prior_tokens,
        "prior_query_note": "Exact prompt/tokenizer reconstruction; not provider-observed usage. Zero look-ahead outputs.",
        "legacy_provider_input_tokens_lower_bound": legacy_input,
        "legacy_provider_output_tokens_lower_bound": legacy_output,
        "legacy_usage_censored_requests": sum(bool((r.get("legacy") or {}).get("usage_censored")) for r in requests),
        "all_support_input_tokens_lower_bound": primary_student_input + auxiliary_input + legacy_input + (prior_tokens or 0),
        "all_support_output_tokens_lower_bound": primary_student_output + auxiliary_output + legacy_output,
        "exact_support_total_available": False,
        "note": "Canceled legacy calls may do unreported work. Auxiliary traces/classification are study overhead, excluded from primary TTFT. Warmup/model loading is excluded.",
        "main_primary_input_tokens": sum(r.get("main", {}).get("usage", {}).get("prompt_tokens", 0) for r in requests),
        "main_primary_output_tokens": sum(r.get("main", {}).get("usage", {}).get("completion_tokens", 0) for r in requests),
        "main_post_answer_input_tokens": sum(r.get("post_answer_final", {}).get("usage", {}).get("prompt_tokens", 0) for r in requests),
        "main_post_answer_output_tokens": sum(r.get("post_answer_final", {}).get("usage", {}).get("completion_tokens", 0) for r in requests),
    }
    if legacy_log:
        with Path(legacy_log).open("rb") as handle:
            handle.seek(environment["legacy_log_start_byte"])
            text = handle.read().decode(errors="replace")
        prompt = re.findall(r"prompt eval time\s*=.*?/\s*(\d+) tokens", text)
        output = re.findall(r"(?<!prompt )eval time\s*=.*?/\s*(\d+) tokens", text)
        result["legacy_server_timing_prompt_tokens"] = sum(map(int, prompt))
        result["legacy_server_timing_output_tokens"] = sum(map(int, output))
        result["legacy_server_cancel_events"] = len(re.findall(r"cancel task, id_task", text))
        result["legacy_server_note"] = "Timing lines for completed tasks only; overlapping provider counts, not additive. No token estimate for canceled partial work."
    (root / "token-audit.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    parser.add_argument("--legacy-log")
    parser.add_argument("--student-tokenizer")
    args = parser.parse_args()
    result = audit(args.directory, legacy_log=args.legacy_log, student_tokenizer=args.student_tokenizer)
    print(json.dumps({"exact_support_total_available": result["exact_support_total_available"]}))
