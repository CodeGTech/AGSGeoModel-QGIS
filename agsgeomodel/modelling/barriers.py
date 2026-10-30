"""Fault barriers: no information passes through a known fault without geological justification.

A data point is *visible* from a prediction node when the straight segment between them does not
cross a fault trace. If a crossed fault has a known throw and downthrown side, the data value is
corrected by the throw and the point stays visible ("throw-corrected" mode); a fault of unknown
throw is an absolute barrier. Faults are vertical in plan (2.5D) in v0.1; dipping faults are
handled by the GemPy backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..core.geometry import crossing_mask


@dataclass
class FaultBarrier:
    segments: np.ndarray = field(default_factory=lambda: np.zeros((0, 2, 2)))
    names: list[str] = field(default_factory=list)
    throws: np.ndarray = field(default_factory=lambda: np.zeros(0))  # NaN = unknown
    down_sign: np.ndarray = field(default_factory=lambda: np.zeros(0))  # +1 left / -1 right downthrown

    @classmethod
    def from_faults(cls, faults) -> FaultBarrier:
        segs, names, throws, signs = [], [], [], []
        for f in faults or []:
            for s in f.segments():
                segs.append(s)
                names.append(f.name)
                throws.append(np.nan if f.throw_m is None else float(f.throw_m))
                signs.append(
                    1.0
                    if (f.downthrown_side or "").lower() == "left"
                    else -1.0
                    if (f.downthrown_side or "").lower() == "right"
                    else 0.0
                )
        if not segs:
            return cls()
        return cls(np.array(segs, float), names, np.array(throws, float), np.array(signs, float))

    @property
    def empty(self) -> bool:
        return len(self.segments) == 0

    def _side(self, pts: np.ndarray) -> np.ndarray:
        """(N, F) +1 if point is left of each segment's direction, -1 if right."""
        a = self.segments[:, 0, :]
        b = self.segments[:, 1, :]
        d = b - a
        cross = d[None, :, 0] * (pts[:, None, 1] - a[None, :, 1]) - d[None, :, 1] * (pts[:, None, 0] - a[None, :, 0])
        return np.where(cross >= 0, 1.0, -1.0)

    def visibility(self, nodes: np.ndarray, data: np.ndarray, use_throws: bool = True) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(visible (N, n) bool, correction (N, n) metres to add to data values)``."""
        N, n = len(nodes), len(data)
        vis = np.ones((N, n), dtype=bool)
        corr = np.zeros((N, n))
        if self.empty or N == 0 or n == 0:
            return vis, corr
        a0 = np.repeat(nodes, n, axis=0)
        a1 = np.tile(data, (N, 1))
        cm = crossing_mask(a0, a1, self.segments).reshape(N, n, -1)  # (N, n, F)
        if not cm.any():
            return vis, corr
        known = np.isfinite(self.throws) & (self.down_sign != 0) & use_throws
        unknown_hit = (cm & ~known[None, None, :]).any(axis=2)
        vis &= ~unknown_hit
        if known.any():
            node_side = self._side(nodes)  # (N, F)
            node_down = node_side == self.down_sign[None, :]
            sgn = np.where(node_down, -1.0, 1.0)  # lower upthrown data
            thr = np.where(known, np.nan_to_num(self.throws), 0.0)
            corr = (cm * (sgn * thr[None, :])[:, None, :]).sum(axis=2)
        return vis, corr

    def pair_visibility(
        self, nodes: np.ndarray, data: np.ndarray, idx: np.ndarray, use_throws: bool = True
    ) -> tuple[np.ndarray, np.ndarray]:
        """Visibility / throw correction for candidate pairs ``nodes[i] -> data[idx[i, j]]``."""
        m, kq = idx.shape
        vis = np.ones((m, kq), dtype=bool)
        corr = np.zeros((m, kq))
        if self.empty or m == 0:
            return vis, corr
        a0 = np.repeat(nodes, kq, axis=0)
        a1 = data[idx.ravel()]
        cm = crossing_mask(a0, a1, self.segments).reshape(m, kq, -1)
        if not cm.any():
            return vis, corr
        known = np.isfinite(self.throws) & (self.down_sign != 0) & use_throws
        vis &= ~(cm & ~known[None, None, :]).any(axis=2)
        if known.any():
            node_down = self._side(nodes) == self.down_sign[None, :]
            sgn = np.where(node_down, -1.0, 1.0)
            thr = np.where(known, np.nan_to_num(self.throws), 0.0)
            corr = (cm * (sgn * thr[None, :])[:, None, :]).sum(axis=2)
        return vis, corr

    def same_block(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """Element-wise: True where a[i] -> b[i] crosses no fault (used for pairwise checks)."""
        if self.empty:
            return np.ones(len(a), dtype=bool)
        return ~crossing_mask(a, b, self.segments).any(axis=1)
