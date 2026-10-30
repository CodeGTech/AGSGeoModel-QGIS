"""Shared pytest fixtures.

The synthetic Thames-side example (NOT REAL DATA) has an analytic geological truth, so tests can
measure interpretation accuracy and surface errors rather than only checking that code runs.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))


def pytest_configure(config):
    for m, doc in (
        ("slow", "runs the full pipeline (seconds to a minute)"),
        ("performance", "large synthetic data sets; run with -m performance"),
        ("qgis", "needs the QGIS Python bindings"),
        ("network", "contacts live services (never run in CI)"),
    ):
        config.addinivalue_line("markers", f"{m}: {doc}")


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path_factory, monkeypatch):
    """Keep the BGS HTTP cache and any user files out of the real home directory."""
    home = tmp_path_factory.getbasetemp() / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))


@pytest.fixture(scope="session")
def example_inputs(tmp_path_factory):
    """Synthetic example inputs (AGS, DTM, faults, HDD alignment, crossings) and the manifest."""
    from agsgeomodel.examples.synthetic import write_example

    out = tmp_path_factory.mktemp("example_inputs")
    man = write_example(out)
    return out, man


@pytest.fixture(scope="session")
def example_ags(example_inputs):
    out, man = example_inputs
    return out / man["ags"]


@pytest.fixture(scope="session")
def example_import(example_ags):
    from agsgeomodel.importers.ags import import_ags

    return import_ags(example_ags)


@pytest.fixture(scope="session")
def interpreted(example_ags):
    """(project, interpretation) for the synthetic example, with the known fault."""
    from agsgeomodel.examples.synthetic import FAULT
    from agsgeomodel.geology import GeoReasoner
    from agsgeomodel.importers.ags import import_ags
    from agsgeomodel.models.entities import Fault

    p = import_ags(example_ags).project
    p.faults.append(Fault("F1", np.array(FAULT), 4.0, "right"))
    it = GeoReasoner("london_basin").interpret(p)
    return p, it


@pytest.fixture(scope="session")
def dtm(example_inputs):
    from agsgeomodel.importers import read_dtm

    out, man = example_inputs
    return read_dtm(out / "dtm_5m.asc")


@pytest.fixture(scope="session")
def model(interpreted, dtm):
    from agsgeomodel.core.config import ModelConfig
    from agsgeomodel.modelling import ModelInputs, StratigraphicModelBuilder

    p, it = interpreted
    cfg = ModelConfig(cell_size_m=10.0, buffer_m=150.0, monte_carlo_realisations=0)
    return StratigraphicModelBuilder(cfg).build(
        ModelInputs(it.observations, it.column, p.located(), dtm=dtm, faults=p.faults, crs=p.crs)
    )


@pytest.fixture(scope="session")
def example_project(tmp_path_factory):
    """Full pipeline run of the synthetic example (project folder with project.gpkg)."""
    from agsgeomodel.examples import example_config
    from agsgeomodel.pipeline import run_pipeline
    from agsgeomodel.workflows import save_config

    out = tmp_path_factory.mktemp("example_project")
    cfg = example_config(out, formats=("vtk", "gltf"))
    cfg.settings["model"]["monte_carlo_realisations"] = 12
    save_config(cfg, cfg.out_dir)
    res = run_pipeline(cfg)
    return Path(cfg.out_dir), res


@pytest.fixture
def project_copy(example_project, tmp_path):
    """A private copy of the example project folder (for tests that modify it)."""
    src, _ = example_project
    dst = tmp_path / "project"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("exports", "backups"))
    cfg_path = dst / "agsgeomodel_project.json"
    text = cfg_path.read_text().replace(str(src), str(dst))
    cfg_path.write_text(text)
    return dst


@pytest.fixture
def bgs_fetcher():
    """Route BGS HTTP requests to schema-accurate fixtures (TEST DATA, NOT REAL BGS DATA)."""
    from fixtures.bgs_fixtures import fake_fetcher

    from agsgeomodel.bgs import set_fetcher

    set_fetcher(fake_fetcher())
    yield
    set_fetcher(None)


@pytest.fixture
def connector(bgs_fetcher, tmp_path):
    from agsgeomodel.workflows import bgs_connector

    return bgs_connector("EPSG:27700", cache_path=str(tmp_path / "cache.sqlite"))


def has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
