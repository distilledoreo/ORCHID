# Preserved harness interruption

The first execution completed all 12 Arm A exact-repeat calls successfully and flushed them to `telemetry.jsonl`. It stopped before issuing any Arm B or Arm C request because the benchmark harness incorrectly treated frozen integer chunk indices as chunk objects during the A-to-B transition.

Observed failure: `TypeError: 'int' object is not subscriptable` while building `selected_chunks`.

This was a local harness error, not a provider timeout, selector protocol failure, or semantic result. The 12 successful Arm A calls were not retried. A corrected continuation issued only the not-yet-run Arm B and Arm C calls from the same frozen plan.
