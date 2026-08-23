"""Freeze and report the sealed FreetoShop Pi-vs-ORCHID all-Solar A/B.

The controller that launches Pi and the hidden browser evaluator remain
controller-side. This file owns only reproducible benchmark-root preparation,
configuration hashing, and post-run evidence assembly; it does not change
production ORCHID behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from memory_gateway.pipeline_adapters import (
    CONSOLIDATOR_RESPONSE_FORMAT,
    selector_response_format_for_chunk,
)


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = Path(
    os.environ.get(
        "ORCHID_ALL_SOLAR_ARTIFACT",
        str(ROOT / "artifacts/agent_benchmarks/freetoshop_pi_vs_orchid_all_solar"),
    )
)
STAGE = Path(
    os.environ.get(
        "ORCHID_ALL_SOLAR_STAGE",
        r"C:\Users\disti\benchmark_work\freetoshop_pi_vs_orchid_all_solar_20260823",
    )
)
OLD_ARTIFACT = ROOT / "artifacts/agent_benchmarks/freetoshop_pi_vs_orchid_20260820"
OLD_STAGE = Path(r"C:\Users\disti\benchmark_work\freetoshop_pi_vs_orchid_20260820")
ORACLE_ROOT = Path(
    os.environ.get(
        "FREETOSHOP_ORACLE_ROOT",
        r"C:\Users\disti\Documents\App Development\FreetoShop",
    )
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def run_git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def git_snapshot(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path)}
    try:
        result["head"] = run_git(path, "rev-parse", "HEAD")
        result["tree"] = run_git(path, "rev-parse", "HEAD^{tree}")
        result["status"] = run_git(path, "status", "--short")
        result["remotes"] = run_git(path, "remote", "-v")
    except Exception as error:
        result["error"] = str(error)
    return result


def freeze() -> None:
    if (ARTIFACT / "BENCHMARK_MANIFEST.json").exists():
        raise SystemExit(f"refusing to overwrite frozen artifact: {ARTIFACT}")
    source_manifest_path = OLD_ARTIFACT / "BENCHMARK_MANIFEST.json"
    source_goal_path = OLD_ARTIFACT / "BENCHMARK_GOAL.md"
    if not source_manifest_path.exists() or not source_goal_path.exists():
        raise SystemExit("historical FreetoShop manifest/goal is missing")
    source = load_json(source_manifest_path)
    goal_bytes = source_goal_path.read_bytes()
    oracle_head = run_git(ORACLE_ROOT, "rev-parse", "HEAD")
    if oracle_head != source["target_commit"]:
        raise SystemExit(
            "oracle checkout is not the frozen hidden target: "
            f"expected {source['target_commit']}, got {oracle_head}"
        )
    if STAGE.exists():
        raise SystemExit(f"refusing to reuse staging root: {STAGE}")

    ARTIFACT.mkdir(parents=True, exist_ok=False)
    (ARTIFACT / "memory_transitions").mkdir()
    (ARTIFACT / "quality").mkdir()
    (ARTIFACT / "economics").mkdir()
    (ARTIFACT / "pi_native").mkdir()
    (ARTIFACT / "orchid_all_solar").mkdir()
    (ARTIFACT / "preflight").mkdir()
    (ARTIFACT / "BENCHMARK_GOAL.md").write_bytes(goal_bytes)

    pi_config = {
        "arm": "PI_NATIVE",
        "foreground_provider": "OpenRouter",
        "foreground_model": "upstage/solar-pro4",
        "native_compaction": True,
        "pi_version": "0.84.2",
        "settings": "historical sealed Pi settings; provider-default temperature",
        "retry_policy": "historical Pi/runtime policy; no controller rescue",
    }
    orchid_config = {
        "arm": "ORCHID_ALL_SOLAR",
        "foreground_provider": "OpenRouter",
        "foreground_model": "upstage/solar-pro4",
        "selector_provider": "OpenRouter",
        "selector_model": "upstage/solar-pro4",
        "consolidator_provider": "OpenRouter",
        "consolidator_model": "upstage/solar-pro4",
        "canonicalizer_enabled": False,
        "selector_prompt_version": "selector-v1",
        "selector_chunk_target_tokens": 1200,
        "selector_dynamic_exact_id_schema": True,
        "canonicalizer_prompt_version": "canonicalizer-v1",
        "consolidator_prompt_version": "consolidator-v1",
        "temperature": 0,
        "top_p": 1,
        "reasoning_effort": "none",
        "structured_output": True,
        "timeout_policy_seconds": 120,
        "retry_policy": "existing adapter policy; no benchmark controller rescue",
        "cold_memory_mode": "inject",
        "cold_memory_timeout_ms": 50,
        "cold_memory_token_budget": 512,
        "cold_memory_max_injected": 3,
        "dense_production_injection": False,
        "compaction_context_tokens": 524288,
        "compaction_reserved_output_tokens": 8192,
        "compaction_fixed_overhead_tokens": 4096,
        "compaction_safety_margin": 0.7,
        "compaction_request_amplification": 3.0,
    }
    write_json(ARTIFACT / "CONFIG_PI_NATIVE.json", pi_config)
    write_json(ARTIFACT / "CONFIG_ORCHID_ALL_SOLAR.json", orchid_config)

    manifest = {
        "benchmark": "FreetoShop Pi-vs-ORCHID All-Solar Architecture Feasibility A/B",
        "run_id": "freetoshop_pi_vs_orchid_all_solar_20260823",
        "status": "FROZEN",
        "design": "sealed contemporaneous paired run, n=1",
        "frozen_at": now(),
        "repository_commit_at_freeze": run_git(ROOT, "rev-parse", "HEAD"),
        "historical_source_manifest": str(source_manifest_path),
        "historical_source_manifest_sha256": sha256_file(source_manifest_path),
        "start_commit": source["start_commit"],
        "start_tree": source["canonical_source_fingerprint"]["start_tree"],
        "target_commit_hidden_from_agents": source["target_commit"],
        "target_tree": source["canonical_source_fingerprint"]["target_tree"],
        "public_goal": {
            "path": "BENCHMARK_GOAL.md",
            "sha256": sha256_bytes(goal_bytes),
            "byte_identical_between_arms": True,
        },
        "sealed_arms": {
            "PI_NATIVE": {
                "baseline_commit": source["sealed_arms"]["arm_a_pi"]["baseline_commit"],
                "tree": source["sealed_arms"]["arm_a_pi"]["tree"],
                "model": "openrouter/upstage/solar-pro4",
                "native_compaction": True,
            },
            "ORCHID_ALL_SOLAR": {
                "baseline_commit": source["sealed_arms"]["arm_b_orchid"]["baseline_commit"],
                "tree": source["sealed_arms"]["arm_b_orchid"]["tree"],
                "model": "orchid/upstage/solar-pro4",
                "native_compaction": False,
                "selector_model": "upstage/solar-pro4",
                "consolidator_model": "upstage/solar-pro4",
                "canonicalizer_enabled": False,
            },
        },
        "solar": {
            "provider": "OpenRouter",
            "endpoint": "https://openrouter.ai/api/v1/chat/completions",
            "model": "upstage/solar-pro4",
            "context_window": 524288,
            "api_key_source": "OPENROUTER_API_KEY user environment; value excluded",
        },
        "pi": {
            "version": "0.84.2",
            "mode": "rpc",
            "extensions": "disabled",
            "skills": "disabled",
            "context_files": "disabled",
            "prompt_templates": "disabled",
            "thinking": "medium",
            "goal_mode": "disabled_for_both_arms",
        },
        "orchid": {
            "compaction_model_roles": ["selector", "consolidator"],
            "canonicalizer_enabled": False,
            "selector_chunk_target_tokens": 1200,
            "selector_exact_id_hardening": True,
            "cold_memory_mode": "inject",
            "dense_production_injection": False,
            "scheduler": "existing coalesced scheduler",
            "provider_aware_source_window": True,
            "database": "fresh benchmark-only database",
        },
        "run_policy": {
            "max_wall_clock_ms_per_arm": 21600000,
            "checkpoint_interval_ms": 900000,
            "launch": "paired, as close together as practical",
            "controller_restart_limit": 0,
            "manual_intervention": "none",
            "manual_rescue": "forbidden",
            "tuning_after_launch": "forbidden",
            "provider_retry": "existing client policy only",
        },
        "predeclared_stop_conditions": [
            "unrecoverable integration stall",
            "asymmetric provider outage",
            "repository corruption",
            "benchmark leakage",
            "resource exhaustion invalidating comparison",
        ],
        "stage": str(STAGE),
        "oracle_root": str(ORACLE_ROOT),
        "source_protocol": "reused historical sealed FreetoShop protocol; no hidden target exposed to agents",
    }
    write_json(ARTIFACT / "BENCHMARK_MANIFEST.json", manifest)
    hashes = {
        "benchmark_goal_sha256": sha256_file(ARTIFACT / "BENCHMARK_GOAL.md"),
        "benchmark_manifest_sha256": sha256_file(ARTIFACT / "BENCHMARK_MANIFEST.json"),
        "config_pi_native_sha256": sha256_file(ARTIFACT / "CONFIG_PI_NATIVE.json"),
        "config_orchid_all_solar_sha256": sha256_file(ARTIFACT / "CONFIG_ORCHID_ALL_SOLAR.json"),
        "source_repository_commit": manifest["repository_commit_at_freeze"],
        "start_commit": manifest["start_commit"],
        "target_commit": manifest["target_commit_hidden_from_agents"],
    }
    write_json(ARTIFACT / "CONFIG_HASHES.json", hashes)
    (ARTIFACT / "memory_transitions" / "pi_compactions.jsonl").write_text("", encoding="utf-8")
    (ARTIFACT / "memory_transitions" / "orchid_promotions.jsonl").write_text("", encoding="utf-8")
    print(json.dumps({"artifact": str(ARTIFACT), "stage": str(STAGE), "manifest": manifest}, indent=2))


def prepare() -> None:
    manifest = load_json(ARTIFACT / "BENCHMARK_MANIFEST.json")
    if STAGE.exists():
        raise SystemExit(f"refusing to reuse staging root: {STAGE}")
    STAGE.mkdir(parents=True)
    source_a = OLD_STAGE / "arm-a-pi"
    source_b = OLD_STAGE / "arm-b-orchid"
    source_agent_a = OLD_STAGE / "pi-agent-a"
    source_agent_b = OLD_STAGE / "pi-agent-b"
    for source in (source_a, source_b, source_agent_a, source_agent_b):
        if not source.exists():
            raise SystemExit(f"historical staging input is missing: {source}")
    ignored_repo = shutil.ignore_patterns("node_modules", "test-results", ".freebuff")
    shutil.copytree(source_a, STAGE / "arm-a-pi", ignore=ignored_repo)
    shutil.copytree(source_b, STAGE / "arm-b-orchid", ignore=ignored_repo)
    shutil.copytree(source_agent_a, STAGE / "pi-agent-a")
    shutil.copytree(source_agent_b, STAGE / "pi-agent-b")
    for arm_name, baseline in (("arm-a-pi", manifest["sealed_arms"]["PI_NATIVE"]["baseline_commit"]), ("arm-b-orchid", manifest["sealed_arms"]["ORCHID_ALL_SOLAR"]["baseline_commit"])):
        arm = STAGE / arm_name
        subprocess.run(["git", "reset", "--hard", baseline], cwd=arm, check=True, capture_output=True, text=True)
        subprocess.run(["git", "clean", "-fd"], cwd=arm, check=True, capture_output=True, text=True)
        subprocess.run(["git", "remote", "remove", "origin"], cwd=arm, check=False, capture_output=True, text=True)
        (arm / "BENCHMARK_GOAL.md").write_bytes((ARTIFACT / "BENCHMARK_GOAL.md").read_bytes())
        node_modules = arm / "node_modules"
        source_node_modules = OLD_STAGE / arm_name / "node_modules"
        if source_node_modules.exists() and not node_modules.exists():
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(node_modules), str(source_node_modules)],
                check=True,
                capture_output=True,
                text=True,
            )
    for name in ("sessions", "orchid-state"):
        (STAGE / name).mkdir()
    write_json(ARTIFACT / "PREPARED_STAGE.json", {"prepared_at": now(), "stage": str(STAGE), "pi": git_snapshot(STAGE / "arm-a-pi"), "orchid": git_snapshot(STAGE / "arm-b-orchid")})
    print(json.dumps({"prepared": str(STAGE), "manifest_start_commit": manifest["start_commit"]}, indent=2))


def preflight() -> None:
    if not (ARTIFACT / "BENCHMARK_MANIFEST.json").exists():
        raise SystemExit("freeze must run before preflight")
    key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY_USER")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY must be present in the process environment")
    direct_url = "https://openrouter.ai/api/v1/chat/completions"
    gateway_url = "http://127.0.0.1:7332/v1/chat/completions"
    headers = {"authorization": f"Bearer {key}", "content-type": "application/json"}
    common = {"model": "upstage/solar-pro4", "temperature": 0, "top_p": 1, "stream": False}
    requests = {
        "foreground_gateway": {
            "url": gateway_url,
            "body": {**common, "messages": [{"role": "user", "content": "Reply with exactly READY."}]},
        },
        "selector_direct": {
            "url": direct_url,
            "body": {
                **common,
                "messages": [{"role": "system", "content": "Select durable-memory source items. Return JSON only."}, {"role": "user", "content": 'INPUT_JSON\n{"events":[{"id":"evt-1","role":"user","content":"Keep this task fact."}],"event_ids_in_order":["evt-1"],"source_ids_in_order":["evt-1"]}'}],
                "response_format": selector_response_format_for_chunk(["evt-1"]),
            },
        },
        "consolidator_direct": {
            "url": direct_url,
            "body": {
                **common,
                "messages": [{"role": "system", "content": "Render a durable-memory capsule. Return JSON only."}, {"role": "user", "content": 'INPUT_JSON\n{"base_capsule":null,"events":[{"id":"evt-1","role":"user","content":"Keep this task fact."}],"canonical_text":"Keep this task fact.","evidence_event_ids":["evt-1"]}'}],
                "response_format": CONSOLIDATOR_RESPONSE_FORMAT,
            },
        },
    }
    results: dict[str, Any] = {"started_at": now(), "requests": {}}
    with httpx.Client(timeout=180.0) as client:
        for name, request in requests.items():
            started = time.perf_counter()
            try:
                response = client.post(request["url"], json=request["body"], headers=headers)
                payload: Any
                try:
                    payload = response.json()
                except Exception:
                    payload = response.text[:2000]
                results["requests"][name] = {
                    "url": request["url"],
                    "status_code": response.status_code,
                    "ok": response.is_success,
                    "wall_ms": (time.perf_counter() - started) * 1000,
                    "response": payload if response.is_success else str(payload)[:2000],
                }
            except Exception as error:
                results["requests"][name] = {
                    "url": request["url"],
                    "ok": False,
                    "wall_ms": (time.perf_counter() - started) * 1000,
                    "error": str(error),
                }
    results["ended_at"] = now()
    results["all_ok"] = all(row.get("ok") for row in results["requests"].values())
    write_json(ARTIFACT / "preflight" / "RESULTS.json", results)
    print(json.dumps(results, indent=2))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    rows.append({"_invalid_json": line[:500]})
    return rows


def summarize_rpc(path: Path) -> dict[str, Any]:
    rows = load_jsonl(path)
    event_counts = Counter(str(row.get("type", "unknown")) for row in rows)
    usages: list[dict[str, Any]] = []
    for row in rows:
        value = row.get("usage")
        if isinstance(value, dict):
            usages.append(value)
        value = row.get("assistantMessageEvent")
        if isinstance(value, dict) and isinstance(value.get("usage"), dict):
            usages.append(value["usage"])
    def total(field: str) -> float:
        return sum(float(u.get(field) or 0) for u in usages)
    return {
        "path": str(path),
        "rpc_lines": len(rows),
        "event_counts": dict(event_counts),
        "assistant_messages": event_counts.get("message_end", 0),
        "tool_calls": event_counts.get("tool_execution_start", 0),
        "tool_failures": sum(1 for row in rows if row.get("type") == "tool_execution_end" and row.get("isError")),
        "pi_native_compactions": event_counts.get("compaction_end", 0),
        "agent_settled": event_counts.get("agent_settled", 0),
        "provider_errors": sum(1 for row in rows if "error" in str(row.get("type", "")).lower() or "error" in str(row.get("stopReason", "")).lower()),
        "usage_samples": len(usages),
        "input_tokens_observed": total("input"),
        "output_tokens_observed": total("output"),
        "cache_read_tokens_observed": total("cacheRead"),
    }


def db_summary(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"path": str(db_path), "status": "MISSING"}
    result: dict[str, Any] = {"path": str(db_path), "status": "OK", "bytes": db_path.stat().st_size}
    with sqlite3.connect(db_path) as connection:
        for table, key in (("events", "events"), ("compaction_jobs", "compaction_jobs"), ("capsules", "capsules"), ("long_term_memories", "long_term_memories"), ("model_runs", "model_runs")):
            try:
                result[key] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                result[key] = None
        try:
            result["job_status_counts"] = dict(connection.execute("SELECT status, COUNT(*) FROM compaction_jobs GROUP BY status").fetchall())
        except sqlite3.Error:
            result["job_status_counts"] = {}
        try:
            result["promotion_count"] = connection.execute("SELECT COUNT(*) FROM compaction_jobs WHERE status='PROMOTED'").fetchone()[0]
        except sqlite3.Error:
            result["promotion_count"] = 0
        try:
            rows = connection.execute("SELECT stage, COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(wall_ms) FROM model_runs GROUP BY stage").fetchall()
            result["model_runs_by_stage"] = [
                {"stage": row[0], "calls": row[1], "input_tokens": row[2], "output_tokens": row[3], "wall_ms": row[4]}
                for row in rows
            ]
        except sqlite3.Error:
            result["model_runs_by_stage"] = []
    return result


def copy_repository_final(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.mkdir(parents=True, exist_ok=True)
    ignored = shutil.ignore_patterns(".git", "node_modules", "test-results", ".freebuff")
    shutil.copytree(source, destination, dirs_exist_ok=True, ignore=ignored)


def report() -> None:
    manifest_path = ARTIFACT / "BENCHMARK_MANIFEST.json"
    if not manifest_path.exists():
        raise SystemExit("freeze must run before report")
    pi_log = ARTIFACT / "pi_native" / "telemetry.jsonl"
    orchid_log = ARTIFACT / "orchid_all_solar" / "telemetry.jsonl"
    pi_summary = summarize_rpc(pi_log)
    orchid_summary = summarize_rpc(orchid_log)
    db = db_summary(STAGE / "orchid-state" / "memory.db")
    copy_repository_final(STAGE / "arm-a-pi", ARTIFACT / "pi_native" / "repository_final")
    copy_repository_final(STAGE / "arm-b-orchid", ARTIFACT / "orchid_all_solar" / "repository_final")
    hidden = ARTIFACT / "quality" / "hidden_eval.json"
    source_hidden = ARTIFACT / "hidden_evaluation" / "hidden_results.json"
    if source_hidden.exists():
        shutil.copyfile(source_hidden, hidden)
    status = "COMPLETED" if pi_summary.get("rpc_lines") and orchid_summary.get("rpc_lines") else "INCOMPLETE"
    transitions = {
        "pi_compactions": pi_summary.get("pi_native_compactions", 0),
        "orchid_promotions": db.get("promotion_count", 0),
        "orchid_job_status_counts": db.get("job_status_counts", {}),
    }
    write_json(ARTIFACT / "memory_transitions" / "SUMMARY.json", transitions)
    write_json(ARTIFACT / "pi_native" / "SUMMARY.json", pi_summary)
    write_json(ARTIFACT / "orchid_all_solar" / "SUMMARY.json", {**orchid_summary, "gateway": db})
    write_json(ARTIFACT / "SUMMARY.json", {"status": status, "generated_at": now(), "pi_native": pi_summary, "orchid_all_solar": {**orchid_summary, "gateway": db}, "memory_transitions": transitions, "hidden_evaluation_present": hidden.exists()})
    write_json(ARTIFACT / "economics" / "SUMMARY.json", {"status": "MEASURED_FROM_PROVIDER_TELEMETRY", "pi_native": {"input_tokens": pi_summary.get("input_tokens_observed"), "output_tokens": pi_summary.get("output_tokens_observed")}, "orchid_foreground": {"input_tokens": orchid_summary.get("input_tokens_observed"), "output_tokens": orchid_summary.get("output_tokens_observed")}, "orchid_memory_pipeline": db.get("model_runs_by_stage", [])})
    write_json(ARTIFACT / "quality" / "milestone_scores.jsonl", [{"arm": "PI_NATIVE", "assistant_messages": pi_summary.get("assistant_messages"), "tool_calls": pi_summary.get("tool_calls")}, {"arm": "ORCHID_ALL_SOLAR", "assistant_messages": orchid_summary.get("assistant_messages"), "tool_calls": orchid_summary.get("tool_calls")}])
    manifest = load_json(manifest_path)
    manifest["status"] = status
    manifest["reported_at"] = now()
    write_json(manifest_path, manifest)
    report_text = build_report(manifest, pi_summary, orchid_summary, db, hidden.exists())
    (ARTIFACT / "FINAL_REPORT.md").write_text(report_text, encoding="utf-8")
    (ARTIFACT / "quality" / "REPORT.md").write_text(build_quality_report(pi_summary, orchid_summary, hidden.exists()), encoding="utf-8")
    (ARTIFACT / "economics" / "REPORT.md").write_text(build_economics_report(pi_summary, orchid_summary, db), encoding="utf-8")
    write_json(ARTIFACT / "CONFIG_HASHES.json", {**load_json(ARTIFACT / "CONFIG_HASHES.json"), "final_manifest_sha256": sha256_file(manifest_path)})
    print(json.dumps({"artifact": str(ARTIFACT), "status": status, "pi": pi_summary, "orchid": orchid_summary, "gateway": db}, indent=2))


def build_quality_report(pi: dict[str, Any], orchid: dict[str, Any], hidden_present: bool) -> str:
    return """# Quality evidence\n\nThis report contains measured controller/RPC evidence only. A missing hidden-evaluator file is reported as missing, not as a pass.\n\n| Arm | Assistant message events | Tool calls | Tool failures | Native compactions |\n|---|---:|---:|---:|---:|\n| PI_NATIVE | {pi_messages} | {pi_tools} | {pi_failures} | {pi_compactions} |\n| ORCHID_ALL_SOLAR | {o_messages} | {o_tools} | {o_failures} | n/a |\n\nHidden evaluation present: **{hidden}**.\n""".format(pi_messages=pi.get("assistant_messages", 0), pi_tools=pi.get("tool_calls", 0), pi_failures=pi.get("tool_failures", 0), pi_compactions=pi.get("pi_native_compactions", 0), o_messages=orchid.get("assistant_messages", 0), o_tools=orchid.get("tool_calls", 0), o_failures=orchid.get("tool_failures", 0), hidden=hidden_present)


