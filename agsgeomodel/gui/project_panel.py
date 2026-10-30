"""Project tab of the dock: project folder, AGS inputs, context layers, HDD alignment and outputs.

The panel edits a :class:`~agsgeomodel.pipeline.PipelineConfig`. QGIS layers chosen as inputs are
exported to ``<project>/inputs`` (project CRS) when a run starts, so the stored configuration always
points at files and any run can be repeated outside QGIS (``python -m agsgeomodel run <folder>``).
"""

from __future__ import annotations

from pathlib import Path

from qgis.core import Qgis, QgsCoordinateReferenceSystem, QgsMapLayerProxyModel, QgsProject
from qgis.gui import (
    QgsCollapsibleGroupBox,
    QgsDoubleSpinBox,
    QgsFieldComboBox,
    QgsFileWidget,
    QgsMapLayerComboBox,
    QgsProjectionSelectionWidget,
)
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.config import ProjectSettings
from ..exports import MODEL_FORMATS
from ..geology.knowledge.kb import available_provinces
from ..pipeline import PipelineConfig
from . import qgis_bridge as qb

FORMAT_LABELS = {
    "vtk": "VTK (ParaView / PyVista)",
    "vtk_voxels": "VTK voxel block model",
    "obj": "OBJ",
    "ply": "PLY",
    "gltf": "glTF binary (.glb)",
    "dxf": "DXF (CAD)",
    "ifc": "IFC4 (BIM)",
}


def _layer_filter(kind: str):
    names = {"raster": "RasterLayer", "line": "LineLayer", "polygon": "PolygonLayer"}
    try:
        return getattr(Qgis.LayerFilter, names[kind])
    except AttributeError:  # QGIS < 3.34
        return getattr(QgsMapLayerProxyModel, names[kind])


def _layer_combo(kind: str) -> QgsMapLayerComboBox:
    cb = QgsMapLayerComboBox()
    cb.setFilters(_layer_filter(kind))
    cb.setAllowEmptyLayer(True, "(none)")
    cb.setLayer(None)
    return cb


