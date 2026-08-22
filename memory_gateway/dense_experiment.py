"""Offline dense-retrieval primitives for the Phase 2.0 experiment only.

This module is intentionally not imported by the gateway. It provides a real
ONNX sentence encoder, an in-memory cosine index, and serialization helpers so
the experiment can measure incremental semantic recall without changing FTS,
context assembly, or injection policy.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class DenseMemoryCandidate:
    memory_id: str
    score: float


class OnnxTextEmbedder:
    """Mean-pooled, L2-normalized embeddings from a local ONNX encoder."""

    def __init__(
        self,
        *,
        model_path: str | Path,
        tokenizer_path: str | Path,
        max_length: int = 256,
        providers: list[str] | None = None,
    ) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        if max_length <= 0:
            raise ValueError("max_length must be positive")
        self.model_path = str(model_path)
        self.tokenizer_path = str(tokenizer_path)
        self.max_length = max_length
        self.tokenizer = Tokenizer.from_file(self.tokenizer_path)
        self.tokenizer.enable_truncation(max_length=max_length)
        self.tokenizer.enable_padding()
        self.session = ort.InferenceSession(
            self.model_path,
            providers=providers or ["CPUExecutionProvider"],
        )
        self.output_name = self.session.get_outputs()[0].name

    @property
    def dimension(self) -> int:
        shape = self.session.get_outputs()[0].shape
        return int(shape[-1])

    def embed(self, texts: Iterable[str]) -> np.ndarray:
        import numpy as np

        values = [str(text) for text in texts]
        if not values:
            return np.empty((0, self.dimension), dtype=np.float32)
        encoded = self.tokenizer.encode_batch(values)
        input_ids = np.asarray([item.ids for item in encoded], dtype=np.int64)
        attention_mask = np.asarray(
            [item.attention_mask for item in encoded],
            dtype=np.int64,
        )
        token_type_ids = np.asarray(
            [item.type_ids for item in encoded],
            dtype=np.int64,
        )
        hidden = self.session.run(
            [self.output_name],
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )[0]
        mask = attention_mask.astype(np.float32)[..., None]
        pooled = (hidden * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1e-9)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        return (pooled / np.maximum(norms, 1e-12)).astype(np.float32)


class DenseMemoryIndex:
    """Exact in-memory cosine search over precomputed memory vectors."""

    def __init__(self, memory_ids: list[str], embeddings: np.ndarray) -> None:
        import numpy as np

        vectors = np.asarray(embeddings, dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[0] != len(memory_ids):
            raise ValueError("embedding rows must match memory IDs")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = vectors / np.maximum(norms, 1e-12)
        self.memory_ids = tuple(str(memory_id) for memory_id in memory_ids)
        self.embeddings = vectors

    @classmethod
    def build(
        cls,
        memories: list[dict[str, Any]],
        embedder: OnnxTextEmbedder,
    ) -> "DenseMemoryIndex":
        texts = [str(memory["content"]) for memory in memories]
        return cls(
            [str(memory["id"]) for memory in memories],
            embedder.embed(texts),
        )

    @classmethod
    def load(cls, path: str | Path) -> "DenseMemoryIndex":
        import numpy as np

        with np.load(path, allow_pickle=False) as data:
            memory_ids = [str(value) for value in data["memory_ids"].tolist()]
            embeddings = np.asarray(data["embeddings"], dtype=np.float32)
        return cls(memory_ids, embeddings)

    def save(self, path: str | Path) -> None:
        import numpy as np

        np.savez_compressed(
            path,
            memory_ids=np.asarray(self.memory_ids),
            embeddings=self.embeddings,
        )

    def search(
        self,
        query_embedding: np.ndarray,
        *,
        top_k: int = 5,
    ) -> tuple[DenseMemoryCandidate, ...]:
        import numpy as np

        if top_k <= 0:
            return ()
        query = np.asarray(query_embedding, dtype=np.float32).reshape(-1)
        if query.shape[0] != self.embeddings.shape[1]:
            raise ValueError("query embedding dimension does not match index")
        query = query / max(float(np.linalg.norm(query)), 1e-12)
        scores = self.embeddings @ query
        order = sorted(
            range(len(self.memory_ids)),
            key=lambda index: (-float(scores[index]), self.memory_ids[index]),
        )[:top_k]
        return tuple(
            DenseMemoryCandidate(
                memory_id=self.memory_ids[index],
                score=float(scores[index]),
            )
            for index in order
        )


def model_metadata(
    *,
    model_id: str,
    revision: str,
    model_path: str | Path,
    tokenizer_path: str | Path,
    embedder: OnnxTextEmbedder,
) -> dict[str, Any]:
    return {
        "model_id": model_id,
        "revision": revision,
        "model_path": str(model_path),
        "tokenizer_path": str(tokenizer_path),
        "model_sha256": sha256_file(model_path),
        "tokenizer_sha256": sha256_file(tokenizer_path),
        "embedding_dimension": embedder.dimension,
        "max_length": embedder.max_length,
        "pooling": "attention_mask_mean_then_l2_normalize",
        "similarity": "cosine_dot_product",
    }


def write_json(path: str | Path, value: Any) -> None:
    Path(path).write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
