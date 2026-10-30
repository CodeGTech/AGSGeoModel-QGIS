"""Dependency-free OGC GeoPackage (1.3) reader/writer built on :mod:`sqlite3`.

Implements what the plugin needs and nothing more: feature and attribute tables, the mandatory
metadata tables, standard GeoPackageBinary geometry encoding (ISO WKB, 2D and Z), the
``gpkg_rtree_index`` extension with the standard triggers, and decoding for round trips. The
``ST_*`` helper functions the R-tree triggers call are registered on every connection, so the file
can be written by this module and edited by GDAL/QGIS interchangeably.
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
import struct
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..core.errors import StoreError

GPKG_APPLICATION_ID = 0x47504B47
GPKG_USER_VERSION = 10300

_WKB_CODES = {
    "Point": 1,
    "LineString": 2,
    "Polygon": 3,
    "MultiPoint": 4,
    "MultiLineString": 5,
    "MultiPolygon": 6,
    "GeometryCollection": 7,
}
_WKB_NAMES = {v: k for k, v in _WKB_CODES.items()}
GPKG_TYPE_NAMES = {
    "Point": "POINT",
    "LineString": "LINESTRING",
    "Polygon": "POLYGON",
    "MultiPoint": "MULTIPOINT",
    "MultiLineString": "MULTILINESTRING",
    "MultiPolygon": "MULTIPOLYGON",
    "Geometry": "GEOMETRY",
}
WGS84_WKT = (
    'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],'
    'AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,'
    'AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4326"]]'
)
BNG_WKT = (
    'PROJCS["OSGB 1936 / British National Grid",GEOGCS["OSGB 1936",DATUM["OSGB_1936",SPHEROID["Airy 1830",'
    '6377563.396,299.3249646,AUTHORITY["EPSG","7001"]],AUTHORITY["EPSG","6277"]],PRIMEM["Greenwich",0,'
    'AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
    'AUTHORITY["EPSG","4277"]],PROJECTION["Transverse_Mercator"],PARAMETER["latitude_of_origin",49],'
    'PARAMETER["central_meridian",-2],PARAMETER["scale_factor",0.9996012717],PARAMETER["false_easting",400000],'
    'PARAMETER["false_northing",-100000],UNIT["metre",1,AUTHORITY["EPSG","9001"]],AXIS["Easting",EAST],'
    'AXIS["Northing",NORTH],AUTHORITY["EPSG","27700"]]'
)
KNOWN_WKT = {4326: WGS84_WKT, 27700: BNG_WKT}

_SQL_TYPES = {
    "int": "INTEGER",
    "integer": "INTEGER",
    "float": "REAL",
    "real": "REAL",
    "double": "REAL",
    "str": "TEXT",
    "text": "TEXT",
    "bool": "BOOLEAN",
    "boolean": "BOOLEAN",
    "json": "TEXT",
    "date": "DATE",
    "datetime": "DATETIME",
    "blob": "BLOB",
}


# ============================================================================ geometry codec
def _coords_dim(coords) -> int:
    while isinstance(coords, (list, tuple)) and coords and isinstance(coords[0], (list, tuple)):
        coords = coords[0]
    return len(coords) if isinstance(coords, (list, tuple)) else 2


def _flatten_xy(geom: dict) -> list[tuple]:
    t = geom["type"]
    c = geom["coordinates"]
    if t == "Point":
        return [tuple(c)]
    if t in ("LineString", "MultiPoint"):
        return [tuple(p) for p in c]
    if t in ("Polygon", "MultiLineString"):
        return [tuple(p) for ring in c for p in ring]
    if t == "MultiPolygon":
        return [tuple(p) for poly in c for ring in poly for p in ring]
    if t == "GeometryCollection":
        return [p for g in geom["geometries"] for p in _flatten_xy(g)]
    raise StoreError(f"unsupported geometry type {t}")


def _wkb(geom: dict, has_z: bool) -> bytes:
    t = geom["type"]
    code = _WKB_CODES[t] + (1000 if has_z else 0)
    out = bytearray(struct.pack("<BI", 1, code))
    nd = 3 if has_z else 2

    def pt(p):
        vals = list(p[:nd]) + [0.0] * (nd - len(p[:nd]))
        return struct.pack("<" + "d" * nd, *[float(v) for v in vals])

    c = geom.get("coordinates")
    if t == "Point":
        out += pt(c)
    elif t == "LineString":
        out += struct.pack("<I", len(c)) + b"".join(pt(p) for p in c)
    elif t == "Polygon":
        out += struct.pack("<I", len(c))
        for ring in c:
            out += struct.pack("<I", len(ring)) + b"".join(pt(p) for p in ring)
    elif t in ("MultiPoint", "MultiLineString", "MultiPolygon"):
        sub = {"MultiPoint": "Point", "MultiLineString": "LineString", "MultiPolygon": "Polygon"}[t]
        out += struct.pack("<I", len(c))
        for part in c:
            out += _wkb({"type": sub, "coordinates": part}, has_z)
    elif t == "GeometryCollection":
        gs = geom["geometries"]
        out += struct.pack("<I", len(gs))
        for g in gs:
            out += _wkb(g, has_z)
    return bytes(out)


def encode_geometry(geom: dict | None, srs_id: int, has_z: bool | None = None) -> bytes | None:
    """GeoJSON-like dict -> GeoPackageBinary blob (little endian, envelope [x, y] or [x, y, z])."""
    if geom is None:
        return None
    pts = _flatten_xy(geom)
    if has_z is None:
        has_z = _coords_dim(geom.get("coordinates", [])) >= 3 if geom["type"] != "GeometryCollection" else False
    empty = len(pts) == 0
    env_code = 0 if empty else (2 if has_z else 1)
    flags = (env_code << 1) | 1 | (0x10 if empty else 0)
    head = b"GP" + bytes([0, flags]) + struct.pack("<i", srs_id)
    if not empty:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        env = [min(xs), max(xs), min(ys), max(ys)]
        if has_z:
            zs = [p[2] if len(p) > 2 else 0.0 for p in pts]
            env += [min(zs), max(zs)]
        head += struct.pack("<" + "d" * len(env), *env)
    return head + _wkb(geom, has_z)


def _envelope(blob: bytes) -> tuple[float, float, float, float] | None:
    if blob is None or len(blob) < 8 or blob[:2] != b"GP":
        return None
    flags = blob[3]
    if flags & 0x10:
        return None
    env_code = (flags >> 1) & 0x07
    le = flags & 1
    fmt = "<" if le else ">"
    if env_code == 0:
        g = decode_geometry(blob)
        pts = _flatten_xy(g) if g else []
        if not pts:
            return None
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), max(xs), min(ys), max(ys)
    return struct.unpack(fmt + "dddd", blob[8:40])


def _read_wkb(b: bytes, off: int):
    order = b[off]
    fmt = "<" if order == 1 else ">"
    (code,) = struct.unpack_from(fmt + "I", b, off + 1)
    off += 5
    has_z = (code // 1000) in (1, 3) or bool(code & 0x80000000)
    has_m = (code // 1000) in (2, 3)
    base = (code & 0xFFFF) % 1000
    nd = 2 + int(has_z) + int(has_m)
    t = _WKB_NAMES[base]

    def pt(o):
        v = struct.unpack_from(fmt + "d" * nd, b, o)
        return list(v[:3] if has_z else v[:2]), o + 8 * nd

    if t == "Point":
        p, off = pt(off)
        return {"type": t, "coordinates": p}, off
    if t == "LineString":
        (n,) = struct.unpack_from(fmt + "I", b, off)
        off += 4
        cs = []
        for _ in range(n):
            p, off = pt(off)
            cs.append(p)
        return {"type": t, "coordinates": cs}, off
    if t == "Polygon":
        (nr,) = struct.unpack_from(fmt + "I", b, off)
        off += 4
        rings = []
        for _ in range(nr):
            (n,) = struct.unpack_from(fmt + "I", b, off)
            off += 4
            ring = []
            for _ in range(n):
                p, off = pt(off)
                ring.append(p)
            rings.append(ring)
        return {"type": t, "coordinates": rings}, off
    (n,) = struct.unpack_from(fmt + "I", b, off)
    off += 4
    parts = []
    for _ in range(n):
        g, off = _read_wkb(b, off)
        parts.append(g)
    if t == "GeometryCollection":
        return {"type": t, "geometries": parts}, off
    return {"type": t, "coordinates": [g["coordinates"] for g in parts]}, off


def decode_geometry(blob: bytes | None) -> dict | None:
    if blob is None:
        return None
    if blob[:2] != b"GP":
        g, _ = _read_wkb(blob, 0)
        return g
    flags = blob[3]
    env_code = (flags >> 1) & 0x07
    env_len = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(env_code, 0)
    g, _ = _read_wkb(blob, 8 + env_len)
    return g


# ============================================================================ store
def _now() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class GeoPackage:
    def __init__(self, path: str | Path, create: bool = True):
        self.path = Path(path)
        exists = self.path.exists()
        if not exists and not create:
            raise StoreError(f"GeoPackage not found: {self.path}")
        self.conn = sqlite3.connect(str(self.path))
        self._register_functions()
        if not exists:
            self._init()
        self.conn.execute("PRAGMA foreign_keys = ON")

    # ------------------------------------------------------------------ infrastructure
    def _register_functions(self) -> None:
        def env(i):
            def f(blob):
                e = _envelope(blob)
                return None if e is None else e[i]

            return f

        self.conn.create_function("ST_MinX", 1, env(0), deterministic=True)
        self.conn.create_function("ST_MaxX", 1, env(1), deterministic=True)
        self.conn.create_function("ST_MinY", 1, env(2), deterministic=True)
        self.conn.create_function("ST_MaxY", 1, env(3), deterministic=True)
        self.conn.create_function(
            "ST_IsEmpty", 1, lambda b: 1 if b is None or (len(b) > 3 and b[3] & 0x10) else 0, deterministic=True
        )

    def _init(self) -> None:
        c = self.conn
        c.execute(f"PRAGMA application_id = {GPKG_APPLICATION_ID}")
        c.execute(f"PRAGMA user_version = {GPKG_USER_VERSION}")
        c.executescript("""
        CREATE TABLE gpkg_spatial_ref_sys (srs_name TEXT NOT NULL, srs_id INTEGER NOT NULL PRIMARY KEY,
            organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL, definition TEXT NOT NULL,
            description TEXT);
        CREATE TABLE gpkg_contents (table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL,
            identifier TEXT UNIQUE, description TEXT DEFAULT '',
            last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE, srs_id INTEGER,
            CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id));
        CREATE TABLE gpkg_geometry_columns (table_name TEXT NOT NULL, column_name TEXT NOT NULL,
            geometry_type_name TEXT NOT NULL, srs_id INTEGER NOT NULL, z TINYINT NOT NULL, m TINYINT NOT NULL,
            CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),
            CONSTRAINT uk_gc_table_name UNIQUE (table_name),
            CONSTRAINT fk_gc_tn FOREIGN KEY (table_name) REFERENCES gpkg_contents(table_name),
            CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys (srs_id));
        CREATE TABLE gpkg_extensions (table_name TEXT, column_name TEXT, extension_name TEXT NOT NULL,
            definition TEXT NOT NULL, scope TEXT NOT NULL,
            CONSTRAINT ge_tce UNIQUE (table_name, column_name, extension_name));
        """)
        c.executemany(
            "INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
            [
                (
                    "Undefined cartesian SRS",
                    -1,
                    "NONE",
                    -1,
                    "undefined",
                    "undefined cartesian coordinate reference system",
                ),
                (
                    "Undefined geographic SRS",
                    0,
                    "NONE",
                    0,
                    "undefined",
                    "undefined geographic coordinate reference system",
                ),
                ("WGS 84 geodetic", 4326, "EPSG", 4326, WGS84_WKT, "longitude/latitude coordinates in decimal degrees"),
            ],
        )
        c.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.conn.commit()
        self.close()

    @contextmanager
    def transaction(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ------------------------------------------------------------------ SRS
    def ensure_srs(self, srs: str | int | None, wkt: str | None = None) -> int:
        if srs in (None, "", -1):
            return -1
        code = int(str(srs).upper().replace("EPSG:", ""))
        row = self.conn.execute("SELECT srs_id FROM gpkg_spatial_ref_sys WHERE srs_id = ?", (code,)).fetchone()
        if row:
            return code
        definition = wkt or KNOWN_WKT.get(code)
        if definition is None:
            try:
                import pyproj  # type: ignore

                definition = pyproj.CRS.from_epsg(code).to_wkt("WKT1_GDAL")
            except Exception:
                definition = "undefined"
        self.conn.execute(
            "INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
            (f"EPSG:{code}", code, "EPSG", code, definition, ""),
        )
        return code

    # ------------------------------------------------------------------ tables
    def has_table(self, name: str) -> bool:
        return (
            self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
            is not None
        )

    def drop_table(self, name: str) -> None:
        if not self.has_table(name):
            return
        c = self.conn
        rt = f"rtree_{name}_geom"
        for trig in ("insert", "update1", "update2", "update3", "update4", "delete"):
            c.execute(f'DROP TRIGGER IF EXISTS "{rt}_{trig}"')
        c.execute(f'DROP TABLE IF EXISTS "{rt}"')
        c.execute(f'DROP TABLE "{name}"')
        c.execute("DELETE FROM gpkg_geometry_columns WHERE table_name=?", (name,))
        c.execute("DELETE FROM gpkg_extensions WHERE table_name=?", (name,))
        c.execute("DELETE FROM gpkg_contents WHERE table_name=?", (name,))
        c.commit()

    def create_table(
        self,
        name: str,
        fields: list[tuple[str, str]],
        geometry_type: str | None = None,
        srs: str | int | None = None,
        z: bool = False,
        description: str = "",
        overwrite: bool = False,
        spatial_index: bool = True,
    ) -> None:
        if self.has_table(name):
            if not overwrite:
                return
            self.drop_table(name)
        cols = ['"fid" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL']
        if geometry_type:
            cols.append('"geom" ' + GPKG_TYPE_NAMES.get(geometry_type, "GEOMETRY"))
        for fname, ftype in fields:
            cols.append(f'"{fname}" {_SQL_TYPES.get(ftype.lower(), ftype)}')
        c = self.conn
        c.execute(f'CREATE TABLE "{name}" ({", ".join(cols)})')
        if geometry_type:
            srs_id = self.ensure_srs(srs)
            c.execute(
                "INSERT INTO gpkg_contents (table_name, data_type, identifier, description, last_change, srs_id) "
                "VALUES (?,?,?,?,?,?)",
                (name, "features", name, description, _now(), srs_id),
            )
            c.execute(
                "INSERT INTO gpkg_geometry_columns VALUES (?,?,?,?,?,?)",
                (name, "geom", GPKG_TYPE_NAMES.get(geometry_type, "GEOMETRY"), srs_id, 1 if z else 0, 0),
            )
            if spatial_index:
                self._create_rtree(name)
        else:
            c.execute(
                "INSERT INTO gpkg_contents (table_name, data_type, identifier, description, last_change) "
                "VALUES (?,?,?,?,?)",
                (name, "attributes", name, description, _now()),
            )
        c.commit()

    def _create_rtree(self, t: str) -> None:
        rt = f"rtree_{t}_geom"
        c = self.conn
        c.execute(f'CREATE VIRTUAL TABLE "{rt}" USING rtree(id, minx, maxx, miny, maxy)')
        c.execute(
            "INSERT INTO gpkg_extensions VALUES (?,?,?,?,?)",
            (t, "geom", "gpkg_rtree_index", "http://www.geopackage.org/spec120/#extension_rtree", "write-only"),
        )
        q = f'"{t}"'
        trig = {
            "insert": f"AFTER INSERT ON {q} WHEN (new.geom NOT NULL AND NOT ST_IsEmpty(NEW.geom)) BEGIN "
            f'INSERT OR REPLACE INTO "{rt}" VALUES (NEW.fid, ST_MinX(NEW.geom), ST_MaxX(NEW.geom), '
            f"ST_MinY(NEW.geom), ST_MaxY(NEW.geom)); END",
            "update1": f"AFTER UPDATE OF geom ON {q} WHEN OLD.fid = NEW.fid AND (NEW.geom NOTNULL AND NOT "
            f'ST_IsEmpty(NEW.geom)) BEGIN INSERT OR REPLACE INTO "{rt}" VALUES (NEW.fid, ST_MinX(NEW.geom), '
            f"ST_MaxX(NEW.geom), ST_MinY(NEW.geom), ST_MaxY(NEW.geom)); END",
            "update2": f"AFTER UPDATE OF geom ON {q} WHEN OLD.fid = NEW.fid AND (NEW.geom ISNULL OR "
            f'ST_IsEmpty(NEW.geom)) BEGIN DELETE FROM "{rt}" WHERE id = OLD.fid; END',
            "update3": f"AFTER UPDATE ON {q} WHEN OLD.fid != NEW.fid AND (NEW.geom NOTNULL AND NOT "
            f'ST_IsEmpty(NEW.geom)) BEGIN DELETE FROM "{rt}" WHERE id = OLD.fid; INSERT OR REPLACE INTO '
            f'"{rt}" VALUES (NEW.fid, ST_MinX(NEW.geom), ST_MaxX(NEW.geom), ST_MinY(NEW.geom), '
            f"ST_MaxY(NEW.geom)); END",
            "update4": f"AFTER UPDATE ON {q} WHEN OLD.fid != NEW.fid AND (NEW.geom ISNULL OR ST_IsEmpty(NEW.geom)) "
            f'BEGIN DELETE FROM "{rt}" WHERE id IN (OLD.fid, NEW.fid); END',
            "delete": f'AFTER DELETE ON {q} WHEN old.geom NOT NULL BEGIN DELETE FROM "{rt}" WHERE id = OLD.fid; END',
        }
        for k, body in trig.items():
            c.execute(f'CREATE TRIGGER "{rt}_{k}" {body}')

    def columns(self, name: str) -> list[str]:
        return [r[1] for r in self.conn.execute(f'PRAGMA table_info("{name}")')]

    def add_column(self, name: str, col: str, ftype: str) -> None:
        if col not in self.columns(name):
            self.conn.execute(f'ALTER TABLE "{name}" ADD COLUMN "{col}" {_SQL_TYPES.get(ftype.lower(), ftype)}')

    def insert(
        self, name: str, rows: Iterable[dict[str, Any]], srs: str | int | None = None, z: bool | None = None
    ) -> int:
        cols = [c for c in self.columns(name) if c != "fid"]
        if not cols:
            raise StoreError(f"table {name!r} does not exist in {self.path}")
        has_geom = "geom" in cols
        srs_id = -1
        if has_geom:
            r = self.conn.execute("SELECT srs_id, z FROM gpkg_geometry_columns WHERE table_name=?", (name,)).fetchone()
            srs_id = r[0] if r else -1
            z = bool(r[1]) if (r and z is None) else z
        data_cols = [c for c in cols if c != "geom"]
        all_cols = (["geom"] if has_geom else []) + data_cols
        col_sql = ", ".join(f'"{c}"' for c in all_cols)
        sql = f'INSERT INTO "{name}" ({col_sql}) VALUES ({", ".join("?" * len(all_cols))})'
        n = 0
        batch = []
        for row in rows:
            vals = []
            if has_geom:
                vals.append(encode_geometry(row.get("geometry"), srs_id, z))
            props = row.get("properties", row)
            for c in data_cols:
                v = props.get(c)
                if isinstance(v, (dict, list, tuple)):
                    v = json.dumps(v, default=str)
                elif hasattr(v, "item"):
                    v = v.item()
                if isinstance(v, float) and v != v:
                    v = None
                vals.append(v)
            batch.append(vals)
            n += 1
            if len(batch) >= 5000:
                self.conn.executemany(sql, batch)
                batch = []
        if batch:
            self.conn.executemany(sql, batch)
        if has_geom and n:
            self.update_extent(name)
        self.conn.execute("UPDATE gpkg_contents SET last_change=? WHERE table_name=?", (_now(), name))
        self.conn.commit()
        return n

    def update_extent(self, name: str) -> None:
        rt = f"rtree_{name}_geom"
        if self.has_table(rt):
            e = self.conn.execute(f'SELECT min(minx), min(miny), max(maxx), max(maxy) FROM "{rt}"').fetchone()
        else:
            xs = [_envelope(b[0]) for b in self.conn.execute(f'SELECT geom FROM "{name}"')]
            xs = [x for x in xs if x]
            e = (
                (min(x[0] for x in xs), min(x[2] for x in xs), max(x[1] for x in xs), max(x[3] for x in xs))
                if xs
                else (None, None, None, None)
            )
        self.conn.execute("UPDATE gpkg_contents SET min_x=?, min_y=?, max_x=?, max_y=? WHERE table_name=?", (*e, name))

    def read(self, name: str, where: str = "", params: tuple = ()) -> list[dict]:
        cols = self.columns(name)
        q = f'SELECT * FROM "{name}"' + (f" WHERE {where}" if where else "")
        out = []
        for r in self.conn.execute(q, params):
            d = dict(zip(cols, r))
            if "geom" in d:
                d["geometry"] = decode_geometry(d.pop("geom"))
            out.append(d)
        return out

    def tables(self, data_type: str | None = None) -> list[str]:
        q = "SELECT table_name FROM gpkg_contents" + (" WHERE data_type=?" if data_type else "")
        return [r[0] for r in self.conn.execute(q, (data_type,) if data_type else ())]

    def integrity_check(self) -> list[str]:
        """Light GeoPackage conformance checks (requirement numbers from the OGC 12-128 standard)."""
        probs = []
        c = self.conn
        if c.execute("PRAGMA application_id").fetchone()[0] != GPKG_APPLICATION_ID:
            probs.append("R2: application_id is not GPKG")
        if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            probs.append("R6: SQLite integrity_check failed")
        if c.execute("PRAGMA foreign_key_check").fetchall():
            probs.append("R7: foreign key violations")
        for t in self.tables("features"):
            if not c.execute("SELECT 1 FROM gpkg_geometry_columns WHERE table_name=?", (t,)).fetchone():
                probs.append(f"R21: feature table {t} not registered in gpkg_geometry_columns")
        return probs
