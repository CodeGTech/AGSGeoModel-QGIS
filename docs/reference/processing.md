# Processing algorithms

Generated from `agsgeomodel/algorithms` by `scripts/gen_reference_docs.py` (QGIS 3.40.3-Bratislava).

All algorithms are in the Processing toolbox under **AGSGeoModel**, can be chained in the graphical
modeler and run from Python (`processing.run("agsgeomodel:<id>", {...})`) or the command line
(`qgis_process run agsgeomodel:<id> -- KEY=value ...`).

## Validate AGS file (`agsgeomodel:validate_ags`)

*Group: AGS data*

Checks an AGS4 (or AGS3) file against the AGS4 format rules (1-20, with the bundled data dictionary subset; install python-ags4 for complete rule 7/9 checks) and engineering checks E00-E15 (duplicate holes, overlapping or gapped strata, depths beyond the final depth, missing coordinates or ground levels, implausible values, ...). Writes an HTML report and optionally a CSV of all findings.

| Parameter | Description | Type | Default |
|---|---|---|---|
| `INPUT` | AGS file | file |  |
| `OUTPUT_HTML` | Validation report | fileDestination |  |
| `OUTPUT_CSV` | Findings (CSV) (optional) | fileDestination |  |

| Output | Description | Type |
|---|---|---|
| `OUTPUT_HTML` | Validation report | outputHtml |
| `OUTPUT_CSV` | Findings (CSV) | outputFile |
| `CRITICAL` | Critical | outputNumber |
| `ERRORS` | Errors | outputNumber |
| `WARNINGS` | Warnings | outputNumber |
| `NOTES` | Notes | outputNumber |
| `SUMMARY` | Summary | outputString |

## Import and interpret AGS (`agsgeomodel:import_ags`)

*Group: AGS data*

Imports one or more AGS files into an AGSGeoModel project folder (project.gpkg), validates them, transforms coordinates to the project CRS and interprets the logged strata with the GeoReasoner (formations, probabilities, evidence, inferred missing layers and geological consistency findings). No surfaces are modelled: use 'Build geological model' for that.

| Parameter | Description | Type | Default |
|---|---|---|---|
| `INPUT` | AGS files | multilayer |  |
| `CRS` | Project CRS | crs | EPSG:27700 |
| `SOURCE_CRS` | Source CRS override (if detection is wrong) (optional) | crs |  |
| `PROVINCE` | Stratigraphic column | enum | 1 |
| `PROJECT` | Project folder | folderDestination |  |

| Output | Description | Type |
|---|---|---|
| `PROJECT` | Project folder | outputFolder |
| `GPKG` | Project GeoPackage | outputFile |
| `HOLES` | Exploratory holes | outputNumber |
| `REVIEW` | Intervals flagged for review | outputNumber |

## Build geological model (`agsgeomodel:build_model`)

*Group: Geological modelling*

Complete AGSGeoModel workflow: AGS import and validation, GeoReasoner interpretation (stratigraphic order, superposition, unconformities, missing layers), constrained surface modelling (kriging with inequality constraints, fault barriers, map constraints, uncertainty classes Observed / Interpolated / Inferred / Speculative), Monte Carlo realisations, CPT/SPT interpretation, engineering ground model, hydrogeology, sections, fence diagram, 3D exports and an HTML report. Every run is recorded in project.gpkg (model_runs) with its inputs hash and configuration.

| Parameter | Description | Type | Default |
|---|---|---|---|
| `INPUT` | AGS files | multilayer |  |
| `CRS` | Project CRS | crs | EPSG:27700 |
| `PROVINCE` | Stratigraphic column | enum | 1 |
| `CELL` | Cell size (m, 0 = automatic) | number | 0.0 |
| `BUFFER` | Model buffer around the holes (m) | number | 250.0 |
| `REALISATIONS` | Monte Carlo realisations | number | 40 |
| `DTM` | Ground model (DTM) (optional) | raster |  |
| `FAULTS` | Faults (name, throw_m, downthrown_side, dip_deg) (optional) | source |  |
| `SUP_MAP` | Superficial geology map (optional) | source |  |
| `SUP_FIELD` | Superficial map code field (Lexicon code) (optional) | field |  |
| `SUP_COMPLETE` | Superficial map is complete (absent outside polygons) | boolean | False |
| `BED_MAP` | Bedrock geology map (optional) | source |  |
| `BED_FIELD` | Bedrock map code field (Lexicon code) (optional) | field |  |
| `ENGINEERING` | CPT/SPT and engineering ground model | boolean | True |
| `HYDRO` | Hydrogeology | boolean | True |
| `FENCE` | Fence diagram | boolean | True |
| `FORMATS` | 3D exports (optional) | enum | [0, 4] |
| `REPORT_HTML` | HTML report | boolean | True |
| `PROJECT` | Project folder | folderDestination |  |

| Output | Description | Type |
|---|---|---|
| `PROJECT` | Project folder | outputFolder |
| `GPKG` | Project GeoPackage | outputFile |
| `REPORT` | Report | outputFile |
| `RUN_ID` | Run id | outputString |

