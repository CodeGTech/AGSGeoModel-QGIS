"""Explainable machine learning and correlation: formation-evidence model, hole correlation, domains."""

from __future__ import annotations

import json

from qgis.core import (
    Qgis,
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
)

from .base import WKB, AGSGeoModelAlgorithm, crs_of, make_fields, write_features

BACKENDS = ["auto", "numpy", "sklearn", "lightgbm", "xgboost"]
CORRELATION_MODES = ["rules_constrained", "formation", "hybrid", "ai_assisted", "nearest_neighbour"]


def _project_crs(folder) -> str:
    from ..workflows import load_config

    cfg = load_config(folder)
    return (cfg.settings or {}).get("crs", "EPSG:27700") if cfg else "EPSG:27700"


class CorrelateHolesAlgorithm(AGSGeoModelAlgorithm):
    GROUP = ("ai", "Machine learning")
    NAME = "correlate_holes"
    TITLE = "Correlate exploratory holes"
    ICON = "section.svg"
    HELP = (
        "Correlation links between neighbouring holes of a project (3D lines joining the base of a unit in one "
        "hole to the base of the same, or aligned, unit in the other). Modes: rules_constrained (same interpreted "
        "unit, checked against superposition, apparent-dip limits and mapped faults), formation (same unit), "
        "hybrid (formation where confident, otherwise description similarity and elevation), ai_assisted "
        "(order-preserving alignment of the two logs on description similarity and elevation, with "
        "disagreements flagged) and nearest_neighbour (equal elevations: not geologically constrained, for "
        "comparison only). Each link has a confidence and the flags raised by the checks."
    )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                "PROJECT_IN", self.tr("Project folder"), behavior=Qgis.ProcessingFileParameterBehavior.Folder
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum("MODE", self.tr("Correlation mode"), CORRELATION_MODES, defaultValue=0)
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "OUTPUT", self.tr("Correlation links"), Qgis.ProcessingSourceType.VectorLine
            )
        )
        self.addOutput(QgsProcessingOutputNumber("N_LINKS", self.tr("Links")))
        self.addOutput(QgsProcessingOutputNumber("N_FLAGGED", self.tr("Flagged links")))

    def processAlgorithm(self, parameters, context, feedback):
        from ..core.errors import AGSGeoModelError
        from ..workflows import correlate_holes

        folder = self.check_folder(self.parameterAsFile(parameters, "PROJECT_IN", context))
        mode = CORRELATION_MODES[self.parameterAsEnum(parameters, "MODE", context)]
        try:
            links = correlate_holes(folder, mode)
        except AGSGeoModelError as exc:
            raise QgsProcessingException(str(exc)) from exc
        fields = make_fields(
            [
                ("a", "text"),
                ("b", "text"),
                ("unit_a", "text"),
                ("unit_b", "text"),
                ("mode", "text"),
                ("confidence", "real"),
                ("dip_deg", "real"),
                ("flags", "text"),
            ]
        )
        sink, dest = self.parameterAsSink(
            parameters, "OUTPUT", context, fields, WKB["LineStringZ"], crs_of(_project_crs(folder))
        )
        write_features(sink, fields, [lk.feature() for lk in links], feedback)
        flagged = [lk for lk in links if lk.flags and not lk.flags[0].startswith("equal-elevation")]
        feedback.pushInfo(f"{len(links)} link(s) ({mode}), {len(flagged)} with flags")
        return {"OUTPUT": dest, "N_LINKS": len(links), "N_FLAGGED": len(flagged)}


