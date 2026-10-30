"""Review tab: intervals flagged by the GeoReasoner (inferred missing layers in unlogged / no-recovery
intervals, low-confidence or ambiguous descriptions). The reviewer accepts the proposal or overrides
the unit; decisions are stored with name, reason and time in ``review_decisions`` and applied on the
next run (an acceptance lapses automatically if a later run proposes something different)."""

from __future__ import annotations

from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import workflows as wf

COLUMNS = ["Hole", "From (m)", "To (m)", "Kind", "Proposal", "p", "Alternatives", "Reason", "Description"]


class ReviewPanel(QWidget):
    decided = pyqtSignal()
    zoomTo = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.folder: str | None = None
        self.kb = None
        self.items: list[wf.ReviewItem] = []
        lay = QVBoxLayout(self)
        self.info = QLabel("Run the model to see intervals that need a geologist's review.")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._selection)
        self.table.cellDoubleClicked.connect(lambda r, c: self._zoom())
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.btn_accept = QPushButton("Accept proposal")
        self.btn_accept.setToolTip("Accept the GeoReasoner's interpretation of the selected interval(s)")
        self.btn_accept.clicked.connect(self._accept)
        self.unit = QComboBox()
        self.unit.setMinimumWidth(160)
        self.btn_override = QPushButton("Override with unit")
        self.btn_override.setToolTip("Reject the proposal and assign the chosen unit to the whole interval")
        self.btn_override.clicked.connect(self._override)
        self.btn_zoom = QPushButton("Zoom to hole")
        self.btn_zoom.clicked.connect(self._zoom)
        for w in (self.btn_accept, self.unit, self.btn_override, self.btn_zoom):
            row.addWidget(w)
        row.addStretch(1)
        lay.addLayout(row)
        self.reviewer = "reviewer"
        self._enable(False)

    def _enable(self, on: bool) -> None:
        for w in (self.btn_accept, self.btn_override, self.unit, self.btn_zoom):
            w.setEnabled(on)

    def set_project(self, folder: str | None, kb, reviewer: str) -> None:
        self.folder, self.kb, self.reviewer = folder, kb, reviewer
        self.unit.clear()
        if kb is not None:
            for code, u in kb.units.items():
                self.unit.addItem(f"{u.name} ({code})", code)
        self.refresh()

    def refresh(self) -> None:
        self.items = []
        self.table.setRowCount(0)
        if not self.folder or not wf.gpkg_path(self.folder).exists():
            self.info.setText("Run the model to see intervals that need a geologist's review.")
            return
        try:
            self.items = wf.review_items(self.folder)
        except Exception as exc:  # older project without the review tables
            self.info.setText(f"Review list unavailable: {exc}")
            return
        n_inf = sum(1 for i in self.items if i.kind == "inferred layer")
        self.info.setText(
            f"{len(self.items)} interval(s) to review ({n_inf} inferred layer(s) in unlogged "
            "intervals). Decisions are applied when the model is re-run."
            if self.items
            else "No intervals need review."
        )
        self.table.setRowCount(len(self.items))
        for r, it in enumerate(self.items):

            def name(c):
                return f"{self.kb[c].name} ({c})" if self.kb is not None and c in self.kb else c

            prop = " > ".join(f"{name(u)} {a:.2f}-{b:.2f} m" for u, a, b, _ in it.inferred_runs) or name(it.unit or "")
            alts = ", ".join(f"{a[0]} {a[1]:.2f}" for a in it.alternatives[:3] if isinstance(a, (list, tuple)))
            vals = [
                it.location_id,
                f"{it.top:.2f}",
                f"{it.base:.2f}",
                it.kind,
                prop,
                "" if it.probability is None else f"{it.probability:.2f}",
                alts,
                it.reason,
                it.description,
            ]
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                if c == 3 and it.kind == "inferred layer":
                    cell.setForeground(QColor("#E65100"))
                self.table.setItem(r, c, cell)

    def _selected(self) -> list[wf.ReviewItem]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.items[r] for r in rows if r < len(self.items)]

    def _selection(self) -> None:
        sel = self._selected()
        self._enable(bool(sel))
        if len(sel) == 1 and sel[0].unit:
            k = self.unit.findData(sel[0].unit)
            if k >= 0:
                self.unit.setCurrentIndex(k)

    def _reason(self, title: str) -> str | None:
        text, ok = QInputDialog.getText(self, title, "Reason (recorded with your name and the time):")
        return text.strip() if ok else None

    def _accept(self) -> None:
        sel = self._selected()
        reason = self._reason("Accept interpretation")
        if reason is None or not sel:
            return
        for it in sel:
            wf.decide(self.folder, it, "accepted", self.reviewer, reason)
        self.refresh()
        self.decided.emit()

    def _override(self) -> None:
        sel = self._selected()
        unit = self.unit.currentData()
        reason = self._reason(f"Override with {unit}")
        if reason is None or not sel or not unit:
            return
        for it in sel:
            wf.decide(self.folder, it, "override", self.reviewer, reason, unit)
        self.refresh()
        self.decided.emit()

    def _zoom(self) -> None:
        sel = self._selected()
        if sel:
            self.zoomTo.emit(sel[0].location_id)
