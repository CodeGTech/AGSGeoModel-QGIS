#!/usr/bin/env python3
"""Generate reference pages from the code so the documentation cannot drift from it.

* docs/reference/data_model.md      - every project.gpkg table and field (schema migrations)
* docs/reference/knowledge_base.md  - stratigraphic units and province columns (BGS Lexicon codes)
* docs/reference/processing.md      - Processing algorithms, parameters and outputs (needs QGIS)

    python scripts/gen_reference_docs.py            # inside a QGIS Python environment for all three
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "reference"


def data_model() -> str:
    from agsgeomodel.database.schema import MIGRATIONS, TABLES_V1, TABLES_V2, TABLES_V3

    lines = [
        "# project.gpkg data model",
        "",
        "Generated from `agsgeomodel/database/schema.py` by `scripts/gen_reference_docs.py`. The file is a",
        "standard OGC GeoPackage (R-tree spatial indexes, `gpkg_contents`, `gpkg_geometry_columns`); every",
        "feature table opens directly in QGIS, GDAL/OGR and ArcGIS Pro. Schema changes are applied by numbered",
        "migrations recorded in `schema_migrations`:",
        "",
    ]
    for v, desc, _ in MIGRATIONS:
        lines.append(f"{v}. {desc}")
    lines.append("")
    for title, tables in (("Version 1", TABLES_V1), ("Version 2", TABLES_V2), ("Version 3", TABLES_V3)):
        lines += [f"## {title} tables", ""]
        for name, gtype, z, fields, desc in tables:
            geom = "no geometry" if gtype is None else f"{gtype}{' Z' if z else ''}"
            lines += [f"### `{name}`", "", f"{desc} ({geom}).", "", "| Field | Type |", "|---|---|"]
            lines += [f"| `{f}` | {t} |" for f, t in fields]
            lines.append("")
    lines += [
        "## Files next to the GeoPackage",
        "",
        "| Path | Content |",
        "|---|---|",
        "| `rasters/<run_id>/*.tif` | surfaces, standard deviations, uncertainty classes and confidence (GeoTIFF) |",
        "| `rasters/<run_id>/*.2dm` | surfaces as MDAL meshes |",
        "| `agsgeomodel_project.json` | the pipeline configuration "
        "(re-run with `python -m agsgeomodel run <folder>`) |",
        "| `sections/`, `hdd/` | drawings (SVG / PDF) and HDD station tables |",
        "| `exports/` | 3D exports and `export_manifest.json` |",
        "| `backups/` | GeoPackage snapshots taken before each re-run |",
        "",
    ]
    return "\n".join(lines)


def knowledge_base() -> str:
    from agsgeomodel.geology.knowledge import load_column
    from agsgeomodel.geology.knowledge.kb import available_provinces

    kb = load_column("london_basin").kb
    lines = [
        "# Knowledge base",
        "",
        "Generated from `agsgeomodel/geology/knowledge/data` by `scripts/gen_reference_docs.py`.",
        "",
        f"Knowledge base version `{kb.version}`: {len(kb.units)} units keyed to BGS Lexicon codes.",
        "",
        "| Code | Name | Class | Material | Age | Hydrogeology | Lexicon |",
        "|---|---|---|---|---|---|---|",
    ]
    for code in sorted(kb.units):
        u = kb[code]
        link = f"[{code}]({u.lexicon_url})" if u.lexicon_url else "-"
        lines.append(
            f"| `{code}` | {u.name} | {u.unit_class} | {u.material} | {u.age_label} | {u.hsu.value} | {link} |"
        )
    lines.append("")
    for prov in available_provinces():
        col = load_column(prov)
        lines += [
            f"## Province column `{prov}`",
            "",
            f"**{col.name}.** {col.description}",
            "",
            "| Sequence | Kind | Order | Units (top to base) |",
            "|---|---|---|---|",
        ]
        for s in col.sequences:
            lines.append(f"| `{s.id}` | {s.kind} | {s.order} | {' > '.join(s.codes)} |")
        if col.aquifer_systems:
            lines += ["", "Aquifer systems:", ""]
            for a in col.aquifer_systems:
                lines.append(
                    f"* `{a['id']}`: aquifer units {', '.join(a.get('aquifer_units', []))}; "
                    f"confined by {', '.join(a.get('confined_by', [])) or 'none'}"
                )
        lines += [
            "",
            f"Maximum apparent dip of conformable bedrock contacts {col.max_apparent_dip_deg} deg; "
            f"rockhead {col.rockhead_max_apparent_dip_deg} deg.",
            "",
        ]
    return "\n".join(lines)


def processing() -> str | None:
    try:
        from qgis.core import QgsApplication
    except ImportError:
        return None
    from qgis.testing import start_app

    start_app()
    from agsgeomodel.algorithms.provider import ALGORITHMS

    lines = [
        "# Processing algorithms",
        "",
        "Generated from `agsgeomodel/algorithms` by `scripts/gen_reference_docs.py` (QGIS "
        f"{__import__('qgis.core', fromlist=['Qgis']).Qgis.version()}).",
        "",
        "All algorithms are in the Processing toolbox under **AGSGeoModel**, can be chained in the graphical",
        'modeler and run from Python (`processing.run("agsgeomodel:<id>", {...})`) or the command line',
        "(`qgis_process run agsgeomodel:<id> -- KEY=value ...`).",
        "",
    ]
    for cls in ALGORITHMS:
        alg = cls()
        alg.initAlgorithm()
        lines += [
            f"## {alg.displayName()} (`agsgeomodel:{alg.name()}`)",
            "",
            f"*Group: {alg.group()}*",
            "",
            alg.shortHelpString(),
            "",
            "| Parameter | Description | Type | Default |",
            "|---|---|---|---|",
        ]
        for p in alg.parameterDefinitions():
            default = p.defaultValue()
            opt = " (optional)" if p.flags() & p.Flag.FlagOptional else ""
            lines.append(
                f"| `{p.name()}` | {p.description()}{opt} | {p.type()} | {'' if default is None else default} |"
            )
        outs = [o for o in alg.outputDefinitions()]
        if outs:
            lines += ["", "| Output | Description | Type |", "|---|---|---|"]
            lines += [f"| `{o.name()}` | {o.description()} | {o.type()} |" for o in outs]
        lines.append("")
    del QgsApplication
    return "\n".join(lines)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "data_model.md").write_text(data_model(), encoding="utf-8")
    (OUT / "knowledge_base.md").write_text(knowledge_base(), encoding="utf-8")
    proc = processing()
    if proc is not None:
        (OUT / "processing.md").write_text(proc, encoding="utf-8")
    print("written:", ", ".join(p.name for p in sorted(OUT.glob("*.md"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
