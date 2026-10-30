"""Enumerations shared across the data model."""

from __future__ import annotations

from enum import Enum, StrEnum  # noqa: F401  (Enum kept for downstream imports)


class LocationKind(StrEnum):
    BOREHOLE = "BH"  # cable percussion, rotary, sonic ... (any drilled hole)
    CPT = "CPT"
    TRIAL_PIT = "TP"
    WINDOW_SAMPLE = "WS"
    DYNAMIC_PROBE = "DP"
    OTHER = "OTHER"


class DataSource(StrEnum):
    AGS4 = "AGS4"
    AGS3 = "AGS3"
    BGS_INDEX = "BGS_INDEX"  # BGS Single Onshore Borehole Index record (no log data)
    BGS_AGS = "BGS_AGS"  # AGS data held by BGS (National Geotechnical Properties DB)
    BGS_DIGITISED = "BGS_DIGITISED"  # contacts digitised by a reviewer from a BGS scan
    USER = "USER"
    SYNTHETIC = "SYNTHETIC"


class ConfidenceClass(StrEnum):
    """The four interpretation classes required on every output."""

    OBSERVED = "Observed"  # at a logged observation
    INTERPOLATED = "Interpolated"  # between primary observations, low model uncertainty
    INFERRED = "Inferred"  # secondary data, map constraints, absence data, moderate sigma
    SPECULATIVE = "Speculative"  # extrapolated / weakly supported / inferred missing layers

    @property
    def rank(self) -> int:
        return {"Observed": 0, "Interpolated": 1, "Inferred": 2, "Speculative": 3}[self.value]


class RAG(StrEnum):
    GREEN = "Green"
    AMBER = "Amber"
    RED = "Red"

    @property
    def rank(self) -> int:
        return {"Green": 0, "Amber": 1, "Red": 2}[self.value]

    @staticmethod
    def worst(values) -> RAG:
        vals = list(values)
        return max(vals, key=lambda r: r.rank) if vals else RAG.GREEN


class InterpretationStatus(StrEnum):
    STATED = "stated"  # logger's formation adopted (consistent with the sequence)
    CLASSIFIED = "classified"  # inferred from the description evidence
    INFERRED = "inferred"  # no usable description: inferred from sequence / neighbours
    OVERRIDDEN = "overridden"  # manual override by a named reviewer
    UNRESOLVED = "unresolved"


class ContactType(StrEnum):
    CONFORMABLE = "conformable"  # observed contact between consecutive units
    EROSIONAL = "erosional"  # e.g. rockhead (superficial on bedrock)
    ABSENT = "absent"  # unit absent: surfaces coincide (zero thickness)
    GE = "ge"  # surface is at or above this elevation
    LE = "le"  # surface is at or below this elevation
    MAP = "map"  # equality from geological map linework (lower weight)


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"info": 0, "warning": 1, "error": 2, "critical": 3}[self.value]


class HSU(StrEnum):
    """Hydrostratigraphic behaviour of a unit (screening level)."""

    AQUIFER = "aquifer"
    AQUITARD = "aquitard"
    AQUICLUDE = "aquiclude"
    VARIABLE = "variable"
    UNKNOWN = "unknown"
