"""Contamination-safe dev + held-out prompt sets for adaptive retention.

All identifiers are freshly randomized per generation seed so answers cannot
be memorized from training data or from the earlier frozen smoke cases
(KELP47/FERN83/WILLOW*). Lookup and coding tasks share the same rendering
path as the existing smoke test: target chat template + target tokenizer,
frozen IDs with hashes.
"""

from __future__ import annotations

import hashlib
import json
import random
import string


def _rand_code(rng: random.Random, prefix_len: int = 4, digit_len: int = 2) -> str:
    letters = "".join(rng.choice(string.ascii_uppercase) for _ in range(prefix_len))
    digits = "".join(rng.choice(string.digits) for _ in range(digit_len))
    return f"{letters}{digits}"


def _lookup_case(rng: random.Random, case_id: str, size: int, placement: float) -> dict:
    # Fresh distractor namespace per case so the model cannot rely on WILLOW*.
    distractor_tag = _rand_code(rng, 6, 0)
    target_rec = f"R{rng.randint(10, 99)}"
    answer = _rand_code(rng)
    rows: list[str] = []
    # Grow until we hit the size budget (measured later in tokens; rows approx).
    while len(rows) < 200:
        rows.append(f"Record D{len(rows):03d} has access code {distractor_tag}{len(rows):03d}.")
        if len(rows) > size // 4:
            break
    rows.insert(int(len(rows) * placement), f"Record {target_rec} has access code {answer}.")
    messages = [
        {"role": "system", "content": "Read the records and answer the question exactly."},
        {
            "role": "user",
            "content": "\n".join(rows)
            + f"\nWhat is the access code for record {target_rec}? Reply with only its code.",
        },
    ]
    return {"id": case_id, "kind": "lookup", "messages": messages, "answer": answer}


def _coding_case(rng: random.Random, case_id: str, size: int, placement: float) -> dict:
    # Invented module + function names that cannot exist in training data.
    module = f"{''.join(rng.choice(string.ascii_lowercase) for _ in range(7))}_{rng.randint(100, 999)}"
    target_fn = f"frobnicate_{''.join(rng.choice(string.ascii_lowercase) for _ in range(5))}"
    answer_val = str(rng.randint(10000, 99999))
    funcs: list[str] = []
    while len(funcs) < 120:
        fname = f"frobnicate_{''.join(rng.choice(string.ascii_lowercase) for _ in range(5))}_{len(funcs):03d}"
        val = str(rng.randint(10000, 99999))
        funcs.append(f"def {fname}():\n    # helper in {module}\n    return {val}")
        if len(funcs) > size // 12:
            break
    target_body = f"def {target_fn}():\n    # target helper in {module}\n    return {answer_val}"
    funcs.insert(int(len(funcs) * placement), target_body)
    header = f"# module {module}.py — synthetic helpers (generated, not from any repo)"
    question = f"In module {module}, what does {target_fn}() return? Reply with only the number."
    messages = [
        {"role": "system", "content": "Read the code and answer exactly."},
        {"role": "user", "content": header + "\n\n" + "\n\n".join(funcs) + "\n\n" + question},
    ]
    return {"id": case_id, "kind": "coding", "messages": messages, "answer": answer_val}


def _lookup_case_budgeted(rng: random.Random, case_id: str, size: int, placement: float, prepare) -> dict:
    """Token-budgeted variant matching run.py: grow until prepare() >= size-24."""
    distractor_tag = _rand_code(rng, 6, 0)
    target_rec = f"R{rng.randint(10, 99)}"
    answer = _rand_code(rng)
    rows: list[str] = []
    messages: list[dict] = []
    while True:
        rows.append(f"Record D{len(rows):03d} has access code {distractor_tag}{len(rows):03d}.")
        messages = [
            {"role": "system", "content": "Read the records and answer the question exactly."},
            {
                "role": "user",
                "content": "\n".join(rows)
                + f"\nWhat is the access code for record {target_rec}? Reply with only its code.",
            },
        ]
        if len(prepare({"messages": messages})) >= size - 24:
            break
    rows.insert(int(len(rows) * placement), f"Record {target_rec} has access code {answer}.")
    messages[1]["content"] = "\n".join(rows) + f"\nWhat is the access code for record {target_rec}? Reply with only its code."
    return {"id": case_id, "kind": "lookup", "messages": messages, "answer": answer}


