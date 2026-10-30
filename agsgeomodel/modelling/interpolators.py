"""Deterministic interpolators (IDW, RBF / thin-plate spline, TIN) and a common factory.

Kriging (:mod:`.kriging`) is the default because it provides a model-based standard deviation.
For the deterministic methods a standard deviation *proxy* is calibrated by leave-one-out
cross-validation: ``std(x) = sigma_meas + s * d_nn(x)`` where ``d_nn`` is the distance to the
nearest datum and ``s`` is fitted so the proxy matches the LOO errors. This keeps the uncertainty
classes comparable whichever method the user chooses.
"""

from __future__ import annotations

import numpy as np

from ..core.config import InterpolationConfig
from ..core.errors import ModelError
from ..core.optional import optional_import
from .barriers import FaultBarrier
from .kriging import OrdinaryKriging


class _DistanceStd:
    def _calibrate(self):
        n = len(self.z)
        self._s = 0.02
        if n < 4:
            return
        errs, dists = [], []
        for i in range(n):
            m = np.ones(n, dtype=bool)
            m[i] = False
            sub = self.__class__(**self._params).fit(
                self.xy[m], self.z[m], None if self.sigma is None else self.sigma[m], calibrate=False
            )
            pred = sub.predict(self.xy[i : i + 1], return_std=False)
            pv = float(np.atleast_1d(pred)[0])
            if np.isfinite(pv):
                errs.append(abs(self.z[i] - pv))
                dists.append(float(np.min(np.hypot(*(self.xy[m] - self.xy[i]).T))))
        if errs:
            e, d = np.array(errs), np.maximum(np.array(dists), 1e-6)
            self._s = float(np.sqrt(np.mean(e**2) / np.mean(d**2)))

    def _std(self, P):
        d = np.min(np.hypot(P[:, None, 0] - self.xy[None, :, 0], P[:, None, 1] - self.xy[None, :, 1]), axis=1)
        base = float(np.median(self.sigma)) if self.sigma is not None and len(self.sigma) else 0.0
        return base + self._s * d


class IDW(_DistanceStd):
    def __init__(self, power: float = 2.0, n_neighbours: int = 12, barrier: FaultBarrier | None = None):
        self._params = {"power": power, "n_neighbours": n_neighbours, "barrier": barrier}
        self.power, self.k, self.barrier = power, n_neighbours, barrier or FaultBarrier()

    def fit(self, xy, z, sigma=None, calibrate: bool = True):
        self.xy = np.asarray(xy, float).reshape(-1, 2)
        self.z = np.asarray(z, float).ravel()
        self.sigma = None if sigma is None else np.broadcast_to(np.asarray(sigma, float), self.z.shape)
        if calibrate:
            self._calibrate()
        return self

    def predict(self, pts, return_std: bool = True):
        P = np.asarray(pts, float).reshape(-1, 2)
        out = np.empty(len(P))
        for s in range(0, len(P), 4096):
            Q = P[s : s + 4096]
            D = np.hypot(Q[:, None, 0] - self.xy[None, :, 0], Q[:, None, 1] - self.xy[None, :, 1])
            corr = np.zeros_like(D)
            if not self.barrier.empty:
                vis, corr = self.barrier.visibility(Q, self.xy)
                D = np.where(vis, D, np.inf)
            k = min(self.k, len(self.z))
            nn = np.argpartition(D, k - 1, axis=1)[:, :k]
            dn = np.take_along_axis(D, nn, axis=1)
            zn = self.z[nn] + np.take_along_axis(corr, nn, axis=1)
            exact = dn < 1e-9
            w = np.where(np.isfinite(dn), 1.0 / np.maximum(dn, 1e-9) ** self.power, 0.0)
            w = np.where(exact.any(axis=1, keepdims=True), exact.astype(float), w)
            tot = w.sum(axis=1)
            out[s : s + 4096] = np.where(tot > 0, (w * zn).sum(axis=1) / np.where(tot > 0, tot, 1.0), np.nan)
        return (out, self._std(P)) if return_std else out


