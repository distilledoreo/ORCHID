# Controller continuation

## Bug reproduced (prior sealed run)

On `agent_settled` the driver always sent `{ type: "follow_up" }`. Pi queues `follow_up` and only starts an idle turn on `prompt`. Observed: queue accept, no `message_start`, ~10 minutes silence.

## Fix

`tools/pi_continuation_policy.mjs` + `tools/freetoshop_corrected_all_solar_driver.mjs`:

- idle/settled → `prompt`
- busy (`turn_start` / `message_start` / `agent_start`) → `follow_up`
- watchdog requires turn-start evidence within 30s; stall is classified, not rescued by extra prompts
- process relaunch resets idle + watchdog

## Tests

`tests/test_pi_continuation_policy.py` drives the JS module via Node.

Live turn-start evidence for every continuation was **not** collected (no live preflight this phase).
