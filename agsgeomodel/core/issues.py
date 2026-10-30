"""A single issue type shared by AGS validation, geological validation and model diagnostics."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from ..models.enums import Severity


@dataclass
class Issue:
    rule: str  # e.g. "AGS4-10c", "E04", "GEO-INV-01"
    severity: Severity
    message: str
    category: str = "ags"  # ags | engineering | geology | model | bgs
    group: str | None = None
    heading: str | None = None
    line: int | None = None
    location_ids: list[str] = field(default_factory=list)
    depth: float | None = None
    explanations: list[str] = field(default_factory=list)  # candidate geological explanations
    suggestion: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    status: str = "open"  # open | accepted | dismissed (reviewer decision)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


@dataclass
class IssueLog:
    issues: list[Issue] = field(default_factory=list)

    def add(self, *issues: Issue) -> None:
        self.issues.extend(issues)

    def extend(self, issues: Iterable[Issue]) -> None:
        self.issues.extend(issues)

    def __iter__(self):
        return iter(self.issues)

    def __len__(self) -> int:
        return len(self.issues)

    def filter(
        self,
        *,
        severity: Severity | None = None,
        category: str | None = None,
        rule_prefix: str | None = None,
        location_id: str | None = None,
    ) -> list[Issue]:
        out = self.issues
        if severity is not None:
            out = [i for i in out if i.severity == severity]
        if category is not None:
            out = [i for i in out if i.category == category]
        if rule_prefix is not None:
            out = [i for i in out if i.rule.startswith(rule_prefix)]
        if location_id is not None:
            out = [i for i in out if location_id in i.location_ids]
        return out

    def counts(self) -> dict[str, int]:
        c = Counter(i.severity.value for i in self.issues)
        return {s.value: c.get(s.value, 0) for s in Severity}

    @property
    def worst(self) -> Severity | None:
        return max((i.severity for i in self.issues), key=lambda s: s.rank, default=None)

    def to_list(self) -> list[dict[str, Any]]:
        return [i.to_dict() for i in self.issues]
