"""Knowledge base, BS 5930 description parsing, order-constrained sequence inference and the GeoReasoner."""

import numpy as np
import pytest

from agsgeomodel.core.config import ReasonerConfig
from agsgeomodel.examples.synthetic import SyntheticTruth
from agsgeomodel.geology import GeoReasoner, load_column, parse_description, proposal_key
from agsgeomodel.geology.knowledge.kb import available_provinces
from agsgeomodel.models.entities import Interval, Location, Override, ReviewDecision
from agsgeomodel.models.enums import LocationKind
from agsgeomodel.models.project import Project

# ---------------------------------------------------------------------------- knowledge base


@pytest.mark.parametrize("province", available_provinces())
def test_columns_are_consistent(province):
    col = load_column(province)
    assert col.kb.validate() == []
    assert col.check_age_order() == []
    bedrock = [s for s in col.sequences if s.kind == "bedrock"]
    assert bedrock and bedrock[0].order == "strict"
    codes = bedrock[0].codes
    ages = [col.kb[c].age_min_ma for c in codes]
    assert ages == sorted(ages), "strict bedrock sequence must get older downwards"
    for a, b in zip(codes[:-1], codes[1:]):
        assert col.is_above(a, b) and not col.is_above(b, a)
    for sys_ in col.aquifer_systems:
        assert set(sys_["aquifer_units"]) <= set(col.units)


def test_lexicon_codes_and_urls():
    kb = load_column("london_basin").kb
    for code in ("LC", "HWH", "LMBE", "TAB", "CK", "ALV", "RTDU", "MGR", "PEAT", "HEAD"):
        assert code in kb
    assert kb["LC"].name == "London Clay Formation"
    assert kb["LC"].lexicon_url.endswith("pub=LC")
    col = load_column("london_basin")
    assert col.contact_type("RTDU", "LC").value == "erosional"
    assert col.between("LC", "TAB") == ["HWH", "LMBE"]
    assert col.resolve("THAM") == {"LC", "HWH"}  # Thames Group -> its formations


# ---------------------------------------------------------------------------- description parser
@pytest.mark.parametrize(
    "text,material,features",
    [
        (
            "Stiff grey fissured CLAY with occasional selenite crystals",
            "clay",
            {"pt:clay", "cons:stiff", "struct:fissured", "incl:selenite"},
        ),
        (
            "Dense brown sandy subangular to subrounded fine to coarse flint GRAVEL",
            "gravel",
            {"pt:gravel", "dens:dense", "sec:sandy", "incl:flint"},
        ),
        (
            "Structureless CHALK composed of soft white putty with flints (Grade Dm)",
            "chalk",
            {"pt:chalk", "chalk:grade_dm", "struct:structureless"},
        ),
        ("MADE GROUND: brick, concrete and ash", "made_ground", {"pt:made_ground", "incl:brick", "geo:MGR"}),
        ("Soft dark grey organic silty CLAY with pockets of PEAT", "clay", {"sec:organic", "incl:peat", "cons:soft"}),
        ("No recovery", "unknown", {"rec:none"}),
    ],
)
def test_parse_description(text, material, features):
    p = parse_description(text)
    assert p.material == material
    assert features <= set(p.features)


# ---------------------------------------------------------------------------- sequence inference
def _hole(strata, gl=5.0, lid="T1"):
    p = Project("t", "EPSG:27700")
    depth = max(b for _, b, _ in strata)
    p.locations[lid] = Location(lid, 545500.0, 180300.0, gl, depth, LocationKind.BOREHOLE)
    for top, base, desc in strata:
        p.intervals.append(Interval(lid, top, base, desc))
    return p


def test_order_is_never_violated():
    # descriptions that, read naively, put Thanet sand above London Clay
    p = _hole(
        [
            (0.0, 2.0, "MADE GROUND: brick and concrete"),
            (2.0, 6.0, "Very dense grey glauconitic silty fine SAND (Thanet Formation)"),
            (6.0, 20.0, "Stiff grey fissured CLAY with selenite"),
            (20.0, 30.0, "Very dense grey silty fine SAND with glauconite"),
        ]
    )
    it = GeoReasoner("london_basin").interpret(p)
    col = it.column
    runs = it.runs("T1")
    for a, b in zip(runs[:-1], runs[1:]):
        assert not col.is_above(b.unit, a.unit), f"{b.unit} placed below {a.unit}"


def test_missing_layer_inference_in_unlogged_interval():
    p = _hole(
        [
            (0.0, 1.0, "MADE GROUND"),
            (1.0, 14.0, "Stiff grey fissured CLAY with selenite (LONDON CLAY)"),
            (14.0, 24.0, "No recovery"),
            (24.0, 35.0, "Very dense greenish grey glauconitic silty fine SAND (THANET FORMATION)"),
        ]
    )
    it = GeoReasoner("london_basin").interpret(p)
    units = [r.unit for r in it.runs("T1")]
    assert units[0] == "MGR" and "LC" in units and units[-1] == "TAB"
    gap = [r for r in it.runs("T1") if r.top >= 14.0 - 1e-6 and r.base <= 24.0 + 1e-6]
    assert gap and all(r.unit in ("LC", "HWH", "LMBE", "TAB") for r in gap)
    iv = next(i for i in p.intervals if i.top == 14.0)
    assert iv.needs_review and iv.status.value == "inferred"