class RBF(_DistanceStd):
    """Polyharmonic / radial basis interpolation with a linear polynomial tail.

    Smoothing per datum is ``sigma_i^2`` (so uncertain data are not honoured exactly). Partial
    faults are not supported by a global RBF; use kriging when faults are present.
    """

    def __init__(self, kernel: str = "thin_plate", epsilon: float | None = None, smoothing: float = 0.0):
        self._params = {"kernel": kernel, "epsilon": epsilon, "smoothing": smoothing}
        self.kernel, self.epsilon, self.smoothing = kernel, epsilon, smoothing

    def _phi(self, r):
        e = self.epsilon or 1.0
        if self.kernel == "thin_plate":
            with np.errstate(divide="ignore", invalid="ignore"):
                return np.where(r > 0, r**2 * np.log(r), 0.0)
        if self.kernel == "linear":
            return -r
        if self.kernel == "cubic":
            return r**3
        if self.kernel == "gaussian":
            return np.exp(-((r / e) ** 2))
        if self.kernel == "multiquadric":
            return -np.sqrt(1 + (r / e) ** 2)
        raise ModelError(f"unknown RBF kernel {self.kernel}")

    def fit(self, xy, z, sigma=None, calibrate: bool = True):
        self.xy = np.asarray(xy, float).reshape(-1, 2)
        self.z = np.asarray(z, float).ravel()
        self.sigma = None if sigma is None else np.broadcast_to(np.asarray(sigma, float), self.z.shape)
        n = len(self.z)
        if n > 3000:
            raise ModelError("RBF limited to 3000 points; use kriging (local neighbourhoods) for larger data sets")
        self._c = self.xy.mean(axis=0)
        self._sc = max(float(np.ptp(self.xy, axis=0).max()), 1.0)
        X = (self.xy - self._c) / self._sc
        if self.epsilon is None:
            self.epsilon = 0.2
        r = np.hypot(X[:, None, 0] - X[None, :, 0], X[:, None, 1] - X[None, :, 1])
        A = self._phi(r)
        lam = np.full(n, self.smoothing)
        if self.sigma is not None:
            lam = lam + (np.asarray(self.sigma) / self._sc) ** 2 * 50.0
        A = A + np.diag(lam)
        Pm = np.column_stack([np.ones(n), X])
        M = np.zeros((n + 3, n + 3))
        M[:n, :n] = A
        M[:n, n:] = Pm
        M[n:, :n] = Pm.T
        rhs = np.concatenate([self.z, np.zeros(3)])
        try:
            sol = np.linalg.solve(M, rhs)
        except np.linalg.LinAlgError:
            sol = np.linalg.lstsq(M, rhs, rcond=None)[0]
        self._w, self._p = sol[:n], sol[n:]
        self._X = X
        if calibrate:
            self._calibrate()
        return self

    def predict(self, pts, return_std: bool = True):
        P = np.asarray(pts, float).reshape(-1, 2)
        Q = (P - self._c) / self._sc
        out = np.empty(len(P))
        for s in range(0, len(P), 4096):
            q = Q[s : s + 4096]
            r = np.hypot(q[:, None, 0] - self._X[None, :, 0], q[:, None, 1] - self._X[None, :, 1])
            out[s : s + 4096] = self._phi(r) @ self._w + self._p[0] + q @ self._p[1:]
        return (out, self._std(P)) if return_std else out


def delaunay(points: np.ndarray) -> np.ndarray:
    """Delaunay triangulation (SciPy if available, else Bowyer–Watson). Returns (T, 3) indices."""
    pts = np.asarray(points, float)
    sp = optional_import("scipy.spatial")
    if sp is not None and len(pts) >= 3:
        try:
            return sp.Delaunay(pts).simplices.astype(int)
        except Exception:
            pass
    return _bowyer_watson(pts)


