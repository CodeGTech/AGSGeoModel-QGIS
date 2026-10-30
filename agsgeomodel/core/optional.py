"""Helpers for optional dependencies.

The core only hard-depends on NumPy (always shipped with QGIS). Everything else is imported
lazily through these helpers so that a missing package disables a feature with a clear message
instead of breaking plugin start-up."""

from __future__ import annotations

import importlib
from functools import cache
from types import ModuleType

from .errors import DependencyMissing


@cache
def optional_import(name: str) -> ModuleType | None:
    """Return the imported module or ``None`` if it is not installed / fails to import."""
    try:
        return importlib.import_module(name)
    except Exception:  # pragma: no cover - environment dependent
        return None


def require(name: str, feature: str, package: str | None = None) -> ModuleType:
    """Import ``name`` or raise :class:`DependencyMissing` naming the feature that needs it."""
    mod = optional_import(name)
    if mod is None:
        raise DependencyMissing(package or name, feature)
    return mod


def has(name: str) -> bool:
    return optional_import(name) is not None


def available_features() -> dict[str, bool]:
    """Report which optional capabilities are available (shown in the plugin About box)."""
    return {
        "scipy (fast neighbour search, Delaunay)": has("scipy"),
        "pandas (DataFrame export)": has("pandas"),
        "geopandas (GeoDataFrame export)": has("geopandas"),
        "pyproj (CRS transforms outside QGIS)": has("pyproj"),
        "openpyxl (Excel import/export)": has("openpyxl"),
        "python_ags4 (official AGS4 rule checker)": has("python_ags4"),
        "sklearn (trainable formation classifier)": has("sklearn"),
        "lightgbm (gradient boosting classifier)": has("lightgbm"),
        "xgboost (gradient boosting classifier)": has("xgboost"),
        "sentence_transformers (embedding similarity)": has("sentence_transformers"),
        "gempy (implicit 3D modelling backend)": has("gempy"),
        "pyvista (3D viewer)": has("pyvista"),
    }
