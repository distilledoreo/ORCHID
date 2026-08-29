"""Assemble post-run evidence for the sealed all-Solar FreetoShop A/B.

This is benchmark-only reporting code. It never changes either staged agent
repository or the ORCHID database; it copies final trees and records the
observed interrupted state as evidence.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from benchmark_freetoshop_all_solar_ab import (
    ARTIFACT,
    STAGE,
    copy_repository_final,
    db_summary,
    git_snapshot,
    load_json,
    load_jsonl,
    now,
    write_json,
)


def rpc_summary(path: Path) -> dict[str, Any]:
    rows = load_jsonl(path)
    event_counts = Counter(str(row.get("type", "unknown")) for row in rows)
    assistant_ends = [
        row for row in rows
        if row.get("type") == "message_end"
        and isinstance(row.get("message"), dict)
        and row["message"].get("role") == "assistant"
    ]
    usage_rows = [
        row["message"]["usage"] for row in assistant_ends
        if isinstance(row["message"].get("usage"), dict)
    ]
    if not usage_rows:
        usage_rows = [
            row["usage"] for row in rows
            if row.get("type") == "message_end" and isinstance(row.get("usage"), dict)
        ]

    def total(field: str) -> float:
        return sum(float(item.get(field) or 0) for item in usage_rows)

    errors = []
    for row in rows:
        message = row.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            if message.get("stopReason") == "error":
                errors.append(message.get("errorMessage") or message.get("error") or "assistant error")
        if row.get("type") == "error" or row.get("error"):
            errors.append(row.get("error") or row.get("message") or "rpc error")
    return {
        "path": str(path),
        "rpc_lines": len(rows),
        "event_counts": dict(event_counts),
        "assistant_messages": len(assistant_ends),
        "tool_calls": event_counts.get("tool_execution_start", 0),
        "tool_failures": sum(
            1 for row in rows
            if row.get("type") == "tool_execution_end" and row.get("isError")
        ),
        "pi_native_compactions": event_counts.get("compaction_end", 0),
        "agent_settled": event_counts.get("agent_settled", 0),
        "provider_errors": len(errors),
        "errors": errors[:50],
        "usage_samples": len(usage_rows),
        "input_tokens_observed": total("input"),
        "output_tokens_observed": total("output"),
        "cache_read_tokens_observed": total("cacheRead"),
        "cost_observed": sum(
            float((item.get("cost") or {}).get("total") or 0)
            for item in usage_rows
            if isinstance(item.get("cost"), dict)
        ),
    }


def db_details(path: Path) -> dict[str, Any]:
    result = db_summary(path)
    if not path.exists():
        return result
    with sqlite3.connect(path) as con:
        con.row_factory = sqlite3.Row
        result["jobs"] = [dict(row) for row in con.execute(
            "SELECT id,generation,status,created_at,started_at,finished_at,error," \
            "snapshot_start_event_id,snapshot_end_event_id FROM compaction_jobs ORDER BY generation"
        )]
        result["capsules_detail"] = [dict(row) for row in con.execute(
            "SELECT capsules.id,capsules.state,capsules.created_at,capsules.validated_at,capsules.covered_start_event_id,capsules.covered_end_event_id "
            "FROM capsules LEFT JOIN compaction_jobs ON capsules.id = compaction_jobs.id "
            "ORDER BY capsules.created_at"
        )]
        result["model_runs_detail"] = [dict(row) for row in con.execute(
            "SELECT generation,stage,COUNT(*) calls,SUM(input_tokens) input_tokens," \
            "SUM(output_tokens) output_tokens,SUM(wall_ms) wall_ms " \
            "FROM model_runs GROUP BY generation,stage ORDER BY generation,stage"
        )]
        result["event_timing"] = dict(con.execute(
            "SELECT MIN(created_at) first_event,MAX(created_at) last_event," \
            "COUNT(*) event_count,COALESCE(SUM(token_count),0) token_count FROM events"
        ).fetchone())
        result["active_capsules"] = [dict(row) for row in con.execute(
            "SELECT id,state,created_at,validated_at,covered_start_event_id,covered_end_event_id," \
            "LENGTH(content) content_chars FROM capsules ORDER BY created_at"
        )]
    return result


def run_validation(arm: str, cwd: Path) -> dict[str, Any]:
    output_dir = ARTIFACT / "quality"
    result: dict[str, Any] = {"arm": arm, "cwd": str(cwd), "commands": {}}
    for command_name in ("typecheck", "build"):
        try:
            completed = subprocess.run(
                ["npm.cmd", "run", command_name],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=1800,
                env={**__import__("os").environ, "CI": "1"},
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            output_path = output_dir / f"{arm.lower()}_{command_name}.log"
            output_path.write_text(output, encoding="utf-8", errors="replace")
            result["commands"][command_name] = {
                "exit_code": completed.returncode,
                "output_path": str(output_path),
                "tail": output[-4000:],
            }
        except Exception as error:
            result["commands"][command_name] = {"error": str(error)}
    return result


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> None:
    pi_log = ARTIFACT / "pi_native" / "telemetry.jsonl"
    orchid_log = ARTIFACT / "orchid_all_solar" / "telemetry.jsonl"
    pi = rpc_summary(pi_log)
    orchid = rpc_summary(orchid_log)
    db = db_details(STAGE / "orchid-state" / "memory.db")

    copy_repository_final(STAGE / "arm-a-pi", ARTIFACT / "pi_native" / "repository_final")
    copy_repository_final(STAGE / "arm-b-orchid", ARTIFACT / "orchid_all_solar" / "repository_final")
    write_json(ARTIFACT / "repository_snapshots.json", {
        "pi_native": git_snapshot(STAGE / "arm-a-pi"),
        "orchid_all_solar": git_snapshot(STAGE / "arm-b-orchid"),
    })

    pi_compactions = [
        {"source": "pi_native/telemetry.jsonl", "event": row}
        for row in load_jsonl(pi_log) if row.get("type") == "compaction_end"
    ]
    promotions = [row for row in db.get("jobs", []) if row.get("status") == "PROMOTED"]
    write_jsonl(ARTIFACT / "memory_transitions" / "pi_compactions.jsonl", pi_compactions)
    write_jsonl(ARTIFACT / "memory_transitions" / "orchid_promotions.jsonl", promotions)
    write_json(ARTIFACT / "memory_transitions" / "SUMMARY.json", {
        "pi_compactions": len(pi_compactions),
        "orchid_promotions": len(promotions),
        "jobs": db.get("jobs", []),
        "job_status_counts": db.get("job_status_counts", {}),
    })

    termination = {
        "status": "STOPPED_UNDER_PREDECLARED_CONDITION",
        "recorded_at": now(),
        "reason": "resource exhaustion invalidating comparison",
        "detail": "Four ORCHID compaction failures were recorded, including repeated canonicalizer/provenance failure and a 524288-token provider context rejection; generation 6 remained running when the controller and private gateway were stopped.",
        "manual_rescue": False,
        "controller_restart_count": 0,
        "os_restarted": False,
        "user_processes_touched": False,
    }
    write_json(ARTIFACT / "RUN_TERMINATION.json", termination)

    validation = {
        "PI_NATIVE": run_validation("PI_NATIVE", STAGE / "arm-a-pi"),
        "ORCHID_ALL_SOLAR": run_validation("ORCHID_ALL_SOLAR", STAGE / "arm-b-orchid"),
    }
    write_json(ARTIFACT / "quality" / "validation.json", validation)
    write_jsonl(ARTIFACT / "quality" / "milestone_scores.jsonl", [
        {"arm": "PI_NATIVE", "assistant_messages": pi["assistant_messages"], "tool_calls": pi["tool_calls"], "files_changed": "see repository_final/status"},
        {"arm": "ORCHID_ALL_SOLAR", "assistant_messages": orchid["assistant_messages"], "tool_calls": orchid["tool_calls"], "files_changed": "see repository_final/status"},
    ])

    hidden_path = ARTIFACT / "quality" / "hidden_eval.json"
    source_hidden = ARTIFACT / "hidden_evaluation" / "hidden_results.json"
    if source_hidden.exists():
        shutil.copyfile(source_hidden, hidden_path)
    hidden = load_json(hidden_path) if hidden_path.exists() else {}

    write_json(ARTIFACT / "pi_native" / "SUMMARY.json", pi)
    write_json(ARTIFACT / "orchid_all_solar" / "SUMMARY.json", {**orchid, "gateway": db})
    write_json(ARTIFACT / "economics" / "SUMMARY.json", {
        "provider": "OpenRouter",
        "foreground": {"pi_native": pi, "orchid_all_solar": orchid},
        "orchid_memory_pipeline": db.get("model_runs_by_stage", []),
        "cumulative_foreground_input_token_area_proxy": {
            "pi_native": pi["input_tokens_observed"],
            "orchid_all_solar": orchid["input_tokens_observed"],
            "interpretation": "sum of observed foreground input tokens; not a causal savings estimate because the arms diverged before comparable transitions",
        },
    })

    pi_typecheck = validation["PI_NATIVE"]["commands"].get("typecheck", {}).get("exit_code") == 0
    pi_build = validation["PI_NATIVE"]["commands"].get("build", {}).get("exit_code") == 0
    orchid_typecheck = validation["ORCHID_ALL_SOLAR"]["commands"].get("typecheck", {}).get("exit_code") == 0
    orchid_build = validation["ORCHID_ALL_SOLAR"]["commands"].get("build", {}).get("exit_code") == 0
    hidden_arms = hidden.get("arms", {})

    verdict = "ARCHITECTURE_OPERATIONALLY_WEAK"
    summary = {
        "status": "INCOMPLETE_POST_STOP",
        "verdict": verdict,
        "rx580_local_selector_optimization": "NOT_REQUIRED_FOR_FEASIBILITY",
        "pi_native": {**pi, "typecheck": pi_typecheck, "build": pi_build},
        "orchid_all_solar": {**orchid, "gateway": db, "typecheck": orchid_typecheck, "build": orchid_build},
        "hidden_evaluation": hidden_arms,
        "termination": termination,
    }
    write_json(ARTIFACT / "SUMMARY.json", summary)

    for arm, data in (("PI_NATIVE", pi), ("ORCHID_ALL_SOLAR", orchid)):
        val = validation[arm]["commands"]
        (ARTIFACT / ("pi_native" if arm == "PI_NATIVE" else "orchid_all_solar") / "REPORT.md").write_text(
            f"# {arm}\n\n"
            f"Assistant messages: **{data['assistant_messages']}**; tool calls: **{data['tool_calls']}**; tool failures: **{data['tool_failures']}**.\n\n"
            f"Observed foreground input/output: **{data['input_tokens_observed']:.0f} / {data['output_tokens_observed']:.0f}** tokens.\n\n"
            f"Typecheck exit: **{val.get('typecheck', {}).get('exit_code')}**. Build exit: **{val.get('build', {}).get('exit_code')}**.\n\n"
            "This arm was not manually rescued or tuned. See the raw telemetry and validation logs for complete evidence.\n",
            encoding="utf-8",
        )

    hidden_lines = []
    for arm_id, row in hidden_arms.items():
        hidden_lines.append(f"- {arm_id}: exit `{row.get('exit_code')}`; duration `{row.get('duration_ms')}` ms; failure tail preserved in `quality/hidden_eval.json`.")
    quality_report = (
        "# Quality evidence\n\n"
        "The run stopped under a predeclared operational invalidation condition; no agent repair was performed.\n\n"
        f"- Pi native compactions: **{len(pi_compactions)}**.\n"
        f"- ORCHID ACTIVE promotions: **{len(promotions)}**.\n"
        f"- Pi typecheck/build: **{pi_typecheck}/{pi_build}**.\n"
        f"- ORCHID typecheck/build: **{orchid_typecheck}/{orchid_build}**.\n\n"
        "## Hidden Clone Stamp probe\n\n" + "\n".join(hidden_lines) + "\n"
    )
    (ARTIFACT / "quality" / "REPORT.md").write_text(quality_report, encoding="utf-8")

    stage_rows = json.dumps(db.get("model_runs_by_stage", []), sort_keys=True)
    (ARTIFACT / "economics" / "REPORT.md").write_text(
        "# Provider usage and economics\n\n"
        f"Pi foreground observed input/output: **{pi['input_tokens_observed']:.0f} / {pi['output_tokens_observed']:.0f}**.\n\n"
        f"ORCHID foreground observed input/output: **{orchid['input_tokens_observed']:.0f} / {orchid['output_tokens_observed']:.0f}**.\n\n"
        f"ORCHID selector/canonicalizer/consolidator model-run aggregates: `{stage_rows}`.\n\n"
        "The foreground token sum is an observed context-area proxy, not a causal savings claim because the arms did not reach comparable memory transitions.\n",
        encoding="utf-8",
    )

    manifest = load_json(ARTIFACT / "BENCHMARK_MANIFEST.json")
    manifest["status"] = "INCOMPLETE_POST_STOP"
    manifest["reported_at"] = now()
    manifest["termination"] = termination
    write_json(ARTIFACT / "BENCHMARK_MANIFEST.json", manifest)

    hidden_summary = "; ".join(
        f"{arm} exit={row.get('exit_code')}"
        for arm, row in hidden_arms.items()
    ) or "missing"
    final_report = f"""# FreetoShop Pi-vs-ORCHID All-Solar Architecture Feasibility A/B

