"""Fake BGS service responses for tests.

TEST FIXTURES ONLY - NOT REAL BGS DATA. The JSON structures and property names reproduce the live
schemas of ogcapi.bgs.ac.uk collections (bgsgeology625kbedrock, bgsgeology625ksuperficial,
bgsgeology625kfaults, onshoreboreholeindex, agsboreholeindex, landslideindex, scanned-maps-250k) and
the agsapi.bgs.ac.uk /v7/ags_export/ ZIP response, as checked in October 2026. Geometry is placed
around the synthetic example site and values are invented.
"""

from __future__ import annotations

import io
import json
import re
import urllib.parse
import zipfile

import numpy as np

from agsgeomodel.core.transform import bng_to_wgs84
from agsgeomodel.examples.synthetic import E0, FAULT, N0, SyntheticTruth
from agsgeomodel.importers.ags.reader import AGSTable, write_ags4


def _ll(xy):
    a = np.asarray(xy, float)
    lon, lat = bng_to_wgs84(a[:, 0], a[:, 1])
    return np.column_stack([lon, lat]).tolist()


def _poly(xmin, ymin, xmax, ymax):
    ring = [[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax], [xmin, ymin]]
    return {"type": "Polygon", "coordinates": [_ll(ring)]}


def bedrock():
    base = {
        "rank": "FORMATION",
        "max_period": "PALAEOGENE",
        "min_period": "PALAEOGENE",
        "version": "fixture",
        "released": "fixture",
        "nom_scale": "625000",
    }
    return [
        {
            "type": "Feature",
            "id": 1,
            "geometry": _poly(E0 - 2000, N0 + 380, E0 + 3000, N0 + 3000),
            "properties": {
                **base,
                "objectid": 1,
                "lex": "LC",
                "lex_d": "LONDON CLAY FORMATION",
                "rcs": "CLSISA",
                "rcs_d": "CLAY, SILT AND SAND",
                "fm_eq": "LC",
                "gp_eq": "THAM",
                "max_time_y": 54000000,
                "min_time_y": 49000000,
                "map_code": "FIX1",
            },
        },
        {
            "type": "Feature",
            "id": 2,
            "geometry": _poly(E0 - 2000, N0 - 3000, E0 + 3000, N0 + 380),
            "properties": {
                **base,
                "objectid": 2,
                "lex": "LMBE",
                "lex_d": "LAMBETH GROUP",
                "rank": "GROUP",
                "rcs": "CLSSG",
                "rcs_d": "CLAY, SILT, SAND AND GRAVEL",
                "fm_eq": "NotAp",
                "gp_eq": "LMBE",
                "max_time_y": 65000000,
                "min_time_y": 54800000,
                "map_code": "FIX2",
            },
        },
    ]


def superficial():
    return [
        {
            "type": "Feature",
            "id": 10,
            "geometry": _poly(E0 - 2000, N0 - 3000, E0 + 3000, N0 + 360),
            "properties": {
                "objectid": 10,
                "lex": "ALV",
                "lex_d": "ALLUVIUM",
                "rcs": "CLAY + SILT + SANDU",
                "max_age": "Q",
                "max_system": "QUATERNARY",
            },
        },
        {
            "type": "Feature",
            "id": 11,
            "geometry": _poly(E0 - 2000, N0 + 360, E0 + 3000, N0 + 3000),
            "properties": {
                "objectid": 11,
                "lex": "RTDU",
                "lex_d": "RIVER TERRACE DEPOSITS (UNDIFFERENTIATED)",
                "rcs": "SAGR",
                "rcs_d": "SAND AND GRAVEL",
                "max_age": "Q",
                "max_system": "QUATERNARY",
            },
        },
    ]


def faults():
    return [
        {
            "type": "Feature",
            "id": 20,
            "geometry": {"type": "LineString", "coordinates": _ll(list(FAULT))},
            "properties": {
                "objectid": 20,
                "category": "FAULT",
                "feature": "FLT",
                "feature_d": "FAULT",
                "fltname_c": "",
                "fltname_d": "FIXTURE FAULT",
            },
        }
    ]


def sobi():
    recs = [
        ("TQ48SE900/FIX1", 545300.0, 180700.0, 30.5, "1964", "± 10 METRES", 900001),
        ("TQ48SE901/FIX2", 546300.0, 180100.0, 25.0, "1998", "± 1 METRES", 900002),
        ("TQ48SE902/FIX3", 549000.0, 183000.0, 8.0, "1890", "± 100 METRES", 900003),
    ]
    out = []
    for ref, e, n, length, yr, prec, bid in recs:
        out.append(
            {
                "type": "Feature",
                "id": bid,
                "geometry": {"type": "Point", "coordinates": _ll([[e, n]])[0]},
                "properties": {
                    "reference": ref,
                    "name": "FIXTURE RECORD",
                    "grid_ref": "",
                    "easting": e,
                    "northing": n,
                    "precision": prec,
                    "length": length,
                    "year_known": yr,
                    "sitereport": None,
                    "held_at": "FIX",
                    "id": float(bid),
                    "length_scan_cat": "2_Y",
                    "bgs_id": float(bid),
                    "date_updated": "2013-01-22T18:11:19",
                    "scan_url": f"https://example.invalid/scan/{bid}",
                    "ags_log_url": None,
                    "water_well_ref": None,
                    "scan_quality": "not Entered",
                },
            }
        )
    return out


