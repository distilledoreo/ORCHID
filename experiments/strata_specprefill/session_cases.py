"""Multi-turn coding-agent sessions for per-turn SpecPrefill quality.

Five distractor patterns, one session each (seed 20261019, disjoint from all
prior seeds; v1/v2 sets untouched):
- early_late: key constant shown early, needed much later among decoys.
- task_shift: debug task first, then a new feature task reuses one file.
- multifile: evidence spread across 3 synthetic files; final joins them.
- sleeper: an "irrelevant" config value becomes the answer late.
- supersede: v1 API is replaced by v2; final asks v2 only.

Strict user/assistant alternation (template requirement): consecutive file
reads are joined with assistant acknowledgements. The harness replays turns
cumulatively: turn t prompt = full session history through turn t + the
probe question. Exact-match answers + evidence token lists per probe.
"""

from __future__ import annotations

import random
import string


def _s(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(string.ascii_lowercase) for _ in range(n))


def _num(rng: random.Random) -> str:
    return str(rng.randint(10000, 99999))


def _helpers(rng: random.Random, module: str, n: int, start: int = 0) -> list[str]:
    out = []
    for i in range(n):
        fname = f"frobnicate_{_s(rng, 5)}_{start + i:04d}"
        out.append(f"def {fname}():\n    # helper in {module}\n    return {_num(rng)}")
    return out


def _read(module: str, body: str) -> str:
    return f"File read {module}.py:\n{body}"


def _ack() -> str:
    return "Noted."


def _session_early_late(rng: random.Random) -> tuple[list[dict], list[dict]]:
    module = f"{_s(rng, 7)}_{rng.randint(100, 999)}"
    key_fn = f"frobnicate_{_s(rng, 5)}"
    answer = _num(rng)
    key_def = f"def {key_fn}():\n    # core constant in {module}\n    return {answer}"
    msgs = [{"role": "system", "content": "Read the code and answer exactly."}]
    probes = []
    msgs.append({"role": "user", "content": _read(
        module, f"# module {module}.py\n\n" + "\n\n".join([key_def] + _helpers(rng, module, 12)))})
    probes.append({"after_msg": 1,
                   "q": f"In {module}, what does {key_fn}() return? Reply with only the number.",
                   "answer": answer, "evidence": [key_fn, answer]})
    n = 2
    for t in range(3, 13):
        msgs.append({"role": "assistant", "content": _ack()})
        msgs.append({"role": "user", "content": _read(
            f"{module}_ext{t}",
            f"# module {module}_ext{t}.py\n\n" + "\n\n".join(_helpers(rng, module, 14, t * 20)))})
        n += 2
    msgs.append({"role": "assistant", "content": _ack()})
    msgs.append({"role": "user", "content": (
        f"Back in {module}: what does {key_fn}() return? Reply with only the number.")})
    n += 2
    probes.append({"after_msg": n - 1, "q": None, "answer": answer,
                   "evidence": [key_fn, answer]})
    return msgs, probes


def _session_task_shift(rng: random.Random) -> tuple[list[dict], list[dict]]:
    mod_a = f"{_s(rng, 7)}_{rng.randint(100, 999)}"
    mod_b = f"{_s(rng, 7)}_{rng.randint(100, 999)}"
    bug_fn = f"frobnicate_{_s(rng, 5)}"
    bug_val = _num(rng)
    feat_fn = f"frobnicate_{_s(rng, 5)}"
    feat_val = _num(rng)
    msgs = [{"role": "system", "content": "Read the code and answer exactly."}]
    probes = []
    msgs.append({"role": "user", "content": _read(
        mod_a, f"# module {mod_a}.py\n\n" + "\n\n".join(
            [f"def {bug_fn}():\n    # buggy helper in {mod_a}\n    return {bug_val}"]
            + _helpers(rng, mod_a, 10)))})
    probes.append({"after_msg": 1, "q": f"What does {bug_fn}() return? Reply with only the number.",
                   "answer": bug_val, "evidence": [bug_fn, bug_val]})
    for t in range(3, 8):
        msgs.append({"role": "assistant", "content": _ack()})
        msgs.append({"role": "user", "content": _read(
            f"{mod_a}_log{t}", "# logs\n\n" + "\n\n".join(_helpers(rng, mod_a, 8, t * 10)))})
    msgs.append({"role": "assistant", "content": _ack()})
    msgs.append({"role": "user", "content": (
        f"New task: forget the bug hunt. In module {mod_b} the helper {feat_fn}() is defined below. "
        f"What does it return? Reply with only the number.")})
    msgs.append({"role": "assistant", "content": _ack()})
    msgs.append({"role": "user", "content": _read(
        mod_b, f"# module {mod_b}.py\n\n" + "\n\n".join(
            [f"def {feat_fn}():\n    # feature helper in {mod_b}\n    return {feat_val}"]
            + _helpers(rng, mod_b, 12)))})
    probes.append({"after_msg": len(msgs) - 1, "q": None, "answer": feat_val,
                   "evidence": [feat_fn, feat_val]})
    msgs.append({"role": "assistant", "content": _ack()})
    msgs.append({"role": "user", "content": (
        f"Confirm: the feature value from {mod_b}, not the old {mod_a} bug value. "
        f"Reply with only the number.")})
    probes.append({"after_msg": len(msgs) - 1, "q": None, "answer": feat_val,
                   "evidence": [feat_fn, feat_val]})
    return msgs, probes


