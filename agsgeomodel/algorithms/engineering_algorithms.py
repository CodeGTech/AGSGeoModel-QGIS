"""Engineering: CPT interpretation and HDD ground-risk assessment."""

from __future__ import annotations

import json

import numpy as np
from qgis.core import (
    Qgis,
    QgsProcessingException,
    QgsProcessingOutputFile,
    QgsProcessingOutputString,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFile,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
)

from .base import WKB, AGSGeoModelAlgorithm, crs_of, make_fields, write_features

FOLDER = Qgis.ProcessingFileParameterBehavior.Folder
CPT_COLUMNS = [
    "qc_MPa",
    "qt_MPa",
    "fs_MPa",
    "Rf_pct",
    "u2_MPa",
    "gamma_kNm3",
    "sv0_kPa",
    "u0_kPa",
    "sv0_eff_kPa",
    "Qtn",
    "Fr_pct",
    "Bq",
    "Ic",
    "SBTn",
    "su_kPa",
    "OCR",
    "Dr",
    "phi_deg",
    "E_MPa",
    "M_MPa",
    "mv_m2MN",
    "Vs_ms",
    "G0_MPa",
    "k_ms",
]


class CPTInterpretationAlgorithm(AGSGeoModelAlgorithm):
    GROUP = ("engineering", "Engineering and HDD")
    NAME = "cpt_interpretation"
    TITLE = "Interpret CPT soundings"
    ICON = "cpt.svg"
    HELP = (
        "Interprets the CPT soundings in an AGS file (SCPG/SCPT) with Robertson (2009/2010) methods: "
        "normalised cone resistance Qtn with iterative stress exponent, soil behaviour type index Ic and SBTn "
        "zone, undrained shear strength (Nkt), OCR, relative density, friction angle, constrained and Young's "
        "moduli (coarse soils only), shear-wave velocity, small-strain stiffness and permeability. Every "
        "correlation and its parameters are recorded with the results."
    )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                "INPUT",
                self.tr("AGS file with CPT data"),
                behavior=Qgis.ProcessingFileParameterBehavior.File,
                fileFilter="AGS files (*.ags *.AGS)",
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "NKT",
                self.tr("Cone factor Nkt"),
                Qgis.ProcessingNumberParameterType.Double,
                14.0,
                minValue=5.0,
                maxValue=30.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "WATER_DEPTH",
                self.tr("Groundwater depth (m, blank = from AGS SCPG_WAT or dry)"),
                Qgis.ProcessingNumberParameterType.Double,
                optional=True,
                minValue=0.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "OUTPUT", self.tr("CPT parameters"), Qgis.ProcessingSourceType.VectorPoint
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        from ..importers.ags import import_ags
        from ..processors import CPTSettings, interpret_cpt

        path = self.parameterAsFile(parameters, "INPUT", context)
        imp = import_ags(path)
        project = imp.project
        if not project.cpt:
            raise QgsProcessingException("the AGS file has no CPT data (SCPG / SCPT groups)")
        st = CPTSettings(nkt=self.parameterAsDouble(parameters, "NKT", context))
        wd = parameters.get("WATER_DEPTH")
        water = None if wd in (None, "") else self.parameterAsDouble(parameters, "WATER_DEPTH", context)
        spec = [("location_id", "text"), ("test_id", "text"), ("depth", "real")] + [
            (c, "integer" if c == "SBTn" else "real") for c in CPT_COLUMNS
        ]
        fields = make_fields(spec)
        crs = project.crs or "EPSG:27700"
        sink, dest = self.parameterAsSink(parameters, "OUTPUT", context, fields, WKB["PointZ"], crs_of(crs))
        n = 0
        for k, c in enumerate(project.cpt):
            loc = project.locations.get(c.location_id)
            r = interpret_cpt(
                c.depth,
                c.qc,
                c.fs,
                c.u2,
                c.qt,
                c.location_id,
                c.test_id,
                st,
                c.area_ratio,
                water if water is not None else c.water_depth,
            )
            for note in r.notes:
                feedback.pushInfo(f"{c.location_id}: {note}")
            rows = []
            for i, d in enumerate(r.depth):
                props = {"location_id": r.location_id, "test_id": r.test_id, "depth": float(d)}
                for col in CPT_COLUMNS:
                    if col in r.columns:
                        v = r.columns[col][i]
                        props[col] = (
                            int(v) if col == "SBTn" and np.isfinite(v) else (float(v) if np.isfinite(v) else None)
                        )
                geom = None
                if loc is not None and np.isfinite(loc.x) and loc.ground_level is not None:
                    geom = {"type": "Point", "coordinates": [loc.x, loc.y, loc.ground_level - float(d)]}
                rows.append({"geometry": geom, "properties": props})
            n += write_features(sink, fields, rows, feedback)
            feedback.setProgress(100.0 * (k + 1) / len(project.cpt))
        feedback.pushInfo(f"{len(project.cpt)} sounding(s), {n} readings interpreted")
        return {"OUTPUT": dest}


