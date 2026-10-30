"""Geological domain clustering of exploratory holes (explainable k-means).

Each hole is described by interpretable features (cover thicknesses by domain, rockhead elevation,
presence of each bedrock unit, mean SPT N in the top 10 m). Features are standardised, k is chosen
by the silhouette score, and each cluster is explained by the features that differ most from the
site mean (in original units). Clusters are compared with the rule-based domains so disagreements
are visible to the reviewer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..geology.domains import location_domain


@dataclass
class ClusterResult:
    location_ids: list[str]
    labels: np.ndarray
    k: int
    silhouette: float
    feature_names: list[str]
    centroids: np.ndarray  # original units
    explanations: dict[int, list[str]] = field(default_factory=dict)
    rule_domains: list[str] = field(default_factory=list)
    agreement: float = 0.0


def _kmeans(X: np.ndarray, k: int, seed: int, iters: int = 100) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    C = X[[rng.integers(len(X))]]
    while len(C) < k:  # k-means++ initialisation
        d2 = np.min(((X[:, None, :] - C[None]) ** 2).sum(-1), axis=1)
        p = d2 / d2.sum() if d2.sum() > 0 else np.full(len(X), 1 / len(X))
        C = np.vstack([C, X[rng.choice(len(X), p=p)]])
    for _ in range(iters):
        lab = np.argmin(((X[:, None, :] - C[None]) ** 2).sum(-1), axis=1)
        newC = np.array([X[lab == j].mean(axis=0) if (lab == j).any() else C[j] for j in range(k)])
        if np.allclose(newC, C):
            break
        C = newC
    return lab, C


def _silhouette(X: np.ndarray, lab: np.ndarray) -> float:
    D = np.sqrt(((X[:, None, :] - X[None]) ** 2).sum(-1))
    s = []
    for i in range(len(X)):
        same = lab == lab[i]
        if same.sum() <= 1:
            s.append(0.0)
            continue
        a = D[i, same].sum() / (same.sum() - 1)
        b = min(D[i, lab == j].mean() for j in np.unique(lab) if j != lab[i])
        s.append((b - a) / max(a, b) if max(a, b) > 0 else 0.0)
    return float(np.mean(s))


def hole_features(project, interpretation, column) -> tuple[list[str], list[str], np.ndarray, list[str]]:
    kb = column.kb
    bed = [u for s in column.bedrock_sequences() for u in s.codes]
    doms = ["artificial", "alluvial", "glacial", "slope", "aeolian"]
    names = [f"thk_{d}_m" for d in doms] + ["rockhead_mOD"] + [f"has_{u}" for u in bed] + ["spt_top10_mean"]
    ids, rows, rule = [], [], []
    spt = {}
    for r in project.spt:
        if r.depth <= 10 and r.n is not None:
            spt.setdefault(r.location_id, []).append(r.n)
    for lid, li in interpretation.locations.items():
        loc = project.locations[lid]
        dom, thk = location_domain(li.runs, column, loc.ground_level)
        rock = next((loc.ground_level - r.top for r in li.runs if kb[r.unit].unit_class == "bedrock"), np.nan)
        row = (
            [thk.get(d, 0.0) for d in doms]
            + [rock]
            + [float(any(r.unit == u for r in li.runs)) for u in bed]
            + [float(np.mean(spt[lid])) if lid in spt else np.nan]
        )
        ids.append(lid)
        rows.append(row)
        rule.append(dom)
    X = np.array(rows, float)
    for j in range(X.shape[1]):  # impute missing with column median
        col = X[:, j]
        med = np.nanmedian(col) if np.isfinite(col).any() else 0.0
        col[~np.isfinite(col)] = med
    return ids, names, X, rule


def cluster_domains(project, interpretation, column, k_range=(2, 6), seed: int = 0) -> ClusterResult:
    ids, names, X, rule = hole_features(project, interpretation, column)
    if len(ids) < 4:
        raise ValueError("need at least four interpreted holes for clustering")
    mu, sd = X.mean(axis=0), X.std(axis=0) + 1e-9
    Z = (X - mu) / sd
    best = None
    for k in range(k_range[0], min(k_range[1], len(ids) - 1) + 1):
        lab, C = _kmeans(Z, k, seed)
        s = _silhouette(Z, lab)
        if best is None or s > best[2]:
            best = (lab, C, s, k)
    lab, C, s, k = best
    cent = C * sd + mu
    expl = {}
    for j in range(k):
        dz = C[j]
        top = np.argsort(-np.abs(dz))[:3]
        expl[j] = [
            f"{names[t]} {'high' if dz[t] > 0 else 'low'} ({cent[j, t]:.2f} vs site mean {mu[t]:.2f})" for t in top
        ]
    # agreement with rule-based domains: fraction of holes whose cluster's majority rule domain matches
    agree = 0
    for j in range(k):
        members = [rule[i] for i in range(len(ids)) if lab[i] == j]
        if members:
            maj = max(set(members), key=members.count)
            agree += sum(1 for m in members if m == maj)
    return ClusterResult(ids, lab, k, s, names, cent, expl, rule, agree / len(ids))
