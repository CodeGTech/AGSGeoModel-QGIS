"""Load project.gpkg outputs into QGIS: geology symbology (BGS Lexicon colours), uncertainty and RAG
styling, reviewer attribute forms and 3D renderers (hole sticks, model surfaces, fence panels, HDD
stations). 3D uses vector renderers only, so it works on every QGIS 3.40 build (no mesh 3D needed).
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from qgis.core import (
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsColorRampShader,
    QgsDefaultValue,
    QgsEditorWidgetSetup,
    QgsFillSymbol,
    QgsLayerTreeGroup,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsMeshLayer,
    QgsPalLayerSettings,
    QgsProject,
    QgsProperty,
    QgsRasterLayer,
    QgsRasterShader,
    QgsRendererCategory,
    QgsSingleBandPseudoColorRenderer,
    QgsStyle,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor

from ..database.gpkg import GeoPackage
from ..models.enums import RAG
from ..visualisation.colours import CLASS_BY_CODE, CLASS_HEX, LOCATION_SYMBOLS, RAG_HEX

try:  # 3D is optional (QGIS built without 3D support)
    from qgis._3d import (
        QgsLine3DSymbol,
        QgsPhongMaterialSettings,
        QgsPoint3DSymbol,
        QgsPolygon3DSymbol,
        QgsRuleBased3DRenderer,
        QgsSimpleLineMaterialSettings,
    )

    HAS_3D = True
except ImportError:  # pragma: no cover - environment dependent
    HAS_3D = False

# drawing order (top of the layer tree first) and initial visibility
LAYERS = [
    ("issues", True),
    ("boreholes", True),
    ("bgs_boreholes", True),
    ("hdd_risk_zones", True),
    ("hdd_stations", False),
    ("cross_sections", True),
    ("faults", True),
    ("flow_vectors", False),
    ("head_contours", False),
    ("seepage_paths", False),
    ("surface_contours", False),
    ("uncertainty_zones", False),
    ("domains", False),
    ("water_levels", False),
    ("unit_runs", False),
    ("lithology", False),
    ("cpt", False),
    ("spt", False),
    ("geological_contacts", False),
    ("fence_diagrams", False),
    ("model_surfaces_3d", False),
]
LABELS = {
    "issues": "Validation issues",
    "boreholes": "Exploratory holes",
    "bgs_boreholes": "BGS boreholes",
    "hdd_risk_zones": "HDD risk zones",
    "hdd_stations": "HDD stations",
    "cross_sections": "Section lines",
    "faults": "Faults",
    "flow_vectors": "Groundwater flow",
    "head_contours": "Groundwater head contours",
    "seepage_paths": "Seepage paths",
    "surface_contours": "Surface contours",
    "uncertainty_zones": "Uncertainty zones",
    "domains": "Ground domains",
    "water_levels": "Groundwater observations",
    "unit_runs": "Interpreted units (3D)",
    "lithology": "Logged strata (3D)",
    "cpt": "CPT readings",
    "spt": "SPT results",
    "geological_contacts": "Modelling observations",
    "fence_diagrams": "Fence diagram (3D)",
    "model_surfaces_3d": "Model surfaces (3D)",
}
RUN_TAGGED = {
    "surface_contours",
    "uncertainty_zones",
    "domains",
    "head_contours",
    "flow_vectors",
    "seepage_paths",
    "fence_diagrams",
    "model_surfaces_3d",
}
SEVERITY_HEX = {"critical": "#7B1FA2", "error": "#C62828", "warning": "#F9A825", "info": "#90A4AE"}
DOMAIN_HEX = {
    "artificial": "#9E9E9E",
    "alluvial": "#D9CC9C",
    "glacial": "#A1887F",
    "marine": "#4FC3F7",
    "slope": "#C8A97E",
    "aeolian": "#E6C27A",
    "bedrock": "#7F93B5",
}
HOLE_LABELS = {
    "BH": "Boreholes",
    "CPT": "CPTs",
    "TP": "Trial pits",
    "WS": "Window samples",
    "DP": "Dynamic probes",
    "BGS": "BGS boreholes",
    "OTHER": "Other",
}


# ---------------------------------------------------------------------------- 2D styles
def _symbol(geom: str, colour: str, size: float = 2.2, width: float = 0.8):
    if geom == "point":
        return QgsMarkerSymbol.createSimple(
            {"name": "circle", "color": colour, "size": str(size), "outline_color": "#222222", "outline_width": "0.2"}
        )
    if geom == "line":
        return QgsLineSymbol.createSimple({"color": colour, "width": str(width), "capstyle": "round"})
    return QgsFillSymbol.createSimple({"color": colour, "outline_color": "#333333", "outline_width": "0.1"})


def categorized(
    field: str,
    mapping: dict[str, str],
    geom: str,
    labels: dict[str, str] | None = None,
    size: float = 2.2,
    width: float = 0.8,
) -> QgsCategorizedSymbolRenderer:
    cats = [
        QgsRendererCategory(value, _symbol(geom, colour, size, width), (labels or {}).get(value, str(value)))
        for value, colour in mapping.items()
    ]
    return QgsCategorizedSymbolRenderer(field, cats)


def unit_mapping(kb) -> tuple[dict[str, str], dict[str, str]]:
    return ({c: u.colour for c, u in kb.units.items()}, {c: f"{u.name} ({c})" for c, u in kb.units.items()})


def label(layer: QgsVectorLayer, expression: str, size: float = 8.0) -> None:
    s = QgsPalLayerSettings()
    s.fieldName = expression
    s.isExpression = True
    fmt = s.format()
    fmt.setSize(size)
    s.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(s))
    layer.setLabelsEnabled(True)


def style_layer(layer: QgsVectorLayer, table: str, kb) -> None:
    colours, names = unit_mapping(kb)
    if table == "boreholes":
        cats = []
        for kind, (shape, col) in LOCATION_SYMBOLS.items():
            sym = QgsMarkerSymbol.createSimple({"name": shape, "color": col, "size": "3.2", "outline_color": "#000000"})
            cats.append(QgsRendererCategory(kind, sym, HOLE_LABELS.get(kind, kind)))
        layer.setRenderer(QgsCategorizedSymbolRenderer("kind", cats))
        label(layer, '"location_id"')
    elif table in ("lithology", "unit_runs"):
        layer.setRenderer(categorized("unit", colours, "line", names, width=1.6))
    elif table in ("fence_diagrams", "model_surfaces_3d"):
        layer.setRenderer(categorized("unit", colours, "polygon", names))
    elif table == "uncertainty_zones":
        layer.setRenderer(categorized("class_name", {c.value: CLASS_HEX[c] for c in CLASS_BY_CODE}, "polygon"))
        layer.setOpacity(0.45)
    elif table == "hdd_stations":
        layer.setRenderer(categorized("rag_overall", {r.value: RAG_HEX[r] for r in RAG}, "point", size=1.6))
    elif table == "hdd_risk_zones":
        layer.setRenderer(categorized("rag", {r.value: RAG_HEX[r] for r in RAG}, "line", width=2.4))
    elif table == "issues":
        layer.setRenderer(categorized("severity", SEVERITY_HEX, "point", size=3.6))
    elif table == "faults":
        layer.renderer().setSymbol(
            QgsLineSymbol.createSimple({"color": "#B71C1C", "width": "0.9", "line_style": "dash"})
        )
        label(layer, "\"name\" || coalesce(' (' || \"throw_m\" || ' m)', '')")
    elif table == "domains":
        layer.setRenderer(categorized("domain", DOMAIN_HEX, "polygon"))
        layer.setOpacity(0.5)
    elif table == "flow_vectors":
        sym = QgsMarkerSymbol.createSimple({"name": "arrow", "color": "#0277BD", "size": "4"})
        sym.setDataDefinedAngle(QgsProperty.fromField("azimuth_deg"))
        layer.renderer().setSymbol(sym)
    elif table in ("head_contours", "surface_contours"):
        layer.renderer().setSymbol(
            QgsLineSymbol.createSimple({"color": "#0277BD" if table == "head_contours" else "#6D4C41", "width": "0.3"})
        )
        label(layer, 'format_number("level", 1)', 7.0)
    elif table == "seepage_paths":
        layer.renderer().setSymbol(
            QgsLineSymbol.createSimple({"color": "#4FC3F7", "width": "0.4", "line_style": "dot"})
        )
    elif table == "bgs_boreholes":
        sym = QgsMarkerSymbol.createSimple({"name": "triangle", "color": "#AD1457", "size": "3"})
        layer.renderer().setSymbol(sym)
        label(layer, 'coalesce("reference", "bgs_id") || \' (\' || format_number("confidence", 0) || \'%)\'', 7.0)
    elif table == "cross_sections":
        layer.renderer().setSymbol(QgsLineSymbol.createSimple({"color": "#000000", "width": "0.6"}))
        label(layer, '"name"')


def style_elevation_raster(layer: QgsRasterLayer, ramp: str = "Spectral") -> None:
    prov = layer.dataProvider()
    stats = prov.bandStatistics(1)
    if not (stats.maximumValue > stats.minimumValue):
        return
    cr = QgsStyle.defaultStyle().colorRamp(ramp)
    shader_fn = QgsColorRampShader(stats.minimumValue, stats.maximumValue, cr)
    shader_fn.classifyColorRamp(9)
    shader = QgsRasterShader()
    shader.setRasterShaderFunction(shader_fn)
    layer.setRenderer(QgsSingleBandPseudoColorRenderer(prov, 1, shader))


# ---------------------------------------------------------------------------- forms
def configure_forms(layer: QgsVectorLayer, table: str, kb) -> None:
    """Reviewer workflow: override fields editable with value maps, computed fields read-only."""
    cfg = layer.editFormConfig()
    if table == "lithology":
        options = [{f"{u.name} ({c})": c} for c, u in kb.units.items()]
        editable = ("override_unit", "override_by", "override_reason")
        for i, name in enumerate(layer.fields().names()):
            cfg.setReadOnly(i, name not in editable)
        layer.setEditFormConfig(cfg)
        idx = layer.fields().indexOf("override_unit")
        if idx >= 0:
            layer.setEditorWidgetSetup(idx, QgsEditorWidgetSetup("ValueMap", {"map": options}))
        by = layer.fields().indexOf("override_by")
        if by >= 0:
            layer.setDefaultValueDefinition(by, QgsDefaultValue("@user_full_name", True))
    elif table == "issues":
        for i, name in enumerate(layer.fields().names()):
            cfg.setReadOnly(i, name != "status")
        layer.setEditFormConfig(cfg)
        idx = layer.fields().indexOf("status")
        if idx >= 0:
            layer.setEditorWidgetSetup(
                idx,
                QgsEditorWidgetSetup(
                    "ValueMap", {"map": [{"open": "open"}, {"accepted": "accepted"}, {"dismissed": "dismissed"}]}
                ),
            )
    elif table in ("unit_runs", "boreholes", "hdd_stations", "geological_contacts", "cpt", "spt"):
        for i in range(len(layer.fields())):
            cfg.setReadOnly(i, True)
        layer.setEditFormConfig(cfg)


# ---------------------------------------------------------------------------- 3D
def _phong(colour: str):
    m = QgsPhongMaterialSettings()
    m.setDiffuse(QColor(colour))
    m.setAmbient(QColor(colour).darker(160))
    return m


def configure_3d(layer: QgsVectorLayer, table: str, kb) -> bool:
    if not HAS_3D:
        return False
    root = QgsRuleBased3DRenderer.Rule(None)
    if table in ("lithology", "unit_runs"):
        for code, u in kb.units.items():
            sym = QgsLine3DSymbol()
            sym.setRenderAsSimpleLines(True)  # vertical sticks need true 3D polylines
            sym.setWidth(6.0 if table == "unit_runs" else 3.0)
            sym.setAltitudeClamping(Qgis.AltitudeClamping.Absolute)
            mat = QgsSimpleLineMaterialSettings()
            mat.setAmbient(QColor(u.colour))
            sym.setMaterialSettings(mat)
            root.appendChild(QgsRuleBased3DRenderer.Rule(sym, f"\"unit\" = '{code}'", f"{u.name} ({code})"))
    elif table in ("fence_diagrams", "model_surfaces_3d"):
        for code, u in kb.units.items():
            sym = QgsPolygon3DSymbol()
            sym.setAddBackFaces(True)
            sym.setAltitudeClamping(Qgis.AltitudeClamping.Absolute)
            sym.setMaterialSettings(_phong(u.colour))
            root.appendChild(QgsRuleBased3DRenderer.Rule(sym, f"\"unit\" = '{code}'", f"{u.name} ({code})"))
    elif table in ("hdd_stations", "boreholes"):
        field, mapping = (
            ("rag_overall", {r.value: RAG_HEX[r] for r in RAG})
            if table == "hdd_stations"
            else ("kind", {k: c for k, (_, c) in LOCATION_SYMBOLS.items()})
        )
        for value, colour in mapping.items():
            sym = QgsPoint3DSymbol()
            sym.setShape(Qgis.Point3DShape.Sphere)
            sym.setShapeProperties({"radius": 0.6 if table == "hdd_stations" else 1.5})
            sym.setAltitudeClamping(Qgis.AltitudeClamping.Absolute)
            sym.setMaterialSettings(_phong(colour))
            root.appendChild(QgsRuleBased3DRenderer.Rule(sym, f"\"{field}\" = '{value}'", value))
    else:
        return False
    layer.setRenderer3D(QgsRuleBased3DRenderer(root))
    return True


# ---------------------------------------------------------------------------- loading
def latest_run(gpkg_path: str) -> str | None:
    gp = GeoPackage(gpkg_path, create=False)
    try:
        r = gp.conn.execute(
            "SELECT run_id FROM model_runs WHERE kind = 'stratigraphic_model' ORDER BY created DESC LIMIT 1"
        ).fetchone()
        return r[0] if r else None
    finally:
        gp.close()


def load_project_layers(
    gpkg_path: str, kb, group_name: str = "AGSGeoModel", rasters: bool = True, project: QgsProject | None = None
) -> list:
    """(Re)load the project's layers into a layer-tree group; returns the layers added."""
    project = project or QgsProject.instance()
    root = project.layerTreeRoot()
    old = root.findGroup(group_name)
    if old is not None:
        for node in old.findLayers():
            project.removeMapLayer(node.layerId())
        root.removeChildNode(old)
    group: QgsLayerTreeGroup = root.insertGroup(0, group_name)
    gp = GeoPackage(gpkg_path, create=False)
    try:
        available = set(gp.tables("features"))
        counts = {t: gp.conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in available}
        zone_surfaces = (
            [r[0] for r in gp.conn.execute("SELECT DISTINCT surface FROM uncertainty_zones")]
            if "uncertainty_zones" in available
            else []
        )
    finally:
        gp.close()
    run_id = latest_run(gpkg_path)
    focus = "rockhead" if "rockhead" in zone_surfaces else (zone_surfaces[0] if zone_surfaces else None)
    added = []
    for t, visible in LAYERS:
        if t not in available or counts.get(t, 0) == 0:
            continue
        subset = []
        name = LABELS.get(t, t)
        if t in RUN_TAGGED and run_id:
            subset.append(f"\"run_id\" = '{run_id}'")
        if t in ("uncertainty_zones", "surface_contours") and focus:
            subset.append(f"\"surface\" = '{focus}'")
            name = f"{LABELS[t]} ({focus})"
        # the filter goes into the URI: setSubsetString() on a freshly opened GeoPackage layer fails on
        # some GDAL/SQLite builds when several layers of the same file are filtered
        uri = f"{gpkg_path}|layername={t}" + (f"|subset={' AND '.join(subset)}" if subset else "")
        lyr = QgsVectorLayer(uri, name, "ogr")
        if not lyr.isValid():
            continue
        style_layer(lyr, t, kb)
        configure_forms(lyr, t, kb)
        configure_3d(lyr, t, kb)
        project.addMapLayer(lyr, False)
        group.addLayer(lyr).setItemVisibilityChecked(visible)
        added.append(lyr)
    if rasters and run_id:
        rd = Path(gpkg_path).parent / "rasters" / run_id
        if rd.exists():
            rgroup = group.addGroup("Model surfaces")
            for tif in sorted(rd.glob("*.tif")):
                stem = tif.stem
                if stem.endswith(("_std", "_class", "_confidence")) or stem.startswith(("unit_", "confinement_")):
                    continue
                r = QgsRasterLayer(str(tif), stem)
                if r.isValid():
                    style_elevation_raster(r, "Blues" if stem.startswith("head_") else "Spectral")
                    project.addMapLayer(r, False)
                    rgroup.addLayer(r).setItemVisibilityChecked(stem == "rockhead")
                    added.append(r)
            meshes = sorted(rd.glob("*.2dm"))
            if meshes:
                mgroup = group.addGroup("Surface meshes (MDAL)")
                for m in meshes:
                    ml = QgsMeshLayer(str(m), f"{m.stem} (mesh)", "mdal")
                    if ml.isValid():
                        project.addMapLayer(ml, False)
                        mgroup.addLayer(ml).setItemVisibilityChecked(False)
                        added.append(ml)
            mgroup = group.findGroup("Surface meshes (MDAL)")
            if mgroup is not None:
                mgroup.setExpanded(False)
            rgroup.setExpanded(False)
    return added


def add_bgs_wms(names: list[str] | None = None, project: QgsProject | None = None, group_name: str = "BGS context"):
    """Add BGS 1:50k WMS layers (verified endpoints in :mod:`agsgeomodel.bgs.services`)."""
    from ..bgs.services import ATTRIBUTION, WMS_LAYERS

    project = project or QgsProject.instance()
    root = project.layerTreeRoot()
    group = root.findGroup(group_name) or root.addGroup(group_name)
    added = []
    for title, (url, layer_name) in WMS_LAYERS.items():
        if names and title not in names:
            continue
        uri = f"contextualWMSLegend=0&crs=EPSG:27700&dpiMode=7&format=image/png&layers={layer_name}&styles=&url={url}"
        lyr = QgsRasterLayer(uri, title, "wms")
        if lyr.isValid():
            md = lyr.metadata()
            md.setRights([ATTRIBUTION.format(year=_dt.date.today().year)])
            lyr.setMetadata(md)
            lyr.setOpacity(0.6)
            project.addMapLayer(lyr, False)
            group.addLayer(lyr).setItemVisibilityChecked(False)
            added.append(lyr)
    return added
