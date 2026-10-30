"""HDD / linear infrastructure alignment geometry.

A bore profile is either supplied explicitly (chainage, elevation pairs — e.g. from a LandXML
profile or the driller's design) or generated from the classic HDD design parameters:

    entry tangent (entry angle) -> sag bend (radius R) -> horizontal run at the design elevation
    -> overbend (radius R) -> exit tangent (exit angle)

Elevations are relative to the project vertical datum. All lengths in metres, angles in degrees
below horizontal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..core.errors import ConfigError
from ..core.geometry import point_at_chainage, polyline_length


@dataclass
class HDDDesign:
    entry_angle_deg: float = 12.0
    exit_angle_deg: float = 10.0
    radius_m: float = 250.0
    design_elevation: float | None = None  # elevation of the horizontal section
    product_od_m: float = 0.25
    ream_factor: float = 1.5
    name: str = "HDD"

    @property
    def reamed_diameter(self) -> float:
        return self.product_od_m * self.ream_factor


@dataclass
class BoreProfile:
    chainage: np.ndarray
    elevation: np.ndarray
    segments: list[dict] = field(default_factory=list)
    feasible: bool = True
    notes: list[str] = field(default_factory=list)

    def elevation_at(self, ch) -> np.ndarray:
        return np.interp(np.asarray(ch, float), self.chainage, self.elevation)

    def slope_at(self, ch) -> np.ndarray:
        c = np.asarray(ch, float)
        h = 0.5
        return (self.elevation_at(c + h) - self.elevation_at(c - h)) / (2 * h)

    @property
    def length(self) -> float:
        return float(self.chainage[-1] - self.chainage[0])

    @property
    def bore_length(self) -> float:
        return float(np.sum(np.hypot(np.diff(self.chainage), np.diff(self.elevation))))


def design_profile(
    plan_length: float, z_entry: float, z_exit: float, design: HDDDesign, step: float = 1.0
) -> BoreProfile:
    a1 = math.radians(design.entry_angle_deg)
    a2 = math.radians(design.exit_angle_deg)
    R = design.radius_m
    if design.design_elevation is None:
        raise ConfigError("design_elevation (horizontal section elevation) is required for a generated profile")
    zd = design.design_elevation
    notes: list[str] = []
    drop1 = z_entry - zd
    drop2 = z_exit - zd
    arc1_dz, arc1_dx = R * (1 - math.cos(a1)), R * math.sin(a1)
    arc2_dz, arc2_dx = R * (1 - math.cos(a2)), R * math.sin(a2)
    feasible = True
    if drop1 < arc1_dz or drop2 < arc2_dz:
        feasible = False
        notes.append("design elevation too shallow for the entry/exit angles and radius (bends overlap the tangents)")
    t1_dx = max(drop1 - arc1_dz, 0.0) / math.tan(a1) if a1 > 0 else 0.0
    t2_dx = max(drop2 - arc2_dz, 0.0) / math.tan(a2) if a2 > 0 else 0.0
    horiz = plan_length - (t1_dx + arc1_dx + arc2_dx + t2_dx)
    if horiz < 0:
        feasible = False
        notes.append(f"alignment too short by {-horiz:.1f} m for the specified angles, radius and depth")
        horiz = 0.0
    segs = [
        {"type": "entry tangent", "from": 0.0, "to": t1_dx},
        {"type": "sag bend", "from": t1_dx, "to": t1_dx + arc1_dx},
        {"type": "horizontal", "from": t1_dx + arc1_dx, "to": t1_dx + arc1_dx + horiz},
        {"type": "overbend", "from": t1_dx + arc1_dx + horiz, "to": t1_dx + arc1_dx + horiz + arc2_dx},
        {"type": "exit tangent", "from": t1_dx + arc1_dx + horiz + arc2_dx, "to": plan_length},
    ]
    ch = np.unique(
        np.concatenate(
            [np.arange(0.0, plan_length, step), [plan_length], [s["from"] for s in segs], [s["to"] for s in segs]]
        )
    )
    ch = ch[(ch >= 0) & (ch <= plan_length)]
    z = np.empty_like(ch)
    c1 = t1_dx + arc1_dx
    c2 = c1 + horiz
    for k, c in enumerate(ch):
        if c <= t1_dx:
            z[k] = z_entry - c * math.tan(a1)
        elif c <= c1:
            # circle centre above the end of the sag bend
            xc, zc = c1, zd + R
            z[k] = zc - math.sqrt(max(R * R - (c - xc) ** 2, 0.0))
        elif c <= c2:
            z[k] = zd
        elif c <= c2 + arc2_dx:
            xc, zc = c2, zd + R
            z[k] = zc - math.sqrt(max(R * R - (c - xc) ** 2, 0.0))
        else:
            z[k] = z_exit - (plan_length - c) * math.tan(a2)
    return BoreProfile(ch, z, segs, feasible, notes)


def explicit_profile(chainage, elevation) -> BoreProfile:
    c = np.asarray(chainage, float)
    z = np.asarray(elevation, float)
    order = np.argsort(c)
    return BoreProfile(c[order], z[order], [{"type": "explicit", "from": float(c.min()), "to": float(c.max())}])


@dataclass
class Alignment:
    """Plan alignment (polyline) plus vertical bore profile."""

    line: np.ndarray
    profile: BoreProfile
    design: HDDDesign
    name: str = "Alignment"

    def __post_init__(self):
        self.line = np.asarray(self.line, float)[:, :2]

    @property
    def plan_length(self) -> float:
        return polyline_length(self.line)

    def stations(self, spacing: float = 1.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        L = self.plan_length
        ch = np.unique(np.concatenate([np.arange(0.0, L, spacing), [L]]))
        xy = point_at_chainage(self.line, ch)
        return ch, xy, self.profile.elevation_at(ch)
