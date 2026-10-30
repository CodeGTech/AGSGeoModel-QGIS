"""AGS import wizard: files -> validation -> coordinates and datum -> summary.

Validation never blocks an import: every finding is shown with its AGS rule (AGS4 rules 1-20) or
engineering check (E00-E15) and is written to the project's ``issues`` table on the next run. The
source CRS detected from LOCA (and the vertical datum) must be confirmed or overridden here.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from qgis.core import QgsCoordinateReferenceSystem
from qgis.gui import QgsProjectionSelectionWidget
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from ..importers.ags import ags_to_project, read_ags, validate_ags
from . import qgis_bridge as qb

SEV_HEX = {"critical": "#7B1FA2", "error": "#C62828", "warning": "#F57F17", "info": "#607D8B"}


class _FilesPage(QWizardPage):
    def __init__(self, wizard):
        super().__init__()
        self.w = wizard
        self.setTitle("AGS files")
        self.setSubTitle("Choose AGS4 (or AGS3) files to validate and import. AGS3 is converted to AGS4.")
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        add = QPushButton("Add…")
        add.clicked.connect(self._add)
        rem = QPushButton("Remove")
        rem.clicked.connect(
            lambda: (
                [self.list.takeItem(self.list.row(i)) for i in self.list.selectedItems()] or self.completeChanged.emit()
            )
        )
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        lay.addLayout(row)

    def _add(self):
        start = qb.user_setting("last_ags_dir", str(Path.home()))
        files, _ = QFileDialog.getOpenFileNames(self, "AGS files", start, "AGS files (*.ags *.AGS);;All files (*)")
        for f in files:
            if not self.list.findItems(f, Qt.MatchFlag.MatchExactly):
                self.list.addItem(f)
        if files:
            qb.set_user_setting("last_ags_dir", str(Path(files[0]).parent))
        self.completeChanged.emit()

    def files(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def isComplete(self) -> bool:
        return self.list.count() > 0


class _ValidationPage(QWizardPage):
    def __init__(self, wizard):
        super().__init__()
        self.w = wizard
        self.setTitle("Validation")
        self.setSubTitle("AGS format rules and engineering checks. Findings do not block the import.")
        lay = QVBoxLayout(self)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["File", "Severity", "Rule", "Group", "Line", "Holes", "Message"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table)
        self.show_info = QCheckBox("Show notes (info)")
        self.show_info.toggled.connect(self._fill)
        lay.addWidget(self.show_info)
        save = QPushButton("Save report (CSV)…")
        save.clicked.connect(self._save)
        lay.addWidget(save, 0, Qt.AlignmentFlag.AlignLeft)

    def initializePage(self):
        self.w.parsed = {}
        texts = []
        for f in self.w.files_page.files():
            try:
                ags = read_ags(f)
                rep = validate_ags(ags, base_dir=Path(f).parent)
                self.w.parsed[f] = (ags, rep)
                texts.append(
                    f"<b>{Path(f).name}</b>: {rep.summary_text()}"
                    + (
                        ""
                        if rep.usable_for_modelling
                        else " - <span style='color:#C62828'>not usable for "
                        "modelling until critical findings are fixed</span>"
                    )
                )
            except Exception as exc:
                texts.append(f"<b>{Path(f).name}</b>: <span style='color:#C62828'>could not be read: {exc}</span>")
        self.summary.setText("<br>".join(texts))
        self._fill()

    def _rows(self):
        out = []
        for f, (_, rep) in self.w.parsed.items():
            for i in rep.issues:
                if i.severity.value == "info" and not self.show_info.isChecked():
                    continue
                out.append(
                    (
                        Path(f).name,
                        i.severity.value,
                        i.rule,
                        i.group or "",
                        "" if i.line is None else str(i.line),
                        ", ".join(i.location_ids[:4]) + ("…" if len(i.location_ids) > 4 else ""),
                        i.message,
                    )
                )
        return out

    def _fill(self):
        rows = self._rows()
        self.table.setRowCount(len(rows))
        for r, vals in enumerate(rows):
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 1:
                    it.setForeground(QColor(SEV_HEX.get(v, "#000000")))
                self.table.setItem(r, c, it)

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Validation report", "ags_validation.csv", "CSV (*.csv)")
        if not path:
            return
        from ..exports import write_csv

        write_csv(path, [{"file": f, **i.to_dict()} for f, (_, rep) in self.w.parsed.items() for i in rep.issues])

    def isComplete(self) -> bool:
        return bool(self.w.parsed)


class _CRSPage(QWizardPage):
    def __init__(self, wizard):
        super().__init__()
        self.w = wizard
        self.setTitle("Coordinates and datum")
        self.setSubTitle("Confirm the coordinate reference system detected from LOCA, or choose another.")
        lay = QVBoxLayout(self)
        self.info = QTextBrowser()
        lay.addWidget(self.info)
        row = QHBoxLayout()
        row.addWidget(QLabel("Source CRS"))
        self.crs = QgsProjectionSelectionWidget()
        row.addWidget(self.crs, 1)
        lay.addLayout(row)
        self.confirm = QCheckBox("I have checked the coordinate system and vertical datum")
        self.confirm.toggled.connect(self.completeChanged.emit)
        lay.addWidget(self.confirm)

    def initializePage(self):
        html, best = [], None
        self.w.imports = {}
        for f, (ags, _) in self.w.parsed.items():
            res = ags_to_project(ags, target_crs=self.w.target_crs)
            self.w.imports[f] = res
            g = res.crs
            best = best or g
            alts = ", ".join(f"EPSG:{a}" for a in g.alternatives) or "none"
            html.append(
                f"<b>{Path(f).name}</b><br>Detected: {g.authid or 'unknown'} (confidence {g.confidence:.0%}) "
                f"- {g.basis}<br>Alternatives: {alts}<br>Vertical datum: {res.datum_basis}<br>"
                f"Transform to project CRS: {res.transform_note}<br>"
                f"Located holes: {len(res.project.located())} of {len(res.project.locations)}<br>"
            )
        self.info.setHtml("<br>".join(html))
        if best is not None and best.authid:
            self.crs.setCrs(QgsCoordinateReferenceSystem(best.authid))
        self.confirm.setChecked(best is not None and not best.needs_confirmation)

    def override(self) -> str | None:
        chosen = self.crs.crs().authid()
        detected = {r.crs.authid for r in self.w.imports.values()}
        return chosen if chosen and chosen not in detected else None

    def isComplete(self) -> bool:
        return self.confirm.isChecked()


class _SummaryPage(QWizardPage):
    def __init__(self, wizard):
        super().__init__()
        self.w = wizard
        self.setTitle("Summary")
        self.setSubTitle("Data that will be added to the project. Run the model to interpret it.")
        lay = QVBoxLayout(self)
        self.text = QTextBrowser()
        lay.addWidget(self.text)

    def initializePage(self):
        html = []
        for f, (ags, rep) in self.w.parsed.items():
            groups = ", ".join(f"{g} ({len(ags.tables[g].rows)})" for g in ags.tables)
            res = self.w.imports.get(f)
            kinds = Counter(l.kind.value for l in res.project.locations.values()) if res else {}
            html.append(
                f"<b>{Path(f).name}</b> - {rep.format} {rep.ags_version}<br>Groups: {groups}<br>"
                f"Holes: {', '.join(f'{k} {v}' for k, v in kinds.items())}"
            )
        ovr = self.w.crs_page.override()
        if ovr:
            html.append(f"<br><b>CRS override:</b> {ovr}")
        self.text.setHtml("<br><br>".join(html))


class ImportWizard(QWizard):
    """Returns the files to add (``files``) and an optional CRS override (``crs_override``)."""

    def __init__(self, parent=None, files: list[str] | None = None, target_crs: str = "EPSG:27700"):
        super().__init__(parent)
        self.setWindowTitle("AGSGeoModel - import AGS")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.target_crs = target_crs
        self.parsed: dict = {}
        self.imports: dict = {}
        self.files_page = _FilesPage(self)
        self.validation_page = _ValidationPage(self)
        self.crs_page = _CRSPage(self)
        self.summary_page = _SummaryPage(self)
        for p in (self.files_page, self.validation_page, self.crs_page, self.summary_page):
            self.addPage(p)
        for f in files or []:
            self.files_page.list.addItem(f)
        self.resize(900, 600)

    @property
    def files(self) -> list[str]:
        return [f for f, (_, rep) in self.parsed.items()]

    @property
    def crs_override(self) -> str | None:
        return self.crs_page.override()
