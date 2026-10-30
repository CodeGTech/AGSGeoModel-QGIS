"""ProjectStore: persistence of a whole AGSGeoModel project in ``project.gpkg`` (+ rasters / meshes).

* project data tables hold the **current** state; reviewer decisions (``override_unit``,
  ``override_by``, ``override_reason`` on ``lithology``; ``status`` on ``issues``) are edited in QGIS
  and read back by :meth:`ProjectStore.apply_overrides` before the next run;
* model outputs are tagged with ``run_id`` and kept (rasters in ``rasters/<run_id>/``), so earlier
  runs stay comparable; ``model_runs`` records inputs hash, configuration, KB version and seed;
* :meth:`ProjectStore.backup` snapshots the GeoPackage with the SQLite online-backup API.
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..core.contour import contour_levels, contour_lines
from ..core.errors import StoreError
from ..core.grid import polygonize
from ..geology.domains import DOMAINS, domain_grid
from ..models.entities import Override
from ..models.enums import DataSource, InterpretationStatus, LocationKind
from ..models.project import Project
from .gpkg import GeoPackage
from .raster import write_2dm, write_geotiff
from .schema import SCHEMA_VERSION, current_version, migrate

CLASS_NAMES = ["Observed", "Interpolated", "Inferred", "Speculative"]


@dataclass
class StoredLocation:
    location_id: str
    runs: list

    def unit_at(self, depth: float) -> str | None:
        for r in self.runs:
            if r.top - 1e-9 <= depth <= r.base + 1e-9:
                return r.unit
        return None


@dataclass
class StoredInterpretation:
    """Duck-type of :class:`~agsgeomodel.geology.reasoner.ProjectInterpretation` for drawing."""

    locations: dict
    run_id: str | None = None
    column: object = None  # StratColumn of the model run (set by workflows.load_context)

    def runs(self, location_id: str) -> list:
        li = self.locations.get(location_id)
        return li.runs if li else []


def _pt(x, y, z=None):
    return {"type": "Point", "coordinates": [x, y] if z is None else [x, y, z]}


class ProjectStore:
    def __init__(self, path: str | Path, crs: str = "EPSG:27700"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.crs = crs
        self.gp = GeoPackage(self.path)
        self.migration_backup: Path | None = None
        if 0 < current_version(self.gp) < SCHEMA_VERSION:  # an older project: keep a copy before upgrading
            self.migration_backup = self.backup()
        self.applied_migrations = migrate(self.gp, crs)
        self.raster_dir = self.path.parent / "rasters"

    @property
    def epsg(self) -> int | None:
        try:
            return int(str(self.crs).upper().replace("EPSG:", ""))
        except ValueError:
            return None

    def close(self) -> None:
        self.gp.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ housekeeping
    def backup(self, keep: int = 10) -> Path:
        bdir = self.path.parent / "backups"
        bdir.mkdir(exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        target = bdir / f"{self.path.stem}.{stamp}.gpkg"
        dst = sqlite3.connect(str(target))
        with dst:
            self.gp.conn.backup(dst)
        dst.close()
        olds = sorted(bdir.glob(f"{self.path.stem}.*.gpkg"))
        for old in olds[:-keep]:
            old.unlink()
        return target

    def clear(self, table: str, where: str = "", params: tuple = ()) -> None:
        self.gp.conn.execute(f'DELETE FROM "{table}"' + (f" WHERE {where}" if where else ""), params)
        self.gp.conn.commit()

    def set_meta(self, key: str, value) -> None:
        self.clear("project_metadata", "key = ?", (key,))
        self.gp.insert("project_metadata", [{"key": key, "value": json.dumps(value, default=str)}])

    def get_meta(self, key: str, default=None):
        r = self.gp.conn.execute("SELECT value FROM project_metadata WHERE key = ?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def record_run(self, run) -> None:
        d = run.to_dict() if hasattr(run, "to_dict") else dict(run)
        self.gp.insert(
            "model_runs",
            [
                {
                    **{k: d.get(k) for k in ("run_id", "kind", "created", "input_hash", "kb_version", "seed")},
                    "config": d.get("config"),
                    "summary": d.get("summary"),
                    "warnings": d.get("warnings"),
                    "software": d.get("software"),
                    "outputs": d.get("outputs"),
                }
            ],
        )

    def runs(self, kind: str | None = None) -> list[dict]:
        rows = self.gp.read("model_runs", "kind = ?" if kind else "", (kind,) if kind else ())
        return sorted(rows, key=lambda r: r["created"] or "")

    # ------------------------------------------------------------------ knowledge base
    def save_knowledge(self, column) -> None:
        kb = column.kb
        self.clear("formations")
        self.gp.insert(
            "formations",
            [
                {
                    "code": u.code,
                    "name": u.name,
                    "lexicon": u.lexicon,
                    "parent": u.parent,
                    "rank": u.rank,
                    "class": u.unit_class,
                    "domain": u.domain,
                    "material": u.material,
                    "age_label": u.age_label,
                    "age_max_ma": u.age_max_ma,
                    "age_min_ma": u.age_min_ma,
                    "environment": u.environment,
                    "hsu": u.hsu.value,
                    "permeability": u.permeability,
                    "colour": u.colour,
                    "lexicon_url": u.lexicon_url,
                    "notes": u.notes,
                }
                for u in kb.units.values()
            ],
        )
        self.clear("strat_relations", "province = ?", (column.id,))
        rows = []
        for s in column.sequences:
            codes = s.codes
            for a, b in zip(codes[:-1], codes[1:]):
                pred = "above" if s.order == "strict" else "typically_above"
                rel = s.relations.get((a, b), {})
                rows.append(
                    {
                        "province": column.id,
                        "subject": a,
                        "predicate": pred,
                        "object": b,
                        "sequence": s.id,
                        "note": rel.get("note", rel.get("type", "")),
                    }
                )
            for c in codes:
                u = kb[c]
                if u.parent:
                    rows.append(
                        {
                            "province": column.id,
                            "subject": c,
                            "predicate": "member_of",
                            "object": u.parent,
                            "sequence": s.id,
                            "note": "",
                        }
                    )
        for up, lo in zip(column.sequences[:-1], column.sequences[1:]):
            rows.append(
                {
                    "province": column.id,
                    "subject": up.id,
                    "predicate": "erosional_contact_above",
                    "object": lo.id,
                    "sequence": "",
                    "note": "sequence boundary",
                }
            )
        self.gp.insert("strat_relations", rows)

    # ------------------------------------------------------------------ observations
    def save_project(self, project: Project) -> None:
        for t in ("boreholes", "samples", "lab_results", "faults"):
            self.clear(t)
        self.gp.insert(
            "boreholes",
            [
                {
                    "geometry": _pt(l.x, l.y, l.ground_level or 0.0),
                    "properties": {
                        "location_id": l.id,
                        "kind": l.kind.value,
                        "type_code": l.type_code,
                        "source": l.source.value,
                        "ground_level": l.ground_level,
                        "final_depth": l.final_depth,
                        "date": l.date,
                        "quality": l.quality,
                        "remarks": l.remarks,
                    },
                }
                for l in project.locations.values()
                if np.isfinite(l.x) and np.isfinite(l.y)
            ],
        )
        locs = project.locations
        rows = []
        for s in project.samples:
            l = locs.get(s.location_id)
            if l and l.ground_level is not None:
                rows.append(
                    {
                        "geometry": _pt(l.x, l.y, l.ground_level - s.top),
                        "properties": {
                            "location_id": s.location_id,
                            "top": s.top,
                            "base": s.base,
                            "ref": s.ref,
                            "type": s.type,
                            "sample_id": s.sample_id,
                        },
                    }
                )
        self.gp.insert("samples", rows)
        rows = []
        for r in project.lab:
            l = locs.get(r.location_id)
            if l and l.ground_level is not None:
                rows.append(
                    {
                        "geometry": _pt(l.x, l.y, l.ground_level - r.depth),
                        "properties": {
                            "location_id": r.location_id,
                            "depth": r.depth,
                            "ags_group": r.group,
                            "test": r.test,
                            "value": r.value,
                            "uom": r.unit,
                            "text": r.text,
                            "sample_ref": r.sample_ref,
                        },
                    }
                )
        self.gp.insert("lab_results", rows)
        self.gp.insert(
            "faults",
            [
                {
                    "geometry": {"type": "LineString", "coordinates": f.trace.tolist()},
                    "properties": {
                        "name": f.name,
                        "throw_m": f.throw_m,
                        "downthrown_side": f.downthrown_side,
                        "dip_deg": f.dip_deg,
                        "source": f.source,
                    },
                }
                for f in project.faults
            ],
        )
        self.set_meta(
            "project",
            {
                "name": project.name,
                "crs": project.crs,
                "vertical_datum": project.vertical_datum,
                "source_files": project.source_files,
                **project.metadata,
            },
        )

    def save_interpretation(self, project: Project, interp, run_id: str) -> None:
        kb = interp.column.kb
        self.clear("lithology")
        rows = []
        for iv in project.intervals:
            l = project.locations.get(iv.location_id)
            if l is None or l.ground_level is None:
                continue
            u = iv.final_unit
            rows.append(
                {
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[l.x, l.y, l.ground_level - iv.top], [l.x, l.y, l.ground_level - iv.base]],
                    },
                    "properties": {
                        "location_id": iv.location_id,
                        "top": iv.top,
                        "base": iv.base,
                        "z_top": l.ground_level - iv.top,
                        "z_base": l.ground_level - iv.base,
                        "description": iv.description,
                        "legend_code": iv.legend_code,
                        "geology_code": iv.geology_code,
                        "bgs_code": iv.bgs_code,
                        "formation_name": iv.formation_name,
                        "unit": u,
                        "unit_name": kb[u].name if u and u in kb else None,
                        "probability": iv.probability,
                        "alternatives": iv.alternatives,
                        "status": iv.status.value,
                        "needs_review": iv.needs_review,
                        "review_reason": iv.review_reason,
                        "evidence": [e.to_dict() for e in iv.evidence],
                        "override_unit": iv.override.unit if iv.override else None,
                        "override_by": iv.override.reviewer if iv.override else None,
                        "override_reason": iv.override.reason if iv.override else None,
                        "colour": kb[u].colour if u and u in kb else "#BDBDBD",
                        "source": iv.source.value,
                        "run_id": run_id,
                    },
                }
            )
        self.gp.insert("lithology", rows)
        self.clear("geological_contacts")
        self.gp.insert(
            "geological_contacts",
            [
                {
                    "geometry": _pt(o.x, o.y, o.value if not o.surface.startswith(("thk", "iso")) else None),
                    "properties": {
                        "surface": o.surface,
                        "location_id": o.location_id,
                        "value": o.value,
                        "kind": o.kind.value,
                        "sigma": o.sigma,
                        "source": o.source.value,
                        "inferred": o.inferred,
                        "confidence_class": o.confidence_class.value,
                        "note": o.note,
                        "run_id": run_id,
                    },
                }
                for o in interp.observations.observations
            ],
        )
        self.save_unit_runs(project, interp, run_id)
        self.save_issues(list(interp.issues), project)

    def save_unit_runs(self, project: Project, interp, run_id: str) -> None:
        """Interpreted unit intervals per hole (including inferred missing layers) as vertical lines."""
        kb = interp.column.kb
        self.clear("unit_runs")
        rows = []
        for lid, li in interp.locations.items():
            l = project.locations.get(lid)
            if l is None or l.ground_level is None or not np.isfinite(l.x):
                continue
            for r in li.runs:
                gl = l.ground_level
                rows.append(
                    {
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[l.x, l.y, gl - r.top], [l.x, l.y, gl - r.base]],
                        },
                        "properties": {
                            "location_id": lid,
                            "unit": r.unit,
                            "unit_name": kb[r.unit].name if r.unit in kb else None,
                            "top": r.top,
                            "base": r.base,
                            "z_top": gl - r.top,
                            "z_base": gl - r.base,
                            "probability": r.p,
                            "inferred": bool(r.synthetic),
                            "colour": kb[r.unit].colour if r.unit in kb else "#BDBDBD",
                            "run_id": run_id,
                        },
                    }
                )
        self.gp.insert("unit_runs", rows)

    def load_interpretation(self) -> StoredInterpretation:
        """Lightweight interpretation (unit runs per hole) for sections and HDD long sections drawn
        from a stored project without re-running the GeoReasoner."""
        from ..geology.contacts import UnitRun

        locs: dict[str, StoredLocation] = {}
        run_id = None
        for r in self.gp.read("unit_runs"):
            run_id = r.get("run_id") or run_id
            locs.setdefault(r["location_id"], StoredLocation(r["location_id"], [])).runs.append(
                UnitRun(
                    r["unit"], float(r["top"]), float(r["base"]), float(r["probability"] or 1.0), bool(r["inferred"])
                )
            )
        for li in locs.values():
            li.runs.sort(key=lambda u: u.top)
        return StoredInterpretation(locs, run_id)

    def save_issues(self, issues, project: Project, replace: bool = True) -> None:
        """Write findings. Reviewer statuses (accepted / dismissed) edited in QGIS are carried over to
        the same finding (rule + holes + depth + message) when the issues are regenerated."""
        prev: dict[tuple, str] = {}
        if replace:
            for rule, lids, depth, msg, status in self.gp.conn.execute(
                "SELECT rule, location_ids, depth, message, status FROM issues"
            ):
                if status and status != "open":
                    prev[(rule, lids or "", None if depth is None else round(depth, 2), msg)] = status
            self.clear("issues")
        rows = []
        for i in issues:
            loc = project.locations.get(i.location_ids[0]) if i.location_ids else None
            geom = _pt(loc.x, loc.y) if loc is not None and np.isfinite(loc.x) else None
            key = (i.rule, ",".join(i.location_ids), None if i.depth is None else round(i.depth, 2), i.message)
            if key in prev and i.status == "open":
                i.status = prev[key]
            rows.append(
                {
                    "geometry": geom,
                    "properties": {
                        "rule": i.rule,
                        "severity": i.severity.value,
                        "category": i.category,
                        "message": i.message,
                        "location_ids": ",".join(i.location_ids),
                        "depth": i.depth,
                        "explanations": i.explanations,
                        "suggestion": i.suggestion,
                        "status": i.status,
                    },
                }
            )
        self.gp.insert("issues", rows)

    # ------------------------------------------------------------------ reviewer decisions
    def record_decision(
        self,
        location_id: str,
        top: float,
        base: float,
        decision: str,
        proposal: str,
        reviewer: str,
        reason: str = "",
        unit: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """Append a reviewer decision (``accepted`` or ``override``); the latest decision wins.

        An override is mirrored into ``lithology.override_unit`` so the QGIS attribute form shows it."""
        if decision not in ("accepted", "override"):
            raise StoreError(f"unknown review decision {decision!r}")
        if decision == "override" and not unit:
            raise StoreError("an override needs a unit")
        now = _dt.datetime.now(_dt.UTC).isoformat()
        self.gp.insert(
            "review_decisions",
            [
                {
                    "location_id": location_id,
                    "top": top,
                    "base": base,
                    "decision": decision,
                    "unit": unit,
                    "proposal": proposal,
                    "reviewer": reviewer,
                    "reason": reason,
                    "created": now,
                    "run_id": run_id,
                }
            ],
        )
        args = (location_id, round(top, 3), round(base, 3))
        where = "location_id = ? AND round(top, 3) = ? AND round(base, 3) = ?"
        if decision == "override":
            self.gp.conn.execute(
                f"UPDATE lithology SET override_unit = ?, override_by = ?, override_reason = ?, "
                f"needs_review = 0 WHERE {where}",
                (unit, reviewer, reason, *args),
            )
        else:
            self.gp.conn.execute(
                f"UPDATE lithology SET override_unit = NULL, override_by = NULL, "
                f"override_reason = NULL, needs_review = 0, review_reason = ? WHERE {where}",
                (f"accepted by {reviewer}: {proposal}", *args),
            )
        self.gp.conn.commit()

    def decisions(self) -> list[dict]:
        """Latest decision per interval."""
        latest: dict[tuple, dict] = {}
        for r in self.gp.read("review_decisions"):
            latest[(r["location_id"], round(r["top"], 3), round(r["base"], 3))] = r
        return list(latest.values())

    def apply_overrides(self, project: Project) -> int:
        """Copy reviewer decisions back onto the project's intervals before a run.

        * overrides: ``lithology.override_unit`` is the source of truth (edited in the QGIS attribute
          form or written by :meth:`record_decision`), so clearing it in the form removes the override;
        * acceptances: the latest ``review_decisions`` entry per interval, if it is ``accepted`` and the
          interval has no override. It stays valid only while the proposal is unchanged.
        """
        from ..models.entities import ReviewDecision

        n = 0
        idx = {(iv.location_id, round(iv.top, 3), round(iv.base, 3)): iv for iv in project.intervals}
        rows = self.gp.conn.execute(
            "SELECT location_id, top, base, override_unit, override_by, override_reason "
            "FROM lithology WHERE override_unit IS NOT NULL AND override_unit != ''"
        ).fetchall()
        for lid, top, base, unit, by, why in rows:
            iv = idx.get((lid, round(top, 3), round(base, 3)))
            if iv is not None:
                iv.override = Override(unit, by or "unknown reviewer", why or "", _dt.datetime.now().isoformat())
                n += 1
        for d in self.decisions():
            iv = idx.get((d["location_id"], round(d["top"], 3), round(d["base"], 3)))
            if iv is None or d["decision"] != "accepted" or iv.override is not None:
                continue
            iv.review = ReviewDecision(
                "accepted",
                d["proposal"] or "",
                d["reviewer"] or "unknown reviewer",
                d["reason"] or "",
                d["created"] or "",
            )
            n += 1
        return n

    def load_project(self, name: str | None = None) -> Project:
        """Rebuild a project (locations + logged strata incl. overrides) from the store."""
        from ..models.entities import Interval, Location

        meta = self.get_meta("project", {}) or {}
        p = Project(name or meta.get("name", "project"), meta.get("crs", self.crs), meta.get("vertical_datum"))
        for r in self.gp.read("boreholes"):
            x, y = r["geometry"]["coordinates"][:2]
            p.locations[r["location_id"]] = Location(
                r["location_id"],
                x,
                y,
                r["ground_level"],
                r["final_depth"],
                LocationKind(r["kind"]),
                r["type_code"] or "",
                DataSource(r["source"]),
                r["date"],
                r["quality"] or 1.0,
                r["remarks"] or "",
            )
        for r in self.gp.read("lithology"):
            iv = Interval(
                r["location_id"],
                r["top"],
                r["base"],
                r["description"] or "",
                r["legend_code"] or "",
                r["geology_code"] or "",
                "",
                r["bgs_code"] or "",
                r["formation_name"] or "",
                source=DataSource(r["source"] or "AGS4"),
            )
            if r["override_unit"]:
                iv.override = Override(r["override_unit"], r["override_by"] or "", r["override_reason"] or "", "")
            # last interpretation (final unit = override if any), e.g. to train a learned model
            iv.unit = r["unit"]
            iv.probability = r["probability"]
            iv.status = InterpretationStatus(r["status"]) if r["status"] else InterpretationStatus.UNRESOLVED
            iv.needs_review = bool(r["needs_review"])
            iv.review_reason = r["review_reason"] or ""
            p.intervals.append(iv)
        return p

    # ------------------------------------------------------------------ model outputs
    def save_model(self, model, contour_interval: float | None = None, write_meshes: bool = True) -> dict:
        run = model.run
        rd = self.raster_dir / run.run_id
        rd.mkdir(parents=True, exist_ok=True)
        g = model.grid
        records, outputs = [], {}
        for name, sg in model.surfaces.items():
            safe = name.replace(":", "_")
            paths = {"raster": write_geotiff(rd / f"{safe}.tif", g, sg.values, self.epsg)}
            paths["std_raster"] = write_geotiff(rd / f"{safe}_std.tif", g, sg.std, self.epsg)
            if sg.classes is not None:
                paths["class_raster"] = write_geotiff(rd / f"{safe}_class.tif", g, sg.classes.astype(float), self.epsg)
            if sg.confidence is not None:
                paths["confidence_raster"] = write_geotiff(rd / f"{safe}_confidence.tif", g, sg.confidence, self.epsg)
            if write_meshes and sg.kind == "elevation" and np.isfinite(sg.values).any():
                paths["mesh"] = write_2dm(rd / f"{safe}.2dm", g, sg.values)
            summ = sg.summary()
            records.append(
                {
                    "run_id": run.run_id,
                    "name": name,
                    "kind": sg.kind,
                    "method": sg.method,
                    "n_data": sg.n_data,
                    "min": summ["min"],
                    "max": summ["max"],
                    "mean_std": summ["mean_std"],
                    **{k: str(v.relative_to(self.path.parent)) for k, v in paths.items()},
                    "variogram": sg.variogram,
                    "diagnostics": sg.diagnostics,
                }
            )
            outputs[name] = {k: str(v) for k, v in paths.items()}
            if (
                sg.kind == "elevation"
                and np.isfinite(sg.values).any()
                and name in (["ground", "rockhead"] + [f"base:{u}" for u in model.stack[:-1]])
            ):
                levels = contour_levels(sg.values, contour_interval)
                self.gp.insert(
                    "surface_contours",
                    [
                        {
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[float(x), float(y), lev] for x, y in line],
                            },
                            "properties": {"run_id": run.run_id, "surface": name, "level": lev},
                        }
                        for lev, line in contour_lines(g, sg.values, levels)
                        if len(line) > 1
                    ],
                )
            if sg.classes is not None and name in (["rockhead"] + [f"base:{u}" for u in model.stack[:-1]]):
                self.gp.insert(
                    "uncertainty_zones",
                    [
                        {
                            "geometry": {"type": "Polygon", "coordinates": [r.tolist() for r in rings]},
                            "properties": {
                                "run_id": run.run_id,
                                "surface": name,
                                "class_code": code,
                                "class_name": CLASS_NAMES[code],
                            },
                        }
                        for code, rings in polygonize(sg.classes, g)
                    ],
                )
        self.gp.insert("surfaces", records)
        units = {}
        for u in model.units:
            units[u] = {
                "top": str(
                    write_geotiff(rd / f"unit_{u}_top.tif", g, model.tops[u], self.epsg).relative_to(self.path.parent)
                ),
                "base": str(
                    write_geotiff(rd / f"unit_{u}_base.tif", g, model.bases[u], self.epsg).relative_to(self.path.parent)
                ),
            }
        run.outputs["units"] = units
        run.outputs["model_meta"] = {
            "units": model.units,
            "stack": model.stack,
            "cover_order": model.cover_order,
            "bottom": model.bottom,
            "province": model.column.id,
            "grid": g.to_dict(),
            "faults": [
                {"name": f.name, "trace": f.trace.tolist(), "throw_m": f.throw_m, "downthrown_side": f.downthrown_side}
                for f in model.faults
            ],
        }
        thk = {k: s.values for k, s in model.surfaces.items() if k.startswith("thk:")}
        if thk:
            dom = domain_grid(thk, model.column, ground=model.surfaces["ground"].values)
            self.gp.insert(
                "domains",
                [
                    {
                        "geometry": {"type": "Polygon", "coordinates": [r.tolist() for r in rings]},
                        "properties": {"run_id": run.run_id, "domain": DOMAINS[code]},
                    }
                    for code, rings in polygonize(dom, g)
                ],
            )
        run.outputs.update({"rasters": outputs})
        self.record_run(run)
        return outputs

    def load_model(self, run_id: str | None = None, column=None):
        """Rebuild a (deterministic) StratigraphicModel from the stored rasters of a run (latest by default).

        The reloaded model supports sections, HDD assessment, exports and queries; Monte Carlo
        simulation needs a rebuild (kriging weights are not stored)."""
        from ..core.provenance import RunRecord
        from ..geology.knowledge import load_column
        from ..modelling.builder import StratigraphicModel, SurfaceGrid
        from ..models.entities import Fault
        from .raster import read_geotiff

        runs = self.runs("stratigraphic_model")
        if not runs:
            raise StoreError("no stored model runs")
        r = next((x for x in runs if x["run_id"] == run_id), None) if run_id else runs[-1]
        if r is None:
            raise StoreError(f"run {run_id} not found")
        outputs = json.loads(r["outputs"]) if isinstance(r["outputs"], str) else (r["outputs"] or {})
        meta = outputs["model_meta"]
        col = column or load_column(meta["province"])
        base = self.path.parent
        tops, bases = {}, {}
        grid = None
        for u, paths in outputs["units"].items():
            grid, tops[u] = read_geotiff(base / paths["top"])
            _, bases[u] = read_geotiff(base / paths["base"])
        surfaces = {}
        for srow in self.gp.read("surfaces", "run_id = ?", (r["run_id"],)):
            if not srow.get("raster") or srow["kind"] == "head":
                continue
            _, v = read_geotiff(base / srow["raster"])
            _, sd = (
                read_geotiff(base / srow["std_raster"]) if srow.get("std_raster") else (None, np.full(v.shape, np.nan))
            )
            cl = read_geotiff(base / srow["class_raster"])[1].astype(int) if srow.get("class_raster") else None
            cf = read_geotiff(base / srow["confidence_raster"])[1] if srow.get("confidence_raster") else None
            surfaces[srow["name"]] = SurfaceGrid(
                srow["name"],
                srow["kind"],
                v,
                sd,
                cf,
                cl,
                srow["method"] or "",
                json.loads(srow["variogram"]) if srow.get("variogram") else None,
                {},
                srow.get("n_data") or 0,
            )
        from ..core.grid import Grid2D

        g = Grid2D.from_dict(meta["grid"])
        run = RunRecord(
            "stratigraphic_model",
            r["input_hash"],
            json.loads(r["config"]) if isinstance(r["config"], str) else r["config"],
            r["kb_version"],
            r["seed"],
            run_id=r["run_id"],
            created=r["created"],
            summary=json.loads(r["summary"]) if isinstance(r["summary"], str) else r["summary"],
            outputs=outputs,
        )
        m = StratigraphicModel(
            g,
            col,
            meta["units"],
            tops,
            bases,
            surfaces,
            run,
            None,
            [],
            [tuple(x) for x in meta["cover_order"]],
            meta["stack"],
            meta["bottom"],
        )
        m.faults = [
            Fault(f["name"], np.array(f["trace"]), f.get("throw_m"), f.get("downthrown_side"))
            for f in meta.get("faults", [])
        ]
        return m

    def load_ground_model(self, run_id: str | None = None):
        """Engineering ground model statistics of a run (latest by default), for HDD assessments made
        on a stored project. Trend intercepts are not stored, so depth trends are not available."""
        from ..processors.ground_model import EngineeringGroundModel, ParameterStats

        rid = run_id or self.latest_run_id()
        stats = {}
        keys = {f for f in ParameterStats.__dataclass_fields__}
        for r in self.gp.read("ground_model_parameters", "run_id = ?", (rid,)):
            d = {k: r[k] for k in keys if k in r}
            d["sources"] = r["sources"] if isinstance(r["sources"], list) else json.loads(r["sources"] or "[]")
            d["log_space"] = bool(r["log_space"])
            for k in (
                "mean",
                "sd",
                "cov",
                "minimum",
                "median",
                "maximum",
                "cautious_mean_low",
                "cautious_mean_high",
                "fractile5_low",
                "fractile5_high",
            ):
                d[k] = float("nan") if d.get(k) is None else float(d[k])
            st = ParameterStats(**d)
            stats[(st.unit, st.parameter)] = st
        return EngineeringGroundModel(stats, [], ["reloaded from project.gpkg (statistics only)"]) if stats else None

    def load_hydro(self, column, run_id: str | None = None):
        """Groundwater head surfaces of a run as a :class:`HydroResult` (surfaces + system mapping)."""
        from ..hydrogeology.engine import HeadSurface, HydroGeoEngine, HydroResult
        from .raster import read_geotiff

        rid = run_id or self.latest_run_id()
        base = self.path.parent
        surfaces = {}
        for r in self.gp.read("surfaces", "run_id = ? AND kind = 'head'", (rid,)):
            sid = r["name"].split(":", 1)[1]
            g, v = read_geotiff(base / r["raster"])
            _, sd = read_geotiff(base / r["std_raster"]) if r.get("std_raster") else (g, np.full(v.shape, np.nan))
            cl = read_geotiff(base / r["class_raster"])[1].astype(int) if r.get("class_raster") else np.full(v.shape, 3)
            surfaces[sid] = HeadSurface(
                sid, g, v, sd, cl, np.exp(-np.nan_to_num(sd, nan=9.0) / 1.5), r["n_data"] or 0, r["method"] or ""
            )
        if not surfaces:
            return None
        eng = HydroGeoEngine(column)
        return HydroResult([], surfaces, system_of=dict(eng._sys_of), notes=["reloaded from project.gpkg"])

    def latest_run_id(self, kind: str = "stratigraphic_model") -> str | None:
        runs = self.runs(kind)
        return runs[-1]["run_id"] if runs else None

    def save_section(self, sec, run_id: str, svg: str | None = None, pdf: str | None = None) -> None:
        from ..visualisation.sections import section_to_features

        self.clear("cross_sections", "name = ?", (sec.name,))
        self.gp.insert(
            "cross_sections",
            [
                {
                    "geometry": {"type": "LineString", "coordinates": sec.line.tolist()},
                    "properties": {"name": sec.name, "length": sec.length, "run_id": run_id, "svg": svg, "pdf": pdf},
                }
            ],
        )
        self.clear("cross_section_units", "section = ?", (sec.name,))
        self.gp.insert("cross_section_units", section_to_features(sec))

    def save_fence(self, panels, run_id: str, name: str = "fence") -> None:
        self.clear("fence_diagrams", "fence = ?", (name,))
        rows = []
        for p in panels:
            s = p.section
            for u, poly in s.unit_polygons():
                ring = []
                for c, z in poly:
                    x = float(np.interp(c, s.chainage, s.xy[:, 0]))
                    y = float(np.interp(c, s.chainage, s.xy[:, 1]))
                    ring.append([x, y, float(z)])
                ring.append(ring[0])
                rows.append(
                    {
                        "geometry": {"type": "Polygon", "coordinates": [ring]},
                        "properties": {"fence": name, "panel": f"{p.a}-{p.b}", "unit": u, "run_id": run_id},
                    }
                )
        self.gp.insert("fence_diagrams", rows)

    def save_cpt(self, results, project: Project, unit_lookup=None) -> None:
        self.clear("cpt")
        rows = []
        for r in results:
            l = project.locations.get(r.location_id)
            if l is None or l.ground_level is None:
                continue
            c = r.columns
            for k, d in enumerate(r.depth):

                def val(key, c=c, k=k):
                    return float(c[key][k]) if np.isfinite(c[key][k]) else None

                rows.append(
                    {
                        "geometry": _pt(l.x, l.y, l.ground_level - float(d)),
                        "properties": {
                            "location_id": r.location_id,
                            "test_id": r.test_id,
                            "depth": float(d),
                            "qc": val("qc_MPa"),
                            "fs": val("fs_MPa"),
                            "qt": val("qt_MPa"),
                            "Ic": val("Ic"),
                            "SBTn": int(c["SBTn"][k]),
                            "su_kPa": val("su_kPa"),
                            "phi_deg": val("phi_deg"),
                            "Dr": val("Dr"),
                            "E_MPa": val("E_MPa"),
                            "M_MPa": val("M_MPa"),
                            "k_ms": val("k_ms"),
                            "gamma_kNm3": val("gamma_kNm3"),
                            "unit": unit_lookup(r.location_id, float(d)) if unit_lookup else None,
                        },
                    }
                )
        self.gp.insert("cpt", rows)

    def save_spt(self, items, project: Project) -> None:
        self.clear("spt")
        rows = []
        for s in items:
            l = project.locations.get(s.location_id)
            if l is None or l.ground_level is None:
                continue
            rows.append({"geometry": _pt(l.x, l.y, l.ground_level - s.depth), "properties": s.to_dict()})
        self.gp.insert("spt", rows)

    def save_ground_model(self, gm, run_id: str) -> None:
        self.gp.insert(
            "ground_model_parameters",
            [{**{k: v for k, v in r.items() if k not in ("trend_intercept",)}, "run_id": run_id} for r in gm.table()],
        )

    def save_hydro(self, hydro, run_id: str, project: Project) -> None:
        self.clear("water_levels")
        self.gp.insert(
            "water_levels", [{"geometry": _pt(o.x, o.y, o.head), "properties": o.to_dict()} for o in hydro.observations]
        )
        for sid, s in hydro.surfaces.items():
            levels = contour_levels(s.values, None, 12)
            self.gp.insert(
                "head_contours",
                [
                    {
                        "geometry": {"type": "LineString", "coordinates": line.tolist()},
                        "properties": {"run_id": run_id, "system": sid, "level": lev},
                    }
                    for lev, line in contour_lines(s.grid, s.values, levels)
                    if len(line) > 1
                ],
            )
            self.gp.insert(
                "flow_vectors",
                [
                    {
                        "geometry": _pt(v["x"], v["y"]),
                        "properties": {
                            "run_id": run_id,
                            "system": sid,
                            "azimuth_deg": v["azimuth_deg"],
                            "gradient": v["gradient"],
                            "head": v["head"],
                            "class_code": v["class"],
                        },
                    }
                    for v in hydro.flow_vectors.get(sid, [])
                ],
            )
            self.gp.insert(
                "seepage_paths",
                [
                    {
                        "geometry": {"type": "LineString", "coordinates": p.tolist()},
                        "properties": {"run_id": run_id, "system": sid},
                    }
                    for p in hydro.seepage_paths.get(sid, [])
                ],
            )
            rd = self.raster_dir / run_id
            rd.mkdir(parents=True, exist_ok=True)
            paths = {
                "raster": write_geotiff(rd / f"head_{sid}.tif", s.grid, s.values, self.epsg),
                "std_raster": write_geotiff(rd / f"head_{sid}_std.tif", s.grid, s.std, self.epsg),
                "class_raster": write_geotiff(rd / f"head_{sid}_class.tif", s.grid, s.classes.astype(float), self.epsg),
            }
            if sid in hydro.confinement:
                write_geotiff(rd / f"confinement_{sid}.tif", s.grid, hydro.confinement[sid].astype(float), self.epsg)
            self.gp.insert(
                "surfaces",
                [
                    {
                        "run_id": run_id,
                        "name": f"head:{sid}",
                        "kind": "head",
                        "method": s.method,
                        "n_data": s.n_obs,
                        "min": float(np.nanmin(s.values)),
                        "max": float(np.nanmax(s.values)),
                        "mean_std": float(np.nanmean(s.std)),
                        **{k: str(v.relative_to(self.path.parent)) for k, v in paths.items()},
                    }
                ],
            )

    def save_hdd(self, a, run_id: str) -> None:
        name = a.alignment.name
        self.clear("hdd_stations", "alignment = ?", (name,))
        self.clear("hdd_risk_zones", "alignment = ?", (name,))
        rows = []
        for s in a.stations:
            d = s.to_dict()
            rows.append(
                {
                    "geometry": _pt(s.x, s.y, s.z),
                    "properties": {
                        "run_id": run_id,
                        "alignment": name,
                        "chainage": s.chainage,
                        "unit": s.unit,
                        "cover": s.cover,
                        "p_unit": s.p_unit,
                        "pressure_head": s.pressure_head,
                        "frac_fos": s.frac_fos,
                        "confidence_rag": d["confidence_rag"],
                        "rag_overall": d["rag_overall"],
                        **{
                            f"rag_{h}": d[f"rag_{h}"]
                            for h in (
                                "running_sand",
                                "groundwater",
                                "boulders",
                                "rock",
                                "mixed_face",
                                "loss_of_circulation",
                            )
                        },
                        "reasons": s.reasons,
                    },
                }
            )
        self.gp.insert("hdd_stations", rows)
        from ..core.geometry import point_at_chainage

        zrows = []
        for z in a.risk_zones:
            ch = np.linspace(z["from"], max(z["to"], z["from"] + 0.5), 5)
            xy = point_at_chainage(a.alignment.line, ch)
            zrows.append(
                {
                    "geometry": {"type": "LineString", "coordinates": xy.tolist()},
                    "properties": {
                        "run_id": run_id,
                        "alignment": name,
                        "hazard": z["hazard"],
                        "rag": z["rag"],
                        "ch_from": z["from"],
                        "ch_to": z["to"],
                        "reasons": z["reasons"],
                    },
                }
            )
        self.gp.insert("hdd_risk_zones", zrows)

    def save_bgs(self, candidates) -> None:
        self.clear("bgs_boreholes")
        self.gp.insert(
            "bgs_boreholes",
            [
                {
                    "geometry": _pt(c.x, c.y),
                    "properties": {
                        "bgs_id": c.bgs_id,
                        "reference": c.reference,
                        "name": c.name,
                        "collection": c.collection,
                        "length_m": c.length_m,
                        "year": c.year,
                        "precision": c.precision,
                        "has_ags": c.has_ags,
                        "url": c.url,
                        "distance_m": c.distance_m,
                        "confidence": c.confidence,
                        "score_breakdown": c.breakdown,
                        "selected": c.selected,
                    },
                }
                for c in candidates
            ],
        )

    def save_model_3d(self, model, max_triangles_per_unit: int = 20000, min_thickness: float = 0.05) -> int:
        """Unit top surfaces as PolygonZ triangles (table ``model_surfaces_3d``) so the model can be
        shown in the QGIS 3D view with vector 3D renderers (no mesh 3D renderer required). Only the
        latest run is kept. Returns the number of triangles written."""
        g = model.grid
        stride = max(1, int(np.ceil(np.sqrt(2.0 * g.nx * g.ny / max_triangles_per_unit))))
        X, Y = g.mesh()
        X, Y = X[::stride, ::stride].ravel(), Y[::stride, ::stride].ravel()
        self.clear("model_surfaces_3d")
        rows = []
        for u in model.units:
            top = np.asarray(model.tops[u], float)[::stride, ::stride]
            base = np.asarray(model.bases[u], float)[::stride, ::stride]
            ny, nx = top.shape
            ok = (np.isfinite(top) & np.isfinite(base) & ((top - base) > min_thickness)).ravel()
            idx = np.arange(ny * nx).reshape(ny, nx)
            a, b, c, d = idx[:-1, :-1], idx[:-1, 1:], idx[1:, 1:], idx[1:, :-1]
            tris = np.concatenate([np.stack([a, b, c], -1).reshape(-1, 3), np.stack([a, c, d], -1).reshape(-1, 3)])
            tris = tris[ok[tris].all(axis=1)]
            if not len(tris):
                continue
            sname = model._top_surface_name(u)
            sgrid = model.surfaces.get(sname) if sname else None
            cls = (
                np.asarray(sgrid.classes)[::stride, ::stride].ravel()
                if sgrid is not None and sgrid.classes is not None
                else None
            )
            cc = cls[tris].max(axis=1).astype(int) if cls is not None else [None] * len(tris)
            zt = top.ravel()
            for t, code in zip(tris, cc):
                ring = [[float(X[k]), float(Y[k]), float(zt[k])] for k in t]
                ring.append(ring[0])
                rows.append(
                    {
                        "geometry": {"type": "Polygon", "coordinates": [ring]},
                        "properties": {
                            "run_id": model.run.run_id,
                            "unit": u,
                            "surface": "top",
                            "class_code": None if code is None else int(code),
                        },
                    }
                )
        self.gp.insert("model_surfaces_3d", rows)
        return len(rows)

    def purge_runs(self, keep: int = 5) -> list[str]:
        """Delete outputs of all but the ``keep`` most recent model runs."""
        runs = self.runs("stratigraphic_model")
        drop = [r["run_id"] for r in runs[:-keep]] if keep else [r["run_id"] for r in runs]
        for rid in drop:
            for t in ("surfaces", "surface_contours", "uncertainty_zones", "domains", "model_runs"):
                self.clear(t, "run_id = ?", (rid,))
            shutil.rmtree(self.raster_dir / rid, ignore_errors=True)
        return drop
