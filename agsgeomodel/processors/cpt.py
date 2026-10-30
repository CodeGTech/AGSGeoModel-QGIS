"""CPT/CPTu interpretation (Groundhog-inspired; pure NumPy).

Method and correlations follow Robertson (2009, 2010) and Robertson & Cabal (2015, *Guide to Cone
Penetration Testing for Geotechnical Engineering*, 6th ed.). Every derived parameter is an
*estimate* from an empirical correlation; the correlation, its coefficients and its range of
applicability are recorded with the results so reviewers can check or override them.

==========================  ===============================================================
Quantity                    Correlation
==========================  ===============================================================
qt                          qc + u2 (1 - a)                                   (a = area ratio)
gamma                       gamma/gamma_w = 0.27 log Rf + 0.36 log(qt/pa) + 1.236   (R&C 2010)
Qtn, n, Ic, Fr              Robertson (2009) iterative normalisation, n <= 1
SBTn zone                   Ic boundaries 1.31 / 2.05 / 2.60 / 2.95 / 3.60 (zones 7..2)
su                          (qt - sigma_v0) / Nkt, Nkt = 14 (range 10-20)       (Ic > 2.6)
OCR                         sigma'_p = k (qt - sigma_v0), k = 0.33               (Ic > 2.6)
Dr                          Dr^2 = Qtn / 350 (uncemented quartz sands)           (Ic < 2.6)
phi'                        17.6 + 11 log Qtn  (Kulhawy & Mayne 1990)             (Ic < 2.6)
E'                          alpha_E (qt - sigma_v0), alpha_E = 0.015*10^(0.55 Ic + 1.68)   (Ic < 2.6)
M                           alpha_M (qt - sigma_v0); Ic > 2.2: alpha_M = min(Qt, 14);
                            Ic <= 2.2: alpha_M = 0.0188*10^(0.55 Ic + 1.68)
Vs, G0                      Vs = [alpha_vs (qt - sigma_v)/pa]^0.5, alpha_vs = 10^(0.55 Ic + 1.68)
k (permeability)            Ic <= 3.27: 10^(0.952 - 3.04 Ic); 3.27 < Ic < 4: 10^(-4.52 - 1.37 Ic)
mv                          1 / M
==========================  ===============================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

PA = 0.101325  # MPa
GAMMA_W = 9.81  # kN/m3

SBTN_ZONES = {
    2: "Organic soils - clay",
    3: "Clay - silty clay to clay",
    4: "Silt mixtures - clayey silt to silty clay",
    5: "Sand mixtures - silty sand to sandy silt",
    6: "Sands - clean sand to silty sand",
    7: "Gravelly sand to dense sand",
}

CORRELATIONS = {
    "unit_weight": "Robertson & Cabal (2010): gamma/gamma_w = 0.27 log Rf + 0.36 log(qt/pa) + 1.236",
    "normalisation": "Robertson (2009): Qtn = ((qt - sv0)/pa)(pa/s'v0)^n, n = 0.381 Ic + 0.05 s'v0/pa - 0.15 <= 1",
    "sbtn": "Robertson (2009/2016) Ic-based SBTn zones 2-7 (zones 1, 8, 9 need the chart)",
    "su": "su = (qt - sv0)/Nkt",
    "ocr": "Kulhawy & Mayne (1990) / Robertson (2009): s'p = k (qt - sv0)",
    "dr": "Kulhawy & Mayne (1990) simplified: Dr^2 = Qtn/350",
    "phi": "Kulhawy & Mayne (1990): phi' = 17.6 + 11 log(Qtn)",
    "E": "Robertson (2009): E' = 0.015*10^(0.55 Ic + 1.68) (qt - sv0)",
    "M": "Robertson (2009): M = alpha_M (qt - sv0)",
    "G0": "Robertson (2009): Vs = [10^(0.55 Ic + 1.68) (qt - sv)/pa]^0.5, G0 = rho Vs^2",
    "k": "Robertson (2010) Ic-based permeability estimate",
}


@dataclass
class CPTSettings:
    nkt: float = 14.0
    k_ocr: float = 0.33
    c_dr: float = 350.0
    area_ratio: float = 0.8  # used if not supplied in SCPG_CAR and u2 is available
    water_depth: float | None = None  # m below ground; None -> SCPG_WAT or 'dry'
    gamma_min: float = 14.0
    gamma_max: float = 22.5
    gamma_above_water: float | None = None


@dataclass
class CPTResult:
    location_id: str
    test_id: str
    depth: np.ndarray
    columns: dict[str, np.ndarray] = field(default_factory=dict)
    settings: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def __getitem__(self, k: str) -> np.ndarray:
        return self.columns[k]

    def table(self) -> list[dict]:
        keys = list(self.columns)
        return [
            {
                "location_id": self.location_id,
                "test_id": self.test_id,
                "depth": float(self.depth[i]),
                **{k: (None if not np.isfinite(self.columns[k][i]) else float(self.columns[k][i])) for k in keys},
            }
            for i in range(len(self.depth))
        ]

    def layer_average(self, top: float, base: float, key: str) -> float:
        m = (self.depth >= top) & (self.depth < base) & np.isfinite(self.columns[key])
        return float(np.mean(self.columns[key][m])) if m.any() else np.nan


def sbtn_zone(ic: np.ndarray) -> np.ndarray:
    ic = np.asarray(ic, float)
    z = np.full(ic.shape, 0, dtype=int)
    z = np.where(ic < 1.31, 7, z)
    z = np.where((ic >= 1.31) & (ic < 2.05), 6, z)
    z = np.where((ic >= 2.05) & (ic < 2.60), 5, z)
    z = np.where((ic >= 2.60) & (ic < 2.95), 4, z)
    z = np.where((ic >= 2.95) & (ic < 3.60), 3, z)
    z = np.where(ic >= 3.60, 2, z)
    return np.where(np.isfinite(ic), z, 0)


def interpret_cpt(
    depth,
    qc,
    fs,
    u2=None,
    qt=None,
    location_id: str = "",
    test_id: str = "1",
    settings: CPTSettings | None = None,
    area_ratio: float | None = None,
    water_depth: float | None = None,
    max_iter: int = 30,
) -> CPTResult:
    """Interpret one sounding. Inputs in metres and MPa; outputs in kPa, kN/m3, MPa, m/s, degrees."""
    s = settings or CPTSettings()
    z = np.asarray(depth, float)
    qc = np.asarray(qc, float)
    fs = np.asarray(fs, float)
    notes = []
    a = area_ratio if area_ratio is not None else s.area_ratio
    if qt is not None and np.isfinite(qt).any():
        qt_ = np.where(np.isfinite(qt), qt, qc)
    elif u2 is not None and np.isfinite(u2).any():
        qt_ = qc + np.where(np.isfinite(u2), u2, 0.0) * (1.0 - a)
        notes.append(f"qt corrected for pore pressure with area ratio a = {a}")
    else:
        qt_ = qc.copy()
        notes.append("no u2: qt taken as qc (no pore pressure correction)")
    qt_ = np.maximum(qt_, 1e-4)
    wd = water_depth if water_depth is not None else s.water_depth
    if wd is None:
        notes.append(
            "groundwater level unknown: assumed below the sounding (dry); effective stresses may be overestimated"
        )
        wd = np.inf
    rf = np.where(qt_ > 0, 100.0 * fs / qt_, np.nan)
    gamma = GAMMA_W * (0.27 * np.log10(np.clip(rf, 0.1, None)) + 0.36 * np.log10(qt_ / PA) + 1.236)
    gamma = np.clip(gamma, s.gamma_min, s.gamma_max)
    if s.gamma_above_water is not None:
        gamma = np.where(z < wd, s.gamma_above_water, gamma)
    dz = np.diff(np.concatenate([[0.0], z]))
    sv0 = np.cumsum(gamma * dz) / 1000.0  # MPa
    u0 = np.where(z > wd, (z - wd) * GAMMA_W / 1000.0, 0.0)  # MPa (hydrostatic)
    s_eff = np.maximum(sv0 - u0, 1e-4)
    qnet = np.maximum(qt_ - sv0, 1e-4)
    Fr = 100.0 * fs / qnet
    Qt1 = qnet / s_eff
    n = np.ones_like(z)
    for _ in range(max_iter):
        Qtn = (qnet / PA) * (PA / s_eff) ** n
        ic = np.sqrt((3.47 - np.log10(np.maximum(Qtn, 1e-3))) ** 2 + (np.log10(np.maximum(Fr, 1e-3)) + 1.22) ** 2)
        n_new = np.clip(0.381 * ic + 0.05 * (s_eff / PA) - 0.15, 0.0, 1.0)
        if np.nanmax(np.abs(n_new - n)) < 1e-3:
            n = n_new
            break
        n = n_new
    Qtn = (qnet / PA) * (PA / s_eff) ** n
    ic = np.sqrt((3.47 - np.log10(np.maximum(Qtn, 1e-3))) ** 2 + (np.log10(np.maximum(Fr, 1e-3)) + 1.22) ** 2)
    fine = ic > 2.6
    coarse = ~fine
    su = np.where(fine, qnet * 1000.0 / s.nkt, np.nan)  # kPa
    sp = np.where(fine, s.k_ocr * qnet, np.nan)  # MPa
    ocr = np.where(fine, sp / s_eff, np.nan)
    dr = np.where(coarse, np.sqrt(np.clip(Qtn / s.c_dr, 0.0, 1.0)), np.nan)
    phi = np.where(coarse, 17.6 + 11.0 * np.log10(np.maximum(Qtn, 1.0)), np.nan)
    alpha_e = 0.015 * 10 ** (0.55 * ic + 1.68)
    E = np.where(coarse, alpha_e * qnet, np.nan)  # MPa; correlation is for uncemented sands only
    alpha_m = np.where(ic > 2.2, np.minimum(Qt1, 14.0), 0.0188 * 10 ** (0.55 * ic + 1.68))
    M = alpha_m * qnet  # MPa
    alpha_vs = 10 ** (0.55 * ic + 1.68)
    vs = np.sqrt(np.maximum(alpha_vs * qnet / PA, 0.0))  # m/s
    g0 = (gamma / 9.81) * vs**2 / 1000.0  # MPa (rho in Mg/m3)
    k = np.where(ic <= 3.27, 10 ** (0.952 - 3.04 * ic), 10 ** (-4.52 - 1.37 * ic))
    k = np.where(ic >= 4.0, np.nan, k)
    bq = np.full_like(z, np.nan)
    if u2 is not None and np.isfinite(u2).any():
        bq = (np.asarray(u2, float) - u0) / qnet
    cols = {
        "qc_MPa": qc,
        "qt_MPa": qt_,
        "fs_MPa": fs,
        "Rf_pct": rf,
        "gamma_kNm3": gamma,
        "sv0_kPa": sv0 * 1000,
        "u0_kPa": u0 * 1000,
        "sv0_eff_kPa": s_eff * 1000,
        "Qt1": Qt1,
        "Qtn": Qtn,
        "Fr_pct": Fr,
        "n": n,
        "Bq": bq,
        "Ic": ic,
        "SBTn": sbtn_zone(ic).astype(float),
        "su_kPa": su,
        "OCR": ocr,
        "Dr": dr,
        "phi_deg": phi,
        "E_MPa": E,
        "M_MPa": M,
        "mv_m2MN": 1.0 / np.maximum(M, 1e-6),
        "Vs_ms": vs,
        "G0_MPa": g0,
        "k_ms": k,
    }
    return CPTResult(
        location_id,
        test_id,
        z,
        cols,
        {**s.__dict__, "area_ratio_used": a, "water_depth_used": None if not np.isfinite(wd) else wd},
        notes,
    )


def interpret_project_cpts(
    project, settings: CPTSettings | None = None, water_depths: dict[str, float] | None = None
) -> list[CPTResult]:
    out = []
    for c in project.cpt:
        wd = (water_depths or {}).get(c.location_id, c.water_depth)
        out.append(interpret_cpt(c.depth, c.qc, c.fs, c.u2, c.qt, c.location_id, c.test_id, settings, c.area_ratio, wd))
    return out


def layers_from_cpt(res: CPTResult, min_thickness: float = 0.3, ic_step: float = 0.35) -> list[dict]:
    """Segment a CPT into SBTn layers (moving median on Ic, merge thin layers)."""
    ic = res["Ic"]
    zone = res["SBTn"].astype(int)
    k = 5
    if len(ic) > k:
        pad = np.pad(ic, (k, k), mode="edge")
        med = np.array([np.nanmedian(pad[i : i + 2 * k + 1]) for i in range(len(ic))])
        zone = sbtn_zone(med)
    layers = []
    start = 0
    for i in range(1, len(zone) + 1):
        if i == len(zone) or zone[i] != zone[start]:
            layers.append({"top": float(res.depth[start]), "base": float(res.depth[i - 1]), "zone": int(zone[start])})
            start = i
    merged: list[dict] = []
    for L in layers:
        if merged and (L["base"] - L["top"] < min_thickness):
            merged[-1]["base"] = L["base"]
        else:
            merged.append(dict(L))
    for L in merged:
        L["description"] = SBTN_ZONES.get(L["zone"], "unclassified")
        L["Ic_mean"] = res.layer_average(L["top"], L["base"] + 1e-6, "Ic")
    return merged
