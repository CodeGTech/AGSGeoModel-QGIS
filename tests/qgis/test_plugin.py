"""QGIS integration: plugin lifecycle, Processing algorithms, layer loading (styles, forms, 3D renderers)
and dialogs. Runs headless (``QT_QPA_PLATFORM=offscreen``) inside a QGIS 3.40+ Python environment."""

import os
import time

import numpy as np
import pytest

qgis_core = pytest.importorskip("qgis.core")
pytestmark = pytest.mark.qgis

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qgis_app():
    from qgis.testing import start_app

    return start_app()


@pytest.fixture(scope="session")
def iface(qgis_app):
    from qgis.testing.mocked import get_iface

    return get_iface()


@pytest.fixture(scope="session")
def plugin(iface):
    import agsgeomodel

    p = agsgeomodel.classFactory(iface)
    p.initGui()
    from processing.core.Processing import Processing

    Processing.initialize()
    yield p
    p.unload()


@pytest.fixture
def feedback():
    from qgis.core import QgsProcessingFeedback

    return QgsProcessingFeedback()


def test_plugin_registers_actions_and_provider(plugin):
    from qgis.core import QgsApplication

    names = [a.text() for a in plugin.actions]
    assert "Import AGS…" in names and "Draw cross section" in names
    prov = QgsApplication.processingRegistry().providerById("agsgeomodel")
    ids = {a.id() for a in prov.algorithms()}
    assert ids == {
        f"agsgeomodel:{n}"
        for n in (
            "validate_ags",
            "import_ags",
            "build_model",
            "rebuild_model",
            "cross_sections",
            "export_model",
            "cpt_interpretation",
            "hdd_assessment",
            "bgs_discovery",
            "train_formation_model",
            "correlate_holes",
            "cluster_domains",
        )
    }
    for a in prov.algorithms():
        assert a.shortHelpString() and a.group()


def test_transforms_go_through_qgis(plugin):
    from agsgeomodel.core.transform import get_transformer

    f, note = get_transformer("EPSG:27700", "EPSG:4326")
    lon, lat = f(np.array([545000.0, 545100.0]), np.array([180000.0, 180100.0]))
    assert 0.0 < lon[0] < 0.2 and 51.4 < lat[0] < 51.6


def test_validate_and_import(plugin, example_ags, tmp_path, feedback):
    import processing

    r = processing.run(
        "agsgeomodel:validate_ags",
        {"INPUT": str(example_ags), "OUTPUT_HTML": str(tmp_path / "v.html")},
        feedback=feedback,
    )
    assert r["CRITICAL"] == 0 and (tmp_path / "v.html").exists()
    r = processing.run(
        "agsgeomodel:import_ags",
        {"INPUT": [str(example_ags)], "CRS": "EPSG:27700", "PROVINCE": 1, "PROJECT": str(tmp_path / "imp")},
        feedback=feedback,
    )
    assert r["HOLES"] == 27 and r["REVIEW"] >= 1


@pytest.fixture(scope="session")
def built_project(plugin, example_inputs, tmp_path_factory):
    import processing
    from qgis.core import QgsProcessingFeedback, QgsRasterLayer, QgsVectorLayer

    out, man = example_inputs
    folder = tmp_path_factory.mktemp("qgis_project")
    dtm = QgsRasterLayer(str(out / "dtm_5m.asc"), "dtm")
    flt = QgsVectorLayer(str(out / "faults.geojson"), "faults", "ogr")
    assert dtm.isValid() and flt.isValid()
    r = processing.run(
        "agsgeomodel:build_model",
        {
            "INPUT": [str(out / man["ags"])],
            "CRS": "EPSG:27700",
            "PROVINCE": 1,
            "CELL": 10,
            "BUFFER": 150,
            "REALISATIONS": 10,
            "DTM": dtm,
            "FAULTS": flt,
            "FORMATS": [0],
            "PROJECT": str(folder),
        },
        feedback=QgsProcessingFeedback(),
    )
    assert r["RUN_ID"]
    return folder


