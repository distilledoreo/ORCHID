"""Tokenizer invariants and the reference's pooling/chunk selection order."""

import hashlib
import json
import math


def token_hash(ids):
    return hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest()


def compatible_ids(target_vocab, student_descriptor):
    student = dict(student_descriptor["model"]["vocab"])
    student.update({t["content"]: t["id"] for t in student_descriptor["added_tokens"]})
    return {index for text, index in student.items() if target_vocab.get(text) == index}


def validate_input(ids, allowed_ids):
    if not ids or any(type(t) is not int or t not in allowed_ids for t in ids):
        raise ValueError("Prompt contains an empty, invalid, or nonshared token ID")


def select_positions(scores, keep_ratio, chunk_size=32):
    """Use torch's dtype, chunk means and topk, as in the pinned reference."""
    import torch

    if not 0 < keep_ratio <= 1 or chunk_size < 1 or scores.ndim != 1 or not scores.numel():
        raise ValueError("Invalid retention policy or scores")
    if not torch.isfinite(scores).all():
        raise ValueError("Nonfinite importance scores")
    chunks = scores.split(chunk_size)
    count = math.ceil(len(chunks) * keep_ratio)
    chosen = torch.stack([chunk.mean() for chunk in chunks]).topk(count).indices.tolist()
    positions = sorted(
        p for c in chosen for p in range(c * chunk_size, min((c + 1) * chunk_size, len(scores)))
    )
    return positions


def importance(logits, pool_kernel=13):
    """[layers, heads, query rows, original prompt keys] -> per-key importance."""
    import torch

    if logits.ndim != 4 or pool_kernel < 1 or pool_kernel % 2 == 0:
        raise ValueError("Expected four-dimensional logits and an odd pooling kernel")
    attn = logits.softmax(-1, dtype=torch.float32).to(logits.dtype).flatten(0, 1)
    attn = torch.nn.functional.avg_pool1d(attn, pool_kernel, stride=1, padding=pool_kernel // 2)
    return attn.max(0).values.mean(0)


def validate_frozen_selection(ids, trace):
    """Bind saved positions to the exact prompt, and reject malformed subsets."""
    if trace.get("input_hash") != token_hash(ids) or trace.get("input_tokens") != len(ids):
        raise ValueError("Frozen selection does not match this exact prompt")
    for selection in trace.get("per_rate", {}).values():
        positions = selection["positions"]
        if (not positions or any(type(p) is not int or not 0 <= p < len(ids)
                                 for p in positions)
                or any(a >= b for a, b in zip(positions, positions[1:]))
                or selection.get("retained_tokens", len(positions)) != len(positions)):
            raise ValueError("Invalid frozen selection positions")


def validate_selected_response(ids, positions, response):
    audit = response["prefill"]
    if (audit.get("token_ids") != [ids[p] for p in positions]
            or audit.get("position_ids") != positions
            or audit.get("next_position") != len(ids)
            or audit.get("original_prompt_length") != len(ids)
            or audit.get("device_table_verified") is not True):
        raise RuntimeError("Target did not confirm the exact selected IDs and original positions")
