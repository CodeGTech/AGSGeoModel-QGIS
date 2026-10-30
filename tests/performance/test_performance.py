"""Scale tests (run with ``pytest -m performance``): 1,000 holes / ~100,000 logged intervals.

Timings are generous upper bounds for CI runners; typical laptop figures are in docs/verification.md.
"""

import time

import numpy as np
import pytest

from agsgeomodel.examples.synthetic import DESCRIPTIONS, E0, N0, SyntheticTruth
from agsgeomodel.geology import GeoReasoner
from agsgeomodel.models.entities import Interval, Location
from agsgeomodel.models.enums import LocationKind
from agsgeomodel.models.project import Project

pytestmark = pytest.mark.performance


@pytest.fixture(scope="module")
def big_project():
    rng = np.random.default_rng(0)
    tr = SyntheticTruth()
    p = Project("perf", "EPSG:27700")
    for k in range(1000):
        x, y = E0 + rng.uniform(0, 1200), N0 + rng.uniform(0, 800)
        g = float(tr.ground(x, y))
        lid = f"H{k:04d}"
        p.locations[lid] = Location(lid, x, y, g, 60.0, LocationKind.BOREHOLE)
        for unit, zt, zb in tr.column_at(x, y):
            top, base = max(0.0, g - zt), (min(60.0, g - zb) if np.isfinite(zb) else 60.0)
            if base <= top:
                continue
            edges = np.linspace(top, base, max(1, int((base - top) / 0.6)) + 1)
            for a, b in zip(edges[:-1], edges[1:]):
                opts = DESCRIPTIONS[unit]
                p.intervals.append(Interval(lid, float(a), float(b), opts[rng.integers(0, len(opts))]))
            if base >= 60:
                break
    return p, tr


def test_reasoner_100k_intervals(big_project):
    p, tr = big_project
    assert len(p.intervals) > 90_000
    t = time.time()
    it = GeoReasoner("london_basin").interpret(p)
    dt = time.time() - t
    ok = tot = 0.0
    for iv in p.intervals:
        loc = p.locations[iv.location_id]
        z = loc.ground_level - iv.mid
        true = next((u for u, a, b in tr.column_at(loc.x, loc.y) if b < z <= a + 1e-9), "CK")
        tot += iv.thickness
        ok += iv.thickness * (iv.unit == true)
    assert ok / tot > 0.98
    assert dt < 300, f"interpretation took {dt:.0f} s"
    big_project[0].metadata["interp"] = it


def test_model_1000_holes(big_project):
    from agsgeomodel.core.config import ModelConfig
    from agsgeomodel.modelling import ModelInputs, StratigraphicModelBuilder

    p, tr = big_project
    it = p.metadata.get("interp") or GeoReasoner("london_basin").interpret(p)
    t = time.time()
    m = StratigraphicModelBuilder(ModelConfig(cell_size_m=5.0, buffer_m=100.0)).build(
        ModelInputs(it.observations, it.column, p.located(), crs=p.crs)
    )
    dt = time.time() - t
    X, Y = m.grid.mesh()
    inner = (X > E0 + 50) & (X < E0 + 1150) & (Y > N0 + 50) & (Y < N0 + 750)
    err = (m.surfaces["base:LC"].values - tr.bases(X, Y)["LC"])[inner]
    assert np.sqrt(np.nanmean(err**2)) < 1.0
    assert dt < 600, f"model build took {dt:.0f} s"