class ClusterDomainsAlgorithm(AGSGeoModelAlgorithm):
    GROUP = ("ai", "Machine learning")
    NAME = "cluster_domains"
    TITLE = "Cluster holes into geological domains"
    ICON = "ai.svg"
    HELP = (
        "Explainable k-means clustering of the interpreted holes. Each hole is described by interpretable "
        "features (cover thickness by domain, rockhead level, presence of each bedrock unit, mean SPT N in the top "
        "10 m), standardised; k is chosen by the silhouette score. Each cluster is explained by the features that "
        "differ most from the site mean (in original units) and compared with the rule-based engineering-"
        "geological domain of each hole, so disagreements are visible. Clusters are suggestions for review, not "
        "a replacement for the geological domains."
    )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFile(
                "PROJECT_IN", self.tr("Project folder"), behavior=Qgis.ProcessingFileParameterBehavior.Folder
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "K_MAX",
                self.tr("Maximum number of clusters"),
                Qgis.ProcessingNumberParameterType.Integer,
                6,
                minValue=2,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "OUTPUT", self.tr("Domain clusters"), Qgis.ProcessingSourceType.VectorPoint
            )
        )
        self.addOutput(QgsProcessingOutputNumber("K", self.tr("Number of clusters")))
        self.addOutput(QgsProcessingOutputNumber("SILHOUETTE", self.tr("Silhouette score")))
        self.addOutput(QgsProcessingOutputNumber("AGREEMENT", self.tr("Agreement with rule-based domains")))

    def processAlgorithm(self, parameters, context, feedback):
        from ..core.errors import AGSGeoModelError
        from ..workflows import cluster_holes, load_context

        folder = self.check_folder(self.parameterAsFile(parameters, "PROJECT_IN", context))
        ctx = load_context(folder)
        try:
            res = cluster_holes(folder, self.parameterAsInt(parameters, "K_MAX", context), ctx=ctx)
        except AGSGeoModelError as exc:
            raise QgsProcessingException(str(exc)) from exc
        fields = make_fields(
            [
                ("location_id", "text"),
                ("cluster", "integer"),
                ("rule_domain", "text"),
                ("explanation", "text"),
            ]
        )
        sink, dest = self.parameterAsSink(parameters, "OUTPUT", context, fields, WKB["Point"], crs_of(ctx.settings.crs))
        rows = []
        for lid, lab, dom in zip(res.location_ids, res.labels, res.rule_domains):
            loc = ctx.project.locations[lid]
            rows.append(
                {
                    "geometry": {"type": "Point", "coordinates": [loc.x, loc.y]},
                    "properties": {
                        "location_id": lid,
                        "cluster": int(lab),
                        "rule_domain": dom,
                        "explanation": "; ".join(res.explanations.get(int(lab), [])),
                    },
                }
            )
        write_features(sink, fields, rows, feedback)
        for k, ex in res.explanations.items():
            feedback.pushInfo(f"cluster {k}: " + "; ".join(ex))
        feedback.pushInfo(
            f"k = {res.k}, silhouette {res.silhouette:.2f}, agreement with rule-based domains {res.agreement:.0%}"
        )
        return {"OUTPUT": dest, "K": res.k, "SILHOUETTE": res.silhouette, "AGREEMENT": res.agreement}


class TrainFormationModelAlgorithm(AGSGeoModelAlgorithm):
    GROUP = ("ai", "Machine learning")
    NAME = "train_formation_model"
    TITLE = "Train formation model from reviewed projects"
    ICON = "ai.svg"
    HELP = (
        "Trains an explainable formation classifier from the interpreted strata of one or more project "
        "GeoPackages: reviewer overrides plus high-confidence stated/classified intervals. Features are the "
        "parsed BS 5930 description terms plus depth and elevation descriptors. Cross-validated accuracy, a "
        "confusion matrix and a temperature calibration (never sharpening) are written to the model card. The "
        "model only adds evidence: the order-constrained GeoReasoner still decides, so the result can never be "
        "stratigraphically impossible. Use it in Settings > GeoReasoner > Learned formation model."
    )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterMultipleLayers(
                "PROJECTS", self.tr("Project GeoPackages (project.gpkg)"), Qgis.ProcessingSourceType.File
            )
        )
        self.addParameter(QgsProcessingParameterEnum("BACKEND", self.tr("Learner"), BACKENDS, defaultValue=0))
        self.addParameter(
            QgsProcessingParameterNumber(
                "MIN_P",
                self.tr("Minimum probability of unreviewed labels"),
                Qgis.ProcessingNumberParameterType.Double,
                0.9,
                minValue=0.5,
                maxValue=1.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterFileDestination("OUTPUT", self.tr("Formation model"), "Formation model (*.json)")
        )
        self.addOutput(QgsProcessingOutputNumber("CV_ACCURACY", self.tr("Cross-validated accuracy")))
        self.addOutput(QgsProcessingOutputNumber("N_TRAIN", self.tr("Training intervals")))

    def processAlgorithm(self, parameters, context, feedback):
        from ..ai import TrainedFormationModel, training_set
        from ..core.errors import AGSGeoModelError
        from ..database import ProjectStore
        from ..models.project import Project

        paths = [p for p in self.parameterAsFileList(parameters, "PROJECTS", context) if p]
        if not paths:
            raise QgsProcessingException("no project GeoPackages")
        merged = Project("training")
        for k, p in enumerate(paths):
            with ProjectStore(p) as st:
                proj = st.load_project()
            merged.merge(proj, id_prefix=f"P{k}:")
        ivs = training_set(merged, None, self.parameterAsDouble(parameters, "MIN_P", context))
        feedback.pushInfo(f"{len(ivs)} training intervals from {len(paths)} project(s)")
        try:
            mdl = TrainedFormationModel.train(
                ivs, merged, backend=BACKENDS[self.parameterAsEnum(parameters, "BACKEND", context)]
            )
        except AGSGeoModelError as exc:
            raise QgsProcessingException(str(exc)) from exc
        out = self.parameterAsFileOutput(parameters, "OUTPUT", context)
        mdl.save(out)
        feedback.pushInfo(json.dumps({k: v for k, v in mdl.card.items() if k != "confusion"}, default=str)[:2000])
        return {"OUTPUT": out, "CV_ACCURACY": float(mdl.card.get("cv_accuracy", 0.0)), "N_TRAIN": len(ivs)}
