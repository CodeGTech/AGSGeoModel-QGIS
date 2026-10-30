"""Synthetic Thames-side site with a known geological "truth".

**This is synthetic data.** It is geologically plausible for the central/eastern London Basin
(Made Ground / Alluvium / River Terrace Deposits over London Clay, Harwich Formation, Lambeth
Group, Thanet Formation and Chalk, with a north-dipping Palaeogene succession, a scour hollow and
a small normal fault), but it does not describe any real site.

Because the truth is known analytically, the test-suite can measure how well the GeoReasoner
recovers formations and how close the modelled surfaces are to the truth at hold-out holes.
The generator also plants realistic defects (an unlogged interval, a wrong logged code, chalk
fill above alluvium, a gap in a log) so the validation engine can be demonstrated.

Run ``python -m agsgeomodel.examples.synthetic <out_dir>`` to write the example project.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..importers.ags.reader import AGSTable, write_ags4

E0, N0 = 545000.0, 180000.0  # origin of the synthetic site (BNG)
FAULT = ((E0 + 820.0, N0 + 0.0), (E0 + 1000.0, N0 + 800.0))
FAULT_THROW = 4.0  # downthrown to the east (right of the digitised direction)
HDD_ENTRY = (E0 + 520.0, N0 + 110.0)
HDD_EXIT = (E0 + 560.0, N0 + 610.0)


def _sig(t):
    return 1.0 / (1.0 + np.exp(-t))


@dataclass
class SyntheticTruth:
    """Analytic surfaces (elevations in m OD, thicknesses in m) as functions of BNG x, y."""

    def local(self, x, y):
        return np.asarray(x, float) - E0, np.asarray(y, float) - N0

    def ground(self, x, y):
        u, v = self.local(x, y)
        return 1.7 + 6.0 * _sig((v - 380.0) / 70.0) + 0.4 * np.sin(u / 180.0)

    def thk_mgr(self, x, y):
        u, v = self.local(x, y)
        return 0.5 + 1.8 * np.exp(-((u - 950.0) ** 2 + (v - 600.0) ** 2) / (2 * 220.0**2)) + 0.2 * np.cos(u / 90.0) ** 2

    def thk_alv(self, x, y):
        u, v = self.local(x, y)
        hollow = 2.5 * np.exp(-((u - 300.0) ** 2 + (v - 200.0) ** 2) / (2 * 60.0**2))  # scour hollow fill
        return 7.0 * _sig((370.0 - v) / 45.0) * (1.0 + 0.12 * np.sin(u / 150.0)) + hollow * _sig((370.0 - v) / 45.0)

    def thk_rtd(self, x, y):
        u, v = self.local(x, y)
        return 3.3 + 0.9 * np.sin(u / 210.0) + 0.8 * np.cos(v / 170.0)

    def fault_offset(self, x, y):
        (x0, y0), (x1, y1) = FAULT
        cross = (x1 - x0) * (np.asarray(y, float) - y0) - (y1 - y0) * (np.asarray(x, float) - x0)
        return np.where(cross < 0, -FAULT_THROW, 0.0)  # right of line (east) is downthrown

    def base_lc(self, x, y):
        u, v = self.local(x, y)
        return -6.0 - 0.012 * (v - 400.0) - 0.0015 * (u - 600.0) + self.fault_offset(x, y)

    def thk_hwh(self, x, y):
        u, v = self.local(x, y)
        return 2.0 + 0.8 * np.sin(u / 250.0)

    def thk_lmbe(self, x, y):
        u, v = self.local(x, y)
        return 12.0 + 2.0 * np.cos(u / 300.0) - 0.003 * (v - 400.0)

    def thk_tab(self, x, y):
        u, v = self.local(x, y)
        return 17.0 + 2.0 * np.sin(v / 200.0)

    # ---- derived
    def rockhead(self, x, y):
        return self.ground(x, y) - self.thk_mgr(x, y) - self.thk_alv(x, y) - self.thk_rtd(x, y)

    def bases(self, x, y) -> dict[str, np.ndarray]:
        b_lc = self.base_lc(x, y)
        b_hwh = b_lc - self.thk_hwh(x, y)
        b_lmbe = b_hwh - self.thk_lmbe(x, y)
        b_tab = b_lmbe - self.thk_tab(x, y)
        return {"LC": b_lc, "HWH": b_hwh, "LMBE": b_lmbe, "TAB": b_tab}

    def column_at(self, x: float, y: float) -> list[tuple[str, float, float]]:
        """[(unit, z_top, z_base)] after erosion, top to bottom (CK base = -inf)."""
        g = float(self.ground(x, y))
        out = []
        z = g
        for unit, t in (("MGR", self.thk_mgr(x, y)), ("ALV", self.thk_alv(x, y)), ("RTDU", self.thk_rtd(x, y))):
            t = float(t)
            if t > 0.05:
                out.append((unit, z, z - t))
                z -= t
        rh = z
        b = {k: float(v) for k, v in self.bases(x, y).items()}
        tops = {"LC": np.inf, "HWH": b["LC"], "LMBE": b["HWH"], "TAB": b["LMBE"], "CK": b["TAB"]}
        bases = {"LC": b["LC"], "HWH": b["HWH"], "LMBE": b["LMBE"], "TAB": b["TAB"], "CK": -np.inf}
        for unit in ("LC", "HWH", "LMBE", "TAB", "CK"):
            top = min(tops[unit], rh)
            if top > bases[unit] + 0.05:
                out.append((unit, top, bases[unit]))
        return out


# ------------------------------------------------------------------------------- descriptions
DESCRIPTIONS = {
    "TOPSOIL": ["Dark brown slightly sandy TOPSOIL with frequent rootlets."],
    "MGR": [
        "MADE GROUND: Brown slightly gravelly sandy clay with frequent brick, concrete and occasional ash fragments.",
        "MADE GROUND: Grey sandy angular to subrounded GRAVEL of concrete, brick and flint (hardcore).",
        "MADE GROUND: Soft dark grey clayey silt with occasional brick fragments and clinker.",
    ],
    "ALV": [
        "Soft dark grey organic silty CLAY with occasional shell fragments.",
        "Very soft grey slightly sandy organic CLAY with pockets of black amorphous peat.",
        "Soft to firm grey mottled orange brown silty CLAY with rootlets (desiccated crust).",
    ],
    "PEAT": ["Dark brown fibrous PEAT with wood fragments."],
    "RTDU": [
        "Medium dense orange brown slightly clayey sandy subangular to rounded fine to coarse flint GRAVEL.",
        "Dense brown very gravelly SAND. Gravel is subrounded fine to coarse flint.",
    ],
    "LC": [
        "Firm brown mottled grey fissured silty CLAY with occasional selenite crystals.",
        "Stiff to very stiff dark grey fissured silty CLAY with rare claystone nodules and pyrite.",
    ],
    "HWH": [
        "Very dense dark greenish grey slightly clayey fine to medium SAND with frequent well rounded black flint "
        "pebbles and shell fragments."
    ],
    "LMBE": [
        "Very stiff red brown and grey mottled CLAY.",
        "Very stiff dark grey laminated CLAY with frequent shells and lignite.",
        "Dense dark green glauconitic fine to coarse SAND with rounded black flint gravel.",
    ],
    "TAB": [
        "Very dense greenish grey slightly clayey silty fine SAND with glauconite.",
        "Very dense grey slightly silty fine SAND.",
    ],
    "CK": [
        "Structureless CHALK composed of soft white putty chalk with occasional flint. Grade Dm.",
        "Weak white CHALK with occasional flint bands. Grade B.",
    ],
}
BRACKET = {
    "MGR": None,
    "TOPSOIL": None,
    "ALV": "ALLUVIUM",
    "PEAT": "ALLUVIUM",
    "RTDU": "RIVER TERRACE DEPOSITS",
    "LC": "LONDON CLAY FORMATION",
    "HWH": "HARWICH FORMATION",
    "LMBE": "LAMBETH GROUP",
    "TAB": "THANET FORMATION",
    "CK": "CHALK GROUP",
}
PROJECT_CODES = {
    "MGR": "MG",
    "TOPSOIL": "TS",
    "ALV": "ALV",
    "PEAT": "ALV",
    "RTDU": "RTD",
    "LC": "LC",
    "HWH": "HWH",
    "LMBE": "LMBE",
    "TAB": "TAB",
    "CK": "CK",
}
SPT_RANGE = {
    "MGR": (5, 15),
    "ALV": (0, 4),
    "PEAT": (0, 2),
    "RTDU": (15, 40),
    "LC": (15, 40),
    "HWH": (40, 60),
    "LMBE": (30, 60),
    "TAB": (50, 90),
    "CK": (15, 40),
}
CPT_UNIT = {
    "MGR": (3.0, 2.0),
    "ALV": (0.5, 4.5),
    "PEAT": (0.25, 8.0),
    "RTDU": (18.0, 0.8),
    "LC": (3.0, 4.0),
    "HWH": (25.0, 1.2),
    "LMBE": (6.0, 3.5),
    "TAB": (30.0, 1.0),
    "CK": (12.0, 1.5),
}  # qc MPa, Rf %


@dataclass
class Hole:
    id: str
    kind: str  # BH | CPT | TP | WS
    x: float
    y: float
    depth: float
    intervals: list[dict] = field(default_factory=list)


LAYOUT = [
    # id, kind, local u, v, depth
    ("BH01", "BH", 515, 120, 40.0),
    ("BH02", "BH", 525, 240, 40.0),
    ("BH03", "BH", 535, 360, 40.0),
    ("BH04", "BH", 545, 480, 45.0),
    ("BH05", "BH", 555, 600, 45.0),
    ("BH06", "BH", 905, 300, 40.0),
    ("BH07", "BH", 300, 150, 40.0),
    ("BH08", "BH", 250, 520, 45.0),
    ("BH09", "BH", 760, 160, 40.0),
    ("BH10", "BH", 760, 560, 45.0),
    ("BH11", "BH", 1100, 600, 45.0),
    ("BH12", "BH", 120, 300, 40.0),
    ("BH13", "BH", 1080, 220, 40.0),
    ("BH14", "BH", 850, 300, 40.0),
    ("CPT01", "CPT", 500, 180, 22.0),
    ("CPT02", "CPT", 540, 300, 22.0),
    ("CPT03", "CPT", 400, 250, 20.0),
    ("CPT04", "CPT", 650, 260, 20.0),
    ("CPT05", "CPT", 200, 120, 20.0),
    ("CPT06", "CPT", 980, 140, 20.0),
    ("TP01", "TP", 400, 650, 3.0),
    ("TP02", "TP", 700, 700, 3.0),
    ("TP03", "TP", 1000, 720, 3.0),
    ("TP04", "TP", 150, 680, 3.0),
    ("WS01", "WS", 600, 520, 5.0),
    ("WS02", "WS", 900, 500, 5.0),
    ("WS03", "WS", 350, 450, 5.0),
]


def build_holes(truth: SyntheticTruth, rng: np.random.Generator) -> list[Hole]:
    holes = []
    for hid, kind, u, v, depth in LAYOUT:
        x, y = E0 + u, N0 + v
        g = float(truth.ground(x, y))
        h = Hole(hid, kind, x, y, depth)
        col = truth.column_at(x, y)
        for unit, zt, zb in col:
            top = max(0.0, g - zt)
            base = min(depth, g - zb) if np.isfinite(zb) else depth
            if base <= top + 1e-6:
                continue
            h.intervals.append({"unit": unit, "top": round(top, 2), "base": round(base, 2)})
            if base >= depth:
                break
        # split thick units into sub-strata with depth-dependent descriptions
        split = []
        for iv in h.intervals:
            th = iv["base"] - iv["top"]
            n = 1 if th < 6 or iv["unit"] in ("MGR", "HWH") else int(min(4, th // 5))
            edges = np.linspace(iv["top"], iv["base"], n + 1)
            for k in range(n):
                split.append(
                    {
                        "unit": iv["unit"],
                        "top": round(float(edges[k]), 2),
                        "base": round(float(edges[k + 1]), 2),
                        "k": k,
                        "n": n,
                    }
                )
        h.intervals = split
        holes.append(h)
    return holes


def _desc(unit: str, k: int, n: int, rng) -> str:
    opts = DESCRIPTIONS[unit]
    if unit == "LC":
        d = opts[0] if k == 0 else opts[1]
    elif unit == "LMBE":
        d = opts[2] if (k == n - 1 and n > 1) else opts[rng.integers(0, 2)]
    elif unit == "CK":
        d = opts[0] if k == 0 else opts[1]
    else:
        d = opts[rng.integers(0, len(opts))]
    return d


def plant_defects(holes: list[Hole]) -> dict[str, str]:
    """Realistic logging defects for the validation demonstration (documented in the README)."""
    notes = {}
    by = {h.id: h for h in holes}
    # BH07: an unlogged (no recovery) interval spanning most of the Lambeth Group
    h = by["BH07"]
    lm = [iv for iv in h.intervals if iv["unit"] == "LMBE"]
    if lm:
        a, b = lm[0]["top"], lm[-1]["base"] - 1.0
        new = []
        for iv in h.intervals:
            if iv["unit"] == "LMBE":
                continue
            new.append(iv)
        new.append({"unit": "UNKNOWN", "top": a, "base": round(b, 2), "k": 0, "n": 1})
        new.append({"unit": "LMBE", "top": round(b, 2), "base": lm[-1]["base"], "k": 1, "n": 2})
        h.intervals = sorted(new, key=lambda i: i["top"])
        notes["BH07"] = f"No recovery {a:.2f}-{b:.2f} m (Lambeth Group not logged) -> missing-layer inference"
    # BH10: one Thanet interval wrongly coded LC by the logger
    h = by["BH10"]
    tabs = [iv for iv in h.intervals if iv["unit"] == "TAB"]
    if tabs:
        tabs[0]["wrong_code"] = "LC"
        notes["BH10"] = f"Thanet sand at {tabs[0]['top']:.2f} m logged with GEOL_GEOL=LC (logging error)"
    # BH12: chalk fill placed above the alluvium (reworked chalk / hardstanding)
    h = by["BH12"]
    first = h.intervals[0]
    if first["unit"] == "MGR":
        mid = round(first["top"] + 0.3, 2)
        base = max(first["base"], mid + 0.4)
        nxt = h.intervals[1]
        nxt["top"] = max(nxt["top"], round(base, 2))
        h.intervals = [
            {"unit": "MGR", "top": first["top"], "base": mid, "k": 0, "n": 1},
            {"unit": "CHALKFILL", "top": mid, "base": round(base, 2), "k": 0, "n": 1},
        ] + h.intervals[1:]
        notes["BH12"] = f"Chalk fill {mid:.2f}-{base:.2f} m described without the words 'made ground'"
    # TP03: a 0.1 m gap in the log
    h = by["TP03"]
    if len(h.intervals) >= 2:
        h.intervals[1]["top"] = round(h.intervals[1]["top"] + 0.1, 2)
        notes["TP03"] = "0.1 m gap between first and second stratum (AGS engineering check E04)"
    return notes


# ------------------------------------------------------------------------------- AGS writing
def _f(v: float, dp: int) -> str:
    return f"{v:.{dp}f}"


def write_example(out_dir: str | Path, seed: int = 7) -> dict:
    """Write the synthetic AGS4 file and companion GIS files. Returns a manifest dict."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    truth = SyntheticTruth()
    holes = build_holes(truth, rng)
    defects = plant_defects(holes)
    T: dict[str, AGSTable] = {}

    def table(name, heads, units, types):
        T[name] = AGSTable(name, heads, units, types)
        return T[name]

    table(
        "PROJ",
        ["PROJ_ID", "PROJ_NAME", "PROJ_LOC", "PROJ_CLNT", "PROJ_CONT", "PROJ_ENG"],
        ["", "", "", "", "", ""],
        ["ID", "X", "X", "X", "X", "X"],
    ).rows.append(
        [
            "SYN001",
            "Synthetic Thames-side HDD crossing (NOT REAL DATA)",
            "Synthetic site, London Basin",
            "Example client",
            "Example GI contractor",
            "AGSGeoModel examples",
        ]
    )
    table(
        "TRAN",
        ["TRAN_ISNO", "TRAN_DATE", "TRAN_PROD", "TRAN_STAT", "TRAN_AGS", "TRAN_RECV", "TRAN_DLIM", "TRAN_RCON"],
        ["", "yyyy-mm-dd", "", "", "", "", "", ""],
        ["X", "DT", "X", "X", "X", "X", "X", "X"],
    ).rows.append(["1", "2026-10-28", "AGSGeoModel synthetic generator", "FINAL", "4.1.1", "Example client", "+", "+"])
    loca = table(
        "LOCA",
        [
            "LOCA_ID",
            "LOCA_TYPE",
            "LOCA_STAT",
            "LOCA_NATE",
            "LOCA_NATN",
            "LOCA_GREF",
            "LOCA_GL",
            "LOCA_REM",
            "LOCA_FDEP",
            "LOCA_STAR",
            "LOCA_DATM",
        ],
        ["", "", "", "m", "m", "", "m", "", "m", "yyyy-mm-dd", ""],
        ["ID", "PA", "PA", "2DP", "2DP", "PA", "2DP", "X", "2DP", "DT", "X"],
    )
    hdph = table(
        "HDPH", ["LOCA_ID", "HDPH_TOP", "HDPH_BASE", "HDPH_TYPE"], ["", "m", "m", ""], ["ID", "2DP", "2DP", "PA"]
    )
    geol = table(
        "GEOL",
        ["LOCA_ID", "GEOL_TOP", "GEOL_BASE", "GEOL_DESC", "GEOL_LEG", "GEOL_GEOL", "GEOL_BGS"],
        ["", "m", "m", "", "", "", ""],
        ["ID", "2DP", "2DP", "X", "PA", "PA", "PA"],
    )
    ispt = table(
        "ISPT",
        ["LOCA_ID", "ISPT_TOP", "ISPT_MAIN", "ISPT_NPEN", "ISPT_NVAL", "ISPT_REP", "ISPT_TYPE", "ISPT_ERAT"],
        ["", "m", "", "mm", "", "", "", "%"],
        ["ID", "2DP", "0DP", "0DP", "0DP", "X", "PA", "0DP"],
    )
    samp = table(
        "SAMP",
        ["LOCA_ID", "SAMP_TOP", "SAMP_REF", "SAMP_TYPE", "SAMP_ID", "SAMP_BASE"],
        ["", "m", "", "", "", "m"],
        ["ID", "2DP", "X", "PA", "ID", "2DP"],
    )
    llpl = table(
        "LLPL",
        [
            "LOCA_ID",
            "SAMP_TOP",
            "SAMP_REF",
            "SAMP_TYPE",
            "SAMP_ID",
            "SPEC_REF",
            "SPEC_DPTH",
            "LLPL_LL",
            "LLPL_PL",
            "LLPL_PI",
        ],
        ["", "m", "", "", "", "", "m", "%", "%", ""],
        ["ID", "2DP", "X", "PA", "ID", "X", "2DP", "0DP", "XN", "0DP"],
    )
    lnmc = table(
        "LNMC",
        ["LOCA_ID", "SAMP_TOP", "SAMP_REF", "SAMP_TYPE", "SAMP_ID", "SPEC_REF", "SPEC_DPTH", "LNMC_MC"],
        ["", "m", "", "", "", "", "m", "%"],
        ["ID", "2DP", "X", "PA", "ID", "X", "2DP", "XN"],
    )
    gchm = table(
        "GCHM",
        [
            "LOCA_ID",
            "SAMP_TOP",
            "SAMP_REF",
            "SAMP_TYPE",
            "SAMP_ID",
            "SPEC_REF",
            "SPEC_DPTH",
            "GCHM_CODE",
            "GCHM_METH",
            "GCHM_TTYP",
            "GCHM_RESL",
            "GCHM_UNIT",
        ],
        ["", "m", "", "", "", "", "m", "", "", "", "", ""],
        ["ID", "2DP", "X", "PA", "ID", "X", "2DP", "PA", "X", "PA", "XN", "PU"],
    )
    grag = table(
        "GRAG",
        [
            "LOCA_ID",
            "SAMP_TOP",
            "SAMP_REF",
            "SAMP_TYPE",
            "SAMP_ID",
            "SPEC_REF",
            "SPEC_DPTH",
            "GRAG_GRAV",
            "GRAG_SAND",
            "GRAG_FINE",
        ],
        ["", "m", "", "", "", "", "m", "%", "%", "%"],
        ["ID", "2DP", "X", "PA", "ID", "X", "2DP", "1DP", "1DP", "1DP"],
    )
    core = table(
        "CORE",
        ["LOCA_ID", "CORE_TOP", "CORE_BASE", "CORE_PREC", "CORE_SREC", "CORE_RQD"],
        ["", "m", "m", "%", "%", "%"],
        ["ID", "2DP", "2DP", "0DP", "0DP", "0DP"],
    )
    wstg = table(
        "WSTG", ["LOCA_ID", "WSTG_DPTH", "WSTG_CAS", "WSTG_REM"], ["", "m", "m", ""], ["ID", "2DP", "2DP", "X"]
    )
    wstd = table(
        "WSTD", ["LOCA_ID", "WSTG_DPTH", "WSTD_NMIN", "WSTD_POST"], ["", "m", "min", "m"], ["ID", "2DP", "0DP", "2DP"]
    )
    mong = table(
        "MONG",
        ["LOCA_ID", "MONG_ID", "MONG_DIS", "MONG_TYPE", "MONG_TRZ", "MONG_BRZ"],
        ["", "", "m", "", "m", "m"],
        ["ID", "X", "2DP", "PA", "2DP", "2DP"],
    )
    mond = table(
        "MOND",
        ["LOCA_ID", "MONG_ID", "MONG_DIS", "MOND_DTIM", "MOND_TYPE", "MOND_REF", "MOND_RDNG", "MOND_UNIT"],
        ["", "", "m", "yyyy-mm-ddThh:mm:ss", "", "", "", ""],
        ["ID", "X", "2DP", "DT", "PA", "X", "XN", "PU"],
    )
    scpg = table(
        "SCPG",
        ["LOCA_ID", "SCPG_TESN", "SCPG_TYPE", "SCPG_WAT", "SCPG_CAR"],
        ["", "", "", "m", ""],
        ["ID", "X", "PA", "2DP", "3DP"],
    )
    scpt = table(
        "SCPT",
        ["LOCA_ID", "SCPG_TESN", "SCPT_DPTH", "SCPT_RES", "SCPT_FRES", "SCPT_PWP2"],
        ["", "", "m", "MPa", "MPa", "MPa"],
        ["ID", "X", "2DP", "3DP", "4DP", "4DP"],
    )
    s_count = 0
    for h in holes:
        g = float(truth.ground(h.x, h.y))
        gl = g + (0.0 if h.id != "WS02" else 0.0)
        ltype = {"BH": "CP+RC", "CPT": "CPT", "TP": "TP", "WS": "WS"}[h.kind]
        depth = h.intervals[-1]["base"] if h.intervals else h.depth
        loca.rows.append(
            [
                h.id,
                ltype,
                "FINAL",
                _f(h.x, 2),
                _f(h.y, 2),
                "OSGB",
                _f(gl, 2),
                "SYNTHETIC LOCATION - NOT REAL DATA",
                _f(depth, 2),
                "2026-03-02",
                "OD",
            ]
        )
        if h.kind == "BH":
            rh = next((iv["top"] for iv in h.intervals if iv["unit"] in ("LC", "HWH", "LMBE", "TAB", "CK")), depth)
            cp_base = min(depth, round(max(rh + 6.0, 15.0), 2))
            hdph.rows.append([h.id, "0.00", _f(cp_base, 2), "CP"])
            if cp_base < depth:
                hdph.rows.append([h.id, _f(cp_base, 2), _f(depth, 2), "RC"])
        else:
            hdph.rows.append([h.id, "0.00", _f(depth, 2), {"CPT": "CPT", "TP": "TP", "WS": "WS"}[h.kind]])
        if h.kind == "CPT":
            n_rows = int(depth / 0.02)
            dep = np.round(np.linspace(0.02, depth, n_rows), 2)
            scpg.rows.append([h.id, "1", "CPTU", "1.50", "0.800"])
            col = truth.column_at(h.x, h.y)
            for d in dep:
                z = g - d
                unit = next((u for u, zt, zb in col if zb < z <= zt + 1e-9), "CK")
                qc0, rf = CPT_UNIT.get(unit, (5.0, 2.0))
                qc = qc0 * (1 + 0.04 * d if unit in ("LC", "ALV") else 1.0) * float(np.exp(rng.normal(0, 0.18)))
                fs = qc * rf / 100.0 * float(np.exp(rng.normal(0, 0.12)))
                u0 = max(0.0, d - 1.5) * 0.00981
                u2 = u0 + (qc * 0.35 if unit in ("LC", "ALV", "PEAT", "LMBE") else -0.01 * qc)
                scpt.rows.append([h.id, "1", _f(d, 2), _f(qc, 3), _f(fs, 4), _f(max(u2, -0.05), 4)])
            continue
        for iv in h.intervals:
            unit = iv["unit"]
            if unit == "UNKNOWN":
                geol.rows.append([h.id, _f(iv["top"], 2), _f(iv["base"], 2), "No recovery.", "", "", ""])
                continue
            if unit == "CHALKFILL":
                geol.rows.append(
                    [
                        h.id,
                        _f(iv["top"], 2),
                        _f(iv["base"], 2),
                        "Soft white CHALK recovered as slightly clayey angular gravel.",
                        "",
                        "",
                        "",
                    ]
                )
                continue
            d = _desc(unit, iv.get("k", 0), iv.get("n", 1), rng)
            br = BRACKET.get(unit)
            if br and rng.random() < 0.6:
                d = f"{d} ({br})"
            code = iv.get("wrong_code") or (PROJECT_CODES[unit] if rng.random() < 0.7 else "")
            bgs = (
                {
                    "MGR": "MGR",
                    "RTDU": "RTDU",
                    "LC": "LC",
                    "HWH": "HWH",
                    "LMBE": "LMBE",
                    "TAB": "TAB",
                    "CK": "CK",
                    "ALV": "ALV",
                }.get(unit, "")
                if rng.random() < 0.3 and not iv.get("wrong_code")
                else ""
            )
            geol.rows.append([h.id, _f(iv["top"], 2), _f(iv["base"], 2), d, "", code, bgs])
            # SPT every 1.5 m in boreholes below 1 m
            if h.kind == "BH":
                z = iv["top"] + 0.5 if iv["top"] > 0.9 else 1.0
                while z < iv["base"] - 0.2:
                    lo, hi = SPT_RANGE.get(unit, (10, 30))
                    n = int(np.clip(rng.normal((lo + hi) / 2 + 0.15 * z, (hi - lo) / 5), lo, hi + 30))
                    if n >= 50:
                        pen = int(np.clip(300 * 50 / n, 25, 295))
                        ispt.rows.append([h.id, _f(z, 2), "50", str(150 + pen), "", f"50/{pen}mm", "S", "70"])
                    else:
                        ispt.rows.append([h.id, _f(z, 2), str(n), "450", str(n), f"N={n}", "S", "70"])
                    z += 1.5
            # samples / lab
            if h.kind in ("BH", "WS") and iv["base"] - iv["top"] >= 0.6:
                s_count += 1
                sid = f"S{s_count:04d}"
                sd = round(iv["top"] + 0.3, 2)
                samp.rows.append(
                    [h.id, _f(sd, 2), sid, "U" if unit in ("LC", "ALV", "LMBE") else "B", sid, _f(sd + 0.45, 2)]
                )
                if unit in ("LC", "ALV", "LMBE"):
                    ll = {"LC": 72, "ALV": 70, "LMBE": 60}[unit] + int(rng.normal(0, 6))
                    pl = {"LC": 27, "ALV": 30, "LMBE": 24}[unit] + int(rng.normal(0, 2))
                    llpl.rows.append([h.id, _f(sd, 2), sid, "U", sid, "1", _f(sd, 2), str(ll), str(pl), str(ll - pl)])
                    mc = {"LC": 28, "ALV": 60, "LMBE": 22}[unit] + rng.normal(0, 3)
                    lnmc.rows.append([h.id, _f(sd, 2), sid, "U", sid, "1", _f(sd, 2), f"{mc:.0f}"])
                if unit == "LC":
                    so4 = max(0.1, rng.normal(1.2, 0.5))
                    gchm.rows.append(
                        [h.id, _f(sd, 2), sid, "U", sid, "1", _f(sd, 2), "SO4", "BRE SD1", "WS", f"{so4:.2f}", "g/l"]
                    )
                    gchm.rows.append(
                        [
                            h.id,
                            _f(sd, 2),
                            sid,
                            "U",
                            sid,
                            "1",
                            _f(sd, 2),
                            "pH",
                            "BRE SD1",
                            "WS",
                            f"{rng.normal(7.4, 0.3):.1f}",
                            "-",
                        ]
                    )
                if unit in ("TAB", "RTDU", "HWH"):
                    grav, fine = {"TAB": (1.0, 18.0), "RTDU": (58.0, 6.0), "HWH": (12.0, 14.0)}[unit]
                    grav = max(0.0, grav + rng.normal(0, 3))
                    fine = max(0.0, fine + rng.normal(0, 2))
                    grag.rows.append(
                        [
                            h.id,
                            _f(sd, 2),
                            sid,
                            "B",
                            sid,
                            "1",
                            _f(sd, 2),
                            f"{grav:.1f}",
                            f"{100 - grav - fine:.1f}",
                            f"{fine:.1f}",
                        ]
                    )
            if unit == "CK" and h.kind == "BH":
                z = iv["top"]
                while z < iv["base"] - 0.1:
                    zb = min(z + 1.5, iv["base"])
                    tcr = int(np.clip(rng.normal(85, 10), 40, 100))
                    core.rows.append([h.id, _f(z, 2), _f(zb, 2), str(tcr), str(int(tcr * 0.5)), str(int(tcr * 0.2))])
                    z = zb
        # groundwater
        if h.kind == "BH":
            col = truth.column_at(h.x, h.y)
            rtd = next(((zt, zb) for u, zt, zb in col if u == "RTDU"), None)
            if rtd is not None:
                shallow_head = 0.6 + 0.0015 * (h.y - N0) - 0.001 * (h.x - E0)
                wt_depth = g - shallow_head
                gravel_top = g - rtd[0]
                gravel_base = g - rtd[1]
                if wt_depth >= gravel_base - 0.2:  # water table below the gravel: dry, no strike
                    pass
                elif wt_depth > gravel_top + 0.2:  # unconfined: struck at the water table, no rise
                    strike = round(wt_depth + float(rng.normal(0, 0.1)), 2)
                    wstg.rows.append(
                        [
                            h.id,
                            _f(strike, 2),
                            _f(max(strike - 0.5, 0), 2),
                            "Water strike in terrace gravel, no rise after 20 min",
                        ]
                    )
                    wstd.rows.append([h.id, _f(strike, 2), "20", _f(strike, 2)])
                else:  # confined beneath alluvium: rises
                    strike = round(gravel_top + 0.2, 2)
                    wstg.rows.append(
                        [
                            h.id,
                            _f(strike, 2),
                            _f(max(strike - 0.5, 0), 2),
                            "Water strike at top of terrace gravel, rose",
                        ]
                    )
                    wstd.rows.append([h.id, _f(strike, 2), "20", _f(max(0.3, wt_depth), 2)])
            tab = next(((zt, zb) for u, zt, zb in col if u in ("TAB", "HWH")), None)
            if tab is not None and g - tab[0] < h.depth:
                strike = round(g - tab[0] + 0.3, 2)
                wstg.rows.append([h.id, _f(strike, 2), _f(strike - 1.0, 2), "Deep strike, water rose (confined)"])
                head = -1.5 - 0.002 * (h.y - N0)
                wstd.rows.append([h.id, _f(strike, 2), "20", _f(round(g - head, 2), 2)])
            if h.id in ("BH02", "BH05", "BH09"):  # shallow standpipe in the gravel
                rtd_b = g - rtd[1] if rtd else 5.0
                mong.rows.append([h.id, "SP1", "0.00", "SP", _f(max(1.0, rtd_b - 2.5), 2), _f(rtd_b, 2)])
                for k, dt in enumerate(["2026-04-01T09:00:00", "2026-05-01T09:00:00", "2026-06-01T09:00:00"]):
                    shallow_head = 0.6 + 0.0015 * (h.y - N0) - 0.001 * (h.x - E0) + 0.1 * k
                    mond.rows.append([h.id, "SP1", "0.00", dt, "WDEP", str(k + 1), f"{g - shallow_head:.2f}", "m"])
            if h.id in ("BH01", "BH04", "BH11", "BH13"):  # deep standpipe in Thanet / Chalk
                deep = next(((zt, zb) for u, zt, zb in col if u == "TAB"), None)
                if deep is not None and g - deep[0] + 6 < h.depth:
                    trz, brz = round(g - deep[0] + 2.0, 2), round(min(h.depth - 1.0, g - deep[0] + 6.0), 2)
                    mong.rows.append([h.id, "SP2", "0.00", "SP", _f(trz, 2), _f(brz, 2)])
                    for k, dt in enumerate(["2026-04-01T09:00:00", "2026-05-01T09:00:00", "2026-06-01T09:00:00"]):
                        head = -1.5 - 0.002 * (h.y - N0) + 0.05 * k
                        mond.rows.append([h.id, "SP2", "0.00", dt, "WDEP", str(k + 1), f"{g - head:.2f}", "m"])
    # dictionary support groups
    used_units = sorted({u for t in T.values() for u in t.units if u})
    used_types = sorted({u for t in T.values() for u in t.types if u})
    unit_desc = {
        "m": "metre",
        "mm": "millimetre",
        "MPa": "megapascal",
        "%": "percent",
        "min": "minutes",
        "yyyy-mm-dd": "year month day",
        "yyyy-mm-ddThh:mm:ss": "date time",
        "g/l": "grams per litre",
        "-": "dimensionless",
    }
    unit_t = AGSTable(
        "UNIT",
        ["UNIT_UNIT", "UNIT_DESC"],
        ["", ""],
        ["X", "X"],
        [[u, unit_desc.get(u, u)] for u in sorted(set(used_units) | {"-", "g/l"})],
    )
    type_desc = {
        "ID": "Unique identifier",
        "X": "Text",
        "PA": "Text listed in ABBR group",
        "PU": "Text listed in UNIT group",
        "DT": "Date time",
        "XN": "Text or numeric",
        "0DP": "Value with 0 decimal places",
        "1DP": "Value with 1 decimal place",
        "2DP": "Value with 2 decimal places",
        "3DP": "Value with 3 decimal places",
        "4DP": "Value with 4 decimal places",
    }
    type_t = AGSTable(
        "TYPE", ["TYPE_TYPE", "TYPE_DESC"], ["", ""], ["X", "X"], [[t, type_desc.get(t, t)] for t in used_types]
    )
    abbr_rows = [
        ["LOCA_TYPE", "CP", "Cable percussion"],
        ["LOCA_TYPE", "RC", "Rotary core"],
        ["LOCA_TYPE", "CPT", "Cone penetration test"],
        ["LOCA_TYPE", "TP", "Trial pit"],
        ["LOCA_TYPE", "WS", "Window sample"],
        ["LOCA_STAT", "FINAL", "Final"],
        ["LOCA_GREF", "OSGB", "OS grid"],
        ["HDPH_TYPE", "CP", "Cable percussion"],
        ["HDPH_TYPE", "RC", "Rotary core"],
        ["HDPH_TYPE", "CPT", "Cone penetration test"],
        ["HDPH_TYPE", "TP", "Trial pit"],
        ["HDPH_TYPE", "WS", "Window sample"],
        ["SAMP_TYPE", "U", "Undisturbed"],
        ["SAMP_TYPE", "B", "Bulk"],
        ["ISPT_TYPE", "S", "Split spoon"],
        ["GCHM_CODE", "SO4", "Sulfate"],
        ["GCHM_CODE", "pH", "pH"],
        ["GCHM_TTYP", "WS", "Water soluble"],
        ["MONG_TYPE", "SP", "Standpipe"],
        ["MOND_TYPE", "WDEP", "Depth to water"],
        ["SCPG_TYPE", "CPTU", "Piezocone"],
    ]
    for code in sorted({r[5] for r in geol.rows if r[5]}):
        abbr_rows.append(["GEOL_GEOL", code, f"Project geology code {code}"])
    for code in sorted({r[6] for r in geol.rows if r[6]}):
        abbr_rows.append(["GEOL_BGS", code, f"BGS Lexicon code {code}"])
    abbr_t = AGSTable("ABBR", ["ABBR_HDNG", "ABBR_CODE", "ABBR_DESC"], ["", "", ""], ["X", "X", "X"], abbr_rows)
    order = [
        "PROJ",
        "TRAN",
        "UNIT",
        "TYPE",
        "ABBR",
        "LOCA",
        "HDPH",
        "GEOL",
        "ISPT",
        "SAMP",
        "LLPL",
        "LNMC",
        "GCHM",
        "GRAG",
        "CORE",
        "WSTG",
        "WSTD",
        "MONG",
        "MOND",
        "SCPG",
        "SCPT",
    ]
    all_t = {**T, "UNIT": unit_t, "TYPE": type_t, "ABBR": abbr_t}
    ags_path = out / "synthetic_thames_hdd.ags"
    write_ags4([all_t[k] for k in order if k in all_t and len(all_t[k].rows)], ags_path)
    # ------------------------------------------------------------- companion GIS files (project CRS EPSG:27700)
    crs = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::27700"}}

    def fc(features):
        return {"type": "FeatureCollection", "crs": crs, "features": features}

    (out / "hdd_alignment.geojson").write_text(
        json.dumps(
            fc(
                [
                    {
                        "type": "Feature",
                        "properties": {
                            "name": "HDD-01 cable duct crossing",
                            "product_od_m": 0.25,
                            "entry_angle_deg": 12,
                            "exit_angle_deg": 10,
                            "radius_m": 250,
                            "design_elevation_m": -14.0,
                        },
                        "geometry": {"type": "LineString", "coordinates": [list(HDD_ENTRY), list(HDD_EXIT)]},
                    }
                ]
            )
        )
    )
    (out / "faults.geojson").write_text(
        json.dumps(
            fc(
                [
                    {
                        "type": "Feature",
                        "properties": {
                            "name": "Synthetic fault F1",
                            "throw_m": None,
                            "downthrown_side": "right",
                            "source": "synthetic map",
                        },
                        "geometry": {"type": "LineString", "coordinates": [list(FAULT[0]), list(FAULT[1])]},
                    }
                ]
            )
        )
    )
    (out / "crossings.geojson").write_text(
        json.dumps(
            fc(
                [
                    {
                        "type": "Feature",
                        "properties": {"name": "Railway (synthetic)", "min_clearance_m": 6.0, "kind": "rail"},
                        "geometry": {"type": "LineString", "coordinates": [[E0, N0 + 420], [E0 + 1200, N0 + 440]]},
                    },
                    {
                        "type": "Feature",
                        "properties": {"name": "Creek (synthetic)", "min_clearance_m": 4.0, "kind": "watercourse"},
                        "geometry": {"type": "LineString", "coordinates": [[E0, N0 + 250], [E0 + 1200, N0 + 230]]},
                    },
                ]
            )
        )
    )
    # synthetic "map" polygons (NOT BGS data): alluvium south of ~370 m; terrace gravel north; LC subcrop north
    sup = [
        {
            "type": "Feature",
            "properties": {"code": "ALV", "kind": "superficial", "source": "synthetic map 1:10k"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [E0 - 300, N0 - 300],
                        [E0 + 1500, N0 - 300],
                        [E0 + 1500, N0 + 360],
                        [E0 - 300, N0 + 360],
                        [E0 - 300, N0 - 300],
                    ]
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {"code": "RTDU", "kind": "superficial", "source": "synthetic map 1:10k"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [E0 - 300, N0 + 360],
                        [E0 + 1500, N0 + 360],
                        [E0 + 1500, N0 + 1100],
                        [E0 - 300, N0 + 1100],
                        [E0 - 300, N0 + 360],
                    ]
                ],
            },
        },
    ]
    (out / "superficial_map.geojson").write_text(json.dumps(fc(sup)))
    # DTM (ESRI-style ASCII grid, 5 m cells) from the truth ground surface
    cell = 5.0
    xs = np.arange(E0 - 300 + cell / 2, E0 + 1500, cell)
    ys = np.arange(N0 - 300 + cell / 2, N0 + 1100, cell)
    X, Y = np.meshgrid(xs, ys)
    Z = truth.ground(X, Y)
    lines = [
        f"ncols {len(xs)}",
        f"nrows {len(ys)}",
        f"xllcorner {E0 - 300}",
        f"yllcorner {N0 - 300}",
        f"cellsize {cell}",
        "NODATA_value -9999",
    ]
    lines += [" ".join(f"{v:.2f}" for v in row) for row in Z[::-1]]
    (out / "dtm_5m.asc").write_text("\n".join(lines) + "\n")
    (out / "dtm_5m.prj").write_text(_BNG_WKT)
    manifest = {
        "ags": ags_path.name,
        "crs": "EPSG:27700",
        "vertical_datum": "ODN",
        "holes": len(holes),
        "defects": defects,
        "seed": seed,
        "disclaimer": "SYNTHETIC DATA generated by agsgeomodel.examples.synthetic - not a real site",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


_BNG_WKT = (
    'PROJCS["OSGB 1936 / British National Grid",GEOGCS["OSGB 1936",DATUM["OSGB_1936",SPHEROID["Airy 1830",'
    '6377563.396,299.3249646,AUTHORITY["EPSG","7001"]],AUTHORITY["EPSG","6277"]],PRIMEM["Greenwich",0],'
    'UNIT["degree",0.0174532925199433],AUTHORITY["EPSG","4277"]],PROJECTION["Transverse_Mercator"],'
    'PARAMETER["latitude_of_origin",49],PARAMETER["central_meridian",-2],PARAMETER["scale_factor",0.9996012717],'
    'PARAMETER["false_easting",400000],PARAMETER["false_northing",-100000],UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
    'AUTHORITY["EPSG","27700"]]'
)


if __name__ == "__main__":  # pragma: no cover
    target = sys.argv[1] if len(sys.argv) > 1 else "synthetic_example"
    print(json.dumps(write_example(target), indent=2))
