"""Domain entities for ground investigation data and interpretations.

Depths are metres below the location's ground level (positive downwards). Elevations are metres
relative to the project vertical datum (e.g. ODN). Holes are assumed vertical in v0.1 (inclined
holes are listed on the roadmap; their LOCA orientation fields are preserved as attributes).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .enums import DataSource, InterpretationStatus, LocationKind


@dataclass
class Location:
    id: str
    x: float
    y: float
    ground_level: float | None
    final_depth: float | None = None
    kind: LocationKind = LocationKind.BOREHOLE
    type_code: str = ""
    source: DataSource = DataSource.AGS4
    date: str | None = None
    quality: float = 1.0  # 0..1 weight used for secondary data
    remarks: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)

    def elevation(self, depth: float) -> float:
        if self.ground_level is None:
            raise ValueError(f"location {self.id} has no ground level")
        return self.ground_level - depth

    @property
    def xy(self) -> tuple[float, float]:
        return (self.x, self.y)


@dataclass
class Override:
    unit: str
    reviewer: str
    reason: str
    timestamp: str


@dataclass
class ReviewDecision:
    """A reviewer's decision on a flagged interpretation (stored in ``review_decisions``).

    ``proposal`` is the unit sequence that was reviewed (e.g. ``"LC>LMBE"``). An acceptance stays
    valid only while the GeoReasoner proposes the same sequence; if a later run changes it, the
    interval is flagged for review again."""

    decision: str  # accepted | override
    proposal: str
    reviewer: str
    reason: str = ""
    timestamp: str = ""


@dataclass
class EvidenceItem:
    """One explainable contribution to a formation decision (log-likelihood points)."""

    feature: str
    unit: str
    weight: float
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"feature": self.feature, "unit": self.unit, "weight": round(self.weight, 3), "note": self.note}


@dataclass
class Interval:
    location_id: str
    top: float
    base: float
    description: str = ""
    legend_code: str = ""
    geology_code: str = ""
    geology_code2: str = ""
    bgs_code: str = ""
    formation_name: str = ""
    remarks: str = ""
    method: str = ""
    source: DataSource = DataSource.AGS4
    # ---- interpretation (filled by the GeoReasoner) ----
    unit: str | None = None
    probability: float | None = None
    alternatives: list[tuple[str, float]] = field(default_factory=list)
    evidence: list[EvidenceItem] = field(default_factory=list)
    status: InterpretationStatus = InterpretationStatus.UNRESOLVED
    needs_review: bool = False
    review_reason: str = ""
    override: Override | None = None
    review: ReviewDecision | None = None
    synthetic: bool = False  # created by the reasoner (e.g. split of no-recovery)

    @property
    def thickness(self) -> float:
        return self.base - self.top

    @property
    def mid(self) -> float:
        return 0.5 * (self.top + self.base)

    @property
    def final_unit(self) -> str | None:
        return self.override.unit if self.override else self.unit


@dataclass
class DrillingSection:
    location_id: str
    top: float
    base: float
    type: str = ""  # HDPH_TYPE, e.g. CP, RC, RO, WLS, IP
    remarks: str = ""


@dataclass
class SPTRecord:
    location_id: str
    depth: float
    n: float | None
    reported: str = ""
    energy_ratio: float | None = None
    n60: float | None = None
    test_type: str = ""  # S (split spoon) / C (solid cone)
    refusal: bool = False
    casing_depth: float | None = None
    water_depth: float | None = None


@dataclass
class CPTSounding:
    location_id: str
    test_id: str
    depth: np.ndarray
    qc: np.ndarray  # MPa
    fs: np.ndarray  # MPa
    u2: np.ndarray | None = None  # MPa
    qt: np.ndarray | None = None  # MPa (if supplied)
    area_ratio: float | None = None
    water_depth: float | None = None
    cone_type: str = ""

    def __post_init__(self):
        self.depth = np.asarray(self.depth, dtype=float)
        self.qc = np.asarray(self.qc, dtype=float)
        self.fs = np.asarray(self.fs, dtype=float)
        if self.u2 is not None:
            self.u2 = np.asarray(self.u2, dtype=float)
        if self.qt is not None:
            self.qt = np.asarray(self.qt, dtype=float)


@dataclass
class WaterStrike:
    location_id: str
    depth: float
    readings: list[tuple[float, float]] = field(default_factory=list)  # (minutes, depth to water)
    casing_depth: float | None = None
    sealed_depth: float | None = None
    datetime: str | None = None
    remarks: str = ""

    @property
    def rest_depth(self) -> float:
        """Depth after the longest recorded wait (``rose to``), else the strike depth."""
        if self.readings:
            return sorted(self.readings)[-1][1]
        return self.depth


@dataclass
class MonitoringReading:
    datetime: str
    value: float
    type: str = ""
    unit: str = "m"


@dataclass
class MonitoringPoint:
    location_id: str
    mon_id: str
    response_top: float | None = None
    response_base: float | None = None
    instrument_type: str = ""
    readings: list[MonitoringReading] = field(default_factory=list)


@dataclass
class Sample:
    location_id: str
    top: float
    base: float | None = None
    ref: str = ""
    type: str = ""
    sample_id: str = ""


@dataclass
class LabResult:
    location_id: str
    depth: float
    group: str  # AGS group, e.g. LLPL
    test: str  # heading or determinand, e.g. LLPL_PI, SO4
    value: float | None
    unit: str = ""
    text: str = ""
    sample_ref: str = ""


@dataclass
class CoreRun:
    location_id: str
    top: float
    base: float
    tcr: float | None = None
    scr: float | None = None
    rqd: float | None = None


@dataclass
class Fault:
    name: str
    trace: np.ndarray  # (N, 2) map trace in project CRS
    throw_m: float | None = None  # vertical throw if known (geological justification)
    downthrown_side: str | None = None  # "left" | "right" of the digitised direction
    dip_deg: float | None = None
    source: str = "user"
    affects: tuple[str, ...] = ("bedrock",)  # sequences displaced by the fault

    def __post_init__(self):
        self.trace = np.asarray(self.trace, dtype=float)[:, :2]

    def segments(self) -> np.ndarray:
        return np.stack([self.trace[:-1], self.trace[1:]], axis=1)


@dataclass
class MapUnitPolygon:
    """A polygon from a geological map (BGS 625k via OGC API, or a user-licensed 50k layer)."""

    code: str  # map / lexicon code, e.g. TAB, THAM, ALV
    kind: str  # bedrock | superficial | artificial | mass_movement
    rings: list[np.ndarray]  # exterior then holes
    source: str = ""
    nominal_scale: int | None = None  # e.g. 625000, 50000
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.rings = [np.asarray(r, dtype=float)[:, :2] for r in self.rings]


@dataclass
class MapContactLine:
    """A mapped boundary between two bedrock units (upper code over lower code)."""

    upper_code: str
    lower_code: str
    line: np.ndarray
    source: str = ""
    nominal_scale: int | None = None

    def __post_init__(self):
        self.line = np.asarray(self.line, dtype=float)[:, :2]