def _line_layer(coords_list, name="lines"):
    from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsVectorLayer

    lyr = QgsVectorLayer("LineString?crs=EPSG:27700&field=name:string", name, "memory")
    for k, coords in enumerate(coords_list):
        f = QgsFeature(lyr.fields())
        f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(*c) for c in coords]))
        f.setAttributes([f"L{k}"])
        lyr.dataProvider().addFeature(f)
    return lyr


def test_sections_hdd_export_cpt(plugin, built_project, example_ags, tmp_path, feedback):
    import processing

    from agsgeomodel.examples.synthetic import E0, HDD_ENTRY, HDD_EXIT, N0

    lines = _line_layer([[(E0 + 150, N0 + 150), (E0 + 1000, N0 + 650)], [(E0 + 600, N0 + 50), (E0 + 640, N0 + 750)]])
    r = processing.run(
        "agsgeomodel:cross_sections",
        {
            "PROJECT_IN": str(built_project),
            "LINES": lines,
            "NAME_FIELD": "name",
            "VE": 6,
            "CORRIDOR": 60,
            "OUTPUT": "memory:",
        },
        feedback=feedback,
    )
    assert len(r["FILES"].split(";")) == 2 and r["OUTPUT"].featureCount() > 0
    r = processing.run(
        "agsgeomodel:hdd_assessment",
        {
            "PROJECT_IN": str(built_project),
            "ALIGNMENT": _line_layer([[HDD_ENTRY, HDD_EXIT]]),
            "NAME": "Q",
            "DESIGN_LEVEL": -14.0,
            "STATIONS": "memory:",
            "ZONES": "memory:",
        },
        feedback=feedback,
    )
    assert r["OVERALL_RAG"] in ("Green", "Amber", "Red") and r["STATIONS"].featureCount() > 100
    r = processing.run(
        "agsgeomodel:export_model",
        {"PROJECT_IN": str(built_project), "FORMATS": [0, 4], "TABLES": [0, 2], "OUTPUT": str(tmp_path / "exp")},
        feedback=feedback,
    )
    assert (tmp_path / "exp" / "3d" / "model_units.glb").exists()
    r = processing.run(
        "agsgeomodel:cpt_interpretation", {"INPUT": str(example_ags), "OUTPUT": "memory:"}, feedback=feedback
    )
    lyr = r["OUTPUT"]
    assert lyr.featureCount() > 1000 and "Ic" in lyr.fields().names()


def test_bgs_and_training_algorithms(plugin, built_project, tmp_path, feedback, bgs_fetcher):
    import processing

    r = processing.run(
        "agsgeomodel:bgs_discovery",
        {"PROJECT_IN": str(built_project), "BUFFER": 2, "THRESHOLD": 60, "OUTPUT": "memory:"},
        feedback=feedback,
    )
    assert r["PROVINCE"] == "london_basin" and r["OUTPUT"].featureCount() == 5
    r = processing.run(
        "agsgeomodel:train_formation_model",
        {"PROJECTS": [str(built_project / "project.gpkg")], "BACKEND": 1, "OUTPUT": str(tmp_path / "fm.json")},
        feedback=feedback,
    )
    assert r["CV_ACCURACY"] > 0.8


def test_correlation_and_domain_algorithms(plugin, built_project, feedback):
    import processing
    from qgis.core import QgsWkbTypes

    r = processing.run(
        "agsgeomodel:correlate_holes",
        {"PROJECT_IN": str(built_project), "MODE": 0, "OUTPUT": "memory:"},
        feedback=feedback,
    )
    lyr = r["OUTPUT"]
    assert r["N_LINKS"] > 50 and lyr.featureCount() == r["N_LINKS"]
    assert QgsWkbTypes.hasZ(lyr.wkbType())
    flags = [f["flags"] for f in lyr.getFeatures() if f["flags"]]
    assert any("fault" in x for x in flags)  # links across F1 are flagged
    r = processing.run(
        "agsgeomodel:cluster_domains",
        {"PROJECT_IN": str(built_project), "K_MAX": 5, "OUTPUT": "memory:"},
        feedback=feedback,
    )
    # every interpreted hole (logged holes; CPT-only locations have no strata to cluster)
    assert 2 <= r["K"] <= 5 and r["OUTPUT"].featureCount() >= 20 and 0.0 <= r["AGREEMENT"] <= 1.0


