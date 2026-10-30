"""HydroGeo engine: groundwater surfaces per aquifer system, perched water, confinement, flow and
seepage paths, hydrostratigraphic units.

Principles

* Heads are only combined within one **aquifer system** (defined per province, e.g. the shallow
  terrace-gravel water and the lower Lambeth/Thanet/Chalk aquifer separated by the London Clay).
  Mixing them into a single "groundwater level" is a common and serious error.
* Monitoring readings (equilibrated) carry more weight than water strikes; a strike with a
  recorded rise uses the rest level; strikes without a rise are weak evidence (large sigma).
* Perched water is identified explicitly and excluded from the regional surface.
* Every output carries the same Observed / Interpolated / Inferred / Speculative classes as the
  geological model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..core.config import HydroConfig, InterpolationConfig, UncertaintyConfig
from ..core.grid import Grid2D
from ..modelling.interpolators import make_interpolator
from ..modelling.uncertainty import classify, confidence_from_std
from ..models.enums import HSU

DEPTH_TYPES = ("WDEP", "DEP", "DTW", "GWD", "WD", "DEPTH")
LEVEL_TYPES = ("WLEV", "LEV", "ELEV", "GWL", "WL", "OD", "HEAD")


@dataclass
class HeadObservation:
    location_id: str
    x: float
    y: float
    head: float
    sigma: float
    system: str | None
    unit: str | None
    kind: str  # monitoring | strike_rest | strike
    depth: float
    n_readings: int = 1
    head_sd: float = 0.0
    perched: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class HeadSurface:
    system: str
    grid: Grid2D
    values: np.ndarray
    std: np.ndarray
    classes: np.ndarray
    confidence: np.ndarray
    n_obs: int
    method: str
    notes: list[str] = field(default_factory=list)


@dataclass
class HydroResult:
    observations: list[HeadObservation]
    surfaces: dict[str, HeadSurface]
    confinement: dict[str, np.ndarray] = field(default_factory=dict)  # 0 unconfined, 1 confined, 2 artesian
    flow_vectors: dict[str, list[dict]] = field(default_factory=dict)
    seepage_paths: dict[str, list[np.ndarray]] = field(default_factory=dict)
    hsu: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    system_of: dict[str, str] = field(default_factory=dict)

    def head_at(self, system: str, xy) -> tuple[np.ndarray, np.ndarray]:
        s = self.surfaces.get(system)
        if s is None:
            n = len(np.asarray(xy).reshape(-1, 2))
            return np.full(n, np.nan), np.full(n, np.nan)
        return s.grid.sample(s.values, xy), s.grid.sample(s.std, xy)


class HydroGeoEngine:
    def __init__(
        self,
        column,
        config: HydroConfig | None = None,
        interpolation: InterpolationConfig | None = None,
        uncertainty: UncertaintyConfig | None = None,
    ):
        self.column = column
        self.cfg = config or HydroConfig()
        self.interp = interpolation or InterpolationConfig()
        self.unc = uncertainty or UncertaintyConfig(
            observed_radius_m=2.0, interpolated_max_sigma_m=0.5, inferred_max_sigma_m=1.5, confidence_scale_m=1.5
        )
        self.systems = column.aquifer_systems or [
            {"id": "groundwater", "units": list(column.units), "aquifer_units": list(column.units), "confined_by": []}
        ]
        self._sys_of = {u: s["id"] for s in self.systems for u in s["units"]}

    def system_of(self, unit: str | None) -> str | None:
        return self._sys_of.get(unit) if unit else None

    # ------------------------------------------------------------------ observations
    def observations(self, project, interpretation) -> list[HeadObservation]:
        out: list[HeadObservation] = []
        for mp in project.monitoring:
            loc = project.locations.get(mp.location_id)
            if loc is None or loc.ground_level is None or not mp.readings:
                continue
            heads = []
            for r in mp.readings:
                t = (r.type or "").upper()
                if any(k in t for k in LEVEL_TYPES) and not any(k == t for k in DEPTH_TYPES):
                    heads.append(r.value)
                else:
                    heads.append(loc.ground_level - r.value)
            heads = np.array(heads, float)
            if mp.response_top is not None and mp.response_base is not None:
                d = 0.5 * (mp.response_top + mp.response_base)
            else:
                d = loc.final_depth or 0.0
            li = interpretation.locations.get(mp.location_id) if interpretation else None
            unit = li.unit_at(d) if li else None
            sd = float(heads.std(ddof=1)) if len(heads) > 1 else 0.0
            out.append(
                HeadObservation(
                    mp.location_id,
                    loc.x,
                    loc.y,
                    float(heads.mean()),
                    float(math.hypot(0.05, sd)),
                    self.system_of(unit),
                    unit,
                    "monitoring",
                    d,
                    len(heads),
                    sd,
                    note=f"standpipe {mp.mon_id}, {len(heads)} reading(s)",
                )
            )
        for ws in project.water_strikes:
            loc = project.locations.get(ws.location_id)
            if loc is None or loc.ground_level is None:
                continue
            li = interpretation.locations.get(ws.location_id) if interpretation else None
            unit = li.unit_at(ws.depth + 0.01) if li else None
            if ws.readings:
                out.append(
                    HeadObservation(
                        ws.location_id,
                        loc.x,
                        loc.y,
                        loc.ground_level - ws.rest_depth,
                        1.0,
                        self.system_of(unit),
                        unit,
                        "strike_rest",
                        ws.depth,
                        note=f"strike at {ws.depth:.2f} m rose to {ws.rest_depth:.2f} m",
                    )
                )
            else:
                out.append(
                    HeadObservation(
                        ws.location_id,
                        loc.x,
                        loc.y,
                        loc.ground_level - ws.depth,
                        2.0,
                        self.system_of(unit),
                        unit,
                        "strike",
                        ws.depth,
                        note="strike without recorded rise (not equilibrated)",
                    )
                )
        return out

    # ------------------------------------------------------------------ perched water
    def flag_perched(self, obs: list[HeadObservation], project, interpretation) -> None:
        kb = self.column.kb
        for o in obs:
            li = interpretation.locations.get(o.location_id) if interpretation else None
            if li is None or o.unit is None:
                continue
            sysdef = next((s for s in self.systems if s["id"] == o.system), None)
            if sysdef is None or o.unit in sysdef.get("aquifer_units", []):
                continue
            below = [r for r in li.runs if r.top >= o.depth - 1e-6]
            seal = next(
                (r for r in below if kb[r.unit].hsu in (HSU.AQUITARD, HSU.AQUICLUDE) and r.thickness >= 0.3), None
            )
            if seal is None:
                continue
            main = [p for p in obs if p.system == o.system and p is not o and p.unit in sysdef.get("aquifer_units", [])]
            if main:
                xy = np.array([[p.x, p.y] for p in main])
                d = np.hypot(xy[:, 0] - o.x, xy[:, 1] - o.y)
                w = 1.0 / np.maximum(d, 1.0) ** 2
                ref = float(np.sum(w * np.array([p.head for p in main])) / w.sum())
                if o.head - ref > self.cfg.perched_tolerance_m:
                    o.perched = True
                    o.note += f"; perched on {seal.unit} (head {o.head - ref:+.2f} m above {o.system} aquifer)"
            else:
                loc = project.locations[o.location_id]
                if o.head > loc.ground_level - seal.top - self.cfg.perched_tolerance_m:
                    o.perched = True
                    o.note += f"; water held above {seal.unit} (no aquifer observations for comparison)"

    # ------------------------------------------------------------------ surfaces
    def head_surface(self, system: str, grid: Grid2D, obs: list[HeadObservation]) -> HeadSurface | None:
        use = [o for o in obs if o.system == system and not o.perched]
        if not use:
            return None
        xy = np.array([[o.x, o.y] for o in use])
        h = np.array([o.head for o in use])
        sg = np.array([o.sigma for o in use])
        w_sig = np.where(
            [o.kind == "monitoring" for o in use],
            sg / max(self.cfg.monitoring_weight, 1e-3),
            sg / max(self.cfg.strike_weight, 1e-3),
        )
        notes = []
        nodes = grid.nodes()
        uniq = np.unique(np.round(xy, 1), axis=0)
        if len(uniq) >= 3:
            model = make_interpolator(self.interp, None, "kriging", var_floor=0.25)
            model.fit(xy, h, w_sig)
            val, std = model.predict(nodes, return_std=True)
            method = "kriging"
        else:
            val = np.full(len(nodes), float(np.average(h, weights=1 / w_sig**2)))
            std = np.full(len(nodes), max(2.0, float(np.ptp(h))))
            method = "mean of observations"
            notes.append(f"{system}: fewer than three observation locations - constant head assumed (speculative)")
        val = val.reshape(grid.shape)
        std = np.asarray(std).reshape(grid.shape)
        prim = np.array([[o.x, o.y] for o in use if o.kind == "monitoring"]).reshape(-1, 2)
        sec = np.array([[o.x, o.y] for o in use if o.kind != "monitoring"]).reshape(-1, 2)
        classes = classify(grid, std, prim, sec, self.unc)
        return HeadSurface(system, grid, val, std, classes, confidence_from_std(std, self.unc), len(use), method, notes)

    # ------------------------------------------------------------------ derived products
    CONFINEMENT_CLASSES = {
        -1: "system absent",
        0: "unconfined (head below top of system)",
        1: "confined (head above top of system, confining layer present)",
        2: "artesian (head above ground level)",
        3: "head above top of system but no confining layer: possible leakage / continuity with overlying deposits",
    }

    def confinement(self, system: str, surf: HeadSurface, model, min_confining_m: float = 0.5) -> np.ndarray | None:
        """Confinement class per node (see ``CONFINEMENT_CLASSES``)."""
        sysdef = next((s for s in self.systems if s["id"] == system), None)
        if model is None or sysdef is None:
            return None
        units = [u for u in (sysdef.get("aquifer_units") or sysdef["units"]) if u in model.tops]
        if not units:
            return None
        g = surf.grid
        nodes = g.nodes()
        top = np.full(g.shape, -np.inf)
        for u in units:
            t = model.sample_grid(model.tops[u], nodes).reshape(g.shape)
            b = model.sample_grid(model.bases[u], nodes).reshape(g.shape)
            top = np.where((t - b) > 0.01, np.maximum(top, t), top)
        conf_thk = np.zeros(g.shape)
        for u in sysdef.get("confined_by", []):
            if u in model.tops:
                t = model.sample_grid(model.tops[u], nodes).reshape(g.shape)
                b = model.sample_grid(model.bases[u], nodes).reshape(g.shape)
                conf_thk += np.nan_to_num(np.maximum(t - b, 0.0))
        ground = model.sample_grid(model.surfaces["ground"].values, nodes).reshape(g.shape)
        above = surf.values > top + 0.1
        out = np.zeros(g.shape, dtype=int)
        if sysdef.get("confined_by"):
            out = np.where(above & (conf_thk >= min_confining_m), 1, out)
            out = np.where(above & (conf_thk < min_confining_m), 3, out)
        else:
            out = np.where(above, 1, out)
        out = np.where(surf.values > ground, 2, out)
        out = np.where(np.isfinite(top), out, -1)
        return out

    @staticmethod
    def flow_vectors(surf: HeadSurface, spacing_cells: int = 6) -> list[dict]:
        g = surf.grid
        gx, gy = g.gradient(surf.values)
        out = []
        for j in range(spacing_cells // 2, g.ny, spacing_cells):
            for i in range(spacing_cells // 2, g.nx, spacing_cells):
                dx, dy = -gx[j, i], -gy[j, i]
                mag = math.hypot(dx, dy)
                if not np.isfinite(mag) or mag < 1e-6:
                    continue
                az = (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0
                out.append(
                    {
                        "x": float(g.x0 + i * g.dx),
                        "y": float(g.y0 + j * g.dy),
                        "azimuth_deg": az,
                        "gradient": mag,
                        "head": float(surf.values[j, i]),
                        "class": int(surf.classes[j, i]),
                    }
                )
        return out

    @staticmethod
    def streamlines(surf: HeadSurface, seeds, step: float | None = None, max_steps: int = 2000) -> list[np.ndarray]:
        """Down-gradient particle paths (RK2 on -grad h). Paths stop at grid edges or stagnation."""
        g = surf.grid
        gx, gy = g.gradient(surf.values)
        step = step or 0.5 * min(g.dx, g.dy)
        lines = []
        for sx, sy in np.asarray(seeds, float).reshape(-1, 2):
            p = np.array([sx, sy])
            path = [p.copy()]
            for _ in range(max_steps):
                v1 = -np.array([g.sample(gx, p)[0], g.sample(gy, p)[0]])
                n1 = np.linalg.norm(v1)
                if not np.isfinite(n1) or n1 < 1e-7:
                    break
                mid = p + 0.5 * step * v1 / n1
                v2 = -np.array([g.sample(gx, mid)[0], g.sample(gy, mid)[0]])
                n2 = np.linalg.norm(v2)
                if not np.isfinite(n2) or n2 < 1e-7:
                    break
                p = p + step * v2 / n2
                if not g.contains(p)[0]:
                    break
                path.append(p.copy())
                if len(path) > 3 and np.linalg.norm(path[-1] - path[-3]) < 0.25 * step:
                    break
            if len(path) > 2:
                lines.append(np.array(path))
        return lines

    def hsu_table(self, ground_model=None) -> list[dict]:
        rows = []
        for u in self.column.units:
            unit = self.column.kb[u]
            row = {
                "unit": u,
                "name": unit.name,
                "hsu": unit.hsu.value,
                "permeability_class": unit.permeability,
                "system": self.system_of(u),
                "ea_designation_typical": unit.ea_aquifer_typical,
            }
            if ground_model is not None:
                k = ground_model.get(u, "CPT_k")
                if k is not None:
                    row["k_cpt_geomean_ms"] = 10**k.mean
                    row["k_cpt_n"] = k.n
            rows.append(row)
        return rows

    # ------------------------------------------------------------------ all-in-one
    def run(
        self, project, interpretation, grid: Grid2D, model=None, ground_model=None, seeds_per_system: int | None = None
    ) -> HydroResult:
        obs = self.observations(project, interpretation)
        self.flag_perched(obs, project, interpretation)
        res = HydroResult(obs, {}, hsu=self.hsu_table(ground_model), system_of=dict(self._sys_of))
        unassigned = [o for o in obs if o.system is None]
        if unassigned:
            res.notes.append(f"{len(unassigned)} water observation(s) could not be assigned to an aquifer system")
        for s in self.systems:
            surf = self.head_surface(s["id"], grid, obs)
            if surf is None:
                res.notes.append(f"{s['id']}: no usable observations")
                continue
            res.surfaces[s["id"]] = surf
            res.notes.extend(surf.notes)
            conf = self.confinement(s["id"], surf, model)
            if conf is not None:
                res.confinement[s["id"]] = conf
            res.flow_vectors[s["id"]] = self.flow_vectors(surf)
            n = seeds_per_system or self.cfg.streamline_seeds
            k = max(1, int(math.sqrt(n)))
            xs = np.linspace(grid.x0, grid.x0 + (grid.nx - 1) * grid.dx, k + 2)[1:-1]
            ys = np.linspace(grid.y0, grid.y0 + (grid.ny - 1) * grid.dy, k + 2)[1:-1]
            seeds = np.array([[x, y] for x in xs for y in ys])
            res.seepage_paths[s["id"]] = self.streamlines(surf, seeds)
        return res