def _session_multifile(rng: random.Random) -> tuple[list[dict], list[dict]]:
    mods = [f"{_s(rng, 7)}_{rng.randint(100, 999)}_{c}" for c in "abc"]
    leaf = f"frobnicate_{_s(rng, 5)}"
    mid = f"frobnicate_{_s(rng, 5)}"
    svc = f"serve_{_s(rng, 5)}"
    answer = _num(rng)
    msgs = [{"role": "system", "content": "Read the code and answer exactly."}]
    probes = []
    bodies = [
        (mods[0], f"# module {mods[0]}.py\n\n" + "\n\n".join(
            [f"def {leaf}():\n    # leaf in {mods[0]}\n    return {answer}"] + _helpers(rng, mods[0], 10))),
        (mods[1], f"# module {mods[1]}.py\n\n" + "\n\n".join(
            [f"def {mid}():\n    # mid in {mods[1]}\n    from {mods[0]} import {leaf}\n    return {leaf}()"] + _helpers(rng, mods[1], 10))),
        (mods[2], f"# module {mods[2]}.py\n\n" + "\n\n".join(
            [f"def {svc}():\n    # service in {mods[2]}\n    from {mods[1]} import {mid}\n    return {mid}()"] + _helpers(rng, mods[2], 10))),
    ]
    for mod, body in bodies:
        if msgs[-1]["role"] == "user":
            msgs.append({"role": "assistant", "content": _ack()})
        msgs.append({"role": "user", "content": _read(mod, body)})
    for t in range(4, 12):
        msgs.append({"role": "assistant", "content": _ack()})
        msgs.append({"role": "user", "content": _read(
            f"aux{t}", "# aux\n\n" + "\n\n".join(_helpers(rng, "aux", 12, t * 15)))})
    msgs.append({"role": "assistant", "content": _ack()})
    msgs.append({"role": "user", "content": (
        f"Tracing the chain {svc} -> {mid} -> {leaf}: what number is finally returned? "
        f"Reply with only the number.")})
    probes.append({"after_msg": len(msgs) - 1, "q": None, "answer": answer,
                   "evidence": [leaf, mid, svc, answer]})
    # v3 probe: provenance as a follow-up question (q-separate, the same shape
    # as the passing number probes). v1 (module-name echo) and v2 (import-line
    # completion) both failed even on full context, so neither was
    # discriminative; this form is validated live below (full must pass).
    msgs.append({"role": "assistant", "content": _ack()})
    msgs.append({"role": "user", "content": _read(
        "aux12", "# aux\n\n" + "\n\n".join(_helpers(rng, "aux", 12, 12 * 15)))})
    probes.append({"after_msg": len(msgs) - 1,
                   "q": f"Which module defines {leaf}()? Reply with only the module name.",
                   "answer": mods[0],
                   "evidence": [mods[0], leaf], "kind": "module-name"})
    return msgs, probes


