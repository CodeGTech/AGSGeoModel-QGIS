# project.gpkg data model

Generated from `agsgeomodel/database/schema.py` by `scripts/gen_reference_docs.py`. The file is a
standard OGC GeoPackage (R-tree spatial indexes, `gpkg_contents`, `gpkg_geometry_columns`); every
feature table opens directly in QGIS, GDAL/OGR and ArcGIS Pro. Schema changes are applied by numbered
migrations recorded in `schema_migrations`:

1. initial AGSGeoModel schema
2. engineering ground model, hydrogeology and HDD tables
3. interpreted unit runs, 3D surface triangles and review decisions

## Version 1 tables

### `project_metadata`

Project settings and provenance (no geometry).

| Field | Type |
|---|---|
| `key` | text |
| `value` | json |

### `formations`

Stratigraphic units (knowledge base, BGS Lexicon codes) (no geometry).

| Field | Type |
|---|---|
| `code` | text |
| `name` | text |
| `lexicon` | boolean |
| `parent` | text |
| `rank` | text |
| `class` | text |
| `domain` | text |
| `material` | text |
| `age_label` | text |
| `age_max_ma` | real |
| `age_min_ma` | real |
| `environment` | text |
| `hsu` | text |
| `permeability` | text |
| `colour` | text |
| `lexicon_url` | text |
| `notes` | text |

### `strat_relations`

Stratigraphic knowledge graph edges (above, below, member_of, ...) (no geometry).

| Field | Type |
|---|---|
| `province` | text |
| `subject` | text |
| `predicate` | text |
| `object` | text |
| `sequence` | text |
| `note` | text |

### `boreholes`

Exploratory hole locations (z = ground level) (Point Z).

| Field | Type |
|---|---|
| `location_id` | text |
| `kind` | text |
| `type_code` | text |
| `source` | text |
| `ground_level` | real |
| `final_depth` | real |
| `date` | text |
| `quality` | real |
| `domain` | text |
| `remarks` | text |

### `lithology`

Logged strata with GeoReasoner interpretation (vertical 3D lines) (LineString Z).

| Field | Type |
|---|---|
| `location_id` | text |
| `top` | real |
| `base` | real |
| `z_top` | real |
| `z_base` | real |
| `description` | text |
| `legend_code` | text |
| `geology_code` | text |
| `bgs_code` | text |
| `formation_name` | text |
| `unit` | text |
| `unit_name` | text |
| `probability` | real |
| `alternatives` | json |
| `status` | text |
| `needs_review` | boolean |
| `review_reason` | text |
| `evidence` | json |
| `override_unit` | text |
| `override_by` | text |
| `override_reason` | text |
| `colour` | text |
| `source` | text |
| `run_id` | text |

### `samples`

Samples (Point Z).

| Field | Type |
|---|---|
| `location_id` | text |
| `top` | real |
| `base` | real |
| `ref` | text |
| `type` | text |
| `sample_id` | text |

### `cpt`

CPT readings with derived engineering parameters (Point Z).

| Field | Type |
|---|---|
| `location_id` | text |
| `test_id` | text |
| `depth` | real |
| `qc` | real |
| `fs` | real |
| `u2` | real |
| `qt` | real |
| `Ic` | real |
| `SBTn` | integer |
| `su_kPa` | real |
| `phi_deg` | real |
| `Dr` | real |
| `E_MPa` | real |
| `M_MPa` | real |
| `k_ms` | real |
| `gamma_kNm3` | real |
| `unit` | text |

### `spt`

SPT results with corrections and correlations (Point Z).

| Field | Type |
|---|---|
| `location_id` | text |
| `depth` | real |
| `n` | real |
| `n60` | real |
| `n1_60` | real |
| `refusal` | boolean |
| `unit` | text |
| `cu_kPa` | real |
| `phi_deg` | real |
| `dr` | real |

### `water_levels`

