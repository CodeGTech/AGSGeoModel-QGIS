"""Geological correlation engine — five modes, from naive to geologically constrained.

==========================  ===================================================================
Mode                        What it does
==========================  ===================================================================
nearest_neighbour           links equal elevations between neighbouring holes (reference only:
                            *not* geologically constrained, shown for comparison)
formation                   links the same interpreted unit (GeoReasoner) between holes
hybrid                      formation links where the unit is confident; elsewhere elevation +
                            description similarity
rules_constrained           formation links validated against superposition (no crossing links),
                            apparent dip limits and known faults (links across a fault are kept
                            but flagged "offset expected")
ai_assisted                 order-preserving alignment (dynamic programming) of the two logs on
                            description similarity + elevation; compared with formation links and
                            disagreements flagged for review
==========================  ===================================================================

Links connect the *base* of a unit in hole A with the base of the same (or aligned) unit in hole B.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..core.geometry import crossing_mask
from ..geology.validation import neighbour_pairs

MODES = ("nearest_neighbour", "formation", "hybrid", "rules_constrained", "ai_assisted")


@dataclass
class CorrelationLink:
    a: str
    b: str
    unit_a: str
    unit_b: str
    z_a: float
    z_b: float
    xy_a: tuple[float, float]
    xy_b: tuple[float, float]
    mode: str
    confidence: float
    flags: list[str] = field(default_factory=list)

    @property
    def dip_deg(self) -> float:
        d = math.hypot(self.xy_b[0] - self.xy_a[0], self.xy_b[1] - self.xy_a[1])
        return math.degrees(math.atan2(abs(self.z_b - self.z_a), max(d, 1e-9)))

    def feature(self) -> dict:
        return {
            "geometry": {"type": "LineString", "coordinates": [[*self.xy_a, self.z_a], [*self.xy_b, self.z_b]]},
            "properties": {
                "a": self.a,
                "b": self.b,
                "unit_a": self.unit_a,
                "unit_b": self.unit_b,
                "mode": self.mode,
                "confidence": round(self.confidence, 3),
                "dip_deg": round(self.dip_deg, 2),
                "flags": "; ".join(self.flags),
            },
        }


def _runs(interp, lid):
    return interp.locations[lid].runs if lid in interp.locations else []


def correlate(
    project,
    interpretation,
    mode: str = "rules_constrained",
    pairs: list[tuple[str, str]] | None = None,
    faults=None,
    similarity=None,
    confident_p: float = 0.8,
) -> list[CorrelationLink]:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    col = interpretation.column
    locs = [l for l in project.located() if l.id in interpretation.locations]
    by_id = {l.id: l for l in locs}
    if pairs is None:
        pairs = [(locs[i].id, locs[j].id) for i, j, _ in neighbour_pairs(locs)]
    segs = np.array([s for f in (faults or []) for s in f.segments()]) if faults else np.zeros((0, 2, 2))
    links: list[CorrelationLink] = []
    for a, b in pairs:
        la, lb = by_id.get(a), by_id.get(b)
        if la is None or lb is None:
            continue
        ra, rb = _runs(interpretation, a), _runs(interpretation, b)
        across = bool(len(segs)) and bool(crossing_mask(np.array([[la.x, la.y]]), np.array([[lb.x, lb.y]]), segs).any())
        new: list[CorrelationLink] = []
        if mode == "nearest_neighbour":
            for r in ra:
                z = la.ground_level - r.base
                ub = next((x.unit for x in rb if lb.ground_level - x.top >= z > lb.ground_level - x.base), None)
                if ub:
                    new.append(
                        CorrelationLink(
                            a,
                            b,
                            r.unit,
                            ub,
                            z,
                            z,
                            (la.x, la.y),
                            (lb.x, lb.y),
                            mode,
                            0.3,
                            ["equal-elevation link: not geologically constrained"],
                        )
                    )
        elif mode in ("formation", "rules_constrained", "hybrid"):
            mb = {x.unit: x for x in rb}
            for r in ra:
                if r.unit in mb and r is not ra[-1] and mb[r.unit] is not rb[-1]:
                    x = mb[r.unit]
                    conf = min(r.p, x.p)
                    if mode == "hybrid" and conf < confident_p and similarity is not None:
                        continue
                    new.append(
                        CorrelationLink(
                            a,
                            b,
                            r.unit,
                            x.unit,
                            la.ground_level - r.base,
                            lb.ground_level - x.base,
                            (la.x, la.y),
                            (lb.x, lb.y),
                            mode,
                            conf,
                        )
                    )
            if mode == "hybrid" and similarity is not None:
                new += _aligned_links(a, b, la, lb, ra, rb, similarity, "hybrid", only_uncertain=confident_p)
        else:  # ai_assisted
            if similarity is None:
                from ..ai.similarity import TfidfSimilarity

                texts = [iv.description for iv in project.intervals]
                similarity = TfidfSimilarity().fit(texts)
            new = _aligned_links(a, b, la, lb, ra, rb, similarity, mode, descriptions=(project, a, b))
            formation = {(x.unit_a) for x in correlate(project, interpretation, "formation", [(a, b)])}
            for lk in new:
                if lk.unit_a != lk.unit_b:
                    lk.flags.append(f"alignment links {lk.unit_a} to {lk.unit_b}: interpretation disagreement")
                elif lk.unit_a not in formation:
                    lk.flags.append("not supported by formation correlation")
        if mode == "rules_constrained":
            # superposition: links must not cross (order preserved) and must respect dip limits
            new.sort(key=lambda lk: col.pos.get(lk.unit_a, 0))
            for k in range(1, len(new)):
                if new[k].z_a > new[k - 1].z_a + 1e-6 or new[k].z_b > new[k - 1].z_b + 1e-6:
                    new[k].flags.append("crossing correlation (superposition violated)")
                    new[k].confidence *= 0.3
            for lk in new:
                limit = (
                    col.max_apparent_dip_deg
                    if col.kb[lk.unit_a].unit_class == "bedrock"
                    else col.rockhead_max_apparent_dip_deg
                )
                if lk.dip_deg > limit:
                    lk.flags.append(f"apparent dip {lk.dip_deg:.1f} deg > {limit:.1f} deg")
                    lk.confidence *= 0.6
        if across:
            for lk in new:
                lk.flags.append("across a mapped fault: offset expected")
        links += new
    return links


def _aligned_links(a, b, la, lb, ra, rb, sim, mode, only_uncertain: float | None = None, descriptions=None):
    """Order-preserving alignment of two logs (Needleman-Wunsch on similarity + elevation)."""
    if not ra or not rb:
        return []
    project = descriptions[0] if descriptions else None

    def text(lid, r):
        if project is None:
            return r.unit
        ivs = [
            iv.description for iv in project.intervals if iv.location_id == lid and iv.top < r.base and iv.base > r.top
        ]
        return " ".join(ivs) or r.unit

    ta = [text(a, r) for r in ra]
    tb = [text(b, r) for r in rb]
    S = sim.matrix(ta, tb)
    za = np.array([la.ground_level - 0.5 * (r.top + r.base) for r in ra])
    zb = np.array([lb.ground_level - 0.5 * (r.top + r.base) for r in rb])
    E = np.exp(-np.abs(za[:, None] - zb[None, :]) / 10.0)
    score = 0.7 * S + 0.3 * E
    n, m = score.shape
    gap = -0.15
    D = np.zeros((n + 1, m + 1))
    D[1:, 0] = gap * np.arange(1, n + 1)
    D[0, 1:] = gap * np.arange(1, m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            D[i, j] = max(D[i - 1, j - 1] + score[i - 1, j - 1] - 0.4, D[i - 1, j] + gap, D[i, j - 1] + gap)
    i, j = n, m
    pairs = []
    while i > 0 and j > 0:
        if np.isclose(D[i, j], D[i - 1, j - 1] + score[i - 1, j - 1] - 0.4):
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif np.isclose(D[i, j], D[i - 1, j] + gap):
            i -= 1
        else:
            j -= 1
    out = []
    for i, j in reversed(pairs):
        r, x = ra[i], rb[j]
        if only_uncertain is not None and min(r.p, x.p) >= only_uncertain:
            continue
        if r is ra[-1] or x is rb[-1]:
            continue
        out.append(
            CorrelationLink(
                a,
                b,
                r.unit,
                x.unit,
                la.ground_level - r.base,
                lb.ground_level - x.base,
                (la.x, la.y),
                (lb.x, lb.y),
                mode,
                float(score[i, j]),
            )
        )
    return out
