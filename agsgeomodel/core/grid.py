"""Regular 2D model grid, raster sampling and raster-to-polygon conversion.

Convention: values arrays have shape ``(ny, nx)``; row ``j`` increases **northwards** (y0 is the
southernmost row of cell centres). Exporters flip rows when writing north-up rasters.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

from .geometry import points_in_ring, ring_signed_area


@dataclass(frozen=True)
class Grid2D:
    x0: float  # x of the centre of column 0
    y0: float  # y of the centre of row 0 (south)
    dx: float
    dy: float
    nx: int
    ny: int
    crs: str | None = None

    # ------------------------------------------------------------------ construction
    @classmethod
    def from_extent(
        cls,
        xmin: float,
        ymin: float,
        xmax: float,
        ymax: float,
        cell: float,
        crs: str | None = None,
        max_cells: int = 4_000_000,
    ) -> Grid2D:
        if cell <= 0:
            raise ValueError("cell size must be positive")
        nx = max(2, int(math.ceil((xmax - xmin) / cell)))
        ny = max(2, int(math.ceil((ymax - ymin) / cell)))
        if nx * ny > max_cells:
            scale = math.sqrt(nx * ny / max_cells)
            return cls.from_extent(xmin, ymin, xmax, ymax, cell * scale, crs, max_cells)
        return cls(xmin + cell / 2, ymin + cell / 2, cell, cell, nx, ny, crs)

    @classmethod
    def auto(
        cls,
        points,
        buffer: float,
        target_cells_across: int = 150,
        min_cell: float = 1.0,
        max_cell: float = 100.0,
        crs: str | None = None,
    ) -> Grid2D:
        p = np.asarray(points, dtype=float)[:, :2]
        xmin, ymin = p.min(axis=0) - buffer
        xmax, ymax = p.max(axis=0) + buffer
        span = max(xmax - xmin, ymax - ymin)
        cell = float(np.clip(span / target_cells_across, min_cell, max_cell))
        # round the cell to a "nice" number for reproducible, human-readable grids
        mag = 10 ** math.floor(math.log10(cell))
        for m in (1, 2, 2.5, 5, 10):
            if cell <= m * mag:
                cell = m * mag
                break
        return cls.from_extent(float(xmin), float(ymin), float(xmax), float(ymax), cell, crs)

    # ------------------------------------------------------------------ geometry
    @property
    def shape(self) -> tuple[int, int]:
        return (self.ny, self.nx)

    @property
    def size(self) -> int:
        return self.nx * self.ny

    def xs(self) -> np.ndarray:
        return self.x0 + self.dx * np.arange(self.nx)

    def ys(self) -> np.ndarray:
        return self.y0 + self.dy * np.arange(self.ny)

    def mesh(self) -> tuple[np.ndarray, np.ndarray]:
        return np.meshgrid(self.xs(), self.ys())

    def nodes(self) -> np.ndarray:
        X, Y = self.mesh()
        return np.column_stack([X.ravel(), Y.ravel()])

    def extent(self) -> tuple[float, float, float, float]:
        """Cell-edge extent (xmin, ymin, xmax, ymax)."""
        return (
            self.x0 - self.dx / 2,
            self.y0 - self.dy / 2,
            self.x0 + (self.nx - 0.5) * self.dx,
            self.y0 + (self.ny - 0.5) * self.dy,
        )

    def contains(self, pts) -> np.ndarray:
        p = np.asarray(pts, dtype=float).reshape(-1, 2)
        xmin, ymin, xmax, ymax = self.extent()
        return (p[:, 0] >= xmin) & (p[:, 0] <= xmax) & (p[:, 1] >= ymin) & (p[:, 1] <= ymax)

    # ------------------------------------------------------------------ sampling
    def sample(self, values: np.ndarray, pts, method: str = "bilinear") -> np.ndarray:
        """Sample a grid at arbitrary points (NaN outside the grid extent).

        Bilinear interpolation falls back to the nearest valid node when one of the four
        surrounding nodes is NaN, so surfaces that are absent locally do not bleed NaNs.
        """
        v = np.asarray(values, dtype=float)
        if v.shape != self.shape:
            raise ValueError(f"values shape {v.shape} != grid shape {self.shape}")
        p = np.asarray(pts, dtype=float).reshape(-1, 2)
        fi = (p[:, 0] - self.x0) / self.dx
        fj = (p[:, 1] - self.y0) / self.dy
        out = np.full(len(p), np.nan)
        inside = (fi >= -0.5) & (fi <= self.nx - 0.5) & (fj >= -0.5) & (fj <= self.ny - 0.5)
        if not inside.any():
            return out
        fi_c = np.clip(fi[inside], 0, self.nx - 1)
        fj_c = np.clip(fj[inside], 0, self.ny - 1)
        if method == "nearest":
            out[inside] = v[np.rint(fj_c).astype(int), np.rint(fi_c).astype(int)]
            return out
        i0 = np.clip(np.floor(fi_c).astype(int), 0, max(self.nx - 2, 0))
        j0 = np.clip(np.floor(fj_c).astype(int), 0, max(self.ny - 2, 0))
        tx = fi_c - i0
        ty = fj_c - j0
        i1 = np.minimum(i0 + 1, self.nx - 1)
        j1 = np.minimum(j0 + 1, self.ny - 1)
        v00, v10, v01, v11 = v[j0, i0], v[j0, i1], v[j1, i0], v[j1, i1]
        res = v00 * (1 - tx) * (1 - ty) + v10 * tx * (1 - ty) + v01 * (1 - tx) * ty + v11 * tx * ty
        bad = ~np.isfinite(res)
        if bad.any():
            near = v[np.rint(fj_c[bad]).astype(int), np.rint(fi_c[bad]).astype(int)]
            res[bad] = near
        out[inside] = res
        return out

    def sample_fault_aware(self, values: np.ndarray, pts, fault_segments) -> np.ndarray:
        """Bilinear sampling that ignores grid nodes on the far side of a fault trace.

        Without this, sampling a faulted surface between two grid nodes that straddle a fault
        blends the up- and downthrown blocks and draws the fault as a ramp instead of a step.
        """
        from .geometry import crossing_mask

        segs = np.asarray(fault_segments, float).reshape(-1, 2, 2)
        if len(segs) == 0:
            return self.sample(values, pts)
        v = np.asarray(values, dtype=float)
        p = np.asarray(pts, dtype=float).reshape(-1, 2)
        fi = (p[:, 0] - self.x0) / self.dx
        fj = (p[:, 1] - self.y0) / self.dy
        out = np.full(len(p), np.nan)
        inside = (fi >= -0.5) & (fi <= self.nx - 0.5) & (fj >= -0.5) & (fj <= self.ny - 0.5)
        if not inside.any():
            return out
        q = p[inside]
        fi_c = np.clip(fi[inside], 0, self.nx - 1)
        fj_c = np.clip(fj[inside], 0, self.ny - 1)
        i0 = np.clip(np.floor(fi_c).astype(int), 0, max(self.nx - 2, 0))
        j0 = np.clip(np.floor(fj_c).astype(int), 0, max(self.ny - 2, 0))
        tx, ty = fi_c - i0, fj_c - j0
        corners = [
            (j0, i0, (1 - tx) * (1 - ty)),
            (j0, i0 + 1, tx * (1 - ty)),
            (j0 + 1, i0, (1 - tx) * ty),
            (j0 + 1, i0 + 1, tx * ty),
        ]
        num = np.zeros(len(q))
        den = np.zeros(len(q))
        best_w = np.full(len(q), -1.0)
        best_v = np.full(len(q), np.nan)
        for jj, ii, w in corners:
            jj = np.minimum(jj, self.ny - 1)
            ii = np.minimum(ii, self.nx - 1)
            node = np.column_stack([self.x0 + ii * self.dx, self.y0 + jj * self.dy])
            blocked = crossing_mask(q, node, segs).any(axis=1)
            val = v[jj, ii]
            ok = ~blocked & np.isfinite(val)
            ww = np.where(ok, np.maximum(w, 1e-12), 0.0)
            num += ww * np.where(ok, val, 0.0)
            den += ww
            better = ok & (ww > best_w)
            best_w = np.where(better, ww, best_w)
            best_v = np.where(better, val, best_v)
        res = np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)
        miss = ~np.isfinite(res)
        if miss.any():  # every corner across a fault (point on the trace): fall back to plain sampling
            res[miss] = self.sample(v, q[miss])
        out[inside] = res
        return out

    def cell_index(self, pts) -> tuple[np.ndarray, np.ndarray]:
        p = np.asarray(pts, dtype=float).reshape(-1, 2)
        i = np.clip(np.rint((p[:, 0] - self.x0) / self.dx).astype(int), 0, self.nx - 1)
        j = np.clip(np.rint((p[:, 1] - self.y0) / self.dy).astype(int), 0, self.ny - 1)
        return j, i

    def gradient(self, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(d/dx, d/dy) using central differences (NaN-safe at edges)."""
        gy, gx = np.gradient(np.asarray(values, dtype=float), self.dy, self.dx)
        return gx, gy

    # ------------------------------------------------------------------ serialisation
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Grid2D:
        return cls(**{k: d[k] for k in ("x0", "y0", "dx", "dy", "nx", "ny")}, crs=d.get("crs"))


