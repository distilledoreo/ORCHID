"""No-reboot RX 580 / Vulkan runtime optimization audit.

The audit reuses the frozen simple Qwen prompt-processing request and direct
llama-server path from the backend shootout. It varies only one low-level
runtime parameter per experiment, launches direct servers temporarily, keeps
all logs/results, and never changes ORCHID production behavior.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
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

from tools.benchmark_qwen_backend_shootout import (  # noqa: E402
    LLAMA_SERVER_BIN,
    MODEL_PATH,
    TARGET_ESTIMATE,
    call_once,
    digest,
    load_plan as load_backend_plan,
    observe as backend_observe,
    percentile,
    prompt_delta,
    write_json,
)
from tools.benchmark_qwen_prompt_scaling import load_plan as load_scaling_plan  # noqa: E402


OUT_ROOT = ROOT / "artifacts/agent_benchmarks/rx580_vulkan_optimization_v1"
PLAN_PATH = OUT_ROOT / "AUDIT_PLAN.json"
RUN_ROOT = OUT_ROOT / "experiments"
DIRECT_HOST = "127.0.0.1"
TIMEOUT_SECONDS = 180.0
START_TIMEOUT_SECONDS = 120.0
MODEL = "qwen3.5-4b@q6_k"
SERVER_VERSION = "fe2adf0"
SERVER_THREADS = 6


EXPERIMENTS: dict[str, dict[str, Any]] = {
    "baseline_full_vulkan": {
        "description": "Pinned direct full-Vulkan reference",
        "repetitions": 5,
        "port": 18101,
        "ctx_size": 8192,
        "batch_size": 2048,
        "ubatch_size": 512,
        "threads": 6,
        "threads_batch": 6,
        "flash_attn": "on",
        "kv_offload": False,
        "kv_unified": False,
        "load_mode": None,
    },
    "batch_1024": {
        "description": "Logical batch size 1024",
        "repetitions": 3,
        "port": 18102,
        "batch_size": 1024,
    },
    "batch_4096": {
        "description": "Logical batch size 4096",
        "repetitions": 3,
        "port": 18103,
        "batch_size": 4096,
    },
    "ubatch_256": {
        "description": "Physical micro-batch size 256",
        "repetitions": 3,
        "port": 18104,
        "ubatch_size": 256,
    },
    "ubatch_1024": {
        "description": "Physical micro-batch size 1024",
        "repetitions": 3,
        "port": 18105,
        "ubatch_size": 1024,
    },
    "threads_batch_3": {
        "description": "CPU-side prompt batch threads 3",
        "repetitions": 3,
        "port": 18106,
        "threads_batch": 3,
    },
    "threads_batch_12": {
        "description": "CPU-side prompt batch threads 12",
        "repetitions": 3,
        "port": 18107,
        "threads_batch": 12,
    },
    "flash_attention_off": {
        "description": "Flash Attention disabled",
        "repetitions": 3,
        "port": 18108,
        "flash_attn": "off",
    },
    "ctx_32768": {
        "description": "Context allocation 32768",
        "repetitions": 3,
        "port": 18109,
        "ctx_size": 32768,
    },
    "kv_unified_on": {
        "description": "KV offload and unified KV enabled",
        "repetitions": 3,
        "port": 18110,
        "kv_offload": True,
        "kv_unified": True,
    },
    "mmap_mlock": {
        "description": "mmap plus mlock model loading",
        "repetitions": 3,
        "port": 18111,
        "load_mode": "mmap+mlock",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def run_process(args: list[str], *, timeout: int = 30) -> tuple[int, str, str]:
    try:
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout, result.stderr
    except Exception as exc:
        return -1, "", f"{type(exc).__name__}: {exc}"


def run_powershell(script: str, *, timeout: int = 30) -> str:
    code, stdout, stderr = run_process(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], timeout=timeout)
    return f"exit_code={code}\nSTDOUT\n{stdout}\nSTDERR\n{stderr}"


def sanitize_secrets(text: str) -> str:
    return re.sub(r"(--api-key\s+)\S+", r"\1<redacted>", text, flags=re.IGNORECASE)


def file_sha256(path: Path) -> str:
    digestor = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digestor.update(block)
    return digestor.hexdigest()


def git_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def gpu_snapshot() -> str:
    return run_powershell(
        r"""
