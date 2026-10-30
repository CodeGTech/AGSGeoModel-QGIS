"""CPT / SPT correlations, PSD indices, engineering ground model, hydrogeology and HDD assessment."""

import math

import numpy as np
import pytest

from agsgeomodel.hdd import Alignment, HDDDesign, assess_alignment, delft_pmax, design_profile, explicit_profile
from agsgeomodel.importers.ags.mapper import psd_indices
from agsgeomodel.models.entities import SPTRecord
from agsgeomodel.processors import CPTSettings, interpret_cpt, interpret_spt, sbtn_zone, summarise, t95

# ---------------------------------------------------------------------------- CPT


def _sounding():
    z = np.arange(0.5, 20.01, 0.5)
    qc = np.where(z < 8, 0.8 + 0.05 * z, 12.0 + 0.4 * (z - 8))  # clay over dense sand
    fs = np.where(z < 8, 0.04, 0.08)
    u2 = np.where(z < 8, 0.25 + 0.02 * z, 0.05 + 0.0098 * z)
    return z, qc, fs, u2


def test_cpt_correlations_follow_the_documented_equations():
    z, qc, fs, u2 = _sounding()
    r = interpret_cpt(z, qc, fs, u2, settings=CPTSettings(nkt=15.0), area_ratio=0.75, water_depth=1.0)
    c = r.columns
    np.testing.assert_allclose(c["qt_MPa"], qc + u2 * 0.25)
    qnet = c["qt_MPa"] - c["sv0_kPa"] / 1000
    fine = c["Ic"] > 2.6
    assert fine[2:14].all() and (~fine[-10:]).all()  # clay then sand
    np.testing.assert_allclose(c["su_kPa"][fine], qnet[fine] * 1000 / 15.0, rtol=1e-9)
    assert np.isnan(c["su_kPa"][~fine]).all() and np.isnan(c["Dr"][fine]).all()
    ic = np.sqrt((3.47 - np.log10(c["Qtn"])) ** 2 + (np.log10(c["Fr_pct"]) + 1.22) ** 2)
    np.testing.assert_allclose(c["Ic"], ic, rtol=1e-9)
    np.testing.assert_allclose(c["phi_deg"][~fine], 17.6 + 11 * np.log10(c["Qtn"][~fine]), rtol=1e-9)
    assert np.isnan(c["E_MPa"][fine]).all()  # E' only for coarse soils
    np.testing.assert_allclose(c["u0_kPa"], np.maximum(z - 1.0, 0) * 9.81, atol=1e-9)
    assert np.all(np.diff(c["sv0_kPa"]) > 0) and np.all(c["gamma_kNm3"] >= 14.0)
    assert set(np.unique(c["SBTn"]).astype(int)) <= {2, 3, 4, 5, 6, 7}


def test_sbtn_boundaries():
    assert sbtn_zone(np.array([1.0, 1.5, 2.3, 2.8, 3.2, 3.8])).tolist() == [7, 6, 5, 4, 3, 2]


def test_cpt_without_u2_and_water_notes():
    z, qc, fs, _ = _sounding()
    r = interpret_cpt(z, qc, fs)
    assert any("no u2" in n for n in r.notes) and any("groundwater" in n for n in r.notes)
    np.testing.assert_allclose(r.columns["qt_MPa"], qc)


# ---------------------------------------------------------------------------- SPT
def test_spt_corrections():
    recs = [
        SPTRecord("BH1", 10.0, 20.0, energy_ratio=75.0, water_depth=2.0),
        SPTRecord("BH1", 12.0, 30.0, energy_ratio=75.0, water_depth=2.0),
        SPTRecord("BH1", 5.0, None),
    ]
    out = interpret_spt(
        recs,
        unit_lookup=lambda l, d: "TAB" if d > 11 else "LC",
        material_lookup=lambda u: "coarse" if u == "TAB" else "fine",
    )
    assert len(out) == 2
    clay, sand = out
    assert clay.n60 == pytest.approx(25.0) and clay.cu_kPa == pytest.approx(4.5 * 20)
    s_eff = 19.0 * 12 - 9.81 * 10
    n1 = min((101.325 / s_eff) ** 0.5, 1.7) * 37.5
    assert sand.n1_60 == pytest.approx(n1)
    assert sand.dr == pytest.approx(math.sqrt(n1 / 60)) and sand.phi_deg == pytest.approx(math.sqrt(20 * n1) + 20)


# ---------------------------------------------------------------------------- statistics
def test_t95_and_characteristic_values():
    assert (
        t95(1) == pytest.approx(6.314)
        and t95(10) == pytest.approx(1.812)
        and t95(1000) == pytest.approx(1.645, abs=0.02)
    )
    v = np.array([60.0, 75.0, 80.0, 95.0, 110.0, 120.0, 135.0, 150.0])
    st = summarise(v, np.linspace(5, 25, len(v)), "LC", "TRIT_CU")
    assert st.n == 8 and st.mean == pytest.approx(v.mean())
    assert st.cautious_mean_low == pytest.approx(v.mean() * (1 - 0.5 * st.cov))
    assert 0 < st.fractile5_low < st.cautious_mean_low < st.mean < st.cautious_mean_high
    assert st.trend_slope is not None and st.trend_slope > 0  # strength increases with depth
    # lognormal fractile for a positive parameter never goes negative
    st2 = summarise(np.array([5.0, 50.0, 150.0]), np.array([1.0, 2.0, 3.0]), "MGR", "TRIT_CU")
    assert st2.fractile5_low > 0


