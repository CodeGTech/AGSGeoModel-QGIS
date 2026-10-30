"""AGS3 (3.1) reader and conversion of the core groups to AGS4 naming.

A large share of UK legacy ground investigation data is AGS3. The conversion is deliberately
limited to groups whose AGS4 equivalents are unambiguous for modelling; every rename, unit
conversion and unmapped heading is reported as a :class:`ParseIssue` so the conversion is
auditable. Data are never invented: fields missing in AGS3 (e.g. SAMP_ID) are left empty.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field

from .reader import AGSFile, AGSTable, ParseIssue, parse_float

GROUP_MAP = {"HOLE": "LOCA", "STCN": "SCPT", "WSTK": "WSTG", "CLSS": "LLPL"}

HEADING_MAP_BY_GROUP: dict[str, dict[str, str]] = {
    "STCN": {
        "STCN_DPTH": "SCPT_DPTH",
        "STCN_RES": "SCPT_RES",
        "STCN_FRES": "SCPT_FRES",
        "STCN_PWP1": "SCPT_PWP1",
        "STCN_PWP2": "SCPT_PWP2",
        "STCN_PWP3": "SCPT_PWP3",
        "STCN_CON": "SCPT_CON",
        "STCN_TEMP": "SCPT_TEMP",
        "STCN_PH": "SCPT_PH",
        "STCN_SLP1": "SCPT_SLP1",
        "STCN_SLP2": "SCPT_SLP2",
        "STCN_REDX": "SCPT_REDX",
        "STCN_FRR": "SCPT_FRR",
        "STCN_REM": "SCPT_REM",
    },
    "WSTK": {"WSTK_DEP": "WSTG_DPTH", "WSTK_CAS": "WSTG_CAS", "WSTK_SEAL": "WSTG_SEAL", "WSTK_REM": "WSTG_REM"},
    "CORE": {"CORE_BOT": "CORE_BASE"},
    "CLSS": {"CLSS_LL": "LLPL_LL", "CLSS_PL": "LLPL_PL", "CLSS_PI": "LLPL_PI", "CLSS_REM": "LLPL_REM"},
}

# Pressures in AGS4 cone groups are MPa; AGS3 files commonly use kN/m2 for sleeve friction and pwp.
_TO_MPA = {"MPa": 1.0, "MN/m2": 1.0, "MN/m²": 1.0, "kPa": 1e-3, "kN/m2": 1e-3, "kN/m²": 1e-3}


@dataclass
class AGS3Raw:
    tables: dict[str, AGSTable] = field(default_factory=dict)
    issues: list[ParseIssue] = field(default_factory=list)
    n_lines: int = 0
    encoding: str = "utf-8"
    sha256: str = ""
    source_name: str = ""


def _fields(line: str) -> list[str]:
    return next(csv.reader([line.strip()], delimiter=",", quotechar='"', doublequote=True))


def read_ags3_text(text: str) -> AGS3Raw:
    raw = AGS3Raw()
    lines = text.splitlines()
    raw.n_lines = len(lines)
    cur: AGSTable | None = None
    state = "none"  # heading | units | data
    for ln, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            f = _fields(line)
        except csv.Error as exc:
            raw.issues.append(ParseIssue("AGS3", "error", f"unparseable line: {exc}", ln))
            continue
        if not f:
            continue
        first = f[0]
        if first.startswith("**"):
            name = first[2:].strip().upper()
            cur = AGSTable(name, group_line=ln)
            if name in raw.tables:
                raw.issues.append(ParseIssue("AGS3", "error", f"group {name} repeated; rows appended", ln, name))
                cur = raw.tables[name]
            else:
                raw.tables[name] = cur
            state = "heading"
            continue
        if cur is None:
            raw.issues.append(ParseIssue("AGS3", "error", "line before first group", ln))
            continue
        if first.startswith("*") and state in ("heading",):
            cur.headings.extend(h.lstrip("*").strip().upper() for h in f if h)
            continue
        if first == "<UNITS>":
            # the "<UNITS>" marker occupies the first (key) column, whose unit is blank
            cur.units = ([""] + f[1:] + [""] * len(cur.headings))[: len(cur.headings)]
            state = "data"
            continue
        if first == "<CONT>":
            if not cur.rows:
                raw.issues.append(ParseIssue("AGS3", "error", "<CONT> line without preceding data row", ln, cur.name))
                continue
            prev = cur.rows[-1]
            for k, val in enumerate(f[1:], start=1):
                if k < len(prev) and val:
                    prev[k] = prev[k] + val
            continue
        state = "data"
        if len(f) != len(cur.headings):
            raw.issues.append(
                ParseIssue("AGS3", "error", f"row has {len(f)} fields, headings {len(cur.headings)}", ln, cur.name)
            )
            f = (f + [""] * len(cur.headings))[: len(cur.headings)]
        cur.rows.append(f)
        cur.line_numbers.append(ln)
    for t in raw.tables.values():
        if not t.units:
            t.units = [""] * len(t.headings)
    return raw


def convert_ags3_to_ags4(raw: AGS3Raw) -> AGSFile:
    issues = list(raw.issues)
    issues.append(
        ParseIssue(
            "AGS3",
            "info",
            "AGS3 file converted to AGS4 group/heading names for core groups; "
            "review the conversion messages before relying on non-core groups",
        )
    )
    out: dict[str, AGSTable] = {}
    for gname, t in raw.tables.items():
        new_g = GROUP_MAP.get(gname, gname)
        hmap = HEADING_MAP_BY_GROUP.get(gname, {})
        heads = []
        for h in t.headings:
            if h == "HOLE_ID":
                nh = "LOCA_ID"
            elif gname == "HOLE" and h.startswith("HOLE_"):
                nh = "LOCA_" + h[5:]
            elif h in hmap:
                nh = hmap[h]
            elif gname in GROUP_MAP and h.startswith(gname + "_"):
                nh = h
                issues.append(
                    ParseIssue(
                        "AGS3", "warning", f"heading {h} has no AGS4 mapping; kept as-is", t.group_line, new_g, h
                    )
                )
            else:
                nh = h
            heads.append(nh)
        rows = [list(r) for r in t.rows]
        units = list(t.units)
        if gname == "STCN":
            for k, h in enumerate(heads):
                if h in ("SCPT_RES", "SCPT_FRES", "SCPT_PWP1", "SCPT_PWP2", "SCPT_PWP3"):
                    factor = _TO_MPA.get(units[k].strip())
                    if factor is None:
                        issues.append(
                            ParseIssue(
                                "AGS3",
                                "warning",
                                f"unit {units[k]!r} for {h} not recognised; assumed MPa",
                                t.group_line,
                                "SCPT",
                                h,
                            )
                        )
                        factor = 1.0
                    if factor != 1.0:
                        for r in rows:
                            v = parse_float(r[k])
                            r[k] = "" if v != v else f"{v * factor:.4f}"
                        issues.append(
                            ParseIssue("AGS3", "info", f"{h} converted from {units[k]} to MPa", t.group_line, "SCPT", h)
                        )
                    units[k] = "MPa"
            if "SCPG_TESN" not in heads:
                heads.append("SCPG_TESN")
                units.append("")
                for r in rows:
                    r.append("1")
            scpg = AGSTable("SCPG", ["LOCA_ID", "SCPG_TESN"], ["", ""], ["ID", "X"])
            seen = set()
            li, ti = heads.index("LOCA_ID"), heads.index("SCPG_TESN")
            for r in rows:
                key = (r[li], r[ti])
                if key not in seen:
                    seen.add(key)
                    scpg.rows.append([r[li], r[ti]])
            out["SCPG"] = scpg
        if gname == "WSTK" and {"WSTK_TIME", "WSTK_POST"} <= set(t.headings):
            wstd = AGSTable(
                "WSTD",
                ["LOCA_ID", "WSTG_DPTH", "WSTD_NMIN", "WSTD_POST"],
                ["", "m", "min", "m"],
                ["ID", "2DP", "0DP", "2DP"],
            )
            hi = {h: t.headings.index(h) for h in ("HOLE_ID", "WSTK_DEP", "WSTK_TIME", "WSTK_POST")}
            for r in t.rows:
                if r[hi["WSTK_POST"]]:
                    wstd.rows.append([r[hi["HOLE_ID"]], r[hi["WSTK_DEP"]], r[hi["WSTK_TIME"]], r[hi["WSTK_POST"]]])
            out["WSTD"] = wstd
            issues.append(
                ParseIssue("AGS3", "info", "water strike time/level pairs converted to WSTD", t.group_line, "WSTD")
            )
        if gname == "CLSS" and "CLSS_NMC" in t.headings:
            k = t.headings.index("CLSS_NMC")
            li = t.headings.index("HOLE_ID")
            ti = t.headings.index("SAMP_TOP") if "SAMP_TOP" in t.headings else None
            lnmc = AGSTable("LNMC", ["LOCA_ID", "SAMP_TOP", "LNMC_MC"], ["", "m", "%"], ["ID", "2DP", "X"])
            for r in t.rows:
                if r[k]:
                    lnmc.rows.append([r[li], r[ti] if ti is not None else "", r[k]])
            out["LNMC"] = lnmc
        types = ["X"] * len(heads)
        nt = AGSTable(new_g, heads, units, types, rows, list(t.line_numbers), t.group_line)
        if new_g in out:
            issues.append(
                ParseIssue(
                    "AGS3", "warning", f"group {new_g} produced twice in conversion; merged", t.group_line, new_g
                )
            )
            out[new_g].rows.extend(nt.rows)
        else:
            out[new_g] = nt
        if gname in GROUP_MAP:
            issues.append(ParseIssue("AGS3", "info", f"group {gname} converted to {new_g}", t.group_line, new_g))
    if "TRAN" not in out:
        out["TRAN"] = AGSTable(
            "TRAN",
            ["TRAN_ISNO", "TRAN_AGS", "TRAN_REM"],
            ["", "", ""],
            ["X", "X", "X"],
            [["1", "3.1", "converted from AGS3 by AGSGeoModel"]],
        )
    ags = AGSFile(
        tables=out,
        format="AGS3->AGS4",
        version="3.1",
        encoding=raw.encoding,
        issues=issues,
        source_name=raw.source_name,
        sha256=raw.sha256,
        n_lines=raw.n_lines,
        line_endings="n/a",
    )
    return ags
