from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from experiments.specprefill.cases import CATEGORIES, build_cases
from experiments.specprefill.core import (
    EvidenceStore,
    digest,
    evidence_gate,
    make_chunks,
    recover,
    render,
    score_chunks,
    select,
    smooth,
    source_inventory,
)
from experiments.specprefill.run import recall, sufficient


class CharacterTokenizer:
    def encode(self, text, add_special_tokens=False):
        return SimpleNamespace(ids=list(range(len(text))), offsets=[(i, i + 1) for i in range(len(text))])


def test_corpus_is_reproducible_disjoint_and_grounded():
    cases = build_cases()
    assert cases == build_cases() and len(cases) == 128
    assert {c["category"] for c in cases} == set(CATEGORIES)
    assert len({c["id"] for c in cases}) == 128
    assert sum(c["split"] == "held_out" for c in cases) == 64
    for case in cases:
        for evidence in case["supporting_spans"]:
            source = next(s for s in case["sources"] if s["id"] == evidence["source_id"])
            assert source["text"][evidence["start"]:evidence["end"]] == evidence["text"]


def test_utf8_offsets_are_exact_and_preserved_in_metadata():
    text = "λ雪\nαβγ\nUTF-8 text"
    chunks = make_chunks("s", "doc", text, CharacterTokenizer(), 3)
    assert "".join(c.text for c in chunks) == text
    for chunk in chunks:
        assert text.encode()[chunk.byte_start:chunk.byte_end].decode() == chunk.text
        assert chunk.source_hash == digest(text)
        assert asdict(chunk)["document_id"] == "doc"


def test_selection_uses_tokenizer_including_range_headers():
    tokenizer = CharacterTokenizer()
    text = "abcdefgh" * 100
    sources = [{"id": "s", "text": text}]
    chunks = make_chunks("s", "doc", text, tokenizer, 32)
    scored = [replace(c, importance=float(c.start)) for c in chunks]
    selected, metadata, budget = select(scored, sources, tokenizer, 0.3)
    assert len(tokenizer.encode(render(selected, sources, inventory=False)).ids) <= budget
    assert any(c.retained for c in metadata)
    assert render(selected, sources).endswith(text[-32:])
    with pytest.raises(ValueError):
        select(scored, sources, tokenizer, 0)


def test_scores_are_query_specific_not_permanent_suppression():
    chunks = make_chunks("s", "doc", "ALPHA=" + "x" * 58 + "BETA=" + "y" * 59, CharacterTokenizer(), 64)
    first = score_chunks(chunks, "ALPHA")
    later = score_chunks(chunks, "BETA")
    assert first[0].importance > first[1].importance
    assert later[1].importance > later[0].importance
    assert first[0].query_hash != later[0].query_hash


def test_attention_mapping_and_zero_padded_pooling():
    chunks = make_chunks("s", "doc", "abcd", CharacterTokenizer(), 2)
    trace = {"scores": [0.0, 0.1, 0.8, 0.1], "reference_scores": [0.2] * 4,
             "offsets": [(0, 1), (1, 2), (2, 3), (3, 4)], "source_locations": {"s": [0, 4]}}
    scored = score_chunks(chunks, "query", trace)
    assert scored[1].importance > scored[0].importance
    assert scored[1].speculator_token_start == 2 and scored[1].speculator_token_end == 4
    assert smooth([1.0, 1.0, 1.0], 3) == pytest.approx([2 / 3, 1, 2 / 3])


def test_coverage_gate_does_not_claim_calibrated_certainty():
    chunks = make_chunks("s", "doc", "record_123 = OLD. " + "filler " * 100, CharacterTokenizer(), 32)
    selected = chunks[:1]
    decision = evidence_gate("What is `record_123`?", selected, source_inventory([{"id": "s", "text": "".join(c.text for c in chunks)}]), lambda _: [])
    # Intentional false-confidence limitation: covered identifiers are not entailment.
    assert decision["decision"] == "SUFFICIENT"
    assert decision["calibrated"] is False and decision["score"] == 0.5


def test_scoped_recovery_and_two_round_limit(tmp_path):
    case = next(c for c in build_cases() if c["category"] == "needle")
    tokenizer = CharacterTokenizer()
    chunks = [chunk for source in case["sources"] for chunk in make_chunks(source["id"], source["document_id"], source["text"], tokenizer, 128)]
    store = EvidenceStore(tmp_path / "memory.db", "p", case["id"], case["sources"])
    recovered, report = recover(case["query"], [], chunks, case["sources"], store)
    assert len(report["rounds"]) <= 2
    assert recall(case, recovered) == 1 and sufficient(case, recovered)
    assert store.read("s1", 0, len(case["sources"][0]["text"])) == case["sources"][0]["text"]
    with pytest.raises(ValueError):
        recover(case["query"], [], chunks, case["sources"], store, max_rounds=3)
    store.store.ensure_project_and_thread("other", "other")
    assert store.recall.read("other", "other", store.ids["s1"]) is None


def test_unanswerable_questions_do_not_repeat_retrieval(tmp_path):
    case = next(c for c in build_cases() if c["category"] == "unanswerable")
    store = EvidenceStore(tmp_path / "memory.db", "p", case["id"], case["sources"])
    chunks = [chunk for source in case["sources"] for chunk in make_chunks(source["id"], source["document_id"], source["text"], CharacterTokenizer(), 64)]
    result, report = recover(case["query"], [], chunks, case["sources"], store)
    assert report["full_fallback"]
    assert report["rounds"] == []
    assert sufficient(case, result)


def test_adaptive_policy_keeps_dense_context_without_learning():
    source = {"id": "s", "text": "amount=10\n" * 100}
    chunks = make_chunks("s", "doc", source["text"], CharacterTokenizer(), 64)
    selected, _, _ = select(chunks, [source], CharacterTokenizer(), 0.1, adaptive=True, query="Sum every amount.")
    assert len(selected) == len(chunks)


def test_experimental_imports_do_not_load_model_dependencies_in_production():
    import subprocess
    import sys
    script = "import sys; import memory_gateway.gateway; print(int('torch' in sys.modules), int('transformers' in sys.modules))"
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "0 0"
