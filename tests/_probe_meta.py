import json
import sqlite3

conn = sqlite3.connect("data/live_endurance_protocol_hardened.db")
conn.row_factory = sqlite3.Row
for gen in [50, 100]:
    job = conn.execute(
        "SELECT id FROM compaction_jobs WHERE generation=?", (gen,)
    ).fetchone()
    for stage in ("selector", "canonicalizer", "consolidator"):
        r = conn.execute(
            "SELECT metadata_json, source_refs_json, output_hash, input_hash, endpoint, model "
            "FROM model_runs WHERE job_id=? AND stage=? AND status='SUCCEEDED'",
            (job["id"], stage),
        ).fetchone()
        if r:
            print(gen, stage, "endpoint", r["endpoint"], "model", r["model"])
            print("  meta keys", list(json.loads(r["metadata_json"]).keys()) if r["metadata_json"] else [])
