"""Level-3 probabilistic modelling: Gaussian random fields and conditional simulation.

Unconditional stationary fields are generated on the model grid by circulant embedding (exact
for the embedded covariance; Dietrich & Newsam 1997). Conditioning uses the classical
"conditioning by kriging" identity:

    Z_c(x) = Z*_k(x) + [ Z_u(x) - Z*_u(x) ]

where ``Z*_k`` is the kriged field from the data and ``Z*_u`` the same kriging predictor applied to
the unconditional field sampled at the data locations. The kriging weights from the deterministic
model are re-used, so each realisation costs one FFT and one sparse weighted sum.
"""

from __future__ import annotations

import numpy as np

from ..core.grid import Grid2D
from .kriging import KrigingWeights
from .variogram import Variogram


def grf(grid: Grid2D, vg: Variogram, rng: np.random.Generator, n: int = 1) -> np.ndarray:
    """``n`` unconditional zero-mean realisations with covariance ``vg.cov`` -> (n, ny, nx)."""
    ny, nx = grid.ny, grid.nx
    # embedding size: at least twice the grid plus the range, rounded up to FFT-friendly sizes
    ext_x = int(np.ceil(vg.range / grid.dx)) if vg.model != "linear" else nx
    ext_y = int(np.ceil(vg.range / grid.dy)) if vg.model != "linear" else ny
    mx = _good_size(max(2 * nx, nx + ext_x))
    my = _good_size(max(2 * ny, ny + ext_y))
    ix = np.minimum(np.arange(mx), mx - np.arange(mx)) * grid.dx
    iy = np.minimum(np.arange(my), my - np.arange(my)) * grid.dy
    H = np.hypot(iy[:, None], ix[None, :])
    c = vg.cov(H)
    lam = np.real(np.fft.fft2(c))
    neg = lam < 0
    if neg.any():
        lam = np.where(neg, 0.0, lam)  # tiny negative eigenvalues from truncation are clipped
    sq = np.sqrt(lam)
    out = np.empty((n, ny, nx))
    for k in range(n):
        eps = rng.standard_normal((my, mx))
        field = np.real(np.fft.ifft2(sq * np.fft.fft2(eps)))
        out[k] = field[:ny, :nx]
    return out


def _good_size(n: int) -> int:
    m = 1
    while m < n:
        m *= 2
    # 2^a * 3^b sizes are often smaller than the next power of two
    best = m
    for a in range(20):
        for b in range(12):
            v = (2**a) * (3**b)
            if v >= n and v < best:
                best = v
    return best


def conditional_realisations(
    grid: Grid2D,
    det: np.ndarray,
    vg: Variogram,
    weights: KrigingWeights,
    data_xy: np.ndarray,
    rng: np.random.Generator,
    n: int,
) -> np.ndarray:
    """Conditional realisations (n, ny, nx) around a kriged field ``det`` using cached weights."""
    U = grf(grid, vg, rng, n)
    out = np.empty_like(U)
    for k in range(n):
        u = U[k]
        u_data = grid.sample(u, data_xy)
        u_data = np.where(np.isfinite(u_data), u_data, 0.0)
        u_star = weights.apply(u_data, with_correction=False).reshape(grid.shape)
        out[k] = det + (u - u_star)
    return out
