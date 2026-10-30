"""Property-based tests (Hypothesis): invariants that must hold for any input."""

import numpy as np
import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agsgeomodel.core.geometry import point_at_chainage, points_in_ring, polyline_chainage
from agsgeomodel.core.grid import Grid2D, polygonize
from agsgeomodel.geology import GeoReasoner, load_column
from agsgeomodel.modelling import OrdinaryKriging, Variogram
from agsgeomodel.models.entities import Interval, Location
from agsgeomodel.models.enums import LocationKind
from agsgeomodel.models.project import Project

COLUMN = load_column("london_basin")
DESCRIPTIONS = [
    "MADE GROUND: brick and concrete",
    "Soft grey organic silty CLAY",
    "Dense brown flint GRAVEL",
    "Stiff grey fissured CLAY with selenite",
    "Very stiff red and grey mottled CLAY",
    "Dense green glauconitic SAND with rounded flint pebbles",
    "Very dense grey silty fine SAND",
    "Weak white CHALK with flints",
    "No recovery",
    "Firm brown sandy CLAY",
    "Black fibrous PEAT",
]


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.lists(st.tuples(st.sampled_from(DESCRIPTIONS), st.floats(0.3, 6.0)), min_size=1, max_size=12),
    st.floats(-5.0, 30.0),
)
def test_interpretation_never_violates_superposition(layers, gl):
    p = Project("h", "EPSG:27700")
    depth = sum(t for _, t in layers)
    p.locations["H"] = Location("H", 545000.0, 180000.0, gl, depth, LocationKind.BOREHOLE)
    top = 0.0
    for desc, t in layers:
        p.intervals.append(Interval("H", top, top + t, desc))
        top += t
    it = GeoReasoner(COLUMN).interpret(p)
    runs = it.runs("H")
    assert runs and runs[0].top == pytest.approx(0.0) and runs[-1].base == pytest.approx(depth)
    for a, b in zip(runs[:-1], runs[1:]):
        assert b.top == pytest.approx(a.base)
        assert not COLUMN.is_above(b.unit, a.unit)
    for iv in p.intervals:
        assert iv.unit in COLUMN and 0.0 <= iv.probability <= 1.0


@settings(max_examples=30, deadline=None)
@given(st.integers(0, 10_000), st.integers(2, 5))
def test_polygonize_partitions_the_grid(seed, n_labels):
    shapely = pytest.importorskip("shapely")
    from shapely.geometry import Polygon

    rng = np.random.default_rng(seed)
    g = Grid2D(0.5, 0.5, 1.0, 1.0, 24, 18)
    # smooth-ish random label field
    base = rng.normal(size=(6, 8))
    field = np.kron(base, np.ones((3, 3)))
    labels = np.digitize(field, np.quantile(field, np.linspace(0, 1, n_labels + 1)[1:-1]))
    total = 0.0
    for _lab, rings in polygonize(labels, g):
        poly = Polygon(rings[0], rings[1:])
        assert poly.is_valid
        total += poly.area
    assert total == pytest.approx(g.nx * g.ny)
    del shapely


@settings(max_examples=40, deadline=None)
@given(
    st.lists(st.tuples(st.floats(-1e3, 1e3), st.floats(-1e3, 1e3)), min_size=2, max_size=8, unique=True),
    st.floats(0.0, 1.0),
)
def test_chainage_roundtrip(pts, frac):
    line = np.array(pts)
    ch = polyline_chainage(line)
    if ch[-1] < 1e-6:
        return
    c = frac * ch[-1]
    p = point_at_chainage(line, [c])[0]
    from agsgeomodel.core.geometry import project_points_to_polyline

    cc, off, _ = project_points_to_polyline(p[None], line)
    assert abs(off[0]) < 1e-6 * max(1.0, ch[-1])
    # projection may land on an earlier vertex of a self-overlapping line, never further along
    assert cc[0] <= c + 1e-6 * max(1.0, ch[-1])


@settings(max_examples=25, deadline=None)
@given(st.integers(0, 10_000))
def test_kriging_interpolates_and_bounds(seed):
    rng = np.random.default_rng(seed)
    xy = rng.uniform(0, 100, (25, 2))
    z = rng.normal(0, 3, 25)
    ok = OrdinaryKriging(Variogram("spherical", 0.0, 9.0, 60.0)).fit(xy, z)
    pred, std = ok.predict(xy)
    np.testing.assert_allclose(pred, z, atol=1e-5)
    q = rng.uniform(0, 100, (50, 2))
    pq, sq = ok.predict(q)
    # ordinary-kriging variance can exceed the sill slightly (Lagrange term) but stays bounded
    assert np.all(np.isfinite(pq)) and np.all(sq >= 0) and np.all(sq**2 <= 2 * 9.0 + 1e-6)


@settings(max_examples=40, deadline=None)
@given(st.floats(-50, 150), st.floats(-50, 150))
def test_point_in_ring_square(x, y):
    ring = np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0], [0.0, 0.0]])
    inside = bool(points_in_ring(np.array([[x, y]]), ring)[0])
    if 1e-6 < x < 100 - 1e-6 and 1e-6 < y < 100 - 1e-6:
        assert inside
    elif x < -1e-6 or x > 100 + 1e-6 or y < -1e-6 or y > 100 + 1e-6:
        assert not inside
