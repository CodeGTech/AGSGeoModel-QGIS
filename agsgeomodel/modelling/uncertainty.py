"""Uncertainty classes (Observed / Interpolated / Inferred / Speculative), confidence and RAG.

Classification of a model node for a given surface:

* **Observed** — within ``observed_radius_m`` of a primary (AGS-logged) observation of that surface;
* **Interpolated** — standard deviation <= ``interpolated_max_sigma_m``, inside the convex hull of
  primary observations;
* **Inferred** — standard deviation <= ``inferred_max_sigma_m``, or controlled mainly by secondary
  data (BGS records, map constraints, absence / inequality data);
* **Speculative** — everything else (extrapolation, sparse or conflicting control).

Confidence = exp(-sigma / confidence_scale_m), mapped to RAG with the configured thresholds.
"""

from __future__ import annotations

import numpy as np

from ..core.config import UncertaintyConfig
from ..core.geometry import convex_hull, points_in_ring
from ..core.grid import Grid2D
from ..core.spatial import PointIndex
from ..models.enums import RAG, ConfidenceClass

CLASS_CODES = {
    ConfidenceClass.OBSERVED: 0,
    ConfidenceClass.INTERPOLATED: 1,
    ConfidenceClass.INFERRED: 2,
    ConfidenceClass.SPECULATIVE: 3,
}
CODE_CLASSES = {v: k for k, v in CLASS_CODES.items()}
RAG_COLOURS = {RAG.GREEN: "#2E7D32", RAG.AMBER: "#F9A825", RAG.RED: "#C62828"}
CLASS_COLOURS = {
    ConfidenceClass.OBSERVED: "#1B5E20",
    ConfidenceClass.INTERPOLATED: "#43A047",
    ConfidenceClass.INFERRED: "#F9A825",
    ConfidenceClass.SPECULATIVE: "#C62828",
}


def confidence_from_std(std: np.ndarray, cfg: UncertaintyConfig) -> np.ndarray:
    s = np.asarray(std, float)
    return np.where(np.isfinite(s), np.exp(-np.maximum(s, 0.0) / cfg.confidence_scale_m), 0.0)


def rag_from_confidence(conf: np.ndarray, cfg: UncertaintyConfig) -> np.ndarray:
    """0 = green, 1 = amber, 2 = red."""
    c = np.asarray(conf, float)
    return np.where(c >= cfg.green_min_confidence, 0, np.where(c >= cfg.amber_min_confidence, 1, 2))


def classify(
    grid: Grid2D, std: np.ndarray, primary_xy: np.ndarray, secondary_xy: np.ndarray | None, cfg: UncertaintyConfig
) -> np.ndarray:
    nodes = grid.nodes()
    s = np.asarray(std, float).ravel()
    out = np.full(len(nodes), CLASS_CODES[ConfidenceClass.SPECULATIVE], dtype=int)
    p = np.asarray(primary_xy, float).reshape(-1, 2)
    sec = np.zeros((0, 2)) if secondary_xy is None else np.asarray(secondary_xy, float).reshape(-1, 2)
    d_p = _nearest(nodes, p)
    d_s = _nearest(nodes, sec)
    inside = np.zeros(len(nodes), dtype=bool)
    if len(p) >= 3:
        hull = convex_hull(p)
        if len(hull) >= 3:
            inside = points_in_ring(nodes, np.vstack([hull, hull[:1]]))
    finite = np.isfinite(s)
    inferred = finite & (s <= cfg.inferred_max_sigma_m)
    out[inferred] = CLASS_CODES[ConfidenceClass.INFERRED]
    interp = finite & (s <= cfg.interpolated_max_sigma_m) & inside & (d_p <= np.maximum(d_s, 0) + 1e-9)
    interp |= finite & (s <= cfg.interpolated_max_sigma_m) & inside & ~np.isfinite(d_s)
    out[interp] = CLASS_CODES[ConfidenceClass.INTERPOLATED]
    # every primary observation marks at least the grid nodes of the cell it falls in (half diagonal)
    obs = d_p <= max(cfg.observed_radius_m, 0.5 * float(np.hypot(grid.dx, grid.dy)) + 1e-9)
    out[obs] = CLASS_CODES[ConfidenceClass.OBSERVED]
    return out.reshape(grid.shape)


def _nearest(nodes: np.ndarray, pts: np.ndarray) -> np.ndarray:
    if len(pts) == 0:
        return np.full(len(nodes), np.inf)
    return PointIndex(pts).nearest_distance(nodes)


def class_name(code: int) -> str:
    return CODE_CLASSES[int(code)].value