def _bowyer_watson(pts: np.ndarray) -> np.ndarray:
    n = len(pts)
    if n < 3:
        return np.zeros((0, 3), dtype=int)
    c = pts.mean(axis=0)
    span = float(np.ptp(pts, axis=0).max()) or 1.0
    P = np.vstack([pts, c + span * np.array([[-20, -20], [20, -20], [0, 20]])])
    tris = [(n, n + 1, n + 2)]

    def circum(t):
        a, b, cc = P[list(t)]
        d = 2 * (a[0] * (b[1] - cc[1]) + b[0] * (cc[1] - a[1]) + cc[0] * (a[1] - b[1]))
        if abs(d) < 1e-18:
            return np.array([np.inf, np.inf]), np.inf
        ux = ((a @ a) * (b[1] - cc[1]) + (b @ b) * (cc[1] - a[1]) + (cc @ cc) * (a[1] - b[1])) / d
        uy = ((a @ a) * (cc[0] - b[0]) + (b @ b) * (a[0] - cc[0]) + (cc @ cc) * (b[0] - a[0])) / d
        center = np.array([ux, uy])
        return center, float(((a - center) ** 2).sum())

    cc = {t: circum(t) for t in tris}
    for i in range(n):
        p = P[i]
        bad = [t for t in tris if ((p - cc[t][0]) ** 2).sum() < cc[t][1] - 1e-12]
        edges: dict[tuple[int, int], int] = {}
        for t in bad:
            for e in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
                key = tuple(sorted(e))
                edges[key] = edges.get(key, 0) + 1
        for t in bad:
            tris.remove(t)
            cc.pop(t, None)
        for (a, b), cnt in edges.items():
            if cnt == 1:
                t = (a, b, i)
                tris.append(t)
                cc[t] = circum(t)
    out = [t for t in tris if max(t) < n]
    return np.array(out, dtype=int) if out else np.zeros((0, 3), dtype=int)


class TIN(_DistanceStd):
    """Linear interpolation on a Delaunay triangulation; outside the hull: nearest datum (flagged by std)."""

    def __init__(self):
        self._params = {}

    def fit(self, xy, z, sigma=None, calibrate: bool = True):
        self.xy = np.asarray(xy, float).reshape(-1, 2)
        self.z = np.asarray(z, float).ravel()
        self.sigma = None if sigma is None else np.broadcast_to(np.asarray(sigma, float), self.z.shape)
        self.tri = delaunay(self.xy)
        if calibrate:
            self._calibrate()
        return self

    def predict(self, pts, return_std: bool = True):
        P = np.asarray(pts, float).reshape(-1, 2)
        out = np.full(len(P), np.nan)
        for t in self.tri:
            a, b, c = self.xy[t]
            M = np.array([[b[0] - a[0], c[0] - a[0]], [b[1] - a[1], c[1] - a[1]]])
            det = np.linalg.det(M)
            if abs(det) < 1e-12:
                continue
            inv = np.linalg.inv(M)
            lm = (P - a) @ inv.T
            l1, l2 = lm[:, 0], lm[:, 1]
            l0 = 1 - l1 - l2
            inside = (l0 >= -1e-9) & (l1 >= -1e-9) & (l2 >= -1e-9) & np.isnan(out)
            out[inside] = l0[inside] * self.z[t[0]] + l1[inside] * self.z[t[1]] + l2[inside] * self.z[t[2]]
        miss = np.isnan(out)
        if miss.any():
            d = np.hypot(P[miss, None, 0] - self.xy[None, :, 0], P[miss, None, 1] - self.xy[None, :, 1])
            out[miss] = self.z[np.argmin(d, axis=1)]
        return (out, self._std(P)) if return_std else out


def make_interpolator(
    cfg: InterpolationConfig,
    barrier: FaultBarrier | None = None,
    method: str | None = None,
    n_data: int = 0,
    var_floor: float = 1.0,
):
    m = (method or cfg.method).lower()
    if m == "kriging":
        return OrdinaryKriging(cfg.variogram_model, cfg.n_neighbours, cfg.drift, barrier, var_floor=var_floor)
    if m == "idw":
        return IDW(cfg.idw_power, cfg.n_neighbours, barrier)
    if m == "rbf":
        return RBF(cfg.rbf_kernel)
    if m == "tin":
        return TIN()
    raise ModelError(f"unknown interpolation method {m!r}")