def _session_sleeper(rng: random.Random) -> tuple[list[dict], list[dict]]:
    module = f"{_s(rng, 7)}_{rng.randint(100, 999)}"
    cfg_key = f"frobnicate_{_s(rng, 5)}"
    cfg_val = _num(rng)
    msgs = [{"role": "system", "content": "Read the code and answer exactly."}]
    probes = []
    msgs.append({"role": "user", "content": _read(
        f"{module}_config",
        f"# config for {module} (background, probably irrelevant)\n\n"
        f"def {cfg_key}():\n    # config default\n    return {cfg_val}\n\n"
        + "\n\n".join(_helpers(rng, module, 8)))})
    for t in range(3, 14):
        msgs.append({"role": "assistant", "content": _ack()})
        msgs.append({"role": "user", "content": _read(
            f"{module}_part{t}",
            f"# module {module}_part{t}.py\n\n" + "\n\n".join(_helpers(rng, module, 12, t * 15)))})
        # v3: filler turns carry no probe. (v2's count probes expected "13"
        # but every arm incl. full answered "12" — wrong expected value, so
        # they were non-discriminative by construction. Removed, not repaired.)
    msgs.append({"role": "assistant", "content": _ack()})
    msgs.append({"role": "user", "content": (
        f"That background config from the start ({cfg_key}): what value did it hold? "
        f"Reply with only the number.")})
    probes.append({"after_msg": len(msgs) - 1, "q": None, "answer": cfg_val,
                   "evidence": [cfg_key, cfg_val]})
    return msgs, probes


def _session_supersede(rng: random.Random) -> tuple[list[dict], list[dict]]:
    module = f"{_s(rng, 7)}_{rng.randint(100, 999)}"
    base = f"frobnicate_{_s(rng, 5)}"
    v1_val, v2_val = _num(rng), _num(rng)
    while v2_val == v1_val:
        v2_val = _num(rng)
    msgs = [{"role": "system", "content": "Read the code and answer exactly."}]
    probes = []
    msgs.append({"role": "user", "content": _read(
        module, f"# module {module}.py v1\n\n"
                f"def {base}():\n    # v1 API\n    return {v1_val}\n\n"
                + "\n\n".join(_helpers(rng, module, 8)))})
    probes.append({"after_msg": 1, "q": f"While on v1: what does {base}() return? Reply with only the number.",
                   "answer": v1_val, "evidence": [v1_val]})
    for t in range(3, 9):
        msgs.append({"role": "assistant", "content": _ack()})
        msgs.append({"role": "user", "content": _read(
            f"{module}_v1use{t}", "# v1 usage\n\n" + "\n\n".join(_helpers(rng, module, 10, t * 12)))})
    msgs.append({"role": "assistant", "content": _ack()})
    msgs.append({"role": "user", "content": (
        f"API update: {base}() is now v2, defined below. Ignore all v1 values seen so far. "
        f"What does it return? Reply with only the number.")})
    msgs.append({"role": "assistant", "content": _ack()})
    msgs.append({"role": "user", "content": _read(
        f"{module}_v2",
        f"# module {module}.py v2 (current)\n\n"
        f"def {base}():\n    # v2 API - current\n    return {v2_val}\n\n"
        + "\n\n".join(_helpers(rng, module, 8)))})
    probes.append({"after_msg": len(msgs) - 1, "q": None, "answer": v2_val,
                   "evidence": [v2_val]})
    msgs.append({"role": "assistant", "content": _ack()})
    msgs.append({"role": "user", "content": (
        f"What does the CURRENT {base}() return? Reply with only the number.")})
    probes.append({"after_msg": len(msgs) - 1, "q": None, "answer": v2_val,
                   "evidence": [v2_val]})
    return msgs, probes


_BUILDERS = {
    "early_late": _session_early_late,
    "task_shift": _session_task_shift,
    "multifile": _session_multifile,
    "sleeper": _session_sleeper,
    "supersede": _session_supersede,
}


def build_sessions(seed: int = 20261019) -> dict:
    rng = random.Random(seed)
    out = {"seed": seed, "sessions": []}
    for name, fn in _BUILDERS.items():
        msgs, probes = fn(rng)
        out["sessions"].append({"id": f"ses-{name}", "pattern": name,
                                "messages": msgs, "probes": probes,
                                "turns": len(msgs)})
    return out
