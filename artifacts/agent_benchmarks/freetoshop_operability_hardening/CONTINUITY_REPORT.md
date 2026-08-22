# Stream continuity report

## Measured facts

- Deterministic fault-injection coverage: 120 request cycles.
- Fault cases covered: disconnect before first token, partial text, partial tool call, provider error frame, EOF/missing finish, HTTP error, and valid requests after failures.
- Every cycle emitted a terminal protocol frame and `[DONE]`; cleanup telemetry completed for every tested request.
- The cancellation-specific test also verified `client_cancelled` is distinct from provider failure and that the next request completed.

## Root cause from the captured run

The upstream stream ended without a normal `finish_reason`/`[DONE]` terminator. The gateway forwarded the incomplete stream as if it were a normal response, so Pi exhausted its retry path and settled. Pi then accepted a queued follow-up, but no subsequent provider request was emitted. The gateway log contains no post-follow-up POST, so this was not evidence of a gateway lock held across requests.

The fix makes incomplete/error streams produce a bounded synthetic error chunk plus `[DONE]`, records cleanup separately from the event journal, and keeps diagnostic persistence fail-open.

## Interpretation

The gateway-side continuity invariant is proven by deterministic tests and a 120-cycle soak. A real Solar failure followed by a Pi follow-up was not injected during the live preflight, so that exact end-to-end sequence remains a limitation rather than an unqualified production proof.
