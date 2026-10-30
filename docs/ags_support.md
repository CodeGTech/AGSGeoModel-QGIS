# AGS support

AGSGeoModel reads AGS4 (4.0.x to 4.2) and AGS3 (3.1) files, validates them, and maps the groups
that matter for ground modelling onto its data model. Files are never modified: the reader records every
structural problem with its line number and keeps parsing where that is safe, because a partially
non-compliant file is still evidence.

## Reading

* **Encoding and syntax**: CSV with double-quoted fields, `"GROUP"`, `"HEADING"`, `"UNIT"`, `"TYPE"` and
  `"DATA"` rows. Non-ASCII characters, other line endings and badly quoted rows are reported, not fixed.
* **Values** keep their text form (`AGSTable.column`) and are converted on demand. Values are checked
  against their declared TYPE for `nDP`, `nSF`, `nSCI`, `DT` (against the format in the UNIT row), `YN`,
  `MC` and `DMS`; mismatches are reported (rule 8) and the value is still read where it is numeric.
* **AGS3** files are converted to AGS4 group and heading names for the core groups (`HOLE` → `LOCA`,
  `STCN` → `SCPT`, `WSTK` → `WSTG` with the time / level pairs converted to `WSTD`, `CLSS` → `LLPL`; groups
  such as `GEOL`, `ISPT` and `SAMP` keep their names). Each rename, unit conversion (cone resistance, sleeve
  friction and pore pressures to MPa) and unmapped heading is reported, so the conversion is auditable. Fields AGS3
  does not have (such as `SAMP_ID`) are left empty, never invented.
* **AGS in Excel** (one sheet per group) and flat CSV / Excel tables (locations and intervals) are also
  accepted (`importers/other.py`).

## Validation

`validate_ags()` returns a `ValidationReport`: the findings, their counts by severity and a
`usable_for_modelling` flag (no critical findings). The command line version is
`python -m agsgeomodel validate FILE.ags [--json]` (exit code 2 when the file is not usable).

### AGS4 format rules

Rule numbers follow the AGS4 rules as implemented by the AGS Data Format Working Group's `python-ags4`
checker. This validator concentrates on the defects that change an interpretation; for formal
submission, also run the official checker (`run_official_checker`, when `python-ags4` is installed).

| Rule | Severity | Check |
|---|---|---|
| AGS4-1 | warning | non-ASCII characters |
| AGS4-2 | error | group without DATA rows |
| AGS4-2a | warning | lines not terminated by CR+LF |
| AGS4-2b | error | descriptor rows before any GROUP row; group without UNIT or TYPE row |
| AGS4-3 | error | line starting with an unknown data descriptor |
| AGS4-4 | error | unparseable lines; rows with a different number of fields than the HEADING row; DATA before HEADING |
| AGS4-5 | error | fields not all enclosed in double quotes or not separated by commas |
| AGS4-7 | error / warning | duplicate headings (error); headings not in dictionary order (warning) |
| AGS4-8 | warning | values that do not match the TYPE of their heading |
| AGS4-9 | error | heading not in the dictionary or the file's DICT group; group not in the dictionary or DICT (error with a complete dictionary, information with the bundled subset) |
| AGS4-10a | error | key fields missing; duplicate key combinations |
| AGS4-10b | error | required fields empty |
| AGS4-10c | error | parent group or parent record missing |
| AGS4-13 | error | PROJ missing or not exactly one DATA row |
| AGS4-14 | error | TRAN missing or not exactly one DATA row |
| AGS4-15 | error | UNIT missing, or a unit used but not defined in UNIT |
| AGS4-16 | warning | abbreviation not defined in ABBR (pick-list fields); PA fields without an ABBR group |
| AGS4-17 | error | TYPE missing, or a type used but not defined in TYPE |
| AGS4-19 / 19a | error | invalid group name; heading not of the form `GROUP_XXXX` (at most 9 characters) |
| AGS4-20 | warning | associated file set (`FILE_FSET`) not found next to the file |

**Dictionary.** A subset of the AGS4 Standard Dictionary (v4.1.1 and v4.2) covering the groups used for
modelling is bundled (`importers/ags/data/`, taken from the dictionaries distributed with the LGPL-3.0
`python-ags4` library). The edition is taken from `TRAN_AGS`. With `python-ags4` installed its complete
dictionaries are used, so rules 7 and 9 are checked for every group.

### Engineering checks

These are not AGS format rules, but each one routinely invalidates a ground model if it is missed.

