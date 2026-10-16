# Third canonicalizer-request stall analysis

## Measured facts

- Frozen replay SHA-256: `6feb697961f22654d813159d07e2e519492b9ec431cdf8df08ae549508c26b16`
- Exact batch index: `2` of `18`
- Exact batch source tokens: `11806`
- Controlled requests recorded: `24`
- Controlled successes: `24`
- Controlled timeouts: `0`
- Idle trial latencies: `[38.56, 39.04] seconds`
- Prior full replay outcome: the same exact batch timed out at the 180-second deadline.
- LM Studio log evidence for a successful equivalent request: approximately 40.16 seconds, 14,143 prompt tokens, 442 output tokens, and 11.08 output tokens/sec.

## Interpretation

The captured failure is not deterministic for the input: the exact isolated request completed twice in the controlled matrix, and all 24 contextual/alternating/concurrent requests completed. LM Studio reported `GENERATING` during the reproduction and emitted normal prediction statistics, so this was real inference activity rather than an immediately dead HTTP request.

The remaining evidence supports a load/runtime tail or endpoint scheduling event. It does not prove a KV-cache, grammar deadlock, or role-switch defect. Non-streaming mode provides no first-token progress signal, and LM Studio did not expose queue depth or KV-hit metrics through the exercised API.

## Required conclusion

Do not increase the deadline blindly. Keep the explicit bounded deadline and treat the prior timeout as an unresolved long-tail reliability risk until a larger repeated full-trace run establishes its frequency.
