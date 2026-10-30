"""AGS4 validation: format rules plus engineering-data checks that matter for modelling.

Rule identifiers ``AGS4-n`` follow the AGS4 format rules (as implemented by the AGS Data Format
Working Group's ``python-ags4`` checker). This validator focuses on the rules and data defects
that change an interpretation; for formal submission checking run the official checker as well
(:func:`run_official_checker`, available when ``python-ags4`` is installed).

``E``-prefixed rules are engineering checks (depth logic, coordinates, datums) that are not part
of the AGS format rules but routinely invalidate ground models if missed.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ...core.issues import Issue, IssueLog
from ...models.enums import Severity
from .dictionary import AGSDictionary, load_dictionary
from .reader import AGSFile, parse_float

_SEV = {"info": Severity.INFO, "warning": Severity.WARNING, "error": Severity.ERROR, "critical": Severity.CRITICAL}
_DP = re.compile(r"^(\d+)DP$")
_SF = re.compile(r"^(\d+)SF$")
_SCI = re.compile(r"^(\d+)SCI$")
DEPTH_HEADINGS = {
    "GEOL_TOP",
    "GEOL_BASE",
    "LOCA_FDEP",
    "ISPT_TOP",
    "SCPT_DPTH",
    "SAMP_TOP",
    "SAMP_BASE",
    "HDPH_TOP",
    "HDPH_BASE",
    "WSTG_DPTH",
    "CORE_TOP",
    "CORE_BASE",
    "WSTD_POST",
}


@dataclass
class ValidationReport:
    log: IssueLog = field(default_factory=IssueLog)
    ags_version: str = "unknown"
    format: str = "AGS4"
    dictionary_complete: bool = False
    groups: list[str] = field(default_factory=list)

    @property
    def issues(self) -> list[Issue]:
        return self.log.issues

    def counts(self) -> dict[str, int]:
        return self.log.counts()

    @property
    def usable_for_modelling(self) -> bool:
        """False only when the data cannot be located or has no geology at all."""
        return not any(i.severity == Severity.CRITICAL for i in self.log)

    def summary_text(self) -> str:
        c = self.counts()
        return (
            f"{self.format} {self.ags_version}: {c['critical']} critical, {c['error']} errors, "
            f"{c['warning']} warnings, {c['info']} notes"
            + ("" if self.dictionary_complete else " (dictionary subset: install python-ags4 for full rule 7/9)")
        )


# ============================================================================ helpers
def _dt_regex(unit: str) -> re.Pattern | None:
    if not unit:
        return None
    pat = re.escape(unit)
    for tok, rx in (
        ("yyyy", r"\d{4}"),
        ("sss", r"\d{3}"),
        ("ss", r"\d{2}"),
        ("dd", r"\d{2}"),
        ("hh", r"\d{2}"),
        ("mm", r"\d{2}"),
    ):
        pat = pat.replace(tok, rx)
    return re.compile("^" + pat + "$")


def _sig_figs_ok(s: str, n: int) -> bool:
    t = s.strip().lstrip("+-")
    if "e" in t.lower():
        t = t.lower().split("e")[0]
    digits = t.replace(".", "")
    stripped = digits.lstrip("0")
    if not stripped:
        return True
    if "." in t:
        return len(stripped) == n
    lo = len(stripped.rstrip("0"))
    return lo <= n <= len(stripped)


def _type_ok(value: str, typ: str, unit: str) -> tuple[bool, str]:
    if value == "":
        return True, ""
    m = _DP.match(typ)
    if m:
        n = int(m.group(1))
        rx = r"^[+-]?\d+$" if n == 0 else rf"^[+-]?\d+\.\d{{{n}}}$"
        return (bool(re.match(rx, value)), f"expected {n} decimal places")
    m = _SF.match(typ)
    if m:
        if np.isnan(parse_float(value)):
            return False, "not numeric"
        return _sig_figs_ok(value, int(m.group(1))), f"expected {m.group(1)} significant figures"
    m = _SCI.match(typ)
    if m:
        n = int(m.group(1))
        return bool(re.match(rf"^[+-]?\d\.\d{{{n}}}[eE][+-]?\d+$", value)), f"expected scientific with {n} dp"
    if typ == "DT":
        rx = _dt_regex(unit)
        return (True, "") if rx is None else (bool(rx.match(value)), f"expected date/time format {unit}")
    if typ == "YN":
        return value in ("Y", "N", "y", "n"), "expected Y or N"
    if typ == "MC":
        return not np.isnan(parse_float(value)) or value.startswith(("<", ">")), "not numeric"
    if typ == "DMS":
        return bool(re.match(r"^[+-]?\d{1,3}:\d{1,2}:\d{1,2}(\.\d+)?$", value)), "expected d:m:s"
    return True, ""


def _issue(rule, sev, msg, **kw) -> Issue:
    return Issue(rule=rule, severity=_SEV[sev] if isinstance(sev, str) else sev, message=msg, **kw)


# ============================================================================ main entry
def validate_ags(
    ags: AGSFile, dictionary: AGSDictionary | None = None, base_dir: str | Path | None = None, engineering: bool = True
) -> ValidationReport:
    d = dictionary or load_dictionary(ags.version)
    rep = ValidationReport(
        ags_version=ags.version, format=ags.format, dictionary_complete=d.complete, groups=ags.groups
    )
    log = rep.log
    for p in ags.issues:
        log.add(_issue(p.rule, p.severity, p.message, group=p.group, heading=p.heading, line=p.line))
    if ags.format == "AGS4" and ags.line_endings not in ("CRLF",):
        log.add(_issue("AGS4-2a", "warning", f"lines are terminated by {ags.line_endings}, not CR+LF"))
    _format_rules(ags, d, log, base_dir)
    if engineering:
        _engineering_rules(ags, log)
    return rep


def _format_rules(ags: AGSFile, d: AGSDictionary, log: IssueLog, base_dir) -> None:
    user_dict: dict[str, set[str]] = defaultdict(set)
    if "DICT" in ags:
        t = ags["DICT"]
        for r in t.records():
            if r.get("DICT_TYPE") == "HEADING":
                user_dict[r.get("DICT_GRP", "")].add(r.get("DICT_HDNG", ""))
            elif r.get("DICT_TYPE") == "GROUP":
                user_dict.setdefault(r.get("DICT_GRP", ""), set())
    for req in ("PROJ", "TRAN", "TYPE", "UNIT"):
        if req not in ags and not (ags.format != "AGS4" and req in ("TYPE", "UNIT", "PROJ")):
            rule = {"PROJ": "AGS4-13", "TRAN": "AGS4-14", "TYPE": "AGS4-17", "UNIT": "AGS4-15"}[req]
            log.add(_issue(rule, "error", f"required group {req} is missing", group=req))
    for g, rule in (("PROJ", "AGS4-13"), ("TRAN", "AGS4-14")):
        if g in ags and len(ags[g]) != 1:
            log.add(_issue(rule, "error", f"{g} must contain exactly one DATA row (has {len(ags[g])})", group=g))
    units_defined = set(ags["UNIT"].column("UNIT_UNIT")) if "UNIT" in ags else None
    types_defined = set(ags["TYPE"].column("TYPE_TYPE")) if "TYPE" in ags else None
    abbr: dict[str, set[str]] = defaultdict(set)
    if "ABBR" in ags:
        for r in ags["ABBR"].records():
            abbr[r.get("ABBR_HDNG", "")].add(r.get("ABBR_CODE", ""))
    dlim = ags.delimiter
    units_used: set[str] = set()
    types_used: set[str] = set()
    for gname, t in ags.tables.items():
        if not re.fullmatch(r"[A-Z0-9]{4}", gname):
            continue
        if len(t) == 0 and gname not in ("DICT",):
            log.add(_issue("AGS4-2", "error", "group has no DATA rows", group=gname, line=t.group_line))
        gdef = d.get(gname)
        for h in t.headings:
            if not re.fullmatch(r"[A-Z0-9]{4}_[A-Z0-9]{1,4}(_\d+)?", h):
                log.add(
                    _issue(
                        "AGS4-19a",
                        "error",
                        f"heading {h!r} is not of the form GROUP_XXXX (max 9 chars)",
                        group=gname,
                        heading=h,
                        line=t.group_line,
                    )
                )
        if gdef is None:
            if gname not in user_dict and ags.format == "AGS4":
                sev = "error" if d.complete else "info"
                msg = (
                    "group is not in the AGS4 dictionary and not defined in DICT"
                    if d.complete
                    else "group not in the bundled dictionary subset; rule 9 not checked"
                )
                log.add(_issue("AGS4-9", sev, msg, group=gname, line=t.group_line))
        else:
            known = set(gdef.order) | user_dict.get(gname, set())
            for h in t.headings:
                if h not in known and ags.format == "AGS4":
                    log.add(
                        _issue(
                            "AGS4-9",
                            "error",
                            f"heading {h} not in dictionary or DICT",
                            group=gname,
                            heading=h,
                            line=t.group_line,
                        )
                    )
            order = [h for h in gdef.order if h in t.headings]
            present = [h for h in t.headings if h in gdef.order]
            if order != present and ags.format == "AGS4":
                log.add(
                    _issue("AGS4-7", "warning", "headings are not in dictionary order", group=gname, line=t.group_line)
                )
            # 10a: key fields present + unique
            missing_keys = [k for k in gdef.key_fields if k not in t.headings]
            if missing_keys:
                log.add(
                    _issue("AGS4-10a", "error", f"key fields missing: {missing_keys}", group=gname, line=t.group_line)
                )
            keys = [k for k in gdef.key_fields if k in t.headings]
            if keys and len(t):
                idx = [t.index(k) for k in keys]
                cnt = Counter(tuple(r[i] for i in idx) for r in t.rows)
                for combo, n in cnt.items():
                    if n > 1:
                        log.add(
                            _issue(
                                "AGS4-10a",
                                "error",
                                f"duplicate key combination {dict(zip(keys, combo))} ({n} rows)",
                                group=gname,
                                location_ids=[combo[0]] if keys[0] == "LOCA_ID" else [],
                            )
                        )
            # 10b: required fields not empty
            for rq in gdef.required_fields:
                if rq in t.headings:
                    k = t.index(rq)
                    for r, ln in zip(t.rows, t.line_numbers):
                        if r[k] == "":
                            log.add(
                                _issue(
                                    "AGS4-10b",
                                    "error",
                                    f"required field {rq} is empty",
                                    group=gname,
                                    heading=rq,
                                    line=ln,
                                )
                            )
            # 10c: parent records exist
            if gdef.parent and gdef.parent not in ("-", "") and gdef.parent in ags:
                pdef = d.get(gdef.parent)
                pkeys = [
                    k for k in (pdef.key_fields if pdef else []) if k in t.headings and k in ags[gdef.parent].headings
                ]
                if pkeys:
                    pt = ags[gdef.parent]
                    parent_set = {tuple(r[pt.index(k)] for k in pkeys) for r in pt.rows}
                    for r, ln in zip(t.rows, t.line_numbers):
                        combo = tuple(r[t.index(k)] for k in pkeys)
                        if combo not in parent_set:
                            log.add(
                                _issue(
                                    "AGS4-10c",
                                    "error",
                                    f"no parent {gdef.parent} record for {dict(zip(pkeys, combo))}",
                                    group=gname,
                                    line=ln,
                                    location_ids=[combo[0]] if pkeys[0] == "LOCA_ID" else [],
                                )
                            )
            elif gdef.parent and gdef.parent not in ("-", "") and len(t) and ags.format == "AGS4":
                log.add(_issue("AGS4-10c", "error", f"parent group {gdef.parent} is missing", group=gname))
        # rule 8 / 15 / 16 / 17
        for k, h in enumerate(t.headings):
            typ = t.types[k] if k < len(t.types) else ""
            unit = t.units[k] if k < len(t.units) else ""
            if typ:
                types_used.add(typ)
            if unit:
                units_used.add(unit)
            bad = 0
            first_bad = None
            for r, ln in zip(t.rows, t.line_numbers):
                v = r[k]
                ok, why = _type_ok(v, typ, unit)
                if not ok:
                    bad += 1
                    first_bad = first_bad or (ln, v, why)
                if typ == "PA" and v and abbr and h in abbr:
                    for code in v.split(dlim):
                        if code and code not in abbr[h]:
                            log.add(
                                _issue(
                                    "AGS4-16",
                                    "warning",
                                    f"abbreviation {code!r} for {h} not defined in ABBR",
                                    group=gname,
                                    heading=h,
                                    line=ln,
                                )
                            )
            if bad and ags.format == "AGS4":
                ln, v, why = first_bad
                log.add(
                    _issue(
                        "AGS4-8",
                        "warning",
                        f"{bad} value(s) of {h} do not match TYPE {typ} (first: {v!r} at line {ln}; {why})",
                        group=gname,
                        heading=h,
                        line=ln,
                    )
                )
    if units_defined is not None:
        for u in sorted(units_used - units_defined):
            log.add(_issue("AGS4-15", "error", f"unit {u!r} used but not defined in UNIT group", group="UNIT"))
    if types_defined is not None:
        for ty in sorted(types_used - types_defined):
            log.add(_issue("AGS4-17", "error", f"type {ty!r} used but not defined in TYPE group", group="TYPE"))
    if "PA" in types_used and "ABBR" not in ags and ags.format == "AGS4":
        log.add(_issue("AGS4-16", "warning", "PA (pick list) fields used but no ABBR group present", group="ABBR"))
    if base_dir is not None:
        fsets = set()
        for t in ags.tables.values():
            if "FILE_FSET" in t.headings:
                fsets.update(v for v in t.column("FILE_FSET") if v)
        for fs in sorted(fsets):
            if not (Path(base_dir) / "FILE" / fs).exists():
                log.add(_issue("AGS4-20", "warning", f"associated file set FILE/{fs} not found", group="FILE"))


def _engineering_rules(ags: AGSFile, log: IssueLog) -> None:
    loca = ags.get("LOCA")
    if loca is None or len(loca) == 0:
        log.add(_issue("E00", "critical", "no LOCA group: locations cannot be placed", category="engineering"))
        return
    ids = loca.column("LOCA_ID")
    e = loca.float_column("LOCA_NATE")
    n = loca.float_column("LOCA_NATN")
    gl = loca.float_column("LOCA_GL")
    fdep = loca.float_column("LOCA_FDEP")
    has_latlon = loca.has("LOCA_LAT") and loca.has("LOCA_LON")
    lat = loca.column("LOCA_LAT")
    for k, lid in enumerate(ids):
        if not (np.isfinite(e[k]) and np.isfinite(n[k])) and not (has_latlon and lat[k]):
            log.add(
                _issue(
                    "E01",
                    "error",
                    "location has no coordinates",
                    category="engineering",
                    location_ids=[lid],
                    group="LOCA",
                )
            )
        if not np.isfinite(gl[k]):
            log.add(
                _issue(
                    "E02",
                    "warning",
                    "no ground level (LOCA_GL): depths cannot be converted to elevations unless a DTM is supplied",
                    category="engineering",
                    location_ids=[lid],
                    group="LOCA",
                    heading="LOCA_GL",
                )
            )
    for h in DEPTH_HEADINGS:
        for t in ags.tables.values():
            if h in t.headings:
                u = t.unit_of(h)
                if u and u != "m":
                    log.add(
                        _issue(
                            "E11",
                            "error",
                            f"{h} unit is {u!r}; depths must be in metres",
                            category="engineering",
                            group=t.name,
                            heading=h,
                        )
                    )
    if loca.has("LOCA_DATM"):
        datums = {v.strip() for v in loca.column("LOCA_DATM") if v.strip()}
        if len(datums) > 1:
            log.add(
                _issue(
                    "E08",
                    "error",
                    f"mixed vertical datums in LOCA_DATM: {sorted(datums)}; levels cannot "
                    "be combined without conversion",
                    category="engineering",
                    group="LOCA",
                )
            )
    pts = np.column_stack([e, n])
    good = np.isfinite(pts).all(axis=1)
    if good.sum() > 1:
        gp = pts[good]
        gid = [i for i, ok in zip(ids, good) if ok]
        order = np.lexsort((gp[:, 1], gp[:, 0]))
        for a, b in zip(order[:-1], order[1:]):
            if np.hypot(*(gp[a] - gp[b])) < 0.5 and gid[a] != gid[b]:
                log.add(
                    _issue(
                        "E07",
                        "warning",
                        f"{gid[a]} and {gid[b]} are within 0.5 m: possible duplicate or re-drilled hole",
                        category="engineering",
                        location_ids=[gid[a], gid[b]],
                    )
                )
    if "HORN" in ags:
        log.add(
            _issue(
                "E13",
                "info",
                "HORN (orientation/inclination) present: v0.1 treats holes as vertical",
                category="engineering",
                group="HORN",
            )
        )
    fdep_map = {lid: fdep[k] for k, lid in enumerate(ids)}
    geol = ags.get("GEOL")
    if geol is None or len(geol) == 0:
        log.add(_issue("E00", "critical", "no GEOL group: no geological descriptions to model", category="engineering"))
        return
    by_loc: dict[str, list[tuple[float, float, int, str]]] = defaultdict(list)
    tops, bases = geol.float_column("GEOL_TOP"), geol.float_column("GEOL_BASE")
    descs = geol.column("GEOL_DESC")
    for k, (lid, t0, b0, ln) in enumerate(zip(geol.column("LOCA_ID"), tops, bases, geol.line_numbers)):
        if not (np.isfinite(t0) and np.isfinite(b0)):
            log.add(
                _issue(
                    "E03",
                    "error",
                    "GEOL interval without numeric top/base",
                    category="engineering",
                    location_ids=[lid],
                    line=ln,
                    group="GEOL",
                )
            )
            continue
        if t0 >= b0:
            log.add(
                _issue(
                    "E03",
                    "error",
                    f"GEOL top {t0} >= base {b0}",
                    category="engineering",
                    location_ids=[lid],
                    line=ln,
                    group="GEOL",
                    depth=t0,
                )
            )
        if not descs[k].strip():
            log.add(
                _issue(
                    "E12",
                    "warning",
                    "empty GEOL_DESC",
                    category="engineering",
                    location_ids=[lid],
                    line=ln,
                    group="GEOL",
                    depth=t0,
                )
            )
        by_loc[lid].append((t0, b0, ln, descs[k]))
    for lid, ivs in by_loc.items():
        ivs.sort()
        if ivs and ivs[0][0] > 0.01:
            log.add(
                _issue(
                    "E04",
                    "warning",
                    f"first GEOL interval starts at {ivs[0][0]} m (not at ground level)",
                    category="engineering",
                    location_ids=[lid],
                    group="GEOL",
                    depth=0.0,
                )
            )
        for (t1, b1, _l1, _), (t2, b2, l2, _) in zip(ivs[:-1], ivs[1:]):
            if t2 < b1 - 0.005:
                log.add(
                    _issue(
                        "E04",
                        "error",
                        f"overlapping GEOL intervals {t1}-{b1} and {t2}-{b2}",
                        category="engineering",
                        location_ids=[lid],
                        line=l2,
                        group="GEOL",
                        depth=t2,
                    )
                )
            elif t2 > b1 + 0.005:
                log.add(
                    _issue(
                        "E04",
                        "warning",
                        f"gap in GEOL between {b1} and {t2} m",
                        category="engineering",
                        location_ids=[lid],
                        line=l2,
                        group="GEOL",
                        depth=b1,
                    )
                )
        fd = fdep_map.get(lid, np.nan)
        if np.isfinite(fd) and ivs and ivs[-1][1] > fd + 0.01:
            log.add(
                _issue(
                    "E05",
                    "warning",
                    f"GEOL base {ivs[-1][1]} m exceeds LOCA_FDEP {fd} m",
                    category="engineering",
                    location_ids=[lid],
                    group="GEOL",
                )
            )
    ispt = ags.get("ISPT")
    if ispt is not None:
        for lid, d0, ln in zip(ispt.column("LOCA_ID"), ispt.float_column("ISPT_TOP"), ispt.line_numbers):
            fd = fdep_map.get(lid, np.nan)
            if np.isfinite(fd) and np.isfinite(d0) and d0 > fd + 0.01:
                log.add(
                    _issue(
                        "E09",
                        "warning",
                        f"SPT at {d0} m below final depth {fd} m",
                        category="engineering",
                        location_ids=[lid],
                        line=ln,
                        group="ISPT",
                    )
                )
    scpt = ags.get("SCPT")
    if scpt is not None and len(scpt):
        keyc = list(zip(scpt.column("LOCA_ID"), scpt.column("SCPG_TESN")))
        dep = scpt.float_column("SCPT_DPTH")
        last: dict[tuple, float] = {}
        flagged = set()
        for key, dv in zip(keyc, dep):
            if key in last and np.isfinite(dv) and dv < last[key] and key not in flagged:
                log.add(
                    _issue(
                        "E10",
                        "warning",
                        f"CPT {key[0]}/{key[1]} depths are not monotonic",
                        category="engineering",
                        location_ids=[key[0]],
                        group="SCPT",
                    )
                )
                flagged.add(key)
            last[key] = dv
    wstg = ags.get("WSTG")
    if wstg is not None:
        for lid, d0 in zip(wstg.column("LOCA_ID"), wstg.float_column("WSTG_DPTH")):
            fd = fdep_map.get(lid, np.nan)
            if np.isfinite(fd) and np.isfinite(d0) and d0 > fd + 0.01:
                log.add(
                    _issue(
                        "E15",
                        "warning",
                        f"water strike at {d0} m below final depth",
                        category="engineering",
                        location_ids=[lid],
                        group="WSTG",
                    )
                )


def run_official_checker(path: str | Path, version: str | None = None) -> dict:
    """Run the AGS Data Format Working Group checker (``python-ags4``) if installed."""
    from ...core.optional import require

    ags4 = require("python_ags4.AGS4", "official AGS4 rule checking", package="python-ags4")
    return ags4.check_file(str(path), standard_AGS4_dictionary=version)
