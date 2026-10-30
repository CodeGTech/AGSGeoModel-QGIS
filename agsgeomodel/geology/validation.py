"""Geological validation rules (per borehole and between boreholes).

Every rule returns :class:`~agsgeomodel.core.issues.Issue` objects with candidate geological
explanations instead of silently "fixing" data. Rule catalogue (see docs/geological_rules.md):

GEO-INV-01  logged formations violate superposition (stated codes)
GEO-INV-02  description evidence implies a repeated / inverted sequence
GEO-CON-01  order constraint overrides strong description evidence
GEO-STA-01  logged formation conflicts with the consistent interpretation
GEO-OMI-01  usually-present unit missing between two units (possible omission)
GEO-MIS-01  unlogged interval: inferred sequence (accept / reject)
GEO-THK-01  unit thicker than its known maximum
GEO-LOW-01  interpretation probability below the review threshold
GEO-DIP-01  steep apparent dip on a conformable bedrock surface between neighbours
GEO-DIP-02  steep rockhead between neighbours
GEO-ELV-01  same-elevation conflict between neighbouring holes
GEO-LOO-01  contact elevation anomalous relative to neighbours (leave-one-out)
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from ..core.config import ReasonerConfig
from ..core.issues import Issue
from ..models.entities import Interval, Location
from ..models.enums import ContactType, Severity
from .contacts import ObservationSet, UnitRun, bedrock_stack
from .knowledge.kb import StratColumn
from .sequence import SequenceResult


def _explanations(column: StratColumn, kind: str = "default") -> list[str]:
    ex = list(column.repetition_explanations.get(kind, column.repetition_explanations.get("default", [])))
    if kind == "default" and any(column.kb[u].domain == "glacial" for u in column.units):
        ex += column.repetition_explanations.get("glacial", [])
    return ex or ["logging or classification error", "reworked material", "faulting"]


def validate_sequence(
    res: SequenceResult, intervals: list[Interval], runs: list[UnitRun], column: StratColumn, cfg: ReasonerConfig
) -> list[Issue]:
    issues: list[Issue] = []
    lid = res.location_id
    kb = column.kb
    ivs = sorted(intervals, key=lambda i: (i.top, i.base))
    # ---- GEO-INV-01: stated (logged) codes out of order
    stated_seq: list[tuple[float, str, str]] = []
    for sl in res.slices:
        if sl.synthetic:
            continue
        units = {u for u, f, _ in sl.evidence.stated_units if f in ("GEOL_BGS", "GEOL_GEOL", "GEOL_FORM")}
        if len(units) == 1:
            stated_seq.append((sl.top, next(iter(units)), ivs[sl.source_index].description[:60]))
    flagged_lower: set[int] = set()
    for i in range(len(stated_seq)):
        for j in range(i + 1, len(stated_seq)):
            (zi, ui, _), (zj, uj, _) = stated_seq[i], stated_seq[j]
            if j in flagged_lower:
                continue
            if column.is_above(uj, ui):
                flagged_lower.add(j)
                issues.append(
                    Issue(
                        "GEO-INV-01",
                        Severity.ERROR,
                        f"logged {kb[ui].name} at {zi:.2f} m lies above logged "
                        f"{kb[uj].name} at {zj:.2f} m, which should be younger (above)",
                        category="geology",
                        location_ids=[lid],
                        depth=zj,
                        explanations=_explanations(column),
                        suggestion="check the logged geology codes; if genuine, enable repetition for this hole",
                        details={"upper": ui, "lower": uj},
                    )
                )
                break
    # ---- GEO-INV-02: strong evidence for an inverted sequence
    strong = [
        (t, u)
        for t, u in enumerate(res.unconstrained)
        if res.unconstrained_prob[t] >= 0.8 and not res.slices[t].synthetic
    ]
    reported_upper: set[int] = set()
    for a in range(len(strong)):
        ta, ua = strong[a]
        younger_below = [(tb, ub) for tb, ub in strong[a + 1 :] if column.is_above(ub, ua)]
        if not younger_below or ta in reported_upper:
            continue
        reported_upper.add(ta)
        tb, ub = younger_below[0]
        expl = _explanations(column)
        if ua == "CK":
            expl = [
                "reworked or soliflucted chalk (Head) or chalk fill in Made Ground",
                "putty chalk / chalk clasts logged as Chalk",
            ] + expl
        lower_units = list(dict.fromkeys(u for _, u in younger_below))
        issues.append(
            Issue(
                "GEO-INV-02",
                Severity.WARNING,
                f"descriptions indicate {kb[ua].name} ({res.slices[ta].top:.2f}-{res.slices[ta].base:.2f} m) above "
                f"younger {', '.join(kb[u].name for u in lower_units)} (from {res.slices[tb].top:.2f} m): "
                "an inverted or repeated sequence",
                category="geology",
                location_ids=[lid],
                depth=res.slices[ta].top,
                explanations=expl,
                suggestion="review the log; the model uses the most likely consistent "
                "interpretation until a reviewer accepts a structural explanation",
                details={"upper": ua, "lower": lower_units},
            )
        )
    # ---- GEO-CON-01 / GEO-STA-01 / GEO-LOW-01
    for t, sl in enumerate(res.slices):
        if sl.synthetic:
            continue
        iv = ivs[sl.source_index]
        u_path, u_ev = res.path[t], res.unconstrained[t]
        margin = sl.evidence.score(u_ev) - sl.evidence.score(u_path)
        if u_path != u_ev and margin > 3.0 and sl.evidence.informative:
            issues.append(
                Issue(
                    "GEO-CON-01",
                    Severity.WARNING,
                    f"{iv.top:.2f}-{iv.base:.2f} m: description favours "
                    f"{kb[u_ev].name} (margin {margin:.1f} log-units) but stratigraphic order implies "
                    f"{kb[u_path].name}",
                    category="geology",
                    location_ids=[lid],
                    depth=iv.top,
                    suggestion="check the description, the logged depth, or allow structural repetition",
                )
            )
        stated = {u for u, f, _ in sl.evidence.stated_units}
        if stated and u_path not in stated:
            issues.append(
                Issue(
                    "GEO-STA-01",
                    Severity.WARNING,
                    f"{iv.top:.2f}-{iv.base:.2f} m: logged as "
                    f"{'/'.join(sorted(stated))} but the consistent interpretation is {u_path}",
                    category="geology",
                    location_ids=[lid],
                    depth=iv.top,
                    suggestion="accept the reinterpretation or override with a reason",
                )
            )
        p = res.prob(t, u_path)
        if p < cfg.review_below_probability:
            alts = ", ".join(f"{u} {q:.2f}" for u, q in res.alternatives(t))
            issues.append(
                Issue(
                    "GEO-LOW-01",
                    Severity.INFO,
                    f"{iv.top:.2f}-{iv.base:.2f} m: {u_path} p={p:.2f} (alternatives: {alts or 'none'})",
                    category="geology",
                    location_ids=[lid],
                    depth=iv.top,
                    suggestion="review",
                )
            )
    # ---- GEO-OMI-01: possible omission of usually present units
    for a, b in zip(runs[:-1], runs[1:]):
        between = column.between(a.unit, b.unit)
        missing = [c for c in between if column.presence(c) >= cfg.omission_presence_threshold]
        if missing:
            exp_t = sum(column.column_unit(c).thickness_mean for c in missing)
            names = ", ".join(kb[c].name for c in missing)
            issues.append(
                Issue(
                    "GEO-OMI-01",
                    Severity.WARNING,
                    f"{kb[a.unit].name} rests directly on {kb[b.unit].name} at "
                    f"{b.top:.2f} m: possible {names} omission (regional presence "
                    f"{', '.join(f'{column.presence(c):.0%}' for c in missing)}; typical {exp_t:.0f} m)",
                    category="geology",
                    location_ids=[lid],
                    depth=b.top,
                    explanations=[
                        f"{names} present but logged as part of an adjacent unit (check descriptions near "
                        f"{b.top:.2f} m)",
                        "unit locally absent (erosion, channel or pinch-out)",
                        "depth or legend error in the log",
                    ],
                    details={"upper": a.unit, "lower": b.unit, "missing": missing},
                )
            )
    # ---- GEO-MIS-01: unlogged intervals with inferred content
    by_src: dict[int, list[int]] = defaultdict(list)
    for t, sl in enumerate(res.slices):
        if not sl.evidence.informative:
            by_src[sl.source_index].append(t)
    for k, ts in by_src.items():
        iv = ivs[k]
        segs: list[list] = []
        for t in ts:
            u = res.path[t]
            if segs and segs[-1][0] == u:
                segs[-1][2] = res.slices[t].base
                segs[-1][3].append(res.prob(t, u))
            else:
                segs.append([u, res.slices[t].top, res.slices[t].base, [res.prob(t, u)]])
        txt = "; ".join(f"{kb[u].name} {a:.1f}-{b:.1f} m (p={np.mean(ps):.2f})" for u, a, b, ps in segs)
        pmin = min(np.mean(s[3]) for s in segs)
        issues.append(
            Issue(
                "GEO-MIS-01",
                Severity.WARNING if pmin < cfg.review_below_probability else Severity.INFO,
                f"unlogged interval {iv.top:.2f}-{iv.base:.2f} m inferred as: {txt}",
                category="geology",
                location_ids=[lid],
                depth=iv.top,
                suggestion="accept or reject the inferred sequence",
                details={
                    "segments": [{"unit": u, "top": a, "base": b, "p": float(np.mean(ps))} for u, a, b, ps in segs]
                },
            )
        )
    # ---- GEO-THK-01
    for r in runs:
        u = kb[r.unit]
        if r.thickness > u.thickness_max * 1.05:
            issues.append(
                Issue(
                    "GEO-THK-01",
                    Severity.WARNING,
                    f"{u.name} is {r.thickness:.1f} m thick (known maximum {u.thickness_max:.0f} m)",
                    category="geology",
                    location_ids=[lid],
                    depth=r.top,
                    explanations=[
                        "repeated logging of one unit",
                        "misidentified unit",
                        "faulting / repetition",
                        "depth error",
                    ],
                )
            )
    return issues


# ============================================================================ cross-borehole
def neighbour_pairs(locs: list[Location], k: int = 4, max_factor: float = 4.0) -> list[tuple[int, int, float]]:
    if len(locs) < 2:
        return []
    xy = np.array([[l.x, l.y] for l in locs])
    d = np.hypot(xy[:, None, 0] - xy[None, :, 0], xy[:, None, 1] - xy[None, :, 1])
    np.fill_diagonal(d, np.inf)
    nn = np.min(d, axis=1)
    dmax = max_factor * float(np.median(nn[np.isfinite(nn)]))
    pairs = set()
    for i in range(len(locs)):
        for j in np.argsort(d[i])[:k]:
            if d[i, j] <= dmax:
                pairs.add((min(i, j), max(i, j)))
    return [(i, j, float(d[i, j])) for i, j in sorted(pairs)]


def cross_validate(
    locs: list[Location], runs: dict[str, list[UnitRun]], obs: ObservationSet, column: StratColumn, faults=None
) -> list[Issue]:
    """Cross-hole checks. Pairs separated by a *known* fault are reported as information that is
    consistent with the mapped fault rather than as warnings (an unexplained offset is what
    suggests an unmapped fault)."""
    from ..core.geometry import crossing_mask

    issues: list[Issue] = []
    kb = column.kb
    stack = bedrock_stack(column)
    pairs = neighbour_pairs(locs)
    fault_segs, fault_names = [], []
    for f in faults or []:
        for seg in f.segments():
            fault_segs.append(seg)
            fault_names.append(f.name)
    fault_segs = np.array(fault_segs) if fault_segs else np.zeros((0, 2, 2))

    def crossing_fault(a: Location, b: Location) -> str | None:
        if not len(fault_segs):
            return None
        m = crossing_mask(np.array([[a.x, a.y]]), np.array([[b.x, b.y]]), fault_segs)[0]
        return fault_names[int(np.argmax(m))] if m.any() else None

    eq: dict[tuple[str, str], float] = {}
    for o in obs.observations:
        if o.kind in (ContactType.CONFORMABLE, ContactType.EROSIONAL) and not o.inferred:
            eq[(o.location_id, o.surface)] = o.value
    surfaces = sorted({s for (_, s) in eq if s.startswith("base:") or s == "rockhead"})
    for i, j, dist in pairs:
        a, b = locs[i], locs[j]
        if dist <= 0:
            continue
        for s in surfaces:
            if (a.id, s) in eq and (b.id, s) in eq:
                dz = abs(eq[(a.id, s)] - eq[(b.id, s)])
                dip = math.degrees(math.atan2(dz, dist))
                limit = column.rockhead_max_apparent_dip_deg if s == "rockhead" else column.max_apparent_dip_deg
                if dip > limit:
                    if s == "rockhead":
                        expl = [
                            "scour hollow / buried channel",
                            "dissolution pipe or doline (where Chalk is at rockhead)",
                            "levelling or depth error",
                        ]
                        rule, sev = "GEO-DIP-02", Severity.INFO
                    else:
                        expl = [
                            "fault between the holes",
                            "channel or erosion surface",
                            "misidentified contact",
                            "levelling or depth error",
                        ]
                        rule, sev = "GEO-DIP-01", Severity.WARNING
                    fname = crossing_fault(a, b)
                    if fname and s != "rockhead":
                        sev = Severity.INFO
                        expl = [f"consistent with mapped fault {fname} between the holes"] + expl[1:]
                    issues.append(
                        Issue(
                            rule,
                            sev,
                            f"{s} differs by {dz:.1f} m between {a.id} and {b.id} "
                            f"({dist:.0f} m apart): apparent dip {dip:.1f} deg exceeds {limit:.1f} deg"
                            + (f" (across mapped fault {fname})" if fname else ""),
                            category="geology",
                            location_ids=[a.id, b.id],
                            explanations=expl,
                            details={"surface": s, "dz": dz, "distance": dist, "dip_deg": dip},
                        )
                    )
        # same-elevation conflicts
        ra, rb = runs.get(a.id, []), runs.get(b.id, [])
        if a.ground_level is None or b.ground_level is None:
            continue
        for first, second, l1, l2 in ((ra, rb, a, b), (rb, ra, b, a)):
            for r1 in first:
                if r1.unit not in stack:
                    continue
                z_bottom_1 = l1.ground_level - r1.base
                for r2 in second:
                    if r2.unit not in stack or not column.is_above(r1.unit, r2.unit):
                        continue
                    z_top_2 = l2.ground_level - r2.top
                    overlap = z_top_2 - z_bottom_1
                    if overlap <= 0:
                        continue
                    between = column.between(r1.unit, r2.unit)
                    t_min = sum(
                        column.column_unit(c).thickness_mean * 0.5 for c in between if column.presence(c) >= 0.5
                    )
                    need = overlap + t_min
                    dip = math.degrees(math.atan2(need, dist))
                    if dip > column.max_apparent_dip_deg * 2:
                        fname = crossing_fault(l1, l2)
                        issues.append(
                            Issue(
                                "GEO-ELV-01",
                                Severity.INFO if fname else Severity.WARNING,
                                f"{kb[r1.unit].name} in {l1.id} extends down to {z_bottom_1:.1f} m while "
                                f"{kb[r2.unit].name} in {l2.id} rises to {z_top_2:.1f} m ({dist:.0f} m apart): needs "
                                f">= {need:.1f} m of offset (apparent dip {dip:.1f} deg)",
                                category="geology",
                                location_ids=[l1.id, l2.id],
                                explanations=[
                                    "possible fault",
                                    "possible erosion / channel",
                                    "potential logging or levelling issue",
                                ],
                                details={"upper": r1.unit, "lower": r2.unit, "offset_m": need, "dip_deg": dip},
                            )
                        )
                        break
    # leave-one-out anomalies
    by_surface: dict[str, list[tuple[str, float, float, float]]] = defaultdict(list)
    loc_xy = {l.id: (l.x, l.y) for l in locs}
    for (lid, s), z in eq.items():
        if lid in loc_xy:
            by_surface[s].append((lid, loc_xy[lid][0], loc_xy[lid][1], z))
    for s, rows in by_surface.items():
        if len(rows) < 5:
            continue
        xy = np.array([[r[1], r[2]] for r in rows])
        z = np.array([r[3] for r in rows])
        for k, r in enumerate(rows):
            d = np.hypot(xy[:, 0] - r[1], xy[:, 1] - r[2])
            d[k] = np.inf
            if len(fault_segs):
                blocked = crossing_mask(np.repeat([[r[1], r[2]]], len(xy), axis=0), xy, fault_segs).any(axis=1)
                d[blocked] = np.inf
                if np.isfinite(d).sum() < 3:
                    continue
            nn = np.argsort(d)[:6]
            w = 1.0 / np.maximum(d[nn], 1.0) ** 2
            pred = float(np.sum(w * z[nn]) / w.sum())
            spread = float(np.sqrt(np.sum(w * (z[nn] - pred) ** 2) / w.sum())) + 0.5
            res = r[3] - pred
            if abs(res) > max(3 * spread, 2.0):
                issues.append(
                    Issue(
                        "GEO-LOO-01",
                        Severity.WARNING,
                        f"{s} in {r[0]} is {res:+.1f} m from the "
                        f"neighbour prediction ({pred:.1f} m, spread {spread:.1f} m)",
                        category="geology",
                        location_ids=[r[0]],
                        explanations=[
                            "local structure (fault, channel, dissolution)",
                            "misidentified contact",
                            "levelling or depth error",
                        ],
                        details={"surface": s, "residual": res},
                    )
                )
    return issues
