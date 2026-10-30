"""Geological modelling: full pipeline, re-run with reviewer decisions, sections and exports."""

from __future__ import annotations

from pathlib import Path

from qgis.core import (
    Qgis,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingOutputFile,
    QgsProcessingOutputFolder,
    QgsProcessingOutputString,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterFile,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
)

from .base import WKB, AGSGeoModelAlgorithm, crs_of, make_fields, write_features

FOLDER = Qgis.ProcessingFileParameterBehavior.Folder


def _provinces() -> list[str]:
    from ..geology.knowledge.kb import available_provinces

    return available_provinces()


def _formats() -> list[str]:
    from ..exports import MODEL_FORMATS

    return list(MODEL_FORMATS)


def _export_source(source, path: Path, crs: str, context) -> str:
    from ..exports import write_geojson
    from ..gui.qgis_bridge import features_from

    write_geojson(path, features_from(source, crs, context=context.transformContext()), crs, path.stem)
    return str(path)


def _report_outputs(res, context) -> dict:
    gpkg = res.paths.get("geopackage")
    for table, name in (
        ("boreholes", "Exploratory holes"),
        ("unit_runs", "Interpreted units (3D)"),
        ("uncertainty_zones", "Uncertainty zones"),
        ("issues", "Validation issues"),
    ):
        context.addLayerToLoadOnCompletion(
            f"{gpkg}|layername={table}", QgsProcessingContext.LayerDetails(name, context.project(), table)
        )
    return {
        "PROJECT": res.config.out_dir,
        "GPKG": gpkg,
        "REPORT": res.paths.get("report"),
        "RUN_ID": res.model.run.run_id if res.model else "",
    }


