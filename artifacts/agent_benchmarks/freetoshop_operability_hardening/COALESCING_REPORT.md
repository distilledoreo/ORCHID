# Compaction coalescing report

## Measured facts

- Fifty pressure signals while one job was RUNNING produced one RUNNING job, zero queued duplicates, and one durable dirty indication.
- After the running promotion, the dirty state caused exactly one fresh snapshot job from the current watermark.
- Legacy stale queued snapshots are safely parked by the coalescing admission path; they do not remain eligible for overlapping work.
- The 120-cycle stream soak did not show a scheduler wedge.

## Interpretation

The control state is now bounded per thread: one active worker plus one durable “more work exists” condition. The scheduler does not claim that bounded scheduling alone makes a provider-limited compactor keep up; the replay rate comparison below is the separate throughput gate.
