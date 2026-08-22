"""Assemble Phase 3.2 reports without changing benchmark inputs or thresholds."""

from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/agent_benchmarks/freetoshop_pipeline_ablation"
REPLAY = ROOT / (
    "artifacts/agent_benchmarks/freetoshop_operability_hardening/"
    "frozen_freetoshop_replay/events.jsonl"
)
ARRIVAL = 59.148
TARGET = 75.0


def load(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def flatten_requests(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if "condition" in value and "status" in value and "wall_ms" in value:
            result.append(value)
        for child in value.values():
            result.extend(flatten_requests(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(flatten_requests(child))
    return result


def safe_rate(row: dict[str, Any]) -> float | None:
    if row.get("status") != "SUCCEEDED":
        return None
    value = row.get("source_tokens_per_second")
    return float(value) if value is not None else None


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "stall_analysis").mkdir(exist_ok=True)
    (OUT / "role_switch").mkdir(exist_ok=True)
    (OUT / "concurrency").mkdir(exist_ok=True)
    (OUT / "quality").mkdir(exist_ok=True)
    (OUT / "economics").mkdir(exist_ok=True)
    (OUT / "preflight").mkdir(exist_ok=True)

    # Keep deliberately unrun ablations explicit in the artifact tree.  The
    # generic runner must not silently turn an unsupported arm into a result.
    for name, reason in {
        "arm_d_role_affinity": "NOT_RUN: no validated persistent endpoint slot/session-affinity primitive was exposed by the configured LM Studio API.",
        "arm_e_pipelined": "NOT_RUN: no validated multi-slot endpoint configuration was available; a pipelined arm would confound the concurrency experiment.",
    }.items():
        arm_dir = OUT / "ablation" / name
        arm_dir.mkdir(parents=True, exist_ok=True)
        summary_path = arm_dir / "SUMMARY.json"
        if not summary_path.exists():
            arm_name = "ARM_D_ROLE_AFFINITY" if name.endswith("role_affinity") else "ARM_E_PIPELINED"
            summary_path.write_text(
                json.dumps({"arm": arm_name, "status": "NOT_RUN", "reason": reason}, indent=2) + "\n",
                encoding="utf-8",
            )
        report_path = arm_dir / "REPORT.md"
        if not report_path.exists():
            report_path.write_text(f"# {name}\n\n{reason}\n", encoding="utf-8")

    stall = load(OUT / "stall_analysis/SUMMARY.json", {"conditions": []})
    stall_rows = flatten_requests(stall.get("conditions", []))
    idle = [row for row in stall_rows if row.get("condition") == "A_idle"]
    timeout_count = sum(bool(row.get("timeout")) for row in stall_rows)
    stall_status = "MIXED_OR_PRIOR_TAIL" if timeout_count else "NO_REPRODUCTION_IN_CONTROLLED_TRIALS"
    stall_report = f"""# Third canonicalizer-request stall analysis

## Measured facts

- Frozen replay SHA-256: `{stall.get('replay_sha256')}`
- Exact batch index: `{stall.get('batch_index')}` of `{stall.get('batch_count')}`
- Exact batch source tokens: `{stall.get('source_tokens')}`
- Controlled requests recorded: `{len(stall_rows)}`
- Controlled successes: `{sum(row.get('status') == 'SUCCEEDED' for row in stall_rows)}`
- Controlled timeouts: `{timeout_count}`
- Idle trial latencies: `{[round(float(row.get('wall_ms') or 0) / 1000, 2) for row in idle]} seconds`
- Prior full replay outcome: the same exact batch timed out at the 180-second deadline.
- LM Studio log evidence for a successful equivalent request: approximately 40.16 seconds, 14,143 prompt tokens, 442 output tokens, and 11.08 output tokens/sec.

## Interpretation

The captured failure is not deterministic for the input: the exact isolated request completed twice in the controlled matrix, and all 24 contextual/alternating/concurrent requests completed. LM Studio reported `GENERATING` during the reproduction and emitted normal prediction statistics, so this was real inference activity rather than an immediately dead HTTP request.

The remaining evidence supports a load/runtime tail or endpoint scheduling event. It does not prove a KV-cache, grammar deadlock, or role-switch defect. Non-streaming mode provides no first-token progress signal, and LM Studio did not expose queue depth or KV-hit metrics through the exercised API.

## Required conclusion

Do not increase the deadline blindly. Keep the explicit bounded deadline and treat the prior timeout as an unresolved long-tail reliability risk until a larger repeated full-trace run establishes its frequency.
"""
    (OUT / "stall_analysis/REPORT.md").write_text(stall_report, encoding="utf-8")

    role = load(OUT / "role_switch/results.json", {})
    role_configs = role.get("configurations", [])
    role_report = """# Role switching versus role-affine logical clients

## Measured facts

"""
    for config in role_configs:
        role_report += (
            f"- `{config.get('config')}`: {config.get('iterations')} iterations, "
            f"{config.get('failures')} failures, {config.get('timeouts')} timeouts, "
            f"mean request wall time {float(config.get('mean_request_ms') or 0) / 1000:.2f}s.\n"
        )
    role_report += """
- TTFT was unavailable because the benchmark used non-streaming JSON requests.
- No KV/prefix-cache hit metric was exposed by the OpenAI-compatible endpoint or LM Studio CLI.

## Interpretation

The experiment can compare total request wall time and failure rate, but cannot claim prompt-prefill or KV-cache savings. Any difference without exposed prefill/cache counters is endpoint timing evidence only, not proof of role affinity.
"""
    (OUT / "role_switch/REPORT.md").write_text(role_report, encoding="utf-8")

    concurrency = load(OUT / "concurrency/results.json", {})
    parallel1 = load(OUT / "concurrency/results_parallel1.json", {})
    parallel2 = load(OUT / "concurrency/results_parallel2.json", {})
    concurrency_runs = [row for row in (parallel1, parallel2) if row]
    if concurrency_runs:
        (OUT / "concurrency/SUMMARY.json").write_text(
            json.dumps({"runs": concurrency_runs}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        with (OUT / "concurrency/results.jsonl").open("w", encoding="utf-8") as handle:
            for run in concurrency_runs:
                for measurement in run.get("measurements", []):
                    handle.write(json.dumps({"model": run.get("model"), **measurement}, ensure_ascii=False) + "\n")
    concurrency_report = """# Local endpoint concurrency

## Measured facts

"""
    report_runs = concurrency_runs or ([concurrency] if concurrency else [])
    for run in report_runs:
        for measurement in run.get("measurements", []):
            concurrency_report += (
                f"- model `{run.get('model', 'unspecified')}`, concurrency {measurement.get('concurrency')}: aggregate rate "
                f"{float(measurement.get('aggregate_source_tokens_per_second') or 0):.2f} source tok/s, "
                f"timeouts {measurement.get('timeouts')}, failures {measurement.get('failures')}.\n"
            )
    concurrency_report += """

The earlier controlled parallel=1 contextual pair completed in approximately 39.09s and 78.24s, demonstrating serialized endpoint queuing. A parallel=2 result is only accepted if the separately recorded endpoint status confirms the runtime setting.

## Interpretation

Overlapping client coroutines are not sufficient evidence of concurrent inference. The endpoint's configured parallel slots and per-request latency must be read together.
"""
    (OUT / "concurrency/REPORT.md").write_text(concurrency_report, encoding="utf-8")

    # Reuse the frozen Phase 3.1 batch sweep as prior evidence rather than
    # silently relabeling it as a new run.  The new phase adds the explicit
    # current-pipeline pilot and full-trace failure evidence below.
    prior_sweep = load(
        ROOT / "artifacts/agent_benchmarks/freetoshop_canonicalizer_throughput/batch_sweep/SUMMARY.json",
        {"results": []},
    )
    (OUT / "batch_sweep").mkdir(exist_ok=True)
    with (OUT / "batch_sweep/results.jsonl").open("w", encoding="utf-8") as handle:
        for row in prior_sweep.get("results", []):
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (OUT / "batch_sweep/SUMMARY.json").write_text(
        json.dumps(prior_sweep, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (OUT / "batch_sweep/REPORT.md").write_text(
        "# Canonicalizer batch-size evidence\n\n"
        "These are frozen Phase 3.1 corrected replay measurements reused as prior evidence. "
        "The best zero-timeout corrected 12-event result was 12K at 71.14 source tok/s; "
        "the 199-event 12K replay later failed at batch 2 with the 180s deadline. "
        "No new batch-size tuning was performed in Phase 3.2.\n",
        encoding="utf-8",
    )

    prior_profile = ROOT / "artifacts/agent_benchmarks/freetoshop_canonicalizer_throughput/stage_profile.jsonl"
    (OUT / "stage_profile.jsonl").write_text(
        prior_profile.read_text(encoding="utf-8") if prior_profile.exists() else "",
        encoding="utf-8",
    )
    profile_rows = []
    for name in ("ARM_A_CURRENT", "ARM_B_NO_CANONICALIZER", "ARM_C_DIRECT"):
        row = load(OUT / "ablation" / {"ARM_A_CURRENT": "arm_a_current", "ARM_B_NO_CANONICALIZER": "arm_b_no_canonicalizer", "ARM_C_DIRECT": "arm_c_direct"}[name] / "SUMMARY.json", {})
        if row.get("status") == "SUCCEEDED":
            for stage_name in ("selector", "canonicalizer", "direct_consolidator"):
                stage = row.get("stage", {}).get(stage_name, {})
                if stage.get("wall_ms") is not None:
                    profile_rows.append({
                        "arm": name,
                        "stage": stage_name,
                        "wall_ms": stage.get("wall_ms"),
                        "calls": stage.get("calls"),
                        "status": row.get("status"),
                        "source_tokens": row.get("source_tokens"),
                    })
    with (OUT / "stage_profile.jsonl").open("a", encoding="utf-8") as handle:
        for row in profile_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (OUT / "stage_summary.json").write_text(
        json.dumps({
            "prior_full_canonicalizer_batches": len(profile_rows),
            "note": "Full current-pipeline replay has no completed selector/consolidator stage breakdown because it stopped at canonicalizer batch 2; pilot stage percentages are reported in FINAL_REPORT.md.",
            "pilot_arm_files": ["ablation/arm_a_current/SUMMARY.json", "ablation/arm_b_no_canonicalizer/SUMMARY.json", "ablation/arm_c_direct/SUMMARY.json"],
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    (OUT / "PROFILE_REPORT.md").write_text(
        "# Phase 3.2 stage profile\n\n"
        "The full current pipeline did not complete, so a full-trace stage waterfall is not claimed. "
        "On the 12-event current-pipeline pilot, selector was approximately 20.3% of 208.5s, "
        "canonicalizer 75.7%, and consolidator 3.9%; software/assembly/validation made up the remainder. "
        "The canonicalizer is therefore the dominant measured stage in the current pipeline.\n",
        encoding="utf-8",
    )

    prior_amp = load(
        ROOT / "artifacts/agent_benchmarks/freetoshop_canonicalizer_throughput/amplification/token_breakdown.json",
        {},
    )
    (OUT / "amplification").mkdir(exist_ok=True)
    (OUT / "amplification/token_breakdown.json").write_text(
        json.dumps({"prior_phase": prior_amp, "note": "No new amplification change was introduced in Phase 3.2."}, indent=2) + "\n",
        encoding="utf-8",
    )
    (OUT / "amplification/REPORT.md").write_text(
        "# Input amplification\n\n"
        "The prior phase's token breakdown is preserved under token_breakdown.json. "
        "Phase 3.2 did not change prompts, schemas, or semantic stages, so it does not claim a new amplification reduction.\n",
        encoding="utf-8",
    )

    timeout_dir = OUT / "timeout_analysis"
    timeout_dir.mkdir(exist_ok=True)
    timeout_cases = []
    for row in stall_rows:
        if row.get("timeout") or row.get("status") != "SUCCEEDED":
            timeout_cases.append(row)
    timeout_cases.append({
        "source": "prior_full_replay",
        "batch_index": 2,
        "source_tokens": 11806,
        "timeout_seconds": 180,
        "prompt_hash": "96f9480724484aaac60b056057f6807d47ca08d69f1977c937e2b90ada63e591",
        "classification": "unresolved long-tail timeout; exact isolated replay later succeeded",
    })
    with (timeout_dir / "timeout_cases.jsonl").open("w", encoding="utf-8") as handle:
        for row in timeout_cases:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (timeout_dir / "REPORT.md").write_text(
        "# Canonicalizer timeout analysis\n\n"
        f"The prior full replay timeout is preserved, while the controlled exact/context matrix recorded {timeout_count} timeout(s) in {len(stall_rows)} requests. "
        "The exact request completed in the controlled trials, so input shape alone does not explain the failure. "
        "No evidence justifies blindly increasing the deadline; queue depth, KV-cache hits, and first-token progress were not exposed by the exercised API.\n",
        encoding="utf-8",
    )

    arm_paths = {
        "ARM_A_CURRENT": OUT / "ablation/arm_a_current/SUMMARY.json",
        "ARM_B_NO_CANONICALIZER": OUT / "ablation/arm_b_no_canonicalizer/SUMMARY.json",
        "ARM_C_DIRECT": OUT / "ablation/arm_c_direct/SUMMARY.json",
        "ARM_D_ROLE_AFFINITY": OUT / "ablation/arm_d_role_affinity/SUMMARY.json",
        "ARM_E_PIPELINED": OUT / "ablation/arm_e_pipelined/SUMMARY.json",
    }
    arms: list[dict[str, Any]] = []
    for name, path in arm_paths.items():
        row = load(path, {"arm": name, "status": "NOT_RUN", "error": "no arm artifact"})
        if row.get("source_item_count") == 317 or row.get("selected_source_ref_count") == 317 or row.get("quality", {}).get("expected_event_count") == 317:
            row["scope"] = "FULL_REPLAY"
        elif row.get("source_item_count") == 21 or row.get("quality", {}).get("expected_event_count") == 21:
            row["scope"] = "12_EVENT_PILOT"
        else:
            row["scope"] = row.get("scope", "NOT_RUN")
        arms.append(row)
    previous_current = load(
        ROOT / "artifacts/agent_benchmarks/freetoshop_canonicalizer_throughput/final_replay/canonicalizer_12k_full199.json",
        {},
    )
    previous_result = (previous_current.get("results") or [{}])[0]
    arms[0]["prior_full_replay_evidence"] = {
        "status": previous_result.get("status"),
        "source_tokens_planned": previous_result.get("source_tokens"),
        "planned_batches": previous_result.get("batch_count"),
        "attempted_batches": sum(batch.get("status") is not None for batch in previous_result.get("batches", [])),
        "timeout": any(batch.get("timeout") for batch in previous_result.get("batches", [])),
        "note": "The current-pipeline result in this phase is a 12-event pilot; the previous corrected 199-event replay remains the full-trace ARM A evidence.",
    }
    quality_rows = []
    for row in arms:
        quality_rows.append({"arm": row.get("arm"), "status": row.get("status"), "quality": row.get("quality", {}), "promotion": row.get("promotion", {})})
    with (OUT / "quality/results.jsonl").open("w", encoding="utf-8") as handle:
        for row in quality_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (OUT / "quality/REPORT.md").write_text(
        "# Semantic and provenance quality\n\n"
        "Software checks covered structured response shape, exact legal source references, source-parent mapping, and temporary CAS promotion. No deterministic frozen semantic judge exists for this coding trace, so current-state, supersession, and invention scores remain not independently scored. A faster arm without those semantic checks is not production-approved.\n",
        encoding="utf-8",
    )

    economics_rows = []
    for row in arms:
        economics_rows.append({
            "arm": row.get("arm"),
            "status": row.get("status"),
            "source_tokens": row.get("source_tokens"),
            "source_tokens_per_second": safe_rate(row),
            "local_model": row.get("local_model"),
            "solar_model": row.get("solar_model"),
            "promotion": row.get("promotion"),
        })
    economics = {
        "pricing_assumption": {"solar_input_usd_per_million": 0.03, "solar_output_usd_per_million": 0.12},
        "arms": economics_rows,
        "note": "Pricing is the frozen benchmark configuration from the prior OpenRouter Solar setup; local compute cost is represented by wall time/tokens and no energy estimate is claimed.",
    }
    (OUT / "economics/SUMMARY.json").write_text(json.dumps(economics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (OUT / "economics/REPORT.md").write_text(
        "# Economics\n\n"
        "Solar cost is computed from the frozen assumption of $0.03/M input tokens and $0.12/M output tokens. Local Qwen work is not treated as free: local wall time, input/output tokens, timeouts, and serialized/concurrent behavior are reported per arm.\n",
        encoding="utf-8",
    )

    (OUT / "preflight/SUMMARY.json").write_text(json.dumps({"status": "NOT_RUN", "reason": "No candidate has yet completed a full frozen replay with zero unresolved failures, semantic evaluation, and a rate above 75 tok/s."}, indent=2) + "\n", encoding="utf-8")
    (OUT / "preflight/REPORT.md").write_text("# Live preflight\n\nNot run. The Phase 3.2 gate requires a completed, correctness-checked full replay first.\n", encoding="utf-8")
    (OUT / "preflight/telemetry.jsonl").write_text("", encoding="utf-8")

    completed = [row for row in arms if row.get("status") == "SUCCEEDED" and row.get("scope") == "FULL_REPLAY" and safe_rate(row) is not None]
    pilot_completed = [row for row in arms if row.get("status") == "SUCCEEDED" and row.get("scope") == "12_EVENT_PILOT" and safe_rate(row) is not None]
    preserved_c_pilot = load(OUT / "ablation/arm_c_direct/PILOT_12_EVENT.json", {})
    best = max(completed, key=lambda row: float(safe_rate(row) or 0), default=None)
    candidate = best.get("arm") if best else None
    candidate_rate = safe_rate(best) if best else None
    full_a = previous_result.get("status") == "SUCCEEDED"
    summary = {
        "phase": "3.2_semantic_pipeline_ablation_stall_isolation_concurrency",
        "replay_sha256": hashlib.sha256(REPLAY.read_bytes()).hexdigest(),
        "arrival_tokens_per_second": ARRIVAL,
        "minimum_viable_tokens_per_second": TARGET,
        "stall_controlled_requests": len(stall_rows),
        "stall_controlled_timeouts": timeout_count,
        "arms": arms,
        "best_completed_arm": candidate,
        "best_completed_rate": candidate_rate,
        "pilot_results": [
            {"arm": row.get("arm"), "rate": safe_rate(row), "scope": row.get("scope"), "timeouts": (row.get("solar_model") or {}).get("timeouts", 0) + (row.get("local_model") or {}).get("timeouts", 0)}
            for row in pilot_completed
        ] + ([{"arm": "ARM_C_DIRECT", "rate": preserved_c_pilot.get("source_tokens_per_second"), "scope": "12_EVENT_PILOT", "timeouts": preserved_c_pilot.get("timeouts", 0)}] if preserved_c_pilot else []),
        "selected_pipeline": (
            "bounded raw -> Solar consolidator (ARM C), experimental candidate only; semantic judge and full-arm gate still required"
            if candidate == "ARM_C_DIRECT" else "no production pipeline selected"
        ),
        "live_preflight": "NOT_RUN",
        "recommendation": "NOT_READY_FOR_FULL_AB_RERUN",
    }
    (OUT / "SUMMARY.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def rate_text(row: dict[str, Any]) -> str:
        rate = safe_rate(row)
        return f"{rate:.2f}" if rate is not None else "not available"

    rates = "\n".join(
        f"- {row.get('arm')}: scope `{row.get('scope')}`, status `{row.get('status')}`, rate `{rate_text(row)}` source tok/s."
        for row in arms
    )
    final = f"""# ORCHID Phase 3.2 — Semantic pipeline ablation

## Recommendation

`NOT_READY_FOR_FULL_AB_RERUN`

## Measured facts

The frozen replay contains 199 events, 317 expanded source items, and 202,761 planned source tokens. The prior full current-pipeline replay completed two canonicalizer batches, then timed out on batch 2 at 180s; it did not produce a full-trace retirement rate.

{rates}

The completed 12-event pilots are not full-replay claims:

- ARM A current: 13,740 source tokens in 208.48s, 65.91 source tok/s, selector 42.29s, canonicalizer 158.00s, consolidator 8.16s, zero timeouts, exact-reference checks and temporary promotion passed.
- ARM B selector → direct Solar: 13,740 source tokens in 102.79s, 133.67 source tok/s, selector 41.79s, direct Solar 61.00s, zero timeouts, exact-reference checks and temporary promotion passed.
- ARM C bounded raw → direct Solar: the preserved 12-event pilot measured 115.87 source tok/s with zero timeouts and promotion; the full 199-event attempt failed after one 11,463-token direct batch when the second batch hit 180s.

The exact stall matrix recorded 24/24 successes and 0 timeouts: idle, after-selector, after-canonicalizer, three alternation cycles, and a concurrent pair. LM Studio showed real generation. This does not erase the prior full-replay timeout; it shows that the input is not a deterministic failure trigger.

Role-switch testing used a 4K fixture and two iterations per configuration. Shared-client role switching had 1 timeout in 4 requests; two persistent role clients had 0 timeouts in 4. Successful canonicalizer calls remained about 155–158s. TTFT and KV/prefix-cache hits were unavailable.

Concurrency testing used a 2K fixture. At the original `parallel=1`, one request was 63.00s / 27.43 tok/s and two overlapping requests were 105.70s aggregate / 32.70 tok/s. With a separately loaded `parallel=2` model identifier, one request was 145.98s / 11.84 tok/s and two were 171.11s aggregate / 20.20 tok/s. Both configurations had zero timeouts; parallelism did not improve throughput here.

## Interpretation

The current 12-event waterfall makes the canonicalizer the dominant measured stage at 75.7% of current-pipeline pilot wall time. The full-trace failure and role/concurrency probes point to a long-tail local inference/runtime issue, but do not identify a deadlock, deterministic malformed prompt, or cache-affinity mechanism. Persistent role clients reduced observed failure count in this small probe but did not improve successful canonicalizer latency enough to justify a production change.

The simplest promising ablation is bounded raw → Solar direct consolidation, followed by selector → direct Solar. Both pilots are faster than the current pipeline, but neither has a full-trace semantic evaluation. ARM C is therefore an experimental candidate only; no production semantic stage was removed.

## Final questions

1. Selector share: 20.3% of the 12-event current-pipeline pilot; full-trace share unavailable.
2. Canonicalizer share: 75.7% of that pilot; full-trace share unavailable because the replay stopped in canonicalization.
3. Consolidator share: 3.9% of that pilot; full-trace share unavailable.
4. Prior timeouts: an unresolved long-tail local inference/runtime event. Exact isolated/context requests later succeeded; queue depth, KV hits, and first-token progress were not exposed.
5. Best corrected canonicalizer batch size: 12K in the prior sweep at 71.14 source tok/s with zero timeouts on the 12-event fixture; it did not meet the 75 tok/s target on the full trace.
6. Smaller batches: 4K and 8K were slower in the prior corrected sweep (51.44 and 47.89 tok/s).
7. Larger batches: 12K improved amortization over 4K/8K; 16K was slower at 62.05 tok/s and did not establish a safe advantage.
8. Canonicalizer input amplification: 1.242x in the prior corrected 12K local replay (20,060 model-input tokens / 16,155 source tokens). Total semantic amplification was not available from one unified full trace.
9. Fixed prompt/schema overhead: approximately 420 system-prompt characters + 48 wrapper characters + 340–746 schema characters per canonicalizer call in the prior profile; the exact-ID schema component is correctness-constraining.
10. Safe prompt removal: none demonstrated; no schema/provenance weakening was attempted.
11. Concurrency 2: no improvement; the explicit `parallel=2` run was slower than the original `parallel=1` comparison.
12. Concurrency timeout risk: no timeout in the small paired runs, but long latency increased substantially; shared role switching did produce one timeout.
13. Timeout policy: retain a bounded 180s deadline until progress/queue telemetry exists; do not blindly increase it. Treat a timeout as a failed compaction that can be retried/recovered by existing scheduling.
14. Final frozen-replay rate: unavailable; ARM C failed on batch 2 and current ARM A had already failed on canonicalizer batch 2.
15. Above 75 tok/s: no completed full replay, so no.
16. Margin over 59.148 arrival: full-trace margin is unknown; pilot margins were +6.76 tok/s for ARM A, +74.52 for ARM B, and +56.72 for ARM C.
17. Semantic correctness: not independently established; no deterministic frozen semantic judge exists for this trace.
18. Provenance: exact source-reference subset and temporary promotion checks passed on completed pilots; the failed full ARM C did not reach promotion.
19. Live preflight with three promotions: not run because no full candidate passed the gate.
20. Live retirement versus arrival: not measured in this phase; no live preflight was authorized after the failed full replay.
21. Ready for another full A/B: no.

## Selected pipeline

No production pipeline is selected. The next experiment should be a full-trace ARM C/ARM B comparison with a semantic judge, not a production removal of canonicalization. The data supports testing bounded raw → Solar as the simplest candidate, but it does not prove semantic sufficiency or sustainable full-trace throughput.

## Limitations

- This phase did not add or change production memory semantics, retrieval, schemas, provenance validation, or scheduler behavior.
- Full ARM C is a real failure, not an omitted measurement; no full ARM B was launched after that failure.
- Pilot structural promotion is not a substitute for current-state, supersession, or invention scoring.
- The known unrelated selector-schema test expectation remains separate; the dynamic exact-ID selector schema was not weakened.
"""
    (OUT / "FINAL_REPORT.md").write_text(final, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
