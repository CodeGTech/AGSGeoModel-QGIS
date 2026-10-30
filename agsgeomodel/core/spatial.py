"""Spatial index for 2D points: SciPy cKDTree when available, otherwise an exact bucket grid.

The bucket grid answers k-nearest-neighbour queries exactly by expanding square rings of cells
around each query's cell until the k-th distance is no larger than the ring radius (any point
outside the examined block is at least ``r * cell`` away). Queries are processed grouped by cell,
so the Python loop runs over occupied cells, not over points — adequate for national-scale
borehole indexes (10^5–10^6 points) without SciPy.
"""

from __future__ import annotations

import math

import numpy as np

from .optional import optional_import


class PointIndex:
    def __init__(self, xy, force_bucket: bool = False):
        self.xy = np.asarray(xy, float).reshape(-1, 2)
        self.n = len(self.xy)
        self._tree = None
        sp = None if force_bucket else optional_import("scipy.spatial")
        if sp is not None and self.n:
            self._tree = sp.cKDTree(self.xy)
            return
        if self.n == 0:
            return
        lo = self.xy.min(axis=0)
        hi = self.xy.max(axis=0)
        area = max(float(np.prod(np.maximum(hi - lo, 1e-6))), 1e-6)
        self.h = max(math.sqrt(area / max(self.n, 1)) * 1.5, 1e-6)
        self.lo = lo
        self.nbx = int(math.floor((hi[0] - lo[0]) / self.h)) + 1
        self.nby = int(math.floor((hi[1] - lo[1]) / self.h)) + 1
        ij = self._cell(self.xy)
        key = ij[:, 1] * self.nbx + ij[:, 0]
        order = np.argsort(key, kind="stable")
        self._order = order
        skey = key[order]
        self._starts = np.searchsorted(skey, np.arange(self.nbx * self.nby), side="left")
        self._ends = np.searchsorted(skey, np.arange(self.nbx * self.nby), side="right")

    @property
    def backend(self) -> str:
        return "scipy.cKDTree" if self._tree is not None else "bucket grid"

    def _cell(self, p: np.ndarray) -> np.ndarray:
        ij = np.floor((p - self.lo) / self.h).astype(int)
        ij[:, 0] = np.clip(ij[:, 0], 0, self.nbx - 1)
        ij[:, 1] = np.clip(ij[:, 1], 0, self.nby - 1)
        return ij

    def _block(self, i: int, j: int, r: int) -> np.ndarray:
        i0, i1 = max(i - r, 0), min(i + r, self.nbx - 1)
        j0, j1 = max(j - r, 0), min(j + r, self.nby - 1)
        parts = []
        for jj in range(j0, j1 + 1):
            row = jj * self.nbx
            a, b = self._starts[row + i0], self._ends[row + i1]
            if b > a:
                parts.append(self._order[a:b])
        return np.concatenate(parts) if parts else np.zeros(0, dtype=int)

    def query(self, q, k: int = 1) -> tuple[np.ndarray, np.ndarray]:
        """``(distances (m, k), indices (m, k))`` of the k nearest points (k clipped to n)."""
        Q = np.asarray(q, float).reshape(-1, 2)
        k = max(1, min(int(k), self.n))
        if self.n == 0:
            return np.full((len(Q), k), np.inf), np.full((len(Q), k), -1, dtype=int)
        if self._tree is not None:
            d, i = self._tree.query(Q, k=k)
            return np.asarray(d).reshape(len(Q), k), np.asarray(i).reshape(len(Q), k)
        D = np.full((len(Q), k), np.inf)
        I = np.full((len(Q), k), -1, dtype=int)
        qij = self._cell(Q)
        qkey = qij[:, 1] * self.nbx + qij[:, 0]
        order = np.argsort(qkey, kind="stable")
        sk = qkey[order]
        bounds = np.flatnonzero(np.diff(sk)) + 1
        for grp in np.split(order, bounds):
            i, j = int(qij[grp[0], 0]), int(qij[grp[0], 1])
            P = Q[grp]
            r = 1
            maxr = max(self.nbx, self.nby)
            while True:
                cand = self._block(i, j, r)
                if len(cand) >= k or r >= maxr:
                    d = np.hypot(P[:, None, 0] - self.xy[None, cand, 0], P[:, None, 1] - self.xy[None, cand, 1])
                    kk = min(k, len(cand))
                    part = (
                        np.argpartition(d, kk - 1, axis=1)[:, :kk]
                        if kk < len(cand)
                        else np.tile(np.arange(len(cand)), (len(P), 1))
                    )
                    dk = np.take_along_axis(d, part, axis=1)
                    srt = np.argsort(dk, axis=1)
                    dk = np.take_along_axis(dk, srt, axis=1)
                    ik = cand[np.take_along_axis(part, srt, axis=1)]
                    # exact when the k-th distance lies inside the examined ring
                    if r >= maxr or (len(cand) >= k and np.all(dk[:, -1] <= r * self.h)):
                        D[grp, :kk] = dk
                        I[grp, :kk] = ik
                        break
                r += 1
        return D, I

    def nearest_distance(self, q) -> np.ndarray:
        d, _ = self.query(q, 1)
        return d[:, 0]

    def within(self, q, radius: float) -> list[np.ndarray]:
        """Indices of points within ``radius`` of each query point."""
        Q = np.asarray(q, float).reshape(-1, 2)
        if self._tree is not None:
            return [np.asarray(v, dtype=int) for v in self._tree.query_ball_point(Q, radius)]
        out = []
        r_cells = int(math.ceil(radius / self.h)) if self.n else 0
        for p in Q:
            if self.n == 0:
                out.append(np.zeros(0, dtype=int))
                continue
            i, j = self._cell(p[None])[0]
            cand = self._block(int(i), int(j), r_cells)
            d = np.hypot(self.xy[cand, 0] - p[0], self.xy[cand, 1] - p[1])
            out.append(cand[d <= radius])
        return out