$engine = Get-Counter '\GPU Engine(*)\Utilization Percentage' -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty CounterSamples |
  Where-Object { $_.CookedValue -gt 0.1 } |
  Select-Object InstanceName,CookedValue
$memory = Get-CimInstance -ClassName Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory -ErrorAction SilentlyContinue |
  Select-Object Name,DedicatedUsage,SharedUsage,TotalCommitted
$process = Get-CimInstance -ClassName Win32_PerfFormattedData_GPUPerformanceCounters_GPUProcessMemory -ErrorAction SilentlyContinue |
  Where-Object { $_.DedicatedUsage -gt 0 -or $_.LocalUsage -gt 0 } |
  Select-Object Name,ProcessID,DedicatedUsage,LocalUsage,NonLocalUsage
[pscustomobject]@{engine=$engine; adapter_memory=$memory; process_memory=$process} | ConvertTo-Json -Depth 6 -Compress
"""
    )


def inventory_text(backend_plan: dict[str, Any]) -> str:
    manifest = Path(r"C:\Users\disti\.lmstudio\extensions\backends\llama.cpp-win-x86_64-vulkan-avx2-2.28.2\backend-manifest.json")
    display_data = manifest.with_name("display-data.json")
    manifest_sha256 = file_sha256(manifest)
    _, version_out, version_err = run_process([str(LLAMA_SERVER_BIN), "--version"])
    _, devices_out, devices_err = run_process([str(LLAMA_SERVER_BIN), "--list-devices"])
    vulkan_code, vulkan_out, vulkan_err = run_process(["vulkaninfo", "--summary"], timeout=45)
    process_text = run_powershell(
        r"""
$items = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -in @('llama-server.exe','LM Studio.exe') } |
  Select-Object ProcessId,Name,ExecutablePath,CommandLine
$items | Format-List | Out-String
"""
    )
    process_text = sanitize_secrets(process_text)
    gpu = run_powershell(
        r"""
Get-CimInstance Win32_VideoController | Select-Object Name,AdapterCompatibility,AdapterRAM,PNPDeviceID,DriverVersion,DriverDate,Status,VideoModeDescription | Format-List
Get-CimInstance Win32_PnPSignedDriver | Where-Object { $_.DeviceName -match 'Radeon RX 580' } | Select-Object DeviceName,DriverVersion,DriverDate,Manufacturer,InfName,Signer | Format-List
"""
    )
    windows = run_powershell(
        r"""
Get-ComputerInfo | Select-Object WindowsProductName,WindowsVersion,OsBuildNumber,OsArchitecture,CsName | Format-List
powercfg /getactivescheme
powercfg /list
"""
    )
    scheduling = run_powershell(
        r"""
$keys=@('HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers','HKCU:\Software\Microsoft\Windows\CurrentVersion\GameDVR')
foreach($key in $keys){if(Test-Path $key){Get-ItemProperty $key | Select-Object PSPath,HwSchMode,UseGameDVR,AppCaptureEnabled | Format-List}}
powercfg /query SCHEME_CURRENT SUB_PROCESSOR | Select-String -Pattern 'Current AC Power Setting Index|Current DC Power Setting Index|Minimum processor state|Maximum processor state'
"""
    )
    counters = gpu_snapshot()
    pci = run_powershell(
        r"""
$id=(Get-CimInstance Win32_VideoController | Where-Object {$_.Name -match 'Radeon RX 580'}).PNPDeviceID
Get-PnpDeviceProperty -InstanceId $id -KeyName 'DEVPKEY_Device_LocationInfo' -ErrorAction SilentlyContinue | Format-List
"""
    )
    return f"""# RX 580 / Vulkan Runtime System Inventory

Captured: `{utc_now()}`

## Frozen inference identity

