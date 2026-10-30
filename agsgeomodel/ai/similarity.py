"""Description similarity for correlation: sentence embeddings when available, TF-IDF otherwise.

``sentence-transformers`` models must be available locally (the plugin never downloads models
silently); without them a character n-gram TF-IDF cosine similarity is used, which is robust to
spelling variants and abbreviations common in logs.
"""

from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np

from ..core.optional import optional_import


def _grams(text: str, n: int = 3) -> Counter:
    t = " " + re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()) + " "
    t = re.sub(r"\s+", " ", t)
    return Counter(t[i : i + n] for i in range(max(0, len(t) - n + 1)))


class TfidfSimilarity:
    name = "tfidf-char3"

    def fit(self, texts: list[str]) -> TfidfSimilarity:
        docs = [_grams(t) for t in texts]
        df = Counter(g for d in docs for g in d)
        n = max(len(docs), 1)
        self.idf = {g: math.log((1 + n) / (1 + c)) + 1.0 for g, c in df.items()}
        return self

    def embed(self, texts: list[str]) -> list[dict[str, float]]:
        out = []
        for t in texts:
            g = _grams(t)
            v = {k: c * self.idf.get(k, 1.0) for k, c in g.items()}
            norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
            out.append({k: x / norm for k, x in v.items()})
        return out

    def matrix(self, a: list[str], b: list[str]) -> np.ndarray:
        ea, eb = self.embed(a), self.embed(b)
        M = np.zeros((len(ea), len(eb)))
        for i, va in enumerate(ea):
            for j, vb in enumerate(eb):
                small, big = (va, vb) if len(va) < len(vb) else (vb, va)
                M[i, j] = sum(x * big.get(k, 0.0) for k, x in small.items())
        return M


class EmbeddingSimilarity:
    """Sentence-transformers backend (model must be installed / cached locally)."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        st = optional_import("sentence_transformers")
        if st is None:
            raise ImportError("sentence_transformers not installed")
        self.name = f"sentence-transformers:{model_name}"
        self.model = st.SentenceTransformer(model_name)

    def fit(self, texts):
        return self

    def matrix(self, a: list[str], b: list[str]) -> np.ndarray:
        ea = np.asarray(self.model.encode(a, normalize_embeddings=True))
        eb = np.asarray(self.model.encode(b, normalize_embeddings=True))
        return ea @ eb.T


def get_similarity(texts: list[str], prefer_embeddings: bool = True, model_name: str = "all-MiniLM-L6-v2"):
    if prefer_embeddings:
        try:
            return EmbeddingSimilarity(model_name).fit(texts)
        except Exception:
            pass
    return TfidfSimilarity().fit(texts)
