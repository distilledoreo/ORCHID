from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    artifact = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "artifacts/agent_benchmarks/orchid_foreground_substitution_v1/preflight")
    telemetry = load_jsonl(artifact / "telemetry.jsonl")
    driver = load_json(artifact / "driver_summary.json") if (artifact / "driver_summary.json").exists() else {}
    rpc = driver.get("rpc_log") or driver.get("rpc_log") or []
    after_active = [row for row in telemetry if row.get("active")]
    provider_aligned = [
        row
        for row in after_active
        if isinstance(row.get("provider_input_tokens"), int) and row["provider_input_tokens"] > 0
    ]
    ratios = [row["estimator_vs_provider_ratio"] for row in provider_aligned if row.get("estimator_vs_provider_ratio")]

    def _rpc_type(item: dict[str, Any]) -> str:
        return str(item.get("type") or "")

    def _is_prompt(item: dict[str, Any]) -> bool:
        return _rpc_type(item) in {"prompt", "prompt"}

    def _is_follow_up(item: dict[str, Any]) -> bool:
        return _rpc_type(item) in {"follow_up", "follow_up"}

    def _collect_jobs(node: Any) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        if isinstance(node, dict):
            if node.get("status") in {"PROMOTED", "FAILED", "RUNNING", "QUEUED"} and node.get("id"):
                found.append(node)
            for value in node.values():
                found.extend(_collect_jobs(value))
        elif isinstance(node, list):
            for item in node:
                found.extend(_collect_jobs(item))
        return found

    collapses = []
    previous = None
    for row in telemetry:
        if previous and row.get("active") and not previous.get("active"):
            inbound = row.get("inbound_estimated_tokens") or 0
            assembled = row.get("assembled_estimated_tokens") or 0
            collapses.append(
                {
                    "request_id": row.get("request_id"),
                    "inbound_estimated_tokens": inbound,
                    "assembled_estimated_tokens": assembled,
                    "provider_input_tokens": row.get("provider_input_tokens"),
                    "collapse_vs_inbound_pct": round(100.0 * (1.0 - assembled / inbound), 2) if inbound else None,
                }
            )
        previous = row
        if row.get("active") and previous is row:
            pass
    # Collapse after each newly seen ACTIVE token size drop vs inbound.
    by_active_tokens = []
    last_active = None
    for row in after_active:
        current = row.get("active_tokens")
        if current != last_active:
            inbound = row.get("inbound_estimated_tokens") or 0
            assembled = row.get("assembled_estimated_tokens") or 0
            by_active_tokens.append(
                {
                    "active_tokens": current,
                    "inbound_estimated_tokens": inbound,
                    "assembled_estimated_tokens": assembled,
                    "provider_input_tokens": row.get("provider_input_tokens"),
                    "collapse_vs_inbound_pct": round(100.0 * (1.0 - assembled / inbound), 2) if inbound else None,
                }
            )
            last_active = current

    leak = [
        row.get("request_id")
        for row in after_active
        if int(row.get("non_protocol_covered_raw_tokens_forwarded") or 0) != 0
    ]
    idle_prompts = [item for item in rpc if _is_prompt(item) and str(item.get("id") or "").startswith("preflight-continue")]
    followups = [item for item in rpc if _is_follow_up(item)]
    stalls = [error for error in driver.get("errors") or [] if error.get("category") == "continuation_turn_start_timeout"]
    jobs = _collect_jobs(driver.get("orchid"))
    unique_jobs = {job.get("id"): job for job in jobs}
    promotions = sum(1 for job in unique_jobs.values() if job.get("status") == "PROMOTED")
    stop_reason = driver.get("reason") or driver.get("stop_reason")

    pass_criteria = {
        "promotions_ge_3": promotions >= 3,
        "post_promotion_turns_ge_5": int(driver.get("post_promotion_turns") or 0) >= 5,
        "non_protocol_covered_raw_forwarded_zero": not leak,
        "idle_continuation_used_prompt": bool(idle_prompts),
        "no_controller_stall": not stalls,
        "no_manual_intervention": stop_reason in {
            "preflight_gate_satisfied",
            "wall_clock_limit",
            "controller_stall_no_turn_start",
            "unexpected_process_exit",
        },
        "provider_tokens_present": bool(provider_aligned),
        "assembled_tracks_provider": bool(ratios) and min(ratios) >= 0.4 and max(ratios) <= 2.5,
        "material_collapse_after_promotion": bool(by_active_tokens)
        and all((row.get("collapse_vs_inbound_pct") or 0) >= 15 for row in by_active_tokens),
    }
    passed = all(
        pass_criteria[key]
        for key in (
            "promotions_ge_3",
            "post_promotion_turns_ge_5",
            "non_protocol_covered_raw_forwarded_zero",
            "no_controller_stall",
            "provider_tokens_present",
            "assembled_tracks_provider",
            "material_collapse_after_promotion",
        )
    )
    summary = {
        "status": "PASS" if passed and stop_reason == "preflight_gate_satisfied" else "FAIL",
        "stop_reason": stop_reason,
        "promotions": promotions,
        "post_promotion_turns": driver.get("post_promotion_turns"),
        "foreground_requests": len(telemetry),
        "active_foreground_requests": len(after_active),
        "idle_prompt_continuations": len(idle_prompts),
        "follow_up_continuations": len(followups),
        "controller_stalls": stalls,
        "mean_inbound_estimated_tokens": round(mean(row["inbound_estimated_tokens"] for row in telemetry), 1) if telemetry else None,
        "mean_assembled_estimated_tokens": round(mean(row["assembled_estimated_tokens"] for row in telemetry), 1) if telemetry else None,
        "mean_provider_input_tokens": round(mean(row["provider_input_tokens"] for row in provider_aligned), 1) if provider_aligned else None,
        "mean_estimator_vs_provider_ratio": round(mean(ratios), 4) if ratios else None,
        "collapse_after_active_changes": by_active_tokens,
        "non_protocol_covered_raw_forwarded_request_ids": leak,
        "pass_criteria": pass_criteria,
        "historical_note": {
            "replay_corrected_orchid_cumulative": 2_390_898,
            "historical_pi_cumulative": 1_811_513,
            "historical_additive_orchid_cumulative": 7_864_313,
        },
    }
    dump_json(artifact / "SUMMARY.json", summary)
    lines = [
        "# Live foreground substitution preflight",
        "",
        f"Status: **{summary['status']}** ({summary['stop_reason']})",
        "",
        "## Comparison chain",
        "",
        f"- Mean inbound Pi transcript estimate: {summary['mean_inbound_estimated_tokens']}",
        f"- Mean assembled ORCHID estimate: {summary['mean_assembled_estimated_tokens']}",
        f"- Mean Solar `input_tokens`: {summary['mean_provider_input_tokens']}",
        f"- Mean assembled/provider ratio: {summary['mean_estimator_vs_provider_ratio']}",
        "",
        "## Gate",
        "",
    ]
    for key, value in pass_criteria.items():
        lines.append(f"- `{key}`: {'yes' if value else 'NO'}")
    lines.extend(["", "## Collapse at ACTIVE changes", ""])
    for row in by_active_tokens:
        lines.append(
            f"- active_tokens={row['active_tokens']}: inbound {row['inbound_estimated_tokens']} → assembled {row['assembled_estimated_tokens']} / provider {row['provider_input_tokens']} ({row['collapse_vs_inbound_pct']}% vs inbound)"
        )
    (artifact / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
