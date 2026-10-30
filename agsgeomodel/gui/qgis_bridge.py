"""Bridges between QGIS and the headless core (imported only inside QGIS).

* routes coordinate transforms through ``QgsCoordinateTransform`` (PROJ plus the transformation
  grids configured in QGIS, e.g. OSTN15) and HTTP through ``QgsBlockingNetworkRequest`` (QGIS proxy
  and authentication settings);
* converts QGIS vector layers / Processing sources to the core's GeoJSON-like features;
* stores plugin settings in ``QgsSettings`` (user) and in the QGIS project (per project).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from qgis.core import (
    QgsBlockingNetworkRequest,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCoordinateTransformContext,
    QgsFeatureRequest,
    QgsLineString,
    QgsPointXY,
    QgsProject,
    QgsSettings,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest

from ..bgs.http import USER_AGENT, set_fetcher
from ..core.errors import ConnectorError
from ..core.transform import set_host_transformer_factory

SETTINGS_PREFIX = "AGSGeoModel"
PROJECT_SCOPE = "AGSGeoModel"

# The transform context is read on the main thread and cached: QgsProject must not be touched
# from the worker threads that run QgsTasks and Processing algorithms.
_CONTEXT: QgsCoordinateTransformContext | None = None


def _refresh_context(*_args) -> None:
    global _CONTEXT
    _CONTEXT = QgsProject.instance().transformContext()


def transform_context() -> QgsCoordinateTransformContext:
    return _CONTEXT if _CONTEXT is not None else QgsCoordinateTransformContext()


# ---------------------------------------------------------------------------- transforms / network
def make_transformer(src: str, dst: str, context: QgsCoordinateTransformContext | None = None):
    tr = QgsCoordinateTransform(
        QgsCoordinateReferenceSystem(src), QgsCoordinateReferenceSystem(dst), context or transform_context()
    )

    def f(x, y):
        x = np.atleast_1d(np.asarray(x, float))
        y = np.atleast_1d(np.asarray(y, float))
        if len(x) > 1:  # bulk transform through a line string (one PROJ call)
            ls = QgsLineString(x.tolist(), y.tolist())
            ls.transform(tr)
            return (np.array([ls.xAt(i) for i in range(len(x))]), np.array([ls.yAt(i) for i in range(len(x))]))
        p = tr.transform(QgsPointXY(float(x[0]), float(y[0])))
        return np.array([p.x()]), np.array([p.y()])

    return f


def qgis_fetcher(url: str, headers: dict) -> bytes:
    req = QNetworkRequest(QUrl(url))
    req.setRawHeader(b"User-Agent", USER_AGENT.encode())
    for k, v in headers.items():
        req.setRawHeader(k.encode(), str(v).encode())
    br = QgsBlockingNetworkRequest()
    err = br.get(req, forceRefresh=False)
    if err != QgsBlockingNetworkRequest.ErrorCode.NoError:
        raise ConnectorError(f"{br.errorMessage()} ({url})")
    return bytes(br.reply().content())


def install() -> None:
    _refresh_context()
    QgsProject.instance().transformContextChanged.connect(_refresh_context)
    QgsProject.instance().readProject.connect(_refresh_context)
    set_host_transformer_factory(lambda src, dst: make_transformer(src, dst))
    set_fetcher(qgis_fetcher)


def uninstall() -> None:
    for sig in (QgsProject.instance().transformContextChanged, QgsProject.instance().readProject):
        try:
            sig.disconnect(_refresh_context)
        except (TypeError, RuntimeError):
            pass
    set_host_transformer_factory(None)
    set_fetcher(None)


# ---------------------------------------------------------------------------- layers -> features
def _value(v):
    if v is None or (hasattr(v, "isNull") and v.isNull()):
        return None
    if hasattr(v, "toPyDateTime"):
        return v.toPyDateTime().isoformat()
    if hasattr(v, "toPyDate"):
        return v.toPyDate().isoformat()
    return v


def features_from(
    source,
    target_crs: str | None = None,
    selected_only: bool = False,
    context: QgsCoordinateTransformContext | None = None,
) -> list[dict]:
    """GeoJSON-like features from a QGIS vector layer or Processing feature source, optionally
    transformed to ``target_crs``."""
    tr = None
    if target_crs and source.sourceCrs().authid() != target_crs:
        tr = QgsCoordinateTransform(
            source.sourceCrs(), QgsCoordinateReferenceSystem(target_crs), context or transform_context()
        )
    if selected_only and hasattr(source, "getSelectedFeatures"):
        feats = source.getSelectedFeatures()
    else:
        feats = source.getFeatures(QgsFeatureRequest())
    names = source.fields().names()
    out = []
    for f in feats:
        g = f.geometry()
        if tr is not None and not g.isNull():
            g.transform(tr)
        geom = json.loads(g.asJson()) if not g.isNull() else None
        out.append({"geometry": geom, "properties": {n: _value(f[n]) for n in names}})
    return out


def lines_from(
    source, target_crs: str | None = None, selected_only: bool = False, name_field: str | None = None, context=None
) -> list[tuple[str, np.ndarray]]:
    """[(name, (n, 2) vertices)] for every line feature (multi-part features: longest part)."""
    out = []
    for k, f in enumerate(features_from(source, target_crs, selected_only, context), start=1):
        g = f["geometry"]
        if not g:
            continue
        parts = (
            [g["coordinates"]]
            if g["type"] == "LineString"
            else g["coordinates"]
            if g["type"] == "MultiLineString"
            else []
        )
        parts = [np.asarray(p, float)[:, :2] for p in parts if len(p) >= 2]
        if not parts:
            continue
        best = max(parts, key=lambda a: float(np.hypot(*np.diff(a, axis=0).T).sum()))
        name = str(f["properties"].get(name_field) or "") if name_field else ""
        out.append((name or f"Line {k}", best))
    return out


def is_line_layer(layer) -> bool:
    return layer is not None and layer.geometryType() == QgsWkbTypes.GeometryType.LineGeometry


# ---------------------------------------------------------------------------- settings
def user_setting(key: str, default=None, type_=None):
    s = QgsSettings()
    full = f"{SETTINGS_PREFIX}/{key}"
    if type_ is not None:
        return s.value(full, default, type=type_)
    return s.value(full, default)


def set_user_setting(key: str, value) -> None:
    QgsSettings().setValue(f"{SETTINGS_PREFIX}/{key}", value)


def project_setting(key: str, default: str = "") -> str:
    v, ok = QgsProject.instance().readEntry(PROJECT_SCOPE, key, default)
    return v if ok else default


def set_project_setting(key: str, value: str) -> None:
    QgsProject.instance().writeEntry(PROJECT_SCOPE, key, value)


def reviewer_name() -> str:
    name = user_setting("reviewer", "")
    if name:
        return str(name)
    try:
        from qgis.core import QgsApplication

        return QgsApplication.userFullName() or QgsApplication.userLoginName() or "reviewer"
    except Exception:  # pragma: no cover
        return "reviewer"


# ---------------------------------------------------------------------------- rasters
def export_raster(layer, path: Path, crs: str) -> str:
    """Use the raster's own file when it is a GDAL file in the project CRS; otherwise warp it."""
    src = layer.source()
    if (
        layer.providerType() == "gdal"
        and Path(src).exists()
        and layer.crs().authid() == crs
        and Path(src).suffix.lower() in (".tif", ".tiff", ".asc")
    ):
        return src
    from osgeo import gdal

    gdal.UseExceptions()
    ds = gdal.Warp(str(path), src, dstSRS=crs, format="GTiff", resampleAlg="bilinear")
    if ds is None:
        raise RuntimeError(f"could not convert {layer.name()} to a GeoTIFF")
    ds = None
    return str(path)
