# AGSGeoModel for QGIS

**Defensible geological interpretation and 3D ground models from AGS4 ground investigation data.**

AGSGeoModel is an open-source QGIS plugin (GPL-2.0-or-later) for geotechnical engineers and engineering
geologists. It reads AGS4 (and legacy AGS3) files, validates them, interprets every logged stratum
against a stratigraphic knowledge base keyed to BGS Lexicon codes, and builds sections, fence diagrams
and 3D surfaces that cannot connect units in geologically impossible ways. Every output carries an
uncertainty class (**Observed / Interpolated / Inferred / Speculative**) and a confidence value, and every
decision the software makes can be traced to its evidence and overridden by a named reviewer.

No ArcGIS, ArcPy or ESRI licence is needed. The core is pure Python + NumPy; QGIS provides the user
interface, map, 3D view and Processing integration.

> **Status: 0.1.0 (experimental).** The engine is tested against a synthetic site with a known geological
> truth and in QGIS 3.40.3. It has not yet been validated on a wide range of real projects: treat outputs
> as an interpretation aid that a competent geologist checks, never as a substitute for one.

![Exploratory holes, HDD risk zones, sections and rockhead confidence in QGIS](docs/images/map.png)

## What it does

| Area | Capability |
|---|---|
| **AGS data** | AGS4 reader/writer, AGS3 → AGS4 conversion, AGS4 format rules 1-5, 7-10, 13-17, 19-20 (dictionary subset bundled; complete dictionaries with `python-ags4`), engineering checks E00-E15, CRS and vertical datum detection, import wizard |
| **GeoReasoner** | BS 5930 description parsing; formation evidence from logged codes, names and descriptions; order-constrained sequence inference (superposition, unconformities, facies sequences); missing-layer inference in unlogged intervals; explainable probabilities; 14 geological consistency rules with candidate explanations |
| **Review** | Accept or override any flagged interpretation with name, reason and time; decisions survive re-runs; acceptances lapse automatically when new data change the proposal |
| **Modelling** | Kriging with inequality constraints (holes that end in a unit), fault barriers with throw correction, map-polygon and map-contact constraints with scale-dependent tolerance, isochore stacking, erosional rockhead, Monte Carlo realisations; RBF / IDW / TIN alternatives; optional GemPy backend |
| **Uncertainty** | Kriging standard deviation, confidence, the four uncertainty classes and RAG on every surface, section contact and HDD station |
| **Engineering** | CPT/CPTu interpretation (Robertson 2009/2010), SPT corrections and correlations, PSD indices, engineering ground model with cautious-mean and 5 % fractile characteristic values |
| **Hydrogeology** | Head surfaces per aquifer system, confinement (unconfined / confined / artesian / possible leakage), perched water, flow vectors and seepage paths |
| **HDD** | Design or explicit bore profiles; geology, rockhead and groundwater along the bore; RAG for running sand, groundwater, boulders, rock, mixed face and loss of circulation (Delft cavity expansion); crossing clearances; long sections |
| **BGS** | 625k bedrock, superficial and faults, landslides and map sheets (OGC API), Lexicon links, borehole discovery (Single Onshore Borehole Index, AGS index) with explainable confidence scores, AGS download and confidence-weighted merging |
| **AI** | Trainable formation model (NumPy, scikit-learn, LightGBM or XGBoost) used only as bounded evidence, with model card and calibration; description similarity; domain clustering; five correlation modes |
| **Outputs** | `project.gpkg` (OGC GeoPackage), GeoTIFF, MDAL 2DM, SVG/PDF sections and long sections, fence diagrams, VTK, OBJ, PLY, glTF, DXF, IFC4 (georeferenced), Shapefile, GeoJSON, CSV, Excel, HTML report |
| **QGIS** | Dock (project, review, issues, runs), import wizard, BGS dialog, settings, section and HDD map tools, styled layers with attribute forms and 3D renderers, 12 Processing algorithms |

## Quick start

1. **Install** the plugin zip in QGIS 3.40 LTR or a later 3.x release: *Plugins › Manage and Install Plugins › Install from ZIP*
   ([installation guide](docs/installation.md)).
2. Open the **AGSGeoModel** panel, choose an empty **project folder** and **Add AGS…** files
   (or *Plugins › AGSGeoModel › Create synthetic example project…* to try it with the bundled example).
3. Press **Run model**. Layers appear in the *AGSGeoModel* group; the report opens from the *Runs* tab.
4. Work through the **Review** and **Issues** tabs, then re-run: your decisions are applied.
5. Draw sections or HDD alignments with the map tools, or run any step from the Processing toolbox.

Without QGIS the same engine runs from Python or the command line:

```bash
pip install -e ".[geo,io]"
python -m agsgeomodel validate site.ags           # AGS4 rules + engineering checks
python -m agsgeomodel example ./example_project    # synthetic example (NOT REAL DATA), full pipeline
python -m agsgeomodel run ./example_project/output # re-run a project folder
```

```python
from agsgeomodel.importers.ags import import_ags
from agsgeomodel.geology import GeoReasoner

imp = import_ags("site.ags")
interp = GeoReasoner("london_basin").interpret(imp.project)
for iv in imp.project.intervals[:5]:
    print(iv.location_id, iv.top, iv.base, iv.final_unit, iv.probability, iv.review_reason)
```

## Documentation

| | |
|---|---|
| [Installation](docs/installation.md) | QGIS plugin, optional packages, OSTN15 grid, offline use |
| [User guide](docs/user_guide.md) | the end-to-end workflow in QGIS, review, BGS, sections, HDD, exports |
| [Processing reference](docs/reference/processing.md) | every algorithm, parameter and output |
| [Architecture](docs/architecture.md) | design principles, module layout, data flow |
| [Geological rules](docs/geological_rules.md) | knowledge base, sequence inference, rule catalogue |
| [Methods](docs/methods.md) | interpolation, constraints, uncertainty, CPT/SPT, ground model, hydrogeology, HDD |
| [AGS support](docs/ags_support.md) · [BGS integration](docs/bgs_integration.md) · [AI](docs/ai.md) | |
| [Data model](docs/reference/data_model.md) · [Knowledge base](docs/reference/knowledge_base.md) | generated from the code |
| [Examples](docs/examples.md) · [Verification](docs/verification.md) · [Developer guide](docs/developer_guide.md) · [Roadmap](docs/roadmap.md) | |
| [Python API](docs/api.md) · [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) | |

## Design principles

1. **Geology before geometry.** Interpolation only happens between observations that the GeoReasoner has
   shown to be the same unit, in an order the stratigraphic column allows.
2. **Nothing is silently fixed.** Inconsistencies become findings with candidate explanations; the data
   are never edited behind the user's back.
3. **Transparent and reproducible.** Every run records its inputs hash, configuration, knowledge-base
   version and random seed; every interpretation records its evidence.
4. **Explainable AI only.** Learned models add bounded evidence; they cannot overrule superposition.
5. **Open formats.** One GeoPackage per project, readable by any GIS; standard 3D and CAD/BIM exports.

## Licence and data

Code: GPL-2.0-or-later (see [LICENSE](LICENSE)). The bundled stratigraphic knowledge is compiled from
public BGS Lexicon information; BGS services are used under their terms of use and outputs that contain
BGS data carry the attribution *"Contains British Geological Survey materials © UKRI"*. BGS labels its
OGC API services as BETA. The synthetic example data and the BGS test fixtures are invented and labelled
**NOT REAL DATA**.

Groundhog (Bruno Stuyts) inspired the CPT module's structure; no Groundhog code is included. GemPy
(EUPL-1.2) is an optional, separately installed backend.
