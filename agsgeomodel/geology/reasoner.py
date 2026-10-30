"""GeoReasoner: formation interpretation that cannot produce geologically impossible sequences.

Pipeline for a project:

1. **Evidence** — each interval's description and logged codes are scored against every unit
   of the province column (:mod:`.classifier`).
2. **Pass 1 (per hole)** — order-constrained inference along each hole (:mod:`.sequence`).
3. **Pass 2 (correlation)** — contact elevations from the *other* holes (leave-one-out IDW)
   form an elevation prior; inference is repeated so neighbours inform ambiguous intervals.
   This is the "geological rules constrained" correlation mode.
4. **Validation** — per-hole and cross-hole rules (:mod:`.validation`).
5. **Constraints** — typed modelling observations (:mod:`.contacts`).

Manual overrides (``Interval.override``) are hard evidence and are always honoured.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from ..core.config import ReasonerConfig
from ..core.issues import Issue, IssueLog
from ..models.entities import EvidenceItem, Interval, Location
from ..models.enums import ContactType, DataSource, InterpretationStatus, Severity
from ..models.project import Project
from .classifier import FormationClassifier, LearnedModel, describe_decision
from .contacts import ObservationSet, UnitRun, bedrock_stack, extract_observations
from .knowledge.kb import StratColumn, load_column
from .sequence import SequenceResult, infer_sequence, runs_from_result
from .validation import cross_validate, validate_sequence


@dataclass
class LocationInterpretation:
    location_id: str
    result: SequenceResult
    runs: list[UnitRun]
    issues: list[Issue] = field(default_factory=list)

    def unit_at(self, depth: float) -> str | None:
        for r in self.runs:
            if r.top - 1e-9 <= depth <= r.base + 1e-9:
                return r.unit
        return None


@dataclass
class ProjectInterpretation:
    column: StratColumn
    locations: dict[str, LocationInterpretation]
    observations: ObservationSet
    issues: IssueLog
    config: ReasonerConfig
    passes: int = 1

    def runs(self, location_id: str) -> list[UnitRun]:
        li = self.locations.get(location_id)
        return li.runs if li else []

    def summary(self) -> dict:
        n_iv = sum(len(li.result.slices) for li in self.locations.values())
        lows = sum(
            1
            for li in self.locations.values()
            for t in range(len(li.result.slices))
            if li.result.prob(t, li.result.path[t]) < self.config.review_below_probability
        )
        return {
            "locations": len(self.locations),
            "slices": n_iv,
            "low_confidence_slices": lows,
            "issues": self.issues.counts(),
            "passes": self.passes,
        }


SECONDARY_SOURCES = {DataSource.BGS_AGS, DataSource.BGS_DIGITISED, DataSource.BGS_INDEX}


def proposal_key(units) -> str:
    """Canonical text of a proposed unit sequence for one interval (consecutive duplicates removed)."""
    out: list[str] = []
    for u in units:
        if u and (not out or out[-1] != u):
            out.append(u)
    return ">".join(out)


class GeoReasoner:
    def __init__(
        self,
        column: StratColumn | str = "london_basin",
        config: ReasonerConfig | None = None,
        code_aliases: dict[str, str] | None = None,
        learned: LearnedModel | None = None,
        primary_sigma: float = 0.15,
        secondary_sigma: float = 1.0,
    ):
        self.column = load_column(column) if isinstance(column, str) else column
        self.cfg = config or ReasonerConfig()
        self.classifier = FormationClassifier(self.column, self.cfg, code_aliases, learned)
        self.primary_sigma = primary_sigma
        self.secondary_sigma = secondary_sigma

    # ------------------------------------------------------------------ single hole
    def interpret_location(
        self, loc: Location, intervals: list[Interval], elevation_prior=None
    ) -> LocationInterpretation:
        allow_rep = loc.id in set(self.cfg.allow_repetition)
        res = infer_sequence(
            loc.id, intervals, self.column, self.classifier, self.cfg, loc.ground_level, elevation_prior, allow_rep
        )
        runs = [UnitRun(r["unit"], r["top"], r["base"], r["p"], r["synthetic"]) for r in runs_from_result(res)]
        issues = validate_sequence(res, intervals, runs, self.column, self.cfg)
        return LocationInterpretation(loc.id, res, runs, issues)

    # ------------------------------------------------------------------ project
    def interpret(self, project: Project, second_pass: bool = True, apply: bool = True) -> ProjectInterpretation:
        by_loc = project.intervals_by_location()
        locs = [l for l in project.located() if l.id in by_loc]
        out: dict[str, LocationInterpretation] = {}
        for loc in locs:
            out[loc.id] = self.interpret_location(loc, by_loc[loc.id])
        obs = self._observations(locs, out)
        passes = 1
        if second_pass and len(locs) >= 3:
            priors = self._elevation_priors(locs, obs)
            for loc in locs:
                if loc.id in priors:
                    out[loc.id] = self.interpret_location(loc, by_loc[loc.id], priors[loc.id])
            obs = self._observations(locs, out)
            passes = 2
        log = IssueLog()
        for li in out.values():
            log.extend(li.issues)
        log.extend(cross_validate(locs, {k: v.runs for k, v in out.items()}, obs, self.column, project.faults))
        for lid in by_loc:
            if lid not in project.locations:
                log.add(
                    Issue(
                        "GEO-LOC-01",
                        Severity.ERROR,
                        "intervals reference an unknown location",
                        category="geology",
                        location_ids=[lid],
                    )
                )
            elif project.locations[lid].ground_level is None:
                log.add(
                    Issue(
                        "GEO-LOC-02",
                        Severity.WARNING,
                        "location has no ground level: excluded from interpretation and modelling",
                        category="geology",
                        location_ids=[lid],
                    )
                )
        interp = ProjectInterpretation(self.column, out, obs, log, self.cfg, passes)
        if apply:
            self.apply(project, interp)
        return interp

    def _observations(self, locs: list[Location], out: dict[str, LocationInterpretation]) -> ObservationSet:
        obs = ObservationSet()
        for loc in locs:
            li = out.get(loc.id)
            if li and loc.ground_level is not None:
                sig = self.primary_sigma / max(loc.quality, 0.1)
                if loc.source in SECONDARY_SOURCES:  # BGS / legacy records: never as precise as new AGS data
                    sig = max(sig, self.secondary_sigma / max(loc.quality, 0.1))
                obs.extend(
                    extract_observations(
                        loc.id, loc.x, loc.y, loc.ground_level, loc.final_depth, li.runs, self.column, sig, loc.source
                    )
                )
        return obs

    def _elevation_priors(self, locs: list[Location], obs: ObservationSet):
        """Leave-one-out IDW predictions of rockhead and bedrock bases -> per-hole prior callables."""
        stack = bedrock_stack(self.column)
        surfaces = ["rockhead"] + [f"base:{u}" for u in stack[:-1]]
        data: dict[str, list[tuple[str, float, float, float]]] = defaultdict(list)
        for o in obs.observations:
            if (
                o.surface in surfaces
                and o.kind in (ContactType.CONFORMABLE, ContactType.EROSIONAL, ContactType.ABSENT)
                and not o.inferred
            ):
                data[o.surface].append((o.location_id, o.x, o.y, o.value))
        sigma0 = self.cfg.neighbour_elevation_sigma_m
        priors = {}
        for loc in locs:
            pred: dict[str, tuple[float, float]] = {}
            for s in surfaces:
                rows = [r for r in data.get(s, []) if r[0] != loc.id]
                if len(rows) < 2:
                    continue
                xy = np.array([[r[1], r[2]] for r in rows])
                z = np.array([r[3] for r in rows])
                d = np.hypot(xy[:, 0] - loc.x, xy[:, 1] - loc.y)
                nn = np.argsort(d)[:6]
                w = 1.0 / np.maximum(d[nn], 1.0) ** 2
                mu = float(np.sum(w * z[nn]) / w.sum())
                spread = float(np.sqrt(np.sum(w * (z[nn] - mu) ** 2) / w.sum()))
                sig = float(np.hypot(sigma0, spread) + 0.002 * d[nn].min())
                pred[s] = (mu, sig)
            if not pred:
                continue
            priors[loc.id] = self._make_prior(pred, stack)
        return priors

    def _make_prior(self, pred: dict[str, tuple[float, float]], stack: list[str]):
        def prior(unit: str, zt: float, zb: float) -> float:
            zm = 0.5 * (zt + zb)
            rock = pred.get("rockhead")
            if unit in stack:
                m = stack.index(unit)
                top = pred.get(f"base:{stack[m - 1]}") if m > 0 else None
                base = pred.get(f"base:{unit}") if m < len(stack) - 1 else None
                hi = top[0] if top else (rock[0] if rock else np.inf)
                if rock and top:
                    hi = min(hi, rock[0])
                lo = base[0] if base else -np.inf
                sig = max([v[1] for v in (top, base, rock) if v] + [1.0])
            else:
                if rock is None:
                    return 0.0
                hi, lo, sig = np.inf, rock[0], rock[1]
            dist = max(lo - zm, zm - hi, 0.0)
            return float(max(-0.5 * (dist / sig) ** 2, -6.0))

        return prior

    # ------------------------------------------------------------------ write back
    def apply(self, project: Project, interp: ProjectInterpretation) -> None:
        by_loc = project.intervals_by_location()
        kb = self.column.kb
        for lid, li in interp.locations.items():
            ivs = by_loc.get(lid, [])
            res = li.result
            slices_of: dict[int, list[int]] = defaultdict(list)
            for t, sl in enumerate(res.slices):
                slices_of[sl.source_index].append(t)
            for k, iv in enumerate(ivs):
                ts = slices_of.get(k, [])
                if not ts:
                    continue
                units = [res.path[t] for t in ts]
                th = np.array([res.slices[t].thickness for t in ts])
                dominant = max(set(units), key=lambda u: th[[i for i, x in enumerate(units) if x == u]].sum())
                p = float(sum(res.prob(t, res.path[t]) * res.slices[t].thickness for t in ts) / max(th.sum(), 1e-9))
                ev = res.slices[ts[0]].evidence
                iv.unit = dominant
                iv.probability = round(p, 3)
                t0 = ts[0]
                iv.alternatives = res.alternatives(t0)
                runner = iv.alternatives[0][0] if iv.alternatives else None
                iv.evidence = [e for e in ev.explain(dominant, 8)] or [
                    EvidenceItem("sequence", dominant, 0.0, "inferred from stratigraphic order and neighbours")
                ]
                if iv.override is not None:
                    iv.status = InterpretationStatus.OVERRIDDEN
                elif dominant in {u for u, _, _ in ev.stated_units}:
                    iv.status = InterpretationStatus.STATED
                elif ev.informative:
                    iv.status = InterpretationStatus.CLASSIFIED
                else:
                    iv.status = InterpretationStatus.INFERRED
                reasons = []
                seq = []
                for t in ts:
                    if not seq or seq[-1][0] != res.path[t]:
                        seq.append([res.path[t], res.slices[t].top, res.slices[t].base])
                    else:
                        seq[-1][2] = res.slices[t].base
                if len(seq) > 1:
                    reasons.append(
                        "unlogged interval inferred as "
                        + ", ".join(f"{kb[u].name} {a:.1f}-{b:.1f} m" for u, a, b in seq)
                    )
                if p < self.cfg.review_below_probability:
                    reasons.append(f"probability {p:.2f} below review threshold")
                if not ev.informative and iv.override is None:
                    reasons.append("no description evidence")
                proposal = proposal_key([u for u, _, _ in seq])
                accepted = iv.review is not None and iv.review.decision == "accepted" and iv.review.proposal == proposal
                iv.needs_review = bool(reasons) and iv.override is None and not accepted
                iv.review_reason = "; ".join(reasons) if reasons else describe_decision(ev, dominant, runner)
                if accepted and reasons:
                    iv.review_reason = f"accepted by {iv.review.reviewer}: {iv.review_reason}"
                elif iv.review is not None and iv.review.decision == "accepted" and not accepted:
                    iv.review_reason = (
                        f"re-review: interpretation changed since accepted by {iv.review.reviewer} "
                        f"({iv.review.proposal} -> {proposal}); " + iv.review_reason
                    )
