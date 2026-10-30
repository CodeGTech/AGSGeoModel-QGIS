"""AGS4 format rules and engineering checks (E00-E15)."""

from helpers import minimal_ags, table

from agsgeomodel.importers.ags import import_ags, read_ags, validate_ags

LOCS = [("BH1", "CP", 545000.0, 180000.0, 5.0, 20.0), ("BH2", "CP", 545100.0, 180050.0, 6.0, 25.0)]


def rules_of(text: str) -> dict[str, set[str]]:
    rep = validate_ags(read_ags(text.encode()))
    out: dict[str, set[str]] = {}
    for i in rep.issues:
        out.setdefault(i.rule, set()).update(i.location_ids or ["-"])
    return out


def test_clean_file_has_no_errors():
    text = minimal_ags(LOCS, [("BH1", 0.0, 20.0, "Stiff grey CLAY", "LC"), ("BH2", 0.0, 25.0, "Stiff grey CLAY", "LC")])
    rep = validate_ags(read_ags(text.encode()))
    assert rep.usable_for_modelling
    assert rep.counts()["critical"] == 0 and rep.counts()["error"] == 0


def test_overlap_gap_and_depth_beyond_final_depth():
    strata = [
        ("BH1", 0.0, 5.0, "CLAY", "LC"),
        ("BH1", 4.0, 8.0, "CLAY", "LC"),  # overlap
        ("BH1", 9.0, 22.0, "CLAY", "LC"),  # gap + beyond FDEP
        ("BH2", 0.5, 25.0, "CLAY", "LC"),
    ]  # starts below GL
    r = rules_of(minimal_ags(LOCS, strata))
    assert "E04" in r and {"BH1", "BH2"} <= r["E04"]
    assert "E05" in r and "BH1" in r["E05"]


def test_top_below_base_and_missing_description():
    r = rules_of(minimal_ags(LOCS, [("BH1", 5.0, 2.0, "CLAY", "LC"), ("BH2", 0.0, 25.0, "", "LC")]))
    assert "BH1" in r["E03"] and "BH2" in r["E12"]


def test_missing_coordinates_ground_level_and_duplicates():
    locs = [
        ("BH1", "CP", float("nan"), float("nan"), 5.0, 20.0),
        ("BH2", "CP", 545100.0, 180050.0, float("nan"), 25.0),
        ("BH3", "CP", 545100.2, 180050.1, 6.0, 25.0),
    ]
    text = minimal_ags(locs, [("BH2", 0.0, 25.0, "CLAY", "LC"), ("BH3", 0.0, 25.0, "CLAY", "LC")])
    text = text.replace('"nan"', '""')
    r = rules_of(text)
    assert "BH1" in r["E01"] and "BH2" in r["E02"] and {"BH2", "BH3"} <= r["E07"]


def test_no_geol_is_critical():
    text = minimal_ags(LOCS, []).replace('"GROUP","GEOL"', '"GROUP","XGEO"')
    rep = validate_ags(read_ags(text.encode()))
    assert not rep.usable_for_modelling or "E00" in {i.rule for i in rep.issues}


def test_spt_below_final_depth_and_depth_units():
    ispt = table("ISPT", ["LOCA_ID", "ISPT_TOP", "ISPT_NVAL"], [["BH1", "30.00", "40"]])
    text = minimal_ags(LOCS, [("BH1", 0.0, 20.0, "CLAY", "LC")], [ispt])
    assert "BH1" in rules_of(text)["E09"]
    wrong = minimal_ags(LOCS, [("BH1", 0.0, 20.0, "CLAY", "LC")]).replace('"UNIT","","m","m"', '"UNIT","","ft","ft"')
    assert "E11" in rules_of(wrong)


def test_mixed_vertical_datums():
    loca = table(
        "LOCA",
        ["LOCA_ID", "LOCA_NATE", "LOCA_NATN", "LOCA_GL", "LOCA_DATM"],
        [["BH1", "545000", "180000", "5", "OD"], ["BH2", "545100", "180000", "6", "LOCAL"]],
    )
    from helpers import tran

    from agsgeomodel.importers.ags import write_ags4

    text = write_ags4(
        [tran(), loca, table("GEOL", ["LOCA_ID", "GEOL_TOP", "GEOL_BASE", "GEOL_DESC"], [["BH1", "0", "1", "CLAY"]])]
    )
    assert "E08" in rules_of(text)


def test_example_dataset_planted_defects(example_import):
    rep = example_import.validation
    assert rep.usable_for_modelling and rep.format == "AGS4"
    e04 = [i for i in rep.issues if i.rule == "E04"]
    assert e04 and all(i.severity.value == "warning" for i in e04)


def test_import_never_raises_on_findings(tmp_path):
    p = tmp_path / "bad.ags"
    p.write_text(minimal_ags(LOCS, [("BH1", 5.0, 2.0, "CLAY", "LC")]))
    imp = import_ags(p)
    assert imp.validation.counts()["error"] >= 1 and "BH1" in imp.project.locations
