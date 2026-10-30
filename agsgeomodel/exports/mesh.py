"""Triangle meshes from the 2.5D model: surfaces, unit solids, borehole sticks and voxel blocks.

All mesh exporters (VTK, OBJ, PLY, glTF, DXF, IFC) consume :class:`TriMesh` objects so geometry is
built once and every format shows exactly the same model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class TriMesh:
    name: str
    vertices: np.ndarray  # (N, 3) world coordinates (z possibly exaggerated)
    faces: np.ndarray  # (M, 3) int
    colour: str = "#BDBDBD"
    properties: dict = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return len(self.faces) == 0

    def compact(self) -> TriMesh:
        used = np.unique(self.faces)
        remap = np.full(len(self.vertices), -1, dtype=np.int64)
        remap[used] = np.arange(len(used))
        return TriMesh(self.name, self.vertices[used], remap[self.faces], self.colour, self.properties)


def _grid_vertices(grid, values, ve):
    X, Y = grid.mesh()
    return np.column_stack([X.ravel(), Y.ravel(), np.asarray(values, float).ravel() * ve])


def _grid_faces(ny, nx, mask_nodes) -> np.ndarray:
    idx = np.arange(ny * nx).reshape(ny, nx)
    a, b, c, d = idx[:-1, :-1], idx[:-1, 1:], idx[1:, 1:], idx[1:, :-1]
    ok = mask_nodes.ravel()
    f1 = np.stack([a, b, c], axis=-1).reshape(-1, 3)
    f2 = np.stack([a, c, d], axis=-1).reshape(-1, 3)
    faces = np.concatenate([f1, f2])
    return faces[ok[faces].all(axis=1)]


def surface_mesh(grid, values, name: str, colour: str = "#BDBDBD", vertical_exaggeration: float = 1.0) -> TriMesh:
    v = np.asarray(values, float)
    verts = _grid_vertices(grid, v, vertical_exaggeration)
    faces = _grid_faces(grid.ny, grid.nx, np.isfinite(v))
    return TriMesh(name, verts, faces, colour).compact()


def unit_solid(
    model, unit: str, vertical_exaggeration: float = 1.0, min_thickness: float = 0.01, stride: int = 1
) -> TriMesh:
    """Watertight solid of one unit: top surface (normals up), base (normals down) and outward side
    walls on the model boundary. Where the unit pinches out (thickness <= ``min_thickness``) the
    base vertex is merged into the top vertex, so the solid closes along the pinch-out line."""
    g = model.grid
    top = np.asarray(model.tops[unit], float)[::stride, ::stride]
    base = np.asarray(model.bases[unit], float)[::stride, ::stride]
    ny, nx = top.shape
    X, Y = g.mesh()
    X, Y = X[::stride, ::stride], Y[::stride, ::stride]
    finite = (np.isfinite(top) & np.isfinite(base)).ravel()
    thick = np.where(finite.reshape(ny, nx), top - base, 0.0)
    present = (thick > min_thickness).ravel()
    n = ny * nx
    ve = vertical_exaggeration
    vt = np.column_stack([X.ravel(), Y.ravel(), np.where(np.isfinite(top), top, 0.0).ravel() * ve])
    vb = np.column_stack([X.ravel(), Y.ravel(), np.where(np.isfinite(base), base, 0.0).ravel() * ve])
    verts = np.vstack([vt, vb])
    bidx = np.where(present, np.arange(n) + n, np.arange(n))  # pinched nodes share the top vertex
    idx = np.arange(n).reshape(ny, nx)
    a, b, c, d = idx[:-1, :-1], idx[:-1, 1:], idx[1:, 1:], idx[1:, :-1]
    tri = np.concatenate([np.stack([a, b, c], -1).reshape(-1, 3), np.stack([a, c, d], -1).reshape(-1, 3)])
    keep = finite[tri].all(axis=1) & present[tri].any(axis=1)
    tri = tri[keep]
    faces = [tri, bidx[tri][:, ::-1]]
    # outward side walls along the (counter-clockwise) grid boundary
    border = list(idx[0, :]) + list(idx[1:, -1]) + list(idx[-1, -2::-1]) + list(idx[-2::-1, 0])
    walls = []
    for p_, q_ in zip(border[:-1], border[1:]):
        if not (finite[p_] and finite[q_]) or not (present[p_] or present[q_]):
            continue
        walls.append([p_, bidx[q_], q_])
        walls.append([p_, bidx[p_], bidx[q_]])
    if walls:
        faces.append(np.array(walls))
    f = np.concatenate(faces) if faces else np.zeros((0, 3), dtype=int)
    f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
    colour = model.column.kb[unit].colour if unit in model.column.kb else "#BDBDBD"
    return TriMesh(unit, verts, f, colour, {"unit": unit}).compact()


def model_solids(model, vertical_exaggeration: float = 1.0, stride: int = 1) -> list[TriMesh]:
    out = []
    for u in model.units:
        m = unit_solid(model, u, vertical_exaggeration, stride=stride)
        if not m.empty:
            out.append(m)
    return out


def borehole_segments(project, interpretation, vertical_exaggeration: float = 1.0):
    """[(unit, (x, y, z_top), (x, y, z_base), location_id)] for line exports (DXF, VTK)."""
    out = []
    for lid, li in interpretation.locations.items():
        l = project.locations.get(lid)
        if l is None or l.ground_level is None:
            continue
        for r in li.runs:
            out.append(
                (
                    r.unit,
                    (l.x, l.y, (l.ground_level - r.top) * vertical_exaggeration),
                    (l.x, l.y, (l.ground_level - r.base) * vertical_exaggeration),
                    lid,
                )
            )
    return out


def voxelise(model, dz: float = 1.0, z_min: float | None = None, z_max: float | None = None):
    """Block model: (x_edges, y_edges, z_edges, unit_id (nx, ny, nz) with -1 = air)."""
    g = model.grid
    zmax = z_max if z_max is not None else float(np.nanmax(model.surfaces["ground"].values))
    zmin = z_min if z_min is not None else float(model.bottom)
    ze = np.arange(zmin, zmax + dz, dz)
    zc = 0.5 * (ze[:-1] + ze[1:])
    ids = np.full((g.nx, g.ny, len(zc)), -1, dtype=np.int16)
    for k, u in enumerate(model.units):
        t = model.tops[u].T[:, :, None]
        b = model.bases[u].T[:, :, None]
        m = (zc[None, None, :] <= t) & (zc[None, None, :] > b) & (ids < 0)
        ids[m] = k
    xe = g.x0 - g.dx / 2 + g.dx * np.arange(g.nx + 1)
    ye = g.y0 - g.dy / 2 + g.dy * np.arange(g.ny + 1)
    return xe, ye, ze, ids
