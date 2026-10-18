# Permanent-omission control

Same frozen incoming conversations, models, frozen adaptive policy, and enabled student/target caches. Both arms retain the full current question. The control permanently denies source chunks omitted on first arrival; no placeholders or retrieval tools are enabled. Each arm carries its own actual replies forward.

Measured setup: Qwen3.5-0.8B and Flash-Next Swift IQ3_S on Tesla P100-PCIE-16GB 16384 MiB.

Latency includes archive, student selection, delivery, and target prefill through the first answer token, excluding model startup. The measured run keeps both models resident.

| Arm | Correct | Correct on paired recovery-needed turns | Growing-turn mean TTFT |
|---|---:|---:|---:|
| append-none | 16/16 | 3/3 | 5.276 s |
| append-no-recovery | 12/16 | 0/3 | 3.490 s |

Initial target prompts and selector outputs matched exactly in 4/4 conversations. All 32 target prompt hashes reconstructed from append-only transcripts; current questions survived. 153 delivered source byte spans verified; no blocked chunk was ever delivered. Growing turns with zero target reuse: 0 with recovery, 0 without recovery.

| Control turn (zero-based) | Correct | Expected answer already in prompt | Blocked selected chunks |
|---|---:|---:|---:|
| held-recovery / 1 | False | False | 8 |
| held-recovery / 2 | False | False | 3 |
| held-recovery / 3 | False | False | 0 |
| held-versions / 2 | False | False | 2 |

The paired recovery-needed subset is defined by the recovery-enabled arm, before comparing control correctness. The additional control-only repeated question is shown separately above. Answer visibility is a literal byte check performed after inference for auditing, never a selector input.

This tests whether recovery matters on these existing cases. It is not fresh held-out validation: four synthetic lookup/version conversations, one sample per arm/turn, fixed arm order. Later selectors can differ because each arm retains its actual replies. Faster incomplete answers are not an accuracy-matched speedup.
