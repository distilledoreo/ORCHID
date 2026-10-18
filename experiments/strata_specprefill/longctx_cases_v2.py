"""P4 extra sets: replicas per 16k cell + new multifile kind (8k/16k).

Seed 20261018 (disjoint from v1 seed 20261017): v1 frozen cases untouched.
Multifile tasks span two synthetic files and require cross-file reasoning:
- multifile_hop: service_fn in file B calls helper in file A; ask what it returns.
- multifile_large: answer lives in the second file among hundreds of helpers.

Token-budgeted with the real target prepare(), evidence mid-file.
"""

from __future__ import annotations

import random

from .longctx_cases import _mod, _s


def _multifile_hop(rng: random.Random, case_id: str, module: str) -> tuple[list[str], str, str]:
    mod_a = f"{module}_a"
    mod_b = f"{module}_b"
    leaf = f"frobnicate_{_s(rng, 5)}"
    svc = f"serve_{_s(rng, 5)}"
    answer = str(rng.randint(10000, 99999))
    file_a = [f"def {leaf}():\n    # helper in {mod_a}\n    return {answer}"]
    file_b = [f"def {svc}():\n    # service in {mod_b}\n    from {mod_a} import {leaf}\n    return {leaf}()"]
    question = (
        f"Module {mod_b} imports from module {mod_a}. What does {svc}() return? "
        f"Reply with only the number."
    )
    return (mod_a, mod_b, file_a, file_b, question, answer)


def _multifile_large(rng: random.Random, case_id: str, module: str) -> tuple[list[str], str, str]:
    mod_a = f"{module}_a"
    mod_b = f"{module}_b"
    target_fn = f"frobnicate_{_s(rng, 5)}"
    answer = str(rng.randint(10000, 99999))
    file_a: list[str] = []
    file_b = [f"def {target_fn}():\n    # target helper in {mod_b}\n    return {answer}"]
    question = f"In module {mod_b}, what does {target_fn}() return? Reply with only the number."
    return (mod_a, mod_b, file_a, file_b, question, answer)


_MULTI = {"multifile_hop": _multifile_hop, "multifile_large": _multifile_large}


def _render_two_file(mod_a: str, mod_b: str, fa: list[str], fb: list[str],
                     fill_a: list[str], fill_b: list[str], question: str) -> list[dict]:
    header_a = f"# module {mod_a}.py — synthetic helpers (generated, not from any repo)"
    header_b = f"# module {mod_b}.py — synthetic helpers (generated, not from any repo)"
    return [
        {"role": "system", "content": "Read the code and answer exactly."},
        {"role": "user", "content": (
            header_a + "\n\n" + "\n\n".join(fill_a + fa)
            + "\n\n" + header_b + "\n\n" + "\n\n".join(fill_b + fb)
            + "\n\n" + question)},
    ]


def _decoy(rng: random.Random, module: str, i: int) -> str:
    fname = f"frobnicate_{_s(rng, 5)}_{i:04d}"
    return f"def {fname}():\n    # helper in {module}\n    return {rng.randint(10000, 99999)}"


def build_v2_sets(prepare, seed: int = 20261018) -> dict:
    """Per split: 16k x {large_read, multi_hop, adversarial} x 2 replicas +
    {8k, 16k} x {multifile_hop, multifile_large} x 2 replicas = 14 cases."""
    from .longctx_cases import _MAKERS

    rng = random.Random(seed)
    out = {"seed": seed, "dev": [], "held": []}
    for split in ("dev", "held"):
        # Replica single-file 16k cells.
        for rep in (2, 3):
            for length in (16384,):
                for kind, maker in _MAKERS.items():
                    module = _mod(rng)
                    case_id = f"{split}-{kind}-{length // 1024}k-r{rep}"
                    seed_funcs, question, answer = maker(rng, case_id, module)
                    funcs: list[str] = []
                    messages: list[dict] = []
                    while True:
                        funcs.append(_decoy(rng, module, len(funcs)))
                        probe = {
                            "messages": [
                                {"role": "system", "content": "Read the code and answer exactly."},
                                {"role": "user", "content": (
                                    f"# module {module}.py\n\n" + "\n\n".join(funcs)
                                    + "\n\n" + question)},
                            ]
                        }
                        if len(prepare(probe)) >= length - 64:
                            break
                    pos = int(len(funcs) * rng.uniform(0.4, 0.6))
                    body = funcs[:pos] + seed_funcs + funcs[pos:]
                    messages = [
                        {"role": "system", "content": "Read the code and answer exactly."},
                        {"role": "user", "content": (
                            f"# module {module}.py — synthetic helpers (generated, not from any repo)\n\n"
                            + "\n\n".join(body) + "\n\n" + question)},
                    ]
                    out[split].append({"id": case_id, "kind": f"coding-{kind}",
                                       "messages": messages, "answer": answer,
                                       "budget": length})
        # Multifile 8k/16k cells.
        for rep in (1, 2):
            for length in (8192, 16384):
                for kind, maker in _MULTI.items():
                    module = _mod(rng)
                    case_id = f"{split}-{kind}-{length // 1024}k-r{rep}"
                    mod_a, mod_b, fa, fb, question, answer = maker(rng, case_id, module)
                    fill_a: list[str] = []
                    fill_b: list[str] = []
                    messages = []
                    i = 0
                    while True:
                        if len(fill_a) <= len(fill_b):
                            fill_a.append(_decoy(rng, mod_a, i))
                        else:
                            fill_b.append(_decoy(rng, mod_b, i))
                        i += 1
                        messages = _render_two_file(mod_a, mod_b, [], [], fill_a, fill_b,
                                                    question)
                        if len(prepare({"messages": messages})) >= length - 64:
                            break
                        if i > 4000:
                            raise RuntimeError("budget loop exceeded guard")
                    # Evidence mid-file: splice seed blocks into each file.
                    pa = int(len(fill_a) * rng.uniform(0.4, 0.6))
                    pb = int(len(fill_b) * rng.uniform(0.4, 0.6))
                    fa_all = fill_a[:pa] + fa + fill_a[pa:]
                    fb_all = fill_b[:pb] + fb + fill_b[pb:]
                    messages = _render_two_file(mod_a, mod_b, [], [], fa_all, fb_all,
                                                question)
                    out[split].append({"id": case_id, "kind": f"coding-{kind}",
                                       "messages": messages, "answer": answer,
                                       "budget": length})
    return out
