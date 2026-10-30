"""Turn interpreted boreholes into modelling constraints — with the right *type* of contact.

Naive tools interpolate "top of London Clay" through every hole. That is geologically wrong
where the top is an erosion surface (rockhead): the observed top is where erosion stopped, not
the depositional top. Here every observation is typed:

* bedrock surfaces are the *bases* of units in a strict stack (``base:LC`` ...);
* a unit found at rockhead means the units above it were eroded there: ``base:<unit above> >= rockhead``;
* skipped units in a stack are absent: their bases coincide (zero thickness);
* a hole ending inside a unit gives ``base:<unit> <= z_end`` (the base is deeper);
* cover sequences (artificial, superficial) are modelled as thicknesses (``thk:ALV``) and a
  total per sequence (``thk_seq:superficial``), equalities when the hole passes through the
  sequence and lower bounds when it ends inside it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from ..models.enums import ConfidenceClass, ContactType, DataSource
from .knowledge.kb import StratColumn


@dataclass
class UnitRun:
    unit: str
    top: float
    base: float
    p: float = 1.0
    synthetic: bool = False

    @property
    def thickness(self) -> float:
        return self.base - self.top


@dataclass
class Observation:
    surface: str  # "base:LC" | "rockhead" | "thk:ALV" | "thk_seq:superficial" | "ground"
    location_id: str
    x: float
    y: float
    value: float  # elevation (surfaces) or thickness (thk*)
    kind: ContactType
    sigma: float = 0.15
    source: DataSource = DataSource.AGS4
    note: str = ""
    weight: float = 1.0
    inferred: bool = False  # e.g. a contact placed inside an unlogged interval

    @property
    def is_equality(self) -> bool:
        return self.kind in (ContactType.CONFORMABLE, ContactType.EROSIONAL, ContactType.ABSENT, ContactType.MAP)

    @property
    def confidence_class(self) -> ConfidenceClass:
        if (
            self.kind in (ContactType.CONFORMABLE, ContactType.EROSIONAL)
            and not self.inferred
            and self.source in (DataSource.AGS4, DataSource.AGS3)
        ):
            return ConfidenceClass.OBSERVED
        return ConfidenceClass.INFERRED


@dataclass
class ObservationSet:
    observations: list[Observation] = field(default_factory=list)

    def by_surface(self) -> dict[str, list[Observation]]:
        out: dict[str, list[Observation]] = defaultdict(list)
        for o in self.observations:
            out[o.surface].append(o)
        return dict(out)

    def arrays(self, surface: str, kinds: tuple[ContactType, ...] | None = None):
        obs = [o for o in self.observations if o.surface == surface and (kinds is None or o.kind in kinds)]
        if not obs:
            return np.zeros((0, 2)), np.zeros(0), np.zeros(0), []
        xy = np.array([[o.x, o.y] for o in obs])
        return xy, np.array([o.value for o in obs]), np.array([o.sigma for o in obs]), obs

    def equalities(self, surface: str):
        return self.arrays(
            surface, (ContactType.CONFORMABLE, ContactType.EROSIONAL, ContactType.ABSENT, ContactType.MAP)
        )

    def inequalities(self, surface: str, kind: ContactType):
        return self.arrays(surface, (kind,))

    def extend(self, other: ObservationSet) -> None:
        self.observations.extend(other.observations)


def bedrock_stack(column: StratColumn) -> list[str]:
    return [c for s in column.bedrock_sequences() for c in s.codes]


def stack_surfaces(column: StratColumn) -> list[str]:
    """Modelled bedrock surfaces: the base of every stack unit except the lowest."""
    st = bedrock_stack(column)
    return [f"base:{u}" for u in st[:-1]]


def extract_observations(
    location_id: str,
    x: float,
    y: float,
    ground_level: float,
    final_depth: float | None,
    runs: list[UnitRun],
    column: StratColumn,
    sigma: float = 0.15,
    source: DataSource = DataSource.AGS4,
) -> ObservationSet:
    obs = ObservationSet()
    if not runs:
        return obs
    gl = ground_level
    end = max(final_depth or 0.0, runs[-1].base)

    def add(surface, value, kind, note="", sig=sigma, inferred=False):
        obs.observations.append(
            Observation(surface, location_id, x, y, float(value), kind, sig, source, note, inferred=inferred)
        )

    add("ground", gl, ContactType.CONFORMABLE, "collar level")
    stack = bedrock_stack(column)
    cover = column.cover_sequences()
    # ---------------------------------------------------------------- cover sequences
    first_bed_idx = next((k for k, r in enumerate(runs) if r.unit in stack), None)
    reached_bedrock = first_bed_idx is not None
    last_unit = runs[-1].unit
    for seq in cover:
        codes = set(seq.codes)
        thk = {c: 0.0 for c in seq.codes}
        for r in runs:
            if r.unit in codes:
                thk[r.unit] += r.thickness
        seq_idx = column.sequences.index(seq)
        ended_in_seq = last_unit in codes
        ended_above = column.seq_of.get(last_unit, 99) < seq_idx
        if ended_above:
            continue  # sequence never reached: no information
        total = sum(thk.values())
        if ended_in_seq:
            add(f"thk_seq:{seq.id}", total, ContactType.GE, "hole ends inside sequence", sigma)
            add(f"thk:{last_unit}", thk[last_unit], ContactType.GE, "hole ends inside unit", sigma)
            for c, v in thk.items():
                if c != last_unit and v > 0:
                    add(f"thk:{c}", v, ContactType.GE, "facies sequence not fully penetrated", sigma)
        else:
            add(
                f"thk_seq:{seq.id}",
                total,
                ContactType.CONFORMABLE if total > 0 else ContactType.ABSENT,
                "sequence fully penetrated",
                sigma,
            )
            for c, v in thk.items():
                add(f"thk:{c}", v, ContactType.CONFORMABLE if v > 0 else ContactType.ABSENT, "", sigma)
    # ---------------------------------------------------------------- rockhead
    if reached_bedrock:
        z_r = gl - runs[first_bed_idx].top
        inf = runs[first_bed_idx].synthetic or (first_bed_idx > 0 and runs[first_bed_idx - 1].synthetic)
        add(
            "rockhead",
            z_r,
            ContactType.EROSIONAL,
            f"top of {runs[first_bed_idx].unit}",
            max(sigma, 1.5) if inf else sigma,
            inf,
        )
    else:
        add("rockhead", gl - end, ContactType.LE, "hole ended above rockhead")
        return obs
    # ---------------------------------------------------------------- bedrock stack
    bed_runs = [r for r in runs[first_bed_idx:] if r.unit in stack]
    seen: list[UnitRun] = []
    for r in bed_runs:  # keep first occurrence of each unit in stack order (repetitions are reported elsewhere)
        if seen and stack.index(r.unit) <= stack.index(seen[-1].unit):
            continue
        seen.append(r)
    top_unit = seen[0].unit
    ti = stack.index(top_unit)
    if ti > 0:
        add(f"base:{stack[ti - 1]}", z_r, ContactType.GE, f"{stack[ti - 1]} eroded above {top_unit} at rockhead")
    for a, b in zip(seen[:-1], seen[1:]):
        ia, ib = stack.index(a.unit), stack.index(b.unit)
        z = gl - b.top
        inferred = a.synthetic or b.synthetic
        sig = max(sigma, 1.5) if inferred else sigma
        note = " (inferred within unlogged interval)" if inferred else ""
        add(f"base:{a.unit}", z, ContactType.CONFORMABLE, f"{a.unit} on {b.unit}{note}", sig, inferred)
        for k in range(ia + 1, ib):
            add(
                f"base:{stack[k]}",
                z,
                ContactType.ABSENT,
                f"{stack[k]} absent between {a.unit} and {b.unit}",
                sig,
                inferred,
            )
    last = seen[-1]
    li = stack.index(last.unit)
    if li < len(stack) - 1:
        add(f"base:{last.unit}", gl - end, ContactType.LE, f"hole ends in {last.unit}")
    return obs


def isochores(obs: ObservationSet, column: StratColumn) -> ObservationSet:
    """Derive bedrock unit thickness observations (``iso:<unit>``) per location.

    Thickness of unit ``U_m`` (between ``base:U_{m-1}`` and ``base:U_m``) is an equality where
    both bounding surfaces were observed in the same hole, and a lower bound where the top was
    eroded (rockhead) or the hole ended inside the unit.
    """
    out = ObservationSet()
    stack = bedrock_stack(column)
    by_loc: dict[str, list[Observation]] = defaultdict(list)
    for o in obs.observations:
        by_loc[o.location_id].append(o)
    for lid, lst in by_loc.items():
        eq = {o.surface: o for o in lst if o.is_equality}
        ge = {o.surface: o for o in lst if o.kind == ContactType.GE}
        le = {o.surface: o for o in lst if o.kind == ContactType.LE}
        rock = eq.get("rockhead")
        for m in range(1, len(stack) - 1):
            upper, here = f"base:{stack[m - 1]}", f"base:{stack[m]}"
            x, y = lst[0].x, lst[0].y
            src = lst[0].source
            if upper in eq and here in eq:
                t = eq[upper].value - eq[here].value
                out.observations.append(
                    Observation(
                        f"iso:{stack[m]}",
                        lid,
                        x,
                        y,
                        max(t, 0.0),
                        ContactType.ABSENT if t <= 1e-6 else ContactType.CONFORMABLE,
                        max(eq[upper].sigma, eq[here].sigma),
                        src,
                    )
                )
            elif upper in ge and here in eq and rock is not None:
                t = rock.value - eq[here].value
                out.observations.append(
                    Observation(
                        f"iso:{stack[m]}",
                        lid,
                        x,
                        y,
                        max(t, 0.0),
                        ContactType.GE,
                        eq[here].sigma,
                        src,
                        "top eroded at rockhead",
                    )
                )
            elif upper in eq and here in le:
                t = eq[upper].value - le[here].value
                out.observations.append(
                    Observation(
                        f"iso:{stack[m]}",
                        lid,
                        x,
                        y,
                        max(t, 0.0),
                        ContactType.GE,
                        eq[upper].sigma,
                        src,
                        "hole ends in unit",
                    )
                )
            elif upper in ge and here in le and rock is not None:
                t = rock.value - le[here].value
                out.observations.append(
                    Observation(
                        f"iso:{stack[m]}",
                        lid,
                        x,
                        y,
                        max(t, 0.0),
                        ContactType.GE,
                        0.5,
                        src,
                        "top eroded and base not reached",
                    )
                )
    return out
