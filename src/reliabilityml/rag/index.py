"""Embeddings and a small vector store.

Embedders share one interface. The default, `lsa`, is TF-IDF followed by truncated SVD: it runs
in a few MB of memory, which matters on a free-tier host. `tfidf` (sparse lexical) and
`sentence-transformers` (dense neural, optional extra) are evaluated against it on the same
question set; see eval/reports.

The store is an in-memory matrix with cosine similarity. With a few dozen chunks this is exact
and instant; Chroma or pgvector would slot in behind the same `search` method at larger scale.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from .corpus import Chunk


class TfidfEmbedder:
    name = "tfidf"

    def __init__(self):
        self.vectorizer = TfidfVectorizer(
            ngram_range=(1, 2), sublinear_tf=True, stop_words="english", token_pattern=r"(?u)\b[\w-]{2,}\b"
        )

    def fit(self, texts: list[str]):
        self.vectorizer.fit(texts)
        return self

    def encode(self, texts: list[str]) -> np.ndarray:
        return normalize(self.vectorizer.transform(texts)).toarray()


class LsaEmbedder(TfidfEmbedder):
    name = "lsa"

    def __init__(self, dims: int = 48, blend: float = 0.5):
        super().__init__()
        self.dims = dims
        self.blend = blend  # share of the lexical signal kept alongside the latent one
        self.svd: TruncatedSVD | None = None

    def fit(self, texts: list[str]):
        matrix = self.vectorizer.fit_transform(texts)
        self.svd = TruncatedSVD(n_components=min(self.dims, matrix.shape[1] - 1, len(texts) - 1), random_state=0)
        self.svd.fit(matrix)
        return self

    def encode(self, texts: list[str]) -> np.ndarray:
        sparse = normalize(self.vectorizer.transform(texts))
        latent = normalize(self.svd.transform(sparse))  # type: ignore[union-attr]
        # Concatenate scaled lexical and latent parts: cosine = blend*lexical + (1-blend)*latent.
        return np.hstack([np.sqrt(self.blend) * sparse.toarray(), np.sqrt(1 - self.blend) * latent])


class SentenceTransformerEmbedder:
    name = "sentence-transformers"

    def __init__(self, model: str = "all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer  # optional dependency

        self.model_name = model
        self.model = SentenceTransformer(model)

    def fit(self, texts: list[str]):
        return self

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.model.encode(texts, normalize_embeddings=True))


def make_embedder(name: str):
    if name == "tfidf":
        return TfidfEmbedder()
    if name == "lsa":
        return LsaEmbedder()
    if name == "sentence-transformers":
        return SentenceTransformerEmbedder()
    raise ValueError(name)


class VectorStore:
    def __init__(self, embedder, chunks: list[Chunk]):
        self.embedder = embedder
        self.chunks = chunks
        texts = [c.embed_text for c in chunks]
        self.embedder.fit(texts)
        self.matrix = self.embedder.encode(texts)

    def search(self, query: str, k: int = 4) -> list[tuple[Chunk, float]]:
        q = self.embedder.encode([query])[0]
        scores = self.matrix @ q
        order = np.argsort(scores)[::-1][:k]
        return [(self.chunks[i], float(scores[i])) for i in order]

    def by_id(self, chunk_id: str) -> Chunk | None:
        return next((c for c in self.chunks if c.chunk_id == chunk_id), None)

    def save_manifest(self, path: Path) -> None:
        path.write_text(json.dumps([asdict(c) for c in self.chunks], indent=2))
