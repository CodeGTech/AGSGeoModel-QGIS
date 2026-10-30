"""Exports: GIS (GeoPackage via ProjectStore, Shapefile, GeoJSON), tables (CSV, Excel), drawings
(SVG, PDF, PNG) and 3D (VTK, OBJ, PLY, glTF, DXF, IFC)."""

from __future__ import annotations

import json
from pathlib import Path

from .formats2d import drawing_to_png, grid_to_rgba, write_csv, write_excel, write_geojson, write_png, write_shapefile
from .formats3d import (
    local_origin,
    write_dxf,
    write_glb,
    write_ifc,
    write_obj,
    write_ply,
    write_vtk_polydata,
    write_vtk_surface,
    write_vtk_voxels,
)
from .mesh import TriMesh, borehole_segments, model_solids, surface_mesh, unit_solid, voxelise

MODEL_FORMATS = ("vtk", "vtk_voxels", "obj", "ply", "gltf", "dxf", "ifc")


def export_model(
    model,
    out_dir,
    formats=MODEL_FORMATS,
    project=None,
    interpretation=None,
    vertical_exaggeration: float = 1.0,
    voxel_dz: float = 1.0,
    stride: int = 1,
) -> dict[str, str]:
    """Export the 3D model to the requested formats. Returns {format: path} and writes a manifest.

    ``stride`` > 1 subsamples the model grid for lighter files (e.g. DXF for CAD)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    solids = model_solids(model, vertical_exaggeration, stride)
    origin = local_origin(solids)
    sticks = borehole_segments(project, interpretation, vertical_exaggeration) if (project and interpretation) else []
    crs = model.grid.crs
    epsg = int(str(crs).upper().replace("EPSG:", "")) if crs and str(crs).upper().startswith("EPSG:") else None
    res: dict[str, str] = {}
    if "vtk" in formats:
        res["vtk"] = str(write_vtk_polydata(out / "model_units.vtk", solids, sticks))
    if "vtk_voxels" in formats:
        xe, ye, ze, ids = voxelise(model, voxel_dz)
        res["vtk_voxels"] = str(
            write_vtk_voxels(out / "model_blocks.vtk", xe, ye, ze * vertical_exaggeration, ids, model.units)
        )
    if "obj" in formats:
        res["obj"] = str(write_obj(out / "model_units.obj", solids, origin)[0])
    if "ply" in formats:
        res["ply"] = str(write_ply(out / "model_units.ply", solids, origin))
    if "gltf" in formats:
        res["gltf"] = str(write_glb(out / "model_units.glb", solids, origin, crs))
    if "dxf" in formats:
        polys = [(f"BH_{u}", [p, q]) for u, p, q, _ in sticks]
        res["dxf"] = str(
            write_dxf(
                out / "model_units.dxf",
                solids,
                polys,
                layer_colours={f"BH_{u}": model.column.kb[u].colour for u, *_ in sticks},
            )
        )
    if "ifc" in formats:
        res["ifc"] = str(write_ifc(out / "model_units.ifc", solids, epsg, origin))
    manifest = {
        "crs": crs,
        "local_origin": origin.tolist(),
        "vertical_exaggeration": vertical_exaggeration,
        "units": model.units,
        "run_id": model.run.run_id,
        "input_hash": model.run.input_hash,
        "files": res,
        "note": "OBJ/PLY/glTF/IFC coordinates are relative to local_origin",
    }
    (out / "export_manifest.json").write_text(json.dumps(manifest, indent=2))
    res["manifest"] = str(out / "export_manifest.json")
    return res


__all__ = [
    "MODEL_FORMATS",
    "TriMesh",
    "borehole_segments",
    "drawing_to_png",
    "export_model",
    "grid_to_rgba",
    "local_origin",
    "model_solids",
    "surface_mesh",
    "unit_solid",
    "voxelise",
    "write_csv",
    "write_dxf",
    "write_excel",
    "write_geojson",
    "write_glb",
    "write_ifc",
    "write_obj",
    "write_ply",
    "write_png",
    "write_shapefile",
    "write_vtk_polydata",
    "write_vtk_surface",
    "write_vtk_voxels",
]