## Rebuild model (apply reviewer decisions) (`agsgeomodel:rebuild_model`)

*Group: Geological modelling*

Re-runs the stored configuration of a project folder. Reviewer overrides edited in the attribute form (lithology.override_unit) and decisions recorded in the review panel are applied first; acceptances lapse automatically if the new interpretation differs from what was accepted.

| Parameter | Description | Type | Default |
|---|---|---|---|
| `PROJECT_IN` | Project folder | file |  |

| Output | Description | Type |
|---|---|---|
| `PROJECT` | Project folder | outputFolder |
| `GPKG` | Project GeoPackage | outputFile |
| `REPORT` | Report | outputFile |
| `RUN_ID` | Run id | outputString |

## Cross sections from lines (`agsgeomodel:cross_sections`)

*Group: Geological modelling*

Draws a geological cross section along every line of the input layer from the latest model of a project: unit polygons, contacts styled by uncertainty class, faults, groundwater, and exploratory holes within the projection corridor. Writes SVG and PDF files to <project>/sections and the sections as vertical 3D polygons (for the QGIS 3D view).

| Parameter | Description | Type | Default |
|---|---|---|---|
| `PROJECT_IN` | Project folder | file |  |
| `LINES` | Section lines | source |  |
| `NAME_FIELD` | Name field (optional) | field |  |
| `VE` | Vertical exaggeration | number | 5.0 |
| `CORRIDOR` | Projection corridor (m) | number | 50.0 |
| `OUTPUT` | Sections (3D panels) | sink |  |

| Output | Description | Type |
|---|---|---|
| `OUTPUT` | Sections (3D panels) | outputVector |
| `FOLDER` | Section drawings | outputFolder |
| `FILES` | PDF files | outputString |

## Export model and tables (`agsgeomodel:export_model`)

*Group: Geological modelling*

Exports the latest model of a project to 3D formats (VTK, VTK voxels, OBJ, PLY, glTF, DXF, IFC4 with georeferencing) and the project tables (holes, strata, interpreted units, findings, HDD stations, ground model) to GeoJSON, Shapefile, CSV and an Excel workbook. OBJ/PLY/glTF/IFC coordinates are relative to the local origin recorded in export_manifest.json.

| Parameter | Description | Type | Default |
|---|---|---|---|
| `PROJECT_IN` | Project folder | file |  |
| `FORMATS` | 3D formats (optional) | enum | [0, 4, 5] |
| `TABLES` | Table formats (optional) | enum | [0, 2] |
| `VE` | Vertical exaggeration | number | 1.0 |
| `STRIDE` | Grid stride (1 = full resolution) | number | 1 |
| `OUTPUT` | Output folder | folderDestination |  |

| Output | Description | Type |
|---|---|---|
| `OUTPUT` | Output folder | outputFolder |

## Interpret CPT soundings (`agsgeomodel:cpt_interpretation`)

*Group: Engineering and HDD*

Interprets the CPT soundings in an AGS file (SCPG/SCPT) with Robertson (2009/2010) methods: normalised cone resistance Qtn with iterative stress exponent, soil behaviour type index Ic and SBTn zone, undrained shear strength (Nkt), OCR, relative density, friction angle, constrained and Young's moduli (coarse soils only), shear-wave velocity, small-strain stiffness and permeability. Every correlation and its parameters are recorded with the results.

| Parameter | Description | Type | Default |
|---|---|---|---|
| `INPUT` | AGS file with CPT data | file |  |
| `NKT` | Cone factor Nkt | number | 14.0 |
| `WATER_DEPTH` | Groundwater depth (m, blank = from AGS SCPG_WAT or dry) (optional) | number |  |
| `OUTPUT` | CPT parameters | sink |  |

| Output | Description | Type |
|---|---|---|
| `OUTPUT` | CPT parameters | outputVector |

## HDD ground-risk assessment (`agsgeomodel:hdd_assessment`)

*Group: Engineering and HDD*

Designs (entry/exit tangents and bends) or follows a given bore profile along an alignment and assesses the ground at every station against the latest model: geology and contacts, rockhead, groundwater heads, formation changes and RAG ratings for running sand, groundwater, boulders, rock, mixed face and loss of circulation (Delft cavity-expansion frac-out screening, hydrostatic unless annular pressures are supplied). Writes stations, risk zones, a long section (PDF/SVG) and a CSV. The model confidence is reported for every station.

| Parameter | Description | Type | Default |
|---|---|---|---|
| `PROJECT_IN` | Project folder | file |  |
| `ALIGNMENT` | Alignment (first or selected line) | source |  |
| `NAME` | Name | string | HDD-01 |
| `ENTRY` | Entry angle (°) | number | 12.0 |
| `EXIT` | Exit angle (°) | number | 10.0 |
| `RADIUS` | Bend radius (m) | number | 250.0 |
| `DESIGN_LEVEL` | Design level (m OD, blank = automatic) (optional) | number |  |
| `OD` | Product outside diameter (m) | number | 0.25 |
| `REAM` | Ream factor | number | 1.5 |
| `CROSSINGS` | Crossed assets (name, min_clearance_m) (optional) | source |  |
| `STATIONS` | HDD stations | sink |  |
| `ZONES` | HDD risk zones | sink |  |