def _coding_case_budgeted(rng: random.Random, case_id: str, size: int, placement: float, prepare) -> dict:
    module = f"{''.join(rng.choice(string.ascii_lowercase) for _ in range(7))}_{rng.randint(100, 999)}"
    target_fn = f"frobnicate_{''.join(rng.choice(string.ascii_lowercase) for _ in range(5))}"
    answer_val = str(rng.randint(10000, 99999))
    funcs: list[str] = []
    messages: list[dict] = []
    while True:
        fname = f"frobnicate_{''.join(rng.choice(string.ascii_lowercase) for _ in range(5))}_{len(funcs):03d}"
        val = str(rng.randint(10000, 99999))
        funcs.append(f"def {fname}():\n    # helper in {module}\n    return {val}")
        header = f"# module {module}.py — synthetic helpers (generated, not from any repo)"
        probe_q = f"In module {module}, what does {target_fn}() return? Reply with only the number."
        messages = [
            {"role": "system", "content": "Read the code and answer exactly."},
            {"role": "user", "content": header + "\n\n" + "\n\n".join(funcs) + "\n\n" + probe_q},
        ]
        if len(prepare({"messages": messages})) >= size - 24:
            break
        if len(funcs) > 300:
            break
    target_body = f"def {target_fn}():\n    # target helper in {module}\n    return {answer_val}"
    funcs.insert(int(len(funcs) * placement), target_body)
    header = f"# module {module}.py — synthetic helpers (generated, not from any repo)"
    question = f"In module {module}, what does {target_fn}() return? Reply with only the number."
    messages = [
        {"role": "system", "content": "Read the code and answer exactly."},
        {"role": "user", "content": header + "\n\n" + "\n\n".join(funcs) + "\n\n" + question},
    ]
    return {"id": case_id, "kind": "coding", "messages": messages, "answer": answer_val}


def build_sets(seed: int = 20261016, prepare=None) -> dict:
    """Build 8 dev + 8 held-out cases, balanced lookup/coding x short/long x early/late."""
    rng = random.Random(seed)
    dev, held = [], []
    specs = [
        ("lookup", 512, 0.2),
        ("lookup", 512, 0.8),
        ("lookup", 1536, 0.2),
        ("lookup", 1536, 0.8),
        ("coding", 512, 0.2),
        ("coding", 512, 0.8),
        ("coding", 1536, 0.2),
        ("coding", 1536, 0.8),
    ]
    for i, (kind, size, place) in enumerate(specs):
        if prepare is not None:
            maker = _lookup_case_budgeted if kind == "lookup" else _coding_case_budgeted
            dev.append(maker(rng, f"dev-{kind}-{size}-at{int(place*100)}", size, place, prepare))
        else:
            maker = _lookup_case if kind == "lookup" else _coding_case
            dev.append(maker(rng, f"dev-{kind}-{size}-at{int(place*100)}", size, place))
    for i, (kind, size, place) in enumerate(specs):
        if prepare is not None:
            maker = _lookup_case_budgeted if kind == "lookup" else _coding_case_budgeted
            held.append(maker(rng, f"held-{kind}-{size}-at{int(place*100)}", size, place, prepare))
        else:
            maker = _lookup_case if kind == "lookup" else _coding_case
            held.append(maker(rng, f"held-{kind}-{size}-at{int(place*100)}", size, place))
    return {"seed": seed, "dev": dev, "held": held}


def freeze(cases: list[dict], prepare) -> list[dict]:
    """Attach input_ids + input_hash using the target prepare() fn. Drops unrenderable."""
    out = []
    for c in cases:
        ids = prepare({"messages": c["messages"]})
        c = dict(c)
        c["input_ids"] = ids
        c["input_hash"] = hashlib.sha256(json.dumps(ids).encode()).hexdigest()
        out.append(c)
    return out
