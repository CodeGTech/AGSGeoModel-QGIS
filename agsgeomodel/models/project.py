"""The :class:`Project` container: all observations for a site plus interpretation state."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .entities import (
    CoreRun,
    CPTSounding,
    DrillingSection,
    Fault,
    Interval,
    LabResult,
    Location,
    MapContactLine,
    MapUnitPolygon,
    MonitoringPoint,
    Sample,
    SPTRecord,
    WaterStrike,
)
from .enums import DataSource, LocationKind


@dataclass
class Project:
    name: str = "Untitled"
    crs: str | None = None
    vertical_datum: str | None = None
    locations: dict[str, Location] = field(default_factory=dict)
    intervals: list[Interval] = field(default_factory=list)
    drilling: list[DrillingSection] = field(default_factory=list)
    spt: list[SPTRecord] = field(default_factory=list)
    cpt: list[CPTSounding] = field(default_factory=list)
    water_strikes: list[WaterStrike] = field(default_factory=list)
    monitoring: list[MonitoringPoint] = field(default_factory=list)
    samples: list[Sample] = field(default_factory=list)
    lab: list[LabResult] = field(default_factory=list)
    core: list[CoreRun] = field(default_factory=list)
    faults: list[Fault] = field(default_factory=list)
    map_units: list[MapUnitPolygon] = field(default_factory=list)
    map_contacts: list[MapContactLine] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    source_files: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------ queries
    def intervals_by_location(self) -> dict[str, list[Interval]]:
        out: dict[str, list[Interval]] = defaultdict(list)
        for iv in self.intervals:
            out[iv.location_id].append(iv)
        for lst in out.values():
            lst.sort(key=lambda i: (i.top, i.base))
        return dict(out)

    def locations_of_kind(self, *kinds: LocationKind) -> list[Location]:
        return [loc for loc in self.locations.values() if loc.kind in kinds]

    def located(self, ids: Iterable[str] | None = None) -> list[Location]:
        """Locations with coordinates and ground level (usable for modelling)."""
        sel = self.locations.values() if ids is None else (self.locations[i] for i in ids if i in self.locations)
        return [loc for loc in sel if np.isfinite(loc.x) and np.isfinite(loc.y) and loc.ground_level is not None]

    def xy(self, ids: Iterable[str] | None = None) -> np.ndarray:
        locs = self.located(ids)
        return np.array([[loc.x, loc.y] for loc in locs]) if locs else np.zeros((0, 2))

    def extent(self, buffer: float = 0.0) -> tuple[float, float, float, float]:
        p = self.xy()
        if len(p) == 0:
            raise ValueError("project has no located points")
        return (
            float(p[:, 0].min() - buffer),
            float(p[:, 1].min() - buffer),
            float(p[:, 0].max() + buffer),
            float(p[:, 1].max() + buffer),
        )

    def max_depth(self) -> float:
        depths = [loc.final_depth for loc in self.locations.values() if loc.final_depth]
        depths += [iv.base for iv in self.intervals]
        return max(depths) if depths else 0.0

    def summary(self) -> dict[str, int]:
        kinds: dict[str, int] = defaultdict(int)
        for loc in self.locations.values():
            kinds[f"locations_{loc.kind.value}"] += 1
        return {
            "locations": len(self.locations),
            **kinds,
            "intervals": len(self.intervals),
            "spt": len(self.spt),
            "cpt_soundings": len(self.cpt),
            "cpt_readings": int(sum(len(c.depth) for c in self.cpt)),
            "water_strikes": len(self.water_strikes),
            "monitoring_points": len(self.monitoring),
            "samples": len(self.samples),
            "lab_results": len(self.lab),
            "faults": len(self.faults),
            "map_polygons": len(self.map_units),
        }

    # ------------------------------------------------------------------ editing
    def merge(self, other: Project, id_prefix: str = "", source: DataSource | None = None) -> list[str]:
        """Merge another project's locations and data (e.g. accepted BGS records).

        Returns the list of merged location ids. ``id_prefix`` avoids identifier clashes.
        """
        mapping: dict[str, str] = {}
        for loc in other.locations.values():
            new_id = f"{id_prefix}{loc.id}"
            if new_id in self.locations:
                raise ValueError(f"duplicate location id {new_id!r}; use an id_prefix")
            mapping[loc.id] = new_id
            loc.id = new_id
            if source is not None:
                loc.source = source
            self.locations[new_id] = loc
        for coll in ("intervals", "drilling", "spt", "cpt", "water_strikes", "monitoring", "samples", "lab", "core"):
            for rec in getattr(other, coll):
                if hasattr(rec, "location_id"):
                    rec.location_id = mapping.get(rec.location_id, rec.location_id)
                if source is not None and hasattr(rec, "source"):
                    rec.source = source
                getattr(self, coll).append(rec)
        self.faults.extend(other.faults)
        self.map_units.extend(other.map_units)
        self.map_contacts.extend(other.map_contacts)
        self.source_files.extend(other.source_files)
        return list(mapping.values())

    def remove_locations(self, ids: Iterable[str]) -> None:
        ids = set(ids)
        for i in ids:
            self.locations.pop(i, None)
        for coll in ("intervals", "drilling", "spt", "cpt", "water_strikes", "monitoring", "samples", "lab", "core"):
            setattr(self, coll, [r for r in getattr(self, coll) if r.location_id not in ids])
