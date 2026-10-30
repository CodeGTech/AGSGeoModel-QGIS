"""AGS data: validation and import/interpretation."""

from __future__ import annotations

import html
from pathlib import Path

from qgis.core import (
    Qgis,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingOutputFile,
    QgsProcessingOutputNumber,
    QgsProcessingOutputString,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterMultipleLayers,
)

from .base import AGSGeoModelAlgorithm


def _provinces() -> list[str]:
    from ..geology.knowledge.kb import available_provinces

    return available_provinces()


class ValidateAGSAlgorithm(AGSGeoModelAlgorithm):
    GROUP = ("ags", "AGS data")
    NAME = "validate_ags"
    TITLE = "Validate AGS file"
    ICON = "validate.svg"
    HELP = (
        "Checks an AGS4 (or AGS3) file against the AGS4 format rules (1-20, with the bundled data dictionary "
        "subset; install python-ags4 for complete rule 7/9 checks) and engineering checks E00-E15 (duplicate "
        "holes, overlapping or gapped strata, depths beyond the final depth, missing coordinates or ground "
        "levels, implausible values, ...). Writes an HTML report and optionally a CSV of all findings."
    )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                "INPUT",
                self.tr("AGS file"),
                behavior=Qgis.ProcessingFileParameterBehavior.File,
                fileFilter="AGS files (*.ags *.AGS);;All files (*.*)",
            )
        )
        self.addParameter(
            QgsProcessingParameterFileDestination("OUTPUT_HTML", self.tr("Validation report"), "HTML files (*.html)")
        )
        self.addParameter(
            QgsProcessingParameterFileDestination(
                "OUTPUT_CSV", self.tr("Findings (CSV)"), "CSV files (*.csv)", optional=True, createByDefault=False
            )
        )
        for k in ("CRITICAL", "ERRORS", "WARNINGS", "NOTES"):
            self.addOutput(QgsProcessingOutputNumber(k, self.tr(k.title())))
        self.addOutput(QgsProcessingOutputString("SUMMARY", self.tr("Summary")))

    def processAlgorithm(self, parameters, context, feedback):
        from ..exports import write_csv
        from ..importers.ags import read_ags, validate_ags

        path = self.parameterAsFile(parameters, "INPUT", context)
        if not path or not Path(path).exists():
            raise QgsProcessingException(f"AGS file not found: {path}")
        ags = read_ags(path)
        rep = validate_ags(ags, base_dir=Path(path).parent)
        c = rep.counts()
        feedback.pushInfo(rep.summary_text())
        out_html = self.parameterAsFileOutput(parameters, "OUTPUT_HTML", context)
        rows = "".join(
            f"<tr class='{i.severity.value}'><td>{i.severity.value}</td><td>{i.rule}</td><td>{i.group or ''}</td>"
            f"<td>{'' if i.line is None else i.line}</td><td>{html.escape(', '.join(i.location_ids))}</td>"
            f"<td>{html.escape(i.message)}</td></tr>"
            for i in rep.issues
        )
        Path(out_html).write_text(
            "<!doctype html><meta charset='utf-8'><title>AGS validation</title><style>body{font-family:sans-serif}"
            "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:3px 6px;font-size:13px}"
            ".critical td:first-child{color:#7B1FA2}.error td:first-child{color:#C62828}"
            ".warning td:first-child{color:#F57F17}</style>"
            f"<h1>AGS validation: {html.escape(Path(path).name)}</h1><p>{html.escape(rep.summary_text())}</p>"
            f"<p>Groups: {', '.join(rep.groups)}</p><table><tr><th>Severity</th><th>Rule</th><th>Group</th>"
            f"<th>Line</th><th>Holes</th><th>Message</th></tr>{rows}</table>",
            encoding="utf-8",
        )
        res = {
            "OUTPUT_HTML": out_html,
            "CRITICAL": c["critical"],
            "ERRORS": c["error"],
            "WARNINGS": c["warning"],
            "NOTES": c["info"],
            "SUMMARY": rep.summary_text(),
        }
        out_csv = self.parameterAsFileOutput(parameters, "OUTPUT_CSV", context)
        if out_csv:
            write_csv(out_csv, [i.to_dict() for i in rep.issues])
            res["OUTPUT_CSV"] = out_csv
        return res


class ImportAGSAlgorithm(AGSGeoModelAlgorithm):
    GROUP = ("ags", "AGS data")
    NAME = "import_ags"
    TITLE = "Import and interpret AGS"
    ICON = "import.svg"
    HELP = (
        "Imports one or more AGS files into an AGSGeoModel project folder (project.gpkg), validates them, "
        "transforms coordinates to the project CRS and interprets the logged strata with the GeoReasoner "
        "(formations, probabilities, evidence, inferred missing layers and geological consistency findings). "
        "No surfaces are modelled: use 'Build geological model' for that."
    )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterMultipleLayers("INPUT", self.tr("AGS files"), Qgis.ProcessingSourceType.File)
        )
        self.addParameter(QgsProcessingParameterCrs("CRS", self.tr("Project CRS"), "EPSG:27700"))
        self.addParameter(
            QgsProcessingParameterCrs(
                "SOURCE_CRS", self.tr("Source CRS override (if detection is wrong)"), optional=True
            )
        )
        self.provinces = _provinces()
        self.addParameter(
            QgsProcessingParameterEnum(
                "PROVINCE",
                self.tr("Stratigraphic column"),
                self.provinces,
                defaultValue=self.provinces.index("london_basin") if "london_basin" in self.provinces else 0,
            )
        )
        self.addParameter(QgsProcessingParameterFolderDestination("PROJECT", self.tr("Project folder")))
        self.addOutput(QgsProcessingOutputFile("GPKG", self.tr("Project GeoPackage")))
        self.addOutput(QgsProcessingOutputNumber("HOLES", self.tr("Exploratory holes")))
        self.addOutput(QgsProcessingOutputNumber("REVIEW", self.tr("Intervals flagged for review")))

    def processAlgorithm(self, parameters, context, feedback):
        from ..core.config import ProjectSettings
        from ..workflows import import_project

        files = [p for p in self.parameterAsFileList(parameters, "INPUT", context) if p]
        if not files:
            raise QgsProcessingException("no AGS files")
        st = ProjectSettings()
        st.crs = self.parameterAsCrs(parameters, "CRS", context).authid() or st.crs
        src = self.parameterAsCrs(parameters, "SOURCE_CRS", context)
        provinces = _provinces()
        st.province = provinces[self.parameterAsEnum(parameters, "PROVINCE", context)]
        folder = self.parameterAsString(parameters, "PROJECT", context)
        r = import_project(folder, files, st, src.authid() if src.isValid() else None, self.progress(feedback))
        n_rev = sum(1 for iv in r["project"].intervals if iv.needs_review)
        for path, rep in r["reports"]:
            feedback.pushInfo(f"{Path(path).name}: {rep.summary_text()}")
        feedback.pushInfo(
            f"{len(r['project'].locations)} holes, {len(r['project'].intervals)} strata, {n_rev} flagged for review"
        )
        gpkg = r["gpkg"]
        for table, name in (
            ("boreholes", "Exploratory holes"),
            ("unit_runs", "Interpreted units (3D)"),
            ("issues", "Validation issues"),
        ):
            context.addLayerToLoadOnCompletion(
                f"{gpkg}|layername={table}", QgsProcessingContext.LayerDetails(name, context.project(), table)
            )
        return {"PROJECT": folder, "GPKG": gpkg, "HOLES": len(r["project"].locations), "REVIEW": n_rev}
