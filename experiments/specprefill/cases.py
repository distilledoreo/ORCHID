"""128 sealed synthetic cases: eight categories, disjoint development/test seeds."""

import random

CATEGORIES = ("redundant", "needle", "multi_hop", "temporal", "dense", "multi_turn", "false_confidence", "unanswerable")


def build_cases():
    cases = []
    for category_index, category in enumerate(CATEGORIES):
        for index in range(16):
            rng = random.Random(20261015 + category_index * 1000 + index)
            ident = f"{category_index}{index:02d}"
            key, answer = f"record_{ident}", f"VALUE{rng.randrange(10000, 99999)}"
            length = 128 if index in {7, 15} else 32
            rows = [f"diagnostic {n}: checksum {rng.randrange(100000,999999)}; subsystem component_{n}; status normal.\n" for n in range(length)]
            a, b = sorted(rng.sample(range(3, length - 3), 2))
            required = []
            second = None
            if category == "redundant":
                rows = ["Periodic health check: all routine subsystems are normal.\n"] * (length * 3 // 2)
                evidence = f"Binding decision: {key} has value {answer}."
                rows[a] = evidence + "\n"
                query = f"What is the value of `{key}`?"
                required = [("s1", evidence)]
            elif category in {"needle", "multi_turn"}:
                evidence = f"Configuration: {key} = {answer}."
                rows[a] = evidence + "\n"
                query = f"Return the exact value assigned to `{key}`."
                required = [("s1", evidence)]
            elif category == "multi_hop":
                route = f"route_{ident}"
                evidence = f"Shipment {key} follows {route}."
                rows[a] = evidence + "\n"
                second = f"Routing registry: {route} terminates at {answer}.\n"
                query = f"What destination does shipment `{key}` terminate at?"
                required = [("s1", evidence), ("s2", second.strip())]
            elif category in {"temporal", "false_confidence"}:
                rows[a] = f"2026-10-01: {key} = OLD{ident}; approved preliminary value.\n"
                evidence = f"2026-10-12 signed correction supersedes all earlier entries: {key} = {answer}."
                rows[b] = evidence + "\n"
                query = (f"What is the latest current value of `{key}`?" if category == "temporal"
                         else f"Return the governing approved value of `{key}`.")
                required = [("s1", evidence)]
            elif category == "dense":
                values = [rng.randrange(10, 90) for _ in range(length * 3 // 4)]
                rows = [f"invoice_{ident}_{n},amount={value}\n" for n, value in enumerate(values)]
                query = "Sum every amount in the complete invoice table. Return only the integer sum."
                answer = str(sum(values))
                required = [("s1", row.strip()) for row in rows]
            else:
                rows[a] = f"A related record decoy_{ident} = {answer}; no value for other records.\n"
                query, answer = f"What value was assigned to `{key}`?", "UNKNOWN"
            sources = [{"id": "s1", "document_id": f"doc-{ident}", "text": "".join(rows)}]
            if second:
                sources.append({"id": "s2", "document_id": f"registry-{ident}", "text": second})
            supporting = []
            for source_id, text in required:
                source = next(s for s in sources if s["id"] == source_id)
                start = source["text"].index(text)
                supporting.append({"source_id": source_id, "start": start, "end": start + len(text), "text": text})
            cases.append({"id": f"{category}-{index:02d}", "category": category,
                          "split": "development" if index < 8 else "held_out", "query": query,
                          "sources": sources, "expected_answer": answer, "supporting_spans": supporting,
                          "answerable": category != "unanswerable", "deferred_evidence_recoverable": bool(supporting),
                          "prior_query": "Report routine diagnostic health." if category == "multi_turn" else None})
    return cases