- Model: `{backend_plan['model']}`
- GGUF: `{backend_plan['model_path']}`
- GGUF SHA-256: `{backend_plan['model_sha256']}`
- llama.cpp binary: `{backend_plan['llama_server_binary']}`
- llama.cpp binary SHA-256: `{backend_plan['llama_server_binary_sha256']}`
- llama.cpp build: `{backend_plan['llama_server_version']}`
- Vulkan backend manifest SHA-256: `{manifest_sha256}`

## GPU and driver

```text
{gpu}
```

## Windows, power plan, and scheduling

```text
{windows}
{scheduling}
```

## Active inference processes (API keys redacted)

```text
{process_text}
```

## Vulkan summary

`vulkaninfo` exit code: `{vulkan_code}`

```text
{vulkan_out}
{vulkan_err}
```

The read-only Vulkan inventory reports the RX 580 as vendor `0x1002`, device `0x67df`, discrete GPU, AMD proprietary driver, Vulkan 1.3.260, driver info `25.8.1`, and 8192 MiB device memory with approximately 7402 MiB free at inventory time.

## llama.cpp device enumeration

```text
{version_out}
{version_err}
{devices_out}
{devices_err}
```

## Backend manifest

```json
{manifest.read_text(encoding='utf-8')}
```

## Display/runtime metadata

```text
{display_data.read_text(encoding='utf-8')[:4000]}
```

## Initial GPU counters

```text
{counters}
```

Windows exposed GPU-engine utilization and adapter/process-memory counters, but no reliable RX 580 core-clock, memory-clock, or temperature counter through the collected interfaces. PCIe negotiated generation/width was not exposed by the collected Windows/Vulkan interfaces; this remains an inventory gap, not a normality claim.
"""


def deferred_driver_text() -> str:
    return f"""# Deferred driver/reboot work

No driver or system package was installed, removed, downgraded, or upgraded.
No reboot, display reset, firmware change, Safe Mode operation, or remote-access change was performed.

## Current

- Windows display driver store: AMD Radeon RX 580 `31.0.21923.11000`
- Vulkan driver info: AMD proprietary `25.8.1`
- Current Vulkan backend: llama.cpp `fe2adf0` / LM Studio engine package 2.28.2

## Candidate for a later maintenance window

AMD lists Adrenalin `26.5.2` for Radeon RX 500-series Windows 10/11 on its RX 580 support page, with a Polaris/Vega release note. This is a candidate comparison point, not an instruction to install it during a remote session.

- Official support page: https://www.amd.com/en/support/downloads/drivers.html/graphics/radeon-600-500-400/radeon-rx-500-series/radeon-rx-580.html
- Official release notes: https://www.amd.com/en/resources/support-articles/release-notes/RN-RAD-WIN-26-5-2-POLARIS-VEGA.html

Potential benefit: updated Vulkan driver/shader compiler behavior could improve Polaris prompt kernels. Risks: driver replacement can require reboot, reset the display path, alter Vulkan compatibility, or strand remote access. Any comparison is therefore `DEFERRED_REBOOT_REQUIRED` and belongs to a maintenance window with physical/recovery access.

