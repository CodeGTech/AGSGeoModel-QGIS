"""Horizontal CRS and vertical datum detection for AGS LOCA data.

Detection is evidence based and always reports its basis and confidence; ambiguous cases (Irish
grids overlap British National Grid numerically) are surfaced to the user in the import wizard
instead of being guessed silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from .reader import AGSFile, parse_float

_GREF_PATTERNS: list[tuple[re.Pattern, int, str]] = [
    (re.compile(r"EPSG\s*[:]?\s*(\d{4,5})", re.I), -1, "explicit EPSG code in LOCA_GREF"),
    # Irish systems first: "Irish National Grid" must not match the British pattern below
    (re.compile(r"\b(ITM|IRISH\s+TRANSVERSE)\b", re.I), 2157, "LOCA_GREF names Irish Transverse Mercator"),
    (re.compile(r"\b(IRISH\s+GRID|TM65|IRISH\s+NATIONAL)\b", re.I), 29903, "LOCA_GREF names the Irish Grid"),
    (
        re.compile(r"\b(OSGB|BNG|BRITISH\s+NATIONAL|OSGB36|OS\s*GB|NATIONAL\s+GRID)\b", re.I),
        27700,
        "LOCA_GREF names the British National Grid",
    ),
]
_UTM = re.compile(r"UTM\s*(?:ZONE)?\s*(\d{1,2})\s*([NS])?", re.I)


@dataclass
class CRSGuess:
    epsg: int | None
    confidence: float
    basis: str
    alternatives: list[int] = field(default_factory=list)
    needs_confirmation: bool = True
    uses_latlon: bool = False

    @property
    def authid(self) -> str | None:
        return f"EPSG:{self.epsg}" if self.epsg else None


@dataclass
class DatumGuess:
    datum: str | None
    basis: str
    mixed: bool = False


def dms_to_decimal(value: str) -> float:
    """Parse AGS DMS ('51:30:26.4') or decimal degrees ('51.5073'); NaN when invalid."""
    s = (value or "").strip()
    if not s:
        return np.nan
    f = parse_float(s)
    if np.isfinite(f):
        return f
    m = re.match(r"^([+-]?)(\d{1,3}):(\d{1,2}):(\d{1,2}(?:\.\d+)?)\s*([NSEW])?$", s, re.I)
    if not m:
        return np.nan
    sign = -1.0 if m.group(1) == "-" or (m.group(5) or "").upper() in ("S", "W") else 1.0
    return sign * (float(m.group(2)) + float(m.group(3)) / 60.0 + float(m.group(4)) / 3600.0)


def detect_crs(ags: AGSFile) -> CRSGuess:
    loca = ags.get("LOCA")
    if loca is None or len(loca) == 0:
        return CRSGuess(None, 0.0, "no LOCA group")
    gref = " ".join(sorted({v for v in loca.column("LOCA_GREF") if v}))
    llz = " ".join(sorted({v for v in loca.column("LOCA_LLZ") if v}))
    text = f"{gref} {llz}".strip()
    if text:
        for rx, epsg, basis in _GREF_PATTERNS:
            m = rx.search(text)
            if m:
                code = int(m.group(1)) if epsg == -1 else epsg
                return CRSGuess(code, 0.95, f"{basis}: {text!r}", needs_confirmation=False)
        m = _UTM.search(text)
        if m:
            zone = int(m.group(1))
            south = (m.group(2) or "N").upper() == "S"
            if re.search(r"ETRS|ETRF", text, re.I):
                code = 25800 + zone
            elif re.search(r"ED\s*50", text, re.I):
                code = 23000 + zone
            else:
                code = (32700 if south else 32600) + zone
            alts = [c for c in (32600 + zone, 25800 + zone, 23000 + zone) if c != code]
            return CRSGuess(code, 0.85, f"UTM zone {zone} named in LOCA_GREF/LOCA_LLZ: {text!r}", alts)
    e = loca.float_column("LOCA_NATE")
    n = loca.float_column("LOCA_NATN")
    ok = np.isfinite(e) & np.isfinite(n)
    if ok.any():
        ee, nn = e[ok], n[ok]
        in_bng = (ee >= 0) & (ee <= 700_000) & (nn >= 0) & (nn <= 1_300_000)
        if in_bng.all():
            alts = []
            if ((ee > 400_000) & (ee < 800_000) & (nn > 500_000) & (nn < 1_000_000)).all():
                alts.append(2157)
            if ((ee < 400_000) & (nn < 500_000)).all():
                alts.append(29903)
            conf = 0.75 if not alts else 0.55
            return CRSGuess(
                27700, conf, "national grid coordinates within British National Grid range (no LOCA_GREF given)", alts
            )
        if ((ee > 160_000) & (ee < 840_000) & (nn > 0) & (nn < 9_400_000)).all():
            return CRSGuess(
                None,
                0.3,
                "coordinates look like UTM but the zone is unknown; specify the CRS",
                [32630, 32631, 25830, 25831],
            )
        return CRSGuess(None, 0.0, "coordinates outside recognised national grids; specify the CRS")
    if loca.has("LOCA_LAT") and loca.has("LOCA_LON"):
        lat = np.array([dms_to_decimal(v) for v in loca.column("LOCA_LAT")])
        lon = np.array([dms_to_decimal(v) for v in loca.column("LOCA_LON")])
        good = np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) <= 90) & (np.abs(lon) <= 180)
        if good.any():
            return CRSGuess(4326, 0.8, "latitude/longitude supplied (assumed WGS84 / ETRS89)", [4258], uses_latlon=True)
    if loca.has("LOCA_LOCX") and np.isfinite(loca.float_column("LOCA_LOCX")).any():
        return CRSGuess(None, 0.0, "only local grid coordinates (LOCA_LOCX/LOCY): supply a transformation")
    return CRSGuess(None, 0.0, "no usable coordinates found")


def detect_vertical_datum(ags: AGSFile, horizontal_epsg: int | None) -> DatumGuess:
    loca = ags.get("LOCA")
    vals = sorted({v.strip() for v in (loca.column("LOCA_DATM") if loca is not None else []) if v.strip()})
    if len(vals) > 1:
        return DatumGuess(None, f"mixed datums {vals}", mixed=True)
    if vals:
        v = vals[0].upper()
        if re.search(r"\bOD\b|ODN|NEWLYN|ORDNANCE", v):
            return DatumGuess("ODN", f"LOCA_DATM = {vals[0]!r}")
        if re.search(r"\bLAT\b|LOWEST ASTRONOMICAL", v):
            return DatumGuess("LAT", f"LOCA_DATM = {vals[0]!r}")
        if re.search(r"\bCD\b|CHART", v):
            return DatumGuess("CD", f"LOCA_DATM = {vals[0]!r}")
        if re.search(r"MSL|MEAN SEA", v):
            return DatumGuess("MSL", f"LOCA_DATM = {vals[0]!r}")
        return DatumGuess(vals[0], f"LOCA_DATM = {vals[0]!r} (not recognised; kept verbatim)")
    if horizontal_epsg == 27700:
        return DatumGuess("ODN", "assumed Ordnance Datum Newlyn (British National Grid, no LOCA_DATM)")
    if horizontal_epsg in (2157, 29903):
        return DatumGuess("OD Malin Head", "assumed Malin Head datum (Irish grid, no LOCA_DATM)")
    return DatumGuess(None, "vertical datum unknown; LOCA_GL values are used as supplied")
