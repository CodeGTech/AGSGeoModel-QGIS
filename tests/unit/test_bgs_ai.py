"""BGS connector (fixtures only, never the live services), explainable ML, similarity, clustering and
correlation."""

import json

import numpy as np
import pytest
from conftest import has_module

from agsgeomodel.ai import TfidfSimilarity, TrainedFormationModel, cluster_domains, training_set
from agsgeomodel.bgs import (
    BGSGeoConnector,
    HttpCache,
    buffer_polygon,
    candidates_from_records,
    merge_bgs_ags,
    parse_lexicon_turtle,
    rank_candidates,
    set_fetcher,
)
from agsgeomodel.core.errors import ConnectorError
from agsgeomodel.correlation.engine import MODES, correlate


# ---------------------------------------------------------------------------- BGS
def test_paging_and_cache(bgs_fetcher, tmp_path):
    from fixtures.bgs_fixtures import fake_fetcher

    f = fake_fetcher(page_size=1)
    set_fetcher(f)
    con = BGSGeoConnector("EPSG:27700", cache=HttpCache(tmp_path / "c.sqlite"))
    feats = con.items("bgsgeology625kbedrock", (0.0, 51.0, 1.0, 52.0))
    assert len(feats) == 2 and len(f.calls) == 2  # followed the 'next' link
    con.items("bgsgeology625kbedrock", (0.0, 51.0, 1.0, 52.0))
    assert len(f.calls) == 2  # second call served from the cache
    offline = BGSGeoConnector("EPSG:27700", cache=HttpCache(tmp_path / "c.sqlite", offline=True))
    assert len(offline.items("bgsgeology625kbedrock", (0.0, 51.0, 1.0, 52.0))) == 2
    with pytest.raises(ConnectorError):
        offline.items("bgsgeology625kfaults", (0.0, 51.0, 1.0, 52.0))


def test_area_analysis_and_ranking(connector, interpreted):
    p, _ = interpreted
    an, data = connector.analyse(p, 500.0)
    assert an.suggested_province == "london_basin"
    assert {u["code"] for u in an.bedrock_units} == {"LC", "LMBE"}
    assert an.n_faults == 1 and "UKRI" in an.attribution
    assert len(an.buffer_polygon) > 10
    recs = connector.borehole_records(an.bbox_wgs84)
    cands = candidates_from_records(recs)
    ranked = rank_candidates(cands, p, buffer_polygon=an.buffer_polygon, map_units=data["bedrock"], threshold=60.0)
    conf = [c.confidence for c in ranked]
    assert conf == sorted(conf, reverse=True) and all(0 <= c <= 100 for c in conf)
    far = next(c for c in ranked if c.distance_m > 3000)
    assert not far.qualified and far.confidence < 30
    for c in ranked:
        assert set(c.breakdown) == {"proximity", "gap_value", "geology", "elevation", "age", "quality"}
    old = next(c for c in ranked if c.year and "1890" in str(c.year))
    assert any("terminology" in r for r in old.reasons)


def test_ags_export_and_merge(connector, interpreted):
    import copy

    p, _ = interpreted
    files = connector.ags_export(["20190000000000000001", "20190000000000000002"])
    assert files and files[0][0].endswith(".ags")
    q = copy.deepcopy(p)
    merged = merge_bgs_ags(q, files)
    assert len(merged) == 2 and all(m.startswith("BGS:") for m in merged)
    assert all(q.locations[m].source.value == "BGS_AGS" for m in merged)


def test_buffer_polygon_contains_points():
    from agsgeomodel.core.geometry import points_in_polygon

    pts = np.array([[0.0, 0.0], [100.0, 0.0], [50.0, 80.0]])
    poly = buffer_polygon(pts, 250.0)
    assert points_in_polygon(pts, [poly]).all()
    assert points_in_polygon(np.array([[-240.0, 0.0]]), [poly])[0]
    assert not points_in_polygon(np.array([[-260.0, -10.0]]), [poly])[0]


