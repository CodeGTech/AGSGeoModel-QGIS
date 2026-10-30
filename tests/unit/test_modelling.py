"""Interpolation (kriging, RBF, IDW, TIN), constraints, fault barriers, uncertainty and the model builder."""

import numpy as np
import pytest

from agsgeomodel.core.config import UncertaintyConfig
from agsgeomodel.core.grid import Grid2D
from agsgeomodel.examples.synthetic import E0, N0, SyntheticTruth
from agsgeomodel.modelling import (
    IDW,
    RBF,
    TIN,
    FaultBarrier,
    OrdinaryKriging,
    Variogram,
    classify,
    confidence_from_std,
    fit_constrained,
    fit_variogram,
    rag_from_confidence,
)
from agsgeomodel.modelling.stochastic import grf
from agsgeomodel.models.entities import Fault

RNG = np.random.default_rng(42)
XY = RNG.uniform(0, 500, (40, 2))
PLANE = 10.0 + 0.02 * XY[:, 0] - 0.01 * XY[:, 1]


# ---------------------------------------------------------------------------- kriging
def test_kriging_is_exact_at_data_without_measurement_error():
    z = np.sin(XY[:, 0] / 80.0) * 3 + PLANE
    ok = OrdinaryKriging(Variogram("spherical", 0.0, 4.0, 250.0)).fit(XY, z)
    pred, std = ok.predict(XY)
    np.testing.assert_allclose(pred, z, atol=1e-6)
    assert np.all(std < 1e-3)


def test_kriging_std_grows_away_from_data():
    ok = OrdinaryKriging(Variogram("exponential", 0.01, 2.0, 200.0)).fit(XY, PLANE, 0.1)
    near = XY[:5] + 1.0
    far = np.array([[900.0, 900.0], [-400.0, 50.0]])
    _, s_near = ok.predict(near)
    _, s_far = ok.predict(far)
    assert s_far.min() > s_near.max()


def test_measurement_error_smooths():
    z = PLANE + RNG.normal(0, 0.5, len(PLANE))
    exact = OrdinaryKriging(Variogram("spherical", 0.0, 1.0, 300.0)).fit(XY, z).predict(XY, return_std=False)
    smooth = OrdinaryKriging(Variogram("spherical", 0.0, 1.0, 300.0)).fit(XY, z, 0.5).predict(XY, return_std=False)
    assert np.abs(exact - z).max() < 1e-6 and np.abs(smooth - z).mean() > 0.05


def test_universal_kriging_reproduces_a_plane():
    ok = OrdinaryKriging(Variogram("spherical", 0.0, 1.0, 100.0), drift="linear").fit(XY, PLANE)
    q = RNG.uniform(50, 450, (30, 2))
    pred = ok.predict(q, return_std=False)
    np.testing.assert_allclose(pred, 10.0 + 0.02 * q[:, 0] - 0.01 * q[:, 1], atol=1e-3)


def test_fault_barrier_separates_blocks():
    fault = Fault("F", np.array([[250.0, -100.0], [250.0, 600.0]]))
    z = np.where(XY[:, 0] < 250, 0.0, 10.0)
    ok = OrdinaryKriging(
        Variogram("spherical", 0.0, 25.0, 1000.0), barrier=FaultBarrier.from_faults([fault]), use_throws=False
    ).fit(XY, z)
    west = ok.predict(np.array([[240.0, 250.0]]), return_std=False)[0]
    east = ok.predict(np.array([[260.0, 250.0]]), return_std=False)[0]
    assert west < 1.0 and east > 9.0
    plain = OrdinaryKriging(Variogram("spherical", 0.0, 25.0, 1000.0)).fit(XY, z)
    w2 = plain.predict(np.array([[240.0, 250.0]]), return_std=False)[0]
    assert w2 > west + 1.0  # without the barrier the step is smeared across the fault


