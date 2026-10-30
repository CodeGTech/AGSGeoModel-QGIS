"""Order-constrained formation inference along a borehole (the core of the GeoReasoner).

The logged intervals of a hole are treated as observations of a hidden Markov chain whose states
are the units of the local stratigraphic column. Transitions encode geology:

* a unit may continue downwards with probability ``stay = stay_base * exp(-d / T_u)`` where
  ``d`` is the thickness of the next interval and ``T_u`` the unit's mean thickness;
* in a **strict** sequence the next unit must lie *below* in the column; skipping unit ``k``
  costs ``(1 - presence_k)`` (so missing a usually-present unit is unlikely but possible);
* in a **facies** sequence (superficial deposits) any member may follow, atypical stacking is
  penalised, and repetition is allowed where the province says so (e.g. till/outwash);
* moving *up* the column is impossible unless repetition has been enabled for that hole
  (reverse fault, glacial raft) by a reviewer.

Forward-backward gives a posterior probability for every interval (the confidence shown to the
user); the reported assignment is the maximum-expected-accuracy path (the order-consistent path
that maximises the expected number of correctly labelled metres), and the Viterbi (joint MAP) path
is kept for reference. Unlogged / no-recovery intervals are split into slices with uninformative
emissions so the chain can infer the missing sequence inside them (e.g. London Clay / ??? / Chalk
-> Lambeth Group and Thanet).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np

from ..core.config import ReasonerConfig
from ..models.entities import Interval
from .classifier import Evidence, FormationClassifier
from .knowledge.kb import StratColumn

NEG_INF = -np.inf


def _logsumexp(a: np.ndarray, axis: int) -> np.ndarray:
    m = np.max(a, axis=axis, keepdims=True)
    m_safe = np.where(np.isfinite(m), m, 0.0)
    with np.errstate(divide="ignore"):
        out = np.log(np.sum(np.exp(a - m_safe), axis=axis, keepdims=True)) + m_safe
    return np.squeeze(out, axis=axis)


def _log(x):
    with np.errstate(divide="ignore"):
        return np.log(x)


@dataclass
class Slice:
    top: float
    base: float
    source_index: int  # index of the logged interval this slice belongs to
    synthetic: bool  # True when produced by splitting an unlogged interval
    evidence: Evidence

    @property
    def thickness(self) -> float:
        return self.base - self.top


@dataclass
class SequenceResult:
    location_id: str
    states: list[str]
    slices: list[Slice]
    path: list[str]
    posterior: np.ndarray  # (n_slices, n_states)
    unconstrained: list[str]  # per slice: best unit from evidence alone
    unconstrained_prob: np.ndarray  # its softmax probability
    log_likelihood: float
    repetition_allowed: bool = False
    notes: list[str] = field(default_factory=list)
    map_path: list[str] = field(default_factory=list)  # Viterbi (joint MAP) path, for reference

    def prob(self, t: int, unit: str) -> float:
        return float(self.posterior[t, self.states.index(unit)])

    def alternatives(self, t: int, k: int = 3, min_p: float = 0.03) -> list[tuple[str, float]]:
        order = np.argsort(-self.posterior[t])
        out = []
        for s in order[: k + 1]:
            if self.states[s] != self.path[t] and self.posterior[t, s] >= min_p:
                out.append((self.states[s], round(float(self.posterior[t, s]), 3)))
        return out[:k]


def _move_matrix(column: StratColumn, cfg: ReasonerConfig, allow_up: bool) -> np.ndarray:
    S = len(column.units)
    M = np.zeros((S, S))
    for i, a in enumerate(column.units):
        w = column.move_weights(a, allow_up=False, reverse_weight=cfg.reverse_order_weight)
        for b, v in w.items():
            M[i, column.pos[b]] = v
        tot = M[i].sum()
        if tot > 0:
            M[i] /= tot
        if allow_up:
            up = [column.pos[b] for b in column.units if column.pos[b] < i]
            if up:
                M[i] *= 1.0 - cfg.repetition_probability * len(up)
                M[i, up] += cfg.repetition_probability
    return M


def build_slices(intervals: list[Interval], classifier: FormationClassifier, cfg: ReasonerConfig) -> list[Slice]:
    out: list[Slice] = []
    for k, iv in enumerate(intervals):
        ev = classifier.evidence(iv)
        if not ev.informative and iv.override is None and iv.thickness > 1.5 * cfg.unknown_split_m:
            n = int(math.ceil(iv.thickness / cfg.unknown_split_m))
            edges = np.linspace(iv.top, iv.base, n + 1)
            for a, b in zip(edges[:-1], edges[1:]):
                out.append(Slice(float(a), float(b), k, True, ev))
        else:
            out.append(Slice(iv.top, iv.base, k, False, ev))
    return out


def infer_sequence(
    location_id: str,
    intervals: list[Interval],
    column: StratColumn,
    classifier: FormationClassifier,
    cfg: ReasonerConfig | None = None,
    ground_level: float | None = None,
    elevation_prior=None,
    allow_repetition: bool = False,
) -> SequenceResult:
    """Infer the most likely stratigraphically consistent unit for every interval of one hole.

    ``elevation_prior(unit, z_top, z_base) -> log-likelihood bonus`` adds neighbour information in
    the second (correlation) pass.
    """
    cfg = cfg or ReasonerConfig()
    ivs = sorted(intervals, key=lambda i: (i.top, i.base))
    states = list(column.units)
    S = len(states)
    slices = build_slices(ivs, classifier, cfg)
    T = len(slices)
    if T == 0:
        return SequenceResult(location_id, states, [], [], np.zeros((0, S)), [], np.zeros(0), 0.0)
    E = np.zeros((T, S))
    for t, sl in enumerate(slices):
        E[t] = sl.evidence.scores
        iv = ivs[sl.source_index]
        if iv.override is not None:
            forced = column.resolve(iv.override.unit)
            E[t] = NEG_INF
            for u in forced:
                E[t, column.pos[u]] = 0.0
        if elevation_prior is not None and ground_level is not None:
            zt, zb = ground_level - sl.top, ground_level - sl.base
            for s, u in enumerate(states):
                E[t, s] += elevation_prior(u, zt, zb)
    if np.isneginf(E).all(axis=1).any():
        E[np.isneginf(E).all(axis=1)] = 0.0
    M = _move_matrix(column, cfg, allow_repetition)
    has_moves = M.sum(axis=1) > 0
    T_mean = np.array([max(column.column_unit(u).thickness_mean, 0.1) for u in states])
    start = np.array([column.start_weights()[u] for u in states])
    log_pi = _log(start / start.sum())

    def log_trans(t: int) -> np.ndarray:
        d = slices[t].thickness
        # Inside a split unlogged interval no boundary was drawn by the logger, so the exponential
        # thickness model applies on its own; at a logged boundary a change is a priori likelier.
        inside_gap = (
            slices[t].synthetic and slices[t - 1].synthetic and slices[t].source_index == slices[t - 1].source_index
        )
        base = 1.0 if inside_gap else cfg.stay_base
        stay = np.clip(base * np.exp(-d / T_mean), 0.02, 0.999)
        stay = np.where(has_moves, stay, 1.0)
        A = (1.0 - stay)[:, None] * M
        A[np.arange(S), np.arange(S)] = stay
        return _log(A)

    logA = [None] + [log_trans(t) for t in range(1, T)]
    # forward
    alpha = np.full((T, S), NEG_INF)
    alpha[0] = log_pi + E[0]
    for t in range(1, T):
        alpha[t] = _logsumexp(alpha[t - 1][:, None] + logA[t], axis=0) + E[t]
    logZ = float(_logsumexp(alpha[-1], axis=0))
    # backward
    beta = np.zeros((T, S))
    for t in range(T - 2, -1, -1):
        beta[t] = _logsumexp(logA[t + 1] + (E[t + 1] + beta[t + 1])[None, :], axis=1)
    post = np.exp(alpha + beta - logZ)
    post = post / np.maximum(post.sum(axis=1, keepdims=True), 1e-300)
    # Viterbi
    delta = np.full((T, S), NEG_INF)
    back = np.zeros((T, S), dtype=int)
    delta[0] = log_pi + E[0]
    for t in range(1, T):
        cand = delta[t - 1][:, None] + logA[t]
        back[t] = np.argmax(cand, axis=0)
        delta[t] = cand[back[t], np.arange(S)] + E[t]
    path_idx = [int(np.argmax(delta[-1]))]
    for t in range(T - 1, 0, -1):
        path_idx.append(int(back[t, path_idx[-1]]))
    path_idx.reverse()
    map_path = [states[i] for i in path_idx]
    # Maximum-expected-accuracy decoding: the order-consistent path maximising the expected
    # number of correctly labelled metres (posterior marginals x thickness). Unlike Viterbi it is
    # not biased against boundaries at unlogged depths, which matters for missing-layer inference.
    allowed = (M > 0) | np.eye(S, dtype=bool)
    allowed &= np.where(has_moves[:, None], True, np.eye(S, dtype=bool))
    gain = post * np.array([sl.thickness for sl in slices])[:, None]
    score = np.full((T, S), NEG_INF)
    bp = np.zeros((T, S), dtype=int)
    score[0] = np.where(np.isfinite(log_pi), gain[0], NEG_INF)
    for t in range(1, T):
        cand = np.where(allowed, score[t - 1][:, None], NEG_INF)
        bp[t] = np.argmax(cand, axis=0)
        score[t] = cand[bp[t], np.arange(S)] + gain[t]
    mea = [int(np.argmax(score[-1]))]
    for t in range(T - 1, 0, -1):
        mea.append(int(bp[t, mea[-1]]))
    mea.reverse()
    path = [states[i] for i in mea]
    # evidence-only labels (for inversion / conflict detection)
    prior = np.array([column.presence(u) for u in states])
    unconstrained, uprob = [], np.zeros(T)
    for t, sl in enumerate(slices):
        p = sl.evidence.softmax(prior)
        k = int(np.argmax(p))
        unconstrained.append(states[k])
        uprob[t] = p[k] if sl.evidence.informative else 0.0
    res = SequenceResult(location_id, states, slices, path, post, unconstrained, uprob, logZ, allow_repetition)
    res.map_path = map_path
    return res


def runs_from_result(res: SequenceResult) -> list[dict]:
    """Merge consecutive slices with the same unit into runs (top, base, unit, mean posterior)."""
    runs: list[dict] = []
    for t, (sl, u) in enumerate(zip(res.slices, res.path)):
        p = res.prob(t, u)
        if runs and runs[-1]["unit"] == u and abs(runs[-1]["base"] - sl.top) < 1e-6:
            r = runs[-1]
            w0 = r["base"] - r["top"]
            r["p"] = (r["p"] * w0 + p * sl.thickness) / max(w0 + sl.thickness, 1e-9)
            r["base"] = sl.base
            r["synthetic"] = r["synthetic"] or sl.synthetic
        else:
            runs.append({"unit": u, "top": sl.top, "base": sl.base, "p": p, "synthetic": sl.synthetic})
    return runs


def with_unit(iv: Interval, **changes) -> Interval:
    return replace(iv, **changes)
