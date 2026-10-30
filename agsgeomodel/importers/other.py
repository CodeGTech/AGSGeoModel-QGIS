"""Other inputs: flat CSV / Excel borehole tables, AGS-in-Excel, LAS (well logs or LiDAR), LandXML,
and DTM rasters (ESRI ASCII grid, GeoTIFF).
"""

from __future__ import annotations

import csv
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..core.errors import AGSGeoModelError, DependencyMissing
from ..core.grid import Grid2D
from ..core.optional import optional_import, require
from ..models.entities import Interval, Location
from ..models.enums import DataSource
from ..models.project import Project
from .ags.mapper import classify_location_type
from .ags.reader import AGSFile, AGSTable, parse_float

LOCATION_FIELDS = {
    "id": ("LOCA_ID", "location_id", "id", "hole_id", "borehole", "bh"),
    "x": ("LOCA_NATE", "x", "easting", "east"),
    "y": ("LOCA_NATN", "y", "northing", "north"),
    "gl": ("LOCA_GL", "ground_level", "gl", "elevation", "z"),
    "depth": ("LOCA_FDEP", "final_depth", "depth"),
    "type": ("LOCA_TYPE", "type", "kind"),
}
INTERVAL_FIELDS = {
    "id": ("LOCA_ID", "location_id", "id", "hole_id", "borehole", "bh"),
    "top": ("GEOL_TOP", "top", "depth_from", "from"),
    "base": ("GEOL_BASE", "base", "depth_to", "to"),
    "desc": ("GEOL_DESC", "description", "desc", "lithology"),
    "code": ("GEOL_GEOL", "geology_code", "code", "unit"),
    "bgs": ("GEOL_BGS", "bgs_code", "lexicon"),
}


def _pick(row: dict, names) -> str:
    low = {k.lower(): v for k, v in row.items()}
    for n in names:
        if n.lower() in low and low[n.lower()] not in (None, ""):
            return str(low[n.lower()])
    return ""


def project_from_tables(
    locations: list[dict], intervals: list[dict], name: str = "tabular", crs: str | None = "EPSG:27700"
) -> Project:
    p = Project(name, crs)
    for r in locations:
        lid = _pick(r, LOCATION_FIELDS["id"])
        if not lid:
            continue
        x, y = parse_float(_pick(r, LOCATION_FIELDS["x"])), parse_float(_pick(r, LOCATION_FIELDS["y"]))
        gl, fd = parse_float(_pick(r, LOCATION_FIELDS["gl"])), parse_float(_pick(r, LOCATION_FIELDS["depth"]))
        t = _pick(r, LOCATION_FIELDS["type"])
        p.locations[lid] = Location(
            lid,
            x,
            y,
            gl if np.isfinite(gl) else None,
            fd if np.isfinite(fd) else None,
            classify_location_type(t),
            t,
            DataSource.USER,
        )
    for r in intervals:
        lid = _pick(r, INTERVAL_FIELDS["id"])
        a, b = parse_float(_pick(r, INTERVAL_FIELDS["top"])), parse_float(_pick(r, INTERVAL_FIELDS["base"]))
        if lid and np.isfinite(a) and np.isfinite(b) and b > a:
            p.intervals.append(
                Interval(
                    lid,
                    a,
                    b,
                    _pick(r, INTERVAL_FIELDS["desc"]),
                    geology_code=_pick(r, INTERVAL_FIELDS["code"]),
                    bgs_code=_pick(r, INTERVAL_FIELDS["bgs"]),
                    source=DataSource.USER,
                )
            )
    return p