# ============================================================================ polygonize
def polygonize(
    labels: np.ndarray, grid: Grid2D, nodata: int = -1, simplify: bool = True
) -> list[tuple[int, list[np.ndarray]]]:
    """Convert a classified grid into polygons (one or more per class).

    Boundaries follow cell edges, so the result is exact and topologically clean: polygons of
    different classes share edges, holes are assigned to their enclosing exterior ring, and
    diagonally touching cells are kept as separate polygons (4-connectivity).

    Returns ``[(class_value, [exterior_ring, hole_ring, ...]), ...]`` in world coordinates with
    closed rings (first vertex repeated).
    """
    lab = np.asarray(labels)
    ny, nx = lab.shape
    out: list[tuple[int, list[np.ndarray]]] = []
    for cls_val in [int(c) for c in np.unique(lab) if int(c) != nodata]:
        mask = lab == cls_val
        rings = _trace_rings(mask, simplify)
        exteriors, holes = [], []
        for ring in rings:
            world = np.column_stack([grid.x0 + (ring[:, 0] - 0.5) * grid.dx, grid.y0 + (ring[:, 1] - 0.5) * grid.dy])
            (exteriors if ring_signed_area(world) > 0 else holes).append(world)
        polys: list[list[np.ndarray]] = [[e] for e in exteriors]
        areas = [abs(ring_signed_area(e)) for e in exteriors]
        for h in holes:
            # probe just to the right of the hole's first edge: class cells are on the left of
            # every traced edge, so the right-hand side is inside the hole itself.
            mid = 0.5 * (h[0] + h[1])
            d = h[1] - h[0]
            probe = mid + 1e-6 * np.array([d[1], -d[0]])
            best, best_area = None, np.inf
            for k, e in enumerate(exteriors):
                if points_in_ring(probe, e)[0] and areas[k] < best_area:
                    best, best_area = k, areas[k]
            if best is not None:
                polys[best].append(h)
        out.extend((cls_val, p) for p in polys)
    return out


