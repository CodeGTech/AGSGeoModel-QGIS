"""End-to-end: the synthetic example through the full pipeline, project workflows and the CLI."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from agsgeomodel import workflows as wf
from agsgeomodel.database import GeoPackage, ProjectStore
from agsgeomodel.pipeline import PipelineConfig, run_pipeline

pytestmark = pytest.mark.slow


def test_pipeline_outputs(example_project):
    folder, res = example_project
    for key in ("geopackage", "report", "fence", "hdd_long_section"):
        assert Path(res.paths[key]).exists(), key
    assert any(k.startswith("section:") for k in res.paths)
    assert (folder / "exports" / "model_units.vtk").exists() and (folder / "exports" / "model_units.glb").exists()
    gp = GeoPackage(folder / "project.gpkg", create=False)
    try:
        assert gp.integrity_check() == []
        for t in (
            "boreholes",
            "lithology",
            "unit_runs",
            "issues",
            "uncertainty_zones",
            "hdd_stations",
            "model_surfaces_3d",
            "geological_contacts",
            "formations",
            "strat_relations",
            "model_runs",
        ):
            n = gp.conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            assert n > 0, f"{t} is empty"
        classes = {r[0] for r in gp.conn.execute("SELECT DISTINCT class_name FROM uncertainty_zones")}
        assert classes >= {"Observed", "Interpolated", "Inferred", "Speculative"}
    finally:
        gp.close()
    html = Path(res.paths["report"]).read_text()
    for needle in ("NOT REAL DATA", "Lambeth Group", "GEO-INV-01", "Monte Carlo", "HDD-01"):
        assert needle in html, needle


def test_reproducible_runs(example_project, tmp_path):
    folder, res = example_project
    cfg = PipelineConfig.load(folder / "agsgeomodel_project.json")
    cfg.out_dir = str(tmp_path / "again")
    cfg.export_formats, cfg.report, cfg.run_fence, cfg.hdd = [], False, False, None
    res2 = run_pipeline(cfg)
    assert res2.model.run.input_hash == res.model.run.input_hash
    for name in ("rockhead", "base:LC", "base:TAB"):
        np.testing.assert_allclose(res2.model.surfaces[name].values, res.model.surfaces[name].values, atol=1e-9)
    assert [(i.unit, i.probability) for i in res2.project.intervals] == [
        (i.unit, i.probability) for i in res.project.intervals
    ]


def test_config_rejects_unknown_keys(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"ags_paths": [], "out_dir": "x", "not_a_key": 1}))
    from agsgeomodel.core.errors import AGSGeoModelError

    with pytest.raises(AGSGeoModelError):
        PipelineConfig.load(p)


def test_sections_and_hdd_from_stored_project(project_copy):
    from agsgeomodel.examples.synthetic import E0, HDD_ENTRY, HDD_EXIT, N0

    r = wf.make_section(project_copy, [[E0 + 150, N0 + 150], [E0 + 1000, N0 + 650]], "Diagonal D-D'", 6.0)
    assert Path(r["pdf"]).exists() and r["logs"]
    h = wf.assess_hdd(project_copy, [list(HDD_ENTRY), list(HDD_EXIT)], {"name": "HDD-X", "design_elevation": -14.0})
    assert Path(h["pdf"]).exists() and Path(h["csv"]).exists()
    summary = json.loads(Path(h["json"]).read_text())
    assert summary["summary"]["overall_rag"] in ("Green", "Amber", "Red") and summary["run_id"]
    with ProjectStore(project_copy / "project.gpkg") as st:
        names = {r["name"] for r in st.gp.read("cross_sections")}
        assert "Diagonal D-D'" in names
        assert {r["alignment"] for r in st.gp.read("hdd_stations")} >= {"HDD-X", "HDD-01"}


def test_import_only_workflow(example_ags, tmp_path):
    r = wf.import_project(tmp_path / "imp", [str(example_ags)])
    assert Path(r["gpkg"]).exists() and r["run_id"].startswith("import-")
    cfg = wf.load_config(tmp_path / "imp")
    assert cfg.ags_paths == [str(example_ags)]
    items = wf.review_items(tmp_path / "imp")
    assert any(i.location_id == "BH07" for i in items)


def test_bgs_gap_filling_end_to_end(project_copy, connector):
    disc = wf.bgs_discover(project_copy, 500.0, 60.0, connector=connector)
    assert disc.analysis.suggested_province == "london_basin"
    wf.save_bgs(project_copy, disc)
    for c in disc.candidates:
        c.selected = c.qualified
    added = wf.download_bgs_ags(project_copy, disc.candidates, connector)
    assert added and all(Path(a["path"]).exists() for a in added)
    cfg = wf.load_config(project_copy)
    assert cfg.secondary_ags and cfg.secondary_ags[0]["per_location"]
    cfg.export_formats, cfg.report, cfg.run_fence, cfg.hdd = [], True, False, None
    wf.save_config(cfg, project_copy)
    res = wf.run_project(project_copy)
    bgs = [l for l in res.project.locations.values() if l.source.value == "BGS_AGS"]
    assert len(bgs) == 2 and all(0.5 < l.quality < 1.0 for l in bgs)
    report = Path(res.paths["report"]).read_text(encoding="utf-8")
    assert "Secondary data" in report and "British Geological Survey" in report
    assert all(l.id in report for l in bgs)
    sig = [
        o.sigma
        for o in res.interpretation.observations.observations
        if o.location_id in {l.id for l in bgs} and not o.inferred
    ]
    assert sig and min(sig) >= 1.0  # secondary data carry secondary uncertainty


def test_cli(example_ags, tmp_path):
    r = subprocess.run(
        [sys.executable, "-m", "agsgeomodel", "validate", str(example_ags), "--json"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["summary"]["critical"] == 0
    r = subprocess.run(
        [sys.executable, "-m", "agsgeomodel", "interpret", str(example_ags)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0 and "BH07" in r.stdout and "GEO-INV-01" in r.stdout
