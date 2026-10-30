"""Geological cross sections from the stratigraphic model, logs and groundwater data.

A section is computed (:func:`compute_section`) as data — stations, unit polygons in
(chainage, elevation), confidence along each contact, projected boreholes, fault intersections,
groundwater lines — and then rendered to SVG / PDF (:func:`render_section`) or written to a
GeoPackage as 2D section-space features. The same object feeds the fence diagram and the HDD
long section, so every output shows the same interpretation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..core.geometry import polyline_intersections, polyline_length, project_points_to_polyline, resample_polyline
from .colours import CLASS_BY_CODE, CLASS_HEX, darker, unit_colour
from .drawing import Drawing, Style


@dataclass
class SectionLog:
    location_id: str
    chainage: float
    offset: float
    ground_level: float
    final_depth: float
    kind: str
    runs: list[tuple[str, float, float, float]]  # (unit, top depth, base depth, probability)
    water: list[tuple[str, float]] = field(default_factory=list)  # (label, elevation)


@dataclass
class SectionData:
    name: str
    line: np.ndarray
    chainage: np.ndarray
    xy: np.ndarray
    ground: np.ndarray
    tops: dict[str, np.ndarray]
    bases: dict[str, np.ndarray]
    contact_std: dict[str, np.ndarray]
    contact_class: dict[str, np.ndarray]
    units: list[str]
    logs: list[SectionLog] = field(default_factory=list)
    faults: list[tuple[str, float]] = field(default_factory=list)
    water: dict[str, np.ndarray] = field(default_factory=dict)
    crossings: list[tuple[str, float, float | None]] = field(default_factory=list)  # (name, chainage, clearance)
    alignment_profile: tuple[np.ndarray, np.ndarray] | None = None  # (chainage, elevation)
    bottom: float = -50.0
    crs: str | None = None

    @property
    def length(self) -> float:
        return float(self.chainage[-1]) if len(self.chainage) else 0.0

    def unit_polygons(self, min_thickness: float = 0.01) -> list[tuple[str, list[tuple[float, float]]]]:
        """Polygons (chainage, z) for each contiguous run of non-zero thickness per unit."""
        out = []
        for u in self.units:
            t, b = self.tops[u], self.bases[u]
            ok = np.isfinite(t) & np.isfinite(b) & (t - b > min_thickness)
            k = 0
            n = len(ok)
            while k < n:
                if not ok[k]:
                    k += 1
                    continue
                s = k
                while k < n and ok[k]:
                    k += 1
                e = k
                ch = self.chainage[s:e]
                top_pts = list(zip(ch, t[s:e]))
                bot_pts = list(zip(ch[::-1], b[s:e][::-1]))
                if e - s == 1:
                    c = float(ch[0])
                    w = 0.5 * (self.chainage[min(e, n - 1)] - self.chainage[max(s - 1, 0)]) or 0.5
                    top_pts = [(c - w / 2, t[s]), (c + w / 2, t[s])]
                    bot_pts = [(c + w / 2, b[s]), (c - w / 2, b[s])]
                out.append((u, [(float(a), float(z)) for a, z in top_pts + bot_pts]))
        return out


def compute_section(
    model,
    line,
    spacing: float | None = None,
    name: str = "Section",
    project=None,
    interpretation=None,
    corridor: float = 50.0,
    faults=None,
    water_surfaces: dict | None = None,
    alignment_profile=None,
    crossings=None,
) -> SectionData:
    line = np.asarray(line, float)[:, :2]
    L = polyline_length(line)
    spacing = spacing or max(min(L / 400.0, model.grid.dx / 2.0), 0.5)
    ch, xy = resample_polyline(line, spacing)
    faults = list(faults) if faults is not None else list(getattr(model, "faults", []))
    fault_hits = []
    for f in faults:
        for c, _, _ in polyline_intersections(line, f.segments()):
            fault_hits.append((f.name, c))
    if fault_hits:  # stations just either side of each fault so offsets are drawn as steps
        extra = np.concatenate([[c - 1e-3, c + 1e-3] for _, c in fault_hits])
        ch = np.unique(np.clip(np.concatenate([ch, extra]), 0.0, L))
        from ..core.geometry import point_at_chainage

        xy = point_at_chainage(line, ch)
    segs = np.array([sg for f in faults for sg in f.segments()], float) if faults else np.zeros((0, 2, 2))

    def samp(v, method="bilinear"):
        if len(segs) and method == "bilinear":
            return model.grid.sample_fault_aware(v, xy, segs)
        return model.grid.sample(v, xy, method=method)

    ground = samp(model.surfaces["ground"].values)
    tops = {u: samp(model.tops[u]) for u in model.units}
    bases = {u: samp(model.bases[u]) for u in model.units}
    stds, classes = {}, {}
    for u in model.units:
        sname = model._top_surface_name(u)
        if sname and sname in model.surfaces:
            sg = model.surfaces[sname]
            stds[u] = samp(sg.std)
            classes[u] = samp(sg.classes.astype(float), "nearest") if sg.classes is not None else np.full(len(xy), 3.0)
    sec = SectionData(
        name,
        line,
        ch,
        xy,
        ground,
        tops,
        bases,
        stds,
        classes,
        list(model.units),
        bottom=model.bottom,
        crs=model.grid.crs,
    )
    sec.faults = fault_hits
    if project is not None and interpretation is not None:
        locs = project.located()
        if locs:
            P = np.array([[l.x, l.y] for l in locs])
            c, off, _ = project_points_to_polyline(P, line)
            for l, cc, oo in zip(locs, c, off):
                if abs(oo) > corridor or cc < -1e-6 or cc > L + 1e-6:
                    continue
                runs = [(r.unit, r.top, r.base, r.p) for r in interpretation.runs(l.id)]
                if not runs:
                    continue
                w = []
                for ws in project.water_strikes:
                    if ws.location_id == l.id:
                        w.append(("strike", l.ground_level - ws.depth))
                        if ws.readings:
                            w.append(("rest", l.ground_level - ws.rest_depth))
                sec.logs.append(
                    SectionLog(
                        l.id, float(cc), float(oo), l.ground_level, l.final_depth or runs[-1][2], l.kind.value, runs, w
                    )
                )
            sec.logs.sort(key=lambda s: s.chainage)
    if water_surfaces:
        for k, (grid, vals) in water_surfaces.items():
            sec.water[k] = grid.sample(vals, xy)
    if alignment_profile is not None:
        sec.alignment_profile = (np.asarray(alignment_profile[0], float), np.asarray(alignment_profile[1], float))
    for name_c, cline, clear in crossings or []:
        for c, _, _ in polyline_intersections(line, np.stack([np.asarray(cline)[:-1], np.asarray(cline)[1:]], axis=1)):
            sec.crossings.append((name_c, c, clear))
    sec.bottom = section_bottom(sec, model)
    for u in sec.units:  # the lowest unit is open-ended: close it at the section bottom
        sec.bases[u] = np.where(np.isfinite(sec.bases[u]), np.maximum(sec.bases[u], sec.bottom), sec.bases[u])
    return sec


def section_bottom(sec: SectionData, model, margin_fraction: float = 0.15, min_margin: float = 5.0) -> float:
    """Default section base: below the deepest of (exploratory hole ends, modelled contacts other than
    the open base of the lowest unit, design alignment), plus a margin. Avoids drawing tens of metres
    of unconstrained lowest unit."""
    cands = []
    lowest = model.units[-1] if model.units else None
    for u in sec.units:
        if u == lowest:
            t = sec.tops[u]
            if np.isfinite(t).any():
                cands.append(float(np.nanmin(t)))
            continue
        b = sec.bases[u]
        if np.isfinite(b).any():
            cands.append(float(np.nanmin(b)))
    for lg in sec.logs:
        cands.append(lg.ground_level - lg.final_depth)
    if sec.alignment_profile is not None:
        cands.append(float(np.nanmin(sec.alignment_profile[1])))
    if not cands:
        return model.bottom
    deepest = min(cands)
    top = float(np.nanmax(sec.ground)) if np.isfinite(sec.ground).any() else deepest + 10
    return deepest - max(min_margin, margin_fraction * (top - deepest))


# ============================================================================ rendering
def _nice_step(span: float, target: int = 8) -> float:
    raw = span / max(target, 1)
    mag = 10 ** math.floor(math.log10(max(raw, 1e-9)))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def render_section(
    sec: SectionData,
    kb=None,
    page: str = "A3",
    h_scale: float | None = None,
    vertical_exaggeration: float = 5.0,
    show_confidence: bool = True,
    show_logs: bool = True,
    title: str | None = None,
    z_range: tuple[float, float] | None = None,
    subtitle: str = "",
    bottom_reserve_mm: float = 0.0,
) -> Drawing:
    d = Drawing.page(page, landscape=True, title=title or sec.name)
    W, H = d.width, d.height
    mL, mR, mT, mB = 22.0, 62.0, 22.0, 32.0 + bottom_reserve_mm
    plot_w = W - mL - mR
    zmax = float(np.nanmax(sec.ground)) + 2.0
    zmin = sec.bottom
    if sec.alignment_profile is not None:
        zmin = min(zmin, float(np.nanmin(sec.alignment_profile[1])) - 5.0)
    if z_range:
        zmin, zmax = z_range
    L = max(sec.length, 1e-6)
    if h_scale is None:
        sx = plot_w / L  # mm per metre horizontally
        sz = sx * vertical_exaggeration
        avail_h = H - mT - mB
        if (zmax - zmin) * sz > avail_h:
            sz = avail_h / (zmax - zmin)
            vertical_exaggeration = sz / sx
    else:
        sx = 1000.0 / h_scale
        sz = sx * vertical_exaggeration
    plot_h = (zmax - zmin) * sz
    X = lambda c: mL + c * sx  # noqa: E731
    Z = lambda z: mT + (zmax - z) * sz  # noqa: E731
    # unit polygons
    for u, poly in sec.unit_polygons():
        colr = unit_colour(kb, u)
        d.polygon(
            [(X(c), Z(min(max(z, zmin), zmax))) for c, z in poly],
            Style(fill=colr, stroke=darker(colr, 0.7), width=0.15),
            layer="units",
        )
    # contacts by confidence class
    if show_confidence:
        for u in sec.units:
            if u not in sec.contact_class:
                continue
            t = sec.tops[u]
            thick = (t - sec.bases[u]) > 0.01
            cls = sec.contact_class[u]
            seg: list[tuple[float, float]] = []
            cur = None
            for k in range(len(t)):
                if not (thick[k] and np.isfinite(t[k])):
                    if len(seg) > 1:
                        _contact(d, seg, cur)
                    seg, cur = [], None
                    continue
                c = int(cls[k]) if np.isfinite(cls[k]) else 3
                if cur is not None and c != cur:
                    seg.append((X(sec.chainage[k]), Z(t[k])))
                    _contact(d, seg, cur)
                    seg = []
                cur = c
                seg.append((X(sec.chainage[k]), Z(t[k])))
            if len(seg) > 1:
                _contact(d, seg, cur)
    # ground line
    d.polyline(
        [(X(c), Z(z)) for c, z in zip(sec.chainage, sec.ground) if np.isfinite(z)],
        Style(stroke="#3E2723", width=0.5),
        layer="ground",
    )
    # groundwater
    wcols = ["#0277BD", "#00838F", "#283593"]
    for k, (name, zz) in enumerate(sec.water.items()):
        pts = [(X(c), Z(z)) for c, z in zip(sec.chainage, zz) if np.isfinite(z)]
        d.polyline(pts, Style(stroke=wcols[k % 3], width=0.4, dash=(2.0, 1.0)), layer="water")
        if pts:
            i = min(len(pts) - 1, max(0, int(len(pts) * (0.08 + 0.1 * k))))
            d.text(
                pts[i][0],
                pts[i][1] - 1.0,
                f"Groundwater ({name})",
                Style(fill=wcols[k % 3], font_size=2.0, bold=True),
                layer="water",
            )
    # faults
    for name, c in sec.faults:
        d.line(X(c), Z(zmax), X(c), Z(zmin), Style(stroke="#B71C1C", width=0.6, dash=(3.0, 1.5)), layer="faults")
        d.text(X(c) + 0.8, Z(zmax) + 3.5, name, Style(fill="#B71C1C", font_size=2.2, bold=True))
    # alignment (HDD)
    if sec.alignment_profile is not None:
        ac, az = sec.alignment_profile
        d.polyline([(X(c), Z(z)) for c, z in zip(ac, az)], Style(stroke="#D50000", width=0.8), layer="alignment")
    for name, c, clear in sec.crossings:
        d.line(X(c), Z(zmax), X(c), Z(zmax) + 4, Style(stroke="#000000", width=0.4))
        label = name + (f" (clearance >= {float(clear):g} m)" if clear not in (None, "") else "")
        d.text(X(c), Z(zmax) - 1, label, Style(font_size=2.0, anchor="middle"))
    # logs
    if show_logs:
        last_label_x = -1e9
        stagger = 0
        for lg in sec.logs:
            x = X(lg.chainage)
            stagger = stagger + 1 if x - last_label_x < 6.0 else 0
            last_label_x = x
            for u, a, b, _p in lg.runs:
                z1, z2 = lg.ground_level - a, lg.ground_level - b
                colr = unit_colour(kb, u)
                d.rect(
                    x - 1.1,
                    Z(z1),
                    2.2,
                    max((z1 - z2) * sz, 0.05),
                    Style(fill=colr, stroke="#000000", width=0.12),
                    layer="logs",
                )
            zb = lg.ground_level - lg.final_depth
            d.line(x - 1.6, Z(zb), x + 1.6, Z(zb), Style(stroke="#000000", width=0.3), layer="logs")
            lab = f"{lg.location_id}" + (f" ({lg.offset:+.0f} m)" if abs(lg.offset) >= 1 else "")
            ly = Z(lg.ground_level) - 2.0 - 7.0 * (stagger % 3)
            if stagger % 3:
                d.line(x, Z(lg.ground_level) - 1.0, x, ly + 1.0, Style(stroke="#777777", width=0.1), layer="logs")
            d.text(x, ly, lab, Style(font_size=2.0, anchor="start"), rotate=-60, layer="logs")
            for kind, zw in lg.water:
                yy = Z(zw)
                d.polygon(
                    [(x + 1.8, yy), (x + 3.2, yy - 1.2), (x + 0.4, yy - 1.2)],
                    Style(fill="#0277BD" if kind == "rest" else "none", stroke="#0277BD", width=0.2),
                    layer="logs",
                )
    # frame, axes
    d.rect(mL, mT, plot_w if h_scale is None else L * sx, plot_h, Style(fill=None, stroke="#000000", width=0.35))
    zs = _nice_step(zmax - zmin, 10)
    zt = math.ceil(zmin / zs) * zs
    while zt <= zmax:
        d.line(mL - 1.5, Z(zt), mL, Z(zt), Style(stroke="#000000", width=0.2))
        d.text(mL - 2, Z(zt), f"{zt:g}", Style(font_size=2.2, anchor="end", baseline="middle"))
        zt += zs
    d.text(mL - 13, mT + plot_h / 2, "Elevation (m OD)", Style(font_size=2.5, anchor="middle"), rotate=-90)
    cs = _nice_step(L, 10)
    c = 0.0
    while c <= L + 1e-6:
        d.line(X(c), mT + plot_h, X(c), mT + plot_h + 1.5, Style(stroke="#000000", width=0.2))
        d.text(X(c), mT + plot_h + 4.5, f"{c:g}", Style(font_size=2.2, anchor="middle"))
        c += cs
    d.text(mL + (L * sx) / 2, mT + plot_h + 9, "Chainage (m)", Style(font_size=2.5, anchor="middle"))
    d.text(mL, 10, title or sec.name, Style(font_size=4.2, bold=True))
    info = f"Vertical exaggeration x{vertical_exaggeration:.1f}" + (f"  |  {subtitle}" if subtitle else "")
    d.text(mL, 16, info, Style(font_size=2.4))
    # legend
    lx, ly = W - mR + 6, mT
    d.text(lx, ly, "Units", Style(font_size=2.8, bold=True))
    ly += 3
    present = [
        u for u in sec.units if np.nanmax(np.nan_to_num(sec.tops[u] - sec.bases[u], nan=0.0), initial=0.0) > 0.01
    ]
    for u in present:
        colr = unit_colour(kb, u)
        d.rect(lx, ly, 5, 3.5, Style(fill=colr, stroke="#000000", width=0.15))
        name = kb[u].name if kb is not None and u in kb else u
        d.text(lx + 6.5, ly + 1.75, f"{name} ({u})", Style(font_size=2.1, baseline="middle"))
        ly += 5
    if show_confidence:
        ly += 3
        d.text(lx, ly, "Contact confidence", Style(font_size=2.8, bold=True))
        ly += 3
        for code, cls in enumerate(CLASS_BY_CODE):
            st = _contact_style(code)
            d.line(lx, ly + 1.75, lx + 5, ly + 1.75, st)
            d.text(lx + 6.5, ly + 1.75, cls.value, Style(font_size=2.1, baseline="middle"))
            ly += 4.5
    d.text(
        W - 5,
        H - 5,
        "AGSGeoModel - interpretation, not observation, between exploratory holes",
        Style(font_size=1.8, anchor="end", fill="#555555"),
    )
    d.meta = {
        "mL": mL,
        "mT": mT,
        "sx": sx,
        "sz": sz,
        "zmax": zmax,
        "zmin": zmin,
        "plot_h": plot_h,
        "plot_w": L * sx,
        "legend_x": W - mR + 6,
        "legend_y": ly,
    }
    return d


def _contact_style(code: int) -> Style:
    cls = CLASS_BY_CODE[min(max(code, 0), 3)]
    dash = {0: None, 1: None, 2: (2.0, 1.0), 3: (0.6, 0.9)}[min(max(code, 0), 3)]
    return Style(stroke=CLASS_HEX[cls], width=0.55 if code <= 1 else 0.45, dash=dash)


def _contact(d: Drawing, seg, code) -> None:
    d.polyline(seg, _contact_style(code if code is not None else 3), layer="contacts")


def section_to_features(sec: SectionData) -> list[dict]:
    """Section-space features (x = chainage, y = elevation) for a GeoPackage layer with no CRS."""
    feats = []
    for u, poly in sec.unit_polygons():
        feats.append(
            {
                "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in poly] + [list(poly[0])]]},
                "properties": {"section": sec.name, "kind": "unit", "unit": u},
            }
        )
    feats.append(
        {
            "geometry": {
                "type": "LineString",
                "coordinates": [[float(c), float(z)] for c, z in zip(sec.chainage, sec.ground) if np.isfinite(z)],
            },
            "properties": {"section": sec.name, "kind": "ground", "unit": ""},
        }
    )
    for name, c in sec.faults:
        feats.append(
            {
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[c, float(np.nanmax(sec.ground)) + 2], [c, sec.bottom]],
                },
                "properties": {"section": sec.name, "kind": "fault", "unit": name},
            }
        )
    return feats