def test_lexicon_turtle_parsing():
    ttl = (
        '<http://data.bgs.ac.uk/id/Lexicon/NamedRockUnit/LC> skos:prefLabel "London Clay Formation"@en ; '
        "skos:broader <http://data.bgs.ac.uk/id/Lexicon/NamedRockUnit/THAM> ; "
        'bgs:hasOldestAgeValue "54.0" ; bgs:hasYoungestAgeValue "49.0" .'
    )
    d = parse_lexicon_turtle(ttl)
    assert d["name"] == "London Clay Formation" and d["parent"] == "THAM" and d["oldest_ma"] == 54.0


# ---------------------------------------------------------------------------- learned formation model
BACKENDS = ["numpy"] + [
    b for b, m in (("sklearn", "sklearn"), ("lightgbm", "lightgbm"), ("xgboost", "xgboost")) if has_module(m)
]


@pytest.mark.parametrize("backend", BACKENDS)
def test_train_save_load_explain(interpreted, tmp_path, backend):
    p, it = interpreted
    ivs = training_set(p, it)
    m = TrainedFormationModel.train(ivs, p, backend=backend)
    card = m.card
    assert card["cv_accuracy"] > 0.8 and card["calibration_temperature"] >= 1.0
    assert "limitations" in card and card["training_data_hash"]
    path = m.save(tmp_path / f"fm_{backend}.json")
    raw = path.read_text()
    assert "pickle" not in raw and json.loads(raw)["backend"] == backend  # plain JSON, no executable payload
    m2 = TrainedFormationModel.load(path)
    P1 = m.predict_proba_intervals(ivs[:20])
    P2 = m2.predict_proba_intervals(ivs[:20])
    np.testing.assert_allclose(P1, P2, atol=1e-6)
    ex = m.explain(ivs[0], p.locations[ivs[0].location_id].ground_level)
    assert ex and all(isinstance(n, str) for n, _ in ex)
    lp = m.log_proba(None, ivs[0])
    assert min(lp.values()) >= np.log(0.0025) - 1e-9  # bounded evidence


def test_learned_evidence_cannot_break_superposition(example_ags, interpreted):
    from agsgeomodel.geology import GeoReasoner
    from agsgeomodel.importers.ags import import_ags

    p0, it0 = interpreted
    m = TrainedFormationModel.train(training_set(p0, it0), p0, backend="numpy")
    p = import_ags(example_ags).project
    it = GeoReasoner("london_basin", learned=m).interpret(p)
    col = it.column
    for lid in it.locations:
        runs = it.runs(lid)
        for a, b in zip(runs[:-1], runs[1:]):
            assert not col.is_above(b.unit, a.unit)


def test_similarity_and_clustering(interpreted):
    p, it = interpreted
    sim = TfidfSimilarity().fit([iv.description for iv in p.intervals])
    M = sim.matrix(["Stiff grey fissured CLAY"], ["Stiff dark grey fissured CLAY with selenite", "Dense flint GRAVEL"])
    assert M[0, 0] > M[0, 1]
    res = cluster_domains(p, it, it.column)
    assert 2 <= res.k <= 6 and len(res.labels) == len(res.location_ids) and -1 <= res.silhouette <= 1
    assert all(res.explanations[j] for j in range(res.k))


@pytest.mark.parametrize("mode", MODES)
def test_correlation_modes(interpreted, mode):
    p, it = interpreted
    links = correlate(p, it, mode, faults=p.faults)
    assert links
    for ln in links:
        assert 0.0 <= ln.confidence <= 1.0 and isinstance(ln.flags, list)
        f = ln.feature()
        assert f["geometry"]["type"] == "LineString" and f["properties"]["mode"] == mode
    if mode == "nearest_neighbour":
        assert all(any("not geologically constrained" in r for r in ln.flags) for ln in links)
    if mode == "rules_constrained":
        across = [ln for ln in links if any("fault" in r for r in ln.flags)]
        assert across, "links across F1 must say so"
        assert all(ln.unit_a == ln.unit_b for ln in links)
