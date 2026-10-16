"""Exact-source routing and an intentionally uncalibrated evidence baseline."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass, replace


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class Chunk:
    source_id: str
    source_hash: str
    document_id: str
    start: int
    end: int
    byte_start: int
    byte_end: int
    token_start: int
    token_end: int
    text: str
    importance: float = 0.0
    query_hash: str = ""
    retained: bool = False
    speculator_token_start: int | None = None
    speculator_token_end: int | None = None


def make_chunks(source_id, document_id, text, tokenizer, size=64):
    if size <= 0:
        raise ValueError("chunk size must be positive")
    encoded = tokenizer.encode(text, add_special_tokens=False)
    result = []
    for offset in range(0, len(encoded.ids), size):
        spans = encoded.offsets[offset:offset + size]
        start, end = min(a for a, _ in spans), max(b for _, b in spans)
        if end <= start:
            continue
        result.append(Chunk(source_id, digest(text), document_id, start, end,
                            len(text[:start].encode()), len(text[:end].encode()),
                            offset, min(offset + size, len(encoded.ids)), text[start:end]))
    return result


def smooth(values, kernel=13):
    if kernel < 1 or kernel % 2 != 1:
        raise ValueError("pool kernel must be odd and positive")
    radius = kernel // 2
    return [sum(values[max(0, i - radius):i + radius + 1]) / kernel for i in range(len(values))]


def lexical_score(query, text):
    words = set(re.findall(r"\w+", query.lower())) - {"what", "the", "and", "for", "with", "from", "answer", "only"}
    return len(words & set(re.findall(r"\w+", text.lower()))) / max(1, len(words))


def score_chunks(chunks, query, trace=None, *, pooling=False, reference_order=False):
    if trace is None:
        return [replace(c, importance=lexical_score(query, c.text), query_hash=digest(query)) for c in chunks]
    importance = trace["reference_scores"] if reference_order else trace["scores"]
    if pooling and not reference_order:
        importance = smooth(importance)
    offsets = trace["offsets"]
    locations = trace["source_locations"]
    result = []
    for chunk in chunks:
        base = locations[chunk.source_id][0]
        low, high = base + chunk.start, base + chunk.end
        overlaps = [(max(0, min(high, b) - max(low, a)), score)
                    for (a, b), score in zip(offsets, importance) if a < high and b > low]
        weight = sum(n for n, _ in overlaps)
        score = sum(n * value for n, value in overlaps) / max(1, weight)
        token_indices = [i for i, (a, b) in enumerate(offsets) if a < high and b > low]
        result.append(replace(chunk, importance=score, query_hash=digest(query),
                              speculator_token_start=min(token_indices) if token_indices else None,
                              speculator_token_end=max(token_indices) + 1 if token_indices else None))
    return result


def render(chunks, sources, *, inventory=True):
    # Reassemble in original source order and merge overlaps before copying.
    lines = []
    if inventory:
        lines.append("UNTRUSTED SOURCE INVENTORY: " + json.dumps(
            [{"id": s["id"], "characters": len(s["text"]), "hash": digest(s["text"])[:12]} for s in sources]))
    for source in sources:
        ranges = []
        for c in sorted((c for c in chunks if c.source_id == source["id"]), key=lambda c: c.start):
            if ranges and c.start <= ranges[-1][1]:
                ranges[-1][1] = max(ranges[-1][1], c.end)
            else:
                ranges.append([c.start, c.end])
        for start, end in ranges:
            lines.append(f"[{source['id']}:{start}:{end}]\n" + source["text"][start:end])
    return "\n".join(lines)


def select(chunks, sources, tokenizer, keep_ratio, *, adaptive=False, query=""):
    if not 0 < keep_ratio <= 1:
        raise ValueError("keep ratio must be in (0, 1]")
    if adaptive and re.search(r"\b(every|all|complete|count|comprehensive|sum)\b", query, re.I):
        keep_ratio = 1.0
    original_tokens = sum(len(tokenizer.encode(s["text"], add_special_tokens=False).ids) for s in sources)
    budget = math.floor(original_tokens * keep_ratio)
    if keep_ratio == 1:
        chosen = list(chunks)
    else:
        chosen = []
        for candidate in sorted(chunks, key=lambda c: (-c.importance, c.source_id, c.start)):
            if len(tokenizer.encode(render(chosen + [candidate], sources, inventory=False), add_special_tokens=False).ids) <= budget:
                chosen.append(candidate)
    identities = {(c.source_id, c.start, c.end) for c in chosen}
    metadata = [replace(c, retained=(c.source_id, c.start, c.end) in identities) for c in chunks]
    return chosen, metadata, budget


def identifiers(query):
    quoted = re.findall(r"`([^`]+)`", query)
    return quoted or re.findall(r"\b(?:[A-Z]{2,}[_-][A-Za-z0-9_-]+|[a-z]+_[a-z_]+)\b", query)


def source_inventory(sources, all_chunks=None, chosen=None):
    inventory = [{"id": s["id"], "characters": len(s["text"]), "hash": digest(s["text"])} for s in sources]
    if all_chunks is not None and chosen is not None:
        retained = {(c.source_id, c.start, c.end) for c in chosen}
        for item in inventory:
            selected = [c.importance for c in chosen if c.source_id == item["id"]]
            deferred = [c.importance for c in all_chunks if c.source_id == item["id"] and (c.source_id, c.start, c.end) not in retained]
            item["selected_min_score"] = min(selected) if selected else None
            item["deferred_max_score"] = max(deferred) if deferred else None
            item["selection_margin"] = min(selected) - max(deferred) if selected and deferred else None
    return inventory


def evidence_gate(query, selected, sources, search):
    """Signals, not calibrated certainty or access to benchmark answer keys."""
    started = time.perf_counter()
    text = "\n".join(c.text for c in selected)
    required = identifiers(query)
    missing = [item for item in required if item.lower() not in text.lower()]
    complete = all(covered_length(selected, s["id"]) >= s["characters"] for s in sources)
    exhaustive = bool(re.search(r"\b(every|all|complete|count|comprehensive|sum)\b", query, re.I))
    temporal = bool(re.search(r"\b(latest|current|correction|supersed|now)\w*\b", query, re.I))
    needs_context = exhaustive or temporal
    matches = search(" ".join(missing) if missing else query) if missing or needs_context else []
    if complete:
        decision, score, reason = "SUFFICIENT", 1.0, "Complete original evidence is present; answer may still be unknown."
    elif missing or needs_context:
        decision = "RETRIEVE" if matches else "INSUFFICIENT"
        score, reason = 0.5, "Missing identifiers or task requires complete/temporal coverage."
    elif required and selected:
        decision, score, reason = "SUFFICIENT", 0.5, "Identifiers are covered; this is not proof of sufficiency."
    else:
        matches = search(query)
        decision, score, reason = ("RETRIEVE" if matches else "INSUFFICIENT"), 0.5, "No positive coverage evidence."
    return {"decision": decision, "score": score, "calibrated": False, "missing": missing,
            "selection_margins": {s["id"]: s.get("selection_margin") for s in sources},
            "candidate_source_ids": list(dict.fromkeys(m["source_id"] for m in matches)),
            "reason": reason, "elapsed_ms": (time.perf_counter() - started) * 1000}


def covered_length(chunks, source_id):
    ranges = sorted((c.start, c.end) for c in chunks if c.source_id == source_id)
    total, end = 0, 0
    for low, high in ranges:
        total += max(0, high - max(end, low))
        end = max(end, high)
    return total


class EvidenceStore:
    """Use authoritative ORCHID events, FTS5, and scoped exact range recovery."""

    def __init__(self, path, project, thread, sources):
        from memory_gateway.db import SQLiteStore
        from memory_gateway.raw_recall import RawRecall
        self.store = SQLiteStore(path)
        self.store.ensure_project_and_thread(project, thread)
        self.project, self.thread = project, thread
        self.recall = RawRecall(self.store)
        self.ids, self.reverse = {}, {}
        for source in sources:
            row = self.store.append_event(project_id=project, thread_id=thread, role="tool", event_type="tool_result", content=source["text"])
            self.ids[source["id"]] = row["id"]
            self.reverse[row["id"]] = source["id"]

    def search(self, query):
        return [dict(item, source_id=self.reverse[item["event_id"]])
                for item in self.recall.lexical(self.project, self.thread, query[:512] or "evidence", 5)]

    def read(self, source_id, start, end):
        text = ""
        while start < end:
            result = self.recall.read(self.project, self.thread, self.ids[source_id], start, end)
            if not result or result["end"] <= start:
                raise ValueError("Original source is unavailable")
            text += result["excerpt"]
            start = result["end"]
        return text


def recover(query, chosen, all_chunks, sources, store, *, max_rounds=2, unconditional=False, evaluator=None):
    if not 0 <= max_rounds <= 2:
        raise ValueError("Initial experiment permits at most two retrieval rounds")
    working, decisions, rounds = list(chosen), [], []
    evaluate = evaluator or (lambda q, w, s, search: evidence_gate(q, w, s, search))
    for round_index in range(max_rounds + 1):
        try:
            decision = evaluate(query, working, source_inventory(sources, all_chunks, working), store.search)
        except Exception:
            decision = {"decision": "INSUFFICIENT", "score": 0.0, "calibrated": False,
                        "missing": [], "candidate_source_ids": [], "reason": "Evaluator unavailable.", "elapsed_ms": 0.0}
        decisions.append(decision)
        if not (unconditional and round_index == 0) and decision["decision"] == "SUFFICIENT":
            break
        if round_index == max_rounds or decision["decision"] == "INSUFFICIENT":
            break
        started = time.perf_counter()
        try:
            hits = store.search(" ".join(decision["missing"]) if decision["missing"] else query)
        except Exception:
            break
        if not hits:
            break
        existing = {(c.source_id, c.start, c.end) for c in working}
        ranked = []
        for hit in hits:
            for chunk in all_chunks:
                if chunk.source_id == hit["source_id"] and chunk.start < hit["end"] and chunk.end > hit["start"]:
                    if (chunk.source_id, chunk.start, chunk.end) not in existing:
                        ranked.append(chunk)
        additions = ranked[:4]
        for chunk in additions:
            if store.read(chunk.source_id, chunk.start, chunk.end) != chunk.text:
                raise ValueError("Source hash/range mismatch")
            working.append(chunk)
        rounds.append({"round": round_index + 1, "added": len(additions), "elapsed_ms": (time.perf_counter() - started) * 1000})
        if not additions:
            break
    before_fallback = [{"source_id": c.source_id, "start": c.start, "end": c.end} for c in working]
    fallback = decisions[-1]["decision"] != "SUFFICIENT"
    if fallback:
        working = list(all_chunks)  # Complete raw context; no silent inadequate prompt.
    return working, {"decisions": decisions, "rounds": rounds, "full_fallback": fallback,
                     "before_fallback_spans": before_fallback}


def metadata_json(chunks):
    return [asdict(c) for c in chunks]
