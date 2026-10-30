"""Fence diagrams: networks of sections between boreholes, in 2D (oblique projection) and 3D (mesh).

The network is either user-defined (a list of location id pairs or polylines) or automatic:
the edges of the Delaunay triangulation of the selected holes, pruned of long edges (more than
``max_edge_factor`` times the median nearest-neighbour distance) so the fence follows the data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..modelling.interpolators import delaunay
from .colours import darker, unit_colour
from .drawing import Drawing, Style
from .sections import SectionData, compute_section


@dataclass
class FencePanel:
    a: str
    b: str
    section: SectionData


def auto_network(ids: list[str], xy: np.ndarray, max_edge_factor: float = 2.5) -> list[tuple[int, int]]:
    xy = np.asarray(xy, float)
    if len(xy) < 2:
        return []
    if len(xy) == 2:
        return [(0, 1)]
    tri = delaunay(xy)
    edges = set()
    for t in tri:
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            edges.add((min(a, b), max(a, b)))
    d = np.hypot(xy[:, None, 0] - xy[None, :, 0], xy[:, None, 1] - xy[None, :, 1])
    np.fill_diagonal(d, np.inf)
    lim = max_edge_factor * float(np.median(d.min(axis=1)))
    return sorted((a, b) for a, b in edges if d[a, b] <= lim)


def build_fence(
    model,
    project,
    interpretation,
    location_ids: list[str] | None = None,
    pairs: list[tuple[str, str]] | None = None,
    max_edge_factor: float = 2.5,
    faults=None,
) -> list[FencePanel]:
    locs = {l.id: l for l in project.located()}
    ids = [i for i in (location_ids or list(locs)) if i in locs]
    if pairs is None:
        xy = np.array([[locs[i].x, locs[i].y] for i in ids])
        pairs = [(ids[a], ids[b]) for a, b in auto_network(ids, xy, max_edge_factor)]
    panels = []
    for a, b in pairs:
        line = np.array([[locs[a].x, locs[a].y], [locs[b].x, locs[b].y]])
        sec = compute_section(
            model, line, name=f"{a}-{b}", project=project, interpretation=interpretation, corridor=1.0, faults=faults
        )
        panels.append(FencePanel(a, b, sec))
    return panels


def render_fence_2d(
    panels: list[FencePanel],
    kb=None,
    page: str = "A3",
    azimuth_deg: float = 210.0,
    elevation_deg: float = 25.0,
    vertical_exaggeration: float = 5.0,
    title: str = "Fence diagram",
) -> Drawing:
    """Oblique (axonometric) projection of the fence; panels drawn far-to-near (painter's algorithm)."""
    d = Drawing.page(page, landscape=True, title=title)
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    ca, sa, se, ce = math.cos(az), math.sin(az), math.sin(el), math.cos(el)

    def proj(x, y, z):
        u = x * ca - y * sa
        v = (x * sa + y * ca) * se + z * vertical_exaggeration * ce
        depth = (x * sa + y * ca) * ce
        return u, v, depth

    allpts = []
    for p in panels:
        s = p.section
        for k in range(len(s.chainage)):
            allpts.append((s.xy[k, 0], s.xy[k, 1], np.nanmax([s.ground[k]])))
            allpts.append((s.xy[k, 0], s.xy[k, 1], s.bottom))
    if not allpts:
        return d
    A = np.array(allpts, float)
    c0 = A[:, :2].mean(axis=0)
    U, V, _ = proj(A[:, 0] - c0[0], A[:, 1] - c0[1], A[:, 2])
    m = 20.0
    sx = (d.width - 2 * m - 50) / max(np.ptp(U), 1e-6)
    sy = (d.height - 2 * m - 10) / max(np.ptp(V), 1e-6)
    s_ = min(sx, sy)
    u0, v0 = U.min(), V.max()

    def P(x, y, z):  # noqa: N802 - page-coordinate transform, named like the maths
        q = proj(x - c0[0], y - c0[1], z)
        return ((q[0] - u0) * s_ + m, (v0 - q[1]) * s_ + m + 10)

    order = sorted(panels, key=lambda p: -proj(*(p.section.xy.mean(axis=0) - c0), 0.0)[2])
    for p in order:
        s = p.section
        for u, poly in s.unit_polygons():
            pts = []
            for c, z in poly:
                x, y = np.interp(c, s.chainage, s.xy[:, 0]), np.interp(c, s.chainage, s.xy[:, 1])
                pts.append(P(x, y, max(z, s.bottom)))
            colr = unit_colour(kb, u)
            d.polygon(pts, Style(fill=colr, stroke=darker(colr, 0.7), width=0.12, opacity=0.95))
        d.polyline(
            [P(s.xy[k, 0], s.xy[k, 1], s.ground[k]) for k in range(len(s.chainage)) if np.isfinite(s.ground[k])],
            Style(stroke="#3E2723", width=0.35),
        )
        for lg in s.logs:
            if lg.location_id in (p.a, p.b):
                x, y = s.xy[0] if lg.location_id == p.a else s.xy[-1]
                top = P(x, y, lg.ground_level)
                bot = P(x, y, lg.ground_level - lg.final_depth)
                d.line(top[0], top[1], bot[0], bot[1], Style(stroke="#000000", width=0.5))
                d.text(top[0], top[1] - 1.5, lg.location_id, Style(font_size=2.0, anchor="middle"))
    d.text(m, 12, title, Style(font_size=4.0, bold=True))
    d.text(
        m,
        17,
        f"Oblique view from azimuth {azimuth_deg:.0f} deg, vertical exaggeration x{vertical_exaggeration:g}",
        Style(font_size=2.3),
    )
    return d


def fence_mesh(panels: list[FencePanel], vertical_exaggeration: float = 1.0):
    """Triangle mesh of fence panels for 3D export: ``(vertices (N,3), faces (M,3), unit per face)``."""
    V, F, U = [], [], []
    for p in panels:
        s = p.section
        for u in s.units:
            t, b = s.tops[u], s.bases[u]
            for k in range(len(s.chainage) - 1):
                if not (
                    np.isfinite(t[k]) and np.isfinite(t[k + 1]) and t[k] - b[k] > 0.01 and t[k + 1] - b[k + 1] > 0.01
                ):
                    continue
                x0, y0 = s.xy[k]
                x1, y1 = s.xy[k + 1]
                i = len(V)
                V.extend(
                    [
                        (x0, y0, t[k] * vertical_exaggeration),
                        (x1, y1, t[k + 1] * vertical_exaggeration),
                        (x1, y1, b[k + 1] * vertical_exaggeration),
                        (x0, y0, b[k] * vertical_exaggeration),
                    ]
                )
                F.extend([(i, i + 1, i + 2), (i, i + 2, i + 3)])
                U.extend([u, u])
    return np.array(V, float).reshape(-1, 3), np.array(F, int).reshape(-1, 3), U