def test_variogram_fit_recovers_range():
    g = Grid2D(0.0, 0.0, 5.0, 5.0, 120, 120)
    vg_true = Variogram("spherical", 0.0, 4.0, 150.0)
    field = grf(g, vg_true, np.random.default_rng(1), 1)[0]
    idx = np.random.default_rng(2).choice(g.size, 600, replace=False)
    pts = g.nodes()[idx]
    vg = fit_variogram(pts, field.ravel()[idx], "spherical")
    assert vg.fitted and 60.0 < vg.range < 350.0
    assert 1.5 < vg.total_sill < 9.0


def test_sparse_data_get_a_conservative_default():
    vg = fit_variogram(XY[:4], np.array([1.0, 1.0, 1.01, 1.0]), var_floor=1.0)
    assert not vg.fitted and vg.total_sill >= 1.0


# ---------------------------------------------------------------------------- other interpolators
@pytest.mark.parametrize("cls", [IDW, RBF, TIN])
def test_interpolators_honour_data(cls):
    m = cls().fit(XY, PLANE)
    pred = m.predict(XY[3:8], return_std=False)
    np.testing.assert_allclose(pred, PLANE[3:8], atol=1e-6)


def test_tin_is_linear_inside_triangles():
    m = TIN().fit(XY, PLANE)
    q = np.array([[250.0, 250.0], [200.0, 300.0]])
    np.testing.assert_allclose(m.predict(q, return_std=False), 10.0 + 0.02 * q[:, 0] - 0.01 * q[:, 1], atol=1e-6)


# ---------------------------------------------------------------------------- inequality constraints
def test_inequality_constraints_are_honoured():
    eq_xy = np.array([[0.0, 0.0], [500.0, 0.0], [0.0, 500.0], [500.0, 500.0]])
    eq_v = np.array([0.0, 0.0, 0.0, 0.0])
    ge_xy = np.array([[250.0, 250.0], [240.0, 260.0], [100.0, 400.0]])  # holes ended above the surface
    ge_v = np.array([-5.0, -5.5, -4.0])  # the surface must be at least this deep... (z <= ...)
    le_xy = np.array([[250.0, 250.0]])
    le_v = np.array([-3.0])  # ... and below -3 here: surface <= -3 at the centre

    def factory():
        return OrdinaryKriging(Variogram("spherical", 0.0, 4.0, 400.0))

    model, diag, _ = fit_constrained(factory, eq_xy, eq_v, 0.05, ge_xy=ge_xy, ge_v=ge_v, le_xy=le_xy, le_v=le_v)
    assert diag.remaining_violations == 0
    assert model.predict(le_xy, return_std=False)[0] <= -3.0 + 0.05
    assert np.all(model.predict(ge_xy, return_std=False) >= ge_v - 0.05)


# ---------------------------------------------------------------------------- uncertainty classes
def test_classification_and_rag():
    g = Grid2D(5.0, 5.0, 10.0, 10.0, 50, 50)
    holes = np.array([[105.0, 105.0], [405.0, 105.0], [255.0, 405.0]])  # on grid nodes
    from agsgeomodel.core.spatial import PointIndex

    d = PointIndex(holes).nearest_distance(g.nodes()).reshape(g.shape)
    std = 0.1 + d / 25.0
    cls = classify(g, std, holes, None, UncertaintyConfig())
    j, i = int(round((105.0 - g.y0) / g.dy)), int(round((105.0 - g.x0) / g.dx))
    assert cls[j, i] == 0  # Observed at a hole
    assert cls[-1, -1] == 3  # Speculative far outside the data
    assert set(np.unique(cls)) <= {0, 1, 2, 3}
    conf = confidence_from_std(std, UncertaintyConfig())
    assert np.all(np.diff(np.sort(conf.ravel())[::-1]) <= 0)
    rag = rag_from_confidence(np.array([0.9, 0.5, 0.1]), UncertaintyConfig())
    assert rag.tolist() == [0, 1, 2]