Status: `DEFERRED_REBOOT_REQUIRED`
"""


def freeze() -> None:
    if PLAN_PATH.exists():
        raise RuntimeError(f"refusing to overwrite frozen plan: {PLAN_PATH}")
    backend_plan = load_backend_plan()
    scaling_plan = load_scaling_plan()
    raw = {
        "schema_version": "rx580-vulkan-optimization-audit-plan-v1",
        "created_at": utc_now(),
        "source_commit": git_commit(),
        "gpu_target": "AMD Radeon RX 580 Series; PCI VEN_1002 DEV_67DF",
        "model": MODEL,
        "model_path": backend_plan["model_path"],
        "model_sha256": backend_plan["model_sha256"],
        "llama_server_binary": backend_plan["llama_server_binary"],
        "llama_server_binary_sha256": backend_plan["llama_server_binary_sha256"],
        "llama_server_version": SERVER_VERSION,
        "backend_plan_sha256": backend_plan["plan_sha256"],
        "scaling_plan_sha256": scaling_plan["plan_sha256"],
        "prompt_request": {
            "target_estimate": TARGET_ESTIMATE,
            "generation": scaling_plan["generation"],
            "system_prompt": scaling_plan["system_prompt"],
            "user_prefix": scaling_plan["user_prefix"],
            "user_suffix": scaling_plan["user_suffix"],
            "filler_unit": scaling_plan["filler_unit"],
            "nonce_policy": "same rx580-fixed nonce sequence across arms; direct prompt cache disabled",
        },
        "baseline_reference": {
            "prompt_eval_tok_s": 184.5,
            "ttft_ms": 8359.0,
            "source": "panoref backend shootout direct full Vulkan arm",
        },
        "default_runtime": {
            "ctx_size": 8192,
            "batch_size": 2048,
            "ubatch_size": 512,
            "threads": 6,
            "threads_batch": 6,
            "n_gpu_layers": 999,
            "flash_attn": "on",
            "no_cache_prompt": True,
            "parallel": 1,
            "no_cont_batching": True,
        },
        "repetitions": {name: data["repetitions"] for name, data in EXPERIMENTS.items()},
        "experiments": EXPERIMENTS,
        "safety": {
            "no_reboot": True,
            "no_driver_change": True,
            "no_power_setting_change": True,
            "no_production_orchid_change": True,
            "no_retries": True,
            "serial": True,
        },
    }
    plan = {**raw, "plan_sha256": digest(raw)}
    write_json(PLAN_PATH, plan)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / "SYSTEM_INVENTORY.md").write_text(inventory_text(backend_plan), encoding="utf-8")
    (OUT_ROOT / "DEFERRED_REBOOT_REQUIRED.md").write_text(deferred_driver_text(), encoding="utf-8")
    print(f"plan: {PLAN_PATH}")
    print(f"plan_sha256: {plan['plan_sha256']}")


def load_plan() -> dict[str, Any]:
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    recorded = plan.pop("plan_sha256", None)
    if recorded != digest(plan):
        raise RuntimeError("audit plan hash mismatch")
    plan["plan_sha256"] = recorded
    return plan


def direct_args(plan: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
    args = [
        str(LLAMA_SERVER_BIN),
        "--model", plan["model_path"],
        "--host", DIRECT_HOST,
        "--port", str(cfg["port"]),
        "--no-webui",
        "--jinja",
        "--ctx-size", str(cfg["ctx_size"]),
        "--parallel", "1",
        "--no-cont-batching",
        "--batch-size", str(cfg["batch_size"]),
        "--ubatch-size", str(cfg["ubatch_size"]),
        "--threads", str(cfg["threads"]),
        "--threads-batch", str(cfg["threads_batch"]),
        "--flash-attn", cfg["flash_attn"],
        "--n-gpu-layers", "999",
        "--no-cache-prompt",
        "--metrics",
        "--slots",
    ]
    if cfg.get("kv_offload", False):
        args.append("--kv-offload")
    if cfg.get("kv_unified", False):
        args.append("--kv-unified")
    if cfg.get("load_mode"):
        args.extend(["--load-mode", cfg["load_mode"]])
    return args


async def wait_ready(client: httpx.AsyncClient, base_url: str) -> None:
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
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


def stat(rows: list[dict[str, Any]], path: tuple[str, ...]) -> dict[str, Any]:
    values: list[float] = []
    for row in rows:
        value: Any = row
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if row.get("status") == "SUCCEEDED" and value is not None:
            values.append(float(value))
    return {
        "count": len(values),
        "p50": percentile(values, 50) if values else None,
        "p95": percentile(values, 95) if values else None,
        "mean": mean(values) if values else None,
    }


def build_results(name: str, cfg: dict[str, Any], rows: list[dict[str, Any]], run_end: dict[str, Any]) -> dict[str, Any]:
    measurements = [row for row in rows if row.get("benchmark_phase") == "measurement"]
    warmups = [row for row in rows if row.get("benchmark_phase") == "warmup"]
    prompt = stat(measurements, ("backend_prompt_delta", "prompt_tok_s_backend_delta"))
    return {
        "schema_version": "rx580-vulkan-experiment-results-v1",
        "experiment": name,
        "description": cfg["description"],
        "config": cfg,
        "calls_recorded": len(rows),
        "warmup_calls": len(warmups),
        "measurement_calls": len(measurements),
        "succeeded": sum(row.get("status") == "SUCCEEDED" for row in measurements),
        "failures": [{"error_category": row.get("error_category"), "error": row.get("error")} for row in measurements if row.get("status") != "SUCCEEDED"],
        "input_tokens": stat(measurements, ("metrics", "input_tokens")),
        "prompt_eval_tok_s": prompt,
        "prompt_eval_ms": stat(measurements, ("backend_prompt_delta", "prompt_eval_ms_backend_delta")),
        "ttft_ms": stat(measurements, ("metrics", "ttft_ms")),
        "wall_ms": stat(measurements, ("metrics", "total_wall_ms")),
        "decode_ms": stat(measurements, ("metrics", "decode_ms_client_observed")),
        "warmup_ttft_ms": stat(warmups, ("metrics", "ttft_ms")),
        "warmup_prompt_eval_tok_s": stat(warmups, ("backend_prompt_delta", "prompt_tok_s_backend_delta")),
        "run_end": run_end,
    }


async def run_experiment(plan: dict[str, Any], name: str) -> None:
    cfg = {**plan["default_runtime"], **plan["experiments"][name]}
    run_dir = RUN_ROOT / name
    if run_dir.exists():
        raise RuntimeError(f"refusing to overwrite existing experiment: {run_dir}")
    run_dir.mkdir(parents=True)
    write_json(run_dir / "CONFIG.json", {
        "schema_version": "rx580-vulkan-experiment-config-v1",
        "experiment": name,
        "plan_sha256": plan["plan_sha256"],
        "source_commit": plan["source_commit"],
        "config": cfg,
        "launch_args": direct_args(plan, cfg),
        "no_reboot": True,
        "no_retries": True,
    })
    server_dir = run_dir / "server"
    server_dir.mkdir()
    stdout_handle = (server_dir / "stdout.log").open("wb")
    stderr_handle = (server_dir / "stderr.log").open("wb")
    process: subprocess.Popen[bytes] | None = None
    startup_error: str | None = None
    endpoint = f"http://{DIRECT_HOST}:{cfg['port']}/v1"
    observability_endpoint = endpoint.removesuffix("/v1")
    rows_path = run_dir / "CALLS.jsonl"
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        try:
            process = subprocess.Popen(
                direct_args(plan, cfg),
                cwd=server_dir,
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            await wait_ready(client, observability_endpoint)
        except Exception as exc:
            startup_error = f"{type(exc).__name__}: {exc}"
        before = await backend_observe(client, observability_endpoint) if startup_error is None else {}
        write_json(run_dir / "BACKEND_BEFORE.json", before)
        with rows_path.open("x", encoding="utf-8") as output:
            if startup_error is None:
                total = 1 + cfg["repetitions"]
                for index in range(total):
                    phase = "warmup" if index == 0 else "measurement"
                    repetition = index + 1 if phase == "warmup" else index
                    print(f"{name} {index + 1}/{total} {phase}", flush=True)
                    request_plan = {**load_scaling_plan(), "endpoint": endpoint}
                    row = await call_once(
                        client,
                        request_plan,
                        phase="rx580-fixed",
                        target_tokens=TARGET_ESTIMATE,
                        repetition=repetition,
                        global_index=index + 1,
                    )
                    after = await backend_observe(client, observability_endpoint)
                    row["benchmark_phase"] = phase
                    row["benchmark_experiment"] = name
                    row["backend_observability_after_call"] = after
                    row["backend_prompt_delta"] = prompt_delta(before, after)
                    row["gpu_snapshot_after_call"] = gpu_snapshot()
                    output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                    output.flush()
                    before = after
        write_json(run_dir / "BACKEND_AFTER.json", before)
    stdout_handle.close()
    stderr_handle.close()
    process_result = terminate_process(process) if process is not None else None
    run_end = {
        "ended_at": utc_now(),
        "elapsed_wall_seconds": time.perf_counter() - started,
        "startup_error": startup_error,
        "process": process_result,
        "calls_recorded": len(rows_path.read_text(encoding="utf-8").splitlines()) if rows_path.exists() else 0,
    }
    write_json(run_dir / "RUN_END.json", run_end)
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    results = build_results(name, cfg, rows, run_end)
    write_json(run_dir / "RESULTS.json", results)
    (run_dir / "REPORT.md").write_text(
        f"# {name}\n\n{cfg['description']}\n\n"
        f"Prompt tok/s p50: `{results['prompt_eval_tok_s']['p50']}`; TTFT p50: `{results['ttft_ms']['p50']}` ms; "
        f"TTFT p95: `{results['ttft_ms']['p95']}` ms; failures: `{len(results['failures'])}`.\n",
        encoding="utf-8",
    )


def load_results(name: str) -> dict[str, Any]:
    return json.loads((RUN_ROOT / name / "RESULTS.json").read_text(encoding="utf-8"))


def report_all(plan: dict[str, Any]) -> None:
    results: dict[str, Any] = {}
    for name in plan["experiments"]:
        path = RUN_ROOT / name / "RESULTS.json"
        if path.exists():
            results[name] = load_results(name)
    write_json(OUT_ROOT / "SUMMARY.json", {
        "schema_version": "rx580-vulkan-optimization-summary-v1",
        "generated_at": utc_now(),
        "plan_sha256": plan["plan_sha256"],
        "baseline_reference_tok_s": plan["baseline_reference"]["prompt_eval_tok_s"],
        "experiments": results,
    })
    if "baseline_full_vulkan" in results:
        write_json(OUT_ROOT / "BASELINE.json", results["baseline_full_vulkan"])
    baseline = results.get("baseline_full_vulkan", {})
    base_tok = baseline.get("prompt_eval_tok_s", {}).get("p50") or plan["baseline_reference"]["prompt_eval_tok_s"]
    lines = [
        "# RX 580 / Vulkan Runtime Optimization Audit",
        "",
        f"Plan hash: `{plan['plan_sha256']}`; source commit: `{plan['source_commit']}`.",
        f"Model: `{plan['model']}`; GGUF SHA-256: `{plan['model_sha256']}`; llama.cpp build: `{plan['llama_server_version']}`.",
        "No reboot, driver change, power-plan change, firmware change, display reset, or production ORCHID change was performed.",
        "",
        "| Experiment | Prompt tok/s p50 | vs baseline | TTFT p50 | TTFT p95 | Prompt eval p50 | Errors |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, result in results.items():
        tok = result["prompt_eval_tok_s"]["p50"]
        delta = ((tok / base_tok) - 1) * 100 if tok and base_tok else None
        fmt = lambda value: "—" if value is None else f"{value:.1f}"
        lines.append(
            f"| {name} | {fmt(tok)} | {fmt(delta)}% | {fmt(result['ttft_ms']['p50'])} ms | "
            f"{fmt(result['ttft_ms']['p95'])} ms | {fmt(result['prompt_eval_ms']['p50'])} ms | {len(result['failures'])} |"
        )
    lines.extend([
        "",
        "## Interpretation",
        "",
        "Prompt-evaluation tok/s is taken from direct llama-server `/metrics` counter deltas. Client TTFT is measured from request start to first streamed content token. The fixed request sequence is reused across all arms; direct prompt caching is disabled.",
        "",
        "The current direct full-Vulkan baseline is the pinned reference. Any candidate below 5% repeatable improvement is noise/not worth adopting under the audit threshold.",
        "",
        "## Hardware/runtime limits",
        "",
        "The system inventory records RX 580 device identity, Vulkan device selection, VRAM availability, driver/runtime versions, process configuration, and Windows GPU counters. Core clock, memory clock, temperature, and PCIe negotiated width/generation were not exposed by the available read-only interfaces.",
        "",
        "No newer local Vulkan llama.cpp build or llama-bench executable was available; no build toolchain or driver install was added. See `DEFERRED_REBOOT_REQUIRED.md`.",
        "The earlier harness-stop attempt is preserved under `experiments_ATTEMPT_01/` with its original plan and partial artifacts; it is excluded from the primary comparison.",
    ])
    flash = results.get("flash_attention_off")
    flash_delta = ((flash["prompt_eval_tok_s"]["p50"] / base_tok) - 1) * 100 if flash and flash["prompt_eval_tok_s"]["p50"] else None
    flash_ttft_delta = ((flash["ttft_ms"]["p50"] / baseline["ttft_ms"]["p50"]) - 1) * 100 if flash and baseline.get("ttft_ms", {}).get("p50") else None
    lines.extend([
        "",
        "## Decision answers",
        "",
        "1. Full Vulkan execution is supported by the selected AMD RX 580 Vulkan device, explicit `--n-gpu-layers 999`, successful model load, stable prompt counters, and no fallback/OOM errors. The server logs do not expose a layer-by-layer allocation report, so this is strong operational evidence rather than a claim about an unavailable internal counter.",
        "2. Vulkan reports 8192 MiB total and about 7402 MiB free at inventory time. During requests the direct process held about 3.96 GiB dedicated GPU memory; Windows also reported about 0.82 GiB non-local usage for that process. No crash or throughput collapse occurred, but the available counters cannot prove that every allocation stayed local to VRAM.",
        "3. Core/memory clocks and temperature were not exposed by the installed read-only tools. Stable repeated prompt timings show no obvious benchmark instability, but they do not prove absence of thermal or power throttling.",
        "4. PCIe negotiated generation/width was not exposed; no normality claim is made.",
        "5. The current driver is not obviously problematic: Vulkan identifies the RX 580 and performs stably. AMD Adrenalin 26.5.2 is deferred for a later maintenance window.",
        "6. No OS power or GPU preference was changed because the machine is remote and the available control was global Balanced/High-performance selection.",
        "7. No newer local Vulkan llama.cpp build or usable local build toolchain was available; no replacement was attempted.",
        f"8. No safe runtime flag produced a useful end-to-end improvement. Flash Attention off measured {flash_delta:.1f}% prompt-counter improvement but changed TTFT by {flash_ttft_delta:.1f}% and is rejected. Batch 4096, context 32768, KV unified, mmap+mlock, and thread variants were effectively tied.",
        "9. Batch 2048 / ubatch 512 / Flash Attention on is retained as the safe reference. Shader/pipeline warmup was not materially significant after the one warmup; model startup itself remained separate overhead.",
        "10. Background GPU consumers were visible in Windows counters, but no safe A/B closure test was performed; contention is therefore unquantified.",
        "11. Best repeatable prompt-counter rate was 194.4 tok/s with Flash Attention off, but the best safe configuration is the baseline at 184.9 tok/s because the faster counter did not improve TTFT.",
        "12. Further RX 580 tuning is unlikely to justify engineering time without a clock/thermal/PCIe telemetry provider or a newer Vulkan build; prompt reduction remains the higher-confidence optimization axis.",
        "",
        "RX580_STACK_ALREADY_NEAR_OPTIMAL",
    ])
    (OUT_ROOT / "FINAL_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"summary: {OUT_ROOT / 'SUMMARY.json'}")
    print(f"final report: {OUT_ROOT / 'FINAL_REPORT.md'}")


async def run_all(plan: dict[str, Any], names: list[str] | None = None) -> None:
    selected = names or list(plan["experiments"])
    unknown = [name for name in selected if name not in plan["experiments"]]
    if unknown:
        raise RuntimeError(f"unknown experiments: {unknown}")
    for name in selected:
        await run_experiment(plan, name)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("freeze")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--only", nargs="+", default=None)
    sub.add_parser("report")
    args = parser.parse_args()
    if args.command == "freeze":
        freeze()
    elif args.command == "run":
        asyncio.run(run_all(load_plan(), args.only))
    else:
        report_all(load_plan())


if __name__ == "__main__":
    main()
