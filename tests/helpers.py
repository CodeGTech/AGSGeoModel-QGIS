"""Helpers to build small AGS4 files in tests."""

from __future__ import annotations

from agsgeomodel.importers.ags.reader import AGSTable, write_ags4

DEFAULT_TYPES = {
    "LOCA_ID": "ID",
    "LOCA_TYPE": "PA",
    "LOCA_NATE": "2DP",
    "LOCA_NATN": "2DP",
    "LOCA_GL": "2DP",
    "LOCA_FDEP": "2DP",
    "LOCA_GREF": "PA",
    "LOCA_DATM": "PA",
    "GEOL_TOP": "2DP",
    "GEOL_BASE": "2DP",
    "GEOL_DESC": "X",
    "GEOL_LEG": "PA",
    "GEOL_GEOL": "PA",
    "GEOL_GEO2": "PA",
    "GEOL_BGS": "PA",
    "GEOL_FORM": "X",
    "TRAN_ISNO": "X",
    "TRAN_AGS": "X",
    "PROJ_ID": "ID",
    "PROJ_NAME": "X",
    "ISPT_TOP": "2DP",
    "ISPT_NVAL": "0DP",
    "SAMP_TOP": "2DP",
    "SAMP_REF": "X",
    "SAMP_TYPE": "PA",
    "SAMP_ID": "ID",
    "WSTG_DPTH": "2DP",
    "HDPH_TOP": "2DP",
    "HDPH_BASE": "2DP",
    "HDPH_TYPE": "PA",
}
DEFAULT_UNITS = {
    "LOCA_NATE": "m",
    "LOCA_NATN": "m",
    "LOCA_GL": "m",
    "LOCA_FDEP": "m",
    "GEOL_TOP": "m",
    "GEOL_BASE": "m",
    "ISPT_TOP": "m",
    "SAMP_TOP": "m",
    "WSTG_DPTH": "m",
    "HDPH_TOP": "m",
    "HDPH_BASE": "m",
}


def table(
    name: str, headings: list[str], rows: list[list], units: dict | None = None, types: dict | None = None
) -> AGSTable:
    u = {**DEFAULT_UNITS, **(units or {})}
    t = {**DEFAULT_TYPES, **(types or {})}
    return AGSTable(
        name,
        headings,
        [u.get(h, "") for h in headings],
        [t.get(h, "X") for h in headings],
        [[("" if v is None else str(v)) for v in r] for r in rows],
    )


def tran(version: str = "4.1.1") -> AGSTable:
    return table("TRAN", ["TRAN_ISNO", "TRAN_AGS"], [["1", version]])


TYPE_DESC = {
    "ID": "Unique identifier",
    "PA": "Text listed in ABBR group",
    "X": "Text",
    "XN": "Text / numeric",
    "0DP": "Value; 0 decimal places",
    "1DP": "Value; 1 decimal place",
    "2DP": "Value; 2 decimal places",
    "3DP": "Value; 3 decimal places",
    "4DP": "Value; 4 decimal places",
    "DT": "Date time",
    "MC": "Moisture content",
    "RL": "Record link",
    "U": "Value with variable format",
}


def complete(tables: list[AGSTable], project_id: str = "TEST") -> list[AGSTable]:
    """Add PROJ, ABBR, TYPE and UNIT groups describing the given tables (AGS4 rules 13-17)."""
    names = {t.name for t in tables}
    out = [t for t in tables if t.name == "TRAN"]
    if "PROJ" not in names:
        out.append(table("PROJ", ["PROJ_ID", "PROJ_NAME"], [[project_id, "Test project (synthetic)"]]))
    body = [t for t in tables if t.name != "TRAN"]
    abbr, units, types = [], set(), set()
    for t in [*out, *body]:
        units.update(u for u in t.units if u)
        types.update(x for x in t.types if x)
        for k, (h, ty) in enumerate(zip(t.headings, t.types)):
            if ty == "PA":
                for v in sorted({r[k] for r in t.rows if r[k]}):
                    abbr.append([h, v, f"{v} (test code)"])
    if abbr:
        out.append(table("ABBR", ["ABBR_HDNG", "ABBR_CODE", "ABBR_DESC"], abbr, types={"ABBR_HDNG": "X"}))
        types.add("X")
    types.update({"X"})
    out.append(
        table(
            "TYPE",
            ["TYPE_TYPE", "TYPE_DESC"],
            [[t, TYPE_DESC.get(t, t)] for t in sorted(types)],
            types={"TYPE_TYPE": "X"},
        )
    )
    out.append(
        table("UNIT", ["UNIT_UNIT", "UNIT_DESC"], [[u, u] for u in sorted(units | {"m"})], types={"UNIT_UNIT": "X"})
    )
    return out + body


def minimal_ags(
    locations: list[tuple],
    strata: list[tuple],
    extra: list[AGSTable] | None = None,
    version: str = "4.1.1",
    full: bool = True,
) -> str:
    """locations: (id, type, E, N, GL, FDEP); strata: (id, top, base, desc, geol_code[, bgs_code]).
    ``full`` adds the PROJ / ABBR / TYPE / UNIT groups so that a clean file has no format findings."""
    loca = table(
        "LOCA",
        ["LOCA_ID", "LOCA_TYPE", "LOCA_NATE", "LOCA_NATN", "LOCA_GREF", "LOCA_GL", "LOCA_FDEP"],
        [[i, t, f"{e:.2f}", f"{n:.2f}", "OSGB", f"{g:.2f}", f"{d:.2f}"] for i, t, e, n, g, d in locations],
    )
    geol = table(
        "GEOL",
        ["LOCA_ID", "GEOL_TOP", "GEOL_BASE", "GEOL_DESC", "GEOL_GEOL", "GEOL_BGS"],
        [
            [s[0], f"{s[1]:.2f}", f"{s[2]:.2f}", s[3], s[4] if len(s) > 4 else "", s[5] if len(s) > 5 else ""]
            for s in strata
        ],
    )
    tables = [tran(version), loca, geol] + list(extra or [])
    return write_ags4(complete(tables) if full else tables)