# ---------------------------------------------------------------------------- stochastic
def test_grf_has_the_requested_variance():
    g = Grid2D(0.0, 0.0, 10.0, 10.0, 80, 80)
    sims = grf(g, Variogram("exponential", 0.0, 2.0, 100.0), np.random.default_rng(0), 20)
    assert sims.shape == (20, 80, 80)
    assert 1.0 < sims.var() < 3.0 and abs(sims.mean()) < 0.5


# ---------------------------------------------------------------------------- full model on the synthetic site
def _truth(model):
    tr = SyntheticTruth()
    X, Y = model.grid.mesh()
    inner = (X > E0 + 80) & (X < E0 + 1120) & (Y > N0 + 80) & (Y < N0 + 720)
    return tr, X, Y, inner


def test_surfaces_close_to_truth(model):
    tr, X, Y, inner = _truth(model)
    truth = {"rockhead": tr.rockhead(X, Y), **{f"base:{u}": v for u, v in tr.bases(X, Y).items()}}
    limits = {"rockhead": 1.6, "base:LC": 2.5, "base:HWH": 2.5, "base:LMBE": 2.0, "base:TAB": 2.0}
    for name, lim in limits.items():
        if name not in model.surfaces or name not in truth:
            continue
        err = (model.surfaces[name].values - truth[name])[inner]
        rmse = float(np.sqrt(np.nanmean(err**2)))
        assert rmse < lim, f"{name} RMSE {rmse:.2f} m"
        z = np.abs(err) / np.maximum(model.surfaces[name].std[inner], 1e-6)
        assert np.mean(z[np.isfinite(z)] < 2.0) > 0.9, f"{name}: uncertainty too small for the actual error"


def test_model_is_geologically_consistent(model):
    for u in model.units:
        t, b = model.tops[u], model.bases[u]
        ok = np.isfinite(t) & np.isfinite(b)
        assert np.all(t[ok] >= b[ok] - 1e-6), f"{u}: base above top"
    ground = model.surfaces["ground"].values
    for u in model.units:
        ok = np.isfinite(model.tops[u])
        assert np.all(model.tops[u][ok] <= ground[ok] + 1e-6)
    # units stack without gaps or overlaps
    col = model.column_at(E0 + 500.0, N0 + 400.0)
    for (_u1, _t1, b1), (_u2, t2, _b2) in zip(col[:-1], col[1:]):
        assert t2 == pytest.approx(b1, abs=1e-6)


def test_fault_offsets_the_bedrock(model):
    tr, X, Y, _ = _truth(model)
    a = model.sample("base:LC", np.array([[E0 + 880.0, N0 + 300.0]]))[0]  # west of F1
    b = model.sample("base:LC", np.array([[E0 + 940.0, N0 + 300.0]]))[0]  # east (downthrown)
    assert a - b > 1.5


def test_classes_observed_at_holes_that_reach_the_surface(model, interpreted):
    p, it = interpreted
    sg = model.surfaces["rockhead"]
    kb = it.column.kb
    reached = [l for l in p.located() if any(kb[r.unit].unit_class == "bedrock" for r in it.runs(l.id))]
    assert len(reached) >= 5
    xy = np.array([[l.x, l.y] for l in reached])
    cls = model.grid.sample(sg.classes.astype(float), xy, method="nearest")
    assert np.mean(cls == 0) >= 0.8
    assert sg.classes[0, 0] >= 2 and sg.classes[-1, -1] >= 2  # grid corners, outside the data


def test_monte_carlo_probabilities(model, interpreted):
    p, _ = interpreted
    model.simulate(16, seed=5)
    loc = next(l for l in p.located() if l.id == "BH03")
    units, P = model.probability(np.array([[loc.x, loc.y]] * 3), np.array([loc.ground_level - 1.0, -12.0, -60.0]))
    assert P.shape == (3, len(units))
    np.testing.assert_allclose(P.sum(axis=1), 1.0, atol=1e-9)
    assert P[2, units.index("CK")] > 0.9  # deep -> chalk
