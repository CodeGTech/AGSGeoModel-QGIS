"""Stratigraphic knowledge base and province columns (the rule database behind the GeoReasoner).

Two layers:

* :class:`KnowledgeBase` — a catalogue of units keyed by BGS Lexicon code, with hierarchy
  (``parent``), indicative age, environment, hydrostratigraphy, lithology signature and
  engineering hazard flags. Stored as JSON (``data/uk_units.json``), loaded into the project
  GeoPackage (``formations`` / ``strat_relations`` tables) so it is queryable in QGIS.
* :class:`StratColumn` — the *local* order of units for a geological province, made of
  sequences separated by erosional surfaces. A sequence is either ``strict`` (layer-cake
  superposition: a unit can only be followed downwards by units listed below it) or ``facies``
  (an association of interfingering deposits, e.g. alluvium/peat or till/outwash, where atypical
  stacking is allowed but penalised and repetition can be enabled).

Order between sequences is always strict: artificial over superficial over bedrock.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from ...core.errors import KnowledgeBaseError
from ...models.enums import HSU, ContactType

_DATA = Path(__file__).with_name("data")

DEFAULT_CODE_ALIASES = {
    "MG": "MGR",
    "MADE": "MGR",
    "FILL": "MGR",
    "TOPS": "TOPSOIL",
    "TOPSOIL": "TOPSOIL",
    "ALLU": "ALV",
    "ALLUVIUM": "ALV",
    "RTD": "RTDU",
    "TERRACE": "RTDU",
    "CHALK": "CK",
    "CHK": "CK",
    "LONDONCLAY": "LC",
    "LCF": "LC",
    "WRB": "LMBE",
    "W&RB": "LMBE",
    "LAMBETH": "LMBE",
    "THANET": "TAB",
    "HARWICH": "HWH",
}


@dataclass(frozen=True)
class Unit:
    code: str
    name: str
    lexicon: bool
    parent: str | None
    rank: str
    unit_class: str  # artificial | superficial | bedrock
    domain: str
    material: str
    age_label: str
    age_max_ma: float
    age_min_ma: float
    environment: str
    hsu: HSU
    permeability: str
    ea_aquifer_typical: str
    aliases: tuple[str, ...]
    signature: dict[str, float]
    thickness_typical: tuple[float, float]
    thickness_max: float
    colour: str
    hazards: dict[str, int]
    notes: str = ""
    model_group: str | None = None

    @property
    def age_mid_ma(self) -> float:
        return 0.5 * (self.age_max_ma + self.age_min_ma)

    @property
    def lexicon_url(self) -> str | None:
        return f"https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub={self.code}" if self.lexicon else None

    def hazard(self, name: str) -> int:
        return int(self.hazards.get(name, 0))


class KnowledgeBase:
    def __init__(
        self,
        units: dict[str, Unit],
        version: str,
        parents: dict[str, str] | None = None,
        references: dict[str, str] | None = None,
    ):
        self.units = units
        self.version = version
        self.parent_names = parents or {}
        self.references = references or {}
        self._alias_rx: list[tuple[re.Pattern, str, str]] = []
        for u in units.values():
            for a in sorted(set(u.aliases) | {u.name.lower()}, key=len, reverse=True):
                self._alias_rx.append((re.compile(r"(?<![a-z])" + re.escape(a.lower()) + r"(?![a-z])"), u.code, a))
        self._alias_rx.sort(key=lambda t: len(t[2]), reverse=True)

    # ------------------------------------------------------------------ access
    def __contains__(self, code: str) -> bool:
        return code in self.units

    def __getitem__(self, code: str) -> Unit:
        try:
            return self.units[code]
        except KeyError as exc:
            raise KnowledgeBaseError(f"unknown unit code {code!r}") from exc

    def get(self, code: str | None) -> Unit | None:
        return self.units.get(code) if code else None

    def ancestors(self, code: str) -> list[str]:
        out, seen = [], set()
        cur = self.units.get(code)
        while cur is not None and cur.parent and cur.parent not in seen:
            out.append(cur.parent)
            seen.add(cur.parent)
            cur = self.units.get(cur.parent)
        return out

    def descendants(self, code: str) -> list[str]:
        return [c for c in self.units if code in self.ancestors(c)]

    def find_in_text(self, text: str) -> list[tuple[str, str]]:
        """Unit names / aliases mentioned in free text, longest match first: ``[(code, alias)]``."""
        t = (text or "").lower()
        found: list[tuple[str, str]] = []
        taken: list[tuple[int, int]] = []
        for rx, code, alias in self._alias_rx:
            for m in rx.finditer(t):
                span = m.span()
                if any(not (span[1] <= a or span[0] >= b) for a, b in taken):
                    continue
                taken.append(span)
                if code not in [c for c, _ in found]:
                    found.append((code, alias))
        return found

    def resolve_code(self, code: str, extra_aliases: dict[str, str] | None = None) -> str | None:
        c = (code or "").strip().upper()
        if not c:
            return None
        if c in self.units:
            return c
        aliases = {**DEFAULT_CODE_ALIASES, **{k.upper(): v for k, v in (extra_aliases or {}).items()}}
        if c in aliases:
            return aliases[c]
        c2 = re.sub(r"[^A-Z&]", "", c)
        return aliases.get(c2)

    def validate(self) -> list[str]:
        problems = []
        for u in self.units.values():
            if u.parent and u.parent not in self.units and u.parent not in self.parent_names:
                problems.append(f"{u.code}: parent {u.parent} undefined")
            if u.model_group and u.model_group not in self.units:
                problems.append(f"{u.code}: model_group {u.model_group} undefined")
            if u.age_min_ma > u.age_max_ma:
                problems.append(f"{u.code}: min age older than max age")
            if u.thickness_typical[0] > u.thickness_typical[1] or u.thickness_typical[1] > u.thickness_max:
                problems.append(f"{u.code}: inconsistent thickness range")
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", u.colour):
                problems.append(f"{u.code}: invalid colour {u.colour}")
            # cycle check
            seen = {u.code}
            cur = u
            while cur.parent:
                if cur.parent in seen:
                    problems.append(f"{u.code}: parent cycle")
                    break
                seen.add(cur.parent)
                nxt = self.units.get(cur.parent)
                if nxt is None:
                    break
                cur = nxt
        return problems


def _unit_from_json(d: dict[str, Any]) -> Unit:
    age = d.get("age", {})
    th = d.get("thickness_m", {})
    typical = th.get("typical", [0.0, math.inf])
    return Unit(
        code=d["code"],
        name=d["name"],
        lexicon=bool(d.get("lexicon", False)),
        parent=d.get("parent"),
        rank=d.get("rank", ""),
        unit_class=d.get("class", "bedrock"),
        domain=d.get("domain", ""),
        material=d.get("material", ""),
        age_label=age.get("label", ""),
        age_max_ma=float(age.get("max_ma", 0.0)),
        age_min_ma=float(age.get("min_ma", 0.0)),
        environment=d.get("environment", ""),
        hsu=HSU(d.get("hsu", "unknown")),
        permeability=d.get("permeability", ""),
        ea_aquifer_typical=d.get("ea_aquifer_typical", ""),
        aliases=tuple(a.lower() for a in d.get("aliases", [])),
        signature={k: float(v) for k, v in d.get("signature", {}).items()},
        thickness_typical=(float(typical[0]), float(typical[1])),
        thickness_max=float(th.get("max", typical[1])),
        colour=d.get("colour", "#999999"),
        hazards={k: int(v) for k, v in d.get("hazards", {}).items()},
        notes=d.get("notes", ""),
        model_group=d.get("model_group"),
    )


def load_kb(path: str | Path | None = None, extra: Iterable[str | Path] = ()) -> KnowledgeBase:
    """Load the bundled unit catalogue (or ``path``) plus optional user catalogues that add or override units."""
    data = json.loads(Path(path or _DATA / "uk_units.json").read_text(encoding="utf-8"))
    units = {d["code"]: _unit_from_json(d) for d in data["units"]}
    parents = dict(data.get("parents", {}))
    version = data.get("kb_version", "unknown")
    for p in extra:
        add = json.loads(Path(p).read_text(encoding="utf-8"))
        for d in add.get("units", []):
            units[d["code"]] = _unit_from_json(d)
        parents.update(add.get("parents", {}))
        version += f"+{Path(p).stem}"
    kb = KnowledgeBase(units, version, parents, data.get("references"))
    problems = kb.validate()
    if problems:
        raise KnowledgeBaseError("; ".join(problems))
    return kb


@lru_cache(maxsize=1)
def default_kb() -> KnowledgeBase:
    return load_kb()


# =============================================================================== columns
@dataclass
class ColumnUnit:
    code: str
    presence: float
    thickness_mean: float
    thickness_sd: float


@dataclass
class Sequence:
    id: str
    kind: str  # artificial | superficial | bedrock
    order: str  # strict | facies
    units: list[ColumnUnit]
    base: str = "erosional"
    typical_order: list[str] = field(default_factory=list)
    free_pairs: set[frozenset[str]] = field(default_factory=set)
    relations: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    repeatable: bool = False
    modelling: str = "stack"

    @property
    def codes(self) -> list[str]:
        return self.typical_order or [u.code for u in self.units]

    def unit(self, code: str) -> ColumnUnit:
        for u in self.units:
            if u.code == code:
                return u
        raise KeyError(code)


class StratColumn:
    def __init__(
        self,
        province_id: str,
        name: str,
        sequences: list[Sequence],
        kb: KnowledgeBase,
        max_apparent_dip_deg: float = 3.0,
        rockhead_max_apparent_dip_deg: float = 15.0,
        repetition_explanations: dict[str, list[str]] | None = None,
        description: str = "",
        aquifer_systems: list[dict] | None = None,
    ):
        self.id = province_id
        self.aquifer_systems = aquifer_systems or []
        self.name = name
        self.description = description
        self.sequences = sequences
        self.kb = kb
        self.max_apparent_dip_deg = max_apparent_dip_deg
        self.rockhead_max_apparent_dip_deg = rockhead_max_apparent_dip_deg
        self.repetition_explanations = repetition_explanations or {}
        self.units: list[str] = [c for s in sequences for c in s.codes]
        if len(set(self.units)) != len(self.units):
            raise KnowledgeBaseError(f"column {province_id}: a unit appears in more than one position")
        for c in self.units:
            if c not in kb:
                raise KnowledgeBaseError(f"column {province_id}: unit {c} not in knowledge base")
        self.pos = {c: i for i, c in enumerate(self.units)}
        self.seq_of: dict[str, int] = {c: k for k, s in enumerate(sequences) for c in s.codes}
        self._cu = {u.code: u for s in sequences for u in s.units}
        problems = self.check_age_order()
        if problems:
            raise KnowledgeBaseError("; ".join(problems))

    # ------------------------------------------------------------------ queries
    def __contains__(self, code: str) -> bool:
        return code in self.pos

    def __len__(self) -> int:
        return len(self.units)

    def sequence(self, code: str) -> Sequence:
        return self.sequences[self.seq_of[code]]

    def column_unit(self, code: str) -> ColumnUnit:
        return self._cu[code]

    def presence(self, code: str) -> float:
        return float(min(max(self._cu[code].presence, 1e-4), 1.0))

    def is_above(self, upper: str, lower: str) -> bool:
        """Strict superposition: upper must lie above lower (False inside facies sequences)."""
        su, sl = self.seq_of[upper], self.seq_of[lower]
        if su != sl:
            return su < sl
        seq = self.sequences[su]
        return seq.order == "strict" and self.pos[upper] < self.pos[lower]

    def between(self, upper: str, lower: str) -> list[str]:
        """Units expected between two units of the same strict sequence."""
        if self.seq_of.get(upper) != self.seq_of.get(lower):
            return []
        seq = self.sequences[self.seq_of[upper]]
        if seq.order != "strict":
            return []
        return [c for c in seq.codes if self.pos[upper] < self.pos[c] < self.pos[lower]]

    def contact_type(self, upper: str, lower: str) -> ContactType:
        if self.seq_of[upper] != self.seq_of[lower]:
            return ContactType.EROSIONAL
        return ContactType.CONFORMABLE

    def relation(self, upper: str, lower: str) -> dict[str, str]:
        seq = self.sequences[self.seq_of[upper]]
        return seq.relations.get((upper, lower), {})

    def resolve(self, code: str | None, extra_aliases: dict[str, str] | None = None) -> set[str]:
        """Map any code (lexicon member, group, generalised map unit, project alias) onto column units."""
        if not code:
            return set()
        c = self.kb.resolve_code(code, extra_aliases) or code.strip().upper()
        if c in self.pos:
            return {c}
        u = self.kb.get(c)
        if u is not None and u.model_group and u.model_group in self.pos:
            return {u.model_group}
        for anc in self.kb.ancestors(c):
            if anc in self.pos:
                return {anc}
        kids = {d for d in self.kb.descendants(c) if d in self.pos}
        return kids

    def bedrock_sequences(self) -> list[Sequence]:
        return [s for s in self.sequences if s.kind == "bedrock"]

    def cover_sequences(self) -> list[Sequence]:
        return [s for s in self.sequences if s.kind != "bedrock"]

    def check_age_order(self) -> list[str]:
        problems = []
        for s in self.sequences:
            if s.order == "strict":
                codes = s.codes
                for a, b in zip(codes[:-1], codes[1:]):
                    ua, ub = self.kb[a], self.kb[b]
                    if ua.age_mid_ma > ub.age_mid_ma + 1e-9 and ua.age_min_ma > ub.age_max_ma:
                        problems.append(
                            f"{self.id}: {a} ({ua.age_label}) is older than underlying {b} ({ub.age_label})"
                        )
        for upper, lower in zip(self.sequences[:-1], self.sequences[1:]):
            youngest_lower = min(self.kb[c].age_min_ma for c in lower.codes)
            oldest_upper = max(self.kb[c].age_min_ma for c in upper.codes)
            if oldest_upper > youngest_lower + 1e-9 and lower.kind == "bedrock":
                problems.append(f"{self.id}: sequence {upper.id} contains units older than sequence {lower.id}")
        return problems

    # ------------------------------------------------------------------ transitions
    def move_weights(self, a: str, allow_up: bool = False, reverse_weight: float = 0.25) -> dict[str, float]:
        """Relative probabilities that the next unit downhole after ``a`` is ``b`` (b != a)."""
        sa = self.seq_of[a]
        seq_a = self.sequences[sa]
        w: dict[str, float] = {}
        for b in self.units:
            if b == a:
                continue
            sb = self.seq_of[b]
            seq_b = self.sequences[sb]
            if sb == sa:
                if seq_a.order == "strict":
                    if self.pos[b] < self.pos[a]:
                        continue
                    skip = 1.0
                    for c in self.between(a, b):
                        skip *= 1.0 - self.presence(c)
                    w[b] = self.presence(b) * skip
                else:
                    pair = frozenset((a, b))
                    if seq_a.repeatable or pair in seq_a.free_pairs or self.pos[b] > self.pos[a]:
                        w[b] = self.presence(b)
                    else:
                        w[b] = self.presence(b) * reverse_weight
            elif sb > sa:
                skip = 1.0
                if seq_a.order == "strict":  # the rest of a's own strict sequence is absent
                    for c in seq_a.codes:
                        if self.pos[c] > self.pos[a]:
                            skip *= 1.0 - self.presence(c)
                for k in range(sa + 1, sb):
                    for c in self.sequences[k].codes:
                        skip *= 1.0 - self.presence(c)
                if seq_b.order == "strict":
                    for c in seq_b.codes:
                        if self.pos[c] < self.pos[b]:
                            skip *= 1.0 - self.presence(c)
                w[b] = self.presence(b) * skip
            elif allow_up:
                w[b] = 1e-9  # placeholder; the reasoner sets the repetition probability explicitly
        return w

    def start_weights(self) -> dict[str, float]:
        w = {}
        for b in self.units:
            sb = self.seq_of[b]
            skip = 1.0
            for k in range(sb):
                for c in self.sequences[k].codes:
                    skip *= 1.0 - self.presence(c)
            if self.sequences[sb].order == "strict":
                for c in self.sequences[sb].codes:
                    if self.pos[c] < self.pos[b]:
                        skip *= 1.0 - self.presence(c)
            w[b] = self.presence(b) * skip
        return w


def load_column(province: str | Path = "london_basin", kb: KnowledgeBase | None = None) -> StratColumn:
    kb = kb or default_kb()
    p = Path(province)
    if not p.suffix:
        p = _DATA / "provinces" / f"{province}.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    seqs = []
    for s in data["sequences"]:
        units = [
            ColumnUnit(
                u["code"],
                float(u.get("presence", 0.5)),
                float(u.get("thickness_mean", 5.0)),
                float(u.get("thickness_sd", u.get("thickness_mean", 5.0) * 0.5)),
            )
            for u in s["units"]
        ]
        rel = {(r["upper"], r["lower"]): r for r in s.get("relations", [])}
        seqs.append(
            Sequence(
                s["id"],
                s.get("kind", "bedrock"),
                s.get("order", "strict"),
                units,
                s.get("base", "erosional"),
                list(s.get("typical_order", [])),
                {frozenset(p) for p in s.get("free_pairs", [])},
                rel,
                bool(s.get("repeatable", False)),
                s.get("modelling", "stack"),
            )
        )
    return StratColumn(
        data["id"],
        data.get("name", data["id"]),
        seqs,
        kb,
        float(data.get("max_apparent_dip_deg", 3.0)),
        float(data.get("rockhead_max_apparent_dip_deg", 15.0)),
        data.get("repetition_explanations"),
        data.get("description", ""),
        data.get("aquifer_systems"),
    )


def available_provinces() -> list[str]:
    return sorted(p.stem for p in (_DATA / "provinces").glob("*.json"))
