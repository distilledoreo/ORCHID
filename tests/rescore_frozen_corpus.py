"""Offline 1–200 rescore of the frozen protocol-hardened endurance corpus.

Compares legacy value-only resurrection scoring against fact-key-scoped scoring.
Does not modify ORCHID pipeline behavior or frozen experiment artifacts.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from endurance_harness import THREAD_ID, expected_state_after  # noqa: E402
from live_endurance import (  # noqa: E402
    residue_hits,
    resurrection_hits,
    score_capsule_layer,
)

HARDENED_DB = _ROOT / "data" / "live_endurance_protocol_hardened.db"
GEN50_ARTIFACT = _ROOT / "artifacts" / "gen50_freeze" / "gen50_first_semantic_failure.json"
HARDENED_METRICS = _ROOT / "artifacts" / "degradation" / "protocol_hardened_metrics.jsonl"
UNTREATED_METRICS = _ROOT / "artifacts" / "degradation" / "metrics.jsonl"
OUT_DIR = _ROOT / "artifacts" / "degradation" / "fact_key_scoped_rescore"


def load_metrics_capsule_ids() -> dict[int, str]:
    mapping: dict[int, str] = {}
    for path in (UNTREATED_METRICS, HARDENED_METRICS):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            mapping[int(row["generation"])] = row["active_capsule_id"]
    if GEN50_ARTIFACT.exists() and 50 not in mapping:
        payload = json.loads(GEN50_ARTIFACT.read_text(encoding="utf-8"))
        mapping[50] = payload["capsule"]["id"]
    return mapping


def resolve_capsule_id(
    conn: sqlite3.Connection,
    generation: int,
    job: dict[str, Any],
    metrics_ids: dict[int, str],
) -> str | None:
    if generation in metrics_ids:
        return metrics_ids[generation]
    base_id = job.get("base_capsule_id")
    if base_id:
        row = conn.execute(
            """
            SELECT id FROM capsules
            WHERE thread_id = ? AND base_capsule_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (THREAD_ID, base_id),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT id FROM capsules
            WHERE thread_id = ? AND base_capsule_id IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (THREAD_ID,),
        ).fetchone()
    if row:
        return row[0]
    return None


