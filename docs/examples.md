# Examples

> **The example data are synthetic.** They are geologically plausible for the central and eastern London
> Basin but describe no real site, and the BGS responses used by the tests are synthetic fixtures. Do not use
> them for design.

## Synthetic Thames-side HDD crossing

A 1.2 km × 0.8 km site on the British National Grid (origin E 545 000, N 180 000) with an HDD crossing
under a railway and a creek. The "truth" is analytic, so every output can be compared with it
([verification](verification.md)).

| | |
|---|---|
| Stratigraphy | Topsoil and Made Ground; Alluvium (with a scour hollow) and River Terrace Deposits; London Clay Formation, Harwich Formation, Lambeth Group, Thanet Formation, Chalk Group, dipping gently north |
| Structure | normal fault F1 (NNE trend), throw 4 m, downthrown to the east |
| Investigation | 14 cable-percussion / rotary boreholes (40-45 m, SPT, samples, laboratory tests, standpipes), 6 CPTu (20-22 m), 4 trial pits, 3 window samples |
| Groundwater | shallow water in the River Terrace Deposits; deep (Lambeth / Thanet / Chalk) system confined by the London Clay |
| Planted defects | BH07 no recovery through the Lambeth Group; BH10 Thanet Formation logged as London Clay; BH12 chalk fill over Alluvium not called made ground; TP03 0.1 m gap between strata |
| Infrastructure | HDD-01 (≈ 500 m, design level −14 m OD, 250 mm product) passing under a railway (6 m clearance required) and a creek (4 m) |

### Run it

* **In QGIS**: *Plugins › AGSGeoModel › Create synthetic example project…*, choose an empty folder. The
  project is built in the background (about 20 s) and opened in the panel with all layers.
* **Command line**: `python -m agsgeomodel example /path/to/folder` writes the inputs, runs the pipeline and
  leaves a project folder in `/path/to/folder/output`.
* **From the repository copy**: `python -m agsgeomodel run examples/synthetic_thames/pipeline_config.json`
  (paths in the configuration are relative to the file).

### Inputs (`examples/synthetic_thames/input/`)

| File | Content |
|---|---|
| `synthetic_thames_hdd.ags` | AGS4 file: PROJ, TRAN, UNIT, TYPE, ABBR, LOCA, HDPH, GEOL, CORE, ISPT, SAMP, LLPL, LNMC, GRAG, GCHM, WSTG/WSTD, MONG/MOND, SCPG/SCPT |
| `dtm_5m.asc` + `.prj` | 5 m DTM (ESRI ASCII grid, EPSG:27700) |
| `faults.geojson` | fault F1 with `throw_m` and `downthrown_side` |
| `hdd_alignment.geojson` | HDD-01 plan alignment |
| `crossings.geojson` | features crossed by HDD-01 (railway, creek) with `min_clearance_m` |
| `superficial_map.geojson` | an illustrative superficial geology map (Alluvium / River Terrace Deposits); not used by the default configuration, add it in the project panel to try map constraints |
| `manifest.json` | what the generator wrote, the planted defects and the disclaimer |

### Outputs (`examples/synthetic_thames/output/`)

A copy of the main outputs is kept in the repository so they can be looked at without running anything:

| File | Content |
|---|---|
| `report.html` | interpretive report: data and validation, stratigraphy and interpretation, ground model with uncertainty, engineering parameters, hydrogeology, HDD assessment, provenance and limitations |
| `Section_A_A__along_HDD_01.pdf`, `Section_B_B__east_west_across_fault_F1.pdf` | cross sections (vector PDF) |
| `HDD-01_long_section.pdf`, `HDD-01_stations.csv` | HDD long section with RAG strips; station table |
| `model/model_units.glb`, `model/model_units.vtk`, `model/export_manifest.json` | 3D unit solids (glTF, VTK) with the local origin (exported at stride 2 to keep the repository small) |

A full run also writes `project.gpkg`, rasters, the fence diagram, every export format and the run notes;
these are not stored in the repository.

### Things to try

1. Open the **Review** tab: BH07's unlogged interval is proposed as Lambeth Group. Accept it, or override it,
   and run again: the decision is kept, and it lapses automatically if new data change the proposal.
2. Open **Issues**: BH10's logged code conflicts with the sequence (GEO-INV-01); BH12's chalk fill is
   explained as made ground; the dips between BH06 and BH14 are reported as information because F1 lies
   between them.
3. Remove the throw from F1 (or swap `downthrown_side`) and rebuild: Section B-B and the verification
   figures show the effect.
4. Draw a section with *Draw cross section* across the scour hollow (around E 545 300, N 180 200).
5. Move the HDD design level and re-assess with *Assess HDD alignment*: mixed-face and frac-out flags move
   with it.
6. Run *Correlate exploratory holes* in `nearest_neighbour` and `rules_constrained` modes and compare the
   links across F1.
