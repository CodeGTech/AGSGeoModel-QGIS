"""Interactive cross sections and HDD alignments: digitise a polyline on the map canvas, then the
section (or HDD long section with RAG strips) is computed from the stored model in a background task
and shown in a viewer; SVG/PDF are saved in the project folder and the trace in project.gpkg."""

from __future__ import annotations

from qgis.core import Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY, QgsProject
from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand
from qgis.PyQt.QtCore import QSize, Qt, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QColor, QDesktopServices
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

try:
    from qgis.PyQt.QtSvg import QSvgWidget
except ImportError:  # Qt6
    from qgis.PyQt.QtSvgWidgets import QSvgWidget  # type: ignore[no-redef]


class PolylineCaptureTool(QgsMapToolEmitPoint):
    """Left click adds a vertex, right click / Enter finishes, Backspace removes, Esc cancels."""

    captured = pyqtSignal(list)

    def __init__(self, canvas, colour: str = "#D32F2F"):
        super().__init__(canvas)
        self.canvas = canvas
        self.rb = QgsRubberBand(canvas, Qgis.GeometryType.Line)
        self.rb.setColor(QColor(colour))
        self.rb.setWidth(2)
        self.points: list[QgsPointXY] = []

    def _update(self, moving: QgsPointXY | None = None) -> None:
        self.rb.reset(Qgis.GeometryType.Line)
        for p in self.points + ([moving] if moving is not None else []):
            self.rb.addPoint(p, False)
        self.rb.show()
        self.rb.updatePosition()
        self.canvas.refresh()

    def canvasPressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.points.append(self.toMapCoordinates(e.pos()))
            self._update()
        elif e.button() == Qt.MouseButton.RightButton:
            self.finish()

    def canvasMoveEvent(self, e):
        if self.points:
            self._update(self.toMapCoordinates(e.pos()))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.reset()
        elif e.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and self.points:
            self.points.pop()
            self._update()
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish()

    def finish(self) -> None:
        pts = list(self.points)
        self.reset()
        if len(pts) >= 2:
            self.captured.emit(pts)

    def reset(self) -> None:
        self.points = []
        self.rb.reset(Qgis.GeometryType.Line)

    def deactivate(self):
        self.reset()
        super().deactivate()


def to_project_crs(points: list[QgsPointXY], canvas, crs: str) -> list[list[float]]:
    tr = QgsCoordinateTransform(
        canvas.mapSettings().destinationCrs(), QgsCoordinateReferenceSystem(crs), QgsProject.instance()
    )
    out = []
    for p in points:
        q = tr.transform(p)
        out.append([q.x(), q.y()])
    return out


class SectionParamsDialog(QDialog):
    def __init__(self, parent=None, name: str = "Section", length: float | None = None):
        super().__init__(parent)
        self.setWindowTitle("Cross section")
        f = QFormLayout(self)
        if length is not None:
            f.addRow(QLabel(f"Length {length:,.0f} m"))
        self.name = QLineEdit(name)
        f.addRow("Name", self.name)
        self.ve = QDoubleSpinBox()
        self.ve.setRange(1, 50)
        self.ve.setValue(5)
        self.ve.setSuffix(" ×")
        f.addRow("Vertical exaggeration", self.ve)
        self.corridor = QDoubleSpinBox()
        self.corridor.setRange(0, 1000)
        self.corridor.setValue(50)
        self.corridor.setSuffix(" m")
        self.corridor.setToolTip("Exploratory holes within this distance are projected onto the section")
        f.addRow("Projection corridor", self.corridor)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)


class HDDParamsDialog(QDialog):
    def __init__(self, parent=None, name: str = "HDD-01", length: float | None = None):
        super().__init__(parent)
        self.setWindowTitle("HDD ground-risk assessment")
        f = QFormLayout(self)
        if length is not None:
            f.addRow(QLabel(f"Plan length {length:,.0f} m"))
        self.name = QLineEdit(name)
        f.addRow("Name", self.name)

        def spin(lo, hi, val, suffix, dec=1):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setDecimals(dec)
            s.setValue(val)
            s.setSuffix(suffix)
            return s

        self.entry = spin(4, 25, 12, " °")
        self.exit = spin(4, 25, 10, " °")
        self.radius = spin(50, 3000, 250, " m", 0)
        self.design = spin(-500, 500, 0, " m OD", 2)
        self.auto = QCheckBox("automatic (minimum cover)")
        self.auto.setChecked(True)
        self.design.setEnabled(False)
        self.auto.toggled.connect(lambda on: self.design.setEnabled(not on))
        self.od = spin(0.02, 3.0, 0.25, " m", 3)
        self.ream = spin(1.0, 3.0, 1.5, " ×", 2)
        f.addRow("Entry angle", self.entry)
        f.addRow("Exit angle", self.exit)
        f.addRow("Bend radius", self.radius)
        row = QHBoxLayout()
        row.addWidget(self.design)
        row.addWidget(self.auto)
        f.addRow("Design level", row)
        f.addRow("Product OD", self.od)
        f.addRow("Ream factor", self.ream)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def spec(self) -> dict:
        return {
            "name": self.name.text().strip() or "HDD-01",
            "entry_angle_deg": self.entry.value(),
            "exit_angle_deg": self.exit.value(),
            "radius_m": self.radius.value(),
            "design_elevation": None if self.auto.isChecked() else self.design.value(),
            "product_od_m": self.od.value(),
            "ream_factor": self.ream.value(),
        }


class DrawingViewer(QDialog):
    """Shows a rendered section / long section (SVG) with links to the saved files."""

    def __init__(self, title: str, svg: str, pdf: str | None = None, text: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        view = QSvgWidget(svg)
        try:
            view.renderer().setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        except AttributeError:  # Qt < 5.15
            pass
        view.setMinimumSize(QSize(900, 420))
        lay.addWidget(view, 1)
        if text:
            info = QLabel(text)
            info.setWordWrap(True)
            lay.addWidget(info)
        row = QHBoxLayout()
        for label, path in (("Open PDF", pdf), ("Open SVG", svg)):
            if path:
                b = QPushButton(label)
                b.clicked.connect(lambda _=False, p=path: QDesktopServices.openUrl(QUrl.fromLocalFile(p)))
                row.addWidget(b)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        ds = view.renderer().defaultSize()
        w = 1200
        h = int(w * ds.height() / ds.width()) if ds.width() > 0 else 620
        self.resize(w, min(h + (110 if text else 70), 900))