Groundwater observations (z = head) (Point Z).

| Field | Type |
|---|---|
| `location_id` | text |
| `kind` | text |
| `system` | text |
| `unit` | text |
| `head` | real |
| `sigma` | real |
| `depth` | real |
| `perched` | boolean |
| `note` | text |

### `lab_results`

Laboratory results (Point Z).

| Field | Type |
|---|---|
| `location_id` | text |
| `depth` | real |
| `ags_group` | text |
| `test` | text |
| `value` | real |
| `uom` | text |
| `text` | text |
| `sample_ref` | text |
| `unit` | text |

### `faults`

Fault traces (LineString).

| Field | Type |
|---|---|
| `name` | text |
| `throw_m` | real |
| `downthrown_side` | text |
| `dip_deg` | real |
| `source` | text |

### `geological_contacts`

Typed modelling observations (Point Z).

| Field | Type |
|---|---|
| `surface` | text |
| `location_id` | text |
| `value` | real |
| `kind` | text |
| `sigma` | real |
| `source` | text |
| `inferred` | boolean |
| `confidence_class` | text |
| `note` | text |
| `run_id` | text |

### `surfaces`

Modelled surfaces (rasters stored alongside) (no geometry).

| Field | Type |
|---|---|
| `run_id` | text |
| `name` | text |
| `kind` | text |
| `method` | text |
| `n_data` | integer |
| `min` | real |
| `max` | real |
| `mean_std` | real |
| `raster` | text |
| `std_raster` | text |
| `class_raster` | text |
| `confidence_raster` | text |
| `mesh` | text |
| `variogram` | json |
| `diagnostics` | json |

### `surface_contours`

Contours of modelled surfaces (LineString Z).

| Field | Type |
|---|---|
| `run_id` | text |
| `surface` | text |
| `level` | real |

### `uncertainty_zones`

Observed/Interpolated/Inferred/Speculative (Polygon).

| Field | Type |
|---|---|
| `run_id` | text |
| `surface` | text |
| `class_code` | integer |
| `class_name` | text |

### `cross_sections`

Section traces (LineString).

| Field | Type |
|---|---|
| `name` | text |
| `length` | real |
| `run_id` | text |
| `svg` | text |
| `pdf` | text |

### `cross_section_units`

Section-space polygons (x = chainage, y = elevation; no CRS) (Polygon).

| Field | Type |
|---|---|
| `section` | text |
| `kind` | text |
| `unit` | text |

### `fence_diagrams`

Vertical 3D fence panels (Polygon Z).

| Field | Type |
|---|---|
| `fence` | text |
| `panel` | text |
| `unit` | text |
| `run_id` | text |

### `domains`

Ground domains (Polygon).

| Field | Type |
|---|---|
| `run_id` | text |
| `domain` | text |

### `bgs_boreholes`

BGS borehole records discovered near the site (Point).

| Field | Type |
|---|---|
| `bgs_id` | text |
| `reference` | text |
| `name` | text |
| `collection` | text |
| `length_m` | real |
| `year` | text |
| `precision` | text |
| `has_ags` | boolean |
| `url` | text |
| `distance_m` | real |
| `confidence` | real |
| `score_breakdown` | json |
| `selected` | boolean |

### `issues`

Validation findings (Point).

| Field | Type |
|---|---|
| `rule` | text |
| `severity` | text |
| `category` | text |
| `message` | text |
| `location_ids` | text |
| `depth` | real |
| `explanations` | json |
| `suggestion` | text |
| `status` | text |

### `model_runs`

Run history (versioning and reproducibility) (no geometry).

| Field | Type |
|---|---|
| `run_id` | text |
| `kind` | text |
| `created` | text |
| `input_hash` | text |
| `kb_version` | text |
| `seed` | integer |
| `config` | json |
| `summary` | json |
| `warnings` | json |
| `software` | json |
| `outputs` | json |

## Version 2 tables

