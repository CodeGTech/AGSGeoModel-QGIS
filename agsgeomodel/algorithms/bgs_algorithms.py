"""BGS: project-area geology and borehole discovery (requires network access, or a populated cache)."""

from __future__ import annotations

import html
import json
from pathlib import Path

from qgis.core import (
    Qgis,
    QgsProcessingOutputString,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber,
)

from .base import WKB, AGSGeoModelAlgorithm, crs_of, make_fields, write_features

BUFFERS = [100.0, 250.0, 500.0, 1000.0, 5000.0]


class BGSDiscoveryAlgorithm(AGSGeoModelAlgorithm):
    GROUP = ("bgs", "British Geological Survey")
    NAME = "bgs_discovery"
    TITLE = "BGS geology and borehole discovery"
    ICON = "bgs.svg"
    HELP = (
        "Retrieves BGS geology around a project (1:625 000 bedrock, superficial deposits and faults via the BGS "
        "OGC API - labelled BETA by BGS - plus landslide and map-sheet indexes), suggests the stratigraphic "
        "column, and ranks BGS borehole records (Single Onshore Borehole Index and AGS data held by BGS) for "
        "gap filling with an explainable confidence score (proximity, value for filling data gaps, geology, "
        "elevation, age and record quality). Contains British Geological Survey materials © UKRI."
    )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                "PROJECT_IN", self.tr("Project folder"), behavior=Qgis.ProcessingFileParameterBehavior.Folder
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                "BUFFER", self.tr("Search buffer"), [f"{b:,.0f} m" for b in BUFFERS], defaultValue=2
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "THRESHOLD",
                self.tr("Confidence threshold (%)"),
                Qgis.ProcessingNumberParameterType.Double,
                60.0,
                minValue=0.0,
                maxValue=100.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "OUTPUT", self.tr("BGS borehole candidates"), Qgis.ProcessingSourceType.VectorPoint
            )
        )
        self.addParameter(
            QgsProcessingParameterFileDestination(
                "SUMMARY_HTML", self.tr("Area summary"), "HTML files (*.html)", optional=True, createByDefault=False
            )
        )
        self.addOutput(QgsProcessingOutputString("PROVINCE", self.tr("Suggested stratigraphic column")))

    def processAlgorithm(self, parameters, context, feedback):
        from ..workflows import bgs_connector, bgs_discover, load_config, save_bgs

        folder = self.check_folder(self.parameterAsFile(parameters, "PROJECT_IN", context))
        cfg = load_config(folder)
        crs = (cfg.settings or {}).get("crs", "EPSG:27700") if cfg else "EPSG:27700"
        kw = {}
        try:
            from ..gui.settings_dialog import bgs_user_settings

            kw = bgs_user_settings()
        except Exception:  # headless qgis_process without the GUI module
            pass
        disc = bgs_discover(
            folder,
            BUFFERS[self.parameterAsEnum(parameters, "BUFFER", context)],
            self.parameterAsDouble(parameters, "THRESHOLD", context),
            connector=bgs_connector(crs, **kw),
            crs=crs,
            progress=self.progress(feedback),
        )
        save_bgs(folder, disc)
        an = disc.analysis
        feedback.pushInfo(an.setting)
        for n in an.notes:
            feedback.pushWarning(n)
        fields = make_fields(
            [
                ("label", "text"),
                ("bgs_id", "text"),
                ("reference", "text"),
                ("collection", "text"),
                ("distance_m", "real"),
                ("length_m", "real"),
                ("year", "text"),
                ("has_ags", "boolean"),
                ("confidence", "real"),
                ("qualified", "boolean"),
                ("url", "text"),
                ("reasons", "text"),
                ("breakdown", "text"),
            ]
        )
        sink, dest = self.parameterAsSink(parameters, "OUTPUT", context, fields, WKB["Point"], crs_of(crs))
        rows = [
            {
                "geometry": {"type": "Point", "coordinates": [c.x, c.y]},
                "properties": {
                    "label": c.label,
                    "bgs_id": c.bgs_id,
                    "reference": c.reference,
                    "collection": c.collection,
                    "distance_m": c.distance_m,
                    "length_m": None if c.length_m is None else float(c.length_m),
                    "year": c.year,
                    "has_ags": c.has_ags,
                    "confidence": round(c.confidence, 1),
                    "qualified": c.qualified,
                    "url": c.url,
                    "reasons": "; ".join(c.reasons),
                    "breakdown": json.dumps(c.breakdown),
                },
            }
            for c in disc.candidates
        ]
        write_features(sink, fields, rows, feedback)
        q = [c for c in disc.candidates if c.qualified]
        feedback.pushInfo(
            f"{len(disc.candidates)} BGS record(s), {len(q)} at or above the threshold: "
            + ", ".join(f"{c.label} {c.confidence:.0f}%" for c in q[:10])
        )
        out = {"OUTPUT": dest, "PROVINCE": an.suggested_province or ""}
        summary = self.parameterAsFileOutput(parameters, "SUMMARY_HTML", context)
        if summary:
            Path(summary).write_text(
                "<!doctype html><meta charset='utf-8'><title>BGS area summary</title>"
                f"<h1>BGS geology within {an.buffer_m:.0f} m</h1><p>{html.escape(an.setting)}</p>"
                f"<p>Local stratigraphy: {' &gt; '.join(an.local_stratigraphy)}</p>"
                f"<p><small>{html.escape(an.attribution)}</small></p>",
                encoding="utf-8",
            )
            out["SUMMARY_HTML"] = summary
        return out
