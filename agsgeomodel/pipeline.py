"""End-to-end pipeline shared by the CLI, the Processing algorithms and the plugin GUI.

    import AGS -> validate -> (BGS context) -> GeoReasoner -> model (+ Monte Carlo) -> CPT/SPT ->
    engineering ground model -> hydrogeology -> sections / fence -> HDD -> GeoPackage + exports + report

Every run is described by a :class:`PipelineConfig` (JSON-serialisable) and recorded in the
GeoPackage ``model_runs`` table, so any output can be regenerated from its configuration.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import numpy as np

from .core.config import HDDConfig, ModelConfig, ProjectSettings
from .core.errors import AGSGeoModelError
from .core.issues import IssueLog
from .models.project import Project

Progress = Callable[[float, str], None]


@dataclass
class SectionSpec:
    name: str
    line: list[list[float]]
    vertical_exaggeration: float = 5.0


@dataclass
class HDDSpec:
    alignment_path: str | None = None
    line: list[list[float]] | None = None
    name: str = "HDD"
    entry_angle_deg: float = 12.0
    exit_angle_deg: float = 10.0
    radius_m: float = 250.0
    design_elevation: float | None = None
    product_od_m: float = 0.25
    ream_factor: float = 1.5
    profile: list[list[float]] | None = None  # explicit (chainage, elevation) pairs
    crossings_path: str | None = None


@dataclass
class PipelineConfig:
    ags_paths: list[str]
    out_dir: str
    secondary_ags: list[dict] = field(
        default_factory=list
    )  # BGS AGS etc: {"path", "confidence" %, "per_location", "prefix"}
    settings: dict = field(default_factory=dict)  # ProjectSettings as dict
    crs_override: str | None = None
    dtm_path: str | None = None
    faults_path: str | None = None
    map_paths: list[dict] = field(
        default_factory=list
    )  # {"path":..., "kind": "superficial", "code_field": "code", "scale": 10000, "complete": false}
    sections: list[dict] = field(default_factory=list)  # SectionSpec dicts; empty -> automatic sections
    hdd: dict | None = None  # HDDSpec dict
    learned_model_path: str | None = None
    train_learned_model: bool = False
    run_hydro: bool = True
    run_engineering: bool = True
    run_fence: bool = True
    export_formats: list[str] = field(default_factory=lambda: ["vtk", "gltf", "dxf"])
    report: bool = True
    backup: bool = True
    write_3d_layers: bool = True  # unit-top triangles for the QGIS 3D view

    @classmethod
    def load(cls, path) -> PipelineConfig:
        """Load a configuration; relative paths are resolved against the file's folder, so a project
        folder (or the bundled example) can be moved or shared as a whole."""
        cfg = cls.from_dict(json.loads(Path(path).read_text()))
        cfg.resolve_paths(Path(path).resolve().parent)
        return cfg

    def resolve_paths(self, base: Path) -> PipelineConfig:
        def r(p):
            return p if p in (None, "") or Path(p).is_absolute() else str((base / p).resolve())

        self.ags_paths = [r(p) for p in self.ags_paths]
        self.out_dir = r(self.out_dir)
        self.dtm_path, self.faults_path = r(self.dtm_path), r(self.faults_path)
        self.learned_model_path = r(self.learned_model_path)
        for d in self.map_paths:
            d["path"] = r(d.get("path"))
        for d in self.secondary_ags:
            d["path"] = r(d.get("path"))
        if self.hdd:
            for k in ("alignment_path", "crossings_path"):
                if self.hdd.get(k):
                    self.hdd[k] = r(self.hdd[k])
        prov = (self.settings or {}).get("province")
        if isinstance(prov, str) and Path(prov).suffix.lower() == ".json":  # project-specific column file
            self.settings["province"] = r(prov)
        return self

    @classmethod
    def from_dict(cls, d: dict) -> PipelineConfig:
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(d) - known)
        if unknown:
            raise AGSGeoModelError(f"unknown pipeline configuration key(s): {', '.join(unknown)}")
        return cls(**d)

    def save(self, path) -> Path:
        Path(path).write_text(json.dumps(asdict(self), indent=2))
        return Path(path)


@dataclass
class PipelineResult:
    config: PipelineConfig
    project: Project
    validation: list = field(default_factory=list)
    interpretation: object = None
    model: object = None
    hydro: object = None
    ground_model: object = None
    cpt: list = field(default_factory=list)
    spt: list = field(default_factory=list)
    sections: list = field(default_factory=list)
    fence: list = field(default_factory=list)
    hdd: object = None
    paths: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)
    issues: IssueLog = field(default_factory=IssueLog)
    notes: list[str] = field(default_factory=list)


def auto_sections(project, max_sections: int = 2) -> list[SectionSpec]:
    """Two sections through the borehole cloud: along and across its principal direction."""
    xy = project.xy()
    if len(xy) < 2:
        return []
    c = xy.mean(axis=0)
    u, s, vt = np.linalg.svd(xy - c, full_matrices=False)
    out = []
    for k, name in enumerate(("Section A-A (principal axis)", "Section B-B (cross axis)")[:max_sections]):
        d = vt[k] if k < len(vt) else np.array([-vt[0][1], vt[0][0]])
        proj = (xy - c) @ d
        a, b = c + d * (proj.min() - 20), c + d * (proj.max() + 20)
        if np.hypot(*(b - a)) > 10:
            out.append(SectionSpec(name, [a.tolist(), b.tolist()]))
    return out


def run_pipeline(cfg: PipelineConfig, progress: Progress | None = None) -> PipelineResult:
    from .core.grid import Grid2D  # noqa: F401
    from .database import ProjectStore
    from .geology import GeoReasoner
    from .geology.knowledge import load_column
    from .importers import read_dtm, read_vector, to_alignment, to_faults, to_map_units
    from .importers.ags import import_ags
    from .modelling import ModelInputs, StratigraphicModelBuilder

    def tick(f, msg):
        if progress:
            progress(f, msg)

    t0 = time.time()
    out = Path(cfg.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    st = ProjectSettings.from_dict(cfg.settings) if cfg.settings else ProjectSettings()
    # ------------------------------------------------------------------ import + validation
    tick(0.02, "importing AGS")
    project: Project | None = None
    validation = []
    for k, path in enumerate(cfg.ags_paths):
        imp = import_ags(path, target_crs=st.crs, crs_override=cfg.crs_override)
        validation.append(imp.validation)
        if project is None:
            project = imp.project
        else:
            project.merge(imp.project, id_prefix=f"F{k}:")
    if project is None:
        raise AGSGeoModelError("no AGS files supplied")
    for sec in cfg.secondary_ags:  # secondary data (e.g. BGS AGS): lower weight, never overrides primary
        from .models.enums import DataSource

        imp = import_ags(sec["path"], target_crs=st.crs)
        validation.append(imp.validation)
        per_loc = sec.get("per_location") or {}
        for loc in imp.project.locations.values():  # BGS candidate confidence (%) -> data quality weight
            loc.quality = float(per_loc.get(loc.id, sec.get("confidence", 60.0))) / 100.0
        prefix = sec.get("prefix", "BGS:")
        clash = [l for l in imp.project.locations if f"{prefix}{l}" in project.locations]
        imp.project.remove_locations(clash)
        project.merge(imp.project, id_prefix=prefix, source=DataSource.BGS_AGS)
    project.crs = st.crs
    res = PipelineResult(cfg, project, validation)
    for v in validation:
        res.issues.extend(v.issues)
    res.timings["import"] = time.time() - t0
    store = ProjectStore(out / "project.gpkg", st.crs)
    if cfg.backup and store.runs():
        res.paths["backup"] = str(store.backup())
    n_ovr = store.apply_overrides(project)
    if n_ovr:
        res.notes.append(f"{n_ovr} reviewer decision(s) (overrides / acceptances) applied from the project GeoPackage")
    dtm = read_dtm(cfg.dtm_path) if cfg.dtm_path else None
    if cfg.faults_path:
        project.faults = to_faults(read_vector(cfg.faults_path))
    for mp in cfg.map_paths:
        project.map_units += to_map_units(
            read_vector(mp["path"]),
            mp.get("code_field", "code"),
            mp.get("kind", "superficial"),
            default_scale=mp.get("scale"),
        )
    sup_complete = any(mp.get("complete") for mp in cfg.map_paths)
    # ------------------------------------------------------------------ interpretation
    tick(0.15, "interpreting geology (GeoReasoner)")
    t = time.time()
    column = load_column(st.province)
    learned = None
    if cfg.learned_model_path:
        from .ai import TrainedFormationModel

        learned = TrainedFormationModel.load(cfg.learned_model_path)
    reasoner = GeoReasoner(
        column,
        st.reasoner,
        learned=learned,
        primary_sigma=st.model.primary_sigma_m,
        secondary_sigma=st.model.secondary_sigma_m,
    )
    interp = reasoner.interpret(project)
    if cfg.train_learned_model:
        from .ai import TrainedFormationModel, training_set

        try:
            mdl = TrainedFormationModel.train(training_set(project, interp), project, backend="auto")
            res.paths["learned_model"] = str(mdl.save(out / "formation_model.json"))
            res.notes.append(f"learned formation model trained: CV accuracy {mdl.card['cv_accuracy']}")
        except AGSGeoModelError as exc:
            res.notes.append(f"learned model not trained: {exc}")
    res.interpretation = interp
    res.issues.extend(interp.issues)
    res.timings["interpret"] = time.time() - t
    # ------------------------------------------------------------------ model
    tick(0.35, "building constrained 3D model")
    t = time.time()
    mcfg: ModelConfig = st.model
    builder = StratigraphicModelBuilder(mcfg)
    model = builder.build(
        ModelInputs(
            interp.observations,
            column,
            project.located(),
            dtm=dtm,
            faults=project.faults,
            map_units=project.map_units,
            map_contacts=project.map_contacts,
            superficial_map_complete=sup_complete,
            crs=st.crs,
        )
    )
    if mcfg.monte_carlo_realisations == 0:
        model.simulate(30)
        res.notes.append("30 Monte Carlo realisations generated for probabilities (configure monte_carlo_realisations)")
    res.model = model
    res.timings["model"] = time.time() - t
    store.save_knowledge(column)
    store.save_project(project)
    store.save_interpretation(project, interp, model.run.run_id)
    store.save_issues(list(res.issues), project)  # AGS validation + geological findings
    res.paths["rasters"] = store.save_model(model)
    if cfg.write_3d_layers:
        store.save_model_3d(model)
    # ------------------------------------------------------------------ engineering
    if cfg.run_engineering:
        tick(0.55, "CPT / SPT interpretation and engineering ground model")
        from .processors import build_ground_model, interpret_project_cpts, interpret_spt, material_class

        kb = column.kb
        res.cpt = interpret_project_cpts(project)
        res.spt = interpret_spt(
            project.spt,
            unit_lookup=lambda l, d: interp.locations[l].unit_at(d) if l in interp.locations else None,
            material_lookup=lambda u: material_class(kb, u),
        )
        res.ground_model = build_ground_model(project, interp, model, res.cpt, res.spt)
        store.save_cpt(res.cpt, project)
        store.save_spt(res.spt, project)
        store.save_ground_model(res.ground_model, model.run.run_id)
    if cfg.run_hydro:
        tick(0.65, "hydrogeology")
        from .hydrogeology import HydroGeoEngine

        res.hydro = HydroGeoEngine(column, st.hydro).run(project, interp, model.grid, model, res.ground_model)
        store.save_hydro(res.hydro, model.run.run_id, project)
    # ------------------------------------------------------------------ sections and fence
    tick(0.72, "sections and fence diagram")
    from .visualisation import build_fence, compute_section, render_fence_2d, render_section

    specs = [SectionSpec(**s) for s in cfg.sections] if cfg.sections else auto_sections(project)
    water = {k: (s.grid, s.values) for k, s in (res.hydro.surfaces.items() if res.hydro else [])}
    for sp in specs:
        sec = compute_section(
            model, np.array(sp.line), name=sp.name, project=project, interpretation=interp, water_surfaces=water
        )
        d = render_section(sec, column.kb, vertical_exaggeration=sp.vertical_exaggeration)
        stem = "".join(ch if ch.isalnum() else "_" for ch in sp.name).strip("_")
        svg, pdf = out / f"{stem}.svg", out / f"{stem}.pdf"
        d.save_svg(svg)
        d.save_pdf(pdf)
        store.save_section(sec, model.run.run_id, str(svg), str(pdf))
        res.sections.append((sec, d))
        res.paths[f"section:{sp.name}"] = str(pdf)
    if cfg.run_fence:
        bh = [l.id for l in project.located() if l.id in interp.locations]
        res.fence = build_fence(model, project, interp, bh)
        if res.fence:
            render_fence_2d(res.fence, column.kb).save_svg(out / "fence_diagram.svg")
            store.save_fence(res.fence, model.run.run_id)
            res.paths["fence"] = str(out / "fence_diagram.svg")
    # ------------------------------------------------------------------ HDD
    if cfg.hdd:
        tick(0.82, "HDD ground-risk assessment")
        from .hdd import (
            Alignment,
            HDDDesign,
            assess_alignment,
            design_profile,
            explicit_profile,
            render_hdd_long_section,
        )

        hs = HDDSpec(**cfg.hdd)
        if hs.line:
            line = np.array(hs.line, float)
        else:
            line, _ = to_alignment(read_vector(hs.alignment_path))
        des = HDDDesign(
            hs.entry_angle_deg,
            hs.exit_angle_deg,
            hs.radius_m,
            hs.design_elevation,
            hs.product_od_m,
            hs.ream_factor,
            hs.name,
        )
        from .core.geometry import polyline_length

        L = polyline_length(line)
        zg = model.sample_grid(model.surfaces["ground"].values, line[[0, -1]])
        prof = (
            explicit_profile(*np.array(hs.profile).T)
            if hs.profile
            else design_profile(L, float(zg[0]), float(zg[1]), des)
        )
        crossings = []
        if hs.crossings_path:
            for f in read_vector(hs.crossings_path):
                g = f["geometry"]
                if g and g["type"] == "LineString":
                    crossings.append(
                        (
                            f["properties"].get("name", "crossing"),
                            np.array(g["coordinates"])[:, :2],
                            f["properties"].get("min_clearance_m"),
                        )
                    )
        hcfg: HDDConfig = st.hdd
        res.hdd = assess_alignment(
            Alignment(line, prof, des, hs.name), model, res.hydro, res.ground_model, hcfg, crossings=crossings
        )
        d = render_hdd_long_section(res.hdd, model, project, interp, res.hydro)
        d.save_svg(out / f"{hs.name}_long_section.svg")
        d.save_pdf(out / f"{hs.name}_long_section.pdf")
        res.sections.append((None, d))
        store.save_hdd(res.hdd, model.run.run_id)
        from .exports import write_csv

        write_csv(out / f"{hs.name}_stations.csv", res.hdd.table())
        res.paths["hdd_long_section"] = str(out / f"{hs.name}_long_section.pdf")
    # ------------------------------------------------------------------ exports + report
    if cfg.export_formats:
        tick(0.9, "3D exports")
        from .exports import export_model

        res.paths["exports"] = export_model(model, out / "exports", cfg.export_formats, project, interp)
    if cfg.report:
        tick(0.96, "report")
        from .reports import write_html_report

        res.paths["report"] = str(write_html_report(res, out / "report.html"))
    res.timings["total"] = time.time() - t0
    store.set_meta("last_pipeline_config", asdict(cfg))
    store.close()
    res.paths["geopackage"] = str(out / "project.gpkg")
    tick(1.0, "done")
    return res
