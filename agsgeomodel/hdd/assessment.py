"""HDD and linear infrastructure ground-risk assessment.

For every station along the bore path the model is interrogated (fault-aware) for the unit at
the bore, the distance to the contacts above and below, rockhead, cover, groundwater pressure
and — where Monte Carlo realisations exist — the probability of the unit. Hazards are rated
Green / Amber / Red with the reason recorded:

====================  ==========================================================================
Hazard                Rule (thresholds in :class:`HDDRiskRules`)
====================  ==========================================================================
running_sand          granular unit below its groundwater level; KB running-sand score and head
groundwater           pressure head in a permeable unit; artesian where head > ground
boulders              KB boulders/cobbles score of the unit at the bore
rock                  KB hard-bands score (e.g. flint bands in Chalk, Lambeth Group hard bands)
mixed_face            contact within the reamed-hole envelope (+ contact uncertainty), with the
                      material contrast across it
loss_of_circulation   KB loss-of-circulation score; low cover / reamed diameter; soft cover;
                      Delft cavity-expansion screening of the maximum allowable mud pressure
====================  ==========================================================================

The geological confidence of each station is reported *separately* (P(unit) from Monte Carlo
realisations, else the interpretation class of the nearest contact) — it is not mixed into the
hazard RAG, so a "green but speculative" station remains visible as such.

The Delft method (Luger & Hergarden 1988, as presented by Staheli et al. 1998):

    P_max = u + (P'_f + c' cot phi') [ (R_o / R_p,max)^2 + Q ]^(-sin phi' / (1 + sin phi')) - c' cot phi'
    P'_f  = sigma'_0 (1 + sin phi') + c' cos phi',   Q = (sigma'_0 sin phi' + c' cos phi') / G

with the undrained limit (phi = 0): P_max = sigma_0 + c_u [1 - ln((R_o/R_p,max)^2 + c_u/G)].
R_p,max = 2/3 of the cover in clays, 1/2 of the cover in sands. It is a screening check: annular
friction losses are only included when supplied, and final design needs a specialist assessment.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from ..core.config import HDDConfig
from ..core.geometry import polyline_intersections
from ..models.enums import HSU, RAG
from .profile import Alignment

HAZARDS = ("running_sand", "groundwater", "boulders", "rock", "mixed_face", "loss_of_circulation")
GROUPS = {
    "made_ground": "made",
    "topsoil": "made",
    "clay_silt_organic": "soft_fine",
    "peat": "organic",
    "clay": "fine",
    "silt": "fine",
    "mixed": "mixed",
    "diamicton": "mixed",
    "sand": "coarse",
    "fine_sand": "coarse",
    "sand_gravel": "gravel",
    "gravel": "gravel",
    "chalk": "rock",
}
GAMMA_W = 9.81


@dataclass
class HDDRiskRules:
    groundwater_amber_head_m: float = 0.0
    groundwater_red_head_m: float = 10.0
    running_sand_red_head_m: float = 3.0
    mixed_face_tolerance_m: float = 1.0
    low_cover_ratio: float = 10.0
    soft_cover_depth_m: float = 10.0
    frac_fos_red: float = 1.0
    frac_fos_amber: float = 2.0
    mud_density_mgm3: float = 1.15
    annular_loss_kpa_per_m: float | None = None  # None -> hydrostatic only (stated in the output)
    returns_at: str = "entry"  # entry | exit
    rigidity_index_clay: float = 100.0  # G / cu when no stiffness data
    poisson: float = 0.3
    confidence_green: float = 0.8
    confidence_amber: float = 0.5


@dataclass
class Station:
    chainage: float
    x: float
    y: float
    z: float
    ground: float
    cover: float
    unit: str
    unit_name: str
    p_unit: float | None
    d_top: float
    d_base: float
    unit_above: str | None
    unit_below: str | None
    contact_sigma: float
    rockhead: float
    rockhead_sigma: float
    heads: dict[str, float]
    pressure_head: float | None
    hazards: dict[str, int] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    frac_pmax_kpa: float | None = None
    frac_pmin_kpa: float | None = None
    frac_fos: float | None = None
    confidence: int = 0

    @property
    def overall(self) -> int:
        return max(self.hazards.values()) if self.hazards else 0

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("hazards", "reasons", "heads")}
        for h in HAZARDS:
            d[f"rag_{h}"] = RAG_NAMES[self.hazards.get(h, 0)]
            d[f"why_{h}"] = self.reasons.get(h, "")
        d["rag_overall"] = RAG_NAMES[self.overall]
        d["confidence_rag"] = RAG_NAMES[self.confidence]
        for k, v in self.heads.items():
            d[f"head_{k}"] = v
        return d


RAG_NAMES = {0: RAG.GREEN.value, 1: RAG.AMBER.value, 2: RAG.RED.value}
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass
class HDDAssessment:
    alignment: Alignment
    stations: list[Station]
    formation_changes: list[dict]
    risk_zones: list[dict]
    constraints: list[dict]
    summary: dict
    notes: list[str] = field(default_factory=list)

    def table(self) -> list[dict]:
        return [s.to_dict() for s in self.stations]

    def rockhead_profile(self) -> list[dict]:
        return [
            {
                "chainage": s.chainage,
                "rockhead": s.rockhead,
                "rockhead_sigma": s.rockhead_sigma,
                "bore_elevation": s.z,
                "depth_below_rockhead": s.rockhead - s.z,
            }
            for s in self.stations
        ]

    def groundwater_profile(self) -> list[dict]:
        return [
            {
                "chainage": s.chainage,
                "bore_elevation": s.z,
                **{f"head_{k}": v for k, v in s.heads.items()},
                "pressure_head_at_bore": s.pressure_head,
            }
            for s in self.stations
        ]


# ============================================================================ helpers
def _phi_rad(phi_deg):
    return math.radians(phi_deg)


def delft_pmax(sigma_v0_eff: float, u: float, c: float, phi_deg: float, G: float, Ro: float, Rp_max: float) -> float:
    """Maximum allowable mud pressure (kPa) by the Delft cavity-expansion method."""
    ratio2 = (Ro / max(Rp_max, Ro * 1.0001)) ** 2
    if phi_deg < 1.0:  # undrained
        return u + sigma_v0_eff + c * (1.0 - math.log(ratio2 + c / max(G, 1e-6)))
    phi = _phi_rad(phi_deg)
    s, co = math.sin(phi), math.cos(phi)
    cot = co / s
    pf = sigma_v0_eff * (1 + s) + c * co
    q = (sigma_v0_eff * s + c * co) / max(G, 1e-6)
    return u + (pf + c * cot) * (ratio2 + q) ** (-s / (1 + s)) - c * cot


class UnitParameters:
    """Resolve strength / stiffness / weight per unit from an engineering ground model, with flags."""

    def __init__(self, kb, ground_model=None, rules: HDDRiskRules | None = None):
        self.kb = kb
        self.gm = ground_model
        self.r = rules or HDDRiskRules()

    def get(self, unit: str) -> dict:
        u = self.kb[unit]
        g = self.gm
        out: dict = {"assumed": []}
        coarse = GROUPS.get(u.material) in ("coarse", "gravel")

        def val(*keys):
            for k in keys:
                if g is not None:
                    v = g.value(unit, k, "cautious_mean_low")
                    if v is not None and np.isfinite(v):
                        return v, k
            return None, None

        gamma, _ = val("CPT_gamma")
        if gamma is None and g is not None and g.value(unit, "LDEN_BDEN") is not None:
            gamma = 9.81 * g.value(unit, "LDEN_BDEN", "mean")
        if gamma is None:
            gamma = {"peat": 11.0, "clay_silt_organic": 16.0, "made_ground": 18.0}.get(u.material, 19.0)
            out["assumed"].append(f"gamma={gamma}")
        out["gamma"] = gamma
        if coarse:
            phi, _ = val("CPT_phi", "SPT_phi")
            if phi is None:
                phi = 32.0
                out["assumed"].append("phi'=32")
            out["phi"], out["c"] = min(phi, 40.0), 0.0
            E, _ = val("CPT_E")
            if E is None:
                E = 30.0
                out["assumed"].append("E'=30 MPa")
            out["G"] = E * 1000.0 / (2 * (1 + self.r.poisson))
            out["kind"] = "drained"
        else:
            cu, _ = val("CPT_su", "TRIT_CU", "SPT_cu")
            if cu is None:
                return {"kind": "unknown", "assumed": out["assumed"] + ["no strength data"], "gamma": gamma}
            out["phi"], out["c"] = 0.0, cu
            out["G"] = self.r.rigidity_index_clay * cu
            out["assumed"].append(f"G={self.r.rigidity_index_clay:g} cu")
            out["kind"] = "undrained"
        return out


# ============================================================================ main
def assess_alignment(
    alignment: Alignment,
    model,
    hydro=None,
    ground_model=None,
    config: HDDConfig | None = None,
    rules: HDDRiskRules | None = None,
    crossings=None,
) -> HDDAssessment:
    cfg = config or HDDConfig()
    r = rules or HDDRiskRules(mixed_face_tolerance_m=cfg.mixed_face_tolerance_m, low_cover_ratio=cfg.low_cover_ratio)
    kb = model.column.kb
    ch, xy, zb = alignment.stations(cfg.station_spacing_m)
    D = alignment.design.reamed_diameter
    env = D / 2.0
    ground = model.sample_grid(model.surfaces["ground"].values, xy)
    units = model.unit_at(xy, zb)
    tops = {u: model.sample_grid(model.tops[u], xy) for u in model.units}
    bases = {u: model.sample_grid(model.bases[u], xy) for u in model.units}
    rock = model.sample_grid(model.surfaces["rockhead"].values, xy)
    rock_sd = model.sample_grid(model.surfaces["rockhead"].std, xy)
    p_unit = None
    notes: list[str] = []
    if model._realisations is not None:
        ulist, P = model.probability(xy, zb)
        p_unit = np.array([P[k, ulist.index(u)] if u in ulist else np.nan for k, u in enumerate(units)])
    else:
        notes.append("no Monte Carlo realisations: geological confidence taken from contact uncertainty")
    heads = {}
    if hydro is not None:
        for s in hydro.surfaces:
            h, _ = hydro.head_at(s, xy)
            heads[s] = h
    params = UnitParameters(kb, ground_model, r)
    pcache: dict[str, dict] = {}
    stations: list[Station] = []
    returns_ch = 0.0 if r.returns_at == "entry" else float(ch[-1])
    returns_z = float(ground[0] if r.returns_at == "entry" else ground[-1])
    for k in range(len(ch)):
        u = units[k] or ""
        if not u:
            continue
        t, b = tops[u][k], bases[u][k]
        d_top, d_base = float(t - zb[k]), float(zb[k] - b)
        idx = model.units.index(u)
        above = next(
            (
                model.units[j]
                for j in range(idx - 1, -1, -1)
                if tops[model.units[j]][k] - bases[model.units[j]][k] > 0.01
            ),
            None,
        )
        below = next(
            (
                model.units[j]
                for j in range(idx + 1, len(model.units))
                if tops[model.units[j]][k] - bases[model.units[j]][k] > 0.01
            ),
            None,
        )
        sd_top = model.unit_top_std(u, xy[k : k + 1])[0]
        sd_base = model.unit_top_std(below, xy[k : k + 1])[0] if below else np.nan
        csig = (
            float(np.nanmin([sd_top if d_top <= d_base else sd_base, 5.0]))
            if np.isfinite(sd_top if d_top <= d_base else sd_base)
            else 5.0
        )
        sys_of = hydro_system(hydro, u)
        hk = {s: float(v[k]) for s, v in heads.items()}
        ph = hk.get(sys_of) - zb[k] if (sys_of in hk and np.isfinite(hk.get(sys_of, np.nan))) else None
        st = Station(
            float(ch[k]),
            float(xy[k, 0]),
            float(xy[k, 1]),
            float(zb[k]),
            float(ground[k]),
            float(ground[k] - zb[k]),
            u,
            kb[u].name,
            None if p_unit is None else float(p_unit[k]),
            d_top,
            d_base,
            above,
            below,
            csig,
            float(rock[k]),
            float(rock_sd[k]),
            hk,
            None if ph is None else float(ph),
        )
        unit = kb[u]
        grp = GROUPS.get(unit.material, "mixed")
        # ---------------- running sand
        if grp in ("coarse", "gravel") and ph is not None and ph > 0:
            score = unit.hazard("running_sand")
            if score >= 3 or (score >= 2 and ph > r.running_sand_red_head_m):
                st.hazards["running_sand"], st.reasons["running_sand"] = (
                    2,
                    f"{unit.name} below groundwater (head {ph:.1f} m)",
                )
            elif score >= 1:
                st.hazards["running_sand"], st.reasons["running_sand"] = 1, f"granular {unit.name} below groundwater"
            else:
                st.hazards["running_sand"] = 0
        else:
            st.hazards["running_sand"] = 0
        # ---------------- groundwater
        if ph is not None and ph > r.groundwater_amber_head_m:
            artesian = hk.get(sys_of, -np.inf) > ground[k]
            if unit.hsu == HSU.AQUIFER and (ph > r.groundwater_red_head_m or artesian):
                st.hazards["groundwater"] = 2
                st.reasons["groundwater"] = (
                    f"{'artesian ' if artesian else ''}pressure head {ph:.1f} m in aquifer {unit.name}"
                )
            elif unit.hsu in (HSU.AQUIFER, HSU.VARIABLE):
                st.hazards["groundwater"], st.reasons["groundwater"] = 1, f"pressure head {ph:.1f} m in {unit.name}"
            else:
                st.hazards["groundwater"] = 0
        else:
            st.hazards["groundwater"] = 0
        # ---------------- boulders / rock
        sb = unit.hazard("boulders_cobbles")
        st.hazards["boulders"] = 2 if sb >= 3 else 1 if sb >= 2 else 0
        if sb >= 2:
            st.reasons["boulders"] = f"cobbles/boulders possible in {unit.name}"
        sr = unit.hazard("hard_bands")
        st.hazards["rock"] = 2 if sr >= 3 else 1 if sr >= 2 else 0
        if sr >= 2:
            st.reasons["rock"] = f"hard bands / flints possible in {unit.name}"
        # ---------------- mixed face
        near_d, other = (d_top, above) if d_top <= d_base else (d_base, below)
        tol = env + max(r.mixed_face_tolerance_m, csig if np.isfinite(csig) else 0.0)
        if other is not None and near_d <= tol:
            g2 = GROUPS.get(kb[other].material, "mixed")
            contrast = g2 != grp
            st.hazards["mixed_face"] = 2 if contrast else 1
            st.reasons["mixed_face"] = (
                f"{'contrasting ' if contrast else ''}contact {unit.name}/{kb[other].name} "
                f"within {near_d:.1f} m (envelope {tol:.1f} m)"
            )
        elif other is not None and near_d <= 2 * tol:
            st.hazards["mixed_face"], st.reasons["mixed_face"] = (
                1,
                f"contact with {kb[other].name} within {near_d:.1f} m",
            )
        else:
            st.hazards["mixed_face"] = 0
        # ---------------- loss of circulation / frac-out
        lc = unit.hazard("loss_of_circulation")
        level, why = (
            (2, f"loss of circulation likely in {unit.name}")
            if lc >= 3
            else (1, f"loss of circulation possible in {unit.name}")
            if lc >= 2
            else (0, "")
        )
        cover_ratio = st.cover / max(D, 1e-6)
        if cover_ratio < r.low_cover_ratio:
            if level < 1:
                level, why = 1, f"low cover ({st.cover:.1f} m = {cover_ratio:.0f} x reamed diameter)"
        soft_above = [m for m in model.units[:idx] if kb[m].hazard("frac_out") >= 3 and tops[m][k] - bases[m][k] > 0.3]
        if soft_above and st.cover < r.soft_cover_depth_m and level < 1:
            level, why = 1, "soft deposits (" + ", ".join(soft_above) + f") within {st.cover:.1f} m cover"
        prm = pcache.setdefault(u, params.get(u))
        if prm.get("kind") in ("drained", "undrained") and st.cover > 0.5:
            sv = 0.0
            for m in model.units:
                tt = min(tops[m][k], ground[k])
                bb = max(bases[m][k], zb[k])
                if tt > bb:
                    sv += pcache.setdefault(m, params.get(m)).get("gamma", 19.0) * (tt - bb)
            sys_head = hk.get(sys_of) if sys_of in hk else None
            u_pore = GAMMA_W * max(sys_head - zb[k], 0.0) if (sys_head is not None and np.isfinite(sys_head)) else 0.0
            s_eff = max(sv - u_pore, 1.0)
            rp = (2.0 / 3.0 if prm["kind"] == "undrained" else 0.5) * st.cover
            pmax = delft_pmax(s_eff, u_pore, prm["c"], prm["phi"], prm["G"], D / 2.0, rp)
            pmin = 9.81 * r.mud_density_mgm3 * max(returns_z - zb[k], 0.0)
            if r.annular_loss_kpa_per_m:
                pmin += r.annular_loss_kpa_per_m * abs(ch[k] - returns_ch)
            fos = pmax / pmin if pmin > 0 else math.inf
            st.frac_pmax_kpa, st.frac_pmin_kpa, st.frac_fos = float(pmax), float(pmin), float(fos)
            if fos < r.frac_fos_red:
                level, why = 2, f"frac-out: P_max {pmax:.0f} kPa < mud pressure {pmin:.0f} kPa (FoS {fos:.2f})"
            elif fos < r.frac_fos_amber and level < 2:
                level, why = max(level, 1), f"frac-out margin low (FoS {fos:.2f})"
        st.hazards["loss_of_circulation"] = level
        if why:
            st.reasons["loss_of_circulation"] = why
        # ---------------- geological confidence (separate)
        if st.p_unit is not None and np.isfinite(st.p_unit):
            pu = st.p_unit
        else:
            pu = float(math.erf(max(near_d, 0.0) / (math.sqrt(2) * max(csig, 1e-3)))) if np.isfinite(csig) else 0.5
        st.confidence = 0 if pu >= r.confidence_green else 1 if pu >= r.confidence_amber else 2
        stations.append(st)
    # ---------------------------------------------------------------- formation changes
    changes = []
    for a, b in zip(stations[:-1], stations[1:]):
        if a.unit != b.unit:
            c = 0.5 * (a.chainage + b.chainage)
            dz_contact = b.z - a.z
            slope_bore = (b.z - a.z) / max(b.chainage - a.chainage, 1e-9)
            # chainage uncertainty of the crossing ~ sigma_z / |slope(contact) - slope(bore)|
            sig = a.contact_sigma if np.isfinite(a.contact_sigma) else 2.0
            rel = abs(slope_bore) if abs(slope_bore) > 0.02 else 0.02
            changes.append(
                {
                    "chainage": c,
                    "from_unit": a.unit,
                    "to_unit": b.unit,
                    "contact_sigma_m": sig,
                    "chainage_uncertainty_m": float(min(sig / rel, 500.0)),
                    "note": "bore passes downwards"
                    if slope_bore < 0
                    else "bore passes upwards"
                    if dz_contact > 0
                    else "horizontal section",
                }
            )
    # ---------------------------------------------------------------- risk zones
    zones = []
    for key in ("overall",) + HAZARDS:
        cur = None
        for s in stations:
            lvl = s.overall if key == "overall" else s.hazards.get(key, 0)
            hz = (
                [h for h in HAZARDS if s.hazards.get(h, 0) == lvl and h in s.reasons]
                if key == "overall"
                else ([key] if key in s.reasons else [])
            )
            if cur is None or cur["level"] != lvl:
                if cur is not None and cur["level"] > 0:
                    zones.append(cur)
                cur = {
                    "hazard": key,
                    "level": lvl,
                    "rag": RAG_NAMES[lvl],
                    "from": s.chainage,
                    "to": s.chainage,
                    "_fam": {},
                }
            cur["to"] = s.chainage
            for h in hz:
                fam = (h, _NUM.sub("#", s.reasons[h]))
                ent = cur["_fam"].setdefault(fam, [s.reasons[h], 0])
                ent[1] += 1
        if cur is not None and cur["level"] > 0:
            zones.append(cur)
    for z in zones:
        fams = sorted(z.pop("_fam").items(), key=lambda kv: -kv[1][1])
        z["reasons"] = [f"{h}: {txt}" + (f" ({n} stations)" if n > 1 else "") for (h, _), (txt, n) in fams[:4]]
    # ---------------------------------------------------------------- engineering constraints
    cons = []
    for name, line, clearance in crossings or []:
        line = np.asarray(line, float)
        for c, _, _ in polyline_intersections(alignment.line, np.stack([line[:-1], line[1:]], axis=1)):
            s = min(stations, key=lambda q: abs(q.chainage - c))
            ok = clearance is None or s.cover - env >= clearance
            cons.append(
                {
                    "type": "crossing clearance",
                    "name": name,
                    "chainage": float(c),
                    "cover_m": s.cover,
                    "required_m": clearance,
                    "pass": bool(ok),
                    "message": f"{name}: cover {s.cover:.1f} m (crown {s.cover - env:.1f} m) vs required "
                    f"{clearance if clearance is not None else 'n/a'} m",
                }
            )
    if not alignment.profile.feasible:
        for n in alignment.profile.notes:
            cons.append({"type": "geometry", "name": "profile", "pass": False, "message": n})
    for end, s in (("entry", stations[0] if stations else None), ("exit", stations[-1] if stations else None)):
        if s is None:
            continue
        col = model.column_at(s.x, s.y)
        top3 = [u for u, t, b in col if (s.ground - t) < 3.0]
        shallow = (
            hydro.head_at("shallow", np.array([[s.x, s.y]]))[0][0]
            if (hydro and "shallow" in hydro.surfaces)
            else np.nan
        )
        cons.append(
            {
                "type": "pit",
                "name": f"{end} pit",
                "pass": True,
                "message": f"{end}: units within 3 m: {', '.join(top3) or 'n/a'}; shallow groundwater "
                f"{'%.1f m below ground' % (s.ground - shallow) if np.isfinite(shallow) else 'unknown'}",
            }
        )
    if r.annular_loss_kpa_per_m is None:
        notes.append(
            "frac-out screening uses hydrostatic mud pressure only (annular friction losses not supplied): "
            "the factor of safety is optimistic"
        )
    worst = max((s.overall for s in stations), default=0)
    segs = alignment.profile.segments
    c_lo = next((sg["to"] for sg in segs if sg["type"] == "entry tangent"), 0.1 * alignment.plan_length)
    c_hi = next((sg["from"] for sg in segs if sg["type"] == "exit tangent"), 0.9 * alignment.plan_length)
    mid = [s for s in stations if c_lo <= s.chainage <= c_hi] or stations
    summary = {
        "name": alignment.name,
        "plan_length_m": alignment.plan_length,
        "bore_length_m": alignment.profile.bore_length,
        "reamed_diameter_m": D,
        "min_cover_m": float(min((s.cover for s in mid), default=np.nan)),
        "min_cover_basis": "between end of entry tangent and start of exit tangent",
        "max_depth_m": float(max((s.cover for s in stations), default=np.nan)),
        "units_crossed": list(dict.fromkeys(s.unit for s in stations)),
        "overall_rag": RAG_NAMES[worst],
        "red_length_m": float(
            sum(
                z["to"] - z["from"] + cfg.station_spacing_m
                for z in zones
                if z["hazard"] == "overall" and z["level"] == 2
            )
        ),
        "amber_length_m": float(
            sum(
                z["to"] - z["from"] + cfg.station_spacing_m
                for z in zones
                if z["hazard"] == "overall" and z["level"] == 1
            )
        ),
        "low_confidence_length_m": float(sum(cfg.station_spacing_m for s in stations if s.confidence == 2)),
        "min_frac_fos": float(min((s.frac_fos for s in stations if s.frac_fos is not None), default=np.nan)),
        "constraints_failed": sum(1 for c in cons if not c["pass"]),
    }
    return HDDAssessment(alignment, stations, changes, zones, cons, summary, notes)


def hydro_system(hydro, unit: str) -> str | None:
    """Aquifer system that a unit belongs to (from the hydrogeology result)."""
    if hydro is None:
        return None
    return hydro.system_of.get(unit)