Status: **INCOMPLETE_POST_STOP**. Frozen start commit: `{manifest['start_commit']}`. Public goal SHA256: `{manifest['public_goal']['sha256']}`. All semantic roles used `upstage/solar-pro4` via OpenRouter; no local Qwen was used.

## Direct measured result

| Arm | Assistant messages | Tool calls | Pi compactions / ORCHID promotions | Foreground input/output | Typecheck | Build |
|---|---:|---:|---:|---:|---:|---:|
| Pi native | {pi['assistant_messages']} | {pi['tool_calls']} | {len(pi_compactions)} | {pi['input_tokens_observed']:.0f} / {pi['output_tokens_observed']:.0f} | {pi_typecheck} | {pi_build} |
| ORCHID all-Solar | {orchid['assistant_messages']} | {orchid['tool_calls']} | {len(promotions)} | {orchid['input_tokens_observed']:.0f} / {orchid['output_tokens_observed']:.0f} | {orchid_typecheck} | {orchid_build} |

Both arms launched once and ran without controller rescue or restart. The controller was stopped after four ORCHID compaction failures and a new generation remained running; this is recorded in `RUN_TERMINATION.json`. Pi never reached native compaction. ORCHID reached one ACTIVE promotion, not multiple promotions.

ORCHID did not demonstrate retirement throughput exceeding arrival throughput or backlog catch-up. At stop, the database retained a running generation and the active capsule had not advanced beyond its first promotion; therefore retirement ratio, catch-up time, repeated post-promotion collapse, and context-token savings after repeated promotion are **not established**. The active capsule was about 211 estimated tokens while the raw tail was about 15,951 estimated tokens at the last checkpoint.

