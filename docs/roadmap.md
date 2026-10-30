# Roadmap

Version 0.1 is the first public release: the complete workflow from AGS data to reviewed interpretation,
constrained model, uncertainty, sections, HDD assessment, exports and report, for the London Basin and its
glacial margin. The items below are ordered by priority. Proposals are welcome as issues (the
*Stratigraphic rule / knowledge base change* template is for new rules, units and province columns).

## Known limitations of 0.1

| Limitation | Workaround |
|---|---|
| Holes are treated as vertical (`HORN` inclination is reported, not used) | use vertical holes, or convert inclined holes to true depths before import |
| Faults are vertical in plan (2.5D) | GemPy backend (experimental) for dipping faults |
| Bundled stratigraphy covers the London Basin and the East Anglian glacial margin | write a province column for other areas ([geological rules](geological_rules.md#7-extending-the-rules)) and select it in `agsgeomodel_project.json` |
| QGIS 4 / Qt 6, Windows and macOS not yet tested | use QGIS 3.40 LTR; the code follows the Qt6 conventions |
| BGS integration tested against offline fixtures only | check the first live run; endpoints can be changed in Settings |
| HDD frac-out screening uses hydrostatic mud pressure unless annular losses are supplied | enter `annular_loss_kpa_per_m` from the drilling contractor's hydraulics |
| Correlation and domain clustering are Processing algorithms only | run them from the toolbox or the graphical modeler |

## 0.2 — platforms and coverage

* **QGIS 4 / Qt 6**: run the QGIS suite on a Qt 6 build, add `supportsQt6`, raise `qgisMaximumVersion`.
* **Windows and macOS** CI jobs (OSGeo4W and the official macOS build).
* **Live BGS checks**: a scheduled CI job that calls the BGS services and reports changed endpoints or
  attributes (BGS labels the OGC API BETA).
* **More province columns**, each with its sources: Hampshire Basin, Weald, Thames Valley gravels west of
  London, Mercia Mudstone and Sherwood Sandstone (Midlands), Carboniferous Coal Measures, Scottish Midland
  Valley tills; a province editor in the settings dialog.
* **Inclined holes**: use `HORN` to compute true positions along the hole.
* **AGS output**: write the reviewed interpretation back as an AGS4 file (interpretation in a DICT-defined
  group) for exchange, without touching the original files.

## 0.3 — modelling

* **Dipping faults** in the native modeller (fault surfaces with dip and throw, offsetting the stack).
* **Lenses and channels**: discontinuous units inside a sequence (e.g. sand lenses in the Lambeth Group)
  modelled as indicator fields with their own uncertainty.
* **Anisotropic variograms** and a trend from the regional dip.
* **Volumes and quantities** per unit with uncertainty (e.g. for excavation and spoil classification).
* **GemPy backend**: tests in CI with GemPy installed; uncertainty from GemPy realisations.

## Later

* Scanned-log digitising aid for BGS borehole index records (transcription to AGS with provenance).
* Geophysics (resistivity, seismic refraction) as soft constraints.
* Tunnel and pipeline alignments other than HDD (open-cut, microtunnelling, auger bore) with their own
  hazard rules.
* Translations of the user interface.

## Not planned

* Editing or "repairing" AGS files: the plugin reports problems and keeps the data unchanged.
* Unconstrained machine-learning interpretation: learned models will stay evidence for the
  order-constrained GeoReasoner.
