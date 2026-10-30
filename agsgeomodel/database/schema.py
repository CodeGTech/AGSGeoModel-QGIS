"""project.gpkg schema and migrations.

Each migration is applied once, in order, and recorded in ``schema_migrations``. A project written
by an earlier release is backed up (``backups/``) by :class:`~agsgeomodel.database.ProjectStore`
before it is upgraded. Never edit a released migration — add a new one.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable

from .gpkg import GeoPackage

SCHEMA_VERSION = 3

# (table, geometry type | None, z, fields, description)
TABLES_V1: list[tuple[str, str | None, bool, list[tuple[str, str]], str]] = [
    ("project_metadata", None, False, [("key", "text"), ("value", "json")], "Project settings and provenance"),
    (
        "formations",
        None,
        False,
        [
            ("code", "text"),
            ("name", "text"),
            ("lexicon", "boolean"),
            ("parent", "text"),
            ("rank", "text"),
            ("class", "text"),
            ("domain", "text"),
            ("material", "text"),
            ("age_label", "text"),
            ("age_max_ma", "real"),
            ("age_min_ma", "real"),
            ("environment", "text"),
            ("hsu", "text"),
            ("permeability", "text"),
            ("colour", "text"),
            ("lexicon_url", "text"),
            ("notes", "text"),
        ],
        "Stratigraphic units (knowledge base, BGS Lexicon codes)",
    ),
    (
        "strat_relations",
        None,
        False,
        [
            ("province", "text"),
            ("subject", "text"),
            ("predicate", "text"),
            ("object", "text"),
            ("sequence", "text"),
            ("note", "text"),
        ],
        "Stratigraphic knowledge graph edges (above, below, member_of, ...)",
    ),
    (
        "boreholes",
        "Point",
        True,
        [
            ("location_id", "text"),
            ("kind", "text"),
            ("type_code", "text"),
            ("source", "text"),
            ("ground_level", "real"),
            ("final_depth", "real"),
            ("date", "text"),
            ("quality", "real"),
            ("domain", "text"),
            ("remarks", "text"),
        ],
        "Exploratory hole locations (z = ground level)",
    ),
    (
        "lithology",
        "LineString",
        True,
        [
            ("location_id", "text"),
            ("top", "real"),
            ("base", "real"),
            ("z_top", "real"),
            ("z_base", "real"),
            ("description", "text"),
            ("legend_code", "text"),
            ("geology_code", "text"),
            ("bgs_code", "text"),
            ("formation_name", "text"),
            ("unit", "text"),
            ("unit_name", "text"),
            ("probability", "real"),
            ("alternatives", "json"),
            ("status", "text"),
            ("needs_review", "boolean"),
            ("review_reason", "text"),
            ("evidence", "json"),
            ("override_unit", "text"),
            ("override_by", "text"),
            ("override_reason", "text"),
            ("colour", "text"),
            ("source", "text"),
            ("run_id", "text"),
        ],
        "Logged strata with GeoReasoner interpretation (vertical 3D lines)",
    ),
    (
        "samples",
        "Point",
        True,
        [
            ("location_id", "text"),
            ("top", "real"),
            ("base", "real"),
            ("ref", "text"),
            ("type", "text"),
            ("sample_id", "text"),
        ],
        "Samples",
    ),
    (
        "cpt",
        "Point",
        True,
        [
            ("location_id", "text"),
            ("test_id", "text"),
            ("depth", "real"),
            ("qc", "real"),
            ("fs", "real"),
            ("u2", "real"),
            ("qt", "real"),
            ("Ic", "real"),
            ("SBTn", "integer"),
            ("su_kPa", "real"),
            ("phi_deg", "real"),
            ("Dr", "real"),
            ("E_MPa", "real"),
            ("M_MPa", "real"),
            ("k_ms", "real"),
            ("gamma_kNm3", "real"),
            ("unit", "text"),
        ],
        "CPT readings with derived engineering parameters",
    ),
    (
        "spt",
        "Point",
        True,
        [
            ("location_id", "text"),
            ("depth", "real"),
            ("n", "real"),
            ("n60", "real"),
            ("n1_60", "real"),
            ("refusal", "boolean"),
            ("unit", "text"),
            ("cu_kPa", "real"),
            ("phi_deg", "real"),
            ("dr", "real"),
        ],
        "SPT results with corrections and correlations",
    ),
    (
        "water_levels",
        "Point",
        True,
        [
            ("location_id", "text"),
            ("kind", "text"),
            ("system", "text"),
            ("unit", "text"),
            ("head", "real"),
            ("sigma", "real"),
            ("depth", "real"),
            ("perched", "boolean"),
            ("note", "text"),
        ],
        "Groundwater observations (z = head)",
    ),
    (
        "lab_results",
        "Point",
        True,
        [
            ("location_id", "text"),
            ("depth", "real"),
            ("ags_group", "text"),
            ("test", "text"),
            ("value", "real"),
            ("uom", "text"),
            ("text", "text"),
            ("sample_ref", "text"),
            ("unit", "text"),
        ],
        "Laboratory results",
    ),
    (
        "faults",
        "LineString",
        False,
        [("name", "text"), ("throw_m", "real"), ("downthrown_side", "text"), ("dip_deg", "real"), ("source", "text")],
        "Fault traces",
    ),
    (
        "geological_contacts",
        "Point",
        True,
        [
            ("surface", "text"),
            ("location_id", "text"),
            ("value", "real"),
            ("kind", "text"),
            ("sigma", "real"),
            ("source", "text"),
            ("inferred", "boolean"),
            ("confidence_class", "text"),
            ("note", "text"),
            ("run_id", "text"),
        ],
        "Typed modelling observations",
    ),
    (
        "surfaces",
        None,
        False,
        [
            ("run_id", "text"),
            ("name", "text"),
            ("kind", "text"),
            ("method", "text"),
            ("n_data", "integer"),
            ("min", "real"),
            ("max", "real"),
            ("mean_std", "real"),
            ("raster", "text"),
            ("std_raster", "text"),
            ("class_raster", "text"),
            ("confidence_raster", "text"),
            ("mesh", "text"),
            ("variogram", "json"),
            ("diagnostics", "json"),
        ],
        "Modelled surfaces (rasters stored alongside)",
    ),
    (
        "surface_contours",
        "LineString",
        True,
        [("run_id", "text"), ("surface", "text"), ("level", "real")],
        "Contours of modelled surfaces",
    ),
    (
        "uncertainty_zones",
        "Polygon",
        False,
        [("run_id", "text"), ("surface", "text"), ("class_code", "integer"), ("class_name", "text")],
        "Observed/Interpolated/Inferred/Speculative",
    ),
    (
        "cross_sections",
        "LineString",
        False,
        [("name", "text"), ("length", "real"), ("run_id", "text"), ("svg", "text"), ("pdf", "text")],
        "Section traces",
    ),
    (
        "cross_section_units",
        "Polygon",
        False,
        [("section", "text"), ("kind", "text"), ("unit", "text")],
        "Section-space polygons (x = chainage, y = elevation; no CRS)",
    ),
    (
        "fence_diagrams",
        "Polygon",
        True,
        [("fence", "text"), ("panel", "text"), ("unit", "text"), ("run_id", "text")],
        "Vertical 3D fence panels",
    ),
    ("domains", "Polygon", False, [("run_id", "text"), ("domain", "text")], "Ground domains"),
    (
        "bgs_boreholes",
        "Point",
        False,
        [
            ("bgs_id", "text"),
            ("reference", "text"),
            ("name", "text"),
            ("collection", "text"),
            ("length_m", "real"),
            ("year", "text"),
            ("precision", "text"),
            ("has_ags", "boolean"),
            ("url", "text"),
            ("distance_m", "real"),
            ("confidence", "real"),
            ("score_breakdown", "json"),
            ("selected", "boolean"),
        ],
        "BGS borehole records discovered near the site",
    ),
    (
        "issues",
        "Point",
        False,
        [
            ("rule", "text"),
            ("severity", "text"),
            ("category", "text"),
            ("message", "text"),
            ("location_ids", "text"),
            ("depth", "real"),
            ("explanations", "json"),
            ("suggestion", "text"),
            ("status", "text"),
        ],
        "Validation findings",
    ),
    (
        "model_runs",
        None,
        False,
        [
            ("run_id", "text"),
            ("kind", "text"),
            ("created", "text"),
            ("input_hash", "text"),
            ("kb_version", "text"),
            ("seed", "integer"),
            ("config", "json"),
            ("summary", "json"),
            ("warnings", "json"),
            ("software", "json"),
            ("outputs", "json"),
        ],
        "Run history (versioning and reproducibility)",
    ),
]

TABLES_V2: list[tuple[str, str | None, bool, list[tuple[str, str]], str]] = [
    (
        "ground_model_parameters",
        None,
        False,
        [
            ("run_id", "text"),
            ("unit", "text"),
            ("parameter", "text"),
            ("label", "text"),
            ("uom", "text"),
            ("n", "integer"),
            ("mean", "real"),
            ("sd", "real"),
            ("cov", "real"),
            ("minimum", "real"),
            ("median", "real"),
            ("maximum", "real"),
            ("cautious_mean_low", "real"),
            ("cautious_mean_high", "real"),
            ("fractile5_low", "real"),
            ("fractile5_high", "real"),
            ("trend_slope", "real"),
            ("trend_r2", "real"),
            ("log_space", "boolean"),
            ("sources", "json"),
        ],
        "Engineering ground model: statistics and characteristic values per unit",
    ),
    (
        "head_contours",
        "LineString",
        False,
        [("run_id", "text"), ("system", "text"), ("level", "real")],
        "Groundwater head contours",
    ),
    (
        "flow_vectors",
        "Point",
        False,
        [
            ("run_id", "text"),
            ("system", "text"),
            ("azimuth_deg", "real"),
            ("gradient", "real"),
            ("head", "real"),
            ("class_code", "integer"),
        ],
        "Groundwater flow direction (rotate arrow symbols by azimuth)",
    ),
    ("seepage_paths", "LineString", False, [("run_id", "text"), ("system", "text")], "Down-gradient particle paths"),
    (
        "hdd_stations",
        "Point",
        True,
        [
            ("run_id", "text"),
            ("alignment", "text"),
            ("chainage", "real"),
            ("unit", "text"),
            ("cover", "real"),
            ("p_unit", "real"),
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
            ("reasons", "json"),
        ],
        "HDD bore stations with RAG ratings (z = bore elevation)",
    ),
    (
        "hdd_risk_zones",
        "LineString",
        False,
        [
            ("run_id", "text"),
            ("alignment", "text"),
            ("hazard", "text"),
            ("rag", "text"),
            ("ch_from", "real"),
            ("ch_to", "real"),
            ("reasons", "json"),
        ],
        "Contiguous HDD risk zones",
    ),
]


TABLES_V3: list[tuple[str, str | None, bool, list[tuple[str, str]], str]] = [
    (
        "unit_runs",
        "LineString",
        True,
        [
            ("location_id", "text"),
            ("unit", "text"),
            ("unit_name", "text"),
            ("top", "real"),
            ("base", "real"),
            ("z_top", "real"),
            ("z_base", "real"),
            ("probability", "real"),
            ("inferred", "boolean"),
            ("colour", "text"),
            ("run_id", "text"),
        ],
        "Interpreted geological units per hole incl. inferred missing layers (vertical 3D lines)",
    ),
    (
        "model_surfaces_3d",
        "Polygon",
        True,
        [("run_id", "text"), ("unit", "text"), ("surface", "text"), ("class_code", "integer")],
        "Unit top surfaces as 3D triangles for the QGIS 3D view",
    ),
    (
        "review_decisions",
        None,
        False,
        [
            ("location_id", "text"),
            ("top", "real"),
            ("base", "real"),
            ("decision", "text"),
            ("unit", "text"),
            ("proposal", "text"),
            ("reviewer", "text"),
            ("reason", "text"),
            ("created", "text"),
            ("run_id", "text"),
        ],
        "Reviewer decisions on flagged interpretations (accept / override); audit trail, latest wins",
    ),
]


def _create(gp: GeoPackage, tables, crs) -> None:
    for name, gtype, z, fields, desc in tables:
        srs = -1 if name == "cross_section_units" else crs
        gp.create_table(name, fields, gtype, srs if gtype else None, z, desc)


MIGRATIONS: list[tuple[int, str, Callable[[GeoPackage, str], None]]] = [
    (1, "initial AGSGeoModel schema", lambda gp, crs: _create(gp, TABLES_V1, crs)),
    (2, "engineering ground model, hydrogeology and HDD tables", lambda gp, crs: _create(gp, TABLES_V2, crs)),
    (
        3,
        "interpreted unit runs, 3D surface triangles and review decisions",
        lambda gp, crs: _create(gp, TABLES_V3, crs),
    ),
]


def current_version(gp: GeoPackage) -> int:
    if not gp.has_table("schema_migrations"):
        return 0
    r = gp.conn.execute("SELECT max(version) FROM schema_migrations").fetchone()
    return int(r[0] or 0)


def migrate(gp: GeoPackage, crs: str, target: int | None = None) -> list[int]:
    if not gp.has_table("schema_migrations"):
        gp.create_table(
            "schema_migrations",
            [("version", "integer"), ("description", "text"), ("applied", "text")],
            description="Applied schema migrations",
        )
    have = current_version(gp)
    applied = []
    for version, desc, fn in MIGRATIONS:
        if version <= have or (target is not None and version > target):
            continue
        fn(gp, crs)
        gp.insert(
            "schema_migrations",
            [{"version": version, "description": desc, "applied": _dt.datetime.now(_dt.UTC).isoformat()}],
        )
        applied.append(version)
    return applied
