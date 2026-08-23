# Provider usage and economics

Pi foreground observed input/output: **3000735 / 115427**.

ORCHID foreground observed input/output: **15965998 / 46882**.

ORCHID selector/canonicalizer/consolidator model-run aggregates: `[{"calls": 61, "input_tokens": 636714, "output_tokens": 25949, "stage": "canonicalizer", "wall_ms": 647322.7046999964}, {"calls": 3, "input_tokens": 487246, "output_tokens": 625, "stage": "consolidator", "wall_ms": 49375.46609999845}, {"calls": 791, "input_tokens": 1175523, "output_tokens": 42431, "stage": "selector", "wall_ms": 2182679.369600286}]`.

The foreground token sum is an observed context-area proxy, not a causal savings claim because the arms did not reach comparable memory transitions.
