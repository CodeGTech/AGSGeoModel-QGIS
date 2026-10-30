"""Map parsed AGS tables onto the domain model (:class:`~agsgeomodel.models.Project`)."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from ...core.issues import Issue
from ...core.transform import get_transformer
from ...models.entities import (
    CoreRun,
    CPTSounding,
    DrillingSection,
    Interval,
    LabResult,
    Location,
    MonitoringPoint,
    MonitoringReading,
    Sample,
    SPTRecord,
    WaterStrike,
)
from ...models.enums import DataSource, LocationKind, Severity
from ...models.project import Project
from .crs import CRSGuess, detect_crs, detect_vertical_datum, dms_to_decimal
from .reader import AGSFile, parse_float

_PRESSURE_TO_MPA = {"MPa": 1.0, "MN/m2": 1.0, "kPa": 1e-3, "kN/m2": 1e-3, "": 1.0}

LAB_FIELDS: dict[str, list[str]] = {
    "LLPL": ["LLPL_LL", "LLPL_PL", "LLPL_PI", "LLPL_425"],
    "LNMC": ["LNMC_MC"],
    "LDEN": ["LDEN_BDEN", "LDEN_DDEN", "LDEN_MC"],
    "GRAG": ["GRAG_GRAV", "GRAG_SAND", "GRAG_SILT", "GRAG_CLAY", "GRAG_FINE", "GRAG_UC"],
    "TRIT": ["TRIT_CU", "TRIT_DEVF", "TRIT_BDEN", "TRIT_MC"],
    "CONS": ["CONS_MVC", "CONS_CVRT", "CONS_CVLG", "CONS_INCF"],
    "SHBT": ["SHBT_PEAK", "SHBT_RES"],
}


def classify_location_type(code: str, has_geol: bool = True, has_cpt: bool = False) -> LocationKind:
    c = (code or "").upper()
    parts = re.split(r"[+/,; ]+", c) if c else []
    if any(p in ("TP", "TT", "PIT", "TRIALPIT", "HDTP", "MTP") or p.startswith("TP") for p in parts):
        return LocationKind.TRIAL_PIT
    if any(p in ("WS", "WLS", "WINDOW", "DS") or p.startswith(("WS", "WLS")) for p in parts):
        return LocationKind.WINDOW_SAMPLE
    if any(p in ("CPT", "CPTU", "SCP", "SCPT", "PCPT", "SCPTU", "CONE") for p in parts):
        return LocationKind.CPT if not any(p in ("CP", "RC", "RO", "BH") for p in parts) else LocationKind.BOREHOLE
    if any(p in ("DP", "DPL", "DPM", "DPH", "DPSH", "DPSHA", "DPSHB", "PROBE") for p in parts):
        return LocationKind.DYNAMIC_PROBE
    if parts:
        return LocationKind.BOREHOLE
    if has_cpt and not has_geol:
        return LocationKind.CPT
    return LocationKind.BOREHOLE


@dataclass
class ImportResult:
    project: Project
    crs: CRSGuess
    datum_basis: str
    issues: list[Issue] = field(default_factory=list)
    transform_note: str = "identity"


def ags_to_project(
    ags: AGSFile, target_crs: str | None = None, crs_override: str | None = None, name: str | None = None
) -> ImportResult:
    """Convert an AGS file into a :class:`Project`.

    ``crs_override`` replaces the detected source CRS (e.g. after the user confirms it in the import
    wizard). ``target_crs`` (default: the source CRS) is the project CRS; coordinates are
    transformed when they differ.
    """
    issues: list[Issue] = []
    guess = detect_crs(ags)
    src = crs_override or guess.authid
    datum = detect_vertical_datum(ags, int(src.split(":")[1]) if src else None)
    if datum.mixed:
        issues.append(Issue("E08", Severity.ERROR, f"mixed vertical datums ({datum.basis})", category="engineering"))
    proj = Project(
        name=name or _proj_name(ags) or ags.source_name or "AGS project",
        crs=target_crs or src,
        vertical_datum=datum.datum,
    )
    proj.metadata.update(
        {
            "ags_version": ags.version,
            "ags_format": ags.format,
            "crs_detection": guess.basis,
            "crs_confidence": guess.confidence,
            "vertical_datum_basis": datum.basis,
        }
    )
    if "PROJ" in ags and len(ags["PROJ"]):
        proj.metadata["PROJ"] = ags["PROJ"].records()[0]
    proj.source_files.append(
        {
            "name": ags.source_name,
            "sha256": ags.sha256,
            "format": ags.format,
            "version": ags.version,
            "encoding": ags.encoding,
        }
    )
    has_geol = set(ags["GEOL"].column("LOCA_ID")) if "GEOL" in ags else set()
    has_cpt = set(ags["SCPG"].column("LOCA_ID")) if "SCPG" in ags else set()
    if "SCPT" in ags:
        has_cpt |= set(ags["SCPT"].column("LOCA_ID"))
    transform_note = "identity"
    loca = ags.get("LOCA")
    if loca is not None:
        ids = loca.column("LOCA_ID")
        if guess.uses_latlon and not crs_override:
            xs = np.array([dms_to_decimal(v) for v in loca.column("LOCA_LON")])
            ys = np.array([dms_to_decimal(v) for v in loca.column("LOCA_LAT")])
        else:
            xs, ys = loca.float_column("LOCA_NATE"), loca.float_column("LOCA_NATN")
        if src and proj.crs and src != proj.crs:
            tr, transform_note = get_transformer(src, proj.crs, allow_approximate=False)
            ok = np.isfinite(xs) & np.isfinite(ys)
            tx, ty = tr(xs[ok], ys[ok])
            xs, ys = xs.copy(), ys.copy()
            xs[ok], ys[ok] = tx, ty
        gl = loca.float_column("LOCA_GL")
        fd = loca.float_column("LOCA_FDEP")
        types = loca.column("LOCA_TYPE")
        star = loca.column("LOCA_STAR")
        rem = loca.column("LOCA_REM")
        for k, lid in enumerate(ids):
            if not lid:
                continue
            attrs = {
                h: v
                for h, v in zip(loca.headings, loca.rows[k])
                if v and h not in ("LOCA_ID", "LOCA_NATE", "LOCA_NATN", "LOCA_GL", "LOCA_FDEP", "LOCA_TYPE")
            }
            proj.locations[lid] = Location(
                id=lid,
                x=float(xs[k]),
                y=float(ys[k]),
                ground_level=float(gl[k]) if np.isfinite(gl[k]) else None,
                final_depth=float(fd[k]) if np.isfinite(fd[k]) else None,
                kind=classify_location_type(types[k], lid in has_geol, lid in has_cpt),
                type_code=types[k],
                source=DataSource.AGS3 if ags.format.startswith("AGS3") else DataSource.AGS4,
                date=star[k] or None,
                remarks=rem[k],
                attributes=attrs,
            )
    _map_geol(ags, proj)
    _map_drilling(ags, proj)
    _map_spt(ags, proj)
    _map_cpt(ags, proj, issues)
    _map_water(ags, proj)
    _map_monitoring(ags, proj)
    _map_samples_lab(ags, proj)
    _map_core(ags, proj)
    for loc in proj.locations.values():
        if loc.final_depth is None:
            depths = [iv.base for iv in proj.intervals if iv.location_id == loc.id]
            if depths:
                loc.final_depth = max(depths)
    return ImportResult(proj, guess, datum.basis, issues, transform_note)


def _proj_name(ags: AGSFile) -> str:
    if "PROJ" in ags and len(ags["PROJ"]):
        r = ags["PROJ"].records()[0]
        return r.get("PROJ_NAME") or r.get("PROJ_ID") or ""
    return ""


def _map_geol(ags: AGSFile, proj: Project) -> None:
    t = ags.get("GEOL")
    if t is None:
        return
    tops, bases = t.float_column("GEOL_TOP"), t.float_column("GEOL_BASE")
    cols = {
        h: t.column(h)
        for h in ("LOCA_ID", "GEOL_DESC", "GEOL_LEG", "GEOL_GEOL", "GEOL_GEO2", "GEOL_BGS", "GEOL_FORM", "GEOL_REM")
    }
    src = DataSource.AGS3 if ags.format.startswith("AGS3") else DataSource.AGS4
    for k in range(len(t)):
        if not (np.isfinite(tops[k]) and np.isfinite(bases[k])) or bases[k] <= tops[k]:
            continue
        proj.intervals.append(
            Interval(
                location_id=cols["LOCA_ID"][k],
                top=float(tops[k]),
                base=float(bases[k]),
                description=cols["GEOL_DESC"][k],
                legend_code=cols["GEOL_LEG"][k],
                geology_code=cols["GEOL_GEOL"][k],
                geology_code2=cols["GEOL_GEO2"][k],
                bgs_code=cols["GEOL_BGS"][k],
                formation_name=cols["GEOL_FORM"][k],
                remarks=cols["GEOL_REM"][k],
                source=src,
            )
        )


def _map_drilling(ags: AGSFile, proj: Project) -> None:
    t = ags.get("HDPH")
    if t is None:
        return
    tops, bases = t.float_column("HDPH_TOP"), t.float_column("HDPH_BASE")
    for lid, a, b, ty, rem in zip(t.column("LOCA_ID"), tops, bases, t.column("HDPH_TYPE"), t.column("HDPH_REM")):
        if np.isfinite(a) and np.isfinite(b):
            proj.drilling.append(DrillingSection(lid, float(a), float(b), ty, rem))
    by_loc = defaultdict(list)
    for dsec in proj.drilling:
        by_loc[dsec.location_id].append(dsec)
    for iv in proj.intervals:
        for dsec in by_loc.get(iv.location_id, []):
            if dsec.top - 1e-6 <= iv.mid <= dsec.base + 1e-6:
                iv.method = dsec.type
                break


def _extrapolated_n(main: float, npen: float) -> tuple[float, bool]:
    """N from main-drive blows when the 300 mm test drive was not completed (refusal)."""
    if not np.isfinite(main):
        return np.nan, False
    test_pen = npen - 150.0 if np.isfinite(npen) else np.nan
    if np.isfinite(test_pen) and 0 < test_pen < 300:
        return main * 300.0 / test_pen, True
    return main, False


def _map_spt(ags: AGSFile, proj: Project) -> None:
    t = ags.get("ISPT")
    if t is None:
        return
    dep, nval = t.float_column("ISPT_TOP"), t.float_column("ISPT_NVAL")
    main, npen = t.float_column("ISPT_MAIN"), t.float_column("ISPT_NPEN")
    erat, n60 = t.float_column("ISPT_ERAT"), t.float_column("ISPT_N60")
    cas, wat = t.float_column("ISPT_CAS"), t.float_column("ISPT_WAT")
    for k, lid in enumerate(t.column("LOCA_ID")):
        if not np.isfinite(dep[k]):
            continue
        n = nval[k]
        refusal = False
        rep = t.column("ISPT_REP")[k]
        if not np.isfinite(n):
            n, refusal = _extrapolated_n(main[k], npen[k])
        elif np.isfinite(npen[k]) and npen[k] < 450:
            refusal = True
        if re.search(r"\d+\s*/\s*\d+\s*mm|\bfor\b\s*\d+\s*mm", rep or "", re.I):
            refusal = True
        er = erat[k] if np.isfinite(erat[k]) else None
        n60v = n60[k] if np.isfinite(n60[k]) else (n * er / 60.0 if er and np.isfinite(n) else None)
        proj.spt.append(
            SPTRecord(
                lid,
                float(dep[k]),
                float(n) if np.isfinite(n) else None,
                rep,
                er,
                n60v,
                t.column("ISPT_TYPE")[k],
                refusal,
                float(cas[k]) if np.isfinite(cas[k]) else None,
                float(wat[k]) if np.isfinite(wat[k]) else None,
            )
        )


def _map_cpt(ags: AGSFile, proj: Project, issues: list[Issue]) -> None:
    t = ags.get("SCPT")
    if t is None or len(t) == 0:
        return
    meta: dict[tuple[str, str], dict] = {}
    g = ags.get("SCPG")
    if g is not None:
        for r in g.records():
            meta[(r.get("LOCA_ID", ""), r.get("SCPG_TESN", ""))] = r
    factors = {}
    for h in ("SCPT_RES", "SCPT_FRES", "SCPT_PWP2", "SCPT_QT"):
        u = t.unit_of(h)
        factors[h] = _PRESSURE_TO_MPA.get(u)
        if factors[h] is None:
            issues.append(
                Issue(
                    "E11",
                    Severity.WARNING,
                    f"{h} unit {u!r} not recognised; assumed MPa",
                    category="engineering",
                    group="SCPT",
                    heading=h,
                )
            )
            factors[h] = 1.0
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    ids, tesn = t.column("LOCA_ID"), t.column("SCPG_TESN")
    for k in range(len(t)):
        groups[(ids[k], tesn[k])].append(k)
    cols = {h: t.float_column(h) for h in ("SCPT_DPTH", "SCPT_RES", "SCPT_FRES", "SCPT_PWP2", "SCPT_QT")}
    for (lid, tn), rows in groups.items():
        rows = sorted(rows, key=lambda r: cols["SCPT_DPTH"][r])
        d = cols["SCPT_DPTH"][rows]
        ok = np.isfinite(d)
        rows = [r for r, o in zip(rows, ok) if o]
        if not rows:
            continue
        m = meta.get((lid, tn), {})
        u2 = cols["SCPT_PWP2"][rows] * factors["SCPT_PWP2"]
        qt = cols["SCPT_QT"][rows] * factors["SCPT_QT"]
        car = parse_float(m.get("SCPG_CAR", ""))
        wat = parse_float(m.get("SCPG_WAT", ""))
        proj.cpt.append(
            CPTSounding(
                location_id=lid,
                test_id=tn,
                depth=cols["SCPT_DPTH"][rows],
                qc=cols["SCPT_RES"][rows] * factors["SCPT_RES"],
                fs=cols["SCPT_FRES"][rows] * factors["SCPT_FRES"],
                u2=u2 if np.isfinite(u2).any() else None,
                qt=qt if np.isfinite(qt).any() else None,
                area_ratio=car if np.isfinite(car) else None,
                water_depth=wat if np.isfinite(wat) else None,
                cone_type=m.get("SCPG_TYPE", ""),
            )
        )


def _map_water(ags: AGSFile, proj: Project) -> None:
    g = ags.get("WSTG")
    if g is None:
        return
    readings: dict[tuple[str, float], list[tuple[float, float]]] = defaultdict(list)
    d = ags.get("WSTD")
    if d is not None:
        for lid, dep, nmin, post in zip(
            d.column("LOCA_ID"), d.float_column("WSTG_DPTH"), d.float_column("WSTD_NMIN"), d.float_column("WSTD_POST")
        ):
            if np.isfinite(dep) and np.isfinite(post):
                readings[(lid, round(float(dep), 3))].append((float(nmin) if np.isfinite(nmin) else 0.0, float(post)))
    cas, seal = g.float_column("WSTG_CAS"), g.float_column("WSTG_SEAL")
    for k, (lid, dep) in enumerate(zip(g.column("LOCA_ID"), g.float_column("WSTG_DPTH"))):
        if not np.isfinite(dep):
            continue
        proj.water_strikes.append(
            WaterStrike(
                lid,
                float(dep),
                sorted(readings.get((lid, round(float(dep), 3)), [])),
                float(cas[k]) if np.isfinite(cas[k]) else None,
                float(seal[k]) if np.isfinite(seal[k]) else None,
                g.column("WSTG_DTIM")[k] or None,
                g.column("WSTG_REM")[k],
            )
        )


def _map_monitoring(ags: AGSFile, proj: Project) -> None:
    g = ags.get("MONG")
    if g is None:
        return
    pts: dict[tuple[str, str], MonitoringPoint] = {}
    trz, brz = g.float_column("MONG_TRZ"), g.float_column("MONG_BRZ")
    for k, (lid, mid) in enumerate(zip(g.column("LOCA_ID"), g.column("MONG_ID"))):
        pts[(lid, mid)] = MonitoringPoint(
            lid,
            mid,
            float(trz[k]) if np.isfinite(trz[k]) else None,
            float(brz[k]) if np.isfinite(brz[k]) else None,
            g.column("MONG_TYPE")[k],
        )
    d = ags.get("MOND")
    if d is not None:
        vals = d.float_column("MOND_RDNG")
        for k, (lid, mid) in enumerate(zip(d.column("LOCA_ID"), d.column("MONG_ID"))):
            if (lid, mid) in pts and np.isfinite(vals[k]):
                pts[(lid, mid)].readings.append(
                    MonitoringReading(
                        d.column("MOND_DTIM")[k], float(vals[k]), d.column("MOND_TYPE")[k], d.column("MOND_UNIT")[k]
                    )
                )
    proj.monitoring.extend(pts.values())


def _map_samples_lab(ags: AGSFile, proj: Project) -> None:
    s = ags.get("SAMP")
    if s is not None:
        tops, bases = s.float_column("SAMP_TOP"), s.float_column("SAMP_BASE")
        for k, lid in enumerate(s.column("LOCA_ID")):
            if np.isfinite(tops[k]):
                proj.samples.append(
                    Sample(
                        lid,
                        float(tops[k]),
                        float(bases[k]) if np.isfinite(bases[k]) else None,
                        s.column("SAMP_REF")[k],
                        s.column("SAMP_TYPE")[k],
                        s.column("SAMP_ID")[k],
                    )
                )
    for grp, fields in LAB_FIELDS.items():
        t = ags.get(grp)
        if t is None:
            continue
        depth = t.float_column("SPEC_DPTH")
        stop = t.float_column("SAMP_TOP")
        depth = np.where(np.isfinite(depth), depth, stop)
        refs = t.column("SAMP_REF")
        for h in fields:
            if h not in t.headings:
                continue
            vals = t.column(h)
            for k, lid in enumerate(t.column("LOCA_ID")):
                if not np.isfinite(depth[k]) or vals[k] == "":
                    continue
                proj.lab.append(
                    LabResult(
                        lid,
                        float(depth[k]),
                        grp,
                        h,
                        parse_float(vals[k]) if np.isfinite(parse_float(vals[k])) else None,
                        t.unit_of(h),
                        vals[k],
                        refs[k],
                    )
                )
    _map_grat(ags, proj)
    _map_generic_lab(ags, proj)
    g = ags.get("GCHM")
    if g is not None:
        depth = g.float_column("SPEC_DPTH")
        depth = np.where(np.isfinite(depth), depth, g.float_column("SAMP_TOP"))
        for k, lid in enumerate(g.column("LOCA_ID")):
            res = g.column("GCHM_RESL")[k]
            if np.isfinite(depth[k]) and res:
                v = parse_float(res)
                proj.lab.append(
                    LabResult(
                        lid,
                        float(depth[k]),
                        "GCHM",
                        g.column("GCHM_CODE")[k],
                        float(v) if np.isfinite(v) else None,
                        g.column("GCHM_UNIT")[k],
                        res,
                        g.column("SAMP_REF")[k],
                    )
                )


HANDLED_LAB_GROUPS = set(LAB_FIELDS) | {"GCHM", "GRAT", "SAMP"}
_NUMERIC_TYPE = re.compile(r"^(\d+(DP|SF|SCI)|XN|MC|RL)$")
_SAMPLE_KEYS = {
    "LOCA_ID",
    "SAMP_TOP",
    "SAMP_REF",
    "SAMP_TYPE",
    "SAMP_ID",
    "SPEC_REF",
    "SPEC_DPTH",
    "SPEC_BASE",
    "FILE_FSET",
    "SPEC_DESC",
    "SPEC_PREP",
    "SPEC_REM",
}


def _sample_depths(t) -> np.ndarray:
    depth = t.float_column("SPEC_DPTH")
    return np.where(np.isfinite(depth), depth, t.float_column("SAMP_TOP"))


def _map_generic_lab(ags: AGSFile, proj: Project) -> None:
    """Numeric results of any other sample-child group (``SAMP_TOP`` / ``SPEC_DPTH`` present), e.g.
    project-specific groups defined in DICT such as LABG or GREM. Values keep their AGS heading,
    unit and sample reference so they can be traced and summarised in the ground model."""
    for name, t in ags.tables.items():
        if name in HANDLED_LAB_GROUPS or "LOCA_ID" not in t.headings:
            continue
        if not ("SAMP_TOP" in t.headings or "SPEC_DPTH" in t.headings):
            continue
        depth = _sample_depths(t)
        refs = t.column("SAMP_REF")
        for h in t.headings:
            if h in _SAMPLE_KEYS or not _NUMERIC_TYPE.match(t.type_of(h) or ""):
                continue
            vals = t.column(h)
            for k, lid in enumerate(t.column("LOCA_ID")):
                if not np.isfinite(depth[k]) or vals[k] == "":
                    continue
                v = parse_float(vals[k])
                proj.lab.append(
                    LabResult(
                        lid,
                        float(depth[k]),
                        name,
                        h,
                        float(v) if np.isfinite(v) else None,
                        t.unit_of(h),
                        vals[k],
                        refs[k],
                    )
                )


def psd_indices(size_mm, passing_pct) -> dict[str, float]:
    """D10, D30, D50, D60, uniformity Cu, curvature Cc, gravel / sand / fines (BS EN ISO 14688 sizes:
    gravel > 2 mm, fines < 0.063 mm) from a particle size distribution (log-linear interpolation)."""
    d = np.asarray(size_mm, float)
    p = np.asarray(passing_pct, float)
    ok = np.isfinite(d) & np.isfinite(p) & (d > 0)
    d, p = d[ok], p[ok]
    out: dict[str, float] = {}
    if len(d) < 2:
        return out
    order = np.argsort(d)
    d, p = d[order], np.maximum.accumulate(np.clip(p[order], 0, 100))
    ld = np.log10(d)

    def pass_at(size):
        return float(np.interp(np.log10(size), ld, p, left=np.nan, right=np.nan))

    def size_at(pct):
        if pct < p[0] or pct > p[-1]:
            return np.nan
        k = int(np.searchsorted(p, pct, side="left"))
        if k == 0:
            return float(d[0])
        if p[k] == p[k - 1]:
            return float(d[k])
        f = (pct - p[k - 1]) / (p[k] - p[k - 1])
        return float(10 ** (ld[k - 1] + f * (ld[k] - ld[k - 1])))

    for q in (10, 30, 50, 60):
        out[f"D{q}"] = size_at(q)
    if np.isfinite(out["D10"]) and np.isfinite(out["D60"]) and out["D10"] > 0:
        out["CU"] = out["D60"] / out["D10"]
        if np.isfinite(out["D30"]):
            out["CC"] = out["D30"] ** 2 / (out["D10"] * out["D60"])
    fines = pass_at(0.063)
    two = pass_at(2.0)
    if np.isfinite(fines):
        out["FINES"] = fines
    if np.isfinite(two):
        out["GRAV"] = 100.0 - two
        if np.isfinite(fines):
            out["SAND"] = two - fines
    return {k: float(v) for k, v in out.items() if np.isfinite(v)}


def _map_grat(ags: AGSFile, proj: Project) -> None:
    """Particle size distribution data (GRAT_SIZE, GRAT_PERP) -> D10/D30/D50/D60, Cu, Cc and gravel /
    sand / fines fractions per specimen (group ``GRAT``, tests ``GRAT_D10`` ...)."""
    t = ags.get("GRAT")
    if t is None or "GRAT_SIZE" not in t.headings or "GRAT_PERP" not in t.headings:
        return
    depth = _sample_depths(t)
    size, perp = t.float_column("GRAT_SIZE"), t.float_column("GRAT_PERP")
    keys = list(
        zip(
            t.column("LOCA_ID"),
            t.column("SAMP_REF"),
            t.column("SPEC_REF"),
            [round(float(x), 3) if np.isfinite(x) else None for x in depth],
        )
    )
    groups: dict[tuple, list[int]] = {}
    for k, key in enumerate(keys):
        if key[3] is not None:
            groups.setdefault(key, []).append(k)
    units = {
        "D10": "mm",
        "D30": "mm",
        "D50": "mm",
        "D60": "mm",
        "CU": "-",
        "CC": "-",
        "FINES": "%",
        "GRAV": "%",
        "SAND": "%",
    }
    for (lid, sref, _spec, dep), idx in groups.items():
        for name, v in psd_indices(size[idx], perp[idx]).items():
            proj.lab.append(LabResult(lid, float(dep), "GRAT", f"GRAT_{name}", v, units[name], f"{v:.4g}", sref))


def _map_core(ags: AGSFile, proj: Project) -> None:
    t = ags.get("CORE")
    if t is None:
        return
    tops, bases = t.float_column("CORE_TOP"), t.float_column("CORE_BASE")
    tcr, scr, rqd = t.float_column("CORE_PREC"), t.float_column("CORE_SREC"), t.float_column("CORE_RQD")
    for k, lid in enumerate(t.column("LOCA_ID")):
        if np.isfinite(tops[k]) and np.isfinite(bases[k]):
            proj.core.append(
                CoreRun(
                    lid,
                    float(tops[k]),
                    float(bases[k]),
                    *(float(v[k]) if np.isfinite(v[k]) else None for v in (tcr, scr, rqd)),
                )
            )