class ProjectPanel(QWidget):
    runRequested = pyqtSignal()
    loadRequested = pyqtSignal()
    folderChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._building = False
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.folder = QgsFileWidget()
        self.folder.setStorageMode(QgsFileWidget.StorageMode.GetDirectory)
        self.folder.setDialogTitle("AGSGeoModel project folder")
        self.folder.fileChanged.connect(self._folder_changed)
        form.addRow("Project folder", self.folder)
        self.name = QLineEdit("AGSGeoModel project")
        form.addRow("Name", self.name)
        self.province = QComboBox()
        for p in available_provinces():
            self.province.addItem(p.replace("_", " ").title(), p)
        self.province.setToolTip(
            "Stratigraphic column (province) used by the GeoReasoner. The BGS dialog "
            "suggests one from the mapped geology."
        )
        form.addRow("Stratigraphy", self.province)
        self.crs = QgsProjectionSelectionWidget()
        self.crs.setCrs(QgsCoordinateReferenceSystem("EPSG:27700"))
        form.addRow("Project CRS", self.crs)
        lay.addLayout(form)

        # ---- AGS inputs
        lay.addWidget(QLabel("<b>AGS data</b> (primary site investigation)"))
        self.ags = QListWidget()
        self.ags.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.ags.setMaximumHeight(90)
        lay.addWidget(self.ags)
        row = QHBoxLayout()
        self.btn_add = QPushButton("Add AGS…")
        self.btn_add.clicked.connect(self._add_ags)
        self.btn_remove = QPushButton("Remove")
        self.btn_remove.clicked.connect(self._remove_ags)
        self.btn_validate = QPushButton("Validate…")
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_remove)
        row.addWidget(self.btn_validate)
        lay.addLayout(row)
        self.secondary = QLabel("")
        self.secondary.setWordWrap(True)
        lay.addWidget(self.secondary)
        self._secondary: list[dict] = []
        self.crs_override: str | None = None  # source CRS confirmed in the import wizard

        # ---- context data
        ctx = QgsCollapsibleGroupBox("Context data (optional)")
        g = QGridLayout(ctx)
        self.dtm = _layer_combo("raster")
        self.faults = _layer_combo("line")
        self.sup_map = _layer_combo("polygon")
        self.sup_field = QgsFieldComboBox()
        self.sup_field.setAllowEmptyFieldName(True)
        self.sup_map.layerChanged.connect(self.sup_field.setLayer)
        self.sup_complete = QCheckBox("complete")
        self.sup_complete.setToolTip(
            "Tick if the superficial map is complete: outside mapped polygons the "
            "superficial deposits are taken as absent (BGS convention)."
        )
        self.bed_map = _layer_combo("polygon")
        self.bed_field = QgsFieldComboBox()
        self.bed_field.setAllowEmptyFieldName(True)
        self.bed_map.layerChanged.connect(self.bed_field.setLayer)
        g.addWidget(QLabel("Ground model (DTM)"), 0, 0)
        g.addWidget(self.dtm, 0, 1, 1, 2)
        g.addWidget(QLabel("Faults"), 1, 0)
        g.addWidget(self.faults, 1, 1, 1, 2)
        g.addWidget(QLabel("Superficial map"), 2, 0)
        g.addWidget(self.sup_map, 2, 1)
        g.addWidget(self.sup_field, 2, 2)
        g.addWidget(self.sup_complete, 3, 2)
        g.addWidget(QLabel("Bedrock map"), 4, 0)
        g.addWidget(self.bed_map, 4, 1)
        g.addWidget(self.bed_field, 4, 2)
        hint = QLabel(
            "Map code fields hold BGS Lexicon codes (e.g. LC, ALV). Fault fields: name, throw_m, "
            "downthrown_side, dip_deg."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")
        g.addWidget(hint, 5, 0, 1, 3)
        ctx.setCollapsed(True)
        lay.addWidget(ctx)

        # ---- HDD
        hdd = QgsCollapsibleGroupBox("HDD / linear infrastructure (optional)")
        hf = QFormLayout(hdd)
        self.hdd_line = _layer_combo("line")
        hf.addRow("Alignment", self.hdd_line)
        self.hdd_name = QLineEdit("HDD-01")
        hf.addRow("Name", self.hdd_name)
        self.entry = QgsDoubleSpinBox()
        self.entry.setRange(4, 25)
        self.entry.setValue(12)
        self.entry.setSuffix(" °")
        self.exit = QgsDoubleSpinBox()
        self.exit.setRange(4, 25)
        self.exit.setValue(10)
        self.exit.setSuffix(" °")
        ang = QHBoxLayout()
        ang.addWidget(QLabel("entry"))
        ang.addWidget(self.entry)
        ang.addWidget(QLabel("exit"))
        ang.addWidget(self.exit)
        hf.addRow("Angles", ang)
        self.radius = QgsDoubleSpinBox()
        self.radius.setRange(50, 3000)
        self.radius.setValue(250)
        self.radius.setSuffix(" m")
        hf.addRow("Bend radius", self.radius)
        self.auto_depth = QCheckBox("automatic (minimum cover)")
        self.auto_depth.setChecked(True)
        self.design_z = QgsDoubleSpinBox()
        self.design_z.setRange(-500, 500)
        self.design_z.setDecimals(2)
        self.design_z.setSuffix(" m OD")
        self.design_z.setEnabled(False)
        self.auto_depth.toggled.connect(lambda on: self.design_z.setEnabled(not on))
        dz = QHBoxLayout()
        dz.addWidget(self.design_z)
        dz.addWidget(self.auto_depth)
        hf.addRow("Design level", dz)
        self.od = QgsDoubleSpinBox()
        self.od.setRange(0.02, 3.0)
        self.od.setDecimals(3)
        self.od.setValue(0.25)
        self.od.setSuffix(" m")
        hf.addRow("Product OD", self.od)
        self.crossings = _layer_combo("line")
        self.crossings.setToolTip("Line layer of crossed assets (fields: name, min_clearance_m)")
        hf.addRow("Crossings", self.crossings)
        hdd.setCollapsed(True)
        lay.addWidget(hdd)

        # ---- outputs
        out = QgsCollapsibleGroupBox("Modelling and outputs")
        of = QFormLayout(out)
        self.cell = QgsDoubleSpinBox()
        self.cell.setRange(0, 1000)
        self.cell.setDecimals(1)
        self.cell.setSpecialValueText("automatic")
        self.cell.setSuffix(" m")
        of.addRow("Cell size", self.cell)
        self.buffer = QgsDoubleSpinBox()
        self.buffer.setRange(0, 10000)
        self.buffer.setValue(250)
        self.buffer.setSuffix(" m")
        of.addRow("Model buffer", self.buffer)
        self.mc = QSpinBox()
        self.mc.setRange(0, 1000)
        self.mc.setValue(40)
        self.mc.setToolTip("Monte Carlo realisations (probability of units, exceedance)")
        of.addRow("Realisations", self.mc)
        self.chk_eng = QCheckBox("CPT / SPT and engineering ground model")
        self.chk_hydro = QCheckBox("Hydrogeology")
        self.chk_fence = QCheckBox("Fence diagram")
        self.chk_report = QCheckBox("HTML report")
        for c in (self.chk_eng, self.chk_hydro, self.chk_fence, self.chk_report):
            c.setChecked(True)
            of.addRow(c)
        self.formats = QListWidget()
        for f in MODEL_FORMATS:
            it = QListWidgetItem(FORMAT_LABELS.get(f, f))
            it.setData(Qt.ItemDataRole.UserRole, f)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if f in ("vtk", "gltf", "dxf") else Qt.CheckState.Unchecked)
            self.formats.addItem(it)
        self.formats.setMaximumHeight(110)
        of.addRow("3D exports", self.formats)
        out.setCollapsed(True)
        lay.addWidget(out)

        row = QHBoxLayout()
        self.btn_run = QPushButton("Run model")
        self.btn_run.setDefault(True)
        self.btn_run.setStyleSheet("font-weight: bold;")
        self.btn_run.clicked.connect(self.runRequested.emit)
        self.btn_load = QPushButton("Load layers")
        self.btn_load.clicked.connect(self.loadRequested.emit)
        row.addWidget(self.btn_run)
        row.addWidget(self.btn_load)
        lay.addLayout(row)
        lay.addStretch(1)

    # ------------------------------------------------------------------ AGS list
    def _add_ags(self) -> None:
        start = qb.user_setting("last_ags_dir", str(Path.home()))
        files, _ = QFileDialog.getOpenFileNames(self, "Add AGS files", start, "AGS files (*.ags *.AGS);;All files (*)")
        for f in files:
            if not self.ags.findItems(f, Qt.MatchFlag.MatchExactly):
                self.ags.addItem(f)
        if files:
            qb.set_user_setting("last_ags_dir", str(Path(files[0]).parent))

    def _remove_ags(self) -> None:
        for it in self.ags.selectedItems():
            self.ags.takeItem(self.ags.row(it))

    def ags_files(self) -> list[str]:
        return [self.ags.item(i).text() for i in range(self.ags.count())]

    def selected_ags(self) -> str | None:
        it = self.ags.currentItem()
        return it.text() if it else (self.ags.item(0).text() if self.ags.count() else None)

    def add_ags(self, paths: list[str]) -> None:
        for f in paths:
            if not self.ags.findItems(f, Qt.MatchFlag.MatchExactly):
                self.ags.addItem(f)

    # ------------------------------------------------------------------ folder
    def project_folder(self) -> str | None:
        f = self.folder.filePath().strip()
        return f or None

    def set_folder(self, folder: str) -> None:
        self.folder.setFilePath(folder)

    def _folder_changed(self, folder: str) -> None:
        if not self._building:
            self.folderChanged.emit(folder)

    # ------------------------------------------------------------------ config <-> widgets
    def settings(self, base: ProjectSettings | None = None) -> ProjectSettings:
        st = base or ProjectSettings()
        st.name = self.name.text().strip() or st.name
        st.province = self.province.currentData() or st.province
        st.crs = self.crs.crs().authid() or st.crs
        st.model.cell_size_m = self.cell.value() or None
        st.model.buffer_m = self.buffer.value()
        st.model.monte_carlo_realisations = self.mc.value()
        return st

    def export_formats(self) -> list[str]:
        return [
            self.formats.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.formats.count())
            if self.formats.item(i).checkState() == Qt.CheckState.Checked
        ]

    def build_config(
        self, folder: str, base: PipelineConfig | None = None, settings: ProjectSettings | None = None
    ) -> PipelineConfig:
        """Pipeline configuration from the widgets; input layers are exported to ``folder/inputs``."""
        from ..exports import write_geojson

        if settings is None and base is not None and base.settings:
            settings = ProjectSettings.from_dict(base.settings)  # keep the settings-dialog values
        st = self.settings(settings)
        crs = st.crs
        inputs = Path(folder) / "inputs"
        inputs.mkdir(parents=True, exist_ok=True)

        def export(layer, name: str) -> str | None:
            if layer is None:
                return None
            p = inputs / f"{name}.geojson"
            write_geojson(p, qb.features_from(layer, crs), crs, name)
            return str(p)

        cfg = base or PipelineConfig(ags_paths=[], out_dir=folder)
        cfg.ags_paths = self.ags_files()
        cfg.out_dir = folder
        cfg.settings = st.to_dict()
        cfg.secondary_ags = list(self._secondary)
        cfg.crs_override = self.crs_override
        cfg.dtm_path = (
            qb.export_raster(self.dtm.currentLayer(), inputs / "dtm.tif", crs) if self.dtm.currentLayer() else None
        )
        cfg.faults_path = export(self.faults.currentLayer(), "faults")
        cfg.map_paths = []
        if self.sup_map.currentLayer() is not None and self.sup_field.currentField():
            cfg.map_paths.append(
                {
                    "path": export(self.sup_map.currentLayer(), "superficial_map"),
                    "kind": "superficial",
                    "code_field": self.sup_field.currentField(),
                    "complete": self.sup_complete.isChecked(),
                }
            )
        if self.bed_map.currentLayer() is not None and self.bed_field.currentField():
            cfg.map_paths.append(
                {
                    "path": export(self.bed_map.currentLayer(), "bedrock_map"),
                    "kind": "bedrock",
                    "code_field": self.bed_field.currentField(),
                }
            )
        cfg.hdd = None
        if self.hdd_line.currentLayer() is not None:
            cfg.hdd = {
                "alignment_path": export(self.hdd_line.currentLayer(), "hdd_alignment"),
                "name": self.hdd_name.text().strip() or "HDD-01",
                "entry_angle_deg": self.entry.value(),
                "exit_angle_deg": self.exit.value(),
                "radius_m": self.radius.value(),
                "design_elevation": None if self.auto_depth.isChecked() else self.design_z.value(),
                "product_od_m": self.od.value(),
                "crossings_path": export(self.crossings.currentLayer(), "crossings"),
            }
        cfg.run_engineering = self.chk_eng.isChecked()
        cfg.run_hydro = self.chk_hydro.isChecked()
        cfg.run_fence = self.chk_fence.isChecked()
        cfg.report = self.chk_report.isChecked()
        cfg.export_formats = self.export_formats()
        # remember the chosen layers with the QGIS project
        for key, cb in (
            ("dtm", self.dtm),
            ("faults", self.faults),
            ("sup_map", self.sup_map),
            ("bed_map", self.bed_map),
            ("hdd_line", self.hdd_line),
            ("crossings", self.crossings),
        ):
            qb.set_project_setting(f"layer_{key}", cb.currentLayer().id() if cb.currentLayer() else "")
        return cfg

    def load_config(self, cfg: PipelineConfig | None) -> None:
        self._building = True
        try:
            st = ProjectSettings.from_dict(cfg.settings) if cfg and cfg.settings else ProjectSettings()
            self.name.setText(st.name)
            i = self.province.findData(st.province)
            if i < 0 and st.province:  # project-specific column file: keep it selectable
                self.province.addItem(f"Project column ({Path(st.province).name})", st.province)
                i = self.province.count() - 1
            if i >= 0:
                self.province.setCurrentIndex(i)
            self.crs.setCrs(QgsCoordinateReferenceSystem(st.crs))
            self.cell.setValue(st.model.cell_size_m or 0)
            self.buffer.setValue(st.model.buffer_m)
            self.mc.setValue(st.model.monte_carlo_realisations)
            self.ags.clear()
            if cfg:
                self.add_ags(cfg.ags_paths)
                self._secondary = list(cfg.secondary_ags)
                self.crs_override = cfg.crs_override
                self.chk_eng.setChecked(cfg.run_engineering)
                self.chk_hydro.setChecked(cfg.run_hydro)
                self.chk_fence.setChecked(cfg.run_fence)
                self.chk_report.setChecked(cfg.report)
                for k in range(self.formats.count()):
                    it = self.formats.item(k)
                    it.setCheckState(
                        Qt.CheckState.Checked
                        if it.data(Qt.ItemDataRole.UserRole) in cfg.export_formats
                        else Qt.CheckState.Unchecked
                    )
                if cfg.hdd:
                    self.hdd_name.setText(cfg.hdd.get("name", "HDD-01"))
                    self.entry.setValue(cfg.hdd.get("entry_angle_deg", 12.0))
                    self.exit.setValue(cfg.hdd.get("exit_angle_deg", 10.0))
                    self.radius.setValue(cfg.hdd.get("radius_m", 250.0))
                    de = cfg.hdd.get("design_elevation")
                    self.auto_depth.setChecked(de is None)
                    if de is not None:
                        self.design_z.setValue(de)
                    self.od.setValue(cfg.hdd.get("product_od_m", 0.25))
            else:
                self._secondary = []
                self.crs_override = None
            self._update_secondary()
            proj = QgsProject.instance()
            for key, cb in (
                ("dtm", self.dtm),
                ("faults", self.faults),
                ("sup_map", self.sup_map),
                ("bed_map", self.bed_map),
                ("hdd_line", self.hdd_line),
                ("crossings", self.crossings),
            ):
                lid = qb.project_setting(f"layer_{key}", "")
                cb.setLayer(proj.mapLayer(lid) if lid else None)
        finally:
            self._building = False

    def set_secondary(self, items: list[dict]) -> None:
        self._secondary = list(items)
        self._update_secondary()

    def _update_secondary(self) -> None:
        n = len(self._secondary)
        self.secondary.setText(f"{n} BGS AGS file(s) merged as secondary data (weighted by confidence)" if n else "")
