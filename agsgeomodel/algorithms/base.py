"""Shared helpers for the AGSGeoModel Processing algorithms.

Algorithms are thin wrappers around :mod:`agsgeomodel.workflows` / :mod:`agsgeomodel.pipeline`, so
they behave exactly like the GUI and the command line and can be chained in the graphical modeler or
called from scripts with ``processing.run("agsgeomodel:<name>", {...})``.
"""

from __future__ import annotations

from pathlib import Path

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsLineString,
    QgsPoint,
    QgsPolygon,
    QgsProcessingAlgorithm,
    QgsProcessingException,
)
from qgis.PyQt.QtCore import QCoreApplication, QMetaType
from qgis.PyQt.QtGui import QIcon

ICON_DIR = Path(__file__).resolve().parent.parent / "icons"
DOCS = "https://github.com/YOUR-ORG/AGSGeoModel-QGIS/blob/main/docs/user_guide.md"


class AGSGeoModelAlgorithm(QgsProcessingAlgorithm):
    GROUP = ("modelling", "Geological modelling")
    NAME = ""
    TITLE = ""
    HELP = ""
    ICON = "agsgeomodel.svg"

    def name(self):
        return self.NAME

    def displayName(self):
        return self.tr(self.TITLE)

    def group(self):
        return self.tr(self.GROUP[1])

    def groupId(self):
        return self.GROUP[0]

    def shortHelpString(self):
        return self.tr(self.HELP)

    def helpUrl(self):
        return f"{DOCS}#{self.NAME.replace('_', '-')}"

    def icon(self):
        return QIcon(str(ICON_DIR / self.ICON))

    def createInstance(self):
        return type(self)()

    def tr(self, text: str) -> str:
        return QCoreApplication.translate("AGSGeoModel", text)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def progress(feedback, start: float = 0.0, end: float = 1.0):
        """Adapter from the pipeline progress callback to Processing feedback (with cancellation)."""

        def f(frac: float, msg: str = "") -> None:
            if feedback is not None and feedback.isCanceled():
                raise QgsProcessingException("cancelled by the user")
            if feedback is not None:
                feedback.setProgress(100.0 * (start + (end - start) * float(frac)))
                if msg:
                    feedback.pushInfo(msg)

        return f

    @staticmethod
    def check_folder(folder: str) -> Path:
        from ..workflows import gpkg_path

        if not folder or not gpkg_path(folder).exists():
            raise QgsProcessingException(
                f"{folder!r} is not an AGSGeoModel project folder (no project.gpkg). "
                "Run 'Build geological model' or 'Import and interpret AGS' first."
            )
        return Path(folder)


# ---------------------------------------------------------------------------- feature helpers
_QT = {
    "text": QMetaType.Type.QString,
    "real": QMetaType.Type.Double,
    "integer": QMetaType.Type.LongLong,
    "boolean": QMetaType.Type.Bool,
}


def make_fields(spec: list[tuple[str, str]]) -> QgsFields:
    f = QgsFields()
    for name, typ in spec:
        f.append(QgsField(name, _QT[typ]))
    return f


def geometry_from_geojson(g: dict | None) -> QgsGeometry:
    if not g:
        return QgsGeometry()
    t, c = g["type"], g["coordinates"]

    def pt(p):
        return QgsPoint(p[0], p[1], p[2]) if len(p) > 2 and p[2] is not None else QgsPoint(p[0], p[1])

    if t == "Point":
        return QgsGeometry(pt(c))
    if t == "LineString":
        return QgsGeometry(QgsLineString([pt(p) for p in c]))
    if t == "Polygon":
        poly = QgsPolygon()
        poly.setExteriorRing(QgsLineString([pt(p) for p in c[0]]))
        for ring in c[1:]:
            poly.addInteriorRing(QgsLineString([pt(p) for p in ring]))
        return QgsGeometry(poly)
    raise QgsProcessingException(f"unsupported geometry type {t}")


def write_features(sink, fields: QgsFields, rows: list[dict], feedback=None) -> int:
    names = fields.names()
    n = 0
    for r in rows:
        f = QgsFeature(fields)
        f.setGeometry(geometry_from_geojson(r.get("geometry")))
        props = r.get("properties", {})
        vals = []
        for name in names:
            v = props.get(name)
            if isinstance(v, (list, dict)):
                import json

                v = json.dumps(v, default=str)
            elif hasattr(v, "item"):
                v = v.item()
            if isinstance(v, float) and v != v:
                v = None
            vals.append(v)
        f.setAttributes(vals)
        sink.addFeature(f, QgsFeatureSink.Flag.FastInsert)
        n += 1
        if feedback is not None and feedback.isCanceled():
            break
    return n


def crs_of(authid: str) -> QgsCoordinateReferenceSystem:
    return QgsCoordinateReferenceSystem(authid)


WKB = {
    "PointZ": Qgis.WkbType.PointZ,
    "Point": Qgis.WkbType.Point,
    "LineString": Qgis.WkbType.LineString,
    "LineStringZ": Qgis.WkbType.LineStringZ,
    "PolygonZ": Qgis.WkbType.PolygonZ,
    "Polygon": Qgis.WkbType.Polygon,
    "None": Qgis.WkbType.NoGeometry,
}
