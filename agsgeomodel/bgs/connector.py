"""BGSGeoConnector: regional geological context for a project from British Geological Survey services.

Workflow (as in the plugin): import AGS -> :meth:`BGSGeoConnector.analyse` the project area (extent,
buffer, geological setting, province, local stratigraphy) -> retrieve map units, faults, landslides,
map-sheet citations and borehole records -> rank boreholes (:mod:`.discovery`) -> merge accepted
records into the project.

All geometry is returned in the project CRS. Data are OGL-licensed BGS products; every result carries
the attribution string. BGS map data at 1:625 000 are generalised: their positional tolerance
(~0.5 mm at map scale, i.e. ~300 m) is attached to each polygon and honoured by the model builder.
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import re
import urllib.parse
import zipfile
from dataclasses import dataclass, field

import numpy as np

from ..core.errors import ConnectorError
from ..core.geometry import circle_ring, convex_hull, points_in_polygon
from ..core.transform import get_transformer
from ..models.entities import Fault, MapUnitPolygon
from .http import HttpCache
from .services import AGS_API, ATTRIBUTION, COLLECTIONS, LEXICON_HTML, LEXICON_LD, OGC_API, WMS_LAYERS

LONDON_BASIN_CODES = {"LC", "THAM", "LMBE", "TAB", "CK", "HWH", "BRBA", "WHCK", "GRCK", "BGS"}
GLACIAL_CODES = {"TILL", "LOFT", "GSG", "DMTN"}


@dataclass
class AreaAnalysis:
    extent: tuple[float, float, float, float]
    buffer_m: float
    buffer_polygon: np.ndarray
    bbox_wgs84: tuple[float, float, float, float]
    bedrock_units: list[dict] = field(default_factory=list)
    superficial_units: list[dict] = field(default_factory=list)
    n_faults: int = 0
    landslides: list[dict] = field(default_factory=list)
    map_sheets: list[dict] = field(default_factory=list)
    suggested_province: str | None = None
    province_basis: str = ""
    local_stratigraphy: list[str] = field(default_factory=list)
    setting: str = ""
    attribution: str = ""
    transform_note: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["buffer_polygon"] = self.buffer_polygon.tolist()
        return d


def buffer_polygon(points: np.ndarray, distance: float, n: int = 32) -> np.ndarray:
    """Exact buffer of the convex hull of ``points`` (hull of discs at the hull vertices)."""
    p = np.asarray(points, float).reshape(-1, 2)
    hull = convex_hull(p) if len(p) > 2 else p
    discs = np.vstack([circle_ring(x, y, distance, n)[:-1] for x, y in hull])
    ring = convex_hull(discs)
    return np.vstack([ring, ring[:1]])


class BGSGeoConnector:
    def __init__(
        self,
        crs: str = "EPSG:27700",
        cache: HttpCache | None = None,
        ogc_api: str = OGC_API,
        ags_api: str = AGS_API,
        page_size: int = 500,
        max_features: int = 20000,
    ):
        self.crs = crs
        self.cache = cache or HttpCache()
        self.ogc = ogc_api.rstrip("/")
        self.ags_api = ags_api.rstrip("/")
        self.page_size = page_size
        self.max_features = max_features
        self.to_wgs, note1 = get_transformer(crs, "EPSG:4326", allow_approximate=True)
        self.from_wgs, note2 = get_transformer("EPSG:4326", crs, allow_approximate=True)
        self.transform_note = note1 if note1 == note2 else f"{note1} / {note2}"

    # ------------------------------------------------------------------ OGC API
    def items(
        self, collection: str, bbox_wgs84: tuple[float, float, float, float], limit: int | None = None
    ) -> list[dict]:
        """All features of a collection intersecting a CRS84 bbox, following ``next`` links."""
        q = {"f": "json", "bbox": ",".join(f"{v:.6f}" for v in bbox_wgs84), "limit": str(limit or self.page_size)}
        url = f"{self.ogc}/collections/{collection}/items?{urllib.parse.urlencode(q)}"
        feats: list[dict] = []
        seen = set()
        while url and url not in seen and len(feats) < self.max_features:
            seen.add(url)
            body = self.cache.get(url, {"Accept": "application/geo+json"})
            try:
                data = json.loads(body)
            except json.JSONDecodeError as exc:
                raise ConnectorError(f"invalid JSON from {url}") from exc
            page = data.get("features", [])
            feats.extend(page)
            url = next((l.get("href") for l in data.get("links", []) if l.get("rel") == "next"), None)
            if not page:
                break
        return feats

    def _to_project(self, coords):
        a = np.asarray(coords, float)
        x, y = self.from_wgs(a[:, 0], a[:, 1])
        return np.column_stack([x, y])

    def _rings(self, geom: dict) -> list[list[np.ndarray]]:
        t = geom.get("type")
        if t == "Polygon":
            polys = [geom["coordinates"]]
        elif t == "MultiPolygon":
            polys = geom["coordinates"]
        else:
            return []
        return [[self._to_project(r) for r in poly] for poly in polys]

    # ------------------------------------------------------------------ retrieval
    def bbox_for(self, points: np.ndarray, buffer_m: float) -> tuple[np.ndarray, tuple[float, float, float, float]]:
        poly = buffer_polygon(points, buffer_m)
        lon, lat = self.to_wgs(poly[:, 0], poly[:, 1])
        pad = 0.002
        return poly, (float(lon.min()) - pad, float(lat.min()) - pad, float(lon.max()) + pad, float(lat.max()) + pad)

    def map_units(self, bbox_wgs84, kind: str = "bedrock") -> list[MapUnitPolygon]:
        coll = COLLECTIONS["bedrock_625k" if kind == "bedrock" else "superficial_625k"][0]
        out = []
        for f in self.items(coll, bbox_wgs84):
            p = f.get("properties", {})
            for rings in self._rings(f.get("geometry") or {}):
                out.append(
                    MapUnitPolygon(
                        code=(p.get("lex") or "").strip(),
                        kind=kind,
                        rings=rings,
                        source=f"BGS Geology 625k ({coll})",
                        nominal_scale=625000,
                        attributes={
                            k: p.get(k)
                            for k in (
                                "lex_d",
                                "rcs",
                                "rcs_d",
                                "rank",
                                "fm_eq",
                                "gp_eq",
                                "max_period",
                                "min_period",
                                "max_time_y",
                                "min_time_y",
                                "max_age",
                                "map_code",
                                "version",
                                "released",
                            )
                        },
                    )
                )
        return out

    def faults(self, bbox_wgs84) -> list[Fault]:
        out = []
        for f in self.items(COLLECTIONS["faults_625k"][0], bbox_wgs84):
            g = f.get("geometry") or {}
            lines = (
                [g["coordinates"]]
                if g.get("type") == "LineString"
                else g.get("coordinates", [])
                if g.get("type") == "MultiLineString"
                else []
            )
            p = f.get("properties", {})
            for k, ln in enumerate(lines):
                xy = self._to_project(ln)
                if len(xy) >= 2:
                    out.append(
                        Fault(
                            name=(p.get("fltname_d") or p.get("feature_d") or "BGS fault").strip()
                            + (f" #{p.get('objectid')}" if p.get("objectid") is not None else "")
                            + (f".{k}" if len(lines) > 1 else ""),
                            trace=xy,
                            throw_m=None,
                            source="BGS Geology 625k faults",
                        )
                    )
        return out

    def landslides(self, bbox_wgs84) -> list[dict]:
        out = []
        for f in self.items(COLLECTIONS["landslides"][0], bbox_wgs84):
            p = f.get("properties", {})
            out.append(
                {
                    "id": p.get("ls_id"),
                    "name": p.get("landslide_name"),
                    "x": p.get("easting"),
                    "y": p.get("northing"),
                    "precision_m": p.get("plus_or_minus_metres"),
                    "first_known": p.get("first_known_date_year"),
                    "locality": p.get("locality_details"),
                }
            )
        return out

    def map_sheets(self, bbox_wgs84, scale_key: str = "maps_250k") -> list[dict]:
        out = []
        for f in self.items(COLLECTIONS[scale_key][0], bbox_wgs84):
            p = f.get("properties", {})
            out.append(
                {
                    "title": p.get("full_title"),
                    "series": p.get("map_series_formal_title"),
                    "edition": p.get("map_series_edition"),
                    "scale": p.get("geol_scale"),
                    "published": p.get("publication_date"),
                    "authors": p.get("author_statement"),
                    "publication": p.get("publication_statement"),
                    "scan_url": p.get("scan_url"),
                }
            )
        return out

    def lexicon(self, code: str, fetch: bool = False) -> dict:
        """Lexicon links (always) and, when ``fetch``, key fields parsed from the linked-data record."""
        info = {"code": code, "url": LEXICON_HTML.format(code=code), "linked_data": LEXICON_LD.format(code=code)}
        if not fetch:
            return info
        try:
            ttl = self.cache.get(info["linked_data"], {"Accept": "text/turtle"}).decode("utf-8", "replace")
        except ConnectorError as exc:
            info["error"] = str(exc)
            return info
        info.update(parse_lexicon_turtle(ttl))
        return info

    # ------------------------------------------------------------------ analysis
    def analyse(
        self, project, buffer_m: float = 500.0, include_landslides: bool = True, include_sheets: bool = True
    ) -> tuple[AreaAnalysis, dict]:
        """Analyse the project area. Returns ``(analysis, data)`` where ``data`` holds the retrieved
        map units, faults and borehole index features for later use."""
        pts = project.xy()
        if len(pts) == 0:
            raise ConnectorError("project has no located exploratory holes")
        poly, bbox = self.bbox_for(pts, buffer_m)
        ext = (float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max()))
        an = AreaAnalysis(
            ext,
            buffer_m,
            poly,
            bbox,
            attribution=ATTRIBUTION.format(year=_dt.date.today().year),
            transform_note=self.transform_note,
        )
        bed = self.map_units(bbox, "bedrock")
        sup = self.map_units(bbox, "superficial")
        flt = self.faults(bbox)
        an.n_faults = len(flt)
        an.bedrock_units = _summarise_units(bed, poly)
        an.superficial_units = _summarise_units(sup, poly)
        codes_b = {u["code"] for u in an.bedrock_units}
        codes_s = {u["code"] for u in an.superficial_units}
        if codes_b & LONDON_BASIN_CODES:
            if codes_s & GLACIAL_CODES:
                an.suggested_province = "east_anglia_glacial"
                an.province_basis = (
                    f"Palaeogene/Chalk bedrock ({', '.join(sorted(codes_b & LONDON_BASIN_CODES))}) "
                    f"with glacial deposits ({', '.join(sorted(codes_s & GLACIAL_CODES))})"
                )
            else:
                an.suggested_province = "london_basin"
                an.province_basis = (
                    f"London Basin bedrock units mapped: {', '.join(sorted(codes_b & LONDON_BASIN_CODES))}"
                )
        else:
            an.notes.append(
                "no bundled province column matches the mapped bedrock: create a project column (a "
                "province JSON file, set as settings.province in agsgeomodel_project.json) before "
                "relying on the GeoReasoner"
            )
        units = sorted(an.bedrock_units, key=lambda u: u.get("min_time_y") or 0)
        an.local_stratigraphy = [u["code"] for u in sorted(an.superficial_units, key=lambda u: -u["area_fraction"])] + [
            u["code"] for u in units
        ]
        an.setting = _setting_text(an)
        if include_landslides:
            try:
                an.landslides = self.landslides(bbox)
            except ConnectorError as exc:
                an.notes.append(f"landslide index unavailable: {exc}")
        if include_sheets:
            try:
                an.map_sheets = self.map_sheets(bbox)
            except ConnectorError as exc:
                an.notes.append(f"map sheet index unavailable: {exc}")
        data = {"bedrock": bed, "superficial": sup, "faults": flt}
        return an, data

    # ------------------------------------------------------------------ boreholes
    def borehole_records(self, bbox_wgs84) -> list[dict]:
        recs = []
        for f in self.items(COLLECTIONS["boreholes"][0], bbox_wgs84):
            p = f.get("properties", {})
            recs.append({"collection": "onshoreboreholeindex", **p, "_geometry": f.get("geometry")})
        for f in self.items(COLLECTIONS["ags_boreholes"][0], bbox_wgs84):
            p = f.get("properties", {})
            recs.append({"collection": "agsboreholeindex", **p, "_geometry": f.get("geometry")})
        return recs

    def ags_export(self, bgs_loca_ids: list[str], batch: int = 50) -> list[tuple[str, bytes]]:
        """Download AGS4 data for BGS LOCA IDs (AGS API ``/ags_export/``, max 50 per request).

        Returns ``[(file_name, ags_bytes)]`` extracted from the returned ZIP archives."""
        out: list[tuple[str, bytes]] = []
        for i in range(0, len(bgs_loca_ids), batch):
            ids = ";".join(str(x) for x in bgs_loca_ids[i : i + batch])
            url = f"{self.ags_api}/ags_export/?{urllib.parse.urlencode({'bgs_loca_id': ids})}"
            body = self.cache.get(url, {"Accept": "application/zip"})
            if body[:2] != b"PK":
                raise ConnectorError(f"AGS export did not return a ZIP archive: {body[:120]!r}")
            with zipfile.ZipFile(io.BytesIO(body)) as zf:
                for n in zf.namelist():
                    if n.lower().endswith(".ags"):
                        out.append((n, zf.read(n)))
        return out

    def ags_log_url(self, bgs_loca_id: str) -> str:
        return f"{self.ags_api}/ags_log/?{urllib.parse.urlencode({'bgs_loca_id': bgs_loca_id})}"

    @staticmethod
    def wms_layers() -> dict[str, tuple[str, str]]:
        return dict(WMS_LAYERS)


def _summarise_units(units: list[MapUnitPolygon], poly: np.ndarray, n_samples: int = 40) -> list[dict]:
    """Codes present within the buffer polygon with their approximate area fraction (point sampling)."""
    xmin, ymin = poly.min(axis=0)
    xmax, ymax = poly.max(axis=0)
    xs = np.linspace(xmin, xmax, n_samples)
    ys = np.linspace(ymin, ymax, n_samples)
    grid = np.array([[x, y] for x in xs for y in ys])
    grid = grid[points_in_polygon(grid, [poly])]
    out: dict[str, dict] = {}
    for u in units:
        frac = float(points_in_polygon(grid, u.rings).mean()) if len(grid) else 0.0
        if frac <= 0 and not len(grid):
            continue
        d = out.setdefault(
            u.code,
            {
                "code": u.code,
                "name": (u.attributes.get("lex_d") or u.code).title(),
                "rank": u.attributes.get("rank"),
                "lithology": u.attributes.get("rcs_d"),
                "max_period": u.attributes.get("max_period"),
                "min_period": u.attributes.get("min_period"),
                "max_time_y": u.attributes.get("max_time_y"),
                "min_time_y": u.attributes.get("min_time_y"),
                "area_fraction": 0.0,
                "lexicon_url": LEXICON_HTML.format(code=u.code),
            },
        )
        d["area_fraction"] += frac
    return sorted((v for v in out.values() if v["area_fraction"] > 0), key=lambda v: -v["area_fraction"])


def _setting_text(an: AreaAnalysis) -> str:
    parts = []
    if an.bedrock_units:
        b = ", ".join(
            f"{u['name']} ({u['code']}, {u.get('max_period') or ''})".replace(", )", ")") for u in an.bedrock_units[:5]
        )
        parts.append(f"Bedrock mapped within {an.buffer_m:.0f} m: {b}.")
    if an.superficial_units:
        s = ", ".join(f"{u['name']} ({u['code']})" for u in an.superficial_units[:5])
        parts.append(f"Superficial deposits: {s}.")
    if an.n_faults:
        parts.append(f"{an.n_faults} mapped fault segment(s) in the search area (625k generalised mapping).")
    if an.suggested_province:
        parts.append(f"Suggested stratigraphic province: {an.suggested_province} ({an.province_basis}).")
    parts.append("Regional (1:625 000) context only: check against the 1:50 000 map and sheet explanation.")
    return " ".join(parts)


def parse_lexicon_turtle(ttl: str) -> dict:
    """Extract a few fields from a BGS Lexicon linked-data record (Turtle). Best effort."""

    def first(pat):
        m = re.search(pat, ttl, re.S)
        return m.group(1).strip() if m else None

    out = {
        "name": first(r'skos:prefLabel\s+"([^"]+)"'),
        "parent": first(r"skos:broader\s+<[^>]*/NamedRockUnit/([A-Z0-9]+)>"),
        "oldest_ma": first(r'hasOldestAgeValue\s+"?([0-9.eE+-]+)'),
        "youngest_ma": first(r'hasYoungestAgeValue\s+"?([0-9.eE+-]+)'),
        "lithology": first(r'hasLithologyDescription\s+"([^"]+)"'),
        "definition": first(r'skos:definition\s+"([^"]{0,600})'),
    }
    for k in ("oldest_ma", "youngest_ma"):
        if out[k] is not None:
            try:
                out[k] = float(out[k])
            except ValueError:
                out[k] = None
    return {k: v for k, v in out.items() if v is not None}
