"""project.gpkg (pure-sqlite3 GeoPackage), migrations, reviewer workflow and 2D / 3D exports."""

import json
import sqlite3

import numpy as np
import pytest
from conftest import has_module

from agsgeomodel.core.grid import Grid2D
from agsgeomodel.database import SCHEMA_VERSION, GeoPackage, ProjectStore, current_version, migrate
from agsgeomodel.database.gpkg import decode_geometry, encode_geometry
from agsgeomodel.database.raster import read_geotiff, write_2dm, write_geotiff
from agsgeomodel.exports import (
    export_model,
    model_solids,
    write_csv,
    write_dxf,
    write_geojson,
    write_glb,
    write_ifc,
    write_obj,
    write_ply,
    write_shapefile,
    write_vtk_polydata,
)
from agsgeomodel.importers.vector import read_dxf, read_geojson, read_shapefile


# ---------------------------------------------------------------------------- GeoPackage
@pytest.mark.parametrize(
    "geom",
    [
        {"type": "Point", "coordinates": [1.0, 2.0]},
        {"type": "Point", "coordinates": [1.0, 2.0, 3.0]},
        {"type": "LineString", "coordinates": [[0.0, 0.0, 1.0], [5.0, 5.0, 2.0]]},
        {
            "type": "Polygon",
            "coordinates": [[[0.0, 0.0], [4.0, 0.0], [4.0, 4.0], [0.0, 0.0]], [[1, 1], [2, 1], [2, 2], [1, 1]]],
        },
    ],
)
def test_geometry_blob_roundtrip(geom):
    z = (
        len(
            np.asarray(
                geom["coordinates"]
                if geom["type"] == "Point"
                else (geom["coordinates"][0] if geom["type"] == "LineString" else geom["coordinates"][0][0])
            )
        )
        == 3
    )
    blob = encode_geometry(geom, 27700, z)
    assert blob[:2] == b"GP"
    back = decode_geometry(blob)
    assert back["type"] == geom["type"]
    np.testing.assert_allclose(
        np.asarray(back["coordinates"][0] if geom["type"] == "Polygon" else back["coordinates"], dtype=float),
        np.asarray(geom["coordinates"][0] if geom["type"] == "Polygon" else geom["coordinates"], dtype=float),
    )


def test_geopackage_is_valid_and_spatially_indexed(tmp_path):
    p = tmp_path / "t.gpkg"
    gp = GeoPackage(p)
    gp.ensure_srs(27700)
    gp.create_table("pts", [("name", "text"), ("v", "real")], "Point", 27700, True, "test points")
    gp.insert(
        "pts",
        [
            {
                "geometry": {"type": "Point", "coordinates": [float(i), float(2 * i), 1.0]},
                "properties": {"name": f"p{i}", "v": i * 0.5},
            }
            for i in range(50)
        ],
    )
    assert gp.integrity_check() == []
    rows = gp.read("pts", "v > ?", (20.0,))
    assert len(rows) == 9 and rows[0]["geometry"]["type"] == "Point"
    n = gp.conn.execute("SELECT count(*) FROM rtree_pts_geom WHERE minx >= 10 AND maxx <= 20").fetchone()[0]
    assert n == 11  # R-tree maintained by triggers
    gp.close()
    con = sqlite3.connect(p)
    assert con.execute("PRAGMA application_id").fetchone()[0] == 0x47504B47
    con.close()


def test_geopackage_readable_by_ogr(tmp_path):
    ogr = pytest.importorskip("osgeo.ogr")
    p = tmp_path / "t.gpkg"
    gp = GeoPackage(p)
    gp.ensure_srs(27700)
    gp.create_table("lines", [("name", "text")], "LineString", 27700, True)
    gp.insert(
        "lines",
        [{"geometry": {"type": "LineString", "coordinates": [[0, 0, 1], [10, 10, 2]]}, "properties": {"name": "a"}}],
    )
    gp.close()
    ds = ogr.Open(str(p))
    lyr = ds.GetLayerByName("lines")
    assert lyr.GetFeatureCount() == 1 and lyr.GetSpatialRef().GetAuthorityCode(None) == "27700"


