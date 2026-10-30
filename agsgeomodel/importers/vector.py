"""Vector readers (GeoJSON, GeoPackage, Shapefile, DXF) and converters to domain objects.

Pure-Python readers cover the formats the plugin writes and common simple files; inside QGIS any OGR
format is read through QGIS layers instead (see :mod:`agsgeomodel.gui.qgis_bridge`). ``pyogrio`` is
used for anything else when installed.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

from ..core.errors import AGSGeoModelError
from ..core.optional import optional_import
from ..database.gpkg import GeoPackage
from ..models.entities import Fault, MapContactLine, MapUnitPolygon


# ============================================================================ GeoJSON / GPKG
def read_geojson(path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("type") == "Feature":
        data = {"features": [data]}
    return [{"geometry": f.get("geometry"), "properties": f.get("properties") or {}} for f in data.get("features", [])]


def read_gpkg(path, layer: str | None = None) -> list[dict]:
    gp = GeoPackage(path, create=False)
    try:
        layers = gp.tables("features")
        name = layer or (layers[0] if layers else None)
        if name is None:
            return []
        rows = gp.read(name)
        return [{"geometry": r.pop("geometry", None), "properties": r} for r in rows]
    finally:
        gp.close()


# ============================================================================ Shapefile
def read_shapefile(path) -> list[dict]:
    p = Path(path).with_suffix(".shp")
    b = p.read_bytes()
    if struct.unpack_from(">i", b, 0)[0] != 9994:
        raise AGSGeoModelError(f"{p.name} is not an ESRI Shapefile")
    feats, off = [], 100
    while off < len(b):
        _, clen = struct.unpack_from(">2i", b, off)
        rec = b[off + 8 : off + 8 + 2 * clen]
        off += 8 + 2 * clen
        t = struct.unpack_from("<i", rec, 0)[0]
        if t == 0:
            feats.append(None)
            continue
        if t in (1, 11, 21):
            x, y = struct.unpack_from("<2d", rec, 4)
            c = [x, y] + ([struct.unpack_from("<d", rec, 20)[0]] if t == 11 else [])
            feats.append({"type": "Point", "coordinates": c})
            continue
        nparts, npts = struct.unpack_from("<2i", rec, 36)
        parts = list(struct.unpack_from(f"<{nparts}i", rec, 44))
        po = 44 + 4 * nparts
        xy = np.frombuffer(rec[po : po + 16 * npts], dtype="<f8").reshape(npts, 2)
        z = None
        if t in (13, 15):
            zo = po + 16 * npts + 16
            z = np.frombuffer(rec[zo : zo + 8 * npts], dtype="<f8")
        coords = np.column_stack([xy, z]) if z is not None else xy
        rings = [coords[s:e].tolist() for s, e in zip(parts, parts[1:] + [npts])]
        if t in (3, 13, 23):
            feats.append(
                {"type": "LineString", "coordinates": rings[0]}
                if len(rings) == 1
                else {"type": "MultiLineString", "coordinates": rings}
            )
        else:
            polys: list[list] = []
            for r in rings:
                a = np.asarray(r)[:, :2]
                area = 0.5 * float(np.dot(a[:, 0], np.roll(a[:, 1], -1)) - np.dot(a[:, 1], np.roll(a[:, 0], -1)))
                if area <= 0 or not polys:  # clockwise = exterior in shapefiles
                    polys.append([r])
                else:
                    polys[-1].append(r)
            feats.append(
                {"type": "Polygon", "coordinates": polys[0]}
                if len(polys) == 1
                else {"type": "MultiPolygon", "coordinates": polys}
            )
    props = _read_dbf(p.with_suffix(".dbf")) if p.with_suffix(".dbf").exists() else [{} for _ in feats]
    return [{"geometry": g, "properties": pr} for g, pr in zip(feats, props)]


def _read_dbf(path: Path) -> list[dict]:
    b = path.read_bytes()
    n, hlen, rlen = struct.unpack_from("<IHH", b, 4)
    cpg = path.with_suffix(".cpg")
    enc = cpg.read_text().strip() if cpg.exists() else "latin-1"
    fields = []
    off = 32
    while b[off] != 0x0D:
        name = b[off : off + 11].split(b"\0")[0].decode("ascii", "replace")
        ftype = chr(b[off + 11])
        flen, fdec = b[off + 16], b[off + 17]
        fields.append((name, ftype, flen, fdec))
        off += 32
    rows = []
    for i in range(n):
        rec = b[hlen + i * rlen : hlen + (i + 1) * rlen]
        pos = 1
        row = {}
        for name, t, flen, dec in fields:
            raw = rec[pos : pos + flen].decode(enc, "replace").strip()
            pos += flen
            if t in "NF":
                row[name] = None if raw == "" else (float(raw) if (dec or "." in raw) else int(raw))
            elif t == "L":
                row[name] = None if raw in ("", "?") else raw.upper() in ("T", "Y")
            else:
                row[name] = raw
        rows.append(row)
    return rows


# ============================================================================ DXF
def read_dxf(path) -> list[dict]:
    """Minimal ASCII DXF reader: LINE, LWPOLYLINE, POLYLINE/VERTEX/SEQEND, POINT, 3DFACE, TEXT.

    The layer name is returned as the ``layer`` property (use it to map layers to units or faults)."""
    raw = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    pairs = [(raw[i].strip(), raw[i + 1].rstrip("\r")) for i in range(0, len(raw) - 1, 2)]
    # split the ENTITIES section into entities: [(type, [(code, value), ...])]
    ents: list[tuple[str, list[tuple[str, str]]]] = []
    in_ent = False
    cur: tuple[str, list] | None = None
    for k, (code, val) in enumerate(pairs):
        if code == "2" and val.strip() == "ENTITIES" and k and pairs[k - 1] == ("0", "SECTION"):
            in_ent = True
            continue
        if not in_ent:
            continue
        if code == "0":
            if cur is not None:
                ents.append(cur)
            if val.strip() == "ENDSEC":
                in_ent = False
                cur = None
                continue
            cur = (val.strip(), [])
        elif cur is not None:
            cur[1].append((code, val.strip()))

    def pt(codes, n):
        d = dict(codes)
        if f"1{n}" not in d:
            return None
        return [float(d[f"1{n}"]), float(d.get(f"2{n}", 0.0)), float(d.get(f"3{n}", 0.0))]

    feats: list[dict] = []
    poly: dict | None = None
    for t, codes in ents:
        layer = dict(codes).get("8", "0")
        if t == "POLYLINE":
            flags = int(dict(codes).get("70", "0") or 0)
            poly = {"layer": layer, "verts": [], "closed": bool(flags & 1)}
        elif t == "VERTEX" and poly is not None:
            p = pt(codes, 0)
            if p is not None:
                poly["verts"].append(p)
        elif t == "SEQEND" and poly is not None:
            v = poly["verts"] + (poly["verts"][:1] if poly["closed"] else [])
            if len(v) >= 2:
                feats.append(
                    {"geometry": {"type": "LineString", "coordinates": v}, "properties": {"layer": poly["layer"]}}
                )
            poly = None
        elif t == "LINE":
            feats.append(
                {
                    "geometry": {"type": "LineString", "coordinates": [pt(codes, 0), pt(codes, 1)]},
                    "properties": {"layer": layer},
                }
            )
        elif t == "POINT":
            feats.append({"geometry": {"type": "Point", "coordinates": pt(codes, 0)}, "properties": {"layer": layer}})
        elif t == "LWPOLYLINE":
            xs = [float(v) for c, v in codes if c == "10"]
            ys = [float(v) for c, v in codes if c == "20"]
            elev = float(dict(codes).get("38", 0.0))
            closed = bool(int(dict(codes).get("70", "0") or 0) & 1)
            v = [[x, y, elev] if elev else [x, y] for x, y in zip(xs, ys)]
            if closed and v:
                v.append(v[0])
            if len(v) >= 2:
                feats.append({"geometry": {"type": "LineString", "coordinates": v}, "properties": {"layer": layer}})
        elif t == "3DFACE":
            v = [q for q in (pt(codes, n) for n in range(4)) if q is not None]
            dedup = [v[0]] + [q for a, q in zip(v[:-1], v[1:]) if q != a]
            if len(dedup) >= 3:
                feats.append(
                    {
                        "geometry": {"type": "Polygon", "coordinates": [dedup + [dedup[0]]]},
                        "properties": {"layer": layer},
                    }
                )
        elif t in ("TEXT", "MTEXT"):
            d = dict(codes)
            feats.append(
                {
                    "geometry": {"type": "Point", "coordinates": pt(codes, 0)},
                    "properties": {"layer": layer, "text": d.get("1", "")},
                }
            )
    return feats


def read_vector(path, layer: str | None = None) -> list[dict]:
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".geojson", ".json"):
        return read_geojson(p)
    if ext == ".gpkg":
        return read_gpkg(p, layer)
    if ext == ".shp":
        return read_shapefile(p)
    if ext == ".dxf":
        return read_dxf(p)
    pyogrio = optional_import("pyogrio")
    if pyogrio is not None:
        meta, _, geoms, fields = pyogrio.raw.read(str(p), layer=layer)
        from ..database.gpkg import decode_geometry

        names = meta["fields"]
        if geoms is None:  # table without geometry
            geoms = [None] * (len(fields[0]) if len(fields) else 0)
        return [
            {
                "geometry": decode_geometry(bytes(g)) if g is not None else None,
                "properties": {n: (v.item() if hasattr(v, "item") else v) for n, v in zip(names, row)},
            }
            for g, row in zip(geoms, zip(*fields))
        ]
    raise AGSGeoModelError(f"unsupported vector format {ext} (install pyogrio or load it through QGIS)")


# ============================================================================ converters
def _lines(geom):
    if not geom:
        return []
    if geom["type"] == "LineString":
        return [np.asarray(geom["coordinates"], float)[:, :2]]
    if geom["type"] == "MultiLineString":
        return [np.asarray(c, float)[:, :2] for c in geom["coordinates"]]
    return []


def to_faults(
    features,
    name_field: str = "name",
    throw_field: str = "throw_m",
    side_field: str = "downthrown_side",
    dip_field: str = "dip_deg",
) -> list[Fault]:
    out = []
    for k, f in enumerate(features):
        pr = f.get("properties", {})
        for j, ln in enumerate(_lines(f.get("geometry"))):
            if len(ln) < 2:
                continue
            thr = pr.get(throw_field)
            out.append(
                Fault(
                    str(pr.get(name_field) or f"fault_{k}") + (f".{j}" if j else ""),
                    ln,
                    float(thr) if thr not in (None, "") else None,
                    pr.get(side_field) or None,
                    float(pr[dip_field]) if pr.get(dip_field) not in (None, "") else None,
                    "user",
                )
            )
    return out


def to_alignment(features, index: int = 0) -> tuple[np.ndarray, dict]:
    lines = [(ln, f.get("properties", {})) for f in features for ln in _lines(f.get("geometry"))]
    if not lines:
        raise AGSGeoModelError("no line geometry for the alignment")
    return lines[index]


def to_map_units(
    features,
    code_field: str = "code",
    kind: str = "superficial",
    scale_field: str | None = None,
    default_scale: int | None = None,
) -> list[MapUnitPolygon]:
    out = []
    for f in features:
        g = f.get("geometry")
        pr = f.get("properties", {})
        if not g:
            continue
        polys = (
            [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"] if g["type"] == "MultiPolygon" else []
        )
        for poly in polys:
            out.append(
                MapUnitPolygon(
                    str(pr.get(code_field) or "").strip(),
                    pr.get("kind") or kind,
                    [np.asarray(r, float)[:, :2] for r in poly],
                    pr.get("source", "user map"),
                    int(pr.get(scale_field)) if scale_field and pr.get(scale_field) else default_scale,
                    pr,
                )
            )
    return out


def to_map_contacts(features, upper_field: str = "upper", lower_field: str = "lower") -> list[MapContactLine]:
    out = []
    for f in features:
        pr = f.get("properties", {})
        for ln in _lines(f.get("geometry")):
            out.append(MapContactLine(str(pr.get(upper_field)), str(pr.get(lower_field)), ln, pr.get("source", "")))
    return out