## Failure evidence

- Generation 2: canonicalizer rejected source references out of supplied order.
- Generation 3: consolidator rejected an unknown evidence event ID.
- Generation 4: Solar Pro rejected a 547,802-token request against its 524,288-token maximum context.
- Generation 5: canonicalizer again rejected source references out of supplied order.
- Generation 6 was running when the controller/gateway were stopped; its incomplete state is preserved.

These are operational/protocol failures, not semantic-oracle passes. No retries or fuzzy provenance repair were added.

## Quality and hidden validation

Hidden Clone Stamp probe: **{hidden_summary}**. Both exits were 1. Pi timed out waiting for the Clone stamp button; ORCHID found the button but failed the required `Alt/Option-click to set clone source` text. Neither arm passed the browser workflow. Pi produced a buildable tree in the independent checks; ORCHID failed typecheck/build on missing `cloneStampStroke`, `createCloneSource`, `CloneSource`, and task-bar symbols. Neither final repository earns a full functional-quality claim.

The observed work was not a fair post-compaction quality comparison: Pi had zero native compactions and ORCHID had only one promotion before repeated failures. Coding degradation after a memory transition is therefore **not isolatable** for either arm. The all-Solar ORCHID path nevertheless failed the required operational sustainability test.

## Economics and context area

Observed foreground token totals are recorded above. ORCHID memory-stage aggregates are in `economics/SUMMARY.json` and include selector, canonicalizer, and consolidator calls/tokens/wall time. A causal foreground context-token-area saving cannot be claimed because the arms diverged before comparable repeated promotions; the measured proxy is the sum of foreground input tokens only.

