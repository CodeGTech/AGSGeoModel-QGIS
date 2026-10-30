"""Feature extraction for the learned formation model.

Features are the *same* human-readable tokens produced by the BS 5930 parser (``pt:clay``,
``incl:selenite``, ``col:blue_grey`` ...) plus coarse depth / elevation descriptors and optional
character n-grams. Using named features keeps every learned prediction explainable in geological
terms (see :meth:`TrainedFormationModel.explain`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from ..geology.description import parse_description


def interval_features(iv, ground_level: float | None = None, use_depth: bool = True) -> dict[str, float]:
    p = parse_description(iv.description)
    f = {k: float(v) for k, v in p.features.items() if not k.startswith("geo:") or not k[4:].isupper()}
    if use_depth:
        f["depth:mid_log1p"] = float(np.log1p(max(iv.mid, 0.0)))
        f["depth:thick_log1p"] = float(np.log1p(max(iv.thickness, 0.0)))
        if ground_level is not None:
            f["elev:mid_10m"] = float((ground_level - iv.mid) / 10.0)
    return f


@dataclass
class FeatureSpace:
    names: list[str] = field(default_factory=list)
    index: dict[str, int] = field(default_factory=dict)
    ngram: int = 0  # 0 = no character n-grams
    max_ngrams: int = 2000

    def fit(self, feature_dicts: list[dict[str, float]], texts: list[str] | None = None) -> FeatureSpace:
        names = sorted({k for d in feature_dicts for k in d})
        if self.ngram and texts:
            counts: dict[str, int] = {}
            for t in texts:
                for g in _ngrams(t, self.ngram):
                    counts[g] = counts.get(g, 0) + 1
            top = sorted(counts, key=lambda g: -counts[g])[: self.max_ngrams]
            names += [f"ng:{g}" for g in sorted(top)]
        self.names = names
        self.index = {n: i for i, n in enumerate(names)}
        return self

    def transform(self, feature_dicts: list[dict[str, float]], texts: list[str] | None = None) -> np.ndarray:
        X = np.zeros((len(feature_dicts), len(self.names)))
        for r, d in enumerate(feature_dicts):
            for k, v in d.items():
                j = self.index.get(k)
                if j is not None:
                    X[r, j] = v
            if self.ngram and texts:
                for g in _ngrams(texts[r], self.ngram):
                    j = self.index.get(f"ng:{g}")
                    if j is not None:
                        X[r, j] = 1.0
        return X

    def to_dict(self) -> dict:
        return {"names": self.names, "ngram": self.ngram, "max_ngrams": self.max_ngrams}

    @classmethod
    def from_dict(cls, d: dict) -> FeatureSpace:
        fs = cls(list(d["names"]), {}, int(d.get("ngram", 0)), int(d.get("max_ngrams", 2000)))
        fs.index = {n: i for i, n in enumerate(fs.names)}
        return fs


def _ngrams(text: str, n: int) -> set[str]:
    t = " " + re.sub(r"\s+", " ", (text or "").lower()) + " "
    return {t[i : i + n] for i in range(max(0, len(t) - n + 1))}
