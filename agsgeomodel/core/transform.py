"""Coordinate transformations with graceful degradation.

Order of preference:

1. a transformer injected by the host (inside QGIS: ``QgsCoordinateTransform``, which uses PROJ
   and the OSTN15 grid when installed — the only route suitable for engineering accuracy);
2. ``pyproj`` if installed;
3. a built-in OSGB36 / WGS84 conversion (Helmert 7-parameter + Transverse Mercator on Airy 1830)
   accurate to a few metres. It is used **only** for service queries (e.g. BGS bounding boxes)
   and is reported as such; it is never used to position boreholes for modelling.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np

from .optional import optional_import

Transformer = Callable[[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]

_HOST_FACTORY: Callable[[str, str], Transformer] | None = None


def set_host_transformer_factory(factory: Callable[[str, str], Transformer] | None) -> None:
    """Called by the QGIS plugin to route all transforms through QGIS/PROJ."""
    global _HOST_FACTORY
    _HOST_FACTORY = factory


def _norm(crs: str | int) -> str:
    s = str(crs).strip().upper()
    return s if s.startswith("EPSG:") else f"EPSG:{s}"


def get_transformer(src: str | int, dst: str | int, allow_approximate: bool = True) -> tuple[Transformer, str]:
    """Return ``(transform, note)``; ``note`` documents accuracy for provenance."""
    s, d = _norm(src), _norm(dst)
    if s == d:
        return (lambda x, y: (np.asarray(x, float), np.asarray(y, float))), "identity"
    if _HOST_FACTORY is not None:
        return _HOST_FACTORY(s, d), "QGIS/PROJ"
    pyproj = optional_import("pyproj")
    if pyproj is not None:
        tr = pyproj.Transformer.from_crs(s, d, always_xy=True)
        return (lambda x, y: tuple(np.asarray(v, float) for v in tr.transform(x, y))), "pyproj"
    if allow_approximate and {s, d} == {"EPSG:4326", "EPSG:27700"}:
        if s == "EPSG:4326":
            return (
                lambda x, y: wgs84_to_bng(np.asarray(x, float), np.asarray(y, float))
            ), "built-in Helmert (approx. 5 m)"
        return (lambda x, y: bng_to_wgs84(np.asarray(x, float), np.asarray(y, float))), "built-in Helmert (approx. 5 m)"
    from .errors import DependencyMissing

    raise DependencyMissing("pyproj", f"transforming {s} to {d} outside QGIS")


# ---------------------------------------------------------------------------- ellipsoids
_AIRY = (6377563.396, 6356256.909)
_GRS80 = (6378137.000, 6356752.3141)
_F0, _LAT0, _LON0, _E0, _N0 = 0.9996012717, math.radians(49.0), math.radians(-2.0), 400000.0, -100000.0
# ETRS89/WGS84 -> OSGB36 Helmert (Ordnance Survey, "A guide to coordinate systems in Great Britain")
_HELMERT = dict(
    tx=-446.448,
    ty=125.157,
    tz=-542.060,
    s=20.4894e-6,
    rx=math.radians(-0.1502 / 3600),
    ry=math.radians(-0.2470 / 3600),
    rz=math.radians(-0.8421 / 3600),
)


def _geodetic_to_cart(lat, lon, h, ell):
    a, b = ell
    e2 = (a * a - b * b) / (a * a)
    nu = a / np.sqrt(1 - e2 * np.sin(lat) ** 2)
    return (
        (nu + h) * np.cos(lat) * np.cos(lon),
        (nu + h) * np.cos(lat) * np.sin(lon),
        ((1 - e2) * nu + h) * np.sin(lat),
    )


def _cart_to_geodetic(x, y, z, ell):
    a, b = ell
    e2 = (a * a - b * b) / (a * a)
    lon = np.arctan2(y, x)
    p = np.sqrt(x * x + y * y)
    lat = np.arctan2(z, p * (1 - e2))
    for _ in range(10):
        nu = a / np.sqrt(1 - e2 * np.sin(lat) ** 2)
        lat = np.arctan2(z + e2 * nu * np.sin(lat), p)
    return lat, lon


def _helmert(x, y, z, sign=1.0):
    t = _HELMERT
    s = sign * t["s"]
    rx, ry, rz = sign * t["rx"], sign * t["ry"], sign * t["rz"]
    tx, ty, tz = sign * t["tx"], sign * t["ty"], sign * t["tz"]
    return (tx + (1 + s) * x - rz * y + ry * z, ty + rz * x + (1 + s) * y - rx * z, tz - ry * x + rx * y + (1 + s) * z)


def _meridional_arc(phi, a, b):
    n = (a - b) / (a + b)
    n2, n3 = n * n, n * n * n
    dphi, sphi = phi - _LAT0, phi + _LAT0
    return (
        b
        * _F0
        * (
            (1 + n + 1.25 * n2 + 1.25 * n3) * dphi
            - (3 * n + 3 * n2 + 21 / 8 * n3) * np.sin(dphi) * np.cos(sphi)
            + (15 / 8 * n2 + 15 / 8 * n3) * np.sin(2 * dphi) * np.cos(2 * sphi)
            - 35 / 24 * n3 * np.sin(3 * dphi) * np.cos(3 * sphi)
        )
    )


def osgb36_latlon_to_bng(lat_deg, lon_deg):
    """Transverse Mercator projection of OSGB36 geodetic coordinates to National Grid E, N."""
    a, b = _AIRY
    phi = np.radians(np.asarray(lat_deg, float))
    lam = np.radians(np.asarray(lon_deg, float))
    e2 = 1 - (b * b) / (a * a)
    sinp, cosp, tanp = np.sin(phi), np.cos(phi), np.tan(phi)
    nu = a * _F0 / np.sqrt(1 - e2 * sinp**2)
    rho = a * _F0 * (1 - e2) / (1 - e2 * sinp**2) ** 1.5
    eta2 = nu / rho - 1
    M = _meridional_arc(phi, a, b)
    I = M + _N0
    II = nu / 2 * sinp * cosp
    III = nu / 24 * sinp * cosp**3 * (5 - tanp**2 + 9 * eta2)
    IIIA = nu / 720 * sinp * cosp**5 * (61 - 58 * tanp**2 + tanp**4)
    IV = nu * cosp
    V = nu / 6 * cosp**3 * (nu / rho - tanp**2)
    VI = nu / 120 * cosp**5 * (5 - 18 * tanp**2 + tanp**4 + 14 * eta2 - 58 * tanp**2 * eta2)
    dl = lam - _LON0
    N = I + II * dl**2 + III * dl**4 + IIIA * dl**6
    E = _E0 + IV * dl + V * dl**3 + VI * dl**5
    return E, N


def bng_to_osgb36_latlon(E, N):
    a, b = _AIRY
    E = np.asarray(E, float)
    N = np.asarray(N, float)
    e2 = 1 - (b * b) / (a * a)
    phi = (N - _N0) / (a * _F0) + _LAT0
    for _ in range(20):
        M = _meridional_arc(phi, a, b)
        delta = N - _N0 - M
        if np.all(np.abs(delta) < 1e-5):
            break
        phi = phi + delta / (a * _F0)
    sinp, cosp, tanp = np.sin(phi), np.cos(phi), np.tan(phi)
    nu = a * _F0 / np.sqrt(1 - e2 * sinp**2)
    rho = a * _F0 * (1 - e2) / (1 - e2 * sinp**2) ** 1.5
    eta2 = nu / rho - 1
    VII = tanp / (2 * rho * nu)
    VIII = tanp / (24 * rho * nu**3) * (5 + 3 * tanp**2 + eta2 - 9 * tanp**2 * eta2)
    IX = tanp / (720 * rho * nu**5) * (61 + 90 * tanp**2 + 45 * tanp**4)
    X = 1 / (cosp * nu)
    XI = 1 / (cosp * 6 * nu**3) * (nu / rho + 2 * tanp**2)
    XII = 1 / (cosp * 120 * nu**5) * (5 + 28 * tanp**2 + 24 * tanp**4)
    XIIA = 1 / (cosp * 5040 * nu**7) * (61 + 662 * tanp**2 + 1320 * tanp**4 + 720 * tanp**6)
    dE = E - _E0
    lat = phi - VII * dE**2 + VIII * dE**4 - IX * dE**6
    lon = _LON0 + X * dE - XI * dE**3 + XII * dE**5 - XIIA * dE**7
    return np.degrees(lat), np.degrees(lon)


def wgs84_to_bng(lon_deg, lat_deg):
    """WGS84/ETRS89 lon, lat (degrees) -> approximate BNG easting, northing (Helmert, ~5 m)."""
    lat = np.radians(np.asarray(lat_deg, float))
    lon = np.radians(np.asarray(lon_deg, float))
    x, y, z = _geodetic_to_cart(lat, lon, 0.0, _GRS80)
    x2, y2, z2 = _helmert(x, y, z, +1.0)
    la, lo = _cart_to_geodetic(x2, y2, z2, _AIRY)
    return osgb36_latlon_to_bng(np.degrees(la), np.degrees(lo))


def bng_to_wgs84(E, N):
    """Approximate BNG -> WGS84 lon, lat (degrees)."""
    la, lo = bng_to_osgb36_latlon(E, N)
    x, y, z = _geodetic_to_cart(np.radians(la), np.radians(lo), 0.0, _AIRY)
    x2, y2, z2 = _helmert(x, y, z, -1.0)
    lat, lon = _cart_to_geodetic(x2, y2, z2, _GRS80)
    return np.degrees(lon), np.degrees(lat)