def test_override_wins_and_acceptance_clears_review():
    p = _hole([(0.0, 10.0, "Stiff grey CLAY"), (10.0, 12.0, "No recovery"), (12.0, 20.0, "Dense grey SAND")])
    GeoReasoner("london_basin").interpret(p)
    gap = next(i for i in p.intervals if i.top == 10.0)
    assert gap.needs_review
    # acceptance of the same proposal clears the flag
    q = _hole([(0.0, 10.0, "Stiff grey CLAY"), (10.0, 12.0, "No recovery"), (12.0, 20.0, "Dense grey SAND")])
    it1 = GeoReasoner("london_basin").interpret(q)
    runs = [r for r in it1.runs("T1") if r.base > 10.0 + 1e-6 and r.top < 12.0 - 1e-6]
    key = proposal_key([r.unit for r in runs])
    q2 = _hole([(0.0, 10.0, "Stiff grey CLAY"), (10.0, 12.0, "No recovery"), (12.0, 20.0, "Dense grey SAND")])
    q2.intervals[1].review = ReviewDecision("accepted", key, "J. Reviewer")
    GeoReasoner("london_basin").interpret(q2)
    assert not q2.intervals[1].needs_review and "accepted by J. Reviewer" in q2.intervals[1].review_reason
    # a stale acceptance (different proposal) re-opens the review
    q3 = _hole([(0.0, 10.0, "Stiff grey CLAY"), (10.0, 12.0, "No recovery"), (12.0, 20.0, "Dense grey SAND")])
    q3.intervals[1].review = ReviewDecision("accepted", "CK", "J. Reviewer")
    GeoReasoner("london_basin").interpret(q3)
    assert q3.intervals[1].needs_review and "re-review" in q3.intervals[1].review_reason
    # an override is final
    q4 = _hole([(0.0, 10.0, "Stiff grey CLAY"), (10.0, 12.0, "No recovery"), (12.0, 20.0, "Dense grey SAND")])
    q4.intervals[1].override = Override("LMBE", "J. Reviewer", "core photographs", "2026-01-01")
    GeoReasoner("london_basin").interpret(q4)
    assert q4.intervals[1].final_unit == "LMBE" and q4.intervals[1].status.value == "overridden"


def test_proposal_key():
    assert proposal_key(["LC", "LC", "HWH", "HWH", "LMBE"]) == "LC>HWH>LMBE"
    assert proposal_key([]) == "" and proposal_key(["TAB", None, "TAB"]) == "TAB"


# ---------------------------------------------------------------------------- synthetic site
def test_reasoner_accuracy_on_synthetic_site(interpreted):
    p, it = interpreted
    tr = SyntheticTruth()
    ok = tot = 0.0
    for iv in p.intervals:
        loc = p.locations[iv.location_id]
        if loc.ground_level is None or iv.unit is None:
            continue
        z = loc.ground_level - iv.mid
        true = next((u for u, a, b in tr.column_at(loc.x, loc.y) if b < z <= a + 1e-9), "CK")
        if iv.description.lower().startswith("no recovery"):
            continue
        tot += iv.thickness
        ok += iv.thickness * (iv.final_unit == true)
    assert ok / tot >= 0.97


def test_planted_defects_are_explained(interpreted):
    p, it = interpreted
    rules = {(i.rule, tuple(i.location_ids)) for i in it.issues}
    assert any(r == "GEO-INV-01" and "BH10" in ids for r, ids in rules)  # inverted logged codes
    assert any(r.startswith("GEO-") and "BH12" in ids for r, ids in rules)  # chalk fill over alluvium
    mis = [i for i in it.issues if i.rule == "GEO-MIS-01" and "BH07" in i.location_ids]
    assert mis and "Lambeth Group" in mis[0].message
    inv = next(i for i in it.issues if i.rule == "GEO-INV-01")
    assert inv.explanations and inv.suggestion


def test_every_interval_has_evidence_and_probability(interpreted):
    p, _ = interpreted
    for iv in p.intervals:
        if iv.location_id not in p.locations or p.locations[iv.location_id].ground_level is None:
            continue
        assert iv.unit is not None and 0.0 <= iv.probability <= 1.0
        assert iv.evidence, f"{iv.location_id} {iv.top} has no explanation"


def test_learned_review_threshold_changes_flags(example_ags):
    from agsgeomodel.importers.ags import import_ags

    p1 = import_ags(example_ags).project
    GeoReasoner("london_basin", ReasonerConfig(review_below_probability=0.99)).interpret(p1)
    p2 = import_ags(example_ags).project
    GeoReasoner("london_basin", ReasonerConfig(review_below_probability=0.2)).interpret(p2)
    assert sum(i.needs_review for i in p1.intervals) > sum(i.needs_review for i in p2.intervals)


def test_secondary_sources_are_less_certain():
    from agsgeomodel.models.enums import DataSource

    def obs_sigma(source):
        p = _hole([(0.0, 8.0, "Stiff grey fissured CLAY (LONDON CLAY)"), (8.0, 20.0, "Very dense grey SAND (THANET)")])
        p.locations["T1"].source = source
        it = GeoReasoner("london_basin", primary_sigma=0.15, secondary_sigma=1.0).interpret(p)
        return min(o.sigma for o in it.observations.observations if not o.inferred)

    assert obs_sigma(DataSource.AGS4) == pytest.approx(0.15)
    assert obs_sigma(DataSource.BGS_AGS) >= 1.0


def test_interpretation_is_deterministic(example_ags):
    from agsgeomodel.importers.ags import import_ags

    a = import_ags(example_ags).project
    b = import_ags(example_ags).project
    GeoReasoner("london_basin").interpret(a)
    GeoReasoner("london_basin").interpret(b)
    assert [(i.unit, i.probability) for i in a.intervals] == [(i.unit, i.probability) for i in b.intervals]
    assert np.isfinite([i.probability for i in a.intervals if i.probability is not None]).all()