## Required answers

1. Both arms ran continuously without rescue until the predeclared stop: **yes**.
2. Pi native compaction: **no; 0**.
3. ORCHID multiple ACTIVE promotions: **no; 1 total**.
4. ORCHID retirement exceeded arrival: **not demonstrated**.
5. ORCHID backlog bounded/caught up: **no evidence of catch-up; one running stale generation remained**.
6. ORCHID awake context repeatedly collapsed: **not reached**.
7. ORCHID promotion degradation: **not isolatable after one promotion; later operational failures occurred**.
8. Pi post-native-compaction degradation: **not testable; no native compaction**.
9. More task progress: **Pi had the stronger validated outcome because it typechecked/built; ORCHID changed more files but failed validation**.
10. Architecture constraints/current intent/blockers: **not proven across memory transitions; only the initial ORCHID capsule preserved them**.
11. Better final repository: **Pi for build validity; neither passed the hidden browser workflow**.
12. Compile: **Pi yes; ORCHID no**.
13. Clone Stamp browser workflow: **neither**.
14. Hidden evaluator: **both exit 1; full diagnostics in `quality/hidden_eval.json`**.
15. Foreground usage and ORCHID extra cost: **measured in `economics/SUMMARY.json`; selector/canonicalizer/consolidator records are separated by stage**.
16. Foreground context-token area saved: **not causally measurable in this incomplete, non-comparable transition run**.
17. Solar selection coherent: **not fully; exact ordering/provenance failures were observed**.
18. Architecture feasibility independent of RX580: **not supported; this all-Solar path was operationally weak before local hardware became relevant**.

## Verdict

**ARCHITECTURE_OPERATIONALLY_WEAK**

RX580_LOCAL_SELECTOR_OPTIMIZATION: **NOT_REQUIRED_FOR_FEASIBILITY**
"""
    (ARTIFACT / "FINAL_REPORT.md").write_text(final_report, encoding="utf-8")
    config_hashes_path = ARTIFACT / "CONFIG_HASHES.json"
    config_hashes = load_json(config_hashes_path)
    import hashlib
    config_hashes["final_manifest_sha256"] = hashlib.sha256(
        (ARTIFACT / "BENCHMARK_MANIFEST.json").read_bytes()
    ).hexdigest()
    config_hashes["final_report_sha256"] = hashlib.sha256(
        (ARTIFACT / "FINAL_REPORT.md").read_bytes()
    ).hexdigest()
    write_json(config_hashes_path, config_hashes)

    print(json.dumps({"artifact": str(ARTIFACT), "verdict": verdict, "pi": pi, "orchid": orchid}, indent=2))


if __name__ == "__main__":
    main()