def ags_index():
    out = []
    for k, (e, n) in enumerate([(545700.0, 180500.0), (545150.0, 180650.0)], start=1):
        lid = f"2019000000000000000{k}"
        out.append(
            {
                "type": "Feature",
                "id": lid,
                "geometry": {"type": "Point", "coordinates": _ll([[e, n]])[0]},
                "properties": {
                    "bgs_loca_id": lid,
                    "id": lid,
                    "proj_name": "FIXTURE PROJECT",
                    "proj_cont": "Fixture contractor",
                    "proj_eng": "Fixture engineer",
                    "x": e,
                    "y": n,
                    "loca_fdep": 40.0,
                    "rec_id": float(k),
                    "loca_id": f"FXBH{k}",
                    "item_index_id": 1.0,
                    "ags_log_url": f"https://example.invalid/log/{lid}",
                    "dad_item_url": "https://example.invalid/item",
                },
            }
        )
    return out


def scanned_maps():
    return [
        {
            "type": "Feature",
            "id": 30,
            "geometry": _poly(E0 - 20000, N0 - 20000, E0 + 20000, N0 + 20000),
            "properties": {
                "ql_map_title_id": 1,
                "map_title_id": 1,
                "full_title": "FIXTURE 1:250 000 SHEET",
                "publication_date": "1980-01-01T00:00:00",
                "map_series": "FIX",
                "map_series_formal_title": "Fixture series",
                "map_series_edition": "1",
                "geol_scale": "1:250000",
                "extent_feature": "",
                "copyright_owner": "Fixture",
                "author_statement": "Fixture authors",
                "publication_statement": "Fixture",
                "additional_info": "",
                "scan_url": "https://example.invalid/sheet",
            },
        }
    ]


def ags_zip(ids: list[str]) -> bytes:
    """A ZIP like the AGS API export: AGS4 file(s) plus a metadata text file."""
    tr = SyntheticTruth()
    locs = {f["properties"]["bgs_loca_id"]: f for f in ags_index()}
    loca = AGSTable(
        "LOCA",
        ["LOCA_ID", "LOCA_TYPE", "LOCA_NATE", "LOCA_NATN", "LOCA_GREF", "LOCA_GL", "LOCA_FDEP"],
        ["", "", "m", "m", "", "m", "m"],
        ["ID", "PA", "2DP", "2DP", "PA", "2DP", "2DP"],
    )
    geol = AGSTable(
        "GEOL",
        ["LOCA_ID", "GEOL_TOP", "GEOL_BASE", "GEOL_DESC", "GEOL_GEOL"],
        ["", "m", "m", "", ""],
        ["ID", "2DP", "2DP", "X", "PA"],
    )
    desc = {
        "MGR": "MADE GROUND",
        "ALV": "Soft grey organic CLAY",
        "RTDU": "Dense brown sandy flint GRAVEL",
        "LC": "Stiff grey fissured CLAY with selenite",
        "HWH": "Dense green glauconitic SAND with rounded flint pebbles",
        "LMBE": "Very stiff red and grey mottled CLAY",
        "TAB": "Very dense grey silty fine SAND with glauconite",
        "CK": "Weak white CHALK with flints",
    }
    for i in ids:
        f = locs[i]["properties"]
        e, n = f["x"], f["y"]
        g = float(tr.ground(e, n))
        loca.rows.append([f["bgs_loca_id"], "CP", f"{e:.2f}", f"{n:.2f}", "OSGB", f"{g:.2f}", "40.00"])
        for u, zt, zb in tr.column_at(e, n):
            top, base = max(0.0, g - zt), min(40.0, g - zb if np.isfinite(zb) else 40.0)
            if base > top:
                geol.rows.append([f["bgs_loca_id"], f"{top:.2f}", f"{base:.2f}", desc[u], ""])
    tran = AGSTable("TRAN", ["TRAN_ISNO", "TRAN_AGS"], ["", ""], ["X", "X"], [["1", "4.1"]])
    text = write_ags4([tran, loca, geol])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("FIXTURE_PROJECT.ags", text)
        zf.writestr("FIXTURE_PROJECT_metadata.txt", "TEST FIXTURE - NOT REAL BGS DATA")
    return buf.getvalue()


COLLECTION_FEATURES = {
    "bgsgeology625kbedrock": bedrock,
    "bgsgeology625ksuperficial": superficial,
    "bgsgeology625kfaults": faults,
    "onshoreboreholeindex": sobi,
    "agsboreholeindex": ags_index,
    "landslideindex": list,
    "scanned-maps-250k": scanned_maps,
}


def fake_fetcher(page_size: int = 2):
    """Transport serving fixture responses; paginates with ``next`` links to exercise paging."""
    calls: list[str] = []

    def fetch(url: str, headers: dict) -> bytes:
        calls.append(url)
        u = urllib.parse.urlparse(url)
        q = dict(urllib.parse.parse_qsl(u.query))
        if "ags_export" in u.path:
            return ags_zip(q["bgs_loca_id"].split(";"))
        m = re.search(r"/collections/([^/]+)/items", u.path)
        feats = COLLECTION_FEATURES[m.group(1)]()
        off = int(q.get("offset", 0))
        page = feats[off : off + page_size]
        links = [{"rel": "self", "href": url}]
        if off + page_size < len(feats):
            q2 = {**q, "offset": str(off + page_size)}
            links.append({"rel": "next", "href": f"{u.scheme}://{u.netloc}{u.path}?{urllib.parse.urlencode(q2)}"})
        return json.dumps(
            {
                "type": "FeatureCollection",
                "features": page,
                "numberMatched": len(feats),
                "numberReturned": len(page),
                "links": links,
            }
        ).encode()

    fetch.calls = calls
    return fetch
