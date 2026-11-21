"""Text embedders behind one interface.

`SentenceTransformerEmbedder` wraps all-MiniLM-L6-v2 (or whatever EMBED_MODEL
names). `HashEmbedder` is a deterministic bag-of-words stand-in selected with
EMBED_MODEL=hash: tests and CI use it so they never download a model, and it
still ranks texts that share words above texts that do not.
"""

import hashlib
import logging
import math
import re
from functools import lru_cache
from typing import Protocol

from app.config import get_settings

HASH_MODEL = "hash"
_TOKEN = re.compile(r"[a-z0-9]+(?:[.\-/][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens; keeps clinical tokens like 'hba1c', '9.8', 'mg/dl' whole."""
    return _TOKEN.findall(text.lower())


class Embedder(Protocol):
    name: str
    dimension: int

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    name = HASH_MODEL

    def __init__(self, dimension: int = 256):
        self.dimension = dimension

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dimension
        for token in tokenize(text):
            digest = hashlib.blake2b(token.encode(), digest_size=4).digest()
            vec[int.from_bytes(digest, "big") % self.dimension] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        for noisy in ("httpx", "httpx2", "huggingface_hub", "sentence_transformers"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        self.name = model_name
        try:
            # Cached model: skip the Hub round-trips that otherwise cost tens of seconds.
            self._model = SentenceTransformer(model_name, local_files_only=True)
        except (OSError, ValueError):
            self._model = SentenceTransformer(model_name)
        get_dim = getattr(self._model, "get_embedding_dimension", None) or (
            self._model.get_sentence_embedding_dimension
        )
        self.dimension = int(get_dim())

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(
            texts, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False
        )
        return vectors.tolist()


class OnnxEmbedder:
    """The same sentence-transformer model through fastembed (onnxruntime): no torch,
    a few hundred MB less resident memory. Vectors are L2-normalised like the torch path."""

    def __init__(self, model_name: str):
        import numpy as np
        from fastembed import TextEmbedding

        self.name = f"{model_name} (onnx)"
        self._np = np
        self._model = TextEmbedding(model_name=model_name, threads=1)
        self.dimension = len(next(iter(self._model.embed(["dim probe"]))))

    def encode(self, texts: list[str]) -> list[list[float]]:
        out = []
        for vec in self._model.embed(texts, batch_size=16):
            norm = float(self._np.linalg.norm(vec)) or 1.0
            out.append((vec / norm).astype(float).tolist())
        return out


@lru_cache(maxsize=4)
def _cached(model_name: str, backend: str) -> Embedder:
    if model_name == HASH_MODEL:
        return HashEmbedder()
    if backend == "onnx":
        return OnnxEmbedder(model_name)
    return SentenceTransformerEmbedder(model_name)


def get_embedder(model_name: str | None = None, backend: str | None = None) -> Embedder:
    """The configured embedder, loaded once per process."""
    s = get_settings()
    return _cached(model_name or s.embed_model, backend or s.embed_backend)
