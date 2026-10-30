"""Example data generators (synthetic, clearly labelled as NOT REAL DATA)."""

from __future__ import annotations

from pathlib import Path


def example_config(out_dir, formats=("vtk", "vtk_voxels", "gltf", "obj", "dxf", "ifc")):
    """Write the synthetic Thames-side example inputs to ``out_dir/input`` and return the pipeline
    configuration of the example project (project folder ``out_dir/output``)."""
    from ..pipeline import PipelineConfig
    from .synthetic import E0, HDD_ENTRY, HDD_EXIT, N0, write_example

    out = Path(out_dir)
    man = write_example(out / "input")
    return PipelineConfig(
        ags_paths=[str(out / "input" / man["ags"])],
        out_dir=str(out / "output"),
        settings={
            "name": "Synthetic Thames-side HDD crossing (NOT REAL DATA)",
            "crs": "EPSG:27700",
            "province": "london_basin",
            "model": {"cell_size_m": 10.0, "buffer_m": 150.0, "monte_carlo_realisations": 40},
        },
        dtm_path=str(out / "input" / "dtm_5m.asc"),
        faults_path=str(out / "input" / "faults.geojson"),
        sections=[
            {"name": "Section A-A (along HDD-01)", "line": [list(HDD_ENTRY), list(HDD_EXIT)]},
            {
                "name": "Section B-B (east-west across fault F1)",
                "line": [[E0 + 100, N0 + 300], [E0 + 1150, N0 + 300]],
                "vertical_exaggeration": 8.0,
            },
        ],
        hdd={
            "alignment_path": str(out / "input" / "hdd_alignment.geojson"),
            "name": "HDD-01",
            "design_elevation": -14.0,
            "crossings_path": str(out / "input" / "crossings.geojson"),
        },
        export_formats=list(formats),
    )