def test_psd_indices():
    size = [0.002, 0.006, 0.02, 0.063, 0.15, 0.3, 0.6, 1.18, 2.0, 6.3, 20, 37.5, 63]
    perp = [1, 2, 3, 5, 9, 15, 24, 33, 40, 58, 82, 95, 100]
    r = psd_indices(size, perp)
    assert r["FINES"] == pytest.approx(5.0) and r["GRAV"] == pytest.approx(60.0) and r["SAND"] == pytest.approx(35.0)
    assert 0.15 < r["D10"] < 0.3 and r["CU"] == pytest.approx(r["D60"] / r["D10"])
    assert psd_indices([1.0], [50.0]) == {}


def test_ground_model_on_synthetic_site(example_project):
    _, res = example_project
    gm = res.ground_model
    assert gm is not None and gm.stats
    lc_su = gm.get("LC", "CPT_su") or gm.get("LC", "SPT_cu")
    assert lc_su is not None and 30 < lc_su.mean < 400
    # correlations restricted to their material class
    for (unit, par), _st in gm.stats.items():
        if par in ("CPT_phi", "CPT_Dr", "CPT_E"):
            assert unit not in ("LC", "ALV", "PEAT")


# ---------------------------------------------------------------------------- hydrogeology
def test_hydrogeology_on_synthetic_site(example_project):
    _, res = example_project
    h = res.hydro
    assert set(h.surfaces) >= {"shallow", "deep"}
    for _sid, conf in h.confinement.items():
        assert set(np.unique(conf[np.isfinite(conf)]).astype(int)) <= {-1, 0, 1, 2, 3}
    assert h.system_of.get("TAB") == "deep" and h.system_of.get("RTDU") == "shallow"
    assert h.flow_vectors.get("deep")


# ---------------------------------------------------------------------------- HDD
def test_design_profile_geometry():
    des = HDDDesign(entry_angle_deg=12.0, exit_angle_deg=10.0, radius_m=250.0, design_elevation=-14.0)
    prof = design_profile(500.0, 5.0, 6.0, des)
    z = prof.elevation_at(np.array([0.0, 500.0]))
    assert z[0] == pytest.approx(5.0, abs=0.05) and z[1] == pytest.approx(6.0, abs=0.05)
    assert prof.elevation_at(np.array([250.0]))[0] == pytest.approx(-14.0, abs=0.05)
    slope0 = prof.slope_at(np.array([0.5]))[0]
    assert math.degrees(math.atan(abs(slope0))) == pytest.approx(12.0, abs=0.5)
    assert prof.bore_length > 500.0
    ep = explicit_profile([0, 100, 200], [0, -10, 0])
    assert ep.elevation_at(np.array([50.0]))[0] == pytest.approx(-5.0)


def test_delft_undrained_limit():
    # undrained (phi = 0): p_max = u + s'v0 + cu * (1 + ln(G / cu * (Ro/Rp)^2 + ...)) style bound;
    # check monotonic behaviour and the closed-form limit for a large plastic zone
    p1 = delft_pmax(sigma_v0_eff=100.0, u=50.0, c=50.0, phi_deg=0.0, G=5000.0, Ro=0.2, Rp_max=2.0)
    p2 = delft_pmax(sigma_v0_eff=100.0, u=50.0, c=100.0, phi_deg=0.0, G=10000.0, Ro=0.2, Rp_max=2.0)
    p3 = delft_pmax(sigma_v0_eff=100.0, u=50.0, c=50.0, phi_deg=0.0, G=5000.0, Ro=0.2, Rp_max=4.0)
    assert p2 > p1 > 150.0 and p3 > p1
    limit = 50.0 + 100.0 + 50.0 * (1 + math.log(5000.0 / 50.0))  # cavity expansion limit pressure
    assert p1 < limit + 1e-6


def test_hdd_assessment_on_synthetic_site(example_project):
    _, res = example_project
    a = res.hdd
    assert a is not None and a.stations
    assert a.summary["overall_rag"] in ("Green", "Amber", "Red")
    hazards = {z["hazard"] for z in a.risk_zones}
    assert hazards <= {
        "running_sand",
        "groundwater",
        "boulders",
        "rock",
        "mixed_face",
        "loss_of_circulation",
        "overall",
    }
    assert a.formation_changes  # the bore crosses contacts
    for s in a.stations:
        assert s.cover >= -1e-6 or s.chainage in (a.stations[0].chainage, a.stations[-1].chainage)
        assert s.frac_fos is None or s.frac_fos > 0
    # crossings are checked
    assert any(c.get("kind") == "crossing" or "clearance" in str(c) for c in a.constraints)


def test_hdd_alignment_stations(model):
    from agsgeomodel.examples.synthetic import HDD_ENTRY, HDD_EXIT

    line = np.array([HDD_ENTRY, HDD_EXIT])
    L = float(np.hypot(*(line[1] - line[0])))
    zg = model.sample_grid(model.surfaces["ground"].values, line)
    des = HDDDesign(design_elevation=-14.0)
    al = Alignment(line, design_profile(L, float(zg[0]), float(zg[1]), des), des, "T")
    ch, xy, z = al.stations(5.0)
    assert ch[0] == 0 and ch[-1] == pytest.approx(L) and len(xy) == len(z)
    a = assess_alignment(al, model)
    assert len(a.stations) > 50 and a.summary["overall_rag"]
