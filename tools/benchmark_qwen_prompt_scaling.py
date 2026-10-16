"""Diagnostic prompt-length scaling benchmark for the frozen local Qwen stack.

This benchmark does not touch production ORCHID behavior. It sends a simple,
deterministic ``OK`` request with inert filler prompts at several approximate
input sizes, serially, using the same model/runtime/generation settings as the
existing selector TTFT profile. Each call has a distinct nonce so exact-repeat
prompt caching is not the variable under test.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_gateway.context import estimate_tokens
from tools.profile_panoref_selector_calls import (
    endpoint_url,
    extract_metric,
    header_metric,
    percentile,
)

OUT_ROOT = ROOT / "artifacts/agent_benchmarks/qwen_prompt_scaling_v1"
PLAN_PATH = OUT_ROOT / "SCALING_PLAN.json"
PROFILE_CONFIG_PATH = ROOT / "artifacts/agent_benchmarks/panoref_selector_microprofile_v1/PROFILE_CONFIG.json"
MODEL = "qwen3.5-4b@q6_k"
TARGETS = [128, 256, 512, 1000, 1500, 2000, 4000]
REPETITIONS = 6
WARMUPS = 3
TIMEOUT_SECONDS = 180.0
SYSTEM_PROMPT = "You are a deterministic diagnostic endpoint. Reply with exactly OK and nothing else."
USER_PREFIX = "Prompt-scaling diagnostic payload. Ignore its meaning and reply with exactly OK.\n"
USER_SUFFIX = "\nEND_DIAGNOSTIC_PAYLOAD"
FILLER_UNIT = "inert prefill scaling unit "


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def git_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def load_profile() -> dict[str, Any]:
    profile = json.loads(PROFILE_CONFIG_PATH.read_text(encoding="utf-8"))
    if profile["model"] != MODEL:
        raise RuntimeError(f"profile model changed: {profile['model']}")
    return profile


def plan_without_hash() -> dict[str, Any]:
    profile = load_profile()
    return {
        "schema_version": "qwen-prompt-scaling-plan-v1",
        "created_at": utc_now(),
        "source_commit": git_commit(),
        "profile_config": str(PROFILE_CONFIG_PATH.resolve()),
        "profile_config_sha256": file_sha256(PROFILE_CONFIG_PATH),
        "endpoint": profile["endpoint"],
        "model": profile["model"],
        "generation": profile["profile_generation"],
        "system_prompt": SYSTEM_PROMPT,
        "user_prefix": USER_PREFIX,
        "user_suffix": USER_SUFFIX,
        "filler_unit": FILLER_UNIT,
        "target_prompt_tokens": TARGETS,
        "repetitions_per_target": REPETITIONS,
        "warmup_calls": WARMUPS,
        "warmup_target_tokens": 1000,
        "serial": True,
        "distinct_nonce_per_call": True,
        "cache_policy": "no explicit cache controls; distinct per-call nonce prevents exact-repeat identity across measurements",
        "output_contract": "assistant content must be exactly OK after whitespace stripping",
        "measurement_policy": "TTFT is measured from request start to first streamed content token; backend phase metrics are recorded only when exposed",
        "no_retries": True,
    }


def freeze_plan() -> None:
    if PLAN_PATH.exists():
        raise RuntimeError(f"refusing to overwrite frozen plan: {PLAN_PATH}")
    raw = plan_without_hash()
    plan = {**raw, "plan_sha256": digest(raw)}
    write_json(PLAN_PATH, plan)
    print(f"frozen plan: {PLAN_PATH}")
    print(f"plan hash: {plan['plan_sha256']}")


def load_plan() -> dict[str, Any]:
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    expected = dict(plan)
    actual = expected.pop("plan_sha256", None)
    if actual != digest(expected):
        raise RuntimeError("scaling plan hash mismatch")
    return plan


def make_prompt(target_tokens: int, nonce: str) -> tuple[str, dict[str, Any]]:
    user_base = USER_PREFIX + f"measurement_nonce={nonce}\n"
    filler = ""
    prompt_estimate = estimate_tokens(SYSTEM_PROMPT + user_base + USER_SUFFIX)
    while prompt_estimate < target_tokens:
        filler += FILLER_UNIT
        prompt_estimate = estimate_tokens(SYSTEM_PROMPT + user_base + filler + USER_SUFFIX)
    user = user_base + filler + USER_SUFFIX
    return user, {
        "target_prompt_tokens": target_tokens,
        "estimated_prompt_tokens": estimate_tokens(SYSTEM_PROMPT + user),
        "prompt_chars": len(SYSTEM_PROMPT + user),
        "filler_chars": len(filler),
        "nonce": nonce,
    }


def build_body(plan: dict[str, Any], target_tokens: int, nonce: str) -> tuple[dict[str, Any], dict[str, Any]]:
    user, identity = make_prompt(target_tokens, nonce)
    body = {
        "model": plan["model"],
        "messages": [
            {"role": "system", "content": plan["system_prompt"]},
            {"role": "user", "content": user},
        ],
        **plan["generation"],
    }
    identity.update({
        "request_hash": digest(body),
        "prompt_hash": digest(body["messages"]),
        "system_prompt_hash": digest(plan["system_prompt"]),
    })
    return body, identity


def find_named_fields(value: Any, path: str = "") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if any(term in key.lower().replace("-", "_") for term in ("cache", "prefix", "kv_")):
                if isinstance(child, (str, int, float, bool)) or child is None:
                    result.append({"path": child_path, "value": child})
            result.extend(find_named_fields(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.extend(find_named_fields(child, f"{path}[{index}]"))
    return result


async def call_once(
    client: httpx.AsyncClient,
    plan: dict[str, Any],
    *,
    phase: str,
    target_tokens: int,
    repetition: int,
    global_index: int,
) -> dict[str, Any]:
    nonce = f"{phase}-{target_tokens:04d}-{repetition:02d}-{global_index:04d}"
    body, identity = build_body(plan, target_tokens, nonce)
    started = time.perf_counter()
    first_content_at: float | None = None
    content = ""
    frames = 0
    usage: dict[str, Any] | None = None
    response_status: int | None = None
    response_headers: dict[str, str] = {}
    response_payload_keys: set[str] = set()
    explicit_cache_fields: list[dict[str, Any]] = []
    backend_prompt_eval_ms = None
    backend_decode_ms = None
    backend_queue_wait_ms = None
    error = None
    error_category = None
    try:
        async with client.stream("POST", endpoint_url(plan["endpoint"]), json=body, headers={"content-type": "application/json"}) as response:
            response_status = response.status_code
            response_headers = dict(response.headers)
            if response.status_code >= 400:
                error_category = "http"
                error = f"HTTP {response.status_code}: {(await response.aread())[:500]!r}"
            else:
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload_text = line[5:].strip()
                    if payload_text == "[DONE]":
                        continue
                    payload = json.loads(payload_text)
                    frames += 1
                    response_payload_keys.update(payload.keys())
                    explicit_cache_fields.extend(find_named_fields(payload))
                    usage = payload.get("usage") or usage
                    backend_prompt_eval_ms = backend_prompt_eval_ms or extract_metric(payload, "prompt_eval_ms") or extract_metric(payload, "prompt_eval_time_ms") or extract_metric(payload, "prompt_eval_duration_ms") or extract_metric(payload, "prompt_eval_duration") or extract_metric(payload, "prefill_ms")
                    backend_decode_ms = backend_decode_ms or extract_metric(payload, "decode_ms") or extract_metric(payload, "decode_time_ms") or extract_metric(payload, "eval_duration") or extract_metric(payload, "generation_time_ms") or extract_metric(payload, "generation_time")
                    backend_queue_wait_ms = backend_queue_wait_ms or extract_metric(payload, "queue_wait_ms") or extract_metric(payload, "queue_time_ms") or extract_metric(payload, "queue_wait")
                    choice = (payload.get("choices") or [{}])[0]
                    token = ((choice.get("delta") or {}).get("content") or choice.get("text") or "")
                    if token and first_content_at is None:
                        first_content_at = time.perf_counter()
                    content += token
                backend_prompt_eval_ms = backend_prompt_eval_ms or header_metric(response.headers, {"x_prompt_eval_ms", "x_prompt_eval_duration_ms", "x_prefill_ms"})
                backend_decode_ms = backend_decode_ms or header_metric(response.headers, {"x_decode_ms", "x_eval_duration_ms"})
                backend_queue_wait_ms = backend_queue_wait_ms or header_metric(response.headers, {"x_queue_wait_ms"})
    except httpx.TimeoutException as exc:
        error_category, error = "timeout", str(exc) or "request timed out"
    except Exception as exc:
        error_category, error = "protocol_or_transport", f"{type(exc).__name__}: {exc}"
    ended = time.perf_counter()
    total_wall_ms = (ended - started) * 1000
    ttft_ms = (first_content_at - started) * 1000 if first_content_at else None
    decode_ms = (ended - first_content_at) * 1000 if first_content_at else None
    if error is None and content.strip() != "OK":
        error_category = "output_contract"
        error = f"expected exact OK, got {content[:200]!r}"
    input_tokens = (usage or {}).get("prompt_tokens")
    output_tokens = (usage or {}).get("completion_tokens")
    return {
        "timestamp": utc_now(),
        "global_call_index": global_index,
        "phase": phase,
        "repetition": repetition,
        **identity,
        "status": "SUCCEEDED" if error is None else "FAILED",
        "error": error,
        "error_category": error_category,
        "http_status": response_status,
        "frame_count": frames,
        "response_payload_keys": sorted(response_payload_keys),
        "response_headers": {key: value for key, value in response_headers.items() if key.lower().startswith("x-") or "time" in key.lower() or "queue" in key.lower() or "cache" in key.lower()},
        "explicit_cache_fields": explicit_cache_fields,
        "assistant_content": content,
        "assistant_content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest() if content else None,
        "usage": usage,
        "metrics": {
            "total_wall_ms": total_wall_ms,
            "ttft_ms": ttft_ms,
            "decode_ms_client_observed": decode_ms,
            "prompt_eval_ms_backend": backend_prompt_eval_ms,
            "decode_ms_backend": backend_decode_ms,
            "queue_wait_ms_backend": backend_queue_wait_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_tok_s_ttft_proxy": float(input_tokens) / (ttft_ms / 1000) if input_tokens and ttft_ms and ttft_ms > 0 else None,
        },
    }


async def run_async(run_id: str) -> None:
    plan = load_plan()
    run_dir = OUT_ROOT / "runs" / run_id
    if run_dir.exists():
        raise RuntimeError(f"refusing to overwrite run directory: {run_dir}")
    run_dir.mkdir(parents=True)
    write_json(run_dir / "RUN_CONFIG.json", {
        "schema_version": "qwen-prompt-scaling-run-v1",
        "run_id": run_id,
        "plan_sha256": plan["plan_sha256"],
        "source_commit": plan["source_commit"],
        "targets": TARGETS,
        "repetitions": REPETITIONS,
        "warmups": WARMUPS,
        "serial": True,
        "no_retries": True,
    })
    rows_path = run_dir / "CALLS.jsonl"
    global_index = 0
    timeout = httpx.Timeout(TIMEOUT_SECONDS, connect=10)
    async with httpx.AsyncClient(timeout=timeout) as client:
        with rows_path.open("w", encoding="utf-8") as output:
            warmup_targets = [1000] * WARMUPS
            for warmup_index, target in enumerate(warmup_targets, start=1):
                global_index += 1
                print(f"call {global_index}/{WARMUPS + len(TARGETS) * REPETITIONS} warmup target={target}", flush=True)
                row = await call_once(client, plan, phase="warmup", target_tokens=target, repetition=warmup_index, global_index=global_index)
                output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                output.flush()
            for target in TARGETS:
                for repetition in range(1, REPETITIONS + 1):
                    global_index += 1
                    print(f"call {global_index}/{WARMUPS + len(TARGETS) * REPETITIONS} target={target} rep={repetition}", flush=True)
                    row = await call_once(client, plan, phase="measurement", target_tokens=target, repetition=repetition, global_index=global_index)
                    output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                    output.flush()
    print(f"run: {run_dir}")


def load_rows(run_id: str) -> list[dict[str, Any]]:
    path = OUT_ROOT / "runs" / run_id / "CALLS.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def metric_values(rows: list[dict[str, Any]], name: str) -> list[float]:
    return [float(row["metrics"][name]) for row in rows if row["metrics"].get(name) is not None]


def metric_stats(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    data = metric_values(rows, name)
    return {"count": len(data), "p50": percentile(data, 50) if data else None, "p95": percentile(data, 95) if data else None, "mean": mean(data) if data else None, "total": sum(data) if data else None}


def fit_linear(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = [(float(row["metrics"]["input_tokens"]), float(row["metrics"]["ttft_ms"])) for row in rows if row["status"] == "SUCCEEDED" and row["metrics"].get("input_tokens") is not None and row["metrics"].get("ttft_ms") is not None]
    if len(pairs) < 2:
        return {"n": len(pairs), "intercept_ms": None, "slope_ms_per_token": None, "r2": None}
    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    mx, my = mean(xs), mean(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in pairs) / denom if denom else 0.0
    intercept = my - slope * mx
    predictions = [intercept + slope * x for x in xs]
    ss_res = sum((y - pred) ** 2 for y, pred in zip(ys, predictions))
    ss_tot = sum((y - my) ** 2 for y in ys)
    return {
        "n": len(pairs),
        "input_token_min": min(xs),
        "input_token_max": max(xs),
        "intercept_ms": intercept,
        "slope_ms_per_token": slope,
        "slope_ms_per_1000_tokens": slope * 1000,
        "r2": 1 - ss_res / ss_tot if ss_tot else None,
        "predicted_ttft_ms_at_0": intercept,
        "predicted_ttft_ms_at_128": intercept + slope * 128,
        "predicted_ttft_ms_at_4000": intercept + slope * 4000,
    }


def report(run_id: str) -> None:
    plan = load_plan()
    rows = load_rows(run_id)
    measurements = [row for row in rows if row["phase"] == "measurement"]
    run_dir = OUT_ROOT / "runs" / run_id
    groups: dict[int, list[dict[str, Any]]] = {target: [row for row in measurements if row["target_prompt_tokens"] == target] for target in TARGETS}
    summary = {
        "schema_version": "qwen-prompt-scaling-summary-v1",
        "run_id": run_id,
        "plan_sha256": plan["plan_sha256"],
        "calls_recorded": len(rows),
        "warmup_calls": sum(1 for row in rows if row["phase"] == "warmup"),
        "measurement_calls": len(measurements),
        "measurement_failures": [{"target": row["target_prompt_tokens"], "repetition": row["repetition"], "category": row["error_category"], "error": row["error"]} for row in measurements if row["status"] != "SUCCEEDED"],
        "by_target": {str(target): {"calls": len(group), "succeeded": sum(1 for row in group if row["status"] == "SUCCEEDED"), "input_tokens": metric_stats(group, "input_tokens"), "ttft_ms": metric_stats(group, "ttft_ms"), "total_wall_ms": metric_stats(group, "total_wall_ms"), "prompt_eval_ms_backend": metric_stats(group, "prompt_eval_ms_backend"), "output_tokens": metric_stats(group, "output_tokens")} for target, group in groups.items()},
        "linear_fit": fit_linear(measurements),
    }
    write_json(run_dir / "SUMMARY.json", summary)
    lines = [
        f"# Qwen prompt-length scaling benchmark: `{run_id}`",
        "",
        f"Plan hash: `{plan['plan_sha256']}`; source commit: `{plan['source_commit']}`.",
        f"Model: `{plan['model']}`; serial; {REPETITIONS} repetitions per target; {WARMUPS} common warmups. Each measurement used a distinct nonce to avoid exact-repeat request identity.",
        "",
        "| Target tokens | Calls | Actual input p50 / mean | TTFT p50 | TTFT p95 | Total wall p50 | Backend prompt eval |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for target in TARGETS:
        data = summary["by_target"][str(target)]
        inp, ttft, wall, prompt_eval = data["input_tokens"], data["ttft_ms"], data["total_wall_ms"], data["prompt_eval_ms_backend"]
        lines.append(f"| {target} | {data['calls']} ({data['succeeded']} ok) | {inp['p50']} / {inp['mean']} | {ttft['p50']} ms | {ttft['p95']} ms | {wall['p50']} ms | {prompt_eval['p50']} ms |")
    fit = summary["linear_fit"]
    lines.extend([
        "",
        "## Fixed-plus-linear fit",
        "",
        f"`TTFT_ms = {fit['intercept_ms']:.3f} + {fit['slope_ms_per_token']:.6f} * input_tokens` (n={fit['n']}, R²={fit['r2']:.4f}).",
        f"Estimated fixed component: `{fit['intercept_ms']:.1f} ms`; estimated prompt component: `{fit['slope_ms_per_1000_tokens']:.1f} ms per 1,000 input tokens`.",
        f"Predicted TTFT at 0 / 128 / 4,000 tokens: `{fit['predicted_ttft_ms_at_0']:.1f}` / `{fit['predicted_ttft_ms_at_128']:.1f}` / `{fit['predicted_ttft_ms_at_4000']:.1f}` ms.",
        "",
        "Backend prompt-eval, queue-wait, and scheduling metrics were recorded when exposed. LM Studio did not expose prompt-eval timing if the corresponding summary values are null; client TTFT is measured directly from request start to first streamed content token.",
        "",
    ])
    low = summary["by_target"][str(TARGETS[0])]["ttft_ms"]["p50"]
    high = summary["by_target"][str(TARGETS[-1])]["ttft_ms"]["p50"]
    if fit["intercept_ms"] >= 0.5 * low and low >= 2000:
        verdict = "FIXED_RUNTIME_OVERHEAD_DOMINATES"
        interpretation = "The smallest prompts already incur a multi-second TTFT and the fitted fixed component is at least half of the 128-token median. Investigate runtime/queue/transport overhead before kernel tuning."
    elif fit["slope_ms_per_token"] > 0 and fit["r2"] is not None and fit["r2"] >= 0.5 and high >= 1.5 * low:
        verdict = "PREFILL_SCALING_DOMINATES"
        interpretation = "TTFT rises materially with prompt length and the linear fit explains a substantial fraction of observed variance. Investigate prompt-eval throughput and kernels next."
    else:
        verdict = "MIXED_OR_INSUFFICIENT_EVIDENCE"
        interpretation = "The measured curve does not cleanly separate fixed overhead from prompt scaling at this sample size and variance level."
    lines.extend(["## Conclusion", "", interpretation, "", verdict])
    (run_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"summary: {run_dir / 'SUMMARY.json'}")
    print(f"report: {run_dir / 'REPORT.md'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("freeze-plan")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--run-id", required=True)
    report_parser = sub.add_parser("report")
    report_parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if args.command == "freeze-plan":
        freeze_plan()
    elif args.command == "run":
        asyncio.run(run_async(args.run_id))
    elif args.command == "report":
        report(args.run_id)


if __name__ == "__main__":
    main()
