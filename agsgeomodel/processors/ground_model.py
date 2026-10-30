"""Engineering ground model: parameter statistics and characteristic values per geological unit.

Each test result is assigned to a unit using the GeoReasoner interpretation of its hole (or the
3D model at its elevation for holes without logs, e.g. CPTs). Statistics per unit and parameter:

* n, mean, standard deviation, COV, min / median / max;
* characteristic values (BS EN 1997-1 2.4.5.2 asks for a *cautious estimate*):
  - ``cautious_mean_low/high`` = mean (1 -/+ 0.5 COV)            (Schneider 1997 approximation)
  - ``fractile5_low/high`` = mean -/+ t(0.95, n-1) sd sqrt(1 + 1/n) (local failure / 5 % fractile);
* a linear trend with depth when significant (n >= 6 and R^2 >= 0.3), e.g. su increasing in
  London Clay.

The engineer chooses which value is appropriate for each limit state; the table only supplies
consistent, traceable statistics. CPT readings are averaged in depth bins first so that one
sounding cannot dominate a unit's statistics.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

_T95 = {
    1: 6.314,
    2: 2.920,
    3: 2.353,
    4: 2.132,
    5: 2.015,
    6: 1.943,
    7: 1.895,
    8: 1.860,
    9: 1.833,
    10: 1.812,
    11: 1.796,
    12: 1.782,
    13: 1.771,
    14: 1.761,
    15: 1.753,
    16: 1.746,
    17: 1.740,
    18: 1.734,
    19: 1.729,
    20: 1.725,
    21: 1.721,
    22: 1.717,
    23: 1.714,
    24: 1.711,
    25: 1.708,
    26: 1.706,
    27: 1.703,
    28: 1.701,
    29: 1.699,
    30: 1.697,
    40: 1.684,
    60: 1.671,
    120: 1.658,
}


def t95(df: int) -> float:
    """One-sided 95 % Student t quantile (table values; normal limit 1.645)."""
    if df <= 0:
        return math.nan
    if df in _T95:
        return _T95[df]
    keys = sorted(_T95)
    for a, b in zip(keys[:-1], keys[1:]):
        if a < df < b:
            return _T95[a] + (_T95[b] - _T95[a]) * (1 / a - 1 / df) / (1 / a - 1 / b)
    return 1.645


PARAMETERS = {
    # key: (label, unit, source family)
    "LLPL_LL": ("Liquid limit", "%", "lab"),
    "LLPL_PL": ("Plastic limit", "%", "lab"),
    "LLPL_PI": ("Plasticity index", "%", "lab"),
    "LNMC_MC": ("Moisture content", "%", "lab"),
    "LDEN_BDEN": ("Bulk density", "Mg/m3", "lab"),
    "TRIT_CU": ("Undrained shear strength (triaxial)", "kPa", "lab"),
    "GRAG_FINE": ("Fines content", "%", "lab"),
    "GRAG_GRAV": ("Gravel content", "%", "lab"),
    "SO4": ("Water soluble sulfate", "g/l", "lab"),
    "pH": ("pH", "-", "lab"),
    "SPT_N": ("SPT N", "blows", "insitu"),
    "SPT_N60": ("SPT N60", "blows", "insitu"),
    "SPT_N1_60": ("SPT (N1)60", "blows", "insitu"),
    "SPT_cu": ("cu from SPT (Stroud)", "kPa", "insitu"),
    "SPT_phi": ("phi' from SPT (Hatanaka & Uchida)", "deg", "insitu"),
    "SPT_Dr": ("Dr from SPT (Skempton)", "-", "insitu"),
    "CPT_su": ("su from CPT (Nkt)", "kPa", "insitu"),
    "CPT_phi": ("phi' from CPT (K&M)", "deg", "insitu"),
    "CPT_Dr": ("Dr from CPT", "-", "insitu"),
    "CPT_E": ("E' from CPT (Robertson 2009)", "MPa", "insitu"),
    "CPT_M": ("M from CPT (Robertson 2009)", "MPa", "insitu"),
    "CPT_G0": ("G0 from CPT", "MPa", "insitu"),
    "CPT_k": ("Permeability from CPT (Robertson 2010)", "m/s", "insitu"),
    "CPT_gamma": ("Unit weight from CPT", "kN/m3", "insitu"),
    "CPT_Ic": ("Soil behaviour type index Ic", "-", "insitu"),
    "CORE_RQD": ("RQD", "%", "core"),
    "CORE_TCR": ("Total core recovery", "%", "core"),
}
_LOG_PARAMS = {"CPT_k"}  # statistics in log10 space
_POSITIVE = {
    "LLPL_LL",
    "LLPL_PL",
    "LLPL_PI",
    "LNMC_MC",
    "LDEN_BDEN",
    "TRIT_CU",
    "SPT_N",
    "SPT_N60",
    "SPT_N1_60",
    "SPT_cu",
    "SPT_phi",
    "CPT_su",
    "CPT_phi",
    "CPT_E",
    "CPT_M",
    "CPT_G0",
    "CPT_gamma",
    "SO4",
}
FINE_ONLY = {"CPT_su", "SPT_cu", "TRIT_CU"}
COARSE_ONLY = {"CPT_phi", "CPT_Dr", "CPT_E", "SPT_phi", "SPT_Dr"}


@dataclass
class ParameterStats:
    unit: str
    parameter: str
    n: int
    mean: float
    sd: float
    cov: float
    minimum: float
    median: float
    maximum: float
    cautious_mean_low: float
    cautious_mean_high: float
    fractile5_low: float
    fractile5_high: float
    trend_intercept: float | None = None  # value at depth 0
    trend_slope: float | None = None  # per metre depth
    trend_r2: float | None = None
    log_space: bool = False
    sources: list[str] = field(default_factory=list)
    uom: str = ""  # unit for parameters not in PARAMETERS (generic lab groups)

    @property
    def label(self) -> str:
        return PARAMETERS.get(self.parameter, (self.parameter, "", ""))[0]

    @property
    def unit_of_measure(self) -> str:
        return PARAMETERS[self.parameter][1] if self.parameter in PARAMETERS else self.uom

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["label"] = self.label
        d["uom"] = self.unit_of_measure
        return d


def summarise(values, depths, unit: str, parameter: str, sources=None) -> ParameterStats | None:
    v = np.asarray(values, float)
    d = np.asarray(depths, float)
    ok = np.isfinite(v)
    v, d = v[ok], d[ok]
    log = parameter in _LOG_PARAMS
    if log:
        keep = v > 0
        v, d = np.log10(v[keep]), d[keep]
    n = len(v)
    if n == 0:
        return None
    mean = float(v.mean())
    sd = float(v.std(ddof=1)) if n > 1 else float("nan")
    cov = abs(sd / mean) if n > 1 and mean != 0 else float("nan")
    positive = (not log) and parameter in _POSITIVE and bool(np.all(v > 0))
    if positive and n > 1:
        # lognormal fractiles for strictly positive quantities (strength, stiffness, N, PI ...)
        lv = np.log(v)
        lm, ls = float(lv.mean()), float(lv.std(ddof=1))
        tf = t95(n - 1) * ls * math.sqrt(1 + 1 / n)
        f_low, f_high = math.exp(lm - tf), math.exp(lm + tf)
    else:
        tf = t95(n - 1) * sd * math.sqrt(1 + 1 / n) if n > 1 else float("nan")
        f_low, f_high = mean - tf, mean + tf
    c_low = mean * (1 - 0.5 * cov) if np.isfinite(cov) else mean
    if positive:
        c_low = max(c_low, 0.0)
    st = ParameterStats(
        unit,
        parameter,
        n,
        mean,
        sd,
        cov,
        float(v.min()),
        float(np.median(v)),
        float(v.max()),
        c_low,
        mean * (1 + 0.5 * cov) if np.isfinite(cov) else mean,
        f_low,
        f_high,
        log_space=log,
        sources=sorted(set(sources or [])),
    )
    if n >= 6 and np.ptp(d) > 0:
        A = np.column_stack([np.ones(n), d])
        coef, *_ = np.linalg.lstsq(A, v, rcond=None)
        pred = A @ coef
        ss_res = float(((v - pred) ** 2).sum())
        ss_tot = float(((v - v.mean()) ** 2).sum())
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        if r2 >= 0.3:
            st.trend_intercept, st.trend_slope, st.trend_r2 = float(coef[0]), float(coef[1]), float(r2)
    return st


@dataclass
class EngineeringGroundModel:
    stats: dict[tuple[str, str], ParameterStats]
    data: list[dict]  # every assigned value (traceability)
    notes: list[str] = field(default_factory=list)

    def get(self, unit: str, parameter: str) -> ParameterStats | None:
        return self.stats.get((unit, parameter))

    def value(self, unit: str, parameter: str, which: str = "mean") -> float | None:
        st = self.get(unit, parameter)
        if st is None:
            return None
        v = getattr(st, which)
        return 10**v if st.log_space and which not in ("sd", "cov") else v

    def units(self) -> list[str]:
        return sorted({u for u, _ in self.stats})

    def table(self) -> list[dict]:
        return [s.to_dict() for s in sorted(self.stats.values(), key=lambda s: (s.unit, s.parameter))]


def build_ground_model(
    project,
    interpretation=None,
    model=None,
    cpt_results=None,
    spt_results=None,
    cpt_bin_m: float = 0.5,
    generic_lab: bool = True,
) -> EngineeringGroundModel:
    """Assign every test result to a unit and compute statistics.

    ``interpretation`` gives units in logged holes; ``model`` (StratigraphicModel) gives the unit at
    a 3D position for holes without logs. Values that cannot be assigned are kept in ``data`` with
    ``unit = None`` and excluded from statistics.
    """
    rows: list[dict] = []

    def unit_at(loc_id: str, depth: float) -> tuple[str | None, str]:
        if interpretation is not None:
            li = interpretation.locations.get(loc_id)
            if li is not None:
                u = li.unit_at(depth)
                if u:
                    return u, "interpretation"
        if model is not None and loc_id in project.locations:
            loc = project.locations[loc_id]
            if loc.ground_level is not None:
                u = model.unit_at(np.array([[loc.x, loc.y]]), np.array([loc.ground_level - depth]))[0]
                if u:
                    return str(u), "model"
        return None, "unassigned"

    extra_units: dict[str, str] = {}
    for r in project.lab:
        key = r.test
        if r.value is None:
            continue
        if key not in PARAMETERS:
            if not generic_lab or r.group == "GCHM":  # chemistry codes are free text: only known keys
                continue
            extra_units.setdefault(key, r.unit or "")
        u, how = unit_at(r.location_id, r.depth)
        rows.append(
            {
                "location_id": r.location_id,
                "depth": r.depth,
                "parameter": key,
                "value": r.value,
                "unit": u,
                "assigned_by": how,
                "source": r.group,
            }
        )
    for c in project.core:
        mid = 0.5 * (c.top + c.base)
        u, how = unit_at(c.location_id, mid)
        for key, val in (("CORE_RQD", c.rqd), ("CORE_TCR", c.tcr)):
            if val is not None:
                rows.append(
                    {
                        "location_id": c.location_id,
                        "depth": mid,
                        "parameter": key,
                        "value": val,
                        "unit": u,
                        "assigned_by": how,
                        "source": "CORE",
                    }
                )
    for s in spt_results or []:
        u, how = unit_at(s.location_id, s.depth)
        for key, val in (
            ("SPT_N", s.n),
            ("SPT_N60", s.n60),
            ("SPT_N1_60", s.n1_60),
            ("SPT_cu", s.cu_kPa),
            ("SPT_phi", s.phi_deg),
            ("SPT_Dr", s.dr),
        ):
            if val is not None and not (s.refusal and key.startswith("SPT_N")):
                rows.append(
                    {
                        "location_id": s.location_id,
                        "depth": s.depth,
                        "parameter": key,
                        "value": float(val),
                        "unit": u,
                        "assigned_by": how,
                        "source": "ISPT" + (" (refusal)" if s.refusal else ""),
                    }
                )
    cmap = {
        "CPT_su": "su_kPa",
        "CPT_phi": "phi_deg",
        "CPT_Dr": "Dr",
        "CPT_E": "E_MPa",
        "CPT_M": "M_MPa",
        "CPT_G0": "G0_MPa",
        "CPT_k": "k_ms",
        "CPT_gamma": "gamma_kNm3",
        "CPT_Ic": "Ic",
    }
    for res in cpt_results or []:
        z = res.depth
        if len(z) == 0:
            continue
        edges = np.arange(0.0, float(z.max()) + cpt_bin_m, cpt_bin_m)
        idx = np.digitize(z, edges)
        for b in np.unique(idx):
            m = idx == b
            dmid = float(z[m].mean())
            u, how = unit_at(res.location_id, dmid)
            for key, col in cmap.items():
                vals = res.columns[col][m]
                vals = vals[np.isfinite(vals)]
                if len(vals) == 0:
                    continue
                val = float(10 ** np.mean(np.log10(vals))) if key == "CPT_k" else float(vals.mean())
                rows.append(
                    {
                        "location_id": res.location_id,
                        "depth": dmid,
                        "parameter": key,
                        "value": val,
                        "unit": u,
                        "assigned_by": how,
                        "source": f"SCPT ({cpt_bin_m} m bins)",
                    }
                )
    kb = interpretation.column.kb if interpretation is not None else (model.column.kb if model is not None else None)
    from .spt import material_class

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    excluded = 0
    for r in rows:
        if not r["unit"]:
            continue
        mc = material_class(kb, r["unit"]) if kb is not None else None
        if (mc == "coarse" and r["parameter"] in FINE_ONLY) or (mc == "fine" and r["parameter"] in COARSE_ONLY):
            r["excluded"] = f"{r['parameter']} not applicable to {mc}-grained unit (transition-zone reading)"
            excluded += 1
            continue
        groups[(r["unit"], r["parameter"])].append(r)
    stats = {}
    for (u, p), lst in groups.items():
        st = summarise([r["value"] for r in lst], [r["depth"] for r in lst], u, p, [r["source"] for r in lst])
        if st is not None:
            st.uom = extra_units.get(p, "")
            stats[(u, p)] = st
    notes = []
    if excluded:
        notes.append(f"{excluded} value(s) excluded: correlation not applicable to the unit's material class")
    n_un = sum(1 for r in rows if not r["unit"])
    if n_un:
        notes.append(f"{n_un} value(s) could not be assigned to a unit (no interpretation or model at that point)")
    return EngineeringGroundModel(stats, rows, notes)
