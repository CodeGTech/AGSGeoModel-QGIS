"""Optional GemPy backend (implicit 3D modelling, dipping faults).

GemPy (EUPL-1.2) is *not* bundled. When installed, the bedrock stack can be modelled as a 3D
implicit potential-field model, which handles dipping faults and non-2.5D geometry. Convention
(GemPy 3 documentation, checked October 2026: "surface points mark the bottom of a layer"):
elements are listed top to bottom and an element's surface points define its **base**; the lowest
stack unit becomes the basement. The adapter is experimental: it is not covered by the test suite
because GemPy is not installed in CI.

Limitations: GemPy has no inequality constraints, so holes that end inside a
unit and units eroded at rockhead only influence the GemPy model through the orientations derived
from the 2.5D model; the 2.5D model remains the reference for uncertainty classes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..core.optional import require
from ..geology.contacts import ObservationSet, bedrock_stack
from ..geology.knowledge.kb import StratColumn
from ..models.entities import Fault
from ..models.enums import ContactType


@dataclass
class VoxelModel:
    xs: np.ndarray  # cell centres
    ys: np.ndarray
    zs: np.ndarray
    codes: np.ndarray  # (nx, ny, nz) object array of unit codes
    units: list[str]
    source: str = "gempy"

    def unit_ids(self) -> np.ndarray:
        lut = {u: i for i, u in enumerate(self.units)}
        return np.vectorize(lambda c: lut.get(c, -1))(self.codes)


def build_gempy_model(
    observations: ObservationSet,
    column: StratColumn,
    extent: tuple[float, float, float, float, float, float],
    resolution: tuple[int, int, int] = (40, 40, 30),
    faults: list[Fault] | None = None,
    gradient_model=None,
    max_points_per_surface: int = 400,
) -> VoxelModel:
    gp = require("gempy", "the GemPy implicit modelling backend", package="gempy")
    stack = bedrock_stack(column)
    if len(stack) < 2:
        raise ValueError("GemPy backend needs at least two bedrock units")
    xs, ys, zs, names = [], [], [], []
    for u in stack[:-1]:
        pts = [
            o
            for o in observations.observations
            if o.surface == f"base:{u}" and o.kind in (ContactType.CONFORMABLE, ContactType.ABSENT, ContactType.MAP)
        ]
        if len(pts) > max_points_per_surface:
            pts = pts[:: int(np.ceil(len(pts) / max_points_per_surface))]
        for o in pts:
            xs.append(o.x)
            ys.append(o.y)
            zs.append(o.value)
            names.append(u)
    used = [u for u in stack[:-1] if u in names]
    if not used:
        raise ValueError("no bedrock contacts available for GemPy")
    ox, oy, oz, gx, gy, gz, onames = [], [], [], [], [], [], []
    for u in used:
        sel = [k for k, n in enumerate(names) if n == u]
        cx, cy, cz = (float(np.mean(np.array(v)[sel])) for v in (xs, ys, zs))
        nx_, ny_ = 0.0, 0.0
        if gradient_model is not None and f"base:{u}" in gradient_model.surfaces:
            g = gradient_model.grid
            dzdx, dzdy = g.gradient(gradient_model.surfaces[f"base:{u}"].values)
            p = np.array([[cx, cy]])
            nx_, ny_ = -float(g.sample(dzdx, p)[0]), -float(g.sample(dzdy, p)[0])
            if not np.isfinite(nx_ + ny_):
                nx_, ny_ = 0.0, 0.0
        n = np.array([nx_, ny_, 1.0])
        n /= np.linalg.norm(n)
        ox.append(cx)
        oy.append(cy)
        oz.append(cz)
        gx.append(n[0])
        gy.append(n[1])
        gz.append(n[2])
        onames.append(u)
    fault_names = []
    for f in faults or []:
        zl = np.linspace(extent[4], extent[5], 3)
        tr = f.trace
        for z in zl:
            for x, y in tr:
                xs.append(float(x))
                ys.append(float(y))
                zs.append(float(z))
                names.append(f.name)
        d = tr[-1] - tr[0]
        nrm = np.array([d[1], -d[0], 0.0])
        if f.dip_deg is not None and 0 < f.dip_deg < 90:
            nrm[2] = np.linalg.norm(nrm[:2]) / np.tan(np.radians(f.dip_deg))
        nrm /= np.linalg.norm(nrm)
        mid = tr.mean(axis=0)
        ox.append(float(mid[0]))
        oy.append(float(mid[1]))
        oz.append(float(np.mean(zl)))
        gx.append(nrm[0])
        gy.append(nrm[1])
        gz.append(nrm[2])
        onames.append(f.name)
        fault_names.append(f.name)
    sp = gp.data.SurfacePointsTable.from_arrays(x=np.array(xs), y=np.array(ys), z=np.array(zs), names=names)
    ori = gp.data.OrientationsTable.from_arrays(
        x=np.array(ox),
        y=np.array(oy),
        z=np.array(oz),
        G_x=np.array(gx),
        G_y=np.array(gy),
        G_z=np.array(gz),
        names=onames,
    )
    frame = gp.data.StructuralFrame.from_data_tables(sp, ori)
    gm = gp.create_geomodel(
        project_name="agsgeomodel",
        extent=list(extent),
        resolution=list(resolution),
        structural_frame=frame,
        intpolation_options_tye=gp.data.options.InterpolationOptionsType.DENSE_GRID,
    )
    mapping = {f"Fault_{k}": (fn,) for k, fn in enumerate(fault_names)}
    mapping["Strat"] = tuple(used)
    gp.map_stack_to_surfaces(gm, mapping)
    if fault_names:
        gp.set_is_fault(gm.structural_frame, [f"Fault_{k}" for k in range(len(fault_names))])
    sol = gp.compute_model(gm)
    lb = np.asarray(sol.raw_arrays.lith_block).round().astype(int).reshape(resolution)
    # lithology ids follow the element order of the whole frame (1-based): fault elements come
    # first and occupy ids 1..n_faults, then the stratigraphic elements, basement last
    order = list(used) + [stack[-1]]
    offset = len(fault_names)
    codes = np.empty(lb.shape, dtype=object)
    for i, u in enumerate(order, start=1 + offset):
        codes[lb == i] = u
    codes[codes == None] = stack[-1]  # noqa: E711
    rx, ry, rz = resolution
    x0, x1, y0, y1, z0, z1 = extent
    cx = x0 + (np.arange(rx) + 0.5) * (x1 - x0) / rx
    cy = y0 + (np.arange(ry) + 0.5) * (y1 - y0) / ry
    cz = z0 + (np.arange(rz) + 0.5) * (z1 - z0) / rz
    return VoxelModel(cx, cy, cz, codes, order)
