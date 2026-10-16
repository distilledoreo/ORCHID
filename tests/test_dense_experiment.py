from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("onnxruntime")
pytest.importorskip("tokenizers")

import numpy as np

from memory_gateway.dense_experiment import DenseMemoryIndex


class _FakeEmbedder:
    dimension = 3

    def embed(self, texts):
        vectors = {
            "alpha": [1.0, 0.0, 0.0],
            "beta": [0.0, 1.0, 0.0],
            "gamma": [0.0, 0.0, 1.0],
        }
        return np.asarray([vectors[text] for text in texts], dtype=np.float32)


def test_dense_index_returns_stable_cosine_order():
    index = DenseMemoryIndex.build(
        [
            {"id": "mem_beta", "content": "beta"},
            {"id": "mem_alpha", "content": "alpha"},
            {"id": "mem_gamma", "content": "gamma"},
        ],
        _FakeEmbedder(),
    )

    candidates = index.search(np.asarray([1.0, 0.0, 0.0], dtype=np.float32), top_k=3)

    assert [candidate.memory_id for candidate in candidates] == [
        "mem_alpha",
        "mem_beta",
        "mem_gamma",
    ]
    assert candidates[0].score == 1.0


def test_dense_index_round_trip_preserves_search(tmp_path):
    index = DenseMemoryIndex(
        ["mem_b", "mem_a"],
        np.asarray([[0.0, 1.0], [1.0, 0.0]], dtype=np.float32),
    )
    path = tmp_path / "index.npz"

    index.save(path)
    loaded = DenseMemoryIndex.load(path)

    assert loaded.memory_ids == index.memory_ids
    assert np.array_equal(loaded.embeddings, index.embeddings)
    assert [candidate.memory_id for candidate in loaded.search([1.0, 0.0])] == [
        "mem_a",
        "mem_b",
    ]


def test_dense_index_rejects_dimension_mismatch():
    index = DenseMemoryIndex(
        ["mem_a"],
        np.asarray([[1.0, 0.0]], dtype=np.float32),
    )

    try:
        index.search([1.0, 0.0, 0.0])
    except ValueError as error:
        assert "dimension" in str(error)
    else:
        raise AssertionError("dimension mismatch should fail explicitly")
