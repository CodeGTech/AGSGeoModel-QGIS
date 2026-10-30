# Contributing to AGSGeoModel

Thank you for helping. Geologists, engineers and developers are all welcome: a well-described geological
rule or a real-world AGS file that breaks the importer is as valuable as code.

## Reporting problems

* **Bugs**: open an issue with the *Bug report* template. Include the QGIS and plugin versions, the steps,
  and the run notes or the Python error (*View › Panels › Log Messages › AGSGeoModel*).
* **Geology**: use the *Stratigraphic rule / knowledge base change* template for a wrong interpretation, a
  missing unit or province, or a new check. Say which BGS Lexicon codes are involved and cite the source
  (BGS sheet explanation, memoir, published paper).
* **Data**: never attach confidential ground investigation data. Reduce the problem to a few holes, change
  coordinates and identifiers, and say so in the issue; the synthetic generator
  (`agsgeomodel.examples.synthetic`) is a good starting point for a shareable test case.

## Making a change

1. Fork, create a branch, and set up the development environment ([developer guide](docs/developer_guide.md)).
2. Write a test that fails without your change. Put headless logic in the core (no `qgis` imports outside
   `plugin.py`, `gui/` and `algorithms/`).
3. Run `ruff check . && ruff format --check .` and `pytest`; run the QGIS suite if you touched the QGIS
   shell.
4. Update the documentation that describes the behaviour (rule catalogues, methods, user guide) and
   `CHANGELOG.md`.
5. Open a pull request that explains *why*, with before / after evidence for geological changes (a section,
   a finding, a metric from the synthetic site).

Geological changes are reviewed for their evidence as well as their code. Changes that make the plugin
modify input data, or that let a learned model override the stratigraphic order, will not be accepted.

## Licence

By contributing you agree that your contribution is licensed under the GPL-2.0-or-later, the licence of the
project. Do not contribute code copied from projects with incompatible licences, or data you are not free
to publish.