class BuildModelAlgorithm(AGSGeoModelAlgorithm):
    NAME = "build_model"
    TITLE = "Build geological model"
    ICON = "run.svg"
    HELP = (
        "Complete AGSGeoModel workflow: AGS import and validation, GeoReasoner interpretation (stratigraphic "
        "order, superposition, unconformities, missing layers), constrained surface modelling (kriging with "
        "inequality constraints, fault barriers, map constraints, uncertainty classes Observed / Interpolated "
        "/ Inferred / Speculative), Monte Carlo realisations, CPT/SPT interpretation, engineering ground model, "
        "hydrogeology, sections, fence diagram, 3D exports and an HTML report. Every run is recorded in "
        "project.gpkg (model_runs) with its inputs hash and configuration."
    )

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterMultipleLayers("INPUT", self.tr("AGS files"), Qgis.ProcessingSourceType.File)
        )
        self.addParameter(QgsProcessingParameterCrs("CRS", self.tr("Project CRS"), "EPSG:27700"))
        provinces = _provinces()
        self.addParameter(
            QgsProcessingParameterEnum(
                "PROVINCE",
                self.tr("Stratigraphic column"),
                provinces,
                defaultValue=provinces.index("london_basin") if "london_basin" in provinces else 0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "CELL",
                self.tr("Cell size (m, 0 = automatic)"),
                Qgis.ProcessingNumberParameterType.Double,
                0.0,
                minValue=0.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "BUFFER",
                self.tr("Model buffer around the holes (m)"),
                Qgis.ProcessingNumberParameterType.Double,
                250.0,
                minValue=0.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "REALISATIONS",
                self.tr("Monte Carlo realisations"),
                Qgis.ProcessingNumberParameterType.Integer,
                40,
                minValue=0,
                maxValue=1000,
            )
        )
        self.addParameter(QgsProcessingParameterRasterLayer("DTM", self.tr("Ground model (DTM)"), optional=True))
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                "FAULTS",
                self.tr("Faults (name, throw_m, downthrown_side, dip_deg)"),
                [Qgis.ProcessingSourceType.VectorLine],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                "SUP_MAP", self.tr("Superficial geology map"), [Qgis.ProcessingSourceType.VectorPolygon], optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                "SUP_FIELD",
                self.tr("Superficial map code field (Lexicon code)"),
                parentLayerParameterName="SUP_MAP",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                "SUP_COMPLETE", self.tr("Superficial map is complete (absent outside polygons)"), False
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                "BED_MAP", self.tr("Bedrock geology map"), [Qgis.ProcessingSourceType.VectorPolygon], optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                "BED_FIELD",
                self.tr("Bedrock map code field (Lexicon code)"),
                parentLayerParameterName="BED_MAP",
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean("ENGINEERING", self.tr("CPT/SPT and engineering ground model"), True)
        )
        self.addParameter(QgsProcessingParameterBoolean("HYDRO", self.tr("Hydrogeology"), True))
        self.addParameter(QgsProcessingParameterBoolean("FENCE", self.tr("Fence diagram"), True))
        self.addParameter(
            QgsProcessingParameterEnum(
                "FORMATS", self.tr("3D exports"), _formats(), allowMultiple=True, defaultValue=[0, 4], optional=True
            )
        )
        self.addParameter(QgsProcessingParameterBoolean("REPORT_HTML", self.tr("HTML report"), True))
        self.addParameter(QgsProcessingParameterFolderDestination("PROJECT", self.tr("Project folder")))
        self.addOutput(QgsProcessingOutputFile("GPKG", self.tr("Project GeoPackage")))
        self.addOutput(QgsProcessingOutputFile("REPORT", self.tr("Report")))
        self.addOutput(QgsProcessingOutputString("RUN_ID", self.tr("Run id")))

    def processAlgorithm(self, parameters, context, feedback):
        from ..core.config import ProjectSettings
        from ..pipeline import PipelineConfig, run_pipeline
        from ..workflows import save_config

        files = [p for p in self.parameterAsFileList(parameters, "INPUT", context) if p]
        if not files:
            raise QgsProcessingException("no AGS files")
        folder = Path(self.parameterAsString(parameters, "PROJECT", context))
        inputs = folder / "inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        st = ProjectSettings()
        st.crs = self.parameterAsCrs(parameters, "CRS", context).authid() or st.crs
        st.province = _provinces()[self.parameterAsEnum(parameters, "PROVINCE", context)]
        st.model.cell_size_m = self.parameterAsDouble(parameters, "CELL", context) or None
        st.model.buffer_m = self.parameterAsDouble(parameters, "BUFFER", context)
        st.model.monte_carlo_realisations = self.parameterAsInt(parameters, "REALISATIONS", context)
        cfg = PipelineConfig(ags_paths=files, out_dir=str(folder), settings=st.to_dict())
        dtm = self.parameterAsRasterLayer(parameters, "DTM", context)
        if dtm is not None:
            from ..gui.qgis_bridge import export_raster

            cfg.dtm_path = export_raster(dtm, inputs / "dtm.tif", st.crs)
        faults = self.parameterAsSource(parameters, "FAULTS", context)
        if faults is not None:
            cfg.faults_path = _export_source(faults, inputs / "faults.geojson", st.crs, context)
        for key, kind in (("SUP", "superficial"), ("BED", "bedrock")):
            src = self.parameterAsSource(parameters, f"{key}_MAP", context)
            fld = self.parameterAsString(parameters, f"{key}_FIELD", context)
            if src is not None:
                if not fld:
                    raise QgsProcessingException(f"choose the Lexicon code field of the {kind} map")
                cfg.map_paths.append(
                    {
                        "path": _export_source(src, inputs / f"{kind}_map.geojson", st.crs, context),
                        "kind": kind,
                        "code_field": fld,
                        "complete": kind == "superficial" and self.parameterAsBool(parameters, "SUP_COMPLETE", context),
                    }
                )
        cfg.run_engineering = self.parameterAsBool(parameters, "ENGINEERING", context)
        cfg.run_hydro = self.parameterAsBool(parameters, "HYDRO", context)
        cfg.run_fence = self.parameterAsBool(parameters, "FENCE", context)
        cfg.report = self.parameterAsBool(parameters, "REPORT_HTML", context)
        fmts = _formats()
        cfg.export_formats = [fmts[i] for i in self.parameterAsEnums(parameters, "FORMATS", context)]
        save_config(cfg, folder)
        res = run_pipeline(cfg, self.progress(feedback))
        for n in res.notes:
            feedback.pushInfo(n)
        c = res.issues.counts()
        feedback.pushInfo(f"findings: {c['critical']} critical, {c['error']} errors, {c['warning']} warnings")
        return _report_outputs(res, context)