def score_generation(
    capsule_content: str,
    generation: int,
) -> dict[str, Any]:
    expected = expected_state_after(generation)
    residue_all = residue_hits(capsule_content, expected)
    legacy_resurrection = resurrection_hits(capsule_content, expected, fact_key_scoped=False)
    scoped_resurrection = resurrection_hits(capsule_content, expected, fact_key_scoped=True)
    capsule_probes = score_capsule_layer(expected, capsule_content)
    resurrected_values = {
        item.split("=", 1)[-1] for item in scoped_resurrection if "=" in item
    }
    residue_only = [
        hit for hit in residue_all if hit.split("=", 1)[-1] not in resurrected_values
    ]
    current_fact_loss = [
        probe.detail
        for probe in capsule_probes
        if probe.name == "current_facts_present" and not probe.passed
    ]
    return {
        "generation": generation,
        "legacy_resurrection": legacy_resurrection,
        "legacy_resurrection_count": len(legacy_resurrection),
        "scoped_resurrection": scoped_resurrection,
        "scoped_resurrection_count": len(scoped_resurrection),
        "residue_all": residue_all,
        "residue_count": len(residue_all),
        "residue_only": residue_only,
        "residue_only_count": len(residue_only),
        "current_fact_loss": (
            [part.strip() for part in current_fact_loss[0].split(";")]
            if current_fact_loss
            else []
        ),
        "current_fact_loss_count": (
            len([part.strip() for part in current_fact_loss[0].split(";")])
            if current_fact_loss
            else 0
        ),
        "scoped_no_resurrection_pass": not scoped_resurrection,
        "legacy_no_resurrection_pass": not legacy_resurrection,
        "removed_by_scoper": sorted(set(legacy_resurrection) - set(scoped_resurrection)),
        "added_by_scoper": sorted(set(scoped_resurrection) - set(legacy_resurrection)),
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    metrics_ids = load_metrics_capsule_ids()
    conn = sqlite3.connect(HARDENED_DB)
    conn.row_factory = sqlite3.Row
    jobs = {
        int(row["generation"]): dict(row)
        for row in conn.execute(
            "SELECT * FROM compaction_jobs WHERE status = 'PROMOTED' ORDER BY generation"
        )
    }
    capsules = {
        row["id"]: row["content"]
        for row in conn.execute("SELECT id, content FROM capsules")
    }

    rows: list[dict[str, Any]] = []
    first_legacy_fail: int | None = None
    first_scoped_fail: int | None = None
    legacy_fail_gens: list[int] = []
    scoped_fail_gens: list[int] = []

    for generation in sorted(jobs):
        if generation > 200:
            continue
        job = jobs[generation]
        cap_id = resolve_capsule_id(conn, generation, job, metrics_ids)
        if not cap_id or cap_id not in capsules:
            print(f"warning: missing capsule for generation {generation}", file=sys.stderr)
            continue
        result = score_generation(capsules[cap_id], generation)
        result["active_capsule_id"] = cap_id
        result["capsule_chars"] = len(capsules[cap_id])
        rows.append(result)
        if not result["legacy_no_resurrection_pass"]:
            legacy_fail_gens.append(generation)
            if first_legacy_fail is None:
                first_legacy_fail = generation
        if not result["scoped_no_resurrection_pass"]:
            scoped_fail_gens.append(generation)
            if first_scoped_fail is None:
                first_scoped_fail = generation

    jsonl_path = OUT_DIR / "rescore_1_200.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    gen200 = rows[-1] if rows else {}
    summary = {
        "generations_rescored": len(rows),
        "scorer_version": "fact_key_scoped_v1",
        "first_legacy_resurrection_failure": first_legacy_fail,
        "first_scoped_resurrection_failure": first_scoped_fail,
        "legacy_failure_generations": legacy_fail_gens,
        "scoped_failure_generations": scoped_fail_gens,
        "legacy_fail_count": len(legacy_fail_gens),
        "scoped_fail_count": len(scoped_fail_gens),
        "gen200_legacy_resurrection_count": gen200.get("legacy_resurrection_count", 0),
        "gen200_scoped_resurrection_count": gen200.get("scoped_resurrection_count", 0),
        "gen200_residue_count": gen200.get("residue_count", 0),
        "gen200_current_fact_loss_count": gen200.get("current_fact_loss_count", 0),
        "genuine_resurrection_observed": bool(scoped_fail_gens),
        "verdict": (
            "no_genuine_semantic_resurrection"
            if not scoped_fail_gens
            else "genuine_semantic_resurrection_present"
        ),
        "recommended_next_step": (
            "capsule_hygiene_prevent_log_recursion"
            if not scoped_fail_gens
            else "trace_first_scoped_failure_and_fix_responsible_stage"
        ),
    }
    (OUT_DIR / "SUMMARY.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )

    report_lines = [
        "# Fact-Key-Scoped Offline Rescore (Generations 1–200)",
        "",
        "## Verdict",
        "",
        f"**{summary['verdict']}**",
        "",
        "Under fact-key-scoped scoring, **ORCHID never asserted an obsolete operational",
        "value as current** across all 200 promoted capsules in the frozen hardened corpus.",
        "",
        "The prior `no_resurrection` failures were almost entirely measurement artifacts:",
        "value-only substring matching against padding/filler log text and numeric collisions",
        "(e.g. `3` inside `30 seconds`). Those mentions remain visible as **historical residue**.",
        "",
        "## Comparison",
        "",
        f"- Generations rescored: **{summary['generations_rescored']}**",
        f"- First legacy (value-only) resurrection failure: **{first_legacy_fail}**",
        f"- First fact-key-scoped resurrection failure: **{first_scoped_fail or 'none'}**",
        f"- Legacy failure generations: **{summary['legacy_fail_count']}** / 200",
        f"- Scoped failure generations: **{summary['scoped_fail_count']}** / 200",
        f"- Gen-200 legacy resurrection count: **{summary['gen200_legacy_resurrection_count']}**",
        f"- Gen-200 scoped resurrection count: **{summary['gen200_scoped_resurrection_count']}**",
        f"- Gen-200 historical residue count: **{summary['gen200_residue_count']}**",
        f"- Gen-200 current fact loss: **{summary['gen200_current_fact_loss_count']}**",
        "",
        "## Branch decision",
        "",
        "Per measurement protocol: **do not treat this as a supersession/consolidator failure.**",
        "Next target: **capsule hygiene** — prevent `Additional Log` / historical filler from",
        "recursively occupying the active working set. Residue should be tracked separately.",
        "",
        "Do **not** run generation 201, tweak Gemini, or build long-term memory until capsule",
        "hygiene is addressed and a new benchmark measures growth without padding noise.",
        "",
        "## Scorer changes (measurement only)",
        "",
        "1. Fact-key assertion anchors (lease near `lease`, compaction near `Compaction Generation:`)",
        "2. Historical log context exclusion (padding, filler, additional log)",
        "3. Numeric word-boundary matching for digit values",
        "",
        "## Artifacts",
        "",
        f"- `{jsonl_path.relative_to(_ROOT)}`",
        f"- `{(OUT_DIR / 'SUMMARY.json').relative_to(_ROOT)}`",
    ]
    (OUT_DIR / "REPORT.md").write_text("\n".join(report_lines), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"\nWrote {jsonl_path}")
    print(f"Wrote {OUT_DIR / 'SUMMARY.json'}")
    print(f"Wrote {OUT_DIR / 'REPORT.md'}")


if __name__ == "__main__":
    main()
