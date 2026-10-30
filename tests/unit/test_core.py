"""Geometry, grids, polygonisation, contours, spatial index, configuration and provenance."""

import json

import numpy as np
import pytest

from agsgeomodel.core.config import ModelConfig, ProjectSettings
from agsgeomodel.core.contour import contour_levels, contour_lines
from agsgeomodel.core.geometry import (
    convex_hull,
    crosses_any,
    distance_to_segments,
    point_at_chainage,
    points_in_polygon,
    polyline_chainage,
    polyline_intersections,
    polyline_length,
    project_points_to_polyline,
    resample_polyline,
)
from agsgeomodel.core.grid import Grid2D, polygonize
from agsgeomodel.core.optional import DependencyMissing, has, optional_import, require
from agsgeomodel.core.provenance import RunRecord, stable_hash
from agsgeomodel.core.spatial import PointIndex

LINE = np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 50.0]])


def test_chainage_and_points():
    np.testing.assert_allclose(polyline_chainage(LINE), [0, 100, 150])
    assert polyline_length(LINE) == 150
    np.testing.assert_allclose(point_at_chainage(LINE, [50, 125]), [[50, 0], [100, 25]])
    ch, xy = resample_polyline(LINE, 10.0)
    assert ch[0] == 0 and ch[-1] == 150 and np.all(np.diff(ch) <= 10 + 1e-9)
    c, off, _ = project_points_to_polyline(np.array([[30.0, 5.0], [110.0, 40.0]]), LINE)
    np.testing.assert_allclose(c, [30, 140])
    assert abs(off[0]) == pytest.approx(5) and abs(off[1]) == pytest.approx(10)


def test_crossing_tests():
    segs = np.array([[[50.0, -10.0], [50.0, 10.0]]])
    assert crosses_any(np.array([[0.0, 0.0]]), np.array([[100.0, 0.0]]), segs)[0]
    assert not crosses_any(np.array([[0.0, 20.0]]), np.array([[100.0, 20.0]]), segs)[0]
    hits = polyline_intersections(LINE, segs)
    assert len(hits) == 1 and hits[0][0] == pytest.approx(50.0)
    d = distance_to_segments(np.array([[50.0, 15.0], [60.0, 0.0]]), segs)
    np.testing.assert_allclose(d, [5.0, 10.0])


def test_polygon_with_hole():
    outer = np.array([[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], float)
    hole = np.array([[3, 3], [7, 3], [7, 7], [3, 7], [3, 3]], float)
    inside = points_in_polygon(np.array([[1.0, 1.0], [5.0, 5.0], [11.0, 5.0]]), [outer, hole])
    assert inside.tolist() == [True, False, False]
    hull = convex_hull(np.random.default_rng(0).uniform(0, 1, (200, 2)))
    assert len(hull) >= 3


def test_grid_sampling_is_exact_for_planes():
    g = Grid2D.from_extent(0, 0, 100, 80, 5.0)
    X, Y = g.mesh()
    plane = 2.0 + 0.1 * X - 0.05 * Y
    q = np.array([[12.3, 45.6], [77.0, 3.3]])
    np.testing.assert_allclose(g.sample(plane, q), 2.0 + 0.1 * q[:, 0] - 0.05 * q[:, 1], atol=1e-9)
    assert np.isnan(g.sample(plane, np.array([[-50.0, 0.0]]))[0])
    assert Grid2D.from_dict(json.loads(json.dumps(g.to_dict()))) == g


def test_polygonize_is_valid_and_complete():
    shapely = pytest.importorskip("shapely")
    from shapely.geometry import Polygon

    g = Grid2D(0.5, 0.5, 1.0, 1.0, 60, 40)
    X, Y = g.mesh()
    labels = (np.hypot(X - 20, Y - 20) < 12).astype(int) + (np.hypot(X - 45, Y - 20) < 8).astype(int) * 2
    labels[18:22, 18:22] = 0  # a hole
    polys = polygonize(labels, g)
    area = {}
    for lab, rings in polys:
        poly = Polygon(rings[0], rings[1:])
        assert poly.is_valid, shapely.validation.explain_validity(poly)
        area[lab] = area.get(lab, 0.0) + poly.area
    for lab in np.unique(labels):
        assert area[int(lab)] == pytest.approx(float((labels == lab).sum()), rel=1e-9)


def test_contours():
    g = Grid2D(0.0, 0.0, 1.0, 1.0, 50, 50)
    X, Y = g.mesh()
    v = 0.2 * X
    levels = contour_levels(v, 1.0)
    lines = contour_lines(g, v, levels)
    assert {round(lv, 6) for lv, _ in lines} <= {round(float(lv), 6) for lv in levels}
    assert len({round(lv, 6) for lv, _ in lines}) >= 9
    for lv, ln in lines:  # straight, one line per level on a planar field
        np.testing.assert_allclose(ln[:, 0], lv / 0.2, atol=1e-6)
    assert len(lines) == len({round(lv, 6) for lv, _ in lines})


@pytest.mark.parametrize("force_bucket", [False, True])
def test_point_index_matches_brute_force(force_bucket):
    rng = np.random.default_rng(3)
    pts = rng.uniform(0, 1000, (500, 2))
    q = rng.uniform(-100, 1100, (100, 2))
    idx = PointIndex(pts, force_bucket=force_bucket)
    d, i = idx.query(q, k=3)
    brute = np.sort(np.hypot(q[:, None, 0] - pts[None, :, 0], q[:, None, 1] - pts[None, :, 1]), axis=1)[:, :3]
    np.testing.assert_allclose(d, brute, atol=1e-9)
    w = idx.within(q[:5], 80.0)
    for qq, ids in zip(q[:5], w):
        assert set(ids.tolist()) == set(np.where(np.hypot(*(pts - qq).T) <= 80.0)[0].tolist())


def test_config_roundtrip_and_hash():
    st = ProjectSettings(name="x", province="london_basin")
    st.model.cell_size_m = 7.5
    st2 = ProjectSettings.from_dict(json.loads(st.to_json()))
    assert st2.model.cell_size_m == 7.5 and isinstance(st2.model, ModelConfig)
    assert stable_hash({"a": 1, "b": [1, 2]}) == stable_hash({"b": [1, 2], "a": 1})
    r = RunRecord("stratigraphic_model", "abc", st.to_dict(), "kb1", 7)
    assert r.run_id and r.created


def test_optional_dependencies():
    assert has("numpy") and optional_import("definitely_not_a_module_xyz") is None
    with pytest.raises(DependencyMissing):
        require("definitely_not_a_module_xyz", "a test feature")
