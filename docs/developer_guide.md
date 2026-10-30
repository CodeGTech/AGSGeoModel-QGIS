# Developer guide

## Set up

```bash
git clone https://github.com/YOUR-ORG/AGSGeoModel-QGIS.git && cd AGSGeoModel-QGIS
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,geo,io]"         # core needs only NumPy; extras enable optional features
pytest                                  # core suites, performance excluded
ruff check . && ruff format --check .
```

For the QGIS suite and for running the plugin from source, use a QGIS Python environment, for example
conda-forge:

```bash
micromamba create -n qgis -c conda-forge python=3.12 qgis=3.40 pytest hypothesis
micromamba activate qgis
QT_QPA_PLATFORM=offscreen python -m pytest tests/qgis
```

and link `agsgeomodel/` into a QGIS profile (see [installation](installation.md)). The *Plugin Reloader*
plugin reloads it after code changes.

## Repository layout

```
agsgeomodel/          the plugin package (also the installable `agsgeomodel` Python package)
  plugin.py, gui/, algorithms/      QGIS shell: the only code that imports qgis
  workflows.py, pipeline.py         orchestration shared by GUI, Processing and CLI
  core/ models/ importers/ geology/ modelling/ processors/ hydrogeology/ hdd/ bgs/ ai/
  correlation/ visualisation/ exports/ database/ reports/ examples/
  icons/ gui/ui/ help/ metadata.txt
tests/                unit/ property/ integration/ qgis/ performance/ fixtures/ (offline BGS)
docs/                 Markdown (MyST) documentation, diagrams (Mermaid sources + SVG), images
examples/             synthetic example project (NOT REAL DATA)
scripts/              package_plugin.py, gen_reference_docs.py
.github/              CI, release workflow, issue templates
```

See [architecture](architecture.md) for how the layers fit together.

## Rules that keep the project maintainable

1. **No `qgis` imports outside `plugin.py`, `gui/` and `algorithms/`.** The core runs and is tested without
   QGIS. QGIS services reach the core through the hooks installed by `gui/qgis_bridge.install()`
   (coordinate transformer factory, HTTP fetcher).
2. **One code path.** A GUI action and its Processing algorithm call the same function in `workflows.py`
   or `pipeline.py`; put new behaviour there first, with a unit test, then expose it.
3. **Never modify input data.** Interpretations, decisions and findings are stored next to the data; AGS
   files are read, never written back.
4. **Geology is data.** Units, aliases, signatures, province columns and rule thresholds live in JSON and
   configuration dataclasses, not in code.
5. **Optional dependencies are optional.** Import them with `core.optional.optional_import()` (returns
   `None`) or `require()` (raises `DependencyMissing` with the package to install), never at module level.
6. **Explain every output.** New findings carry candidate explanations and a suggested action; new model
   outputs carry σ, a confidence and an interpretation class.
7. **Qt6-ready Qt code.** Use `qgis.PyQt`, fully qualified enums (`Qt.AlignmentFlag.AlignLeft`), `exec()`,
   `Qgis.*` enums and `QMetaType` field types.
8. **Threads.** Work longer than a fraction of a second runs in a `gui.tasks.FunctionTask` (or a Processing
   algorithm). Never touch QGIS GUI objects or layers from the worker function; return data and apply it in
   the `on_done` callback on the main thread. Each worker opens its own GeoPackage connection.
9. **Errors.** Raise subclasses of `core.errors.AGSGeoModelError` (`AGSParseError`, `ConfigError`,
   `GeologyError`, `ModelError`, `ConnectorError`, `StoreError`, `DependencyMissing`…); the GUI and the
   algorithms turn them into messages, everything else is a bug and is logged with its traceback.

Style: Python 3.12, type hints on public functions, dataclasses for data, `ruff` (line length 120) for lint
and formatting.

## Common extensions

**A stratigraphic unit or province column.** Edit `geology/knowledge/data/uk_units.json` or add
`provinces/<id>.json`; use BGS Lexicon codes and cite the source of thickness and presence priors in
`notes`. `KnowledgeBase.validate()` and `StratColumn.check_age_order()` run in the tests. Regenerate the
reference with `python scripts/gen_reference_docs.py`.

**A validation rule.** Add a function to `geology/validation.py` (geology) or `importers/ags/validator.py`
(AGS) that returns `Issue` objects with a rule id, severity, holes, depth, message, explanations and a
suggestion. Add a test that triggers it and one that does not, and add the rule to
[geological rules](geological_rules.md) or [AGS support](ags_support.md).

**An interpolation method.** Implement `fit(xy, z, sigma)` and `predict(xy, return_std=True)` (see
`modelling/interpolators.py`), register it in `make_interpolator()`, and calibrate a standard deviation
(deterministic methods use the leave-one-out proxy) so the uncertainty classes stay meaningful.

**An export format.** Write it from `exports.mesh.TriMesh` objects in `exports/formats3d.py` (3D) or from
GeoJSON-like features in `exports/formats2d.py` (2D), add it to `MODEL_FORMATS` / `TABLE_FORMATS`, and test
that it opens with a reader that does not share code with the writer.

**A Processing algorithm.** Subclass `algorithms.base.AGSGeoModelAlgorithm` (set `GROUP`, `NAME`, `TITLE`,
`HELP`, `ICON`), call a `workflows` function in `processAlgorithm`, add the class to `ALGORITHMS` in
`algorithms/provider.py` and to the id set in `tests/qgis/test_plugin.py`, then regenerate
`docs/reference/processing.md`.

**A GeoPackage table or column.** Never edit a migration that has been released. Add a new
`TABLES_V<n>` list and a migration to `MIGRATIONS` in `database/schema.py`, increase `SCHEMA_VERSION`, and
add store methods and a round-trip test. Existing projects are migrated when they are opened (a backup is
made first).

## Documentation

Pages are Markdown (MyST) in `docs/`. Diagrams are Mermaid sources in `docs/diagrams/*.mmd` rendered to SVG
with the Mermaid CLI (`mmdc -i x.mmd -o x.svg`). Reference pages are generated from the code:

```bash
python scripts/gen_reference_docs.py     # inside a QGIS Python environment (processing.md needs QGIS)
make docs                                 # Sphinx HTML in docs/_build/html
```

The plugin's offline help (`agsgeomodel/help/index.html`) is rendered from `docs/user_guide.md` by the
packaging script.

## Release

1. Update `agsgeomodel/__init__.py` (`__version__`), `metadata.txt` (`version`, `changelog`) and
   `CHANGELOG.md`; the packaging script refuses mismatched versions.
2. `python scripts/package_plugin.py` builds `dist/AGSGeoModel-<version>.zip` (only the plugin package,
   without tests, caches or development files) and renders the offline help.
3. Tag `v<version>` and push the tag: `.github/workflows/release.yml` checks that the tag matches the
   version, builds the zip, attaches it to a GitHub release and, when the `OSGEO_USERNAME` /
   `OSGEO_PASSWORD` repository secrets are set, uploads it to plugins.qgis.org.

Versioning follows semantic versioning; the GeoPackage schema version is independent and only moves
forward.

## Licences of dependencies and data

The plugin is GPL-2.0-or-later (as QGIS). Optional dependencies keep their own licences (GemPy EUPL-1.2,
python-ags4 LGPL-3.0, LightGBM MIT, XGBoost Apache-2.0, scikit-learn BSD). The bundled AGS4 dictionary subset
comes from the dictionaries distributed with python-ags4. BGS data are retrieved at run time under the Open
Government Licence and are not bundled; the test fixtures are synthetic.