### `ground_model_parameters`

Engineering ground model: statistics and characteristic values per unit (no geometry).

| Field | Type |
|---|---|
| `run_id` | text |
| `unit` | text |
| `parameter` | text |
| `label` | text |
| `uom` | text |
| `n` | integer |
| `mean` | real |
| `sd` | real |
| `cov` | real |
| `minimum` | real |
| `median` | real |
| `maximum` | real |
| `cautious_mean_low` | real |
| `cautious_mean_high` | real |
| `fractile5_low` | real |
| `fractile5_high` | real |
| `trend_slope` | real |
| `trend_r2` | real |
| `log_space` | boolean |
| `sources` | json |

### `head_contours`

Groundwater head contours (LineString).

| Field | Type |
|---|---|
| `run_id` | text |
| `system` | text |
| `level` | real |

### `flow_vectors`

Groundwater flow direction (rotate arrow symbols by azimuth) (Point).

| Field | Type |
|---|---|
| `run_id` | text |
| `system` | text |
| `azimuth_deg` | real |
| `gradient` | real |
| `head` | real |
| `class_code` | integer |

### `seepage_paths`

Down-gradient particle paths (LineString).

| Field | Type |
|---|---|
| `run_id` | text |
| `system` | text |

### `hdd_stations`

HDD bore stations with RAG ratings (z = bore elevation) (Point Z).

| Field | Type |
|---|---|
| `run_id` | text |
| `alignment` | text |
| `chainage` | real |
| `unit` | text |
| `cover` | real |
| `p_unit` | real |
| `pressure_head` | real |
| `rag_overall` | text |
| `rag_running_sand` | text |
| `rag_groundwater` | text |
| `rag_boulders` | text |
| `rag_rock` | text |
| `rag_mixed_face` | text |
| `rag_loss_of_circulation` | text |
| `confidence_rag` | text |
| `frac_fos` | real |
| `reasons` | json |

### `hdd_risk_zones`

Contiguous HDD risk zones (LineString).

| Field | Type |
|---|---|
| `run_id` | text |
| `alignment` | text |
| `hazard` | text |
| `rag` | text |
| `ch_from` | real |
| `ch_to` | real |
| `reasons` | json |

## Version 3 tables

### `unit_runs`

Interpreted geological units per hole incl. inferred missing layers (vertical 3D lines) (LineString Z).

| Field | Type |
|---|---|
| `location_id` | text |
| `unit` | text |
| `unit_name` | text |
| `top` | real |
| `base` | real |
| `z_top` | real |
| `z_base` | real |
| `probability` | real |
| `inferred` | boolean |
| `colour` | text |
| `run_id` | text |

### `model_surfaces_3d`

Unit top surfaces as 3D triangles for the QGIS 3D view (Polygon Z).

| Field | Type |
|---|---|
| `run_id` | text |
| `unit` | text |
| `surface` | text |
| `class_code` | integer |

### `review_decisions`

Reviewer decisions on flagged interpretations (accept / override); audit trail, latest wins (no geometry).

| Field | Type |
|---|---|
| `location_id` | text |
| `top` | real |
| `base` | real |
| `decision` | text |
| `unit` | text |
| `proposal` | text |
| `reviewer` | text |
| `reason` | text |
| `created` | text |
| `run_id` | text |

## Files next to the GeoPackage

| Path | Content |
|---|---|
| `rasters/<run_id>/*.tif` | surfaces, standard deviations, uncertainty classes and confidence (GeoTIFF) |
| `rasters/<run_id>/*.2dm` | surfaces as MDAL meshes |
| `agsgeomodel_project.json` | the pipeline configuration (re-run with `python -m agsgeomodel run <folder>`) |
| `sections/`, `hdd/` | drawings (SVG / PDF) and HDD station tables |
| `exports/` | 3D exports and `export_manifest.json` |
| `backups/` | GeoPackage snapshots taken before each re-run |
