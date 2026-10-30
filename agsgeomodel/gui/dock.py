"""AGSGeoModel project manager dock: project set-up, runs, review, issues and run history."""

from __future__ import annotations

import json
import os
from pathlib import Path

from qgis.core import Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject, QgsRectangle
from qgis.gui import QgsDockWidget
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import workflows as wf
from ..geology.knowledge import load_column
from ..pipeline import run_pipeline
from . import qgis_bridge as qb
from .issues_panel import IssuesPanel
from .layers import load_project_layers
from .project_panel import ProjectPanel
from .review_panel import ReviewPanel
from .tasks import FunctionTask, TaskRunner


class ProjectDock(QgsDockWidget):
    def __init__(self, iface, runner: TaskRunner, parent=None):
        super().__init__("AGSGeoModel", parent)
        self.setObjectName("AGSGeoModelDock")
        self.iface = iface
        self.runner = runner
        self.last_result = None
        root = QWidget()
        v = QVBoxLayout(root)
        v.setContentsMargins(4, 4, 4, 4)
        self.tabs = QTabWidget()
        self.project_panel = ProjectPanel()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.project_panel)
        self.review_panel = ReviewPanel()
        self.issues_panel = IssuesPanel()
        self.tabs.addTab(scroll, "Project")
        self.tabs.addTab(self.review_panel, "Review")
        self.tabs.addTab(self.issues_panel, "Issues")
        self.tabs.addTab(self._runs_tab(), "Runs")
        v.addWidget(self.tabs, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        v.addWidget(self.progress)
        v.addWidget(self.status)
        self.setWidget(root)

        self.project_panel.runRequested.connect(self.run_model)
        self.project_panel.loadRequested.connect(self.load_layers)
        self.project_panel.folderChanged.connect(self.open_folder)
        self.review_panel.zoomTo.connect(self.zoom_to_hole)
        self.issues_panel.zoomTo.connect(self.zoom_to_hole)
        self.review_panel.decided.connect(
            lambda: self.status.setText("Decision recorded. Re-run the model to apply it.")
        )
        folder = qb.project_setting("folder", "")
        if folder and Path(folder).exists():
            self.project_panel.set_folder(folder)
            self.open_folder(folder)

    # ------------------------------------------------------------------ helpers
    @property
    def folder(self) -> str | None:
        return self.project_panel.project_folder()

    def kb(self):
        st = self.project_panel.settings()
        try:
            return load_column(st.province).kb
        except Exception:
            return load_column("london_basin").kb

    def message(self, text: str, level=Qgis.MessageLevel.Info, duration: int = 6) -> None:
        self.iface.messageBar().pushMessage("AGSGeoModel", text, level, duration)

    # ------------------------------------------------------------------ project folder
    def open_folder(self, folder: str) -> None:
        if not folder:
            return
        qb.set_project_setting("folder", folder)
        cfg = wf.load_config(folder)
        self.project_panel.load_config(cfg)
        self.review_panel.set_project(folder, self.kb(), qb.reviewer_name())
        self.issues_panel.set_project(folder)
        self.refresh_runs()
        if cfg is None:
            self.status.setText("New project: add AGS files and press Run model.")
        else:
            self.status.setText(f"Project loaded ({len(cfg.ags_paths)} AGS file(s)).")

    # ------------------------------------------------------------------ run
    def run_model(self) -> None:
        folder = self.folder
        if not folder:
            QMessageBox.information(self, "AGSGeoModel", "Choose a project folder first.")
            return
        if not self.project_panel.ags_files():
            QMessageBox.information(self, "AGSGeoModel", "Add at least one AGS file.")
            return
        if self.runner.busy():
            QMessageBox.information(self, "AGSGeoModel", "A model run is already in progress.")
            return
        try:
            cfg = self.project_panel.build_config(folder, wf.load_config(folder))
        except Exception as exc:
            QMessageBox.warning(self, "AGSGeoModel", f"Could not prepare the inputs:\n{exc}")
            return
        wf.save_config(cfg, folder)
        wf.clear_cache()
        self.project_panel.btn_run.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        task = FunctionTask(
            "AGSGeoModel: build geological model",
            lambda progress: run_pipeline(cfg, progress),
            self._run_done,
            self._run_failed,
        )
        task.progressChanged.connect(lambda p: self.progress.setValue(int(p)))
        task.message.connect(self.status.setText)
        self.runner.start(task)

    def _run_done(self, res) -> None:
        self.last_result = res
        self.project_panel.btn_run.setEnabled(True)
        self.progress.setVisible(False)
        c = res.issues.counts()
        self.status.setText(
            f"Model {res.model.run.run_id} built in {res.timings.get('total', 0):.0f} s: "
            f"{c['critical']} critical, {c['error']} errors, {c['warning']} warnings. " + " ".join(res.notes)
        )
        self.load_layers()
        self.review_panel.refresh()
        self.issues_panel.refresh()
        self.refresh_runs()
        if res.paths.get("report"):
            self.message(f"Model built. Report: {res.paths['report']}", Qgis.MessageLevel.Success, 10)

    def _run_failed(self, exc, trace: str) -> None:
        self.project_panel.btn_run.setEnabled(True)
        self.progress.setVisible(False)
        if exc is None:
            self.status.setText("Run cancelled.")
            return
        self.status.setText(f"Run failed: {exc}")
        self.message(f"Model run failed: {exc} (details in the log panel)", Qgis.MessageLevel.Critical, 0)

    def load_layers(self) -> None:
        folder = self.folder
        if not folder or not wf.gpkg_path(folder).exists():
            self.message("No project.gpkg yet: run the model first.", Qgis.MessageLevel.Warning)
            return
        layers = load_project_layers(str(wf.gpkg_path(folder)), self.kb())
        crs = QgsCoordinateReferenceSystem(self.project_panel.settings().crs)
        if QgsProject.instance().crs() != crs and len(QgsProject.instance().mapLayers()) <= len(layers) + 1:
            QgsProject.instance().setCrs(crs)
        bh = next((l for l in layers if l.name() == "Exploratory holes"), None)
        if bh is not None and self.iface.mapCanvas() is not None:
            ext = bh.extent()
            ext.grow(max(ext.width(), ext.height()) * 0.15 + 20)
            self._zoom_extent(ext, bh.crs())

    def _zoom_extent(self, ext: QgsRectangle, crs) -> None:
        canvas = self.iface.mapCanvas()
        tr = QgsCoordinateTransform(crs, canvas.mapSettings().destinationCrs(), QgsProject.instance())
        canvas.setExtent(tr.transformBoundingBox(ext))
        canvas.refresh()

    def zoom_to_hole(self, location_id: str) -> None:
        bh = next((l for l in QgsProject.instance().mapLayersByName("Exploratory holes")), None)
        if bh is None:
            self.load_layers()
            bh = next((l for l in QgsProject.instance().mapLayersByName("Exploratory holes")), None)
        if bh is None:
            return
        safe = location_id.replace("'", "''")
        bh.selectByExpression(f"\"location_id\" = '{safe}'")
        ids = bh.selectedFeatureIds()
        if ids:
            ext = bh.boundingBoxOfSelected()
            ext.grow(60)
            self._zoom_extent(ext, bh.crs())
            self.iface.mapCanvas().flashFeatureIds(bh, ids)

    # ------------------------------------------------------------------ runs tab
    def _runs_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.runs_table = QTableWidget(0, 5)
        self.runs_table.setHorizontalHeaderLabels(["Run", "Created (UTC)", "Inputs hash", "KB", "Summary"])
        self.runs_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.runs_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.runs_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.runs_table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(
            QLabel(
                "Every run is recorded with its inputs hash, configuration, knowledge-base version and "
                "random seed (table model_runs), so outputs can be regenerated."
            )
        )
        lay.addWidget(self.runs_table, 1)
        row = QHBoxLayout()
        b1 = QPushButton("Open folder")
        b1.clicked.connect(lambda: self.folder and QDesktopServices.openUrl(QUrl.fromLocalFile(self.folder)))
        b2 = QPushButton("Open report")
        b2.clicked.connect(self.open_report)
        b3 = QPushButton("Purge old runs")
        b3.setToolTip("Delete rasters and outputs of all but the five most recent runs")
        b3.clicked.connect(self.purge)
        for b in (b1, b2, b3):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        return w

    def refresh_runs(self) -> None:
        self.runs_table.setRowCount(0)
        if not self.folder or not wf.gpkg_path(self.folder).exists():
            return
        from ..database import ProjectStore

        with ProjectStore(wf.gpkg_path(self.folder)) as st:
            runs = list(reversed(st.runs()))
        self.runs_table.setRowCount(len(runs))
        for i, r in enumerate(runs):
            summ = r["summary"] if isinstance(r["summary"], dict) else json.loads(r["summary"] or "{}")
            text = ", ".join(f"{k}={v}" for k, v in list(summ.items())[:6]) if isinstance(summ, dict) else str(summ)
            for c, v in enumerate(
                [r["run_id"], (r["created"] or "")[:19], (r["input_hash"] or "")[:12], r["kb_version"] or "", text]
            ):
                self.runs_table.setItem(i, c, QTableWidgetItem(str(v)))

    def open_report(self) -> None:
        if not self.folder:
            return
        p = Path(self.folder) / "report.html"
        if p.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
        else:
            self.message("No report yet: run the model with 'HTML report' ticked.", Qgis.MessageLevel.Warning)

    def purge(self) -> None:
        if not self.folder or not wf.gpkg_path(self.folder).exists():
            return
        if (
            QMessageBox.question(self, "AGSGeoModel", "Delete the outputs of all but the five most recent runs?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        from ..database import ProjectStore

        with ProjectStore(wf.gpkg_path(self.folder)) as st:
            dropped = st.purge_runs(5)
        self.refresh_runs()
        self.message(f"{len(dropped)} run(s) purged.")

    def closeEvent(self, event) -> None:
        super().closeEvent(event)


def open_path(path: str) -> None:
    if os.path.exists(path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
