"""Geologically constrained 2.5D stratigraphic model builder.

Order of operations (each step is recorded in the run diagnostics):

1. **Ground** from a DTM (preferred) or from collar levels.
2. **Cover sequences** (artificial, superficial) as thickness fields: the total thickness of each
   sequence is interpolated (it is smoother and better constrained than its parts), unit
   thicknesses are interpolated and rescaled to sum to the total, then stacked downwards.
   Geological map polygons add "present here" / "absent here" constraints. Cover is not
   displaced by faults. Rockhead = ground minus cover.
3. **Bedrock stack** (strict sequences): a *reference* surface (the base with most observations) is
   kriged with fault barriers and inequality constraints; every other surface is derived by
   stacking *isochores* (unit thicknesses, continuous across faults — faulting post-dates
   deposition), then corrected by kriging its own residuals (fault-aware) so all observations
   are honoured. Surfaces are clamped so they never cross (topology).
4. **Erosion**: bedrock unit tops are truncated by rockhead.
5. **Uncertainty**: kriging / propagated standard deviations -> confidence, RAG and the four
   interpretation classes per surface.
6. Optional **Monte Carlo** realisations (:meth:`StratigraphicModel.simulate`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..core.config import ModelConfig
from ..core.errors import ModelError
from ..core.grid import Grid2D
from ..core.provenance import RunRecord, stable_hash
from ..geology.contacts import Observation, ObservationSet, bedrock_stack, isochores
from ..geology.knowledge.kb import StratColumn
from ..models.entities import Fault, Location, MapContactLine, MapUnitPolygon
from ..models.enums import ContactType, DataSource
from .barriers import FaultBarrier
from .constraints import fit_constrained, line_sample_points, polygon_sample_points
from .interpolators import make_interpolator
from .kriging import KrigingWeights, OrdinaryKriging
from .uncertainty import classify, confidence_from_std
from .variogram import Variogram, default_variogram

EQ_KINDS = (ContactType.CONFORMABLE, ContactType.EROSIONAL, ContactType.ABSENT, ContactType.MAP)


@dataclass
class SurfaceGrid:
    name: str
    kind: str  # elevation | thickness
    values: np.ndarray
    std: np.ndarray
    confidence: np.ndarray | None = None
    classes: np.ndarray | None = None
    method: str = ""
    variogram: dict | None = None
    diagnostics: dict = field(default_factory=dict)
    n_data: int = 0

    def summary(self) -> dict:
        v = self.values[np.isfinite(self.values)]
        return {
            "name": self.name,
            "kind": self.kind,
            "method": self.method,
            "n_data": self.n_data,
            "min": float(v.min()) if v.size else None,
            "max": float(v.max()) if v.size else None,
            "mean_std": float(np.nanmean(self.std)) if np.isfinite(self.std).any() else None,
            **{k: v for k, v in self.diagnostics.items() if isinstance(v, (int, float, str))},
        }


@dataclass
class FieldModel:
    """What is needed to simulate a kriged field: deterministic grid, variogram, weights, data."""

    name: str
    det: np.ndarray
    variogram: Variogram
    weights: KrigingWeights
    data_xy: np.ndarray
    nonneg: bool = False


@dataclass
class ModelInputs:
    observations: ObservationSet
    column: StratColumn
    locations: list[Location] = field(default_factory=list)
    grid: Grid2D | None = None
    dtm: tuple[Grid2D, np.ndarray] | None = None
    faults: list[Fault] = field(default_factory=list)
    map_units: list[MapUnitPolygon] = field(default_factory=list)
    map_contacts: list[MapContactLine] = field(default_factory=list)
    superficial_map_complete: bool = False
    crs: str | None = None


class StratigraphicModel:
    def __init__(
        self,
        grid: Grid2D,
        column: StratColumn,
        units: list[str],
        tops: dict[str, np.ndarray],
        bases: dict[str, np.ndarray],
        surfaces: dict[str, SurfaceGrid],
        run: RunRecord,
        fields: dict[str, FieldModel] | None = None,
        warnings: list[str] | None = None,
        cover_order: list[tuple[str, list[str]]] | None = None,
        stack: list[str] | None = None,
        bottom: float = -100.0,
    ):
        self.grid = grid
        self.column = column
        self.units = units
        self.tops = tops
        self.bases = bases
        self.surfaces = surfaces
        self.run = run
        self.fields = fields or {}
        self.warnings = warnings or []
        self.cover_order = cover_order or []
        self.stack = stack or []
        self.bottom = bottom
        self.faults: list[Fault] = []
        self._realisations: dict[str, np.ndarray] | None = None

    def fault_segments(self) -> np.ndarray:
        segs = [s for f in self.faults for s in f.segments()]
        return np.array(segs, float) if segs else np.zeros((0, 2, 2))

    def sample_grid(self, values: np.ndarray, xy, fault_aware: bool = True) -> np.ndarray:
        """Sample any model grid; across known faults only same-block nodes are used."""
        segs = self.fault_segments() if fault_aware else np.zeros((0, 2, 2))
        if len(segs):
            return self.grid.sample_fault_aware(values, xy, segs)
        return self.grid.sample(values, xy)

    # ------------------------------------------------------------------ queries
    def thickness(self, unit: str) -> np.ndarray:
        return np.maximum(self.tops[unit] - self.bases[unit], 0.0)

    def sample(self, name: str, xy) -> np.ndarray:
        if name in self.tops:
            return self.sample_grid(self.tops[name], xy)
        return self.sample_grid(self.surfaces[name].values, xy)

    def column_at(self, x: float, y: float, min_thickness: float = 0.01) -> list[tuple[str, float, float]]:
        p = np.array([[x, y]])
        out = []
        for u in self.units:
            t = float(self.sample_grid(self.tops[u], p)[0])
            b = float(self.sample_grid(self.bases[u], p)[0])
            if np.isfinite(t) and np.isfinite(b) and t - b > min_thickness:
                out.append((u, t, b))
        return out

    def unit_at(self, xy, z) -> np.ndarray:
        """Unit code (or '') at points ``xy`` (N, 2) and elevations ``z`` (N,)."""
        xy = np.asarray(xy, float).reshape(-1, 2)
        z = np.asarray(z, float).ravel()
        out = np.full(len(z), "", dtype=object)
        for u in self.units:
            t = self.sample_grid(self.tops[u], xy)
            b = self.sample_grid(self.bases[u], xy)
            m = (z <= t + 1e-9) & (z > b) & (out == "")
            out[m] = u
        return out

    def surface_std(self, name: str, xy) -> np.ndarray:
        s = self.surfaces.get(name)
        return self.sample_grid(s.std, xy) if s is not None else np.full(len(np.asarray(xy).reshape(-1, 2)), np.nan)

    def unit_top_std(self, unit: str, xy) -> np.ndarray:
        name = self._top_surface_name(unit)
        return self.surface_std(name, xy) if name else np.full(len(np.asarray(xy).reshape(-1, 2)), np.nan)

    def _top_surface_name(self, unit: str) -> str | None:
        if unit in self.stack:
            m = self.stack.index(unit)
            return "rockhead" if m == 0 else f"base:{self.stack[m - 1]}"
        for seq_id, codes in self.cover_order:
            if unit in codes:
                k = codes.index(unit)
                return f"top:{unit}" if k else f"top_seq:{seq_id}"
        return None

    # ------------------------------------------------------------------ Monte Carlo
    def simulate(self, n: int | None = None, seed: int | None = None) -> dict[str, np.ndarray]:
        """Generate ``n`` realisations of unit tops/bases (stored and returned as float32 arrays)."""
        from .stochastic import conditional_realisations

        n = n or 50
        rng = np.random.default_rng(self.run.seed if seed is None else seed)
        g = self.grid
        sims: dict[str, np.ndarray] = {}
        for name, fm in self.fields.items():
            r = conditional_realisations(g, fm.det, fm.variogram, fm.weights, fm.data_xy, rng, n)
            if fm.nonneg:
                r = np.maximum(r, 0.0)
            sims[name] = r
        tops = {u: np.repeat(self.tops[u][None].astype(np.float32), n, axis=0) for u in self.units}
        bases = {u: np.repeat(self.bases[u][None].astype(np.float32), n, axis=0) for u in self.units}
        ground = self.surfaces["ground"].values
        # cover: perturb sequence totals, keep unit proportions
        z = np.repeat(ground[None], n, axis=0)
        for seq_id, codes in self.cover_order:
            det_tot = sum(self.thickness(c) for c in codes)
            key = f"thk_seq:{seq_id}"
            tot = sims[key] if key in sims else np.repeat(det_tot[None], n, axis=0)
            frac = {
                c: np.where(det_tot > 1e-6, self.thickness(c) / np.where(det_tot > 1e-6, det_tot, 1), 0.0)
                for c in codes
            }
            for c in codes:
                t = tot * frac[c][None]
                tops[c] = z.astype(np.float32)
                z = z - t
                bases[c] = z.astype(np.float32)
        rock = z
        # bedrock: perturb reference and isochores along the stacking path
        st = self.stack
        if st:
            ref_name = self.run.summary.get("reference_surface")
            S = {}
            for k, u in enumerate(st[:-1]):
                S[k] = np.repeat(self.surfaces[f"base:{u}"].values[None], n, axis=0)
            if ref_name and ref_name in sims:
                r = st.index(ref_name.split(":")[1])
                dref = sims[ref_name] - self.fields[ref_name].det[None]
                delta = {r: dref}
                for k in range(r + 1, len(st) - 1):
                    key = f"iso:{st[k]}"
                    d_iso = sims[key] - self.fields[key].det[None] if key in sims else 0.0
                    delta[k] = delta[k - 1] - d_iso
                for k in range(r - 1, -1, -1):
                    key = f"iso:{st[k + 1]}"
                    d_iso = sims[key] - self.fields[key].det[None] if key in sims else 0.0
                    delta[k] = delta[k + 1] + d_iso
                for k in S:
                    S[k] = S[k] + delta.get(k, 0.0)
            for k in range(1, len(st) - 1):
                S[k] = np.minimum(S[k], S[k - 1])
            for m, u in enumerate(st):
                top_dep = S[m - 1] if m > 0 else np.full_like(rock, np.inf)
                base_dep = S[m] if m < len(st) - 1 else np.full_like(rock, self.bottom)
                top = np.minimum(top_dep, rock)
                base = np.minimum(np.minimum(base_dep, rock), top)
                tops[u] = top.astype(np.float32)
                bases[u] = base.astype(np.float32)
        self._realisations = {"tops": tops, "bases": bases, "n": n}
        return self._realisations

    def probability(self, xy, z) -> tuple[list[str], np.ndarray]:
        """P(unit) at points (from the stored realisations). Returns ``(units, (N, n_units))``."""
        if self._realisations is None:
            raise ModelError("call simulate() first")
        xy = np.asarray(xy, float).reshape(-1, 2)
        z = np.asarray(z, float).ravel()
        n = self._realisations["n"]
        P = np.zeros((len(z), len(self.units)))
        for k in range(n):
            assigned = np.zeros(len(z), dtype=bool)
            for j, u in enumerate(self.units):
                t = self.sample_grid(self._realisations["tops"][u][k], xy)
                b = self.sample_grid(self._realisations["bases"][u][k], xy)
                m = (z <= t + 1e-9) & (z > b) & ~assigned
                P[m, j] += 1
                assigned |= m
        return self.units, P / n

    def summary(self) -> dict:
        return {
            "grid": self.grid.to_dict(),
            "units": self.units,
            "surfaces": {k: s.summary() for k, s in self.surfaces.items()},
            "warnings": self.warnings,
            "run": self.run.summary,
        }


class StratigraphicModelBuilder:
    def __init__(self, config: ModelConfig | None = None, n_threads: int | None = None):
        import os

        self.cfg = config or ModelConfig()
        self.n_threads = n_threads if n_threads is not None else min(4, os.cpu_count() or 1)

    # ------------------------------------------------------------------ helpers
    def _map(self, fn, items):
        """Run independent field interpolations in threads (NumPy releases the GIL in LAPACK)."""
        items = list(items)
        n = self.n_threads
        if n <= 1 or len(items) <= 1:
            return [fn(i) for i in items]
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(n, len(items))) as ex:
            return list(ex.map(fn, items))

    def _interp(self, barrier: FaultBarrier | None, method: str | None = None, var_floor: float = 1.0):
        return lambda: make_interpolator(self.cfg.interpolation, barrier, method, var_floor=var_floor)

    def _arrays(self, obs: list[Observation]):
        xy = np.array([[o.x, o.y] for o in obs]) if obs else np.zeros((0, 2))
        v = np.array([o.value for o in obs]) if obs else np.zeros(0)
        s = np.array([o.sigma for o in obs]) if obs else np.zeros(0)
        return xy, v, s

    def _field(
        self,
        name: str,
        kind: str,
        grid: Grid2D,
        eq: list[Observation],
        ge: list[Observation],
        le: list[Observation],
        barrier: FaultBarrier | None,
        nonneg: bool,
        extra_ge=None,
        extra_le=None,
        extra_eq=None,
        fields: dict | None = None,
        warnings: list | None = None,
        fallback: float | None = None,
        fallback_std: float = 5.0,
    ) -> SurfaceGrid:
        nodes = grid.nodes()
        exy, ev, es = self._arrays(eq)
        gxy, gv, _ = self._arrays(ge)
        lxy, lv, _ = self._arrays(le)
        if extra_eq is not None and len(extra_eq[1]):
            exy = np.vstack([exy, extra_eq[0]])
            ev = np.concatenate([ev, extra_eq[1]])
            es = np.concatenate([es, extra_eq[2]])
        if extra_ge is not None and len(extra_ge[1]):
            gxy = np.vstack([gxy, extra_ge[0]])
            gv = np.concatenate([gv, extra_ge[1]])
        if extra_le is not None and len(extra_le[1]):
            lxy = np.vstack([lxy, extra_le[0]])
            lv = np.concatenate([lv, extra_le[1]])
        # conservative variance floor for sparse data: 1 m for elevations, 20 % of the mean for thicknesses
        if kind == "thickness":
            mean_t = float(np.mean(ev)) if len(ev) else (fallback or 1.0)
            var_floor = max((0.2 * mean_t) ** 2, 0.25)
        else:
            var_floor = 1.0
        model, diag, used = fit_constrained(
            self._interp(barrier, var_floor=var_floor),
            exy,
            ev,
            es,
            gxy,
            gv,
            lxy,
            lv,
            max_iter=self.cfg.interpolation.max_inequality_iterations,
        )
        if model is None:
            val = np.full(grid.shape, np.nan if fallback is None else fallback)
            std = np.full(grid.shape, np.nan if fallback is None else fallback_std)
            if warnings is not None:
                warnings.append(f"{name}: no data; " + ("regional prior used" if fallback is not None else "undefined"))
            return SurfaceGrid(
                name,
                kind,
                val,
                std,
                method="prior" if fallback is not None else "none",
                diagnostics=diag.to_dict(),
                n_data=0,
            )
        if isinstance(model, OrdinaryKriging):
            pred, std, W = model.predict(nodes, return_std=True, return_weights=True)
            vg = model.vg
            if fields is not None:
                fields[name] = FieldModel(name, pred.reshape(grid.shape), vg, W, used[0], nonneg)
            vgd = vg.to_dict()
        else:
            pred, std = model.predict(nodes, return_std=True)
            vgd = None
        val = pred.reshape(grid.shape)
        std = np.asarray(std).reshape(grid.shape)
        if nonneg:
            val = np.maximum(val, 0.0)
        if diag.remaining_violations and warnings is not None:
            warnings.append(
                f"{name}: {diag.remaining_violations} inequality constraint(s) still violated "
                f"(max {diag.max_violation_m:.2f} m) - contradictory data?"
            )
        return SurfaceGrid(
            name,
            kind,
            val,
            std,
            method=self.cfg.interpolation.method,
            variogram=vgd,
            diagnostics=diag.to_dict(),
            n_data=len(eq),
        )

    # ------------------------------------------------------------------ build
    def build(self, inp: ModelInputs) -> StratigraphicModel:
        cfg = self.cfg
        col = inp.column
        obs = inp.observations
        warnings: list[str] = []
        by_s: dict[str, list[Observation]] = {}
        for o in obs.observations:
            by_s.setdefault(o.surface, []).append(o)
        pts = (
            np.array([[l.x, l.y] for l in inp.locations])
            if inp.locations
            else np.array([[o.x, o.y] for o in obs.observations])
        )
        if len(pts) == 0:
            raise ModelError("no located observations to model")
        if inp.grid is not None:
            grid = inp.grid
        elif cfg.cell_size_m:
            xmin, ymin = pts.min(axis=0) - cfg.buffer_m
            xmax, ymax = pts.max(axis=0) + cfg.buffer_m
            grid = Grid2D.from_extent(xmin, ymin, xmax, ymax, cfg.cell_size_m, inp.crs)
        else:
            grid = Grid2D.auto(pts, cfg.buffer_m, crs=inp.crs)
        nodes = grid.nodes()
        barrier = FaultBarrier.from_faults(inp.faults) if (cfg.respect_faults and inp.faults) else FaultBarrier()
        if not barrier.empty and cfg.interpolation.method in ("rbf", "tin"):
            warnings.append("faults are honoured by kriging and IDW only; RBF/TIN surfaces ignore partial faults")
        fields: dict[str, FieldModel] = {}
        surfaces: dict[str, SurfaceGrid] = {}

        def map_tol(m) -> float:
            """Positional tolerance of map linework: configured value or ~0.5 mm at the map's scale."""
            return max(cfg.map_tolerance_m, 0.0005 * (m.nominal_scale or 0))

        def split(name):
            lst = by_s.get(name, [])
            return (
                [o for o in lst if o.kind in EQ_KINDS],
                [o for o in lst if o.kind == ContactType.GE],
                [o for o in lst if o.kind == ContactType.LE],
            )

        # ---------------------------------------------------------- 1. ground
        if inp.dtm is not None:
            dg, dv = inp.dtm
            gv = dg.sample(dv, nodes).reshape(grid.shape)
            if np.isnan(gv).any():
                eq, _, _ = split("ground")
                if eq:
                    fill = self._field("ground_fill", "elevation", grid, eq, [], [], None, False)
                    gv = np.where(np.isnan(gv), fill.values, gv)
            gs = np.full(grid.shape, 0.15)
            surfaces["ground"] = SurfaceGrid("ground", "elevation", gv, gs, method="DTM", n_data=int(dv.size))
            eq, _, _ = split("ground")
            if eq:
                exy, ev, _ = self._arrays(eq)
                diff = ev - grid.sample(gv, exy)
                bad = np.abs(diff) > 0.5
                if bad.any():
                    warnings.append(
                        f"ground: {int(bad.sum())} collar level(s) differ from the DTM by > 0.5 m "
                        f"(max {np.nanmax(np.abs(diff)):.2f} m) - check levelling / DTM datum"
                    )
        else:
            eq, ge, le = split("ground")
            surfaces["ground"] = self._field("ground", "elevation", grid, eq, ge, le, None, False, warnings=warnings)
            warnings.append("ground: no DTM supplied; ground surface interpolated from collar levels only")
        G = surfaces["ground"].values
        # ---------------------------------------------------------- 2. cover sequences
        tops: dict[str, np.ndarray] = {}
        bases: dict[str, np.ndarray] = {}
        z = G.copy()
        z_var = np.nan_to_num(surfaces["ground"].std) ** 2
        cover_order: list[tuple[str, list[str]]] = []
        sup_polys = [m for m in inp.map_units if m.kind in ("superficial", "artificial")]
        for seq in col.cover_sequences():
            codes = seq.codes
            cover_order.append((seq.id, codes))
            thk: dict[str, SurfaceGrid] = {}

            def unit_field(c):
                eq, ge, le = split(f"thk:{c}")
                extra_ge = None
                if cfg.use_map_constraints:
                    pts_c = [
                        polygon_sample_points(grid, m.rings, map_tol(m)) for m in sup_polys if c in col.resolve(m.code)
                    ]
                    pts_c = [p for p in pts_c if len(p)]
                    if pts_c:
                        P = np.vstack(pts_c)
                        extra_ge = (P, np.full(len(P), cfg.min_mapped_thickness_m))
                if not eq and extra_ge is None and not ge:
                    return c, SurfaceGrid(
                        f"thk:{c}",
                        "thickness",
                        np.zeros(grid.shape),
                        np.zeros(grid.shape),
                        method="absent (never observed)",
                        n_data=0,
                    )
                if not eq and extra_ge is None:
                    warnings.append(f"thk:{c}: only lower bounds available")
                return c, self._field(
                    f"thk:{c}",
                    "thickness",
                    grid,
                    eq,
                    ge,
                    le,
                    None,
                    True,
                    extra_ge=extra_ge,
                    fields=fields,
                    warnings=warnings,
                )

            for c, sgrid in self._map(unit_field, codes):
                thk[c] = sgrid
            eq, ge, le = split(f"thk_seq:{seq.id}")
            extra_eq = None
            extra_ge = None
            if cfg.use_map_constraints and seq.kind == "superficial" and sup_polys:
                inside_any = np.zeros(len(nodes), dtype=bool)
                for m in sup_polys:
                    if col.resolve(m.code) & set(codes):
                        P = polygon_sample_points(grid, m.rings, map_tol(m))
                        if len(P):
                            extra_ge = (
                                (P, np.full(len(P), cfg.min_mapped_thickness_m))
                                if extra_ge is None
                                else (
                                    np.vstack([extra_ge[0], P]),
                                    np.concatenate([extra_ge[1], np.full(len(P), cfg.min_mapped_thickness_m)]),
                                )
                            )
                if inp.superficial_map_complete:
                    # the map shows no superficial deposits outside its polygons: thickness 0 there
                    for m in sup_polys:
                        inside_any |= _inside_buffered(nodes, m.rings, map_tol(m))
                    outside = nodes[~inside_any]
                    if len(outside):
                        sel = outside[:: max(1, len(outside) // 200)]
                        extra_eq = (sel, np.zeros(len(sel)), np.full(len(sel), cfg.map_sigma_m))
            if eq or extra_eq is not None:
                tot = self._field(
                    f"thk_seq:{seq.id}",
                    "thickness",
                    grid,
                    eq,
                    ge,
                    le,
                    None,
                    True,
                    extra_ge=extra_ge,
                    extra_eq=extra_eq,
                    fields=fields,
                    warnings=warnings,
                )
            else:
                s = sum((t.values for t in thk.values()), np.zeros(grid.shape))
                tot = SurfaceGrid(f"thk_seq:{seq.id}", "thickness", s, np.full(grid.shape, 1.0), method="sum of units")
            surfaces[tot.name] = tot
            ssum = sum((thk[c].values for c in codes if c in thk), np.zeros(grid.shape))
            for c in codes:
                tc = thk.get(c)
                if tc is None:
                    t = np.zeros(grid.shape)
                    tstd = np.zeros(grid.shape)
                else:
                    scale = np.where(ssum > 1e-6, tot.values / np.where(ssum > 1e-6, ssum, 1.0), 0.0)
                    t = tc.values * scale
                    tstd = np.nan_to_num(tc.std) * np.minimum(scale, 3.0)
                    # where the total says cover exists but no unit field supports it: give it to the
                    # unit with the highest regional presence in this sequence
                orphan = (ssum <= 1e-6) & (tot.values > 1e-6)
                if orphan.any() and c == max(codes, key=lambda q: col.presence(q)):
                    t = np.where(orphan, tot.values, t)
                tops[c] = z.copy()
                z = z - t
                bases[c] = z.copy()
                surfaces[f"thk:{c}"] = SurfaceGrid(
                    f"thk:{c}",
                    "thickness",
                    t,
                    tstd,
                    method=tc.method if tc else "absent",
                    n_data=tc.n_data if tc else 0,
                    diagnostics=tc.diagnostics if tc else {},
                )
                surfaces[f"top:{c}"] = SurfaceGrid(f"top:{c}", "elevation", tops[c], np.sqrt(z_var), method="stacked")
            surfaces[f"top_seq:{seq.id}"] = SurfaceGrid(
                f"top_seq:{seq.id}", "elevation", tops[codes[0]], np.sqrt(z_var), method="stacked"
            )
            z_var = z_var + np.nan_to_num(tot.std) ** 2
        R = z
        r_std = np.sqrt(z_var)
        r_eq, _, _ = split("rockhead")
        surfaces["rockhead"] = SurfaceGrid(
            "rockhead", "elevation", R, r_std, method="ground - cover thickness", n_data=len(r_eq)
        )
        # ---------------------------------------------------------- 3. bedrock stack
        stack = bedrock_stack(col)
        surf_names = [f"base:{u}" for u in stack[:-1]]
        bottom = float(np.nanmin(R)) - 50.0
        reference = None
        S: dict[int, SurfaceGrid] = {}
        if surf_names:
            counts = {s: len([o for o in split(s)[0] if not o.inferred]) for s in surf_names}
            reference = (
                cfg.reference_surface
                if cfg.reference_surface in surf_names
                else max(surf_names, key=lambda s: counts[s])
            )
            if counts[reference] == 0 and not split(reference)[1] and not split(reference)[2]:
                warnings.append("bedrock: no bedrock contacts observed; bedrock surfaces are not modelled")
                reference = None
        map_ge: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
        map_le: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
        map_eq: dict[str, list[tuple[np.ndarray, np.ndarray, np.ndarray]]] = {}
        if cfg.use_map_constraints:
            for m in inp.map_units:
                if m.kind != "bedrock":
                    continue
                M = [u for u in col.resolve(m.code) if u in stack]
                if not M:
                    continue
                a, b = min(stack.index(u) for u in M), max(stack.index(u) for u in M)
                P = polygon_sample_points(grid, m.rings, map_tol(m))
                if not len(P):
                    continue
                rz = grid.sample(R, P)
                if a > 0:
                    map_ge.setdefault(f"base:{stack[a - 1]}", []).append((P, rz + 0.1))
                if b < len(stack) - 1:
                    map_le.setdefault(f"base:{stack[b]}", []).append((P, rz - cfg.min_mapped_thickness_m))
            for ln in inp.map_contacts:
                ua = col.resolve(ln.upper_code)
                ua = [u for u in ua if u in stack]
                if not ua:
                    continue
                s_name = f"base:{max(ua, key=stack.index)}"
                if s_name not in surf_names:
                    continue
                P = line_sample_points(ln.line, max(grid.dx, cfg.map_tolerance_m))
                rz = grid.sample(R, P)
                ok = np.isfinite(rz)
                map_eq.setdefault(s_name, []).append((P[ok], rz[ok], np.full(ok.sum(), cfg.map_sigma_m)))

        def extras(s):
            ge = map_ge.get(s)
            le = map_le.get(s)
            eqm = map_eq.get(s)
            cat = lambda L, k: np.concatenate([x[k] for x in L]) if L else None  # noqa: E731
            return (
                (np.vstack([x[0] for x in ge]), cat(ge, 1)) if ge else None,
                (np.vstack([x[0] for x in le]), cat(le, 1)) if le else None,
                (np.vstack([x[0] for x in eqm]), cat(eqm, 1), cat(eqm, 2)) if eqm else None,
            )

        if reference is not None:
            r = surf_names.index(reference)
            eq, ge, le = split(reference)
            xg, xl, xe = extras(reference)
            S[r] = self._field(
                reference,
                "elevation",
                grid,
                eq,
                ge,
                le,
                barrier,
                False,
                extra_ge=xg,
                extra_le=xl,
                extra_eq=xe,
                fields=fields,
                warnings=warnings,
            )
            iso_obs = isochores(obs, col).by_surface()
            iso: dict[str, SurfaceGrid] = {}

            def iso_field(u):
                lst = iso_obs.get(f"iso:{u}", [])
                eq_i = [o for o in lst if o.kind in EQ_KINDS]
                ge_i = [o for o in lst if o.kind == ContactType.GE]
                cu = col.column_unit(u)
                return u, self._field(
                    f"iso:{u}",
                    "thickness",
                    grid,
                    eq_i,
                    ge_i,
                    [],
                    None,
                    True,
                    fields=fields,
                    warnings=warnings,
                    fallback=cu.thickness_mean,
                    fallback_std=cu.thickness_sd,
                )

            for u, sgrid in self._map(iso_field, stack[1:-1]):
                iso[u] = sgrid
                surfaces[f"iso:{u}"] = sgrid
            for k in range(r + 1, len(surf_names)):
                u = stack[k]
                val = S[k - 1].values - iso[u].values
                std = np.sqrt(np.nan_to_num(S[k - 1].std) ** 2 + np.nan_to_num(iso[u].std) ** 2)
                S[k] = SurfaceGrid(surf_names[k], "elevation", val, std, method="reference - isochores")
            for k in range(r - 1, -1, -1):
                u = stack[k + 1]
                val = S[k + 1].values + iso[u].values
                std = np.sqrt(np.nan_to_num(S[k + 1].std) ** 2 + np.nan_to_num(iso[u].std) ** 2)
                S[k] = SurfaceGrid(surf_names[k], "elevation", val, std, method="reference + isochores")
            # residual correction of derived surfaces (fault-aware) so that all contacts are honoured
            for k, sname in enumerate(surf_names):
                if k == r:
                    continue
                eq, ge, le = split(sname)
                xg, xl, xe = extras(sname)
                exy, ev, es = self._arrays(eq)
                if xe is not None:
                    exy, ev, es = np.vstack([exy, xe[0]]), np.concatenate([ev, xe[1]]), np.concatenate([es, xe[2]])

                def base_at(P, k=k):
                    return grid.sample(S[k].values, P)

                res_eq = ev - base_at(exy) if len(ev) else np.zeros(0)
                gxy, gv, _ = self._arrays(ge)
                lxy, lv, _ = self._arrays(le)
                if xg is not None:
                    gxy, gv = np.vstack([gxy, xg[0]]), np.concatenate([gv, xg[1]])
                if xl is not None:
                    lxy, lv = np.vstack([lxy, xl[0]]), np.concatenate([lv, xl[1]])
                res_ge = gv - base_at(gxy) if len(gv) else np.zeros(0)
                res_le = lv - base_at(lxy) if len(lv) else np.zeros(0)
                if len(res_eq) == 0 and not (res_ge > 0).any() and not (res_le < 0).any():
                    S[k].n_data = 0
                    continue
                vg = default_variogram(
                    exy if len(exy) else gxy, res_eq if len(res_eq) else res_ge, 0.15, "residual correction (assumed)"
                )
                vg.range = max(vg.range * 0.5, 4 * grid.dx)

                # residuals are not elevations: faults stay barriers but no throw correction is applied
                # (the stacked surface already carries the fault offset from the reference surface)
                def fac(vg=vg):
                    return OrdinaryKriging(
                        vg, self.cfg.interpolation.n_neighbours, "constant", barrier, use_throws=False
                    )

                if len(res_eq) == 0:
                    # inequality-only: zero residual everywhere except where bounds are violated
                    exy = np.zeros((0, 2))
                model, diag, _ = fit_constrained(
                    fac, exy, res_eq, es if len(es) else np.zeros(0), gxy, res_ge, lxy, res_le, max_iter=6
                )
                if model is None:
                    continue
                rp, rs = model.predict(nodes, return_std=True)
                rp = np.nan_to_num(rp).reshape(grid.shape)
                rs = np.asarray(rs).reshape(grid.shape)
                # Near the surface's own contacts the residual correction removes the stacking error;
                # far from them it reverts to zero and the stacked (propagated) uncertainty applies.
                ratio = np.clip(np.nan_to_num(rs, nan=np.sqrt(vg.total_sill)) ** 2 / max(vg.total_sill, 1e-9), 0.0, 1.0)
                a2 = np.nan_to_num(S[k].std, nan=10.0) ** 2
                S[k].values = S[k].values + rp
                S[k].std = np.sqrt(a2 * ratio + float(np.median(es)) ** 2 if len(es) else a2 * ratio)
                S[k].n_data = len(eq)
                S[k].diagnostics = {
                    "residual_rms_before": float(np.sqrt(np.mean(res_eq**2))) if len(res_eq) else 0.0,
                    **diag.to_dict(),
                }
                S[k].method += " + residual kriging"
            clamps = 0
            for k in range(1, len(surf_names)):
                above = S[k - 1].values
                bad = S[k].values > above
                clamps += int(bad.sum())
                S[k].values = np.where(bad, above, S[k].values)
            if clamps:
                warnings.append(f"bedrock: {clamps} node(s) clamped to prevent crossing surfaces (zero thickness)")
            for k, sname in enumerate(surf_names):
                surfaces[sname] = S[k]
            bottom = float(np.nanmin(S[len(surf_names) - 1].values)) - 30.0
            for m, u in enumerate(stack):
                top_dep = S[m - 1].values if m > 0 else np.full(grid.shape, np.inf)
                base_dep = S[m].values if m < len(stack) - 1 else np.full(grid.shape, bottom)
                top = np.minimum(top_dep, R)
                base = np.minimum(np.minimum(base_dep, R), top)
                tops[u] = top
                bases[u] = base
        else:
            for u in stack:
                tops[u] = np.full(grid.shape, np.nan)
                bases[u] = np.full(grid.shape, np.nan)
        # ---------------------------------------------------------- 4. classes & confidence
        unc = cfg.uncertainty
        for name, sg in surfaces.items():
            sg.confidence = confidence_from_std(sg.std, unc)
            eqs = [o for o in by_s.get(name, []) if o.kind in EQ_KINDS]
            prim = (
                np.array(
                    [
                        [o.x, o.y]
                        for o in eqs
                        if not o.inferred
                        and o.source in (DataSource.AGS4, DataSource.AGS3)
                        and o.kind != ContactType.MAP
                    ]
                )
                if eqs
                else np.zeros((0, 2))
            )
            sec = (
                np.array(
                    [
                        [o.x, o.y]
                        for o in eqs
                        if o.inferred or o.source not in (DataSource.AGS4, DataSource.AGS3) or o.kind == ContactType.MAP
                    ]
                )
                if eqs
                else np.zeros((0, 2))
            )
            if name == "ground" and sg.method == "DTM":
                sg.classes = np.zeros(grid.shape, dtype=int)
            else:
                sg.classes = classify(grid, sg.std, prim, sec, unc)
        units = [c for _, codes in cover_order for c in codes] + stack
        cfg_d = cfg.to_dict()
        input_hash = stable_hash(
            {
                "obs": [
                    (
                        o.surface,
                        o.location_id,
                        round(o.x, 3),
                        round(o.y, 3),
                        round(o.value, 3),
                        o.kind.value,
                        round(o.sigma, 3),
                    )
                    for o in obs.observations
                ],
                "faults": [(f.name, f.trace.round(3).tolist(), f.throw_m, f.downthrown_side) for f in inp.faults],
                "maps": [(m.code, m.kind, [r.round(2).tolist() for r in m.rings]) for m in inp.map_units],
                "dtm": None if inp.dtm is None else stable_hash(inp.dtm[1].round(3).tobytes().hex()[:2000]),
                "grid": grid.to_dict(),
            }
        )
        run = RunRecord("stratigraphic_model", input_hash, cfg_d, col.kb.version, cfg.seed)
        run.summary = {
            "reference_surface": reference,
            "province": col.id,
            "grid": grid.to_dict(),
            "n_observations": len(obs.observations),
            "faults": [f.name for f in inp.faults],
            "map_polygons": len(inp.map_units),
        }
        run.warnings = warnings
        model = StratigraphicModel(
            grid, col, units, tops, bases, surfaces, run, fields, warnings, cover_order, stack, bottom
        )
        model.faults = list(inp.faults) if cfg.respect_faults else []
        if cfg.monte_carlo_realisations > 0:
            model.simulate(cfg.monte_carlo_realisations)
        return model


def _inside_buffered(nodes: np.ndarray, rings, tol: float) -> np.ndarray:
    """Nodes inside a polygon or within ``tol`` of it (generous 'inside' for absence constraints)."""
    from ..core.geometry import distance_to_segments, points_in_polygon, polygon_boundary_segments

    inside = points_in_polygon(nodes, rings)
    if tol > 0 and (~inside).any():
        d = distance_to_segments(nodes[~inside], polygon_boundary_segments(rings))
        idx = np.where(~inside)[0]
        inside[idx[d <= tol]] = True
    return inside