def _trace_rings(mask: np.ndarray, simplify: bool) -> list[np.ndarray]:
    ny, nx = mask.shape
    pad = np.zeros((ny + 2, nx + 2), dtype=bool)
    pad[1:-1, 1:-1] = mask
    edges: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def add(a, b):
        edges.setdefault(a, []).append(b)

    js, is_ = np.nonzero(mask)
    for j, i in zip(js.tolist(), is_.tolist()):
        pj, pi = j + 1, i + 1
        if not pad[pj - 1, pi]:  # south neighbour empty -> bottom edge, eastwards
            add((i, j), (i + 1, j))
        if not pad[pj, pi + 1]:  # east -> right edge, northwards
            add((i + 1, j), (i + 1, j + 1))
        if not pad[pj + 1, pi]:  # north -> top edge, westwards
            add((i + 1, j + 1), (i, j + 1))
        if not pad[pj, pi - 1]:  # west -> left edge, southwards
            add((i, j + 1), (i, j))
    rings: list[np.ndarray] = []
    while edges:
        start = next(iter(edges))
        ring = [start]
        prev = start
        cur = edges[start].pop()
        if not edges[start]:
            del edges[start]
        while cur != start:
            ring.append(cur)
            outs = edges.get(cur)
            if not outs:
                break
            if len(outs) == 1:
                nxt = outs.pop()
            else:
                # ambiguous (checkerboard) vertex: take the left-most turn -> 4-connectivity
                din = (cur[0] - prev[0], cur[1] - prev[1])

                def turn(o, cur=cur, din=din):
                    dout = (o[0] - cur[0], o[1] - cur[1])
                    return din[0] * dout[1] - din[1] * dout[0]

                k = max(range(len(outs)), key=lambda q: turn(outs[q]))
                nxt = outs.pop(k)
            if not outs:
                del edges[cur]
            prev, cur = cur, nxt
        for part in _split_self_touching(ring):
            arr = np.array(part, dtype=float)
            if simplify and len(arr) > 4:
                arr = _drop_collinear(arr)
            rings.append(arr)
    return rings


def _split_self_touching(ring: list[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """Split a traced ring wherever it revisits a vertex (OGC rings must not self-touch).

    ``ring`` is open (first vertex not repeated). Returns closed rings.
    """
    out: list[list[tuple[int, int]]] = []
    stack: list[tuple[int, int]] = []
    pos: dict[tuple[int, int], int] = {}
    for p in ring:
        if p in pos:
            i = pos[p]
            loop = stack[i:] + [p]
            if len(loop) >= 4:
                out.append(loop)
            for q in stack[i + 1 :]:
                pos.pop(q, None)
            stack = stack[: i + 1]
        else:
            pos[p] = len(stack)
            stack.append(p)
    if len(stack) >= 3:
        out.append(stack + [stack[0]])
    return out


def _drop_collinear(ring: np.ndarray) -> np.ndarray:
    pts = ring[:-1]
    n = len(pts)
    keep = []
    for k in range(n):
        a, b, c = pts[k - 1], pts[k], pts[(k + 1) % n]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cross) > 1e-12:
            keep.append(b)
    if len(keep) < 3:
        return ring
    arr = np.array(keep)
    return np.vstack([arr, arr[:1]])
