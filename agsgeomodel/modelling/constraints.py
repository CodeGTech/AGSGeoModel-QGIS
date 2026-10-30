"""Inequality constraints and geological-map constraints.

Inequalities ("this surface is at or above / below z here") come from holes that stop inside a
unit, from units eroded at rockhead and from map polygons. They are honoured with an active-set
approximation of inequality kriging: fit to the equalities, find violated inequalities, add the
most violated as pseudo-data at their bound, refit, repeat (cf. Journel 1986; Freulon & de
Fouquet 1993). Diagnostics report how many constraints were activated and any that remain
violated (e.g. when two constraints contradict each other).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from ..core.geometry import points_inside_with_margin
from ..core.grid import Grid2D


@dataclass
class ConstraintDiagnostics:
    n_equalities: int = 0
    n_inequalities: int = 0
    n_activated: int = 0
    iterations: int = 0
    remaining_violations: int = 0
    max_violation_m: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def fit_constrained(
    factory: Callable[[], object],
    eq_xy,
    eq_v,
    eq_sig,
    ge_xy=None,
    ge_v=None,
    le_xy=None,
    le_v=None,
    max_iter: int = 8,
    tol: float = 0.05,
    ineq_sigma: float = 0.3,
    margin: float = 0.05,
):
    """Return ``(fitted_model or None, diagnostics, (xy, v, sigma) actually used)``."""
    eq_xy = np.asarray(eq_xy, float).reshape(-1, 2)
    eq_v = np.asarray(eq_v, float).ravel()
    eq_sig = np.broadcast_to(np.asarray(eq_sig, float), eq_v.shape).astype(float)
    ge_xy = np.zeros((0, 2)) if ge_xy is None else np.asarray(ge_xy, float).reshape(-1, 2)
    ge_v = np.zeros(0) if ge_v is None else np.asarray(ge_v, float).ravel()
    le_xy = np.zeros((0, 2)) if le_xy is None else np.asarray(le_xy, float).reshape(-1, 2)
    le_v = np.zeros(0) if le_v is None else np.asarray(le_v, float).ravel()
    diag = ConstraintDiagnostics(len(eq_v), len(ge_v) + len(le_v))
    xy, v, sg = eq_xy, eq_v, eq_sig
    if len(v) == 0:
        if len(ge_v) + len(le_v) == 0:
            return None, diag, (xy, v, sg)
        # only bounds known: seed with the bounds themselves (least committal surface) and flag
        xy = np.vstack([ge_xy, le_xy])
        v = np.concatenate([ge_v + margin, le_v - margin])
        sg = np.full(len(v), max(ineq_sigma, 1.0))
        diag.notes.append("no equality data: surface seeded from inequality bounds only (speculative)")
    active_ge = np.zeros(len(ge_v), dtype=bool)
    active_le = np.zeros(len(le_v), dtype=bool)
    model = None
    for it in range(max_iter + 1):
        model = factory()
        model.fit(xy, v, sg)
        diag.iterations = it
        viol_ge = np.zeros(len(ge_v))
        viol_le = np.zeros(len(le_v))
        if len(ge_v):
            p = model.predict(ge_xy, return_std=False)
            viol_ge = np.where(~active_ge, ge_v - np.asarray(p), 0.0)
        if len(le_v):
            p = model.predict(le_xy, return_std=False)
            viol_le = np.where(~active_le, np.asarray(p) - le_v, 0.0)
        bad_ge = viol_ge > tol
        bad_le = viol_le > tol
        n_bad = int(bad_ge.sum() + bad_le.sum())
        if n_bad == 0 or it == max_iter:
            diag.remaining_violations = n_bad
            diag.max_violation_m = float(max(viol_ge.max(initial=0.0), viol_le.max(initial=0.0)))
            break
        # activate the worst violations (at least 5, at most a quarter of the remainder)
        n_take = max(5, n_bad // 4)
        cand = [(viol_ge[i], "ge", i) for i in np.where(bad_ge)[0]] + [
            (viol_le[i], "le", i) for i in np.where(bad_le)[0]
        ]
        cand.sort(key=lambda c: -c[0])
        add_xy, add_v = [], []
        for _, kind, i in cand[:n_take]:
            if kind == "ge":
                active_ge[i] = True
                add_xy.append(ge_xy[i])
                add_v.append(ge_v[i] + margin)
            else:
                active_le[i] = True
                add_xy.append(le_xy[i])
                add_v.append(le_v[i] - margin)
        xy = np.vstack([xy, np.array(add_xy)])
        v = np.concatenate([v, add_v])
        sg = np.concatenate([sg, np.full(len(add_v), ineq_sigma)])
        diag.n_activated += len(add_v)
    return model, diag, (xy, v, sg)


def polygon_sample_points(grid: Grid2D, polygon, margin: float, max_points: int = 150) -> np.ndarray:
    """Grid nodes inside a map polygon and at least ``margin`` from its edge (map positional tolerance),
    thinned to at most ``max_points`` so map constraints never swamp borehole data."""
    nodes = grid.nodes()
    xmin, ymin, xmax, ymax = (
        float(np.min(polygon[0][:, 0])),
        float(np.min(polygon[0][:, 1])),
        float(np.max(polygon[0][:, 0])),
        float(np.max(polygon[0][:, 1])),
    )
    box = (nodes[:, 0] >= xmin) & (nodes[:, 0] <= xmax) & (nodes[:, 1] >= ymin) & (nodes[:, 1] <= ymax)
    cand = nodes[box]
    if len(cand) == 0:
        return np.zeros((0, 2))
    inside = points_inside_with_margin(cand, polygon, margin)
    pts = cand[inside]
    if len(pts) > max_points:
        step = int(np.ceil(np.sqrt(len(pts) / max_points)))
        jj, ii = grid.cell_index(pts)
        keep = (jj % step == 0) & (ii % step == 0)
        pts = pts[keep]
        if len(pts) > max_points:
            pts = pts[np.linspace(0, len(pts) - 1, max_points).astype(int)]
    return pts


def line_sample_points(line: np.ndarray, spacing: float) -> np.ndarray:
    from ..core.geometry import resample_polyline

    _, xy = resample_polyline(line, spacing, include_vertices=False)
    return xy
