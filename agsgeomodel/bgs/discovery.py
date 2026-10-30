"""BGS borehole discovery and the intelligent gap-filling score.

Each BGS record near the site receives a confidence (0-100 %) from six explainable factors, combined
as a weighted geometric mean (weights in ``WEIGHTS``) so that one very poor factor (e.g. a position
known only to +/-100 m) cannot be compensated by the others:

=================  ==========================================================================
Factor             Score
=================  ==========================================================================
proximity          exp(-d_site / L): distance to the project area / alignment
gap value          how much the record fills a gap: distance to the nearest project hole relative
                   to the typical hole spacing (records next to existing holes add little)
geology            mapped bedrock (and superficial) unit at the record agrees with the units
                   expected there (model rockhead unit or mapped units at nearby project holes)
elevation          ground level available and consistent with the DTM; else assigned from DTM
age                year of the record (older logs use superseded terminology)
data quality       AGS data > scanned log; positional precision; depth reached vs depth needed
=================  ==========================================================================

Records above the qualification threshold (default 60 %) are proposed as **secondary control**:
when merged they keep ``source = BGS_*`` and ``quality = confidence`` so their contacts receive a
larger observation sigma (and Inferred, never Observed, classes).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from ..core.geometry import distance_to_segments, points_in_polygon, polyline_to_segments
from ..models.enums import DataSource

WEIGHTS = {"proximity": 1.0, "gap_value": 0.8, "geology": 1.2, "elevation": 0.6, "age": 0.6, "quality": 1.2}


@dataclass
class BGSCandidate:
    bgs_id: str
    reference: str
    name: str
    collection: str
    x: float
    y: float
    length_m: float | None
    year: str | None
    precision: str | None
    has_ags: bool
    url: str | None
    ground_level: float | None = None
    distance_m: float = 0.0
    confidence: float = 0.0
    breakdown: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    selected: bool = False
    qualified: bool = False

    @property
    def label(self) -> str:
        return f"BGS_{self.reference or self.bgs_id}"


def precision_m(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:METRES|M\b|METRE)", text.upper())
    return float(m.group(1)) if m else None


def candidates_from_records(records: list[dict]) -> list[BGSCandidate]:
    out = []
    for r in records:
        if r.get("collection") == "agsboreholeindex":
            out.append(
                BGSCandidate(
                    str(r.get("bgs_loca_id") or r.get("id")),
                    str(r.get("loca_id") or ""),
                    str(r.get("proj_name") or ""),
                    "agsboreholeindex",
                    float(r.get("x") or np.nan),
                    float(r.get("y") or np.nan),
                    r.get("loca_fdep"),
                    None,
                    "AGS (surveyed)",
                    True,
                    r.get("ags_log_url"),
                )
            )
        else:
            out.append(
                BGSCandidate(
                    str(int(r["bgs_id"])) if r.get("bgs_id") is not None else str(r.get("id")),
                    str(r.get("reference") or ""),
                    str(r.get("name") or ""),
                    "onshoreboreholeindex",
                    float(r.get("easting") or np.nan),
                    float(r.get("northing") or np.nan),
                    r.get("length"),
                    r.get("year_known"),
                    r.get("precision"),
                    bool(r.get("ags_log_url")),
                    r.get("scan_url"),
                )
            )
    return [c for c in out if np.isfinite(c.x) and np.isfinite(c.y)]


def rank_candidates(
    cands: list[BGSCandidate],
    project,
    buffer_polygon=None,
    alignment=None,
    model=None,
    map_units=None,
    dtm=None,
    required_depth: float | None = None,
    length_scale: float = 250.0,
    threshold: float = 60.0,
    kb=None,
) -> list[BGSCandidate]:
    pts = project.xy()
    if len(pts) >= 2:
        d = np.hypot(pts[:, None, 0] - pts[None, :, 0], pts[:, None, 1] - pts[None, :, 1])
        np.fill_diagonal(d, np.inf)
        spacing = float(np.median(d.min(axis=1)))
    else:
        spacing = 100.0
    seg = polyline_to_segments(np.asarray(alignment, float)) if alignment is not None and len(alignment) > 1 else None
    for c in cands:
        b: dict[str, float] = {}
        why: list[str] = []
        p = np.array([[c.x, c.y]])
        # proximity
        if seg is not None:
            d_site = float(distance_to_segments(p, seg)[0])
        elif len(pts):
            inside = buffer_polygon is not None and bool(points_in_polygon(p, [buffer_polygon])[0])
            d_site = 0.0 if inside else float(np.min(np.hypot(pts[:, 0] - c.x, pts[:, 1] - c.y)))
        else:
            d_site = 0.0
        c.distance_m = float(np.min(np.hypot(pts[:, 0] - c.x, pts[:, 1] - c.y))) if len(pts) else 0.0
        b["proximity"] = math.exp(-d_site / length_scale)
        # gap value
        b["gap_value"] = float(np.clip(c.distance_m / max(spacing, 1.0), 0.15, 1.0))
        if c.distance_m < 0.25 * spacing:
            why.append(f"within {c.distance_m:.0f} m of a project hole: little gap value")
        # geology
        g = 0.7
        if map_units:
            at = [m.code for m in map_units if points_in_polygon(p, m.rings)[0]]
            if model is not None and at:
                col = [u for u, t, bb in model.column_at(c.x, c.y)]
                rock = next((u for u in col if model.column.kb[u].unit_class == "bedrock"), None)
                resolved = set().union(*[model.column.resolve(a) for a in at]) if at else set()
                if rock and rock in resolved:
                    g = 1.0
                elif rock and resolved:
                    g = 0.45
                    why.append(
                        f"mapped unit {at} differs from modelled rockhead unit {rock} (possible map "
                        "generalisation, fault or misidentification)"
                    )
            elif at:
                g = 0.8
        b["geology"] = g
        # elevation
        if dtm is not None:
            dg, dv = dtm
            z = float(dg.sample(dv, p)[0])
            if c.ground_level is not None and np.isfinite(z):
                diff = abs(c.ground_level - z)
                b["elevation"] = float(np.clip(1 - diff / 3.0, 0.3, 1.0))
                if diff > 1.0:
                    why.append(f"recorded ground level differs from DTM by {diff:.1f} m")
            elif np.isfinite(z):
                c.ground_level = z
                b["elevation"] = 0.8
                why.append("ground level assigned from DTM")
            else:
                b["elevation"] = 0.5
        else:
            b["elevation"] = 0.9 if c.ground_level is not None else 0.5
        # age
        yr = None
        if c.year and re.search(r"\d{4}", str(c.year)):
            yr = int(re.search(r"\d{4}", str(c.year)).group(0))
        if yr is None and c.collection == "agsboreholeindex":
            yr = 1992  # AGS format data: 1992 or later
        b["age"] = 1.0 if (yr and yr >= 1990) else 0.8 if (yr and yr >= 1960) else 0.6 if (yr and yr >= 1900) else 0.45
        if yr and yr < 1960:
            why.append(f"{yr} log: check terminology (e.g. 'Woolwich and Reading Beds', 'Thanet Sand')")
        # quality
        q = 1.0 if c.has_ags else 0.65
        pm = precision_m(c.precision)
        if pm is not None:
            q *= 1.0 if pm <= 1 else 0.85 if pm <= 10 else 0.55 if pm <= 100 else 0.3
        elif not c.has_ags:
            q *= 0.7
        if required_depth and c.length_m:
            q *= float(np.clip(c.length_m / required_depth, 0.3, 1.0))
            if c.length_m < required_depth:
                why.append(f"depth {c.length_m:.1f} m < required {required_depth:.1f} m")
        b["quality"] = q
        logs = sum(WEIGHTS[k] * math.log(max(v, 1e-3)) for k, v in b.items())
        c.confidence = round(100.0 * math.exp(logs / sum(WEIGHTS.values())), 1)
        c.breakdown = {k: round(v, 3) for k, v in b.items()}
        c.reasons = why
        c.qualified = c.confidence >= threshold
    return sorted(cands, key=lambda c: -c.confidence)


def merge_bgs_ags(
    project,
    ags_files: list[tuple[str, bytes]],
    candidates: dict[str, BGSCandidate] | None = None,
    id_prefix: str = "BGS:",
) -> list[str]:
    """Merge downloaded BGS AGS data into a project as secondary data (quality = confidence)."""
    from ..importers.ags import ags_to_project, read_ags

    merged = []
    for name, data in ags_files:
        ags = read_ags(data, name=name)
        res = ags_to_project(ags, target_crs=project.crs)
        sub = res.project
        for loc in sub.locations.values():
            loc.source = DataSource.BGS_AGS
            cand = (candidates or {}).get(loc.id)
            loc.quality = (cand.confidence / 100.0) if cand else 0.6
        merged += project.merge(sub, id_prefix=id_prefix, source=DataSource.BGS_AGS)
    return merged
