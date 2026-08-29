"""Tiny frozen PanoRef A/B: LM Studio OpenAI path versus llama-server.

This is a disposable selector-only benchmark. It reuses the existing frozen
PanoRef selector request construction and first 24 exact 1.2K chunks. The
conditions differ only in serving endpoint: the current LM Studio path versus
the locally installed llama.cpp server with explicit serial slot, prompt-cache,
cache-reuse, checkpoint, /slots, and /metrics controls.
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
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_gateway.pipeline_adapters import selector_response_format_for_chunk
from tools.benchmark_panoref_selector_explicit_cache import (
    call_selector,
    canonical_bytes,
    metrics,
    write_json,
)
from tools.benchmark_panoref_selector_prefix_reuse import (
    PROFILE_CONFIG_PATH,
    build_chunk_lookup,
    file_sha256,
    load_profile_inputs,
)

OUT_ROOT = ROOT / "artifacts/agent_benchmarks/panoref_selector_llama_server_ab_v1"
PLAN_PATH = OUT_ROOT / "LLAMA_SERVER_AB_PLAN.json"
CONFIG_PATH = OUT_ROOT / "LLAMA_SERVER_AB_CONFIG.json"
RUN_ROOT = OUT_ROOT / "runs"

CALL_COUNT = 24
MODEL = "qwen3.5-4b@q6_k"
LMSTUDIO_ENDPOINT = "http://127.0.0.1:1234/v1"
LLAMA_ENDPOINT = "http://127.0.0.1:18080/v1"
LMSTUDIO_OBSERVABILITY = "http://127.0.0.1:1234"
LLAMA_OBSERVABILITY = "http://127.0.0.1:18080"
TIMEOUT_SECONDS = 180.0
LOW_TTFT_MS = 1_000.0
COLD_TTFT_MS = 5_000.0


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def git_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def plan_without_hash() -> dict[str, Any]:
    profile_config, _sample, items = load_profile_inputs()
    lookup = build_chunk_lookup(profile_config, items)
    if len(lookup) != 148:
        raise RuntimeError(f"expected the frozen 148-call source profile, got {len(lookup)}")
    chunks: list[dict[str, Any]] = []
    for index in range(CALL_COUNT):
        chunk = lookup[index]
        schema = selector_response_format_for_chunk(chunk["event_ids"])
        chunks.append(
            {
                "chunk_index": index,
                "event_ids": chunk["event_ids"],
                "event_count": len(chunk["items"]),
                "estimated_source_tokens": chunk["estimated_source_tokens"],
                "request_payload_hash": hashlib.sha256(
                    canonical_bytes(chunk["payload"])
                ).hexdigest(),
                "response_schema_hash": hashlib.sha256(
                    canonical_bytes(schema)
                ).hexdigest(),
            }
        )
    return {
        "schema_version": "panoref-selector-llama-server-ab-plan-v1",
        "created_at": utc_now(),
        "source_commit": git_commit(),
        "replay": profile_config["replay"],
        "replay_sha256": profile_config["replay_sha256"],
        "prior_profile_config": str(PROFILE_CONFIG_PATH.resolve()),
        "prior_profile_config_sha256": file_sha256(PROFILE_CONFIG_PATH),
        "prior_profile_sample_sha256": profile_config["sample_sha256"],
        "prompt_version": profile_config["prompt_version"],
        "system_prompt": profile_config["system_prompt"],
        "profile_generation": profile_config["profile_generation"],
        "chunk_target_tokens": profile_config["chunk_target_tokens"],
        "call_count": CALL_COUNT,
        "chunk_plan_sha256": hashlib.sha256(canonical_bytes(chunks)).hexdigest(),
        "timeout_seconds": TIMEOUT_SECONDS,
        "no_retries": True,
        "no_concurrency": True,
        "same_model_request_name": MODEL,
        "conditions": {
            "LM_STUDIO_CURRENT": {
                "endpoint": LMSTUDIO_ENDPOINT,
                "observability_endpoint": LMSTUDIO_OBSERVABILITY,
                "model": MODEL,
                "instance_identifier": "qwen3.5-4b@q6_k",
                "backend": "LM Studio OpenAI-compatible current path",
            },
            "LLAMA_CPP_EXPLICIT": {
                "endpoint": LLAMA_ENDPOINT,
                "observability_endpoint": LLAMA_OBSERVABILITY,
                "model": MODEL,
                "instance_identifier": "llama-server-qwen3.5-4b-q6_k",
                "backend": "llama-server explicit serial slot/prompt-cache path",
                "launch_flags": [
                    "--ctx-size 32768",
                    "--parallel 1",
                    "--no-cont-batching",
                    "--flash-attn on",
                    "--n-gpu-layers all",
                    "--cache-prompt",
                    "--cache-reuse 256",
                    "--ctx-checkpoints 148",
                    "--cache-ram 8192",
                    "--slot-prompt-similarity 0.1",
                    "--slots",
                    "--metrics",
                ],
            },
        },
        "llama_server_binary": "C:/Users/disti/.lmstudio/extensions/backends/llama.cpp-win-x86_64-vulkan-avx2-2.28.2/llama-server.exe",
        "model_path": "C:/Users/disti/.lmstudio/models/lmstudio-community/Qwen3.5-4B-GGUF/Qwen3.5-4B-Q6_K.gguf",
        "historical_reference": {
            "selector_ttft_p50_ms": 8416.3,
            "selector_wall_seconds": 1805.1,
        },
    }


def freeze() -> None:
    if PLAN_PATH.exists() or CONFIG_PATH.exists():
        raise RuntimeError(f"refusing to overwrite frozen plan under {OUT_ROOT}")
    plan = plan_without_hash()
    plan["plan_sha256"] = hashlib.sha256(canonical_bytes(plan)).hexdigest()
    write_json(PLAN_PATH, plan)
    write_json(
        CONFIG_PATH,
        {
            "schema_version": "panoref-selector-llama-server-ab-config-v1",
            "plan_path": str(PLAN_PATH.resolve()),
            "plan_sha256": plan["plan_sha256"],
            "source_commit": plan["source_commit"],
            "call_count": CALL_COUNT,
            "condition_order": ["LM_STUDIO_CURRENT", "LLAMA_CPP_EXPLICIT"],
            "no_retries": True,
            "no_concurrency": True,
        },
    )
    print(f"plan: {PLAN_PATH}")
    print(f"config: {CONFIG_PATH}")
    print(f"plan_sha256: {plan['plan_sha256']}")


def verify_plan(plan: dict[str, Any]) -> None:
    recorded = plan.get("plan_sha256")
    without = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if recorded != hashlib.sha256(canonical_bytes(without)).hexdigest():
        raise RuntimeError("frozen plan hash mismatch")
    if git_commit() != plan["source_commit"]:
        raise RuntimeError("benchmark source commit changed after freeze")
    if file_sha256(Path(plan["replay"])) != plan["replay_sha256"]:
        raise RuntimeError("frozen replay changed")
    if file_sha256(PROFILE_CONFIG_PATH) != plan["prior_profile_config_sha256"]:
        raise RuntimeError("prior selector profile config changed")


def _parse_metrics(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or " " not in line:
            continue
        name, value = line.rsplit(" ", 1)
        if not any(term in name.lower() for term in ("prompt", "cache", "slot", "token", "eval")):
            continue
        try:
            result[name] = float(value)
        except ValueError:
            result[name] = value
    return result


async def observability(client: httpx.AsyncClient, base_url: str) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for name, path in (("health", "/health"), ("slots", "/slots"), ("metrics", "/metrics")):
        try:
            response = await client.get(base_url + path, timeout=10.0)
            text = response.text
            if name == "metrics":
                output[name] = {
                    "status": response.status_code,
                    "parsed": _parse_metrics(text),
                    "raw": text[:20000],
                }
            else:
                try:
                    value: Any = response.json()
                except ValueError:
                    value = text[:10000]
                output[name] = {"status": response.status_code, "value": value}
        except Exception as exc:
            output[name] = {"error": f"{type(exc).__name__}: {exc}"}
    return output


def rows_for(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


async def run_condition(plan: dict[str, Any], condition: str, run_dir: Path) -> None:
    verify_plan(plan)
    if run_dir.exists():
        raise RuntimeError(f"refusing to overwrite existing run directory {run_dir}")
    condition_data = plan["conditions"][condition]
    run_dir.mkdir(parents=True)
    lookup = build_chunk_lookup(*load_profile_inputs()[::2])
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        before = await observability(client, condition_data["observability_endpoint"])
        write_json(
            run_dir / "RUN_META.json",
            {
                "schema_version": "panoref-selector-llama-server-ab-run-v1",
                "started_at": utc_now(),
                "condition": condition,
                "source_commit": plan["source_commit"],
                "plan_sha256": plan["plan_sha256"],
                "backend_before": before,
                "condition_data": condition_data,
                "no_retries": True,
                "no_concurrency": True,
            },
        )
        telemetry_path = run_dir / "telemetry.jsonl"
        started = time.perf_counter()
        with telemetry_path.open("x", encoding="utf-8") as handle:
            for call_index in range(CALL_COUNT):
                run_plan = dict(plan)
                run_plan["endpoint"] = condition_data["endpoint"]
                row = await call_selector(
                    client,
                    plan=run_plan,
                    chunk=lookup[call_index],
                    condition=condition,
                    call_index=call_index,
                )
                row["backend_observability_after_call"] = await observability(
                    client, condition_data["observability_endpoint"]
                )
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                metric = row["metrics"]
                print(
                    f"{condition} {call_index + 1:02d}/{CALL_COUNT} "
                    f"status={row['status']} ttft_ms={metric['ttft_ms']} "
                    f"wall_ms={metric['total_wall_ms']:.1f}",
                    flush=True,
                )
        after = await observability(client, condition_data["observability_endpoint"])
    write_json(
        run_dir / "RUN_END.json",
        {
            "ended_at": utc_now(),
            "elapsed_wall_seconds": time.perf_counter() - started,
            "backend_after": after,
            "full_trace_rows": len(rows_for(telemetry_path)),
        },
    )


def cache_signal_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    slot_rows: list[Any] = []
    metric_rows: list[dict[str, Any]] = []
    for row in rows:
        obs = row.get("backend_observability_after_call") or {}
        slot_rows.append((obs.get("slots") or {}).get("value"))
        metric_rows.append((obs.get("metrics") or {}).get("parsed") or {})
    keys = sorted({key for item in metric_rows for key in item})
    return {
        "observability_rows": len(rows),
        "slots_statuses": sorted(
            {
                ((row.get("backend_observability_after_call") or {}).get("slots") or {}).get("status")
                for row in rows
            }
        ),
        "metrics_key_names": keys,
        "slot_samples": slot_rows,
        "metrics_samples": metric_rows,
    }


def summarize(plan: dict[str, Any]) -> None:
    verify_plan(plan)
    results: dict[str, Any] = {}
    for condition in plan["conditions"]:
        rows = rows_for(RUN_ROOT / condition.lower() / "telemetry.jsonl")
        results[condition] = {
            "metrics": metrics(rows),
            "cache_signals": cache_signal_summary(rows),
            "rows": len(rows),
        }
    left = rows_for(RUN_ROOT / "lm_studio_current" / "telemetry.jsonl")
    right = rows_for(RUN_ROOT / "llama_cpp_explicit" / "telemetry.jsonl")
    paired = []
    for a, b in zip(left, right):
        paired.append(
            {
                "call_index": a.get("call_index"),
                "request_hash_identical": a.get("request_hash") == b.get("request_hash"),
                "semantic_request_hash_identical": a.get("semantic_request_hash") == b.get("semantic_request_hash"),
                "decision_identical": a.get("decision_hash") == b.get("decision_hash"),
                "a_decision_hash": a.get("decision_hash"),
                "b_decision_hash": b.get("decision_hash"),
            }
        )
    decision_match = sum(item["decision_identical"] for item in paired)
    summary = {
        "schema_version": "panoref-selector-llama-server-ab-summary-v1",
        "completed_at": utc_now(),
        "plan_sha256": plan["plan_sha256"],
        "results": results,
        "decision_equivalence": {
            "planned": CALL_COUNT,
            "compared": len(paired),
            "identical": decision_match,
            "match_fraction": decision_match / CALL_COUNT if CALL_COUNT else None,
            "divergences": [item for item in paired if not item["decision_identical"]],
            "raw_request_hash_matches": sum(item["request_hash_identical"] for item in paired),
            "semantic_request_hash_matches": sum(item["semantic_request_hash_identical"] for item in paired),
        },
        "paired": paired,
    }
    write_json(OUT_ROOT / "SUMMARY.json", summary)
    write_report(plan, summary)
    print(f"summary: {OUT_ROOT / 'SUMMARY.json'}")
    print(f"report: {OUT_ROOT / 'REPORT.md'}")


def write_report(plan: dict[str, Any], summary: dict[str, Any]) -> None:
    eq = summary["decision_equivalence"]
    lines = [
        "# PanoRef llama-server backend A/B",
        "",
        "Frozen 24-call serial selector comparison using the exact first 24 PanoRef 1.2K requests.",
        "The request body, model name, prompt, dynamic exact-ID schema, and decoding settings were held fixed; only the serving endpoint changed.",
        "",
        "| Condition | Calls | TTFT p50 | TTFT p95 | Decode p50 | Total wall p50 | Decision match | Errors |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for condition, data in summary["results"].items():
        m = data["metrics"]
        lines.append(
            f"| {condition} | {m['calls']} | {m['ttft_ms']['p50']:.1f} ms | "
            f"{m['ttft_ms']['p95']:.1f} ms | {m['decode_ms_client_observed']['p50']:.1f} ms | "
            f"{m['total_wall_ms']['p50']:.1f} ms | {eq['identical']}/{eq['planned']} | {m['errors']} |"
        )
    lines += [
        "",
        f"Decision equivalence: {eq['identical']}/{eq['planned']} identical; divergences: {len(eq['divergences'])}.",
        f"Raw request hashes identical: {eq['raw_request_hash_matches']}/{eq['compared']}; semantic request hashes identical: {eq['semantic_request_hash_matches']}/{eq['compared']}.",
        "",
        "## llama.cpp controls",
        "",
        "The explicit arm used one server slot, no continuous batching, prompt caching, cache reuse threshold 256, 148 context checkpoints, 8192 MiB cache RAM, slot prompt similarity 0.1, /slots, and /metrics.",
        "",
        "The raw /slots and /metrics samples are preserved in each telemetry row. LM Studio endpoint probes are preserved too; unsupported endpoints are recorded as observations, not treated as cache evidence.",
        "",
        "## Frozen identity",
        "",
        f"- Source commit: `{plan['source_commit']}`",
        f"- Plan SHA-256: `{plan['plan_sha256']}`",
        f"- Replay SHA-256: `{plan['replay_sha256']}`",
        f"- Chunk plan SHA-256: `{plan['chunk_plan_sha256']}`",
        f"- Profile config SHA-256: `{plan['prior_profile_config_sha256']}`",
        "",
        "No production ORCHID changes, selector semantic changes, chunk-size changes, concurrency, or Solar calls were made.",
        "",
    ]
    (OUT_ROOT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--run", choices=["LM_STUDIO_CURRENT", "LLAMA_CPP_EXPLICIT"])
    parser.add_argument("--summarize", action="store_true")
    args = parser.parse_args()
    if args.freeze:
        freeze()
        return
    plan = read_json(PLAN_PATH)
    if args.run:
        asyncio.run(run_condition(plan, args.run, RUN_ROOT / args.run.lower()))
        return
    if args.summarize:
        summarize(plan)
        return
    parser.error("choose --freeze, --run, or --summarize")


if __name__ == "__main__":
    main()
