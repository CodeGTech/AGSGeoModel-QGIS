"""Small, dependency-free (NumPy only) computational-geometry toolkit.

Everything here is vectorised and works in projected (metric) coordinates. Functions are used by
the fault barrier logic, map constraints, sections, HDD alignments and uncertainty classes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_EPS = 1e-12


def as_xy(a) -> np.ndarray:
    arr = np.asarray(a, dtype=float)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.shape[-1] < 2:
        raise ValueError("expected (N, 2) coordinates")
    return arr[..., :2]


# --------------------------------------------------------------------------- polylines
def polyline_chainage(line) -> np.ndarray:
    """Cumulative chainage of each vertex of a polyline (first vertex = 0)."""
    xy = as_xy(line)
    if len(xy) == 0:
        return np.zeros(0)
    seg = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
    return np.concatenate([[0.0], np.cumsum(seg)])


def polyline_length(line) -> float:
    ch = polyline_chainage(line)
    return float(ch[-1]) if len(ch) else 0.0


def point_at_chainage(line, chainage) -> np.ndarray:
    """Interpolate (x, y) positions at the given chainages along a polyline."""
    xy = as_xy(line)
    ch = polyline_chainage(xy)
    c = np.clip(np.asarray(chainage, dtype=float), 0.0, ch[-1])
    x = np.interp(c, ch, xy[:, 0])
    y = np.interp(c, ch, xy[:, 1])
    return np.column_stack([x, y])


def resample_polyline(line, spacing: float, include_vertices: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Stations along a polyline every ``spacing`` metres.

    Returns ``(chainage, xy)``. Original vertices are retained (when ``include_vertices``) so that
    bends in an alignment are represented exactly.
    """
    if spacing <= 0:
        raise ValueError("spacing must be positive")
    xy = as_xy(line)
    ch = polyline_chainage(xy)
    total = ch[-1]
    stations = np.arange(0.0, total, spacing)
    if include_vertices:
        stations = np.concatenate([stations, ch])
    stations = np.unique(np.round(np.concatenate([stations, [total]]), 9))
    return stations, point_at_chainage(xy, stations)


def segment_directions(line) -> np.ndarray:
    xy = as_xy(line)
    d = np.diff(xy, axis=0)
    n = np.hypot(d[:, 0], d[:, 1])
    n[n == 0] = 1.0
    return d / n[:, None]