def test_migrations_are_idempotent(tmp_path):
    gp = GeoPackage(tmp_path / "m.gpkg")
    first = migrate(gp, "EPSG:27700")
    assert first == list(range(1, SCHEMA_VERSION + 1)) and current_version(gp) == SCHEMA_VERSION
    assert migrate(gp, "EPSG:27700") == []
    for t in ("boreholes", "lithology", "unit_runs", "review_decisions", "model_surfaces_3d", "hdd_stations"):
        assert gp.has_table(t)
    gp.close()


def test_older_projects_are_backed_up_before_upgrading(tmp_path):
    p = tmp_path / "old" / "project.gpkg"
    p.parent.mkdir()
    gp = GeoPackage(p)
    migrate(gp, "EPSG:27700", target=SCHEMA_VERSION - 1)  # a project written by an earlier release
    gp.close()
    with ProjectStore(p) as st:
        assert st.applied_migrations == [SCHEMA_VERSION]
        assert st.migration_backup is not None and st.migration_backup.exists()
    with ProjectStore(p) as st:  # already current: no new backup
        assert st.applied_migrations == [] and st.migration_backup is None


def test_raster_roundtrip(tmp_path):
    g = Grid2D(100.5, 200.5, 1.0, 2.0, 30, 20)
    v = np.arange(600, dtype=float).reshape(20, 30)
    v[3, 4] = np.nan
    write_geotiff(tmp_path / "r.tif", g, v, 27700)
    g2, v2 = read_geotiff(tmp_path / "r.tif")
    assert (g2.x0, g2.y0, g2.dx, g2.dy, g2.nx, g2.ny) == (g.x0, g.y0, g.dx, g.dy, g.nx, g.ny)
    np.testing.assert_array_equal(np.isnan(v2), np.isnan(v))
    np.testing.assert_allclose(v2[np.isfinite(v2)], v[np.isfinite(v)])
    gdal = pytest.importorskip("osgeo.gdal")
    ds = gdal.Open(str(tmp_path / "r.tif"))
    gt = ds.GetGeoTransform()
    assert gt[0] == pytest.approx(100.0) and gt[1] == 1.0 and gt[5] == -2.0
    a = ds.GetRasterBand(1).ReadAsArray()
    assert a[0, 0] == pytest.approx(v[-1, 0])  # north-up
    text = write_2dm(tmp_path / "s.2dm", g, v).read_text()
    assert text.startswith("MESH2D") and text.count("ND ") == 599  # the NaN node is left out


# ---------------------------------------------------------------------------- store and reviewer workflow
def test_store_roundtrip(example_project):
    folder, res = example_project
    with ProjectStore(folder / "project.gpkg") as st:
        p = st.load_project()
        assert set(p.locations) == set(res.project.locations)
        assert len(p.intervals) == len(res.project.intervals)
        m = st.load_model()
        assert m.units == res.model.units
        xy = np.array([[545500.0, 180300.0], [545700.0, 180500.0]])
        for name in ("rockhead", "base:LC"):
            np.testing.assert_allclose(m.sample(name, xy), res.model.sample(name, xy), atol=1e-4)
        it = st.load_interpretation()
        assert set(it.locations) == set(res.interpretation.locations)
        bh07 = [(r.unit, r.synthetic) for r in it.runs("BH07")]
        assert ("LMBE", True) in bh07  # inferred missing layer stored
        gm = st.load_ground_model()
        assert gm is not None and gm.stats
        hydro = st.load_hydro(m.column)
        assert hydro is not None and "deep" in hydro.surfaces
        runs = st.runs("stratigraphic_model")
        assert runs and runs[-1]["input_hash"] and json.loads(runs[-1]["config"])["cell_size_m"] == 10.0
        issues = st.gp.read("issues")
        assert any(i["category"] == "engineering" for i in issues)  # AGS findings stored too
        assert any(i["category"] == "geology" for i in issues)


