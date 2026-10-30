# Installation

## Requirements

| | Minimum | Notes |
|---|---|---|
| QGIS | 3.40 LTR | tested on 3.40.3 (Qt 5.15). The code follows the Qt6 conventions, but QGIS 4 is not declared (`qgisMaximumVersion=3.99`) until it has been tested |
| Python | 3.12 | the Python that ships with QGIS |
| NumPy | 1.26 | always present in QGIS |

Everything else is optional and only enables extra features:

| Package | Enables |
|---|---|
| `scipy` | faster neighbour searches (KD-tree) on very large projects |
| `pyproj` | coordinate transformations outside QGIS (inside QGIS, QGIS/PROJ is used) |
| `openpyxl` | Excel import and the Excel workbook export |
| `laspy` | LiDAR (binary LAS) to DTM |
| `scikit-learn`, `lightgbm`, `xgboost` | alternative learners for the trainable formation model (a NumPy learner is built in) |
| `sentence-transformers` | embedding-based description similarity (TF-IDF is built in) |
| `python-ags4` | the AGS Data Format Working Group checker (complete AGS4 rule 7/9 dictionary checks) |
| `gempy` | implicit 3D modelling backend (EUPL-1.2, installed separately) |
| `pyvista`, `trimesh` | viewing and checking 3D exports outside QGIS |

## Install the plugin

1. Download `AGSGeoModel-<version>.zip` (release page, or build it: `python scripts/package_plugin.py`).
2. In QGIS: **Plugins › Manage and Install Plugins… › Install from ZIP**, choose the zip, **Install Plugin**.
3. The **AGSGeoModel** toolbar appears, the panel opens from the first button, and the algorithms are in the
   **Processing toolbox › AGSGeoModel**.

To develop, link the source folder into your QGIS profile instead of installing the zip:

```bash
# Linux / macOS (default profile)
ln -s "$PWD/agsgeomodel" ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/agsgeomodel
# Windows (PowerShell, as administrator)
New-Item -ItemType SymbolicLink -Path "$env:APPDATA\QGIS\QGIS3\profiles\default\python\plugins\agsgeomodel" -Target "$PWD\agsgeomodel"
```

Restart QGIS (or use the *Plugin Reloader* plugin) after code changes.

## Optional packages inside QGIS

* **Windows (OSGeo4W / standalone installer):** open the *OSGeo4W Shell* and run
  `python -m pip install openpyxl scikit-learn lightgbm xgboost`.
* **macOS (official installer):** `/Applications/QGIS.app/Contents/MacOS/bin/python3 -m pip install --user openpyxl scikit-learn`.
* **Linux (distribution packages):** `python3 -m pip install --user openpyxl scikit-learn` or the
  distribution's packages (`python3-openpyxl`, `python3-sklearn`).
* **conda-forge:** `conda install -c conda-forge qgis openpyxl scikit-learn lightgbm xgboost`.

Missing packages never stop the plugin: the feature that needs them reports which package to install.

## Accurate OSGB36 / ETRS89 transformations (OSTN15)

British National Grid data are often combined with WGS84/ETRS89 data (BGS services, GPS). Inside QGIS all
transformations go through QGIS/PROJ, so they use the transformation grids that QGIS knows about. Install
the **OSTN15** grid once (QGIS shows a warning with a download link the first time it needs it, or
*Settings › Options › Transformations*) for centimetre-level accuracy; without it PROJ falls back to a
seven-parameter Helmert transformation (a few metres). Outside QGIS, `pyproj` is used when installed,
otherwise a built-in Helmert approximation (also a few metres) that is reported in the run notes.

## Network and BGS services

The BGS dialog and the *BGS geology and borehole discovery* algorithm contact `ogcapi.bgs.ac.uk` and
`agsapi.bgs.ac.uk` through QGIS's network settings (proxy and authentication: *Settings › Options ›
Network*). Responses are cached (default `~/.agsgeomodel/http_cache.sqlite`, 30 days); with **offline
mode** (*Settings › BGS services*) only cached responses are used, so a project can be re-run and reviewed
without a network connection. No other part of the plugin needs network access.

## Headless use (no QGIS)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[geo,io]"           # or ".[all]"; core needs only NumPy
python -m agsgeomodel --help
pytest -m "not qgis"                  # core test suite
```

## Uninstall

*Plugins › Manage and Install Plugins › Installed › AGSGeoModel › Uninstall*. Project folders are ordinary
files (`project.gpkg`, rasters, drawings) and are not touched.