def project_points_to_polyline(points, line) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Project points onto a polyline.

    Returns ``(chainage, offset, segment_index)`` where ``offset`` is signed: positive to the
    left of the direction of travel, negative to the right.
    """
    p = as_xy(points)
    xy = as_xy(line)
    if len(xy) < 2:
        raise ValueError("polyline needs at least two vertices")
    a = xy[:-1]
    b = xy[1:]
    ab = b - a
    ab2 = np.maximum((ab**2).sum(axis=1), _EPS)
    ap = p[:, None, :] - a[None, :, :]  # (N, S, 2)
    t = np.clip((ap * ab[None]).sum(axis=2) / ab2[None], 0.0, 1.0)
    proj = a[None] + t[..., None] * ab[None]
    d2 = ((p[:, None, :] - proj) ** 2).sum(axis=2)
    k = np.argmin(d2, axis=1)
    rows = np.arange(len(p))
    ch_vertex = polyline_chainage(xy)
    seg_len = np.sqrt(ab2)
    chain = ch_vertex[k] + t[rows, k] * seg_len[k]
    cross = ab[k, 0] * (p[:, 1] - a[k, 1]) - ab[k, 1] * (p[:, 0] - a[k, 0])
    dist = np.sqrt(d2[rows, k])
    offset = np.where(cross >= 0, dist, -dist)
    return chain, offset, k


# --------------------------------------------------------------------------- segments
def _orient(ax, ay, bx, by, cx, cy):
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def crosses_any(a0, a1, segments, chunk_pairs: int = 2_000_000) -> np.ndarray:
    """For each segment ``a0[i] -> a1[i]`` report whether it properly crosses any of ``segments``.

    ``segments`` has shape (F, 2, 2). Touching at an end point or collinear overlap is *not*
    counted as a crossing (a borehole located exactly on a fault trace is ambiguous and is left
    visible from both sides).
    """
    a0 = as_xy(a0)
    a1 = as_xy(a1)
    segs = np.asarray(segments, dtype=float).reshape(-1, 2, 2)
    n = len(a0)
    out = np.zeros(n, dtype=bool)
    if n == 0 or len(segs) == 0:
        return out
    q0 = segs[:, 0, :]
    q1 = segs[:, 1, :]
    step = max(1, chunk_pairs // max(1, len(segs)))
    for s in range(0, n, step):
        e = min(n, s + step)
        px0 = a0[s:e, 0][:, None]
        py0 = a0[s:e, 1][:, None]
        px1 = a1[s:e, 0][:, None]
        py1 = a1[s:e, 1][:, None]
        o1 = _orient(px0, py0, px1, py1, q0[None, :, 0], q0[None, :, 1])
        o2 = _orient(px0, py0, px1, py1, q1[None, :, 0], q1[None, :, 1])
        o3 = _orient(q0[None, :, 0], q0[None, :, 1], q1[None, :, 0], q1[None, :, 1], px0, py0)
        o4 = _orient(q0[None, :, 0], q0[None, :, 1], q1[None, :, 0], q1[None, :, 1], px1, py1)
        hit = (o1 * o2 < -_EPS) & (o3 * o4 < -_EPS)
        out[s:e] = hit.any(axis=1)
    return out


def crossing_mask(a0, a1, segments, chunk_pairs: int = 2_000_000) -> np.ndarray:
    """Like :func:`crosses_any` but returns the full (N, F) boolean matrix of crossings."""
    a0 = as_xy(a0)
    a1 = as_xy(a1)
    segs = np.asarray(segments, dtype=float).reshape(-1, 2, 2)
    n, f = len(a0), len(segs)
    out = np.zeros((n, f), dtype=bool)
    if n == 0 or f == 0:
        return out
    q0 = segs[:, 0, :]
    q1 = segs[:, 1, :]
    step = max(1, chunk_pairs // max(1, f))
    for s in range(0, n, step):
        e = min(n, s + step)
        px0 = a0[s:e, 0][:, None]
        py0 = a0[s:e, 1][:, None]
        px1 = a1[s:e, 0][:, None]
        py1 = a1[s:e, 1][:, None]
        o1 = _orient(px0, py0, px1, py1, q0[None, :, 0], q0[None, :, 1])
        o2 = _orient(px0, py0, px1, py1, q1[None, :, 0], q1[None, :, 1])
        o3 = _orient(q0[None, :, 0], q0[None, :, 1], q1[None, :, 0], q1[None, :, 1], px0, py0)
        o4 = _orient(q0[None, :, 0], q0[None, :, 1], q1[None, :, 0], q1[None, :, 1], px1, py1)
        out[s:e] = (o1 * o2 < -_EPS) & (o3 * o4 < -_EPS)
    return out


def polyline_to_segments(line) -> np.ndarray:
    xy = as_xy(line)
    return np.stack([xy[:-1], xy[1:]], axis=1)


def side_of_polyline(points, line) -> np.ndarray:
    """+1 if a point lies to the left of the polyline (direction of digitising), -1 if right."""
    _, off, _ = project_points_to_polyline(points, line)
    return np.where(off >= 0, 1, -1)


def polyline_intersections(line, segments) -> list[tuple[float, int, np.ndarray]]:
    """Intersections of a polyline with a set of segments.

    Returns a list of ``(chainage, segment_index, xy)`` sorted by chainage.
    """
    xy = as_xy(line)
    segs = np.asarray(segments, dtype=float).reshape(-1, 2, 2)
    ch = polyline_chainage(xy)
    hits: list[tuple[float, int, np.ndarray]] = []
    for i in range(len(xy) - 1):
        p, r = xy[i], xy[i + 1] - xy[i]
        for j, (q0, q1) in enumerate(segs):
            s = q1 - q0
            denom = r[0] * s[1] - r[1] * s[0]
            if abs(denom) < _EPS:
                continue
            qp = q0 - p
            t = (qp[0] * s[1] - qp[1] * s[0]) / denom
            u = (qp[0] * r[1] - qp[1] * r[0]) / denom
            if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
                seg_len = float(np.hypot(*r))
                hits.append((float(ch[i] + t * seg_len), j, p + t * r))
    hits.sort(key=lambda h: h[0])
    return hits


def distance_to_segments(points, segments) -> np.ndarray:
    """Minimum Euclidean distance from each point to a set of segments (F, 2, 2)."""
    p = as_xy(points)
    segs = np.asarray(segments, dtype=float).reshape(-1, 2, 2)
    if len(segs) == 0:
        return np.full(len(p), np.inf)
    best = np.full(len(p), np.inf)
    for a, b in segs:
        ab = b - a
        L2 = max(float((ab**2).sum()), _EPS)
        t = np.clip(((p - a) @ ab) / L2, 0.0, 1.0)
        proj = a + t[:, None] * ab
        d = np.hypot(p[:, 0] - proj[:, 0], p[:, 1] - proj[:, 1])
        best = np.minimum(best, d)
    return best


# --------------------------------------------------------------------------- polygons
def ring_signed_area(ring) -> float:
    r = as_xy(ring)
    x, y = r[:, 0], r[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def points_in_ring(points, ring) -> np.ndarray:
    """Even-odd ray casting test, vectorised over points."""
    p = as_xy(points)
    r = as_xy(ring)
    if len(r) and np.allclose(r[0], r[-1]):
        r = r[:-1]
    inside = np.zeros(len(p), dtype=bool)
    x, y = p[:, 0], p[:, 1]
    n = len(r)
    for i in range(n):
        x1, y1 = r[i]
        x2, y2 = r[(i + 1) % n]
        cond = (y1 > y) != (y2 > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = (x2 - x1) * (y - y1) / (y2 - y1) + x1
        inside ^= cond & (x < xint)
    return inside


def points_in_polygon(points, polygon) -> np.ndarray:
    """``polygon`` is ``[exterior_ring, hole_ring, ...]``."""
    if len(polygon) == 0:
        return np.zeros(len(as_xy(points)), dtype=bool)
    inside = points_in_ring(points, polygon[0])
    for hole in polygon[1:]:
        inside &= ~points_in_ring(points, hole)
    return inside


def polygon_boundary_segments(polygon) -> np.ndarray:
    segs = []
    for ring in polygon:
        r = as_xy(ring)
        if not np.allclose(r[0], r[-1]):
            r = np.vstack([r, r[:1]])
        segs.append(polyline_to_segments(r))
    return np.concatenate(segs) if segs else np.zeros((0, 2, 2))


def points_inside_with_margin(points, polygon, margin: float) -> np.ndarray:
    """Points inside ``polygon`` and at least ``margin`` from its boundary (map tolerance)."""
    inside = points_in_polygon(points, polygon)
    if margin <= 0 or not inside.any():
        return inside
    d = distance_to_segments(as_xy(points)[inside], polygon_boundary_segments(polygon))
    out = inside.copy()
    out[np.where(inside)[0][d < margin]] = False
    return out


def convex_hull(points) -> np.ndarray:
    """Andrew's monotone chain. Returns hull vertices counter-clockwise (not closed)."""
    p = np.unique(as_xy(points), axis=0)
    if len(p) <= 2:
        return p
    p = p[np.lexsort((p[:, 1], p[:, 0]))]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[np.ndarray] = []
    for pt in p:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], pt) <= 0:
            lower.pop()
        lower.append(pt)
    upper: list[np.ndarray] = []
    for pt in p[::-1]:
        while len(upper) >= 2 and cross(upper[-2], upper[-1], pt) <= 0:
            upper.pop()
        upper.append(pt)
    return np.array(lower[:-1] + upper[:-1])


def buffer_bbox(points, distance: float) -> tuple[float, float, float, float]:
    p = as_xy(points)
    return (
        float(p[:, 0].min() - distance),
        float(p[:, 1].min() - distance),
        float(p[:, 0].max() + distance),
        float(p[:, 1].max() + distance),
    )


def circle_ring(cx: float, cy: float, radius: float, n: int = 64) -> np.ndarray:
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    ring = np.column_stack([cx + radius * np.cos(a), cy + radius * np.sin(a)])
    return np.vstack([ring, ring[:1]])


@dataclass(frozen=True)
class BBox:
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    @property
    def width(self) -> float:
        return self.xmax - self.xmin

    @property
    def height(self) -> float:
        return self.ymax - self.ymin

    def buffered(self, d: float) -> BBox:
        return BBox(self.xmin - d, self.ymin - d, self.xmax + d, self.ymax + d)

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.xmin, self.ymin, self.xmax, self.ymax)

    @classmethod
    def of(cls, points) -> BBox:
        return cls(*buffer_bbox(points, 0.0))
