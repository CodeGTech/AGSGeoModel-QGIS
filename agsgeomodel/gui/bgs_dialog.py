"""BGS dialog: project-area geology (bedrock, superficial, artificial, faults, landslides, map sheets),
suggested stratigraphy and ranked BGS borehole candidates for gap filling (e.g. ``BGS_FXBH1 87.7 %``).

Selected candidates with AGS data held by BGS are downloaded through the AGS API and merged as
*secondary* data: their observations carry the candidate confidence and the secondary positional
uncertainty, so they inform the model without overriding the site investigation.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from qgis.core import (
    Qgis,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsLineString,
    QgsPointXY,
    QgsPolygon,
    QgsProject,
    QgsVectorLayer,
)
from qgis.PyQt import uic
from qgis.PyQt.QtCore import QMetaType, Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import QDialog, QHeaderView, QMessageBox, QTableWidgetItem

from .. import workflows as wf
from ..bgs.services import ATTRIBUTION
from .layers import add_bgs_wms, categorized, label
from .settings_dialog import bgs_user_settings
from .tasks import FunctionTask

FORM_CLASS, _ = uic.loadUiType(str(Path(__file__).parent / "ui" / "bgs_dialog.ui"))
COLUMNS = ["Use", "Candidate", "Collection", "Distance (m)", "Depth (m)", "Year", "AGS", "Confidence (%)", "Reasons"]


class _NumItem(QTableWidgetItem):
    def __lt__(self, other):  # numeric sort
        try:
            return float(self.data(Qt.ItemDataRole.UserRole)) < float(other.data(Qt.ItemDataRole.UserRole))
        except (TypeError, ValueError):
            return super().__lt__(other)


class BGSDialog(QDialog, FORM_CLASS):
    def __init__(
        self,
        iface,
        runner,
        folder: str | None,
        settings,
        project=None,
        on_province=None,
        on_secondary=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setupUi(self)
        self.iface, self.runner, self.folder, self.settings = iface, runner, folder, settings
        self.project = project  # Project used when no project.gpkg exists yet
        self.on_province, self.on_secondary = on_province, on_secondary
        self.disc = None
        for b in settings.buffer_options_m:
            self.bufferCombo.addItem(f"{b:,.0f} m", float(b))
        self.bufferCombo.setCurrentIndex(min(2, self.bufferCombo.count() - 1))
        self.progressBar.setVisible(False)
        self.candidatesTable.setColumnCount(len(COLUMNS))
        self.candidatesTable.setHorizontalHeaderLabels(COLUMNS)
        self.candidatesTable.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.candidatesTable.horizontalHeader().setStretchLastSection(True)
        self.analyseButton.clicked.connect(self.analyse)
        self.selectQualifiedButton.clicked.connect(self.select_qualified)
        self.addLayerButton.clicked.connect(self.add_to_map)
        self.downloadButton.clicked.connect(self.download)
        self.openLogButton.clicked.connect(self.open_log)
        self.useProvinceButton.clicked.connect(self.use_province)
        self._actions(False)
        self.summaryBrowser.setHtml(
            "<p>Analyse the project area to retrieve BGS geology and search the BGS borehole "
            "indexes (Single Onshore Borehole Index and AGS data held by BGS).</p>"
        )

    def _actions(self, on: bool) -> None:
        for b in (
            self.selectQualifiedButton,
            self.addLayerButton,
            self.downloadButton,
            self.openLogButton,
            self.useProvinceButton,
        ):
            b.setEnabled(on)

    # ------------------------------------------------------------------ analysis
    def analyse(self) -> None:
        buffer_m = float(self.bufferCombo.currentData())
        threshold = self.thresholdSpin.value()
        src = self.folder if self.folder and wf.gpkg_path(self.folder).exists() else self.project
        if src is None:
            QMessageBox.information(self, "AGSGeoModel", "Add AGS data (or run the model) first.")
            return
        self.analyseButton.setEnabled(False)
        self.progressBar.setVisible(True)
        kw = bgs_user_settings()
        crs = self.settings.crs
        task = FunctionTask(
            "AGSGeoModel: BGS discovery",
            lambda progress: wf.bgs_discover(
                src, buffer_m, threshold, connector=wf.bgs_connector(crs, **kw), crs=crs, progress=progress
            ),
            self._done,
            self._failed,
        )
        task.progressChanged.connect(lambda p: self.progressBar.setValue(int(p)))
        self.runner.start(task)

    def _failed(self, exc, trace) -> None:
        self.analyseButton.setEnabled(True)
        self.progressBar.setVisible(False)
        if exc is not None:
            self.summaryBrowser.setHtml(
                f"<p style='color:#C62828'><b>BGS services could not be reached:</b> {exc}</p>"
                "<p>Check the network/proxy settings (QGIS Options &gt; Network) or enable "
                "offline mode with a populated cache in the plugin settings.</p>"
            )

    def _done(self, disc) -> None:
        self.disc = disc
        self.analyseButton.setEnabled(True)
        self.progressBar.setVisible(False)
        self._actions(True)
        an = disc.analysis
        rows = []
        for title, units in (("Bedrock", an.bedrock_units), ("Superficial deposits", an.superficial_units)):
            if units:
                rows.append(
                    f"<b>{title}</b><ul>"
                    + "".join(
                        f"<li><a href='{u['lexicon_url']}'>{u['name']}</a> ({u['code']}) - {u.get('lithology') or ''} "
                        f"{100 * u['area_fraction']:.0f}% of search area</li>"
                        for u in units
                    )
                    + "</ul>"
                )
        prov = (
            f"<b>Suggested stratigraphy:</b> {an.suggested_province} - {an.province_basis}"
            if an.suggested_province
            else "<b>No bundled stratigraphic column matches the mapped bedrock.</b>"
        )
        extra = [
            f"{an.n_faults} fault segment(s)",
            f"{len(an.landslides)} landslide record(s)",
            f"{len(an.map_sheets)} map sheet(s)",
        ]
        notes = "".join(f"<li>{n}</li>" for n in an.notes)
        sheets = "".join(f"<li>{s.get('full_title') or s.get('title') or s}</li>" for s in an.map_sheets[:6])
        n_q = sum(1 for c in disc.candidates if c.qualified)
        self.summaryBrowser.setHtml(
            f"<p>{an.setting}</p><p>{prov}</p>{''.join(rows)}<p>{', '.join(extra)}. "
            f"Local stratigraphy (top down): {' > '.join(an.local_stratigraphy)}</p>"
            + (f"<p><b>Map sheets</b><ul>{sheets}</ul></p>" if sheets else "")
            + (f"<p><b>Notes</b><ul>{notes}</ul></p>" if notes else "")
            + f"<p><b>{len(disc.candidates)} BGS borehole record(s); {n_q} at or above the confidence "
            "threshold.</b></p>"
            f"<p style='color:#666'>{an.attribution}. Coordinates: {an.transform_note}.</p>"
        )
        self._fill()

    def _fill(self) -> None:
        t = self.candidatesTable
        t.setSortingEnabled(False)
        cands = self.disc.candidates if self.disc else []
        t.setRowCount(len(cands))
        for r, c in enumerate(cands):
            use = QTableWidgetItem("")
            use.setFlags(use.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            use.setCheckState(Qt.CheckState.Checked if c.qualified else Qt.CheckState.Unchecked)
            use.setData(Qt.ItemDataRole.UserRole, r)
            t.setItem(r, 0, use)
            vals = [
                (c.label, None),
                (c.collection, None),
                (f"{c.distance_m:,.0f}", c.distance_m),
                ("" if c.length_m is None else f"{float(c.length_m):.1f}", c.length_m),
                (c.year or "", None),
                ("yes" if c.has_ags else "scan only", None),
                (f"{c.confidence:.1f}", c.confidence),
                (explain_candidate(c), None),
            ]
            for k, (text, num) in enumerate(vals, start=1):
                it = _NumItem(text) if num is not None else QTableWidgetItem(text)
                if num is not None:
                    it.setData(Qt.ItemDataRole.UserRole, float(num))
                t.setItem(r, k, it)
        t.setSortingEnabled(True)
        t.sortItems(7, Qt.SortOrder.DescendingOrder)

    def _sync_selection(self) -> list:
        cands = self.disc.candidates if self.disc else []
        for r in range(self.candidatesTable.rowCount()):
            it = self.candidatesTable.item(r, 0)
            cands[int(it.data(Qt.ItemDataRole.UserRole))].selected = it.checkState() == Qt.CheckState.Checked
        return [c for c in cands if c.selected]

    def select_qualified(self) -> None:
        for r in range(self.candidatesTable.rowCount()):
            it = self.candidatesTable.item(r, 0)
            c = self.disc.candidates[int(it.data(Qt.ItemDataRole.UserRole))]
            it.setCheckState(Qt.CheckState.Checked if c.qualified else Qt.CheckState.Unchecked)

    # ------------------------------------------------------------------ outputs
    def add_to_map(self) -> None:
        if self.disc is None:
            return
        self._sync_selection()
        if self.folder and wf.gpkg_path(self.folder).exists():
            wf.save_bgs(self.folder, self.disc)
        crs = self.settings.crs
        proj = QgsProject.instance()
        root = proj.layerTreeRoot()
        group = root.findGroup("BGS context") or root.insertGroup(0, "BGS context")
        lyr = candidates_layer(self.disc.candidates, crs)
        proj.addMapLayer(lyr, False)
        group.insertLayer(0, lyr)
        if self.vectorCheck.isChecked():
            for kind in ("bedrock", "superficial"):
                units = self.disc.data.get(kind) or []
                if units:
                    ml = map_units_layer(units, crs, f"BGS 625k {kind}")
                    proj.addMapLayer(ml, False)
                    group.addLayer(ml).setItemVisibilityChecked(kind == "superficial")
            if self.disc.data.get("faults"):
                fl = faults_layer(self.disc.data["faults"], crs)
                proj.addMapLayer(fl, False)
                group.insertLayer(1, fl)
        if self.wmsCheck.isChecked():
            add_bgs_wms()
        self.iface.messageBar().pushMessage(
            "AGSGeoModel", "BGS layers added (group 'BGS context').", Qgis.MessageLevel.Success, 5
        )

    def download(self) -> None:
        sel = self._sync_selection()
        with_ags = [c for c in sel if c.has_ags]
        if not self.folder:
            QMessageBox.information(self, "AGSGeoModel", "Choose a project folder first.")
            return
        if not with_ags:
            QMessageBox.information(
                self,
                "AGSGeoModel",
                "None of the selected records has AGS data held by BGS. "
                "Records marked 'scan only' have scanned logs: open them with 'Open log / scan' "
                "and digitise the strata if they are needed.",
            )
            return
        kw = bgs_user_settings()
        folder, crs = self.folder, self.settings.crs
        task = FunctionTask(
            "AGSGeoModel: download BGS AGS data",
            lambda progress: wf.download_bgs_ags(folder, with_ags, wf.bgs_connector(crs, **kw)),
            self._downloaded,
            self._failed,
        )
        self.runner.start(task)

    def _downloaded(self, added) -> None:
        cfg = wf.load_config(self.folder)
        if self.on_secondary and cfg is not None:
            self.on_secondary(cfg.secondary_ags)
        n_scan = sum(1 for c in self.disc.candidates if c.selected and not c.has_ags)
        msg = f"{len(added)} BGS AGS file(s) added as secondary data. Re-run the model to use them."
        if n_scan:
            msg += f" {n_scan} selected record(s) have scanned logs only."
        QMessageBox.information(self, "AGSGeoModel", msg)

    def open_log(self) -> None:
        rows = sorted({i.row() for i in self.candidatesTable.selectedIndexes()})
        for r in rows[:5]:
            c = self.disc.candidates[int(self.candidatesTable.item(r, 0).data(Qt.ItemDataRole.UserRole))]
            if c.url:
                QDesktopServices.openUrl(QUrl(c.url))

    def use_province(self) -> None:
        if self.disc and self.disc.analysis.suggested_province and self.on_province:
            self.on_province(self.disc.analysis.suggested_province)
            self.iface.messageBar().pushMessage(
                "AGSGeoModel",
                f"Stratigraphy set to {self.disc.analysis.suggested_province}.",
                Qgis.MessageLevel.Info,
                4,
            )


def explain_candidate(c) -> str:
    """Score factors (0-100) and reasons, e.g. 'proximity 95, gap value 80, ... | 1964 log: check terminology'."""
    factors = ", ".join(f"{k.replace('_', ' ')} {100 * v:.0f}" for k, v in c.breakdown.items())
    return factors + (" | " + "; ".join(c.reasons) if c.reasons else "")


# ---------------------------------------------------------------------------- memory layers
def _fields(spec: list[tuple[str, QMetaType.Type]]) -> QgsFields:
    f = QgsFields()
    for name, typ in spec:
        f.append(QgsField(name, typ))
    return f


def candidates_layer(cands, crs: str) -> QgsVectorLayer:
    lyr = QgsVectorLayer(f"Point?crs={crs}", "BGS borehole candidates", "memory")
    spec = [
        ("label", QMetaType.Type.QString),
        ("collection", QMetaType.Type.QString),
        ("distance_m", QMetaType.Type.Double),
        ("depth_m", QMetaType.Type.Double),
        ("has_ags", QMetaType.Type.Bool),
        ("confidence", QMetaType.Type.Double),
        ("selected", QMetaType.Type.Bool),
        ("url", QMetaType.Type.QString),
        ("explanation", QMetaType.Type.QString),
    ]
    pr = lyr.dataProvider()
    pr.addAttributes(_fields(spec).toList())
    lyr.updateFields()
    feats = []
    for c in cands:
        f = QgsFeature(lyr.fields())
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(c.x, c.y)))
        f.setAttributes(
            [
                c.label,
                c.collection,
                float(c.distance_m),
                None if c.length_m is None else float(c.length_m),
                bool(c.has_ags),
                float(c.confidence),
                bool(c.selected),
                c.url or "",
                explain_candidate(c),
            ]
        )
        feats.append(f)
    pr.addFeatures(feats)
    lyr.updateExtents()
    lyr.setRenderer(
        categorized(
            "CASE WHEN \"selected\" THEN 'selected' ELSE 'not selected' END",
            {"selected": "#AD1457", "not selected": "#F8BBD0"},
            "point",
            size=3.0,
        )
    )
    label(lyr, "\"label\" || ' ' || format_number(\"confidence\", 0) || '%'", 7.0)
    md = lyr.metadata()
    md.setRights([ATTRIBUTION.format(year=_dt.date.today().year)])
    lyr.setMetadata(md)
    return lyr


def map_units_layer(units, crs: str, title: str) -> QgsVectorLayer:
    lyr = QgsVectorLayer(f"Polygon?crs={crs}", title, "memory")
    pr = lyr.dataProvider()
    pr.addAttributes(
        _fields(
            [("code", QMetaType.Type.QString), ("name", QMetaType.Type.QString), ("lithology", QMetaType.Type.QString)]
        ).toList()
    )
    lyr.updateFields()
    feats = []
    for u in units:
        poly = QgsPolygon()
        poly.setExteriorRing(QgsLineString([QgsPointXY(x, y) for x, y in u.rings[0]]))
        for hole in u.rings[1:]:
            poly.addInteriorRing(QgsLineString([QgsPointXY(x, y) for x, y in hole]))
        f = QgsFeature(lyr.fields())
        f.setGeometry(QgsGeometry(poly))
        f.setAttributes([u.code, str(u.attributes.get("lex_d") or u.code).title(), u.attributes.get("rcs_d") or ""])
        feats.append(f)
    pr.addFeatures(feats)
    lyr.updateExtents()
    lyr.setOpacity(0.5)
    return lyr


def faults_layer(faults, crs: str) -> QgsVectorLayer:
    lyr = QgsVectorLayer(f"LineString?crs={crs}", "BGS 625k faults", "memory")
    pr = lyr.dataProvider()
    pr.addAttributes(_fields([("name", QMetaType.Type.QString)]).toList())
    lyr.updateFields()
    feats = []
    for flt in faults:
        f = QgsFeature(lyr.fields())
        f.setGeometry(QgsGeometry(QgsLineString([QgsPointXY(x, y) for x, y in flt.trace])))
        f.setAttributes([flt.name])
        feats.append(f)
    pr.addFeatures(feats)
    lyr.updateExtents()
    from qgis.core import QgsLineSymbol

    lyr.renderer().setSymbol(QgsLineSymbol.createSimple({"color": "#B71C1C", "width": "0.6", "line_style": "dash"}))
    return lyr