| Output | Description | Type |
|---|---|---|
| `STATIONS` | HDD stations | outputVector |
| `ZONES` | HDD risk zones | outputVector |
| `LONG_SECTION` | Long section (PDF) | outputFile |
| `OVERALL_RAG` | Overall rating | outputString |

## BGS geology and borehole discovery (`agsgeomodel:bgs_discovery`)

*Group: British Geological Survey*

Retrieves BGS geology around a project (1:625 000 bedrock, superficial deposits and faults via the BGS OGC API - labelled BETA by BGS - plus landslide and map-sheet indexes), suggests the stratigraphic column, and ranks BGS borehole records (Single Onshore Borehole Index and AGS data held by BGS) for gap filling with an explainable confidence score (proximity, value for filling data gaps, geology, elevation, age and record quality). Contains British Geological Survey materials © UKRI.

| Parameter | Description | Type | Default |
|---|---|---|---|
| `PROJECT_IN` | Project folder | file |  |
| `BUFFER` | Search buffer | enum | 2 |
| `THRESHOLD` | Confidence threshold (%) | number | 60.0 |
| `OUTPUT` | BGS borehole candidates | sink |  |
| `SUMMARY_HTML` | Area summary (optional) | fileDestination |  |

| Output | Description | Type |
|---|---|---|
| `OUTPUT` | BGS borehole candidates | outputVector |
| `SUMMARY_HTML` | Area summary | outputHtml |
| `PROVINCE` | Suggested stratigraphic column | outputString |

## Train formation model from reviewed projects (`agsgeomodel:train_formation_model`)

*Group: Machine learning*

Trains an explainable formation classifier from the interpreted strata of one or more project GeoPackages: reviewer overrides plus high-confidence stated/classified intervals. Features are the parsed BS 5930 description terms plus depth and elevation descriptors. Cross-validated accuracy, a confusion matrix and a temperature calibration (never sharpening) are written to the model card. The model only adds evidence: the order-constrained GeoReasoner still decides, so the result can never be stratigraphically impossible. Use it in Settings > GeoReasoner > Learned formation model.

| Parameter | Description | Type | Default |
|---|---|---|---|
| `PROJECTS` | Project GeoPackages (project.gpkg) | multilayer |  |
| `BACKEND` | Learner | enum | 0 |
| `MIN_P` | Minimum probability of unreviewed labels | number | 0.9 |
| `OUTPUT` | Formation model | fileDestination |  |

| Output | Description | Type |
|---|---|---|
| `OUTPUT` | Formation model | outputFile |
| `CV_ACCURACY` | Cross-validated accuracy | outputNumber |
| `N_TRAIN` | Training intervals | outputNumber |

## Correlate exploratory holes (`agsgeomodel:correlate_holes`)

*Group: Machine learning*

Correlation links between neighbouring holes of a project (3D lines joining the base of a unit in one hole to the base of the same, or aligned, unit in the other). Modes: rules_constrained (same interpreted unit, checked against superposition, apparent-dip limits and mapped faults), formation (same unit), hybrid (formation where confident, otherwise description similarity and elevation), ai_assisted (order-preserving alignment of the two logs on description similarity and elevation, with disagreements flagged) and nearest_neighbour (equal elevations: not geologically constrained, for comparison only). Each link has a confidence and the flags raised by the checks.

| Parameter | Description | Type | Default |
|---|---|---|---|
| `PROJECT_IN` | Project folder | file |  |
| `MODE` | Correlation mode | enum | 0 |
| `OUTPUT` | Correlation links | sink |  |

| Output | Description | Type |
|---|---|---|
| `OUTPUT` | Correlation links | outputVector |
| `N_LINKS` | Links | outputNumber |
| `N_FLAGGED` | Flagged links | outputNumber |

## Cluster holes into geological domains (`agsgeomodel:cluster_domains`)

*Group: Machine learning*

Explainable k-means clustering of the interpreted holes. Each hole is described by interpretable features (cover thickness by domain, rockhead level, presence of each bedrock unit, mean SPT N in the top 10 m), standardised; k is chosen by the silhouette score. Each cluster is explained by the features that differ most from the site mean (in original units) and compared with the rule-based engineering-geological domain of each hole, so disagreements are visible. Clusters are suggestions for review, not a replacement for the geological domains.

| Parameter | Description | Type | Default |
|---|---|---|---|
| `PROJECT_IN` | Project folder | file |  |
| `K_MAX` | Maximum number of clusters | number | 6 |
| `OUTPUT` | Domain clusters | sink |  |

| Output | Description | Type |
|---|---|---|
| `OUTPUT` | Domain clusters | outputVector |
| `K` | Number of clusters | outputNumber |
| `SILHOUETTE` | Silhouette score | outputNumber |
| `AGREEMENT` | Agreement with rule-based domains | outputNumber |