def test_decisions_overrides_and_issue_status_survive_a_rerun(project_copy):
    from agsgeomodel import workflows as wf

    items = wf.review_items(project_copy)
    assert items and items[0].kind == "inferred layer"
    wf.decide(project_copy, items[0], "accepted", "J. Reviewer", "consistent with the regional thickness")
    with ProjectStore(project_copy / "project.gpkg") as st:
        st.gp.conn.execute(
            "UPDATE lithology SET override_unit = 'HWH', override_by = 'J. Reviewer', "
            "override_reason = 'sample description' WHERE location_id = 'BH10' AND top > 15 AND top < 16"
        )
        st.gp.conn.execute("UPDATE issues SET status = 'dismissed' WHERE rule = 'GEO-DIP-01'")
        st.gp.conn.commit()
    res = wf.run_project(project_copy)
    assert any("reviewer decision" in n for n in res.notes)
    with ProjectStore(project_copy / "project.gpkg") as st:
        row = st.gp.read("lithology", "location_id = 'BH07' AND top > 14 AND top < 15")[0]
        assert not row["needs_review"] and row["review_reason"].startswith("accepted by J. Reviewer")
        ovr = st.gp.read("lithology", "location_id = 'BH10' AND top > 15 AND top < 16")[0]
        assert ovr["unit"] == "HWH" and ovr["status"] == "overridden"
        st_dip = {r["status"] for r in st.gp.read("issues", "rule = 'GEO-DIP-01'")}
        assert st_dip == {"dismissed"}
        assert len(st.decisions()) == 1 and len(st.runs("stratigraphic_model")) >= 2


def test_backup(project_copy):
    with ProjectStore(project_copy / "project.gpkg") as st:
        b = st.backup()
    assert b.exists() and b.stat().st_size > 10000


def test_correlation_and_clustering_from_a_stored_project(example_project):
    from agsgeomodel import workflows as wf
    from agsgeomodel.core.errors import AGSGeoModelError

    folder, _ = example_project
    links = wf.correlate_holes(folder, "rules_constrained")
    assert len(links) > 50
    assert any("fault" in f for lk in links for f in lk.flags)  # F1 crosses the site
    naive = wf.correlate_holes(folder, "nearest_neighbour")
    assert all("not geologically constrained" in lk.flags[0] for lk in naive)
    with pytest.raises(AGSGeoModelError):
        wf.correlate_holes(folder, "telepathy")
    res = wf.cluster_holes(folder, k_max=5)
    assert 2 <= res.k <= 5 and len(res.location_ids) == len(res.labels) == len(res.rule_domains)
    assert all(res.explanations[k] for k in range(res.k))


# ---------------------------------------------------------------------------- 3D exports
def test_unit_solids_are_watertight(model):
    meshes = model_solids(model, stride=2)
    assert meshes
    for m in meshes:
        edges = np.sort(np.concatenate([m.faces[:, [0, 1]], m.faces[:, [1, 2]], m.faces[:, [2, 0]]]), axis=1)
        _, counts = np.unique(edges, axis=0, return_counts=True)
        assert np.all(counts == 2), f"{m.name} is not closed"
        V = m.vertices
        vol = np.einsum("ij,ij->i", V[m.faces[:, 0]], np.cross(V[m.faces[:, 1]], V[m.faces[:, 2]])).sum() / 6
        assert vol > 0, f"{m.name}: faces not oriented outwards"