def build_economics_report(pi: dict[str, Any], orchid: dict[str, Any], db: dict[str, Any]) -> str:
    return """# Provider usage and economics\n\nProvider token totals are summed from observed Pi RPC usage events and ORCHID gateway model-run records where available. Missing provider fields remain missing.\n\n- PI_NATIVE foreground observed input/output: **{pi_in:.0f} / {pi_out:.0f}** tokens.\n- ORCHID foreground observed input/output: **{o_in:.0f} / {o_out:.0f}** tokens.\n- ORCHID selector/canonicalizer/consolidator records: `{stages}`.\n- This is an upper-bound feasibility record, not final local Qwen economics.\n""".format(pi_in=pi.get("input_tokens_observed", 0), pi_out=pi.get("output_tokens_observed", 0), o_in=orchid.get("input_tokens_observed", 0), o_out=orchid.get("output_tokens_observed", 0), stages=json.dumps(db.get("model_runs_by_stage", []), sort_keys=True))


def build_report(manifest: dict[str, Any], pi: dict[str, Any], orchid: dict[str, Any], db: dict[str, Any], hidden_present: bool) -> str:
    promotions = db.get("promotion_count", 0)
    pi_compactions = pi.get("pi_native_compactions", 0)
    return f"""# FreetoShop Pi-vs-ORCHID All-Solar Architecture Feasibility A/B

Status: **{manifest.get('status')}**. This is a sealed paired run using the historical start commit `{manifest.get('start_commit')}` and public-goal hash `{manifest.get('public_goal', {}).get('sha256')}`. The hidden target remained controller-only.

## Measured arm evidence

| Arm | RPC lines | Assistant messages | Tool calls | Tool failures | Input tokens observed | Output tokens observed |
|---|---:|---:|---:|---:|---:|---:|
| PI_NATIVE | {pi.get('rpc_lines', 0)} | {pi.get('assistant_messages', 0)} | {pi.get('tool_calls', 0)} | {pi.get('tool_failures', 0)} | {pi.get('input_tokens_observed', 0):.0f} | {pi.get('output_tokens_observed', 0):.0f} |
| ORCHID_ALL_SOLAR | {orchid.get('rpc_lines', 0)} | {orchid.get('assistant_messages', 0)} | {orchid.get('tool_calls', 0)} | {orchid.get('tool_failures', 0)} | {orchid.get('input_tokens_observed', 0):.0f} | {orchid.get('output_tokens_observed', 0):.0f} |

## Memory transitions

1. Both arms ran continuously without manual rescue: **{pi.get('rpc_lines', 0) > 0 and orchid.get('rpc_lines', 0) > 0}; controller evidence only**.
2. Pi native compaction occurred: **{pi_compactions > 0}**, count **{pi_compactions}**.
3. ORCHID performed multiple ACTIVE promotions: **{promotions > 1}**, count **{promotions}**.
4. ORCHID retirement throughput exceeded arrival throughput: **not established by this generic report; inspect telemetry and gateway model-run records**.
5. ORCHID backlog bounded/caught up: **not inferred from job counts alone**; job status counts: `{json.dumps(db.get('job_status_counts', {}), sort_keys=True)}`.
6. Repeated ORCHID awake-context collapse after promotion: **not inferred from RPC counts alone**.

## Quality and repository evidence

7. Observable coding degradation after ORCHID promotion: **not inferred without milestone/hidden-eval evidence**.
8. Observable Pi degradation after native compaction: **not inferred without post-compaction paired evidence**.
9. Hidden evaluation artifact present: **{hidden_present}**.
10. Final repositories are preserved under `pi_native/repository_final/` and `orchid_all_solar/repository_final/`; build, Clone Stamp, undo/redo, and persistence claims require the recorded validation outputs, not message counts.

## Configuration and limits

- Foreground, selector, canonicalizer, and consolidator model: `upstage/solar-pro4` via OpenRouter.
- Selector chunk target: 1,200 estimated source tokens; dynamic exact-ID schema and software provenance validation preserved.
- No local Qwen selector, selector chunk change, selector concurrency, retrieval redesign, direct raw-to-Solar arm, or production ORCHID change.
- Provider-equivalent economics and cumulative context-token area are reported separately; this phase is architecture feasibility, not final local economics.

## Required interpretation

The final architectural verdict must be assigned only after inspecting the raw telemetry, promotion transitions, quality results, and hidden evaluator. This generated report intentionally does not convert missing transitions into a success or failure claim.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("freeze", "prepare", "preflight", "report"))
    args = parser.parse_args()
    {"freeze": freeze, "prepare": prepare, "preflight": preflight, "report": report}[args.command]()


if __name__ == "__main__":
    main()
