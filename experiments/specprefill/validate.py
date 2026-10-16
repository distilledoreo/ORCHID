"""Validate measured artifacts and exact reconstruction with the real tokenizer."""

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from .core import Chunk, covered_length, digest, render
from .report import load
from .run import make_all_chunks, recall
from .tokenization import load_tokenizer


def validate(root, tokenizer_path):
    root = Path(root)
    cases = json.loads((root / "corpus.json").read_text())
    corpus = {c["id"]: c for c in cases}
    manifest = json.loads((root / "manifest.json").read_text())
    tokenizer = load_tokenizer(tokenizer_path)
    requests = load(root / "requests.jsonl")
    pairs = Counter((r["case"], r["arm"]) for r in requests)
    assert len(cases) == 128 and Counter(c["split"] for c in cases) == {"development": 64, "held_out": 64}
    assert len(pairs) == 768 and all(pairs[c, arm] == 1 for c in corpus for arm in "ABCDEF")
    assert manifest["corpus_hash"] == digest(json.dumps(cases, sort_keys=True))
    assert manifest["cases_executed"] == 128 and manifest["position_restoration"] is False
    assert manifest["candidate_chunking_in_ttft"] is True
    for row in requests:
        case = corpus[row["case"]]
        assert (row["split"], row["category"]) == (case["split"], case["category"])
        assert "main_error" not in row and row["main"]["answer"]
        assert row["end_to_end_ttft_ms"] > 0 and row["main"]["usage"]["prompt_tokens"] > 0
        assert row["candidate_chunking_ms"] > 0
        assert row["end_to_end_ttft_ms"] >= row["ingestion_ms"] + row["candidate_chunking_ms"]
        assert row["main"]["correct"] == (row["main"]["answer"].casefold() == case["expected_answer"].casefold())
        for name in ("retrieval", "post_answer_retrieval"):
            assert len((row.get(name) or {}).get("rounds", [])) <= 2
        if row["arm"] == "A":
            full = make_all_chunks(case, tokenizer, manifest["chunk_size"])
            assert all(covered_length(full, s["id"]) == len(s["text"]) for s in case["sources"])
            text = render(full, case["sources"], inventory=False)
            assert all(s["text"] in text for s in case["sources"])
            assert len(tokenizer.encode(text, add_special_tokens=False).ids) == row["main_context_tokens"]
    by_pair = {(r["case"], r["arm"]): r for r in requests}
    chunks = load(root / "chunks.jsonl")
    assert Counter((r["case"], r["arm"]) for r in chunks) == Counter({(c, a): 1 for c in corpus for a in "CDE"})
    count = 0
    for row in chunks:
        case = corpus[row["case"]]
        sources = {s["id"]: s for s in case["sources"]}
        expected = make_all_chunks(case, tokenizer, manifest["chunk_size"])
        assert [(c.source_id, c.start, c.end, c.token_start, c.token_end) for c in expected] == [
            (c["source_id"], c["start"], c["end"], c["token_start"], c["token_end"]) for c in row["chunks"]]
        for chunk in row["chunks"]:
            source = sources[chunk["source_id"]]
            assert chunk["source_hash"] == digest(source["text"])
            assert chunk["document_id"] == source["document_id"]
            assert chunk["text"] == source["text"][chunk["start"]:chunk["end"]]
            assert chunk["text"].encode() == source["text"].encode()[chunk["byte_start"]:chunk["byte_end"]]
            assert chunk["query_hash"] == row["query_hash"] == digest(case["query"])
            assert math.isfinite(chunk["importance"])
            count += 1
        selected = [Chunk(**c) for c in row["chunks"] if c["retained"]]
        assert len(tokenizer.encode(render(selected, case["sources"], inventory=False), add_special_tokens=False).ids) <= row["budget"]
        assert recall(case, selected) == by_pair[row["case"], row["arm"]]["initial_evidence_recall"]
    traces = load(root / "attention-traces.jsonl")
    assert Counter(r["case"] for r in traces) == Counter({c: 1 for c in corpus})
    for trace in traces:
        assert trace["corpus_hash"] == manifest["corpus_hash"] and trace["attention_layers"] == 6
        assert trace["query_rows"] == trace["output_tokens"] + 1 <= 9
        for key in ("scores", "reference_scores", "zero_scores", "reference_zero_scores"):
            assert len(trace[key]) == len(trace["offsets"]) == trace["input_tokens"]
            assert all(math.isfinite(v) and v >= 0 for v in trace[key])
    grid = load(root / "ablations.jsonl")
    assert Counter(r["case"] for r in grid) == Counter({c: 101 for c in corpus})
    for filename in ("classifier.jsonl", "classifier-recovery.jsonl"):
        assert Counter(r["case"] for r in load(root / filename)) == Counter({c: 1 for c in corpus})
    quality = load(root / "quality-ablations.jsonl")
    plan = json.loads((root / "quality-protocol.json").read_text())
    assert Counter((r["case"], r["condition"]) for r in quality) == Counter({
        (c, condition["id"]): 1 for c in plan["cases"] for condition in plan["conditions"]})
    assert len(quality) == 128 and all("main_error" not in r and r.get("main") for r in quality)
    result = {"passed": True, "requests": len(requests), "chunks_checked": count, "traces": len(traces),
              "ablation_rows": len(grid), "exact_full_source_reconstruction": True,
              "focused_quality_requests": len(quality),
              "raw_file_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.glob("*.jsonl"))}}
    (root / "integrity.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    parser.add_argument("--main-tokenizer", required=True)
    args = parser.parse_args()
    result = validate(args.directory, args.main_tokenizer)
    print(json.dumps({k: v for k, v in result.items() if k != "raw_file_sha256"}))
