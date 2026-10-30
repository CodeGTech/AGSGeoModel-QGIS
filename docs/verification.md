# Verification

This page records how AGSGeoModel has been verified, the results, and what has **not** been verified. All
figures below can be reproduced from the repository (see the end of the page).

## 1. Automated tests

| Suite | Path | What it checks |
|---|---|---|
| Unit | `tests/unit/` | AGS reader and writer, AGS3 conversion, every validation rule, CRS and datum detection, transformations, description parser, classifier, sequence model, contacts, validation rules, variograms, kriging, barriers, inequality constraints, uncertainty classes, random fields, CPT / SPT correlations, ground model, hydrogeology, HDD geometry and risk rules, GeoPackage store and migrations, reviewer workflow, 2D/3D exports (closed and outward-oriented solids), drawings, BGS connector and ranking (offline fixtures), learned model, similarity, clustering, correlation |
| Property | `tests/property/` | invariants under random inputs (Hypothesis): the interpretation of random logs never violates superposition; raster-to-polygon conversion partitions the grid; chainages round-trip along polylines; kriging honours its data and its variance stays bounded; point-in-polygon on a known square |
| Integration | `tests/integration/` | the full pipeline on the synthetic site, project folders, re-runs with reviewer decisions, BGS gap filling end to end, the command line |
| QGIS | `tests/qgis/` | plugin load and unload, all Processing algorithms, transformations through QGIS/PROJ, layer loading with styles, forms and 3D renderers, dialogs built from `.ui` files, the section capture tool and background tasks (headless, `QT_QPA_PLATFORM=offscreen`) |
| Performance | `tests/performance/` (marker `performance`) | 1 000 holes / more than 90 000 intervals interpreted, a 5 m model of them built, with accuracy and time limits |

Results of the release run (Linux, Python 3.12; QGIS 3.40.3 with Qt 5.15 for the QGIS suite):

| Environment | Result |
|---|---|
| Core (NumPy only, no QGIS) | 136 passed, 3 skipped (the QGIS suite and two GDAL round-trip checks) |
| QGIS 3.40.3 (conda-forge) | 141 passed, 3 skipped (shapely and openpyxl cross-checks, not installed there) |
| Performance | 2 passed: 1 000 holes (> 90 000 intervals) interpreted in about 25 s with metre-weighted accuracy > 0.98; 5 m model of them in about 12 s |
| Plugin zip | installed into a clean profile and loaded by `qgis.utils`: 12 algorithms registered, validation and import run from the installed copy, clean unload |

Continuous integration (`.github/workflows/ci.yml`) runs lint (ruff), the core tests on Python 3.12 and 3.13
with and without optional packages, the QGIS suite in a QGIS 3.40 environment, the documentation build
(warnings are errors), the packaging check and, on `main`, the performance tests, on every push to `main`
and on every pull request.

## 2. Synthetic site with a known truth

`agsgeomodel.examples.synthetic` generates a Thames-side site (**synthetic data, not a real site**):
Made Ground, Alluvium with a scour hollow, River Terrace Deposits, over a north-dipping London Clay,
Harwich Formation, Lambeth Group, Thanet Formation and Chalk, cut by a normal fault F1 with a 4 m throw
(downthrown to the east). It has 14 boreholes, 6 CPTs, 4 trial pits and 3 window samples (27 locations,
151 logged intervals) and four planted defects. Because the truth is analytic, interpretation and model can
be scored against it. Settings: `london_basin` column, 10 m cells, 150 m buffer, fault F1 with its throw,
40 realisations.

### Interpretation

| Measure | Result |
|---|---|
| Metre-weighted accuracy of the interpreted unit (all logged intervals) | 1.00 |
| Import / interpretation time | 0.2 s / 0.1 s |

| Planted defect | Found by |
|---|---|
| BH07: no recovery 14.85-20.44 m (Lambeth Group not logged) | GEO-MIS-01: inferred as Lambeth Group 14.8-20.4 m (p = 0.83), offered for review |
| BH10: Thanet Formation logged with `GEOL_GEOL = LC` | GEO-INV-01 (error) and GEO-STA-01: the consistent interpretation is TAB |
| BH12: chalk fill over Alluvium described without the words "made ground" | GEO-INV-02 and GEO-CON-01: interpreted as Made Ground, with the conflict explained |
| TP03: 0.1 m gap between strata | E04 (AGS engineering check) |

The only other findings are two GEO-DIP-01 notes between BH06 and BH14, reported as information because the
mapped fault F1 lies between them, which is the correct behaviour.

### Model surfaces

RMSE of modelled minus true elevation over the whole grid and inside the investigated area (80 m inside the
data extent), the fraction of nodes whose error is within 2σ, and the share of interpretation classes
(Observed / Interpolated / Inferred / Speculative):

