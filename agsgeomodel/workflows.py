"""Project-level operations shared by the QGIS GUI, the Processing algorithms and the CLI.

A *project folder* holds ``project.gpkg`` (+ ``rasters/``), the pipeline configuration
``agsgeomodel_project.json`` and the documents generated from it (sections, long sections,
report). Everything here is headless (no QGIS imports) so it is unit-tested without QGIS and
behaves identically in the GUI, in Processing models and on the command line.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .core.config import ProjectSettings
from .core.errors import AGSGeoModelError, DependencyMissing, StoreError
from .pipeline import HDDSpec, PipelineConfig, PipelineResult, run_pipeline

PROJECT_CONFIG = "agsgeomodel_project.json"
GPKG_NAME = "project.gpkg"


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_") or "item"


# ---------------------------------------------------------------------------- configuration
def config_path(folder) -> Path:
    return Path(folder) / PROJECT_CONFIG


def gpkg_path(folder) -> Path:
    return Path(folder) / GPKG_NAME


def load_config(folder) -> PipelineConfig | None:
    p = config_path(folder)
    if not p.exists():
        return None
    cfg = PipelineConfig.load(p)
    cfg.out_dir = str(Path(folder))
    return cfg


def save_config(cfg: PipelineConfig, folder=None) -> Path:
    folder = Path(folder or cfg.out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    cfg.out_dir = str(folder)
    return cfg.save(config_path(folder))


def new_config(folder, ags_paths: list[str], settings: ProjectSettings | None = None, **kw) -> PipelineConfig:
    st = settings or ProjectSettings()
    return PipelineConfig(ags_paths=list(ags_paths), out_dir=str(folder), settings=st.to_dict(), **kw)


def run_project(folder, progress=None, **overrides) -> PipelineResult:
    """Run (or re-run) the pipeline of a project folder; reviewer decisions stored in the project
    GeoPackage are applied automatically."""
    cfg = load_config(folder)
    if cfg is None:
        raise AGSGeoModelError(f"no {PROJECT_CONFIG} in {folder}: create a project first")
    for k, v in overrides.items():
        setattr(cfg, k, v)
    save_config(cfg, folder)
    return run_pipeline(cfg, progress)


# ---------------------------------------------------------------------------- stored context
@dataclass
class ProjectContext:
    """Everything needed to draw sections / assess alignments from a stored project."""

    folder: Path
    settings: ProjectSettings
    project: object
    interpretation: object
    model: object
    hydro: object = None
    ground_model: object = None
    run_id: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def kb(self):
        return self.model.column.kb

    @property
    def water(self) -> dict:
        return {k: (s.grid, s.values) for k, s in (self.hydro.surfaces.items() if self.hydro else [])}


_CACHE: dict[tuple, ProjectContext] = {}


def load_context(folder, run_id: str | None = None, use_cache: bool = True) -> ProjectContext:
    """Load a stored project (latest model run by default). Cached per file modification time."""
    from .database import ProjectStore

    folder = Path(folder)
    gp = gpkg_path(folder)
    if not gp.exists():
        raise StoreError(f"{gp} not found: run the model first")
    key = (str(gp.resolve()), run_id, gp.stat().st_mtime_ns)
    if use_cache and key in _CACHE:
        return _CACHE[key]
    cfg = load_config(folder)
    st = ProjectSettings.from_dict(cfg.settings) if cfg and cfg.settings else ProjectSettings()
    with ProjectStore(gp, st.crs) as store:
        model = store.load_model(run_id)
        rid = model.run.run_id
        ctx = ProjectContext(
            folder,
            st,
            store.load_project(),
            store.load_interpretation(),
            model,
            store.load_hydro(model.column, rid),
            store.load_ground_model(rid),
            rid,
        )
    ctx.interpretation.column = model.column
    if ctx.ground_model is None:
        ctx.notes.append("no engineering ground model stored: HDD frac-out checks use assumed parameters")
    _CACHE.clear()
    _CACHE[key] = ctx
    return ctx


def clear_cache() -> None:
    _CACHE.clear()


# ---------------------------------------------------------------------------- sections
def make_section(
    folder,
    line,
    name: str = "Section",
    vertical_exaggeration: float = 5.0,
    corridor: float = 50.0,
    ctx: ProjectContext | None = None,
    save: bool = True,
) -> dict:
    """Cross section along ``line`` (project CRS) from the stored model: SVG + PDF + GeoPackage rows."""
    from .database import ProjectStore
    from .visualisation import compute_section, render_section

    ctx = ctx or load_context(folder)
    line = np.asarray(line, float)[:, :2]
    if len(line) < 2:
        raise AGSGeoModelError("a section needs at least two vertices")
    sec = compute_section(
        ctx.model,
        line,
        name=name,
        project=ctx.project,
        interpretation=ctx.interpretation,
        corridor=corridor,
        water_surfaces=ctx.water,
    )
    d = render_section(sec, ctx.kb, vertical_exaggeration=vertical_exaggeration)
    out = Path(folder) / "sections"
    out.mkdir(parents=True, exist_ok=True)
    stem = safe_name(name)
    svg, pdf = out / f"{stem}.svg", out / f"{stem}.pdf"
    d.save_svg(svg)
    d.save_pdf(pdf)
    if save:
        with ProjectStore(gpkg_path(folder), ctx.settings.crs) as store:
            store.save_section(sec, ctx.run_id, str(svg), str(pdf))
    return {"svg": str(svg), "pdf": str(pdf), "section": sec, "drawing": d, "logs": [l.location_id for l in sec.logs]}


# ---------------------------------------------------------------------------- HDD
def assess_hdd(
    folder,
    line,
    spec: HDDSpec | dict | None = None,
    crossings=None,
    ctx: ProjectContext | None = None,
    save: bool = True,
) -> dict:
    """HDD ground-risk assessment of an alignment (project CRS) on the stored model."""
    from .core.geometry import polyline_length
    from .database import ProjectStore
    from .exports import write_csv
    from .hdd import Alignment, HDDDesign, assess_alignment, design_profile, explicit_profile, render_hdd_long_section

    ctx = ctx or load_context(folder)
    hs = spec if isinstance(spec, HDDSpec) else HDDSpec(**(spec or {}))
    line = np.asarray(line, float)[:, :2]
    if len(line) < 2:
        raise AGSGeoModelError("an alignment needs at least two vertices")
    des = HDDDesign(
        hs.entry_angle_deg,
        hs.exit_angle_deg,
        hs.radius_m,
        hs.design_elevation,
        hs.product_od_m,
        hs.ream_factor,
        hs.name,
    )
    L = polyline_length(line)
    zg = ctx.model.sample_grid(ctx.model.surfaces["ground"].values, line[[0, -1]])
    if not np.all(np.isfinite(zg)):
        raise AGSGeoModelError("alignment end points lie outside the model extent")
    prof = (
        explicit_profile(*np.array(hs.profile).T) if hs.profile else design_profile(L, float(zg[0]), float(zg[1]), des)
    )
    a = assess_alignment(
        Alignment(line, prof, des, hs.name),
        ctx.model,
        ctx.hydro,
        ctx.ground_model,
        ctx.settings.hdd,
        crossings=crossings or [],
    )
    a.notes.extend(ctx.notes)
    d = render_hdd_long_section(a, ctx.model, ctx.project, ctx.interpretation, ctx.hydro)
    out = Path(folder) / "hdd"
    out.mkdir(parents=True, exist_ok=True)
    stem = safe_name(hs.name)
    paths = {
        "svg": out / f"{stem}_long_section.svg",
        "pdf": out / f"{stem}_long_section.pdf",
        "csv": out / f"{stem}_stations.csv",
        "json": out / f"{stem}_summary.json",
    }
    d.save_svg(paths["svg"])
    d.save_pdf(paths["pdf"])
    write_csv(paths["csv"], a.table())
    paths["json"].write_text(
        json.dumps(
            {
                "summary": a.summary,
                "risk_zones": a.risk_zones,
                "formation_changes": a.formation_changes,
                "constraints": a.constraints,
                "notes": a.notes,
                "design": asdict(hs),
                "run_id": ctx.run_id,
            },
            indent=1,
            default=float,
        )
    )
    if save:
        with ProjectStore(gpkg_path(folder), ctx.settings.crs) as store:
            store.save_hdd(a, ctx.run_id)
    return {"assessment": a, **{k: str(v) for k, v in paths.items()}}


# ---------------------------------------------------------------------------- review
@dataclass
class ReviewItem:
    location_id: str
    top: float
    base: float
    description: str
    unit: str | None
    probability: float | None
    alternatives: list
    reason: str
    proposal: str
    inferred_runs: list = field(default_factory=list)  # [(unit, top, base, p)] inside the interval
    override_unit: str | None = None
    status: str = ""

    @property
    def kind(self) -> str:
        """``inferred layer`` for unlogged / no-recovery intervals filled from the stratigraphic order
        (missing-layer inference), ``low confidence`` for ambiguous descriptions."""
        return "inferred layer" if (len(self.inferred_runs) > 1 or self.status == "inferred") else "low confidence"


def review_items(folder) -> list[ReviewItem]:
    """Intervals flagged for review, with the proposal (unit sequence) a decision refers to."""
    from .database import ProjectStore
    from .geology import proposal_key

    with ProjectStore(gpkg_path(folder)) as store:
        interp = store.load_interpretation()
        rows = store.gp.read("lithology", "needs_review = 1 ORDER BY location_id, top")
    out = []
    for r in rows:
        runs = [x for x in interp.runs(r["location_id"]) if x.base > r["top"] + 1e-6 and x.top < r["base"] - 1e-6]
        alts = r["alternatives"] if isinstance(r["alternatives"], list) else json.loads(r["alternatives"] or "[]")
        out.append(
            ReviewItem(
                r["location_id"],
                r["top"],
                r["base"],
                r["description"] or "",
                r["unit"],
                r["probability"],
                alts,
                r["review_reason"] or "",
                proposal_key([x.unit for x in runs]) or (r["unit"] or ""),
                [(x.unit, max(x.top, r["top"]), min(x.base, r["base"]), x.p) for x in runs],
                r["override_unit"],
                r["status"] or "",
            )
        )
    return out


def decide(folder, item: ReviewItem, decision: str, reviewer: str, reason: str = "", unit: str | None = None) -> None:
    """Record a reviewer decision (``accepted`` or ``override``); applied on the next run."""
    from .database import ProjectStore

    with ProjectStore(gpkg_path(folder)) as store:
        store.record_decision(
            item.location_id,
            item.top,
            item.base,
            decision,
            item.proposal,
            reviewer,
            reason,
            unit,
            store.latest_run_id(),
        )


# ---------------------------------------------------------------------------- correlation and domains
def correlate_holes(folder, mode: str = "rules_constrained", ctx: ProjectContext | None = None) -> list:
    """Correlation links between neighbouring holes of a stored project (see :mod:`.correlation`).

    Links join the base of a unit in one hole with the base of the same (or aligned) unit in the
    other, with a confidence and the flags raised by the chosen mode (crossing links, steep apparent
    dips, links across mapped faults, disagreements between alignment and interpretation)."""
    from .correlation import MODES, correlate

    if mode not in MODES:
        raise AGSGeoModelError(f"correlation mode must be one of {', '.join(MODES)}")
    ctx = ctx or load_context(folder)
    return correlate(ctx.project, ctx.interpretation, mode, faults=ctx.model.faults)


def cluster_holes(folder, k_max: int = 6, seed: int = 0, ctx: ProjectContext | None = None):
    """Explainable k-means clustering of the interpreted holes into geological domains, compared with
    the rule-based domains (see :func:`agsgeomodel.ai.cluster_domains`)."""
    from .ai import cluster_domains

    ctx = ctx or load_context(folder)
    try:
        return cluster_domains(ctx.project, ctx.interpretation, ctx.model.column, k_range=(2, max(2, k_max)), seed=seed)
    except ValueError as exc:
        raise AGSGeoModelError(str(exc)) from exc


# ---------------------------------------------------------------------------- BGS
def bgs_connector(
    crs: str = "EPSG:27700",
    cache_path: str | None = None,
    ttl_days: float = 30.0,
    offline: bool = False,
    ogc_api: str | None = None,
    ags_api: str | None = None,
):
    """BGS connector with user settings (cache location / age, offline mode, service URL overrides)."""
    from .bgs import BGSGeoConnector, HttpCache
    from .bgs.services import AGS_API, OGC_API

    cache = HttpCache(cache_path or None, ttl_s=ttl_days * 86400.0, offline=offline)
    return BGSGeoConnector(crs, cache=cache, ogc_api=ogc_api or OGC_API, ags_api=ags_api or AGS_API)


@dataclass
class BGSDiscovery:
    analysis: object
    data: dict
    candidates: list
    notes: list[str] = field(default_factory=list)


def bgs_discover(
    folder_or_project,
    buffer_m: float = 500.0,
    threshold: float = 60.0,
    connector=None,
    crs: str | None = None,
    progress=None,
) -> BGSDiscovery:
    """BGS context and borehole discovery around a project (stored project folder or Project)."""
    from .bgs import BGSGeoConnector, candidates_from_records, rank_candidates
    from .database import ProjectStore

    if isinstance(folder_or_project, (str, Path)):
        with ProjectStore(gpkg_path(folder_or_project)) as store:
            project = store.load_project()
        model = None
        try:
            model = load_context(folder_or_project).model
        except (StoreError, KeyError, FileNotFoundError):
            pass
    else:
        project, model = folder_or_project, None
    con = connector or BGSGeoConnector(crs or project.crs or "EPSG:27700")
    if progress:
        progress(0.1, "analysing project area (BGS geology)")
    an, data = con.analyse(project, buffer_m)
    if progress:
        progress(0.5, "searching BGS borehole indexes")
    recs = con.borehole_records(an.bbox_wgs84)
    cands = candidates_from_records(recs)
    kb = model.column.kb if model is not None else None
    ranked = rank_candidates(
        cands,
        project,
        buffer_polygon=an.buffer_polygon,
        model=model,
        map_units=data.get("bedrock", []) + data.get("superficial", []),
        threshold=threshold,
        kb=kb,
    )
    if progress:
        progress(1.0, "done")
    return BGSDiscovery(an, data, ranked)


def save_bgs(folder, disc: BGSDiscovery) -> None:
    from .database import ProjectStore

    with ProjectStore(gpkg_path(folder)) as store:
        store.save_bgs(disc.candidates)
        store.set_meta("bgs_area_analysis", disc.analysis.to_dict())


def download_bgs_ags(folder, candidates, connector=None) -> list[dict]:
    """Download AGS data for selected candidates (``has_ags``) into ``<folder>/bgs_ags`` and register
    them as secondary inputs (confidence-weighted) in the project configuration."""
    from .bgs import BGSGeoConnector

    sel = [c for c in candidates if c.selected and c.has_ags]
    if not sel:
        return []
    cfg = load_config(folder)
    con = connector or BGSGeoConnector(cfg.settings.get("crs", "EPSG:27700") if cfg and cfg.settings else "EPSG:27700")
    out = Path(folder) / "bgs_ags"
    out.mkdir(parents=True, exist_ok=True)
    files = con.ags_export([c.bgs_id for c in sel])
    per_location = {}  # the export may use either the original LOCA_ID or the BGS LOCA ID
    for c in sel:
        per_location[c.bgs_id] = round(c.confidence, 1)
        if c.reference:
            per_location[c.reference] = round(c.confidence, 1)
    default = round(min(c.confidence for c in sel), 1)
    added = []
    for name, data in files:
        p = out / Path(name).name
        p.write_bytes(data)
        added.append({"path": str(p), "confidence": default, "per_location": per_location, "prefix": "BGS:"})
    if cfg is not None:
        known = {d["path"] for d in cfg.secondary_ags}
        cfg.secondary_ags += [d for d in added if d["path"] not in known]
        save_config(cfg, folder)
    return added


# ---------------------------------------------------------------------------- import only
def import_project(
    folder,
    ags_paths: list[str],
    settings: ProjectSettings | None = None,
    crs_override: str | None = None,
    progress=None,
) -> dict:
    """Import, validate and interpret AGS files into a project folder without building a model
    (fast first look: holes, interpreted strata and findings in project.gpkg)."""
    from .core.provenance import stable_hash
    from .database import ProjectStore
    from .geology import GeoReasoner
    from .geology.knowledge import load_column
    from .importers.ags import import_ags

    st = settings or ProjectSettings()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    project, reports = None, []
    for k, path in enumerate(ags_paths):
        if progress:
            progress(0.1 + 0.4 * k / max(len(ags_paths), 1), f"importing {Path(path).name}")
        imp = import_ags(path, target_crs=st.crs, crs_override=crs_override)
        reports.append((path, imp.validation))
        if project is None:
            project = imp.project
        else:
            project.merge(imp.project, id_prefix=f"F{k}:")
    if project is None:
        raise AGSGeoModelError("no AGS files supplied")
    project.crs = st.crs
    if progress:
        progress(0.6, "interpreting geology (GeoReasoner)")
    column = load_column(st.province)
    with ProjectStore(gpkg_path(folder), st.crs) as store:
        store.apply_overrides(project)
        interp = GeoReasoner(
            column, st.reasoner, primary_sigma=st.model.primary_sigma_m, secondary_sigma=st.model.secondary_sigma_m
        ).interpret(project)
        run_id = "import-" + stable_hash([str(p) for p in ags_paths])[:8]
        store.save_knowledge(column)
        store.save_project(project)
        store.save_interpretation(project, interp, run_id)
        issues = [i for _, rep in reports for i in rep.issues] + list(interp.issues)
        store.save_issues(issues, project)
    cfg = load_config(folder) or new_config(folder, ags_paths, st)
    cfg.ags_paths = list(ags_paths)
    cfg.crs_override = crs_override
    cfg.settings = st.to_dict()
    save_config(cfg, folder)
    if progress:
        progress(1.0, "done")
    return {
        "project": project,
        "interpretation": interp,
        "reports": reports,
        "gpkg": str(gpkg_path(folder)),
        "run_id": run_id,
    }


# ---------------------------------------------------------------------------- 2D exports
TABLE_FORMATS = ("geojson", "shp", "csv", "xlsx")
EXPORT_TABLES = (
    "boreholes",
    "lithology",
    "unit_runs",
    "issues",
    "hdd_stations",
    "hdd_risk_zones",
    "ground_model_parameters",
    "bgs_boreholes",
)


def export_tables(folder, out_dir, formats=TABLE_FORMATS, tables=EXPORT_TABLES) -> dict[str, str]:
    """Export project tables to GeoJSON / Shapefile / CSV / one Excel workbook."""
    from .database import GeoPackage
    from .exports import write_csv, write_excel, write_geojson, write_shapefile

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    gp = GeoPackage(gpkg_path(folder), create=False)
    res: dict[str, str] = {}
    sheets = {}
    try:
        crs = None
        r = gp.conn.execute(
            "SELECT s.organization, s.organization_coordsys_id FROM gpkg_geometry_columns g "
            "JOIN gpkg_spatial_ref_sys s ON s.srs_id = g.srs_id WHERE g.table_name = 'boreholes'"
        ).fetchone()
        if r and r[0] and str(r[0]).upper() == "EPSG":
            crs = f"EPSG:{r[1]}"
        for t in tables:
            if not gp.has_table(t):
                continue
            rows = gp.read(t)
            if not rows:
                continue
            feats = [
                {"geometry": r.pop("geometry", None), "properties": {k: v for k, v in r.items() if k != "fid"}}
                for r in rows
            ]
            flat = [f["properties"] for f in feats]
            if "geojson" in formats and any(f["geometry"] for f in feats):
                res[f"{t}.geojson"] = str(write_geojson(out / f"{t}.geojson", feats, crs, t))
            if "shp" in formats and any(f["geometry"] for f in feats):
                try:
                    res[f"{t}.shp"] = str(write_shapefile(out / f"{t}.shp", feats, crs))
                except Exception as exc:  # e.g. mixed geometry types
                    res[f"{t}.shp"] = f"not written: {exc}"
            if "csv" in formats:
                res[f"{t}.csv"] = str(write_csv(out / f"{t}.csv", flat))
            sheets[t[:31]] = flat
        if "xlsx" in formats and sheets:
            try:
                res["workbook"] = str(
                    write_excel(
                        out / "agsgeomodel_tables.xlsx", sheets, {"README": "Exported from project.gpkg by AGSGeoModel"}
                    )
                )
            except DependencyMissing as exc:
                res["workbook"] = f"not written: {exc}"
    finally:
        gp.close()
    return res


def section_3d_features(sec, run_id: str | None = None) -> list[dict]:
    """Section unit polygons placed vertically along the section trace (PolygonZ) for 3D views."""
    out = []
    for u, poly in sec.unit_polygons():
        ring = []
        for c, z in poly:
            ring.append(
                [
                    float(np.interp(c, sec.chainage, sec.xy[:, 0])),
                    float(np.interp(c, sec.chainage, sec.xy[:, 1])),
                    float(z),
                ]
            )
        ring.append(ring[0])
        out.append(
            {
                "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {"section": sec.name, "unit": u, "run_id": run_id},
            }
        )
    return out
