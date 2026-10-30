# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/)
and the project uses [semantic versioning](https://semver.org/). The GeoPackage schema version (currently 3)
is independent of the plugin version.

## [0.1.0] — first public release

### Data and validation
- AGS4 (4.0.x to 4.2) reader and writer; AGS3 (3.1) conversion of the core groups with every rename and
  unit conversion reported; AGS in Excel; CSV / Excel borehole tables.
- Validation of the AGS4 format rules (1-5, 7-10, 13-17, 19, 20) against a bundled v4.1.1 / v4.2
  dictionary subset (complete dictionaries when `python-ags4` is installed) and fourteen engineering checks
  (E00-E15) that matter for modelling.
- Evidence-based CRS and vertical-datum detection with confidence and confirmation in the import wizard;
  transformations through QGIS/PROJ (OSTN15 when installed).
- Generic mapping of laboratory groups with numeric headings, PSD indices from GRAT.

### Interpretation
- GeoReasoner: order-constrained hidden-Markov inference of the unit of every stratum over a knowledge base
  keyed to BGS Lexicon codes, with province columns for the London Basin and the East Anglian glacial
  margin; maximum-expected-accuracy decoding; missing-layer inference in unlogged intervals; second pass
  with leave-one-out elevation priors from neighbours.
- Geological rule catalogue (GEO-INV, CON, STA, OMI, MIS, THK, LOW, DIP, ELV, LOO, LOC) with candidate
  explanations and fault-aware severities.
- Reviewer workflow: overrides and acceptances stored in the project GeoPackage and applied on every run;
  an acceptance lapses when the proposal changes.

### Modelling and outputs
- 2.5D model: cover sequences as thickness fields, erosional rockhead, bedrock reference surface with
  isochore stacking and residual kriging; inequality constraints (active set); fault barriers with throw
  correction of elevation fields; geological-map constraints with scale-dependent tolerance.
- Kriging with fitted or assumed variograms and measurement error; RBF, IDW and TIN alternatives with a
  calibrated uncertainty proxy.
- Observed / Interpolated / Inferred / Speculative classes, confidence and RAG on every surface;
  conditional Monte Carlo realisations (circulant embedding) and unit probabilities.
- Sections, fence diagrams and HDD long sections as vector SVG / PDF, in the GeoPackage and in 3D.
- HDD ground-risk assessment (running sand, groundwater, boulders, rock, mixed face, loss of circulation
  with Delft frac-out screening) with geological confidence reported separately.
- Groundwater per aquifer system with perched-water detection, confinement, flow vectors and seepage
  paths; CPT (Robertson 2009/2010) and SPT interpretation; engineering ground model with cautious means and
  5 % fractiles.
- Exports: GeoPackage, GeoJSON, Shapefile, CSV, Excel; VTK, VTK voxels, OBJ, PLY, glTF, DXF, IFC4; HTML
  report with provenance.

### BGS and machine learning
- BGS OGC API (625k geology, faults, borehole and AGS indexes, landslides, map sheets) and AGS API
  connector with offline cache; province suggestion; explainable confidence ranking of BGS boreholes and
  merging of BGS AGS data as secondary control.
- Trainable, explainable formation-evidence model (NumPy, scikit-learn, LightGBM, XGBoost) with model card
  and calibration; correlation in five modes; explainable domain clustering.

### QGIS
- Project panel (project, review, issues, runs), import wizard, BGS dialog, settings, section and HDD map
  tools, background tasks, styled layers with attribute forms and 3D renderers, offline help.
- Twelve Processing algorithms covering every workflow step.
- Synthetic example project (NOT REAL DATA) with a known truth.
