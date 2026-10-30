"""Explainable formation evidence model ("ML proposes" — the GeoReasoner disposes).

For an interval, every unit of the local column receives a score in log-likelihood points:

    score(u) = T * sum_f w_f * signature_u(f)          (description features, see description.py)
             + stated evidence (GEOL_BGS, GEOL_GEOL, GEOL_FORM, names in the text)
             + optional learned model log-probabilities (agsgeomodel.ai)

Scores are *relative* (only differences matter). Each contribution is kept as an
:class:`~agsgeomodel.models.EvidenceItem`, so every decision can be explained feature by feature.
This module never enforces stratigraphic order — that is the job of :mod:`.sequence`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from ..core.config import ReasonerConfig
from ..models.entities import EvidenceItem, Interval
from .description import ParsedDescription, parse_description
from .knowledge.kb import StratColumn

_TENTATIVE_CODE = ("?",)


class LearnedModel(Protocol):
    """Optional data-driven model: returns log-probabilities over column units for a parsed interval."""

    def log_proba(self, parsed: ParsedDescription, interval: Interval) -> dict[str, float]: ...


@dataclass
class Evidence:
    units: list[str]
    scores: np.ndarray  # (n_units,)
    contributions: dict[str, np.ndarray]  # feature -> contribution per unit (explainability)
    parsed: ParsedDescription
    stated_units: list[tuple[str, str, float]] = field(default_factory=list)  # (unit, source field, weight)
    informative: bool = True
    notes: dict[str, str] = field(default_factory=dict)  # feature -> note

    def score(self, unit: str) -> float:
        return float(self.scores[self.units.index(unit)])

    def softmax(self, prior: np.ndarray | None = None) -> np.ndarray:
        s = self.scores + (np.log(prior) if prior is not None else 0.0)
        s = s - s.max()
        p = np.exp(s)
        return p / p.sum()

    @property
    def items(self) -> dict[str, list[EvidenceItem]]:
        return {u: self.explain(u, 10_000) for u in self.units}

    def explain(self, unit: str, n: int = 6) -> list[EvidenceItem]:
        k = self.units.index(unit)
        out = [
            EvidenceItem(f, unit, float(v[k]), self.notes.get(f, ""))
            for f, v in self.contributions.items()
            if abs(v[k]) > 1e-12
        ]
        out.sort(key=lambda e: -abs(e.weight))
        return out[:n]


class FormationClassifier:
    def __init__(
        self,
        column: StratColumn,
        config: ReasonerConfig | None = None,
        code_aliases: dict[str, str] | None = None,
        learned: LearnedModel | None = None,
        learned_weight: float = 1.0,
        cache_size: int = 200_000,
    ):
        self.column = column
        self.cfg = config or ReasonerConfig()
        self.aliases = code_aliases or {}
        self.learned = learned
        self.learned_weight = learned_weight
        self.units = list(column.units)
        self._uidx = {u: i for i, u in enumerate(self.units)}
        feats = sorted({f for u in self.units for f in column.kb[u].signature})
        self._sigvec = {f: np.array([column.kb[u].signature.get(f, 0.0) for u in self.units]) for f in feats}
        self._cache: dict[tuple, Evidence] = {}
        self._cache_size = cache_size

    def _onehot(self, units, w: float) -> np.ndarray:
        v = np.zeros(len(self.units))
        for u in units:
            v[self._uidx[u]] = w
        return v

    # ------------------------------------------------------------------ public
    def evidence(self, iv: Interval) -> Evidence:
        key = (iv.description, iv.bgs_code, iv.geology_code, iv.geology_code2, iv.formation_name)
        if self.learned is None:
            hit = self._cache.get(key)
            if hit is not None:
                return hit
        ev = self._evidence(iv)
        if self.learned is None and len(self._cache) < self._cache_size:
            self._cache[key] = ev
        return ev

    def _evidence(self, iv: Interval) -> Evidence:
        parsed = parse_description(iv.description, self.column.kb)
        tau = self.cfg.temperature
        contrib: dict[str, np.ndarray] = {}
        notes: dict[str, str] = {}
        # 1) description features against unit signatures
        for feat, w in parsed.features.items():
            if feat.startswith("geo:"):
                key = feat[4:]
                targets = self.column.resolve(key) if key.isupper() else set()
                if targets:
                    contrib[feat] = self._onehot(targets, self.cfg.stated_text_log_lr * w)
                    notes[feat] = "unit named in description"
                    continue
            sv = self._sigvec.get(feat)
            if sv is not None:
                contrib[feat] = tau * w * sv
        # 2) stated interpretation fields
        stated = []
        for field_name, value, weight in (
            ("GEOL_BGS", iv.bgs_code, self.cfg.stated_code_log_lr),
            ("GEOL_GEOL", iv.geology_code, self.cfg.stated_geol_log_lr),
            ("GEOL_GEO2", iv.geology_code2, 0.5 * self.cfg.stated_geol_log_lr),
        ):
            if not value:
                continue
            tentative = any(t in value for t in _TENTATIVE_CODE)
            targets = self.column.resolve(value.replace("?", ""), self.aliases)
            w = weight * (self.cfg.tentative_factor if tentative else 1.0)
            if targets:
                f = f"{field_name}={value}"
                contrib[f] = self._onehot(targets, w)
                notes[f] = "logged interpretation"
                stated.extend((u, field_name, w) for u in targets)
        if iv.formation_name:
            tentative = any(t in iv.formation_name.lower() for t in ("?", "possibl", "probabl"))
            w = self.cfg.stated_text_log_lr * (self.cfg.tentative_factor if tentative else 1.0)
            targets = set()
            for code, _alias in self.column.kb.find_in_text(iv.formation_name):
                targets |= self.column.resolve(code)
            if targets:
                f = f"GEOL_FORM={iv.formation_name}"
                contrib[f] = self._onehot(targets, w)
                notes[f] = "logged formation name"
                stated.extend((u, "GEOL_FORM", w) for u in targets)
        # 3) learned model (optional)
        if self.learned is not None and not parsed.no_information:
            lp = self.learned.log_proba(parsed, iv)
            if lp:
                vals = np.array([lp.get(u, np.log(1e-6)) for u in self.units])
                contrib["learned_model"] = self.learned_weight * (vals - vals.mean())
                notes["learned_model"] = "trained classifier"
        scores = np.sum(list(contrib.values()), axis=0) if contrib else np.zeros(len(self.units))
        informative = not parsed.no_information or bool(stated)
        return Evidence(self.units, scores, contrib, parsed, stated, informative, notes)


def presence_prior(column: StratColumn) -> np.ndarray:
    p = np.array([column.presence(u) for u in column.units])
    return p / p.sum()


def describe_decision(ev: Evidence, unit: str, runner_up: str | None = None, n: int = 4) -> str:
    pos = [e for e in ev.explain(unit, 12) if e.weight > 0][:n]
    neg = [e for e in ev.explain(unit, 12) if e.weight < 0][:2]
    parts = []
    if pos:
        parts.append("for: " + ", ".join(f"{e.feature} ({e.weight:+.1f})" for e in pos))
    if neg:
        parts.append("against: " + ", ".join(f"{e.feature} ({e.weight:+.1f})" for e in neg))
    if runner_up:
        parts.append(f"margin over {runner_up}: {ev.score(unit) - ev.score(runner_up):+.1f}")
    return "; ".join(parts) if parts else "no diagnostic description evidence (sequence/neighbour inference)"
