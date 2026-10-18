"""Long-context P100 validation workloads: 8k/16k/32k coding-agent tasks.

Kinds (all identifiers freshly randomized per seed):
- large_read: one target function among hundreds of synthetic helpers.
- multi_hop: fn A names fn B; ask for B's return value (two evidence spans).
- adversarial: near-duplicate names/values + stale v1 vs current v2 copies;
  only the v2-marked definition counts; evidence buried mid-file.

Token-budgeted with the real target prepare() like adaptive_cases.
"""

from __future__ import annotations

import random
import string


def _s(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(string.ascii_lowercase) for _ in range(n))


def _mod(rng: random.Random) -> str:
    return f"{_s(rng, 7)}_{rng.randint(100, 999)}"


def _large_read(rng: random.Random, case_id: str, module: str) -> tuple[list[str], str, str]:
    target_fn = f"frobnicate_{_s(rng, 5)}"
    answer = str(rng.randint(10000, 99999))
    funcs = [f"def {target_fn}():\n    # target helper in {module}\n    return {answer}"]
    question = f"In module {module}, what does {target_fn}() return? Reply with only the number."
    return funcs, question, answer


def _multi_hop(rng: random.Random, case_id: str, module: str) -> tuple[list[str], str, str]:
    mid_fn = f"frobnicate_{_s(rng, 5)}"
    leaf_fn = f"frobnicate_{_s(rng, 5)}"
    answer = str(rng.randint(10000, 99999))
    funcs = [
        f"def {mid_fn}():\n    # wrapper in {module}\n    return {leaf_fn}()",
        f"def {leaf_fn}():\n    # leaf helper in {module}\n    return {answer}",
    ]
    question = (
        f"In module {module}, {mid_fn}() calls another helper. "
        f"What does that helper return? Reply with only the number."
    )
    return funcs, question, answer


def _adversarial(rng: random.Random, case_id: str, module: str) -> tuple[list[str], str, str]:
    base = f"frobnicate_{_s(rng, 5)}"
    target_fn = base + "_v2"
    answer = str(rng.randint(10000, 99999))
    stale = str(rng.randint(10000, 99999))
    assert stale != answer
    near = [f"def {base}_v{i}():\n    # decoy in {module}\n    return {rng.randint(10000, 99999)}" for i in (1, 3)]
    funcs = [
        f"def {base}():\n    # STALE v1 in {module} - superseded, do not use\n    return {stale}",
        *near,
        f"def {target_fn}():\n    # CURRENT v2 in {module}\n    return {answer}",
    ]
    question = (
        f"In module {module}, what does the CURRENT v2 {base} helper return? "
        f"Ignore stale v1 and decoys. Reply with only the number."
    )
    return funcs, question, answer


_MAKERS = {"large_read": _large_read, "multi_hop": _multi_hop, "adversarial": _adversarial}


def build_long_sets(prepare, seed: int = 20261017) -> dict:
    """Build dev+held long-context sets. Keep tiny: 1 case per (kind x length) per split.

    Lengths are target-token budgets (8k/16k/32k). Evidence placed mid-file
    (40-60%) to defeat edge-biased selection.
    """
    rng = random.Random(seed)
    out = {"seed": seed, "dev": [], "held": []}
    for split in ("dev", "held"):
        for length in (8192, 16384, 32768):
            for kind, maker in _MAKERS.items():
                module = _mod(rng)
                case_id = f"{split}-{kind}-{length // 1024}k"
                seed_funcs, question, answer = maker(rng, case_id, module)
                # Fill with decoy helpers until token budget reached.
                funcs: list[str] = []
                messages: list[dict] = []
                header = f"# module {module}.py — synthetic helpers (generated, not from any repo)"
                while True:
                    fname = f"frobnicate_{_s(rng, 5)}_{len(funcs):04d}"
                    funcs.append(
                        f"def {fname}():\n    # helper in {module}\n    return {rng.randint(10000, 99999)}"
                    )
                    probe = {
                        "messages": [
                            {"role": "system", "content": "Read the code and answer exactly."},
                            {"role": "user",
                             "content": header + "\n\n" + "\n\n".join(funcs) + "\n\n" + question},
                        ]
                    }
                    if len(prepare(probe)) >= length - 64:
                        break
                    if len(funcs) > 4000:
                        raise RuntimeError("budget loop exceeded guard")
                # Insert evidence block mid-file (not edges).
                pos = int(len(funcs) * rng.uniform(0.4, 0.6))
                body = funcs[:pos] + seed_funcs + funcs[pos:]
                messages = [
                    {"role": "system", "content": "Read the code and answer exactly."},
                    {"role": "user",
                     "content": header + "\n\n" + "\n\n".join(body) + "\n\n" + question},
                ]
                out[split].append(
                    {"id": case_id, "kind": f"coding-{kind}", "messages": messages,
                     "answer": answer, "budget": length}
                )
    return out
