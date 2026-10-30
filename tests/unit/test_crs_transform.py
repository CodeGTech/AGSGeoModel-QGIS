"""CRS detection from LOCA and coordinate transformations (OSGB36 / British National Grid)."""

import numpy as np
import pytest
from helpers import minimal_ags, table, tran

from agsgeomodel.core.transform import (
    bng_to_osgb36_latlon,
    bng_to_wgs84,
    get_transformer,
    osgb36_latlon_to_bng,
    set_host_transformer_factory,
    wgs84_to_bng,
)
from agsgeomodel.importers.ags import detect_crs, read_ags


def test_os_worked_example_tm_projection():
    """Ordnance Survey 'A guide to coordinate systems in Great Britain', worked example (C.1):
    52°39'27.2531"N, 1°43'4.5177"E (OSGB36) -> E 651409.903, N 313177.270."""
    lat = 52 + 39 / 60 + 27.2531 / 3600
    lon = 1 + 43 / 60 + 4.5177 / 3600
    e, n = osgb36_latlon_to_bng(lat, lon)
    assert e == pytest.approx(651409.903, abs=0.01) and n == pytest.approx(313177.270, abs=0.01)
    la, lo = bng_to_osgb36_latlon(651409.903, 313177.270)
    assert la == pytest.approx(lat, abs=1e-8) and lo == pytest.approx(lon, abs=1e-8)


def test_helmert_wgs84_round_trip_is_metre_level():
    e, n = 545000.0, 180000.0
    lon, lat = bng_to_wgs84(e, n)
    assert 0.0 < lon < 0.2 and 51.4 < lat < 51.6
    e2, n2 = wgs84_to_bng(lon, lat)
    assert abs(e2 - e) < 0.05 and abs(n2 - n) < 0.05


def test_against_pyproj_when_available():
    pyproj = pytest.importorskip("pyproj")
    tr = pyproj.Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)
    lon_ref, lat_ref = tr.transform(545000.0, 180000.0)
    lon, lat = bng_to_wgs84(545000.0, 180000.0)
    # the 7-parameter Helmert approximation is good to a few metres (OSTN15 is exact)
    assert abs(lon - lon_ref) * 70000 < 7 and abs(lat - lat_ref) * 111000 < 7


def test_host_factory_takes_precedence():
    calls = []

    def factory(src, dst):
        calls.append((src, dst))
        return lambda x, y: (np.asarray(x) + 1, np.asarray(y) + 2)

    set_host_transformer_factory(factory)
    try:
        f, note = get_transformer("EPSG:27700", "EPSG:4326")
        x, y = f(np.array([1.0]), np.array([1.0]))
        assert calls and x[0] == 2.0 and y[0] == 3.0
    finally:
        set_host_transformer_factory(None)


@pytest.mark.parametrize("e,n,expected", [(545000.0, 180000.0, 27700), (315000.0, 230000.0, 29903)])
def test_detect_crs_from_coordinates(e, n, expected):
    text = minimal_ags(
        [("BH1", "CP", e, n, 5.0, 10.0), ("BH2", "CP", e + 50, n + 20, 5.0, 10.0)], [("BH1", 0, 10, "CLAY", "LC")]
    )
    if expected == 29903:
        text = text.replace('"OSGB"', '"IRISH GRID"')
    g = detect_crs(read_ags(text.encode()))
    assert g.epsg == expected and 0 < g.confidence <= 1


def test_detect_latlon():
    loca = table(
        "LOCA",
        ["LOCA_ID", "LOCA_LAT", "LOCA_LON", "LOCA_GL"],
        [["BH1", "51.5000", "0.1000", "5"], ["BH2", "51.5010", "0.1010", "6"]],
        types={"LOCA_LAT": "4DP", "LOCA_LON": "4DP"},
    )
    from agsgeomodel.importers.ags import write_ags4

    g = detect_crs(read_ags(write_ags4([tran(), loca]).encode()))
    assert g.uses_latlon and g.epsg == 4326