def test_dock_loads_styled_layers(plugin, built_project):
    from qgis.core import QgsProject

    dock = plugin.ensure_dock()
    dock.project_panel.set_folder(str(built_project))
    dock.open_folder(str(built_project))
    dock.load_layers()
    proj = QgsProject.instance()
    for name in ("Exploratory holes", "Interpreted units (3D)", "Model surfaces (3D)", "Uncertainty zones (rockhead)"):
        lyrs = proj.mapLayersByName(name)
        assert lyrs and lyrs[0].isValid() and lyrs[0].featureCount() > 0, name
    assert proj.mapLayersByName("Model surfaces (3D)")[0].renderer3D() is not None
    lith = proj.mapLayersByName("Logged strata (3D)")[0]
    ws = lith.editorWidgetSetup(lith.fields().indexOf("override_unit"))
    assert ws.type() == "ValueMap"
    assert dock.review_panel.table.rowCount() >= 1 and dock.issues_panel.table.rowCount() >= 1


def test_dialogs(plugin, built_project, example_ags, bgs_fetcher, tmp_path):
    from agsgeomodel import workflows as wf
    from agsgeomodel.gui.bgs_dialog import BGSDialog
    from agsgeomodel.gui.import_wizard import ImportWizard
    from agsgeomodel.gui.section_tool import DrawingViewer
    from agsgeomodel.gui.settings_dialog import SettingsDialog

    sd = SettingsDialog(plugin.settings())
    sd.methodCombo.setCurrentIndex(1)
    assert sd.project_settings().model.interpolation.method == "rbf"
    bd = BGSDialog(plugin.iface, plugin.runner, str(built_project), plugin.settings())
    disc = wf.bgs_discover(
        str(built_project), 500, 60, connector=wf.bgs_connector("EPSG:27700", cache_path=str(tmp_path / "c.sqlite"))
    )
    bd._done(disc)
    assert bd.candidatesTable.rowCount() == 5
    wz = ImportWizard(None, [str(example_ags)])
    wz.validation_page.initializePage()
    wz.crs_page.initializePage()
    assert wz.crs_page.crs.crs().authid() == "EPSG:27700" and wz.files
    r = wf.make_section(str(built_project), [[545100, 180500], [546100, 180520]], "Viewer test", 5.0)
    v = DrawingViewer("t", r["svg"], r["pdf"])
    assert v.width() > 0


def test_capture_tool_and_tasks(plugin, qgis_app):
    from qgis.gui import QgsMapMouseEvent
    from qgis.PyQt.QtCore import QEvent, QPoint, Qt

    from agsgeomodel.gui.tasks import FunctionTask

    canvas = plugin.iface.mapCanvas()
    tool = plugin.capture_tool
    got = []
    tool.captured.disconnect(plugin._captured)
    tool.captured.connect(lambda pts: got.append(pts))
    try:
        canvas.setMapTool(tool)
        for x, y in ((50, 50), (200, 120), (300, 300)):
            tool.canvasPressEvent(
                QgsMapMouseEvent(
                    canvas,
                    QEvent.Type.MouseButtonPress,
                    QPoint(x, y),
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.LeftButton,
                )
            )
        tool.canvasPressEvent(
            QgsMapMouseEvent(
                canvas,
                QEvent.Type.MouseButtonPress,
                QPoint(300, 300),
                Qt.MouseButton.RightButton,
                Qt.MouseButton.RightButton,
            )
        )
        assert len(got) == 1 and len(got[0]) == 3
    finally:
        tool.captured.connect(plugin._captured)
    out = []
    t = FunctionTask(
        "t", lambda progress: (progress(0.5, "half"), 42)[1], out.append, lambda e, tb: out.append(("error", e))
    )
    plugin.runner.start(t)
    t0 = time.time()
    while not out and time.time() - t0 < 30:
        qgis_app.processEvents()
        time.sleep(0.05)
    assert out == [42]
