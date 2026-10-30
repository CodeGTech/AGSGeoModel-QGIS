"""SPT corrections and correlations (BS EN ISO 22476-3 / UK practice).

==================  =========================================================================
Quantity            Correlation
==================  =========================================================================
N60                 N x ER / 60 (ER = hammer energy ratio, ISPT_ERAT)
(N1)60              CN x N60, CN = (pa / s'v0)^0.5 <= 1.7         (Liao & Whitman 1986)
Dr (sands)          Dr = ((N1)60 / 60)^0.5                         (Skempton 1986, NC sands)
phi' (sands)        phi' = (20 (N1)60)^0.5 + 20                    (Hatanaka & Uchida 1996)
cu (clays)          cu = f1 x N, f1 = 4.5 kPa default (Stroud 1974; f1 ~4.2-6 depending on PI)
==================  =========================================================================

Refusals (test drive < 300 mm) are extrapolated linearly and flagged; extrapolated values are
excluded from characteristic values by default because they overstate the soil's N.
"""

from __future__ import annotations

from dataclasses import dataclass

GAMMA_W = 9.81
PA_KPA = 101.325


@dataclass
class SPTSettings:
    default_energy_ratio: float | None = None  # % ; None -> N60 only where ISPT_ERAT is given
    stroud_f1: float = 4.5  # kPa per blow
    gamma_default: float = 19.0  # kN/m3 for effective stress when unknown
    max_cn: float = 1.7


@dataclass
class SPTInterpretation:
    location_id: str
    depth: float
    n: float | None
    n60: float | None
    n1_60: float | None
    refusal: bool
    dr: float | None = None
    phi_deg: float | None = None
    cu_kPa: float | None = None
    unit: str | None = None
    material: str | None = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def interpret_spt(
    records,
    project=None,
    settings: SPTSettings | None = None,
    unit_lookup=None,
    material_lookup=None,
    water_depths: dict[str, float] | None = None,
) -> list[SPTInterpretation]:
    """``unit_lookup(location_id, depth) -> unit``; ``material_lookup(unit) -> 'coarse' | 'fine' | None``."""
    s = settings or SPTSettings()
    out = []
    for r in records:
        if r.n is None:
            continue
        er = r.energy_ratio if r.energy_ratio else s.default_energy_ratio
        n60 = r.n60 if r.n60 is not None else (r.n * er / 60.0 if er else None)
        wd = (water_depths or {}).get(r.location_id, r.water_depth if r.water_depth is not None else 0.0)
        sv = s.gamma_default * r.depth
        u = GAMMA_W * max(r.depth - (wd or 0.0), 0.0)
        s_eff = max(sv - u, 5.0)
        n1_60 = min((PA_KPA / s_eff) ** 0.5, s.max_cn) * n60 if n60 is not None else None
        unit = unit_lookup(r.location_id, r.depth) if unit_lookup else None
        mat = material_lookup(unit) if (material_lookup and unit) else None
        it = SPTInterpretation(r.location_id, r.depth, r.n, n60, n1_60, r.refusal, unit=unit, material=mat)
        if mat == "coarse" and n1_60 is not None:
            it.dr = float(min((n1_60 / 60.0) ** 0.5, 1.0))
            it.phi_deg = float((20.0 * n1_60) ** 0.5 + 20.0)
        if mat == "fine":
            it.cu_kPa = float(s.stroud_f1 * r.n)
        out.append(it)
    return out


COARSE = {"sand", "fine_sand", "sand_gravel", "gravel"}
FINE = {"clay", "silt", "clay_silt_organic", "peat", "diamicton"}


def material_class(kb, unit: str | None) -> str | None:
    if not unit or kb is None or unit not in kb:
        return None
    m = kb[unit].material
    if m in COARSE:
        return "coarse"
    if m in FINE:
        return "fine"
    return None
