"""Semivariogram estimation and fitting (NumPy only).

Fitting is weighted least squares (weights N(h) / gamma(h)^2, after Cressie 1985) over a grid of
ranges, solving the nugget and partial sill in closed form with non-negativity for each range.
When data are too sparse for a defensible experimental variogram the model is *assumed* from the
data extent and variance, and is flagged as such (``fitted=False``) in the run record.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

MODELS = ("spherical", "exponential", "gaussian", "linear")


def _rho(model: str, h: np.ndarray, a: float) -> np.ndarray:
    """Correlation function (1 at h=0, -> 0 at large h). ``a`` is the practical range."""
    h = np.asarray(h, dtype=float)
    if model == "spherical":
        r = np.clip(h / a, 0.0, 1.0)
        return 1.0 - 1.5 * r + 0.5 * r**3
    if model == "exponential":
        return np.exp(-3.0 * h / a)
    if model == "gaussian":
        return np.exp(-3.0 * (h / a) ** 2)
    raise ValueError(model)


@dataclass
class Variogram:
    model: str = "spherical"
    nugget: float = 0.0
    sill: float = 1.0  # partial sill (c1); total sill = nugget + sill
    range: float = 100.0  # practical range (or slope scale for linear)
    slope: float = 0.0  # linear model only
    fitted: bool = False
    n_pairs: int = 0
    note: str = ""

    def gamma(self, h) -> np.ndarray:
        h = np.asarray(h, dtype=float)
        if self.model == "linear":
            return np.where(h > 0, self.nugget + self.slope * h, 0.0)
        return np.where(h > 0, self.nugget + self.sill * (1.0 - _rho(self.model, h, self.range)), 0.0)

    @property
    def total_sill(self) -> float:
        if self.model == "linear":
            return self.nugget + self.slope * self.range
        return self.nugget + self.sill

    def cov(self, h) -> np.ndarray:
        """Covariance C(h) = total_sill - gamma(h) (for the linear model a pseudo-sill at ``range``)."""
        h = np.asarray(h, dtype=float)
        if self.model == "linear":
            return np.maximum(self.total_sill - self.gamma(h), 0.0)
        return np.where(h > 0, self.sill * _rho(self.model, h, self.range), self.nugget + self.sill)

    def to_dict(self) -> dict:
        return asdict(self)


def pairwise_distances(xy: np.ndarray) -> np.ndarray:
    d = xy[:, None, :] - xy[None, :, :]
    return np.sqrt((d**2).sum(axis=2))


def experimental(xy: np.ndarray, z: np.ndarray, n_lags: int = 12, max_lag: float | None = None):
    xy = np.asarray(xy, float)
    z = np.asarray(z, float)
    n = len(z)
    if n < 2:
        return np.zeros(0), np.zeros(0), np.zeros(0, dtype=int)
    if n > 1500:  # subsample for speed; the variogram is a summary statistic
        idx = np.random.default_rng(0).choice(n, 1500, replace=False)
        xy, z, n = xy[idx], z[idx], 1500
    D = pairwise_distances(xy)
    iu = np.triu_indices(n, 1)
    h = D[iu]
    g = 0.5 * (z[iu[0]] - z[iu[1]]) ** 2
    max_lag = max_lag or 0.5 * float(h.max())
    edges = np.linspace(0.0, max_lag, n_lags + 1)
    lags, gam, cnt = [], [], []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (h > a) & (h <= b)
        if m.sum() >= 3:
            lags.append(h[m].mean())
            gam.append(g[m].mean())
            cnt.append(int(m.sum()))
    return np.array(lags), np.array(gam), np.array(cnt)


def _nnls2(A: np.ndarray, y: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, float]:
    """Weighted least squares for 2 non-negative parameters."""
    sw = np.sqrt(w)
    Aw, yw = A * sw[:, None], y * sw
    sol, *_ = np.linalg.lstsq(Aw, yw, rcond=None)
    if (sol >= 0).all():
        return sol, float(((Aw @ sol - yw) ** 2).sum())
    best, best_sse = None, np.inf
    for k in range(2):
        a = Aw[:, k]
        c = max(float(a @ yw) / max(float(a @ a), 1e-30), 0.0)
        s = np.zeros(2)
        s[k] = c
        sse = float(((Aw @ s - yw) ** 2).sum())
        if sse < best_sse:
            best, best_sse = s, sse
    return best, best_sse


def default_variogram(
    xy: np.ndarray, z: np.ndarray, meas_sigma: float = 0.15, note: str = "", var_floor: float = 1.0
) -> Variogram:
    """Conservative assumed model for sparse data: spherical, range 60% of the data extent and a
    sill no smaller than ``var_floor`` (so few, similar values do not produce false confidence)."""
    xy = np.asarray(xy, float)
    span = float(np.hypot(*(xy.max(axis=0) - xy.min(axis=0)))) if len(xy) > 1 else 100.0
    var = float(np.var(z)) if len(z) > 1 else 1.0
    var = max(var, var_floor)
    return Variogram(
        "spherical",
        nugget=max(meas_sigma**2, 0.01 * var),
        sill=var,
        range=max(0.6 * span, 10.0),
        fitted=False,
        note=note or "assumed: too few data for a fitted variogram",
    )


def fit_variogram(
    xy,
    z,
    model: str = "auto",
    n_lags: int = 12,
    min_points: int = 8,
    min_pairs: int = 30,
    meas_sigma: float = 0.15,
    detrend: bool = False,
    var_floor: float = 1.0,
) -> Variogram:
    """Fit a variogram. ``auto`` chooses between spherical and exponential models; the Gaussian
    model is available on request only because, fitted to sparse borehole data, it tends to give
    unrealistically smooth surfaces and over-confident kriging variances."""
    xy = np.asarray(xy, float)
    z = np.asarray(z, float)
    if len(z) < min_points:
        return default_variogram(xy, z, meas_sigma, var_floor=var_floor)
    zz = z
    if detrend:
        A = np.column_stack([np.ones(len(z)), xy - xy.mean(axis=0)])
        coef, *_ = np.linalg.lstsq(A, z, rcond=None)
        zz = z - A @ coef
    lags, gam, cnt = experimental(xy, zz, n_lags)
    if len(lags) < 4 or cnt.sum() < min_pairs:
        return default_variogram(
            xy, zz, meas_sigma, "assumed: experimental variogram has too few lag classes", var_floor=var_floor
        )
    candidates = ("spherical", "exponential") if model == "auto" else (model,)
    best: Variogram | None = None
    best_sse = np.inf
    hmax = float(lags.max())
    for m in candidates:
        if m == "linear":
            A = np.column_stack([np.ones_like(lags), lags])
            w = cnt / np.maximum(gam, 1e-9) ** 2
            sol, sse = _nnls2(A, gam, w)
            if sse < best_sse:
                best_sse = sse
                best = Variogram(
                    "linear",
                    nugget=float(sol[0]),
                    slope=float(sol[1]),
                    range=hmax * 2,
                    fitted=True,
                    n_pairs=int(cnt.sum()),
                )
            continue
        for a in np.geomspace(max(lags.min(), 1e-3), 3.0 * hmax, 40):
            A = np.column_stack([np.ones_like(lags), 1.0 - _rho(m, lags, a)])
            w = cnt / np.maximum(gam, 1e-9) ** 2
            sol, sse = _nnls2(A, gam, w)
            if sse < best_sse and sol[1] > 0:
                best_sse = sse
                best = Variogram(
                    m, nugget=float(sol[0]), sill=float(sol[1]), range=float(a), fitted=True, n_pairs=int(cnt.sum())
                )
    if best is None:
        return default_variogram(xy, zz, meas_sigma, "assumed: fit failed", var_floor=var_floor)
    best.nugget = max(best.nugget, 0.0)
    best.note = "fitted (WLS, Cressie weights)" + (" on detrended data" if detrend else "")
    return best