class RebuildModelAlgorithm(AGSGeoModelAlgorithm):
    NAME = "rebuild_model"
    TITLE = "Rebuild model (apply reviewer decisions)"
    ICON = "run.svg"
    HELP = (
        "Re-runs the stored configuration of a project folder. Reviewer overrides edited in the attribute form "
        "(lithology.override_unit) and decisions recorded in the review panel are applied first; acceptances "
        "lapse automatically if the new interpretation differs from what was accepted."
    )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile("PROJECT_IN", self.tr("Project folder"), behavior=FOLDER))
        self.addOutput(QgsProcessingOutputFolder("PROJECT", self.tr("Project folder")))
        self.addOutput(QgsProcessingOutputFile("GPKG", self.tr("Project GeoPackage")))
        self.addOutput(QgsProcessingOutputFile("REPORT", self.tr("Report")))
        self.addOutput(QgsProcessingOutputString("RUN_ID", self.tr("Run id")))

    def processAlgorithm(self, parameters, context, feedback):
        from ..workflows import run_project

        folder = self.parameterAsFile(parameters, "PROJECT_IN", context)
        res = run_project(folder, self.progress(feedback))
        for n in res.notes:
            feedback.pushInfo(n)
        return _report_outputs(res, context)


class CrossSectionsAlgorithm(AGSGeoModelAlgorithm):
    NAME = "cross_sections"
    TITLE = "Cross sections from lines"
    ICON = "section.svg"
    HELP = (
        "Draws a geological cross section along every line of the input layer from the latest model of a "
        "project: unit polygons, contacts styled by uncertainty class, faults, groundwater, and exploratory "
        "holes within the projection corridor. Writes SVG and PDF files to <project>/sections and the "
        "sections as vertical 3D polygons (for the QGIS 3D view)."
    )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile("PROJECT_IN", self.tr("Project folder"), behavior=FOLDER))
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                "LINES", self.tr("Section lines"), [Qgis.ProcessingSourceType.VectorLine]
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                "NAME_FIELD", self.tr("Name field"), parentLayerParameterName="LINES", optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "VE",
                self.tr("Vertical exaggeration"),
                Qgis.ProcessingNumberParameterType.Double,
                5.0,
                minValue=1.0,
                maxValue=50.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "CORRIDOR",
                self.tr("Projection corridor (m)"),
                Qgis.ProcessingNumberParameterType.Double,
                50.0,
                minValue=0.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "OUTPUT", self.tr("Sections (3D panels)"), Qgis.ProcessingSourceType.VectorPolygon
            )
        )
        self.addOutput(QgsProcessingOutputFolder("FOLDER", self.tr("Section drawings")))
        self.addOutput(QgsProcessingOutputString("FILES", self.tr("PDF files")))

    def processAlgorithm(self, parameters, context, feedback):
        from ..gui.qgis_bridge import lines_from
        from ..workflows import load_context, make_section, section_3d_features

        folder = self.check_folder(self.parameterAsFile(parameters, "PROJECT_IN", context))
        ctx = load_context(folder, use_cache=False)
        src = self.parameterAsSource(parameters, "LINES", context)
        lines = lines_from(
            src,
            ctx.settings.crs,
            name_field=self.parameterAsString(parameters, "NAME_FIELD", context) or None,
            context=context.transformContext(),
        )
        if not lines:
            raise QgsProcessingException("no line features")
        fields = make_fields([("section", "text"), ("unit", "text"), ("run_id", "text")])
        sink, dest = self.parameterAsSink(
            parameters, "OUTPUT", context, fields, WKB["PolygonZ"], crs_of(ctx.settings.crs)
        )
        ve = self.parameterAsDouble(parameters, "VE", context)
        cor = self.parameterAsDouble(parameters, "CORRIDOR", context)
        pdfs = []
        for k, (name, line) in enumerate(lines):
            if feedback.isCanceled():
                break
            r = make_section(folder, line, name, ve, cor, ctx=ctx)
            pdfs.append(r["pdf"])
            write_features(sink, fields, section_3d_features(r["section"], ctx.run_id))
            feedback.pushInfo(f"{name}: {r['pdf']} ({len(r['logs'])} holes projected)")
            feedback.setProgress(100.0 * (k + 1) / len(lines))
        return {"OUTPUT": dest, "FOLDER": str(Path(folder) / "sections"), "FILES": ";".join(pdfs)}