| Check | Severity | Finding |
|---|---|---|
| E00 | critical | no LOCA group (locations cannot be placed) or no GEOL group (nothing to model) |
| E01 | error | location without coordinates |
| E02 | warning | no ground level (`LOCA_GL`): depths cannot be converted to elevations unless a DTM is supplied |
| E03 | error | GEOL interval without numeric top / base, or top ≥ base |
| E04 | error / warning | overlapping GEOL intervals (error); first interval not at ground level, gaps between intervals (warning) |
| E05 | warning | GEOL base below the final depth `LOCA_FDEP` |
| E07 | warning | two locations within 0.5 m: possible duplicate or re-drilled hole |
| E08 | error | mixed vertical datums in `LOCA_DATM` |
| E09 | warning | SPT below the final depth |
| E10 | warning | CPT depths not monotonic |
| E11 | error / warning | depths not in metres (error); cone resistance unit not recognised, MPa assumed (warning) |
| E12 | warning | empty `GEOL_DESC` |
| E13 | info | `HORN` (inclination) present: holes are treated as vertical in this version |
| E15 | warning | water strike below the final depth |

Geological checks (sequence, dips, thicknesses, neighbours) run after interpretation and are described in
[geological rules](geological_rules.md).

## Coordinate reference systems and datums

`detect_crs()` reports a CRS **with its basis and confidence**; the import wizard always shows it and asks
for confirmation when it is not certain.

| Evidence | Result | Confidence |
|---|---|---|
| EPSG code in `LOCA_GREF` / `LOCA_LLZ` | that code | 0.95 |
| `LOCA_GREF` names ITM / Irish Grid / British National Grid (OSGB, BNG, OSGB36…) | EPSG:2157 / 29903 / 27700 | 0.95 |
| UTM zone named (ETRS89, ED50 or WGS84) | EPSG:258zz / 230zz / 326zz (327zz south) | 0.85 |
| `LOCA_NATE`/`LOCA_NATN` within the British National Grid range | EPSG:27700 | 0.75, or 0.55 when the values also fit an Irish grid (alternatives offered) |
| values that look like UTM, zone unknown | none: the user is asked | 0.3 |
| `LOCA_LAT`/`LOCA_LON` (decimal or DMS) | EPSG:4326 (ETRS89 offered) | 0.8 |
| only local grid coordinates (`LOCA_LOCX`/`LOCY`) | none: a transformation is needed | 0 |

Coordinates are transformed to the project CRS through QGIS (so PROJ grids such as OSTN15 are used when
installed), else `pyproj`, else a documented Helmert approximation (a few metres), which is stated in the
run notes.

The **vertical datum** comes from `LOCA_DATM` (ODN / Ordnance Datum Newlyn, LAT, CD, MSL; other values
kept verbatim). Without it, ODN is assumed for British National Grid data and Malin Head for Irish grids,
and the assumption is recorded. Mixed datums are an error (E08) because levels cannot be combined.

## Groups used for modelling

| Group | Use |
|---|---|
| PROJ, TRAN, UNIT, TYPE, ABBR, DICT, FILE | file metadata, units, types, abbreviations, user-defined groups, associated files |
| LOCA | locations: position, ground level, final depth, type (borehole, trial pit, window sample, CPT, probe) |
| GEOL | strata: depths, description, `GEOL_LEG`, `GEOL_GEOL`, `GEOL_GEO2`, `GEOL_BGS` (Lexicon code), `GEOL_FORM` |
| HDPH | drilling method and depths |
| CORE | recovery (TCR, SCR, RQD) |
| ISPT | SPT N, seating and test drives, hammer energy ratio (`ISPT_ERAT`), refusals |
| SCPG, SCPT | CPT / CPTu test details (area ratio) and readings (qc, fs, u2) |
| WSTG, WSTD | water strikes and their rise / rest levels |
| MONG, MOND | monitoring installations (response zone) and readings |
| SAMP | samples (depths and references for laboratory results) |
| LLPL, LNMC, LDEN, GRAG, TRIT, CONS, SHBT | index, density, grading summary, triaxial, consolidation and shear box results |
| GRAT | particle size distribution: D10, D30, D50, D60, Cu, Cc, fines (< 0.063 mm), sand and gravel (> 2 mm) fractions are derived |
| GCHM | geochemistry (included in the ground model for known parameters) |
| **any other group** with `LOCA_ID` and a sample or specimen depth (`SAMP_TOP` or `SPEC_DPTH`) | numeric headings are kept as generic laboratory results with their AGS heading, unit and type, and appear in the ground model statistics |

The generic rule covers standard groups that have no dedicated mapping and project-specific groups
defined in DICT (some contractors issue, for example, `LABG` or `GREM`; these are not AGS4 standard
groups). Only headings of numeric TYPE (`nDP`, `nSF`, `nSCI`, `XN`, `MC`, `RL`) are used.

## Writing

`write_ags4()` writes AGS4 tables (CR+LF, quoted fields, HEADING, UNIT and TYPE rows); the synthetic example
and the tests use it. BGS AGS downloads are stored byte for byte in the project folder (`bgs_ags/`). The
plugin never writes to the user's AGS files.
