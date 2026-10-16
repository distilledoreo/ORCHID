"""Replay FreetoShop ORCHID foreground assembly on captured Pi telemetry.

Does not call a provider. Reconstructs inbound OpenAI-style history from
orchid/telemetry.jsonl, ingests it into a fresh journal, applies recorded
ACTIVE watermarks at promotion finish times, and compares additive vs
substitutive assembly.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from memory_gateway.context import ContextAssembler, estimate_tokens
from memory_gateway.db import SQLiteStore
from memory_gateway.ingestion import append_novel_messages

RUN = ROOT / "artifacts/agent_benchmarks/freetoshop_pi_vs_orchid_corrected_all_solar"
OUT = ROOT / "artifacts/agent_benchmarks/orchid_foreground_substitution_v1"


def _iso_ms(value: str | None) -> int | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def _text_from_content(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif "text" in item:
                    parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return json.dumps(content)


def pi_message_to_openai(message: dict) -> dict | None:
    role = message.get("role")
    if role == "user":
        return {"role": "user", "content": _text_from_content(message.get("content"))}
    if role == "toolResult":
        return {
            "role": "tool",
            "tool_call_id": message.get("toolCallId"),
            "content": _text_from_content(message.get("content")),
        }
    if role != "assistant":
        return None
    texts: list[str] = []
    calls: list[dict] = []
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            if kind == "text":
                texts.append(str(part.get("text") or ""))
            elif kind == "toolCall":
                arguments = part.get("arguments")
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments or {})
                calls.append(
                    {
                        "id": part.get("id"),
                        "type": "function",
                        "function": {
                            "name": part.get("name") or "unknown",
                            "arguments": arguments,
                        },
                    }
                )
    else:
        texts.append(_text_from_content(content))
    converted: dict = {"role": "assistant", "content": "".join(texts) or None}
    if calls:
        converted["tool_calls"] = calls
    return converted


def _promote(store: SQLiteStore, end_event_id: str, content: str, generation: int) -> None:
    events = store.list_events("orchid-arm-b")
    if not events:
        return
    start_id = events[0]["id"]
    digest = f"replay-gen-{generation}"
    current = store.get_active_capsule("orchid-arm-b")
    capsule_id = store.create_capsule(
        thread_id="orchid-arm-b",
        base_capsule_id=current["id"] if current else None,
        content=content,
        source_event_hash=digest,
        input_hash=digest,
        output_hash=digest,
        capsule_hash=digest,
        snapshot_start_event_id=start_id,
        snapshot_end_event_id=end_event_id,
        covered_start_event_id=start_id,
        covered_end_event_id=end_event_id,
    )
    assert store.mark_capsule_ready(capsule_id)
    assert store.promote_capsule_cas("orchid-arm-b", capsule_id)


def old_additive_tokens(assembler: ContextAssembler, inbound: list[dict], capsule_content: str | None) -> int:
    inbound_tokens = sum(max(1, len(json.dumps(message)) // 4) for message in inbound)
    capsule_tokens = estimate_tokens(capsule_content) if capsule_content else 0
    tail = assembler._tail("orchid-arm-b", after_sequence=None, apply_minimum=True)
    tail_tokens = sum(event["token_count"] or estimate_tokens(event["content"]) for event in tail)
    return inbound_tokens + capsule_tokens + tail_tokens


def load_promotions() -> list[dict]:
    path = RUN / "memory_transitions/orchid_promotions.jsonl"
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    rows.sort(key=lambda row: row.get("generation", 0))
    return rows


def main() -> int:
    promotions = load_promotions()
    db_path = OUT / "replay" / "replay.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    store = SQLiteStore(str(db_path))
    store.create_project("freetoshop")
    store.create_thread("orchid-arm-b", "freetoshop")
    assembler = ContextAssembler(store)

    transcript: list[dict] = []
    pending = list(promotions)
    applied: list[dict] = []
    capsule_content: str | None = None
    per_request: list[dict] = []
    request_index = 0

    telemetry = RUN / "orchid/telemetry.jsonl"
    for raw in telemetry.open(encoding="utf-8"):
        event = json.loads(raw)
        kind = event.get("type")
        if kind == "message_end":
            converted = pi_message_to_openai(event.get("message") or {})
            if converted is None:
                continue
            transcript.append(converted)
            append_novel_messages(
                store,
                project_id="freetoshop",
                thread_id="orchid-arm-b",
                messages=transcript,
                request_id=f"msg-{len(transcript)}",
                source="replay",
            )
            wall = event.get("message", {}).get("timestamp")
            while pending:
                promo = pending[0]
                finished = _iso_ms(promo.get("finished_at"))
                events = store.list_events("orchid-arm-b")
                covered = next(
                    (
                        item
                        for item in events
                        if int(item["sequence"]) == int(promo["end_sequence"])
                    ),
                    None,
                )
                if covered is None:
                    break
                if wall is not None and finished is not None and int(wall) < finished:
                    break
                content = (
                    f"ACTIVE generation {promo['generation']} "
                    f"covering sequences {promo['start_sequence']}-{promo['end_sequence']} "
                    + ("x" * max(0, int(promo.get("capsule_chars") or 40)))
                )
                _promote(store, covered["id"], content, int(promo["generation"]))
                capsule_content = content
                applied.append(promo)
                pending.pop(0)
        elif kind == "turn_start":
            request_index += 1
            snapshot = assembler.assemble("orchid-arm-b", transcript)
            substitution = snapshot.substitution
            old_tokens = old_additive_tokens(assembler, transcript, capsule_content)
            record = {
                "request_index": request_index,
                "inbound_messages": len(transcript),
                "old_foreground_tokens": old_tokens,
                "new_foreground_tokens": snapshot.estimated_tokens,
                "active": substitution.active if substitution else False,
                "active_generation": applied[-1]["generation"] if applied else 0,
                "active_tokens": substitution.active_tokens if substitution else 0,
                "uncovered_raw_tokens": substitution.uncovered_tail_tokens if substitution else 0,
                "suppressed_covered_tokens": substitution.suppressed_covered_tokens if substitution else 0,
                "protocol_continuity_tokens": substitution.protocol_continuity_tokens if substitution else 0,
                "covered_raw_tokens_forwarded": substitution.covered_raw_tokens_forwarded if substitution else 0,
                "non_protocol_covered_raw_tokens_forwarded": 0,
                "duplicate_overlap_tokens_eliminated": max(
                    0, old_tokens - snapshot.estimated_tokens
                ),
                "collapse_pct_vs_old": (
                    round(100.0 * (1.0 - snapshot.estimated_tokens / old_tokens), 2)
                    if old_tokens
                    else 0.0
                ),
            }
            per_request.append(record)

    post = [row for row in per_request if row["active"]]
    uncovered_violation = [
        row["request_index"]
        for row in post
        if row["non_protocol_covered_raw_tokens_forwarded"] != 0
        or row["covered_raw_tokens_forwarded"] != row["protocol_continuity_tokens"]
    ]
    collapse_by_generation = []
    for generation in range(1, 6):
        before = [row for row in per_request if row["active_generation"] == generation - 1]
        after = [row for row in per_request if row["active_generation"] == generation]
        if not after:
            continue
        pre_tokens = before[-1]["new_foreground_tokens"] if before else after[0]["old_foreground_tokens"]
        post_tokens = after[0]["new_foreground_tokens"]
        collapse_by_generation.append(
            {
                "generation": generation,
                "pre_promotion_new_tokens": pre_tokens,
                "immediate_post_promotion_new_tokens": post_tokens,
                "immediate_post_old_tokens": after[0]["old_foreground_tokens"],
                "percentage_collapse_vs_old": after[0]["collapse_pct_vs_old"],
                "percentage_collapse_vs_pre_new": (
                    round(100.0 * (1.0 - post_tokens / pre_tokens), 2) if pre_tokens else 0.0
                ),
            }
        )

    summary = {
        "source_run": str(RUN),
        "requests": len(per_request),
        "promotions_applied": [row["generation"] for row in applied],
        "old_mean_foreground_tokens": round(
            sum(row["old_foreground_tokens"] for row in per_request) / max(1, len(per_request)), 1
        ),
        "new_mean_foreground_tokens": round(
            sum(row["new_foreground_tokens"] for row in per_request) / max(1, len(per_request)), 1
        ),
        "old_cumulative_foreground_tokens": sum(row["old_foreground_tokens"] for row in per_request),
        "new_cumulative_foreground_tokens": sum(row["new_foreground_tokens"] for row in per_request),
        "historical_observed_orchid_cumulative_input": 7_864_313,
        "historical_observed_pi_cumulative_input": 1_811_513,
        "covered_raw_tokens_forwarded_nonzero_requests": [
            row["request_index"] for row in post if row["covered_raw_tokens_forwarded"] != 0
        ],
        "offline_gate_non_protocol_covered_raw_forwarded_zero": not uncovered_violation,
        "replay_limitation": (
            "Pi telemetry reconstructed 131 chat messages / 57 turns. "
            "Historical watermarks 4-5 end at sequences 169 and 278 because the "
            "live gateway ingested additional request/response copies. Those two "
            "promotions cannot be applied onto the reconstructed journal."
        ),
        "collapse_by_generation": collapse_by_generation,
        "cache_observation": {
            "active_prefix_stable_across_generation": False,
            "note": "ACTIVE content changes at each promotion; raw tail and current suffix still grow. No cache redesign.",
        },
    }

    (OUT / "replay").mkdir(parents=True, exist_ok=True)
    (OUT / "replay" / "SUMMARY.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with (OUT / "replay" / "per_request.jsonl").open("w", encoding="utf-8") as handle:
        for row in per_request:
            handle.write(json.dumps(row) + "\n")
    print(json.dumps({k: summary[k] for k in summary if k != "collapse_by_generation"}, indent=2))
    print(json.dumps(collapse_by_generation, indent=2))
    return 0 if not uncovered_violation else 1


if __name__ == "__main__":
    raise SystemExit(main())