class ExportModelAlgorithm(AGSGeoModelAlgorithm):
    NAME = "export_model"
    TITLE = "Export model and tables"
    ICON = "export.svg"
    HELP = (
        "Exports the latest model of a project to 3D formats (VTK, VTK voxels, OBJ, PLY, glTF, DXF, IFC4 with "
        "georeferencing) and the project tables (holes, strata, interpreted units, findings, HDD stations, "
        "ground model) to GeoJSON, Shapefile, CSV and an Excel workbook. OBJ/PLY/glTF/IFC coordinates are "
        "relative to the local origin recorded in export_manifest.json."
    )

    TABLE_FORMATS = ["geojson", "shp", "csv", "xlsx"]

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile("PROJECT_IN", self.tr("Project folder"), behavior=FOLDER))
        self.addParameter(
            QgsProcessingParameterEnum(
                "FORMATS", self.tr("3D formats"), _formats(), allowMultiple=True, defaultValue=[0, 4, 5], optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                "TABLES",
                self.tr("Table formats"),
                self.TABLE_FORMATS,
                allowMultiple=True,
                defaultValue=[0, 2],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "VE",
                self.tr("Vertical exaggeration"),
                Qgis.ProcessingNumberParameterType.Double,
                1.0,
                minValue=0.1,
                maxValue=50.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                "STRIDE",
                self.tr("Grid stride (1 = full resolution)"),
                Qgis.ProcessingNumberParameterType.Integer,
                1,
                minValue=1,
                maxValue=20,
            )
        )
        self.addParameter(QgsProcessingParameterFolderDestination("OUTPUT", self.tr("Output folder")))

    def processAlgorithm(self, parameters, context, feedback):
        from ..exports import export_model
        from ..workflows import export_tables, load_context

        folder = self.check_folder(self.parameterAsFile(parameters, "PROJECT_IN", context))
        out = Path(self.parameterAsString(parameters, "OUTPUT", context))
        ctx = load_context(folder, use_cache=False)
        fmts = _formats()
        chosen = [fmts[i] for i in self.parameterAsEnums(parameters, "FORMATS", context)]
        res = {}
        if chosen:
            feedback.pushInfo("writing 3D formats: " + ", ".join(chosen))
            res.update(
                export_model(
                    ctx.model,
                    out / "3d",
                    chosen,
                    ctx.project,
                    ctx.interpretation,
                    self.parameterAsDouble(parameters, "VE", context),
                    stride=self.parameterAsInt(parameters, "STRIDE", context),
                )
            )
        feedback.setProgress(70)
        tables = [self.TABLE_FORMATS[i] for i in self.parameterAsEnums(parameters, "TABLES", context)]
        if tables:
            res.update(export_tables(folder, out / "tables", tables))
        for k, v in res.items():
            feedback.pushInfo(f"{k}: {v}")
        return {"OUTPUT": str(out)}
