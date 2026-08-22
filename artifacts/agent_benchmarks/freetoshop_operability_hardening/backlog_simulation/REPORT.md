# Backlog simulation

## Measured facts

- Frozen novel-history arrival: `59.15` tokens/sec average.
- Peak 60-second arrival: `407.28` tokens/sec.
- Captured provider-backed retirement: `31.87325649401479` tokens/sec.

The JSONL traces use the captured arrival timestamps and the single observed provider-backed retirement rate. The old scheduler trace counts a pressure job for every pressure signal; the coalesced trace has at most one dirty indication. These traces intentionally do not invent a hidden model rate.