| Surface | RMSE all (m) | RMSE inside (m) | Within 2σ | O / I / Inf / S |
|---|---|---|---|---|
| Rockhead | 1.00 | 0.72 | 98.4 % | 0.00 / 0.26 / 0.74 / 0.00 |
| Base of London Clay | 1.57 | 0.79 | 100 % | 0.00 / 0.03 / 0.94 / 0.03 |
| Base of Harwich Formation | 1.50 | 0.68 | 100 % | 0.00 / 0.05 / 0.93 / 0.02 |
| Base of Lambeth Group | 0.98 | 0.42 | 100 % | 0.00 / 0.23 / 0.76 / 0.00 |
| Base of Thanet Formation | 1.05 | 0.58 | 100 % | 0.00 / 0.00 / 0.19 / 0.81 |

Observed nodes are a very small share of the grid (27 holes on 11 520 nodes), so they round to 0.00. The
base of the Thanet Formation is mostly *Speculative* because few holes reach it, which is what the classes
are meant to show. The standard deviations are conservative: errors fall within 2σ more often than the
nominal 95 %. The unit tests require the inside RMSE to stay below 1.6-2.5 m depending on the surface and
more than 90 % of nodes within 2σ.

Model build: 3-4 s for a 90 × 128 grid; 40 conditional realisations: about 2 s.

### Faults

Effect of the fault definition on the inside RMSE (m):

| Fault F1 | Rockhead | Base LC | Base HWH | Base LMBE | Base TAB |
|---|---|---|---|---|---|
| barrier, throw unknown | 0.72 | 0.85 | 0.75 | 0.48 | 0.54 |
| **throw 4 m, downthrown east (truth)** | 0.72 | 0.79 | 0.68 | 0.42 | 0.58 |
| throw 4 m, wrong side | 0.72 | 1.37 | 1.28 | 1.29 | 1.41 |

Rockhead is unaffected, as it should be (the cover post-dates faulting). The correct throw improves the
bedrock surfaces; a wrong downthrown side is clearly worse, which is why the throw is applied only to
elevation fields and recorded with every run. (Before release, a bug that also applied the throw to the
kriging residuals was found with this test and fixed; it had raised the inside RMSE of the base of the
Thanet Formation to 2.0 m.)

### End to end

The bundled example (`examples/synthetic_thames`, also *Create synthetic example project…* in the plugin)
runs the complete pipeline in about 20 s: interpretation, model, 40 realisations, CPT / SPT, ground model,
hydrogeology, two sections, a fence diagram, the HDD assessment, exports and the report. The HDD-01 bore
(505 m long, 375 mm reamed diameter, minimum cover 10.7 m) is rated Red overall: running sand and likely
loss of circulation where the entry and exit tangents pass through saturated River Terrace Deposits,
running sand in the Harwich Formation, and mixed-face conditions where the bore runs close to contrasting
contacts (98 of 503 stations Red, 364 Amber).

## 3. QGIS

Verified headless on QGIS 3.40.3 (Qt 5.15, Python 3.12, conda-forge): the plugin loads and unloads cleanly;
all Processing algorithms run; layers load with their styles, value-map forms and 3D renderers; the dock
(project, review, issues, runs), import wizard, BGS and settings dialogs build from their `.ui` files; the
section capture tool and background tasks work. Screenshots in this documentation were taken from that
environment.

## 4. Not verified

* **Real projects.** The numbers above come from synthetic data. Interpretation quality on real logs depends
  on description quality and on how well the province column fits; review every project.
* **Live BGS services.** The BGS integration was developed against the published service structure and
  tested with offline fixtures (synthetic content). Network access to BGS was not available in the release
  environment; check the first live run (Settings › BGS services shows the endpoints).
* **QGIS 4 / Qt 6**, **Windows** and **macOS** have not been tested. The code follows the Qt6-compatible
  conventions (fully qualified enums, `exec()`, `qgis.PyQt`).
* **GemPy backend**: the adapter follows GemPy's documented conventions but is not covered by the test
  suite (GemPy is not installed in CI); treat it as experimental.
* **Engineering correlations** are implemented from the cited papers and tested against the documented
  equations and limits (SBTn boundaries, the undrained Delft limit, Student-t fractiles); they have not
  been benchmarked against a commercial CPT package.
* **HDD frac-out screening** uses hydrostatic mud pressure unless annular losses are supplied; it is a
  screening check, not a design.

## 5. Reproducing

```bash
pip install -e ".[dev]"
pytest                                   # core suites (performance excluded by default)
pytest -m performance                    # performance suite
python -m agsgeomodel example /tmp/ex    # the example project end to end
# QGIS suite, inside a QGIS Python environment:
QT_QPA_PLATFORM=offscreen python -m pytest tests/qgis
```