def read_csv_rows(path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def read_csv_project(locations_csv, intervals_csv, crs: str = "EPSG:27700") -> Project:
    return project_from_tables(
        read_csv_rows(locations_csv), read_csv_rows(intervals_csv), Path(locations_csv).stem, crs
    )


def read_excel(path) -> Project | AGSFile:
    """Excel input: either AGS-in-Excel (one sheet per 4-letter group with HEADING/UNIT/TYPE rows, as
    produced by the AGS tools) -> :class:`AGSFile`, or flat sheets named 'locations' and 'intervals'
    -> :class:`Project`."""
    openpyxl = require("openpyxl", "Excel import")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    names = wb.sheetnames
    groups = [n for n in names if re.fullmatch(r"[A-Z0-9]{4}", n)]
    if groups:
        tables = {}
        for g in groups:
            rows = [[("" if v is None else str(v)) for v in r] for r in wb[g].iter_rows(values_only=True)]
            rows = [r for r in rows if any(c != "" for c in r)]
            if not rows:
                continue
            hdr = rows[0]
            heads = hdr[1:] if hdr[0] == "HEADING" else hdr
            units = types = None
            data = []
            for r in rows[1:]:
                vals = r[1:] if hdr[0] == "HEADING" else r
                tag = r[0] if hdr[0] == "HEADING" else "DATA"
                if tag == "UNIT":
                    units = vals
                elif tag == "TYPE":
                    types = vals
                else:
                    data.append((vals + [""] * len(heads))[: len(heads)])
            tables[g] = AGSTable(
                g, heads, units or [""] * len(heads), types or [""] * len(heads), data, list(range(len(data)))
            )
        return AGSFile(
            tables,
            format="AGS4",
            version=tables.get("TRAN").column("TRAN_AGS")[0] if "TRAN" in tables and tables["TRAN"].rows else "unknown",
            source_name=Path(path).name,
        )
    low = {n.lower(): n for n in names}
    if "locations" in low and "intervals" in low:

        def rows(sheet):
            it = wb[sheet].iter_rows(values_only=True)
            hdr = [str(h) for h in next(it)]
            return [dict(zip(hdr, r)) for r in it]

        return project_from_tables(rows(low["locations"]), rows(low["intervals"]), Path(path).stem)
    raise AGSGeoModelError("Excel workbook is neither AGS-in-Excel nor has 'locations' and 'intervals' sheets")


# ============================================================================ LAS
@dataclass
class WellLog:
    well: str
    curves: dict[str, np.ndarray]
    units: dict[str, str]
    depth_curve: str
    info: dict[str, str] = field(default_factory=dict)


def read_las(path) -> WellLog | tuple[Grid2D, np.ndarray]:
    """LAS: Log ASCII Standard 2.0 well log, or ASPRS LiDAR (binary, 'LASF') gridded to a ground DTM."""
    p = Path(path)
    head = p.read_bytes()[:4]
    if head == b"LASF":
        return lidar_to_dtm(p)
    text = p.read_text(encoding="utf-8", errors="replace").splitlines()
    section, info, curves, units, data, wrap = None, {}, [], {}, [], False
    for line in text:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("~"):
            section = s[1].upper()
            continue
        if section in ("V", "W", "C", "P"):
            m = re.match(r"^([^.\s]+)\s*\.([^\s]*)\s+([^:]*):(.*)$", s)
            if not m:
                continue
            mnem, unit, val, desc = (g.strip() for g in m.groups())
            if section == "V" and mnem.upper() == "WRAP":
                wrap = val.upper().startswith("Y")
            if section in ("V", "W", "P"):
                info[mnem.upper()] = val
            else:
                curves.append(mnem)
                units[mnem] = unit
        elif section == "A":
            data.extend(float(v) for v in s.split())
    if not curves:
        raise AGSGeoModelError("LAS file has no ~C curve section")
    if wrap:
        raise AGSGeoModelError("wrapped LAS files are not supported; re-export unwrapped")
    arr = np.array(data, float)
    arr = arr[: (len(arr) // len(curves)) * len(curves)].reshape(-1, len(curves))
    null = parse_float(info.get("NULL", "-999.25"))
    arr = np.where(np.isclose(arr, null), np.nan, arr)
    return WellLog(info.get("WELL", p.stem), {c: arr[:, k] for k, c in enumerate(curves)}, units, curves[0], info)


def lidar_to_dtm(path, cell: float = 2.0, ground_class: int = 2) -> tuple[Grid2D, np.ndarray]:
    laspy = optional_import("laspy")
    if laspy is None:
        raise DependencyMissing("laspy", "gridding LiDAR LAS files (or use QGIS point cloud tools to make a DTM)")
    las = laspy.read(str(path))
    m = np.asarray(las.classification) == ground_class
    if not m.any():
        m = np.ones(len(las.x), dtype=bool)
    x, y, z = np.asarray(las.x)[m], np.asarray(las.y)[m], np.asarray(las.z)[m]
    g = Grid2D.from_extent(float(x.min()), float(y.min()), float(x.max()), float(y.max()), cell)
    i = np.clip(((x - g.x0) / g.dx + 0.5).astype(int), 0, g.nx - 1)
    j = np.clip(((y - g.y0) / g.dy + 0.5).astype(int), 0, g.ny - 1)
    total = np.zeros(g.shape)
    count = np.zeros(g.shape)
    np.add.at(total, (j, i), z)
    np.add.at(count, (j, i), 1)
    return g, np.where(count > 0, total / np.maximum(count, 1), np.nan)


# ============================================================================ LandXML
def _strip(tag: str) -> str:
    return tag.split("}", 1)[-1]


def read_landxml(path) -> dict:
    """Surfaces (TIN) and alignments (plan geometry + vertical profile) from LandXML 1.x.

    Northing/easting order follows LandXML (``<PntList2D>``/``<Start>`` are "northing easting").
    """
    root = ET.parse(path).getroot()
    out = {"surfaces": {}, "alignments": {}}
    for el in root.iter():
        t = _strip(el.tag)
        if t == "Surface":
            pts, faces = {}, []
            for e in el.iter():
                tt = _strip(e.tag)
                if tt == "P":
                    n, ea, z = (float(v) for v in e.text.split()[:3])
                    pts[e.get("id")] = (ea, n, z)
                elif tt == "F" and e.text:
                    faces.append(e.text.split()[:3])
            ids = list(pts)
            index = {k: i for i, k in enumerate(ids)}
            V = np.array([pts[k] for k in ids])
            F = np.array([[index[a] for a in f] for f in faces if all(a in index for a in f)], dtype=int)
            out["surfaces"][el.get("name", f"surface{len(out['surfaces'])}")] = (V, F)
        elif t == "Alignment":
            name = el.get("name", f"alignment{len(out['alignments'])}")
            plan: list[tuple[float, float]] = []
            profile: list[tuple[float, float]] = []
            for e in el.iter():
                tt = _strip(e.tag)
                if tt == "Line":
                    s, en = _pt(e, "Start"), _pt(e, "End")
                    if s and en:
                        plan += [s, en]
                elif tt == "Curve":
                    plan += _arc(e)
                elif tt == "Spiral":
                    s, en = _pt(e, "Start"), _pt(e, "End")
                    if s and en:
                        plan += [s, en]
                elif tt in ("PVI", "CircCurve", "ParaCurve"):
                    if e.text and len(e.text.split()) >= 2:
                        ch, z = (float(v) for v in e.text.split()[:2])
                        profile.append((ch, z))
            dedup = [plan[0]] if plan else []
            for q in plan[1:]:
                if math.hypot(q[0] - dedup[-1][0], q[1] - dedup[-1][1]) > 1e-6:
                    dedup.append(q)
            out["alignments"][name] = {"line": np.array(dedup), "profile": np.array(sorted(profile))}
    return out


def _pt(el, child: str):
    for e in el:
        if _strip(e.tag) == child and e.text:
            n, ea = (float(v) for v in e.text.split()[:2])
            return (ea, n)
    return None


def _arc(el, n: int = 16) -> list[tuple[float, float]]:
    s, c, en = _pt(el, "Start"), _pt(el, "Center"), _pt(el, "End")
    if not (s and c and en):
        return []
    rot = el.get("rot", "cw")
    a0 = math.atan2(s[1] - c[1], s[0] - c[0])
    a1 = math.atan2(en[1] - c[1], en[0] - c[0])
    if rot == "cw" and a1 > a0:
        a1 -= 2 * math.pi
    if rot != "cw" and a1 < a0:
        a1 += 2 * math.pi
    r = math.hypot(s[0] - c[0], s[1] - c[1])
    return [(c[0] + r * math.cos(a), c[1] + r * math.sin(a)) for a in np.linspace(a0, a1, n)]


def tin_to_grid(vertices: np.ndarray, faces: np.ndarray, cell: float) -> tuple[Grid2D, np.ndarray]:
    V = np.asarray(vertices, float)
    g = Grid2D.from_extent(V[:, 0].min(), V[:, 1].min(), V[:, 0].max(), V[:, 1].max(), cell)
    nodes = g.nodes()
    out = np.full(len(nodes), np.nan)
    for f in faces:
        a, b, c = V[f]
        xmin, xmax = min(a[0], b[0], c[0]), max(a[0], b[0], c[0])
        ymin, ymax = min(a[1], b[1], c[1]), max(a[1], b[1], c[1])
        sel = np.where((nodes[:, 0] >= xmin) & (nodes[:, 0] <= xmax) & (nodes[:, 1] >= ymin) & (nodes[:, 1] <= ymax))[0]
        if not len(sel):
            continue
        M = np.array([[b[0] - a[0], c[0] - a[0]], [b[1] - a[1], c[1] - a[1]]])
        if abs(np.linalg.det(M)) < 1e-12:
            continue
        l = (nodes[sel] - a[:2]) @ np.linalg.inv(M).T
        inside = (l[:, 0] >= -1e-9) & (l[:, 1] >= -1e-9) & (l.sum(axis=1) <= 1 + 1e-9)
        k = sel[inside]
        out[k] = a[2] + l[inside, 0] * (b[2] - a[2]) + l[inside, 1] * (c[2] - a[2])
    return g, out.reshape(g.shape)


# ============================================================================ rasters
def read_ascii_grid(path) -> tuple[Grid2D, np.ndarray]:
    lines = Path(path).read_text().splitlines()
    hdr = {}
    k = 0
    while k < len(lines) and re.match(r"^[A-Za-z_]+\s", lines[k]):
        key, val = lines[k].split()[:2]
        hdr[key.lower()] = float(val)
        k += 1
    nx, ny, cell = int(hdr["ncols"]), int(hdr["nrows"]), hdr["cellsize"]
    if "xllcenter" in hdr:
        x0, y0 = hdr["xllcenter"], hdr["yllcenter"]
    else:
        x0, y0 = hdr["xllcorner"] + cell / 2, hdr["yllcorner"] + cell / 2
    vals = np.array([float(v) for line in lines[k:] for v in line.split()]).reshape(ny, nx)[::-1]
    nod = hdr.get("nodata_value", -9999.0)
    return Grid2D(x0, y0, cell, cell, nx, ny), np.where(vals == nod, np.nan, vals)


def read_dtm(path) -> tuple[Grid2D, np.ndarray]:
    p = Path(path)
    if p.suffix.lower() == ".asc":
        return read_ascii_grid(p)
    if p.suffix.lower() in (".tif", ".tiff"):
        gdal = optional_import("osgeo.gdal")
        if gdal is not None:
            ds = gdal.Open(str(p))
            gt = ds.GetGeoTransform()
            band = ds.GetRasterBand(1)
            a = band.ReadAsArray().astype(float)
            nod = band.GetNoDataValue()
            if nod is not None:
                a = np.where(a == nod, np.nan, a)
            g = Grid2D(
                gt[0] + gt[1] / 2, gt[3] + gt[5] * (ds.RasterYSize - 0.5), gt[1], -gt[5], ds.RasterXSize, ds.RasterYSize
            )
            return g, a[::-1]
        from ..database.raster import read_geotiff

        return read_geotiff(p)
    if p.suffix.lower() == ".las":
        r = read_las(p)
        if isinstance(r, tuple):
            return r
    raise AGSGeoModelError(f"unsupported DTM format {p.suffix}")
