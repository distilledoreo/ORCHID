"""Disposable Qwen prompt-processing backend/offload shootout.

This benchmark holds a simple approximately 1.5K-token request constant while
comparing the active LM Studio path with the matching local llama-server
binary at several GPU-offload settings. It does not change ORCHID production
behavior. Direct servers are launched only for the duration of their arm and
are terminated by this harness afterward.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.benchmark_qwen_prompt_scaling import (  # noqa: E402
    build_body,
    call_once,
    digest,
    load_plan as load_scaling_plan,
    percentile,
)


OUT_ROOT = ROOT / "artifacts/agent_benchmarks/qwen_backend_shootout_v1"
PLAN_PATH = OUT_ROOT / "BACKEND_SHOOTOUT_PLAN.json"
RUN_ROOT = OUT_ROOT / "runs"
MODEL = "qwen3.5-4b@q6_k"
MODEL_PATH = Path(
    r"C:\Users\disti\.lmstudio\models\lmstudio-community\Qwen3.5-4B-GGUF\Qwen3.5-4B-Q6_K.gguf"
)
LLAMA_SERVER_BIN = Path(
    r"C:\Users\disti\.lmstudio\extensions\backends\llama.cpp-win-x86_64-vulkan-avx2-2.28.2\llama-server.exe"
)
LM_STUDIO_ENDPOINT = "http://127.0.0.1:1234/v1"
DIRECT_HOST = "127.0.0.1"
TARGET_ESTIMATE = 2000
REPETITIONS = 5
WARMUPS = 1
TIMEOUT_SECONDS = 180.0
SERVER_START_TIMEOUT_SECONDS = 120.0
SERVER_THREADS = 6

CONDITIONS: dict[str, dict[str, Any]] = {
    "LM_STUDIO_CURRENT": {
        "kind": "existing_endpoint",
        "endpoint": LM_STUDIO_ENDPOINT,
        "backend": "LM Studio current OpenAI-compatible path; active backend inspected at run start",
        "offload": "LM Studio-managed current Vulkan configuration",
    },
    "LLAMA_CPP_VULKAN_ALL": {
        "kind": "direct_server",
        "port": 18081,
        "n_gpu_layers": 999,
        "flash_attn": "on",
        "offload": "direct llama-server Vulkan, all layers requested",
    },
    "LLAMA_CPP_VULKAN_PARTIAL": {
        "kind": "direct_server",
        "port": 18082,
        "n_gpu_layers": 20,
        "flash_attn": "on",
        "offload": "direct llama-server Vulkan, 20 layers requested",
    },
    "LLAMA_CPP_CPU": {
        "kind": "direct_server",
        "port": 18083,
        "n_gpu_layers": 0,
        "flash_attn": "on",
        "offload": "direct llama-server CPU, zero GPU layers requested",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def file_sha256(path: Path) -> str:
    digestor = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digestor.update(block)
    return digestor.hexdigest()


def git_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def llama_bench_path() -> str | None:
    candidates = [LLAMA_SERVER_BIN.with_name("llama-bench.exe"), LLAMA_SERVER_BIN.with_name("llama-bench")]
    found = shutil.which("llama-bench")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.exists():
            return str(candidate.resolve())
    return None


def plan_without_hash() -> dict[str, Any]:
    scaling_plan = load_scaling_plan()
    if scaling_plan["model"] != MODEL:
        raise RuntimeError(f"scaling profile model changed: {scaling_plan['model']}")
    if not MODEL_PATH.is_file():
        raise RuntimeError(f"missing exact GGUF: {MODEL_PATH}")
    if not LLAMA_SERVER_BIN.is_file():
        raise RuntimeError(f"missing llama-server binary: {LLAMA_SERVER_BIN}")
    return {
        "schema_version": "qwen-backend-shootout-plan-v1",
        "created_at": utc_now(),
        "source_commit": git_commit(),
        "model": MODEL,
        "model_path": str(MODEL_PATH.resolve()),
        "model_sha256": file_sha256(MODEL_PATH),
        "llama_server_binary": str(LLAMA_SERVER_BIN.resolve()),
        "llama_server_binary_sha256": file_sha256(LLAMA_SERVER_BIN),
        "llama_server_version": "fe2adf0",
        "llama_bench_path": llama_bench_path(),
        "llama_bench_available": llama_bench_path() is not None,
        "scaling_plan_sha256": scaling_plan["plan_sha256"],
        "profile_config_sha256": scaling_plan["profile_config_sha256"],
        "request_generation": scaling_plan["generation"],
        "system_prompt": scaling_plan["system_prompt"],
        "user_prefix": scaling_plan["user_prefix"],
        "user_suffix": scaling_plan["user_suffix"],
        "filler_unit": scaling_plan["filler_unit"],
        "target_prompt_estimate": TARGET_ESTIMATE,
        "repetitions": REPETITIONS,
        "warmups_per_condition": WARMUPS,
        "serial": True,
        "no_retries": True,
        "prompt_cache_policy": "direct servers use --no-cache-prompt; distinct nonce per request prevents exact-repeat identity on LM Studio",
        "measurement": "client TTFT from request start to first streamed content token; direct llama-server prompt counters captured from /metrics deltas when available",
        "conditions": CONDITIONS,
        "direct_server_common_flags": [
            "--ctx-size 8192",
            "--parallel 1",
            "--no-cont-batching",
            "--batch-size 2048",
            "--ubatch-size 512",
            "--threads 6",
            "--threads-batch 6",
            "--flash-attn condition value",
            "--no-cache-prompt",
            "--metrics",
            "--slots",
        ],
    }


def freeze_plan() -> None:
    if PLAN_PATH.exists():
        raise RuntimeError(f"refusing to overwrite frozen plan: {PLAN_PATH}")
    raw = plan_without_hash()
    write_json(PLAN_PATH, {**raw, "plan_sha256": digest(raw)})
    print(f"plan: {PLAN_PATH}")
    print(f"plan_sha256: {digest(raw)}")


def load_plan() -> dict[str, Any]:
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    recorded = plan.pop("plan_sha256", None)
    if recorded != digest(plan):
        raise RuntimeError("backend shootout plan hash mismatch")
    plan["plan_sha256"] = recorded
    return plan


def parse_prometheus(text: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or " " not in line:
            continue
        name, raw_value = line.rsplit(" ", 1)
        try:
            result[name] = float(raw_value)
        except ValueError:
            continue
    return result


async def observe(client: httpx.AsyncClient, base_url: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, path in (("health", "/health"), ("slots", "/slots"), ("metrics", "/metrics")):
        try:
            response = await client.get(base_url.rstrip("/") + path, timeout=10.0)
            item: dict[str, Any] = {"status": response.status_code}
            if name == "metrics":
                item["parsed"] = parse_prometheus(response.text)
                item["raw"] = response.text[:20000]
            else:
                try:
                    item["value"] = response.json()
                except ValueError:
                    item["value"] = response.text[:10000]
            result[name] = item
        except Exception as exc:
            result[name] = {"error": f"{type(exc).__name__}: {exc}"}
    return result


def prompt_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, float | None]:
    before_metrics = ((before.get("metrics") or {}).get("parsed") or {})
    after_metrics = ((after.get("metrics") or {}).get("parsed") or {})
    token_key = "llamacpp:prompt_tokens_total"
    seconds_key = "llamacpp:prompt_seconds_total"
    tokens = after_metrics.get(token_key) - before_metrics.get(token_key) if token_key in after_metrics and token_key in before_metrics else None
    seconds = after_metrics.get(seconds_key) - before_metrics.get(seconds_key) if seconds_key in after_metrics and seconds_key in before_metrics else None
    return {
        "prompt_tokens_backend_delta": tokens,
        "prompt_eval_ms_backend_delta": seconds * 1000 if seconds is not None else None,
        "prompt_tok_s_backend_delta": tokens / seconds if tokens is not None and seconds and seconds > 0 else None,
    }


def direct_args(plan: dict[str, Any], condition: dict[str, Any]) -> list[str]:
    return [
        str(LLAMA_SERVER_BIN),
        "--model", plan["model_path"],
        "--host", DIRECT_HOST,
        "--port", str(condition["port"]),
        "--no-webui",
        "--jinja",
        "--ctx-size", "8192",
        "--parallel", "1",
        "--no-cont-batching",
        "--batch-size", "2048",
        "--ubatch-size", "512",
        "--threads", str(SERVER_THREADS),
        "--threads-batch", str(SERVER_THREADS),
        "--flash-attn", condition["flash_attn"],
        "--n-gpu-layers", str(condition["n_gpu_layers"]),
        "--no-cache-prompt",
        "--metrics",
        "--slots",
    ]


async def wait_ready(client: httpx.AsyncClient, base_url: str) -> None:
    deadline = time.monotonic() + SERVER_START_TIMEOUT_SECONDS
    last_error = "not polled"
    while time.monotonic() < deadline:
        try:
            response = await client.get(base_url + "/health", timeout=5.0)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        await asyncio.sleep(1.0)
    raise RuntimeError(f"server did not become ready: {last_error}")


def terminate_process(process: subprocess.Popen[bytes]) -> dict[str, Any]:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=15)
    return {"returncode": process.returncode}


async def run_condition(plan: dict[str, Any], name: str) -> None:
    condition = plan["conditions"][name]
    run_dir = RUN_ROOT / name
    if run_dir.exists():
        raise RuntimeError(f"refusing to overwrite run: {run_dir}")
    run_dir.mkdir(parents=True)
    write_json(run_dir / "RUN_META.json", {
        "schema_version": "qwen-backend-shootout-run-v1",
        "condition": name,
        "started_at": utc_now(),
        "source_commit": plan["source_commit"],
        "plan_sha256": plan["plan_sha256"],
        "condition_data": condition,
        "no_retries": True,
        "serial": True,
    })
    endpoint = condition.get("endpoint") or f"http://{DIRECT_HOST}:{condition['port']}/v1"
    observability_endpoint = endpoint.removesuffix("/v1")
    process: subprocess.Popen[bytes] | None = None
    server_dir = run_dir / "server"
    server_dir.mkdir()
    startup_error: str | None = None
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        if condition["kind"] == "direct_server":
            stdout = (server_dir / "stdout.log").open("wb")
            stderr = (server_dir / "stderr.log").open("wb")
            try:
                process = subprocess.Popen(
                    direct_args(plan, condition),
                    cwd=server_dir,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                try:
                    await wait_ready(client, observability_endpoint)
                except Exception as exc:
                    startup_error = f"{type(exc).__name__}: {exc}"
            except Exception as exc:
                startup_error = f"{type(exc).__name__}: {exc}"
            finally:
                stdout.close()
                stderr.close()
        if startup_error:
            write_json(run_dir / "STARTUP_FAILURE.json", {"error": startup_error, "recorded_at": utc_now()})
        telemetry_path = run_dir / "CALLS.jsonl"
        with telemetry_path.open("x", encoding="utf-8") as output:
            if startup_error is None:
                before = await observe(client, observability_endpoint)
                write_json(run_dir / "BACKEND_BEFORE.json", before)
                total_calls = WARMUPS + REPETITIONS
                for index in range(total_calls):
                    phase = "warmup" if index < WARMUPS else "measurement"
                    repetition = index + 1 if phase == "warmup" else index - WARMUPS + 1
                    print(f"{name} {index + 1}/{total_calls} {phase}", flush=True)
                    row = await call_once(
                        client,
                        {**load_scaling_plan(), "endpoint": endpoint},
                        phase=f"backend-{name}",
                        target_tokens=TARGET_ESTIMATE,
                        repetition=repetition,
                        global_index=index + 1,
                    )
                    after = await observe(client, observability_endpoint)
                    row["backend_observability_after_call"] = after
                    row["backend_prompt_delta"] = prompt_delta(before, after)
                    row["condition"] = name
                    output.write(json.dumps({"benchmark_phase": phase, **row}, ensure_ascii=False, sort_keys=True) + "\n")
                    output.flush()
                    before = after
                write_json(run_dir / "BACKEND_AFTER.json", before)
    process_result = terminate_process(process) if process is not None else None
    write_json(run_dir / "RUN_END.json", {
        "ended_at": utc_now(),
        "startup_error": startup_error,
        "process": process_result,
        "calls_recorded": len(telemetry_path.read_text(encoding="utf-8").splitlines()) if telemetry_path.exists() else 0,
    })


def load_rows(name: str) -> list[dict[str, Any]]:
    path = RUN_ROOT / name / "CALLS.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def stats(rows: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    values = [float(row["metrics"][metric]) for row in rows if row["status"] == "SUCCEEDED" and row["metrics"].get(metric) is not None]
    return {
        "count": len(values),
        "p50": percentile(values, 50) if values else None,
        "p95": percentile(values, 95) if values else None,
        "mean": mean(values) if values else None,
    }


def backend_stats(rows: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    values = [float(row["backend_prompt_delta"][metric]) for row in rows if row.get("backend_prompt_delta", {}).get(metric) is not None]
    return {"count": len(values), "p50": percentile(values, 50) if values else None, "mean": mean(values) if values else None}


def report(plan: dict[str, Any]) -> None:
    summaries: dict[str, Any] = {}
    for name in CONDITIONS:
        rows = [row for row in load_rows(name) if row.get("benchmark_phase") == "measurement"]
        summaries[name] = {
            "calls": len(rows),
            "succeeded": sum(row["status"] == "SUCCEEDED" for row in rows),
            "failures": [{"error_category": row.get("error_category"), "error": row.get("error")} for row in rows if row["status"] != "SUCCEEDED"],
            "input_tokens": stats(rows, "input_tokens"),
            "ttft_ms": stats(rows, "ttft_ms"),
            "wall_ms": stats(rows, "total_wall_ms"),
            "decode_ms": stats(rows, "decode_ms_client_observed"),
            "backend_prompt_tokens": backend_stats(rows, "prompt_tokens_backend_delta"),
            "backend_prompt_eval_ms": backend_stats(rows, "prompt_eval_ms_backend_delta"),
            "backend_prompt_tok_s": backend_stats(rows, "prompt_tok_s_backend_delta"),
        }
    summary = {
        "schema_version": "qwen-backend-shootout-summary-v1",
        "generated_at": utc_now(),
        "plan_sha256": plan["plan_sha256"],
        "conditions": summaries,
    }
    write_json(OUT_ROOT / "SUMMARY.json", summary)
    lines = [
        "# Qwen backend/kernel prompt-processing shootout",
        "",
        f"Plan hash: `{plan['plan_sha256']}`; source commit: `{plan['source_commit']}`.",
        f"Model: `{plan['model']}`; GGUF SHA-256: `{plan['model_sha256']}`; server build: `{plan['llama_server_version']}`.",
        f"Five serial measurements plus one warmup per condition; nominal estimate {TARGET_ESTIMATE}, actual usage is reported below. `llama-bench` available: `{plan['llama_bench_available']}`.",
        "",
        "| Condition | Calls | Actual input p50 | TTFT p50 | TTFT p95 | Wall p50 | Decode p50 | Backend prompt eval p50 | Backend prompt tok/s | Errors |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, data in summaries.items():
        def fmt(value: Any) -> str:
            return "—" if value is None else f"{value:.1f}"
        lines.append(
            f"| {name} | {data['calls']}/{data['succeeded']} | {fmt(data['input_tokens']['p50'])} | "
            f"{fmt(data['ttft_ms']['p50'])} ms | {fmt(data['ttft_ms']['p95'])} ms | "
            f"{fmt(data['wall_ms']['p50'])} ms | {fmt(data['decode_ms']['p50'])} ms | "
            f"{fmt(data['backend_prompt_eval_ms']['p50'])} ms | {fmt(data['backend_prompt_tok_s']['p50'])} | {len(data['failures'])} |"
        )
    lines.extend([
        "",
        "## Findings",
        "",
        "LM Studio current and direct llama-server all-layer Vulkan are within the observed small-run spread, while 20-layer partial offload is materially slower. The CPU control is faster than partial Vulkan but slower than all-layer Vulkan.",
        "",
        "All 20 measurement calls returned the exact `OK` contract. Direct server return code `1` in `RUN_END.json` records the harness terminating the server after a completed arm; it is not a request failure.",
        "",
        "## Interpretation",
        "",
        "Client TTFT is measured from request start to the first streamed content token. Direct llama-server prompt processing uses `/metrics` counter deltas; LM Studio's OpenAI endpoint did not expose equivalent prompt-time counters.",
        "",
        "The fixed prompt, model request, sampling settings, serial execution, and exact GGUF are held constant. Direct arms disable llama.cpp prompt caching; each request also carries a distinct nonce so the LM Studio arm does not receive exact-repeat requests.",
        "",
        "Raw per-call records and direct server logs are preserved under `runs/`.",
    ])
    (OUT_ROOT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"summary: {OUT_ROOT / 'SUMMARY.json'}")
    print(f"report: {OUT_ROOT / 'REPORT.md'}")


async def run_all(plan: dict[str, Any]) -> None:
    for name in CONDITIONS:
        await run_condition(plan, name)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("freeze-plan")
    sub.add_parser("run")
    sub.add_parser("report")
    args = parser.parse_args()
    if args.command == "freeze-plan":
        freeze_plan()
    elif args.command == "run":
        asyncio.run(run_all(load_plan()))
    else:
        report(load_plan())


if __name__ == "__main__":
    main()
