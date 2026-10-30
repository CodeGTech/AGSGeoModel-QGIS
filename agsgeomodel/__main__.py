"""Command line interface: ``python -m agsgeomodel <command>``.

Commands
  validate FILE.ags                 AGS4/AGS3 validation report (text or --json)
  example OUT_DIR                   write the synthetic example project and run the full pipeline
  run CONFIG.json | FOLDER          run the pipeline from a saved configuration or a project folder
  interpret FILE.ags                interpret formations and print the per-hole sequences
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _progress(f, msg):
    print(f"[{f * 100:5.1f}%] {msg}", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="agsgeomodel", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    from . import __version__

    ap.add_argument("--version", action="version", version=f"agsgeomodel {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("path")
    v.add_argument("--json", action="store_true")
    e = sub.add_parser("example")
    e.add_argument("out_dir")
    r = sub.add_parser("run")
    r.add_argument("config", help="pipeline configuration JSON or project folder")
    i = sub.add_parser("interpret")
    i.add_argument("path")
    i.add_argument("--province", default="london_basin")
    a = ap.parse_args(argv)
    if a.cmd == "validate":
        from .importers.ags import read_ags, validate_ags

        rep = validate_ags(read_ags(a.path), base_dir=Path(a.path).parent)
        if a.json:
            print(json.dumps({"summary": rep.counts(), "issues": rep.log.to_list()}, indent=1, default=str))
        else:
            print(rep.summary_text())
            for iss in rep.issues:
                print(
                    f"  {iss.rule:10s} {iss.severity.value:8s} {iss.group or '':5s} line {iss.line or '-':>6} "
                    f"{', '.join(iss.location_ids)} {iss.message}"
                )
        return 0 if rep.usable_for_modelling else 2
    if a.cmd == "example":
        from .examples import example_config
        from .pipeline import run_pipeline
        from .workflows import save_config

        out = Path(a.out_dir)
        cfg = example_config(out)
        cfg.save(out / "pipeline_config.json")
        save_config(cfg, out / "output")  # the output folder is a self-contained project folder
        res = run_pipeline(cfg, _progress)
        print(json.dumps({k: v for k, v in res.paths.items() if k != "rasters" and k != "exports"}, indent=1))
        return 0
    if a.cmd == "run":
        from .pipeline import PipelineConfig, run_pipeline
        from .workflows import run_project

        if Path(a.config).is_dir():  # a project folder (agsgeomodel_project.json inside)
            res = run_project(a.config, _progress)
        else:
            res = run_pipeline(PipelineConfig.load(a.config), _progress)
        print(json.dumps({k: v for k, v in res.paths.items() if k not in ("rasters", "exports")}, indent=1))
        return 0
    if a.cmd == "interpret":
        from .geology import GeoReasoner
        from .importers.ags import import_ags

        imp = import_ags(a.path)
        it = GeoReasoner(a.province).interpret(imp.project)
        for lid, li in it.locations.items():
            print(lid, " > ".join(f"{r.unit}({r.top:.1f}-{r.base:.1f}, p={r.p:.2f})" for r in li.runs))
        for iss in it.issues:
            print(f"  {iss.rule} {iss.severity.value} {iss.location_ids} {iss.message}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
