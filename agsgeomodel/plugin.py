"""QGIS plugin entry point: toolbar, menu, project dock, Processing provider and map tools."""

from __future__ import annotations

from pathlib import Path

from qgis.core import Qgis, QgsApplication, QgsProject
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QIcon
from qgis.PyQt.QtWidgets import QDialog, QFileDialog, QMessageBox

try:  # Qt6 moved QAction to QtGui
    from qgis.PyQt.QtGui import QAction
except ImportError:  # Qt5
    from qgis.PyQt.QtWidgets import QAction

from . import __version__
from . import workflows as wf
from .core.config import ProjectSettings
from .gui import qgis_bridge
from .gui.tasks import FunctionTask, TaskRunner

ICONS = Path(__file__).resolve().parent / "icons"
DOCS_URL = "https://github.com/YOUR-ORG/AGSGeoModel-QGIS/blob/main/docs/user_guide.md"
MENU = "&AGSGeoModel"


class AGSGeoModelPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.actions: list[QAction] = []
        self.toolbar = None
        self.dock = None
        self.runner = TaskRunner()
        self.capture_tool = None
        self.capture_mode: str | None = None
        self.viewers: list[QDialog] = []

    # ------------------------------------------------------------------ set-up
    def initProcessing(self):
        from .algorithms.provider import AGSGeoModelProvider

        self.provider = AGSGeoModelProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def _action(self, icon: str | None, text: str, slot, checkable: bool = False, toolbar: bool = True) -> QAction:
        a = QAction(QIcon(str(ICONS / icon)) if icon else QIcon(), text, self.iface.mainWindow())
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        self.iface.addPluginToMenu(MENU, a)
        if toolbar and self.toolbar is not None:
            self.toolbar.addAction(a)
        self.actions.append(a)
        return a

    def initGui(self):
        qgis_bridge.install()
        self.initProcessing()
        self.toolbar = self.iface.addToolBar("AGSGeoModel")
        self.toolbar.setObjectName("AGSGeoModelToolbar")
        self.a_dock = self._action("agsgeomodel.svg", "AGSGeoModel project manager", self.toggle_dock, checkable=True)
        self._action("import.svg", "Import AGS…", self.import_wizard)
        self._action("run.svg", "Build geological model", self.run_model)
        self._action("bgs.svg", "BGS geology and boreholes…", self.bgs_dialog)
        self.a_section = self._action("section.svg", "Draw cross section", self.start_section, checkable=True)
        self.a_hdd = self._action("hdd.svg", "Assess HDD alignment", self.start_hdd, checkable=True)
        self._action("settings.svg", "Settings…", self.settings_dialog, toolbar=False)
        self._action(None, "Create synthetic example project…", self.example_project, toolbar=False)
        self._action(None, "Help", self.help, toolbar=False)
        from .gui.section_tool import PolylineCaptureTool

        self.capture_tool = PolylineCaptureTool(self.iface.mapCanvas())
        self.capture_tool.captured.connect(self._captured)
        self.iface.mapCanvas().mapToolSet.connect(self._tool_changed)

    def unload(self):
        self.runner.cancel_all()
        try:
            self.iface.mapCanvas().mapToolSet.disconnect(self._tool_changed)
        except (TypeError, RuntimeError):
            pass
        if self.capture_tool is not None and self.iface.mapCanvas().mapTool() is self.capture_tool:
            self.iface.mapCanvas().unsetMapTool(self.capture_tool)
        for a in self.actions:
            self.iface.removePluginMenu(MENU, a)
            self.iface.removeToolBarIcon(a)
        self.actions = []
        if self.toolbar is not None:
            self.toolbar.deleteLater()
            self.toolbar = None
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
        qgis_bridge.uninstall()

    # ------------------------------------------------------------------ dock
    def ensure_dock(self, show: bool = True):
        if self.dock is None:
            from .gui.dock import ProjectDock

            self.dock = ProjectDock(self.iface, self.runner, self.iface.mainWindow())
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
            self.dock.visibilityChanged.connect(self.a_dock.setChecked)
            self.dock.project_panel.btn_validate.clicked.connect(
                lambda: self.import_wizard(files=[f for f in [self.dock.project_panel.selected_ags()] if f])
            )
        if show:
            self.dock.show()
            self.dock.raise_()
        return self.dock

    def toggle_dock(self, checked: bool = True):
        if self.dock is not None and self.dock.isVisible() and not checked:
            self.dock.hide()
        else:
            self.ensure_dock()

    def settings(self) -> ProjectSettings:
        dock = self.ensure_dock(show=False)
        folder = dock.folder
        cfg = wf.load_config(folder) if folder else None
        base = ProjectSettings.from_dict(cfg.settings) if cfg and cfg.settings else None
        return dock.project_panel.settings(base)

    def folder(self) -> str | None:
        dock = self.ensure_dock()
        if not dock.folder:
            QMessageBox.information(
                self.iface.mainWindow(), "AGSGeoModel", "Choose a project folder in the AGSGeoModel panel first."
            )
            return None
        return dock.folder

    # ------------------------------------------------------------------ actions
    def import_wizard(self, checked=False, files: list[str] | None = None):
        from .gui.import_wizard import ImportWizard

        dock = self.ensure_dock()
        wiz = ImportWizard(self.iface.mainWindow(), files or [], self.settings().crs)
        if wiz.exec() == QDialog.DialogCode.Accepted:
            dock.project_panel.add_ags(wiz.files)
            if wiz.crs_override:
                dock.project_panel.crs_override = wiz.crs_override
            self.iface.messageBar().pushMessage(
                "AGSGeoModel", f"{len(wiz.files)} AGS file(s) ready: press Run model.", Qgis.MessageLevel.Info, 5
            )

    def run_model(self):
        self.ensure_dock().run_model()

    def bgs_dialog(self):
        from .gui.bgs_dialog import BGSDialog

        dock = self.ensure_dock()
        folder = dock.folder
        project = None
        if not (folder and wf.gpkg_path(folder).exists()):
            files = dock.project_panel.ags_files()
            if not files:
                QMessageBox.information(
                    self.iface.mainWindow(),
                    "AGSGeoModel",
                    "Add AGS files (or run the model) first: the search area is built around the exploratory holes.",
                )
                return
            from .importers.ags import import_ags

            st = self.settings()
            for k, f in enumerate(files):
                imp = import_ags(f, target_crs=st.crs, crs_override=dock.project_panel.crs_override)
                if project is None:
                    project = imp.project
                else:
                    project.merge(imp.project, id_prefix=f"F{k}:")
        dlg = BGSDialog(
            self.iface,
            self.runner,
            folder,
            self.settings(),
            project,
            on_province=self._set_province,
            on_secondary=dock.project_panel.set_secondary,
            parent=self.iface.mainWindow(),
        )
        dlg.exec()

    def _set_province(self, province: str) -> None:
        p = self.ensure_dock().project_panel
        i = p.province.findData(province)
        if i >= 0:
            p.province.setCurrentIndex(i)

    def settings_dialog(self):
        from .gui.settings_dialog import SettingsDialog

        dock = self.ensure_dock()
        folder = dock.folder
        cfg = wf.load_config(folder) if folder else None
        dlg = SettingsDialog(
            self.settings(),
            cfg.learned_model_path if cfg else None,
            cfg.train_learned_model if cfg else False,
            self.iface.mainWindow(),
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        dlg.save_user_settings()
        dock.review_panel.reviewer = qgis_bridge.reviewer_name()
        if folder:
            st = dlg.project_settings()
            cfg = cfg or wf.new_config(folder, dock.project_panel.ags_files(), st)
            cfg.settings = st.to_dict()
            cfg.learned_model_path = dlg.learned_model_path
            cfg.train_learned_model = dlg.train
            wf.save_config(cfg, folder)

    # ------------------------------------------------------------------ map tools
    def start_section(self, checked: bool):
        self._start_capture("section" if checked else None)

    def start_hdd(self, checked: bool):
        self._start_capture("hdd" if checked else None)

    def _start_capture(self, mode: str | None):
        canvas = self.iface.mapCanvas()
        if mode is None:
            canvas.unsetMapTool(self.capture_tool)
            return
        folder = self.folder()
        if folder is None or not wf.gpkg_path(folder).exists():
            if folder is not None:
                QMessageBox.information(self.iface.mainWindow(), "AGSGeoModel", "Build the model first.")
            self.a_section.setChecked(False)
            self.a_hdd.setChecked(False)
            return
        self.capture_mode = mode
        self.a_section.setChecked(mode == "section")
        self.a_hdd.setChecked(mode == "hdd")
        canvas.setMapTool(self.capture_tool)
        self.iface.messageBar().pushMessage(
            "AGSGeoModel",
            "Click to add vertices, right-click or Enter to finish, Backspace to undo, Esc to cancel.",
            Qgis.MessageLevel.Info,
            6,
        )

    def _tool_changed(self, new_tool, old_tool=None):
        if new_tool is not self.capture_tool:
            self.a_section.setChecked(False)
            self.a_hdd.setChecked(False)
            self.capture_mode = None

    def _captured(self, points):
        from .core.geometry import polyline_length
        from .gui.section_tool import HDDParamsDialog, SectionParamsDialog, to_project_crs

        folder = self.ensure_dock(show=False).folder
        if not folder:
            return
        st = self.settings()
        line = to_project_crs(points, self.iface.mapCanvas(), st.crs)
        import numpy as np

        L = polyline_length(np.asarray(line))
        if self.capture_mode == "hdd":
            dlg = HDDParamsDialog(self.iface.mainWindow(), length=L)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            spec = dlg.spec()
            task = FunctionTask(
                f"AGSGeoModel: HDD assessment {spec['name']}",
                lambda progress: wf.assess_hdd(folder, line, spec),
                self._hdd_done,
                self._failed,
            )
        else:
            n = len(QgsProject.instance().mapLayersByName("Section lines"))
            dlg = SectionParamsDialog(self.iface.mainWindow(), f"Section {chr(67 + n)}-{chr(67 + n)}'", L)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            name, ve, cor = dlg.name.text().strip() or "Section", dlg.ve.value(), dlg.corridor.value()
            task = FunctionTask(
                f"AGSGeoModel: section {name}",
                lambda progress: wf.make_section(folder, line, name, ve, cor),
                self._section_done,
                self._failed,
            )
        self.runner.start(task)

    def _show(self, viewer: QDialog) -> None:
        self.viewers = [v for v in self.viewers if v.isVisible()] + [viewer]
        viewer.show()

    def _section_done(self, r):
        from .gui.section_tool import DrawingViewer

        self._reload_layer("Section lines")
        sec = r["section"]
        self._show(
            DrawingViewer(
                sec.name,
                r["svg"],
                r["pdf"],
                f"{sec.length:,.0f} m long; holes projected: {', '.join(r['logs']) or 'none'}. "
                "Saved in the project's sections folder.",
                self.iface.mainWindow(),
            )
        )

    def _hdd_done(self, r):
        from .gui.section_tool import DrawingViewer

        a = r["assessment"]
        s = a.summary
        self._reload_layer("HDD stations")
        self._reload_layer("HDD risk zones")
        text = (
            f"Overall rating: {s.get('overall_rag', '?')}. "
            + "; ".join(
                f"{z['hazard'].replace('_', ' ')} {z['rag']} ch {z['from']:.0f}-{z['to']:.0f} m"
                for z in a.risk_zones[:6]
            )
            + (" …" if len(a.risk_zones) > 6 else "")
        )
        self._show(
            DrawingViewer(f"HDD long section {a.alignment.name}", r["svg"], r["pdf"], text, self.iface.mainWindow())
        )

    def _failed(self, exc, trace):
        if exc is not None:
            self.iface.messageBar().pushMessage("AGSGeoModel", f"Failed: {exc}", Qgis.MessageLevel.Critical, 10)

    def _reload_layer(self, name: str) -> None:
        layers = QgsProject.instance().mapLayersByName(name)
        if layers:
            for lyr in layers:
                lyr.dataProvider().reloadData()
                lyr.triggerRepaint()
        elif self.dock is not None:
            self.dock.load_layers()

    # ------------------------------------------------------------------ example and help
    def example_project(self):
        out = QFileDialog.getExistingDirectory(self.iface.mainWindow(), "Folder for the synthetic example project")
        if not out:
            return
        from .examples import example_config
        from .pipeline import run_pipeline

        cfg = example_config(out)
        wf.save_config(cfg, cfg.out_dir)
        dock = self.ensure_dock()

        def done(res):
            dock.project_panel.set_folder(cfg.out_dir)
            dock.open_folder(cfg.out_dir)
            dock._run_done(res)

        task = FunctionTask(
            "AGSGeoModel: synthetic example project", lambda progress: run_pipeline(cfg, progress), done, self._failed
        )
        task.progressChanged.connect(lambda p: dock.progress.setValue(int(p)))
        dock.progress.setVisible(True)
        self.runner.start(task)
        self.iface.messageBar().pushMessage(
            "AGSGeoModel", "Building the synthetic example (NOT REAL DATA)…", Qgis.MessageLevel.Info, 5
        )

    def help(self):
        local = Path(__file__).resolve().parent / "help" / "index.html"
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(local)) if local.exists() else QUrl(DOCS_URL))

    @staticmethod
    def version() -> str:
        return __version__
