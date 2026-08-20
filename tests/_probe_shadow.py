"""Probe model_runs telemetry for shadow diagnostic planning."""
import json
import sqlite3
from pathlib import Path

DB = Path("data/live_endurance_protocol_hardened.db")
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

for gen in [25, 50, 75, 100, 125, 150, 175, 200]:
    job = conn.execute(
        "SELECT id, base_capsule_id, snapshot_start_event_id, snapshot_end_event_id FROM compaction_jobs WHERE generation=? AND status='PROMOTED'",
        (gen,),
    ).fetchone()
    if not job:
        print(f"gen {gen}: no job")
        continue
    runs = conn.execute(
        "SELECT stage, status, model, endpoint, prompt_version, input_hash, output_hash, input_tokens, output_tokens, diagnostic_excerpt, metadata_json, generation_settings_json FROM model_runs WHERE job_id=? ORDER BY created_at",
        (job["id"],),
    ).fetchall()
    print(f"\n=== gen {gen} job {job['id']} ===")
    for r in runs:
        meta = json.loads(r["metadata_json"] or "{}")
        excerpt = r["diagnostic_excerpt"]
        print(
            r["stage"],
            r["status"],
            r["model"],
            "in_tok",
            r["input_tokens"],
            "excerpt_len",
            len(excerpt) if excerpt else 0,
        )
        if r["stage"] == "consolidator" and r["status"] == "SUCCEEDED":
            print("  input_hash", r["input_hash"][:24])
            print("  settings", r["generation_settings_json"][:120])
            if excerpt:
                print("  excerpt_head", excerpt[:200])
