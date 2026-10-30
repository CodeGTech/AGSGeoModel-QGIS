"""Marching-squares contouring of grids into polylines (NumPy only).

Saddle cells are resolved with the cell-centre average. Segments are linked into polylines by
exact endpoint matching (edge crossings are computed once per edge, so shared endpoints are
bit-identical).
"""

from __future__ import annotations

import math

import numpy as np

from .grid import Grid2D

# edge ids: 0 bottom (j, i)-(j, i+1), 1 right (j, i+1)-(j+1, i+1), 2 top (j+1, i)-(j+1, i+1), 3 left (j, i)-(j+1, i)
_CASES = {
    1: [(3, 0)],
    2: [(0, 1)],
    3: [(3, 1)],
    4: [(1, 2)],
    5: None,
    6: [(0, 2)],
    7: [(3, 2)],
    8: [(2, 3)],
    9: [(0, 2)],
    10: None,
    11: [(1, 2)],
    12: [(1, 3)],
    13: [(0, 1)],
    14: [(0, 3)],
}


def contour_levels(values: np.ndarray, interval: float | None = None, n: int = 10) -> np.ndarray:
    v = np.asarray(values, float)
    lo, hi = np.nanmin(v), np.nanmax(v)
    if not np.isfinite(lo) or hi <= lo:
        return np.zeros(0)
    if interval is None:
        raw = (hi - lo) / n
        mag = 10 ** math.floor(math.log10(raw))
        interval = next(m * mag for m in (1, 2, 2.5, 5, 10) if raw <= m * mag)
    start = math.ceil(lo / interval) * interval
    return np.arange(start, hi + 1e-9, interval)


def contour_lines(grid: Grid2D, values: np.ndarray, levels) -> list[tuple[float, np.ndarray]]:
    v = np.asarray(values, float)
    ny, nx = v.shape
    out: list[tuple[float, np.ndarray]] = []
    X0, Y0, dx, dy = grid.x0, grid.y0, grid.dx, grid.dy
    for lev0 in np.atleast_1d(levels):
        # nudge the level off exact node values so contours never pass through a node (avoids
        # degenerate zero-length segments and branching at nodes)
        lev = float(lev0) + 1e-9 * max(1.0, abs(float(lev0)))
        above = v > lev
        valid = np.isfinite(v)
        segs: list[tuple[tuple, tuple]] = []
        cache: dict[tuple, tuple[float, float]] = {}

        def edge_point(j, i, e, cache=cache, lev=lev):
            key = {0: ("h", j, i), 1: ("v", j, i + 1), 2: ("h", j + 1, i), 3: ("v", j, i)}[e]
            if key in cache:
                return cache[key]
            if key[0] == "h":
                jj, ii = key[1], key[2]
                a, b = v[jj, ii], v[jj, ii + 1]
                t = (lev - a) / (b - a) if b != a else 0.5
                p = (X0 + (ii + t) * dx, Y0 + jj * dy)
            else:
                jj, ii = key[1], key[2]
                a, b = v[jj, ii], v[jj + 1, ii]
                t = (lev - a) / (b - a) if b != a else 0.5
                p = (X0 + ii * dx, Y0 + (jj + t) * dy)
            cache[key] = p
            return p

        ok = valid[:-1, :-1] & valid[:-1, 1:] & valid[1:, :-1] & valid[1:, 1:]
        idx = (
            above[:-1, :-1].astype(int)
            | (above[:-1, 1:].astype(int) << 1)
            | (above[1:, 1:].astype(int) << 2)
            | (above[1:, :-1].astype(int) << 3)
        )
        js, is_ = np.nonzero(ok & (idx > 0) & (idx < 15))
        for j, i in zip(js.tolist(), is_.tolist()):
            c = int(idx[j, i])
            pairs = _CASES.get(c)
            if pairs is None:  # saddle
                centre = 0.25 * (v[j, i] + v[j, i + 1] + v[j + 1, i] + v[j + 1, i + 1])
                if c == 5:
                    pairs = [(3, 2), (0, 1)] if centre > lev else [(3, 0), (1, 2)]
                else:
                    pairs = [(0, 3), (1, 2)] if centre > lev else [(0, 1), (2, 3)]
            for e1, e2 in pairs:
                a, b = edge_point(j, i, e1), edge_point(j, i, e2)
                if a != b:
                    segs.append((a, b))
        out.extend((float(lev0), line) for line in _link(segs))
    return out


def _link(segs: list[tuple[tuple, tuple]]) -> list[np.ndarray]:
    adj: dict[tuple, list[int]] = {}
    for k, (a, b) in enumerate(segs):
        adj.setdefault(a, []).append(k)
        adj.setdefault(b, []).append(k)
    used = [False] * len(segs)
    lines = []
    for k in range(len(segs)):
        if used[k]:
            continue
        used[k] = True
        a, b = segs[k]
        chain = [a, b]
        for direction in (0, 1):
            while True:
                end = chain[-1] if direction == 0 else chain[0]
                nxt = next((m for m in adj.get(end, []) if not used[m]), None)
                if nxt is None:
                    break
                used[nxt] = True
                p, q = segs[nxt]
                new = q if p == end else p
                if direction == 0:
                    chain.append(new)
                else:
                    chain.insert(0, new)
        lines.append(np.array(chain))
    return lines