class HDDAssessmentAlgorithm(AGSGeoModelAlgorithm):
    GROUP = ("engineering", "Engineering and HDD")
    NAME = "hdd_assessment"
    TITLE = "HDD ground-risk assessment"
    ICON = "hdd.svg"
    HELP = (
        "Designs (entry/exit tangents and bends) or follows a given bore profile along an alignment and "
        "assesses the ground at every station against the latest model: geology and contacts, rockhead, "
        "groundwater heads, formation changes and RAG ratings for running sand, groundwater, boulders, rock, "
        "mixed face and loss of circulation (Delft cavity-expansion frac-out screening, hydrostatic unless "
        "annular pressures are supplied). Writes stations, risk zones, a long section (PDF/SVG) and a CSV. "
        "The model confidence is reported for every station."
    )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile("PROJECT_IN", self.tr("Project folder"), behavior=FOLDER))
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                "ALIGNMENT", self.tr("Alignment (first or selected line)"), [Qgis.ProcessingSourceType.VectorLine]
            )
        )
        self.addParameter(QgsProcessingParameterString("NAME", self.tr("Name"), "HDD-01"))
        self.addParameter(
            QgsProcessingParameterNumber(
                "ENTRY",
                self.tr("Entry angle (°)"),
                Qgis.ProcessingNumberParameterType.Double,
                12.0,
                minValue=4.0,
                maxValue=25.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "EXIT",
                self.tr("Exit angle (°)"),
                Qgis.ProcessingNumberParameterType.Double,
                10.0,
                minValue=4.0,
                maxValue=25.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "RADIUS", self.tr("Bend radius (m)"), Qgis.ProcessingNumberParameterType.Double, 250.0, minValue=50.0
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "DESIGN_LEVEL",
                self.tr("Design level (m OD, blank = automatic)"),
                Qgis.ProcessingNumberParameterType.Double,
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "OD",
                self.tr("Product outside diameter (m)"),
                Qgis.ProcessingNumberParameterType.Double,
                0.25,
                minValue=0.02,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "REAM",
                self.tr("Ream factor"),
                Qgis.ProcessingNumberParameterType.Double,
                1.5,
                minValue=1.0,
                maxValue=3.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                "CROSSINGS",
                self.tr("Crossed assets (name, min_clearance_m)"),
                [Qgis.ProcessingSourceType.VectorLine],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "STATIONS", self.tr("HDD stations"), Qgis.ProcessingSourceType.VectorPoint
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink("ZONES", self.tr("HDD risk zones"), Qgis.ProcessingSourceType.VectorLine)
        )
        self.addOutput(QgsProcessingOutputFile("LONG_SECTION", self.tr("Long section (PDF)")))
        self.addOutput(QgsProcessingOutputString("OVERALL_RAG", self.tr("Overall rating")))

    def processAlgorithm(self, parameters, context, feedback):
        from ..core.geometry import point_at_chainage
        from ..gui.qgis_bridge import features_from, lines_from
        from ..pipeline import HDDSpec
        from ..workflows import assess_hdd, load_context

        folder = self.check_folder(self.parameterAsFile(parameters, "PROJECT_IN", context))
        ctx = load_context(folder, use_cache=False)
        crs = ctx.settings.crs
        lines = lines_from(
            self.parameterAsSource(parameters, "ALIGNMENT", context), crs, context=context.transformContext()
        )
        if not lines:
            raise QgsProcessingException("no alignment line")
        if len(lines) > 1:
            feedback.pushWarning(
                f"{len(lines)} lines in the alignment layer: the first is used (select one feature to choose another)"
            )
        dl = parameters.get("DESIGN_LEVEL")
        spec = HDDSpec(
            name=self.parameterAsString(parameters, "NAME", context) or "HDD-01",
            entry_angle_deg=self.parameterAsDouble(parameters, "ENTRY", context),
            exit_angle_deg=self.parameterAsDouble(parameters, "EXIT", context),
            radius_m=self.parameterAsDouble(parameters, "RADIUS", context),
            design_elevation=None if dl in (None, "") else self.parameterAsDouble(parameters, "DESIGN_LEVEL", context),
            product_od_m=self.parameterAsDouble(parameters, "OD", context),
            ream_factor=self.parameterAsDouble(parameters, "REAM", context),
        )
        crossings = []
        csrc = self.parameterAsSource(parameters, "CROSSINGS", context)
        if csrc is not None:
            for f in features_from(csrc, crs, context=context.transformContext()):
                g = f["geometry"]
                if g and g["type"] == "LineString":
                    crossings.append(
                        (
                            str(f["properties"].get("name") or "crossing"),
                            np.asarray(g["coordinates"], float)[:, :2],
                            f["properties"].get("min_clearance_m"),
                        )
                    )
        r = assess_hdd(folder, lines[0][1], spec, crossings, ctx=ctx)
        a = r["assessment"]
        for n in a.notes:
            feedback.pushInfo(n)
        sfields = make_fields(
            [
                ("alignment", "text"),
                ("chainage", "real"),
                ("unit", "text"),
                ("cover", "real"),
                ("p_unit", "real"),
                ("rockhead", "real"),
                ("pressure_head", "real"),
                ("rag_overall", "text"),
                ("rag_running_sand", "text"),
                ("rag_groundwater", "text"),
                ("rag_boulders", "text"),
                ("rag_rock", "text"),
                ("rag_mixed_face", "text"),
                ("rag_loss_of_circulation", "text"),
                ("confidence_rag", "text"),
                ("frac_fos", "real"),
                ("reasons", "text"),
            ]
        )
        sink, sdest = self.parameterAsSink(parameters, "STATIONS", context, sfields, WKB["PointZ"], crs_of(crs))
        rows = []
        for s in a.stations:
            d = s.to_dict()
            rows.append(
                {
                    "geometry": {"type": "Point", "coordinates": [s.x, s.y, s.z]},
                    "properties": {**d, "alignment": spec.name, "reasons": json.dumps(s.reasons)},
                }
            )
        write_features(sink, sfields, rows, feedback)
        zfields = make_fields(
            [
                ("alignment", "text"),
                ("hazard", "text"),
                ("rag", "text"),
                ("ch_from", "real"),
                ("ch_to", "real"),
                ("reasons", "text"),
            ]
        )
        zsink, zdest = self.parameterAsSink(parameters, "ZONES", context, zfields, WKB["LineString"], crs_of(crs))
        zrows = []
        for z in a.risk_zones:
            ch = np.linspace(z["from"], max(z["to"], z["from"] + 0.5), 5)
            xy = point_at_chainage(a.alignment.line, ch)
            zrows.append(
                {
                    "geometry": {"type": "LineString", "coordinates": xy.tolist()},
                    "properties": {
                        "alignment": spec.name,
                        "hazard": z["hazard"],
                        "rag": z["rag"],
                        "ch_from": z["from"],
                        "ch_to": z["to"],
                        "reasons": json.dumps(z["reasons"]),
                    },
                }
            )
        write_features(zsink, zfields, zrows, feedback)
        overall = a.summary.get("overall_rag", "")
        feedback.pushInfo(f"overall rating {overall}; long section {r['pdf']}")
        return {"STATIONS": sdest, "ZONES": zdest, "LONG_SECTION": r["pdf"], "OVERALL_RAG": overall}
