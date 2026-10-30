"""Ordinary / universal kriging with local neighbourhoods, measurement error and fault barriers.

* Local neighbourhood of ``n_neighbours`` visible data per node (fault-aware).
* Measurement error variance ``sigma_i^2`` is added to the data covariance diagonal only, so
  uncertain data (digitised BGS logs, map-derived contacts) are smoothed while primary contacts
  are honoured to within their error.
* ``drift='linear'`` gives universal kriging with a planar trend (useful where a regional dip
  is clear and extrapolation beyond the data is required); the default is ordinary kriging.
* Systems are solved in batches with ``numpy.linalg.solve``; nodes with fewer visible data than
  the neighbourhood size get padded "dummy" points whose weights are forced to zero.
* The predictor can return its weights so conditional simulation can re-use them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..core.errors import ModelError
from ..core.spatial import PointIndex
from .barriers import FaultBarrier
from .variogram import Variogram, fit_variogram


@dataclass
class KrigingWeights:
    idx: np.ndarray  # (N, k) data indices (-1 = unused)
    w: np.ndarray  # (N, k) weights
    corr: np.ndarray  # (N, k) throw corrections applied to data values

    def apply(self, values: np.ndarray, with_correction: bool = True) -> np.ndarray:
        v = values[np.clip(self.idx, 0, None)]
        if with_correction:
            v = v + self.corr
        v = np.where(self.idx >= 0, v, 0.0)
        return (self.w * v).sum(axis=1)


class OrdinaryKriging:
    def __init__(
        self,
        variogram: Variogram | str = "auto",
        n_neighbours: int = 16,
        drift: str = "constant",
        barrier: FaultBarrier | None = None,
        use_throws: bool = True,
        chunk: int = 2048,
        var_floor: float = 1.0,
    ):
        self.variogram_spec = variogram
        self.var_floor = var_floor
        self.k = int(n_neighbours)
        self.drift = drift
        self.barrier = barrier or FaultBarrier()
        self.use_throws = use_throws
        self.chunk = chunk
        self.vg: Variogram | None = None

    # ------------------------------------------------------------------ fit
    def fit(self, xy, z, sigma=None) -> OrdinaryKriging:
        self.xy = np.asarray(xy, float).reshape(-1, 2)
        self.z = np.asarray(z, float).ravel()
        if len(self.z) == 0:
            raise ModelError("kriging needs at least one data point")
        self.sigma = (
            np.zeros_like(self.z) if sigma is None else np.broadcast_to(np.asarray(sigma, float), self.z.shape).copy()
        )
        if isinstance(self.variogram_spec, Variogram):
            self.vg = self.variogram_spec
        else:
            ms = float(np.median(self.sigma)) if len(self.sigma) else 0.15
            self.vg = fit_variogram(
                self.xy,
                self.z,
                "auto" if self.variogram_spec == "auto" else self.variogram_spec,
                meas_sigma=ms,
                detrend=self.drift == "linear",
                var_floor=self.var_floor,
            )
        self._centre = self.xy.mean(axis=0)
        self._scale = max(float(np.ptp(self.xy, axis=0).max()), 1.0)
        self._tree = None
        return self

    # ------------------------------------------------------------------ predict
    def predict(self, pts, return_std: bool = True, return_weights: bool = False):
        P = np.asarray(pts, float).reshape(-1, 2)
        N = len(P)
        n = len(self.z)
        k = min(self.k, n)
        out = np.full(N, np.nan)
        std = np.full(N, np.nan)
        W_idx = np.full((N, k), -1, dtype=int)
        W_w = np.zeros((N, k))
        W_c = np.zeros((N, k))
        use_drift = self.drift == "linear" and n >= 6
        p = 3 if use_drift else 1
        vg = self.vg
        c0 = vg.cov(0.0)
        for s in range(0, N, self.chunk):
            e = min(N, s + self.chunk)
            Q = P[s:e]
            m = len(Q)
            kq = k if self.barrier.empty else min(n, max(4 * k, 64))
            cand, cd = self._candidates(Q, kq)
            corr_c = np.zeros_like(cd)
            if not self.barrier.empty:
                vis, corr_c = self.barrier.pair_visibility(Q, self.xy, cand, self.use_throws)
                cd = np.where(vis, cd, np.inf)
            order = np.argsort(cd, axis=1)[:, :k]
            nn = np.take_along_axis(cand, order, axis=1)
            dn = np.take_along_axis(cd, order, axis=1)
            valid = np.isfinite(dn)
            idx = np.where(valid, nn, -1)
            cn = np.take_along_axis(corr_c, order, axis=1)
            Xn = self.xy[np.clip(nn, 0, None)]  # (m, k, 2)
            zn = self.z[np.clip(nn, 0, None)] + cn
            sn = self.sigma[np.clip(nn, 0, None)]
            dd = np.hypot(Xn[:, :, None, 0] - Xn[:, None, :, 0], Xn[:, :, None, 1] - Xn[:, None, :, 1])
            K = np.zeros((m, k + p, k + p))
            K[:, :k, :k] = vg.cov(dd)
            K[:, np.arange(k), np.arange(k)] += sn**2
            # dummy (invisible) neighbours: decouple them so their weight is zero
            vm = valid[:, :, None] & valid[:, None, :]
            K[:, :k, :k] = np.where(vm, K[:, :k, :k], 0.0)
            diag = K[:, np.arange(k), np.arange(k)]
            K[:, np.arange(k), np.arange(k)] = np.where(valid, diag, 1.0)
            K[:, :k, k] = valid
            K[:, k, :k] = valid
            rhs = np.zeros((m, k + p))
            rhs[:, :k] = np.where(valid, vg.cov(np.where(valid, dn, 0.0)), 0.0)
            rhs[:, k] = 1.0
            if use_drift:
                xs = (Xn - self._centre) / self._scale
                qs = (Q - self._centre) / self._scale
                K[:, :k, k + 1] = np.where(valid, xs[:, :, 0], 0.0)
                K[:, :k, k + 2] = np.where(valid, xs[:, :, 1], 0.0)
                K[:, k + 1, :k] = K[:, :k, k + 1]
                K[:, k + 2, :k] = K[:, :k, k + 2]
                rhs[:, k + 1] = qs[:, 0]
                rhs[:, k + 2] = qs[:, 1]
                # nodes with too few visible neighbours for a planar drift fall back to OK
                few = valid.sum(axis=1) < 6
                if few.any():
                    K[few, k + 1 :, :] = 0.0
                    K[few, :, k + 1 :] = 0.0
                    K[few, k + 1, k + 1] = 1.0
                    K[few, k + 2, k + 2] = 1.0
                    rhs[few, k + 1 :] = 0.0
            none = ~valid.any(axis=1)
            if none.any():
                K[none] = np.eye(k + p)
                rhs[none] = 0.0
            K[:, np.arange(k + p), np.arange(k + p)] += 1e-10 * max(vg.total_sill, 1e-6)
            try:
                sol = np.linalg.solve(K, rhs[:, :, None])[:, :, 0]
            except np.linalg.LinAlgError:  # pragma: no cover - extremely degenerate geometry
                sol = np.stack([np.linalg.lstsq(Ki, ri, rcond=None)[0] for Ki, ri in zip(K, rhs)])
            w = np.where(valid, sol[:, :k], 0.0)
            pred = (w * np.where(valid, zn, 0.0)).sum(axis=1)
            var = c0 - (sol * rhs).sum(axis=1)
            pred[none] = np.nan
            var[none] = np.nan
            out[s:e] = pred
            std[s:e] = np.sqrt(np.clip(var, 0.0, None))
            W_idx[s:e] = idx
            W_w[s:e] = w
            W_c[s:e] = np.where(valid, cn, 0.0)
        res = [out]
        if return_std:
            res.append(std)
        if return_weights:
            res.append(KrigingWeights(W_idx, W_w, W_c))
        return tuple(res) if len(res) > 1 else res[0]

    def _candidates(self, Q: np.ndarray, kq: int) -> tuple[np.ndarray, np.ndarray]:
        """``kq`` nearest data (indices, distances) for each query point; KD-tree when SciPy is present."""
        n = len(self.z)
        kq = min(kq, n)
        if self._tree is None and n > 256:
            self._tree = PointIndex(self.xy)
        if self._tree:
            d, i = self._tree.query(Q, k=kq)
            return i, d
        D = np.hypot(Q[:, None, 0] - self.xy[None, :, 0], Q[:, None, 1] - self.xy[None, :, 1])
        if kq < n:
            i = np.argpartition(D, kq - 1, axis=1)[:, :kq]
        else:
            i = np.tile(np.arange(n), (len(Q), 1))
        return i, np.take_along_axis(D, i, axis=1)

    def loo_residuals(self) -> np.ndarray:
        """Leave-one-out cross-validation residuals (observed - predicted)."""
        n = len(self.z)
        res = np.full(n, np.nan)
        if n < 3:
            return res
        for i in range(n):
            mask = np.ones(n, dtype=bool)
            mask[i] = False
            sub = OrdinaryKriging(self.vg, self.k, self.drift, self.barrier, self.use_throws, var_floor=self.var_floor)
            sub.fit(self.xy[mask], self.z[mask], self.sigma[mask])
            pred = sub.predict(self.xy[i : i + 1], return_std=False)
            res[i] = self.z[i] - float(np.atleast_1d(pred)[0])
        return res