def test_3d_formats(model, interpreted, tmp_path):
    p, it = interpreted
    paths = export_model(model, tmp_path, ("vtk", "vtk_voxels", "obj", "ply", "gltf", "dxf", "ifc"), p, it, stride=3)
    for k in ("vtk", "vtk_voxels", "obj", "ply", "gltf", "dxf", "ifc", "manifest"):
        assert (tmp_path / paths[k].split("/")[-1]).exists()
    assert open(paths["vtk"]).readline().startswith("# vtk DataFile")
    man = json.loads(open(paths["manifest"]).read())
    assert man["crs"] == "EPSG:27700" and len(man["local_origin"]) == 3
    if has_module("trimesh"):
        import trimesh

        scene = trimesh.load(paths["gltf"])
        assert len(scene.geometry) == len(model_solids(model, stride=3))
        ply = trimesh.load(paths["ply"])
        assert len(ply.faces) > 0
    if has_module("ezdxf"):
        import ezdxf

        doc = ezdxf.readfile(paths["dxf"])
        assert len(list(doc.modelspace())) > 0
    if has_module("ifcopenshell"):
        import ifcopenshell

        f = ifcopenshell.open(paths["ifc"])
        assert f.by_type("IfcMapConversion") and f.by_type("IfcProjectedCRS")
        assert len(f.by_type("IfcGeographicElement")) + len(f.by_type("IfcBuildingElementProxy")) > 0


def test_2d_formats_roundtrip(tmp_path):
    feats = [
        {
            "geometry": {"type": "Point", "coordinates": [545000.0 + i, 180000.0, 5.0]},
            "properties": {"id": f"BH{i}", "gl": 5.0 + i, "ok": True},
        }
        for i in range(3)
    ]
    gj = write_geojson(tmp_path / "p.geojson", feats, "EPSG:27700")
    assert read_geojson(gj)[1]["properties"]["id"] == "BH1"
    shp = write_shapefile(tmp_path / "p.shp", feats, "EPSG:27700")
    back = read_shapefile(shp)
    assert [f["properties"]["id"] for f in back] == ["BH0", "BH1", "BH2"]
    assert back[0]["geometry"]["coordinates"][:2] == pytest.approx([545000.0, 180000.0])
    assert (tmp_path / "p.prj").read_text().startswith("PROJCS")
    csvp = write_csv(tmp_path / "t.csv", [f["properties"] for f in feats])
    assert csvp.read_text().splitlines()[0] == "id,gl,ok"
    if has_module("osgeo"):
        from osgeo import ogr

        ds = ogr.Open(str(shp))  # keep the data source alive while its layer is used
        assert ds.GetLayer().GetFeatureCount() == 3


def test_dxf_writer_and_reader(tmp_path):
    lines = [
        ("BH_LC", np.array([[0.0, 0.0, 5.0], [0.0, 0.0, -10.0]])),
        ("FAULT", np.array([[0, 0, 0], [5, 5, 0], [9, 3, 0]])),
    ]
    p = write_dxf(tmp_path / "x.dxf", [], lines)
    feats = read_dxf(p)
    layers = {f["properties"]["layer"] for f in feats}
    assert {"BH_LC", "FAULT"} <= layers


def test_obj_ply_glb_writers(model, tmp_path):
    meshes = model_solids(model, stride=4)[:2]
    obj, mtl = write_obj(tmp_path / "m.obj", meshes)
    assert obj.read_text().count("\nf ") > 10 and mtl.exists()
    ply = write_ply(tmp_path / "m.ply", meshes)
    assert ply.read_bytes().startswith(b"ply")
    glb = write_glb(tmp_path / "m.glb", meshes, crs="EPSG:27700")
    raw = glb.read_bytes()
    assert raw[:4] == b"glTF" and int.from_bytes(raw[8:12], "little") == len(raw)
    vtk = write_vtk_polydata(tmp_path / "m.vtk", meshes)
    assert "POLYGONS" in vtk.read_text()
    ifc = write_ifc(tmp_path / "m.ifc", meshes, 27700)
    assert ifc.read_text().startswith("ISO-10303-21")
