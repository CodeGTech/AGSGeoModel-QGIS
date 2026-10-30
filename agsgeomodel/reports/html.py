"""Self-contained HTML interpretive report for a pipeline run (inline CSS and SVG, prints to PDF)."""

from __future__ import annotations

import datetime as _dt
import html
from collections import Counter
from pathlib import Path

import numpy as np

from .. import __version__

CSS = """
:root{--ink:#1d2327;--muted:#5f6b72;--line:#d5dade;--bg:#ffffff;--soft:#f4f6f7;--green:#2E7D32;--amber:#B26A00;--red:#C62828}
*{box-sizing:border-box}body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:var(--ink);background:var(--bg);
margin:0;line-height:1.45;font-size:14px}main{max-width:1180px;margin:0 auto;padding:28px 24px 60px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:34px 0 10px;padding-top:12px;border-top:2px solid var(--ink)}
h3{font-size:15px;margin:20px 0 8px}p.lead{color:var(--muted);margin:0 0 18px}.small{font-size:12px;color:var(--muted)}
table{border-collapse:collapse;width:100%;margin:8px 0 14px;font-size:12.5px}th,td{border-bottom:1px solid var(--line);
padding:5px 7px;text-align:left;vertical-align:top}th{background:var(--soft);font-weight:600}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin:14px 0}
.kpi{background:var(--soft);border-radius:6px;padding:10px 12px}.kpi b{display:block;font-size:20px}.kpi span{color:var(--muted);font-size:12px}
.tag{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11.5px;font-weight:600;color:#fff}
.Green{background:var(--green)}.Amber{background:var(--amber)}.Red{background:var(--red)}
.sev-critical,.sev-error{color:var(--red);font-weight:600}.sev-warning{color:var(--amber);font-weight:600}.sev-info{color:var(--muted)}
.swatch{display:inline-block;width:12px;height:12px;border:1px solid #555;margin-right:6px;vertical-align:-1px}
figure{margin:12px 0;border:1px solid var(--line);border-radius:6px;padding:8px;overflow-x:auto}figure svg{width:100%;height:auto;min-width:760px}
figcaption{color:var(--muted);font-size:12px;margin-top:6px}.callout{background:#fff8e1;border-left:4px solid var(--amber);padding:10px 12px;margin:12px 0}
@media print{h2{break-before:page}figure{break-inside:avoid}main{max-width:none}}
"""


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _f(x, nd=2) -> str:
    try:
        return "" if x is None or not np.isfinite(x) else f"{x:.{nd}f}"
    except TypeError:
        return _e(x)


def _table(headers, rows, num_cols=()) -> str:
    h = "".join(
        f"<th class='num'>{_e(c)}</th>" if i in num_cols else f"<th>{_e(c)}</th>" for i, c in enumerate(headers)
    )
    body = []
    for r in rows:
        body.append(
            "<tr>"
            + "".join(f"<td class='num'>{c}</td>" if i in num_cols else f"<td>{c}</td>" for i, c in enumerate(r))
            + "</tr>"
        )
    return f"<table><thead><tr>{h}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def write_html_report(res, path) -> Path:
    p = res.project
    interp = res.interpretation
    model = res.model
    kb = interp.column.kb if interp else None
    run = model.run if model else None
    parts = [
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
        f"content='width=device-width,initial-scale=1'><title>{_e(p.name)} - ground model report</title>"
        f"<style>{CSS}</style></head><body><main>"
    ]
    parts.append(
        f"<h1>{_e(p.name)}</h1><p class='lead'>Geological interpretation and ground model report - "
        f"AGSGeoModel {__version__} - {_dt.datetime.now():%d %B %Y %H:%M}</p>"
    )
    s = p.summary()
    kpis = [
        ("Exploratory holes", s["locations"]),
        ("Logged intervals", s["intervals"]),
        ("CPT readings", s["cpt_readings"]),
        ("SPT tests", s["spt"]),
        ("Lab results", s["lab_results"]),
    ]
    if res.hdd:
        kpis.append(("HDD overall", res.hdd.summary["overall_rag"]))
    parts.append(
        "<div class='kpis'>"
        + "".join(f"<div class='kpi'><b>{_e(v)}</b><span>{_e(k)}</span></div>" for k, v in kpis)
        + "</div>"
    )
    parts.append(
        "<div class='callout'>Between exploratory holes every surface is an interpretation. Each output "
        "is classed Observed / Interpolated / Inferred / Speculative and carries a confidence; read the "
        "classes before using elevations for design.</div>"
    )
    # ---------------------------------------------------------------- data and validation
    parts.append("<h2>1. Data and validation</h2>")
    files = [
        [
            _e(f.get("name")),
            _e(f.get("format")),
            _e(f.get("version")),
            f"<span class='small'>{_e(f.get('sha256', '')[:16])}...</span>",
        ]
        for f in p.source_files
    ]
    parts.append(_table(["Source file", "Format", "Version", "SHA-256"], files))
    parts.append(
        f"<p>CRS {_e(p.crs)}, vertical datum {_e(p.vertical_datum)} ({_e(p.metadata.get('vertical_datum_basis'))}). "
        f"CRS detection: {_e(p.metadata.get('crs_detection'))}.</p>"
    )
    secondary = [loc for loc in p.locations.values() if loc.source.value.startswith("BGS")]
    if secondary:
        from ..bgs.services import ATTRIBUTION

        parts.append(
            "<h3>Secondary data</h3><p>Records below are secondary control: their contacts carry a larger "
            "observation uncertainty (scaled by the record's confidence) and are never classed as Observed.</p>"
            + _table(
                ["Location", "Source", "Confidence", "Ground level", "Final depth"],
                [
                    [
                        _e(loc.id),
                        _e(loc.source.value),
                        f"{100 * (loc.quality if loc.quality is not None else 0):.0f}%",
                        _f(loc.ground_level),
                        _f(loc.final_depth),
                    ]
                    for loc in sorted(secondary, key=lambda x: x.id)
                ],
                num_cols=(2, 3, 4),
            )
            + f"<p class='small'>{_e(ATTRIBUTION.format(year=_dt.date.today().year))}.</p>"
        )
    sev = Counter(i.severity.value for i in res.issues)
    parts.append(
        _table(
            ["Critical", "Error", "Warning", "Info"],
            [[sev.get("critical", 0), sev.get("error", 0), sev.get("warning", 0), sev.get("info", 0)]],
            num_cols=(0, 1, 2, 3),
        )
    )
    top = sorted(res.issues, key=lambda i: -i.severity.rank)[:40]
    parts.append(
        "<h3>Findings requiring review</h3>"
        + _table(
            ["Rule", "Severity", "Location(s)", "Finding", "Possible explanations"],
            [
                [
                    _e(i.rule),
                    f"<span class='sev-{i.severity.value}'>{_e(i.severity.value)}</span>",
                    _e(", ".join(i.location_ids)),
                    _e(i.message),
                    _e("; ".join(i.explanations[:3])),
                ]
                for i in top
            ],
        )
    )
    # ---------------------------------------------------------------- stratigraphy
    if interp:
        parts.append("<h2>2. Stratigraphy and interpretation</h2>")
        col = interp.column
        parts.append(f"<p>Province column <b>{_e(col.name)}</b>. {_e(col.description)}</p>")
        thick: dict[str, list[float]] = {}
        for li in interp.locations.values():
            for r in li.runs:
                thick.setdefault(r.unit, []).append(r.thickness)
        rows = []
        for u in col.units:
            unit = kb[u]
            t = thick.get(u, [])
            link = f"<a href='{_e(unit.lexicon_url)}'>{_e(u)}</a>" if unit.lexicon_url else _e(u)
            rows.append(
                [
                    f"<span class='swatch' style='background:{unit.colour}'></span>{_e(unit.name)}",
                    link,
                    _e(unit.age_label),
                    _e(unit.hsu.value),
                    str(len(t)),
                    f"{min(t):.1f} - {max(t):.1f}" if t else "not encountered",
                ]
            )
        parts.append(_table(["Unit", "Lexicon", "Age", "Hydro", "Holes", "Logged thickness (m)"], rows, num_cols=(4,)))
        review = [iv for iv in p.intervals if iv.needs_review]
        parts.append(f"<h3>Intervals flagged for review ({len(review)})</h3>")
        parts.append(
            _table(
                ["Hole", "Depth (m)", "Description", "Interpretation", "p", "Reason"],
                [
                    [
                        _e(iv.location_id),
                        f"{iv.top:.2f}-{iv.base:.2f}",
                        _e(iv.description[:90]),
                        _e(iv.unit),
                        _f(iv.probability),
                        _e(iv.review_reason),
                    ]
                    for iv in review[:60]
                ],
            )
        )
    # ---------------------------------------------------------------- model
    if model:
        parts.append("<h2>3. Ground model</h2>")
        parts.append(
            f"<p>Grid {model.grid.nx} x {model.grid.ny} at {model.grid.dx:g} m; reference surface "
            f"{_e(run.summary.get('reference_surface'))}; interpolation {_e(run.config['interpolation']['method'])}. "
            f"Run <code>{_e(run.run_id)}</code>, input hash <code>{_e(run.input_hash[:16])}</code>, "
            f"knowledge base {_e(run.kb_version)}, seed {_e(run.seed)}.</p>"
        )
        n_real = int((getattr(model, "_realisations", None) or {}).get("n", 0))
        parts.append(
            f"<p>Monte Carlo: {n_real} conditional realisation(s) of every surface and isochore (Gaussian "
            "random fields conditioned by kriging) give unit probabilities and exceedance levels; "
            "deterministic surfaces are the kriged means.</p>"
            if n_real
            else "<p>Monte Carlo: not run (deterministic surfaces only; set monte_carlo_realisations).</p>"
        )
        rows = []
        for name, sg in model.surfaces.items():
            if not (name in ("ground", "rockhead") or name.startswith(("base:", "thk_seq"))):
                continue
            cls = (
                np.bincount(sg.classes.ravel(), minlength=4) / sg.classes.size
                if sg.classes is not None
                else [np.nan] * 4
            )
            vg = sg.variogram or {}
            rows.append(
                [
                    _e(name),
                    _e(sg.method),
                    str(sg.n_data),
                    _f(np.nanmin(sg.values), 1),
                    _f(np.nanmax(sg.values), 1),
                    _f(np.nanmedian(sg.std)),
                    _e(f"{vg.get('model', '')} {'fitted' if vg.get('fitted') else 'assumed' if vg else ''}"),
                    " / ".join(f"{c * 100:.0f}%" for c in cls),
                ]
            )
        parts.append(
            _table(
                [
                    "Surface",
                    "Method",
                    "Data",
                    "Min",
                    "Max",
                    "Median sigma (m)",
                    "Variogram",
                    "Obs / Interp / Inferred / Spec",
                ],
                rows,
                num_cols=(2, 3, 4, 5),
            )
        )
        if model.warnings:
            parts.append("<h3>Model warnings</h3><ul>" + "".join(f"<li>{_e(w)}</li>" for w in model.warnings) + "</ul>")
    for _sec, d in res.sections:
        parts.append(f"<figure>{d.to_svg()}<figcaption>{_e(d.title)}</figcaption></figure>")
    # ---------------------------------------------------------------- engineering
    gm = res.ground_model
    if gm is not None:
        parts.append(
            "<h2>4. Engineering ground model</h2><p class='small'>Characteristic values are statistical aids "
            "(cautious mean = mean x (1 -/+ 0.5 COV); 5 % fractile with Student t). The designer selects the "
            "value appropriate to each limit state (BS EN 1997-1, 2.4.5.2).</p>"
        )
        keys = ["SPT_N60", "SPT_cu", "CPT_su", "CPT_phi", "LLPL_PI", "LNMC_MC", "CPT_k", "SO4"]
        rows = []
        for r in gm.table():
            if r["parameter"] in keys:
                rows.append(
                    [
                        _e(r["unit"]),
                        _e(r["label"]),
                        _e(r["uom"]),
                        str(r["n"]),
                        _f(r["mean"]),
                        _f(r["cov"]),
                        _f(r["cautious_mean_low"]),
                        _f(r["fractile5_low"]),
                        _f(r["trend_slope"]) + (" /m" if r["trend_slope"] is not None else ""),
                    ]
                )
        parts.append(
            _table(
                ["Unit", "Parameter", "Units", "n", "Mean", "COV", "Cautious low", "5% low", "Depth trend"],
                rows,
                num_cols=(3, 4, 5, 6, 7),
            )
        )
    # ---------------------------------------------------------------- hydrogeology
    if res.hydro is not None:
        h = res.hydro
        parts.append("<h2>5. Hydrogeology</h2>")
        rows = []
        for sid, srf in h.surfaces.items():
            conf = h.confinement.get(sid)
            cc = Counter(conf.ravel().tolist()) if conf is not None else {}
            tot = sum(cc.values()) or 1
            rows.append(
                [
                    _e(sid),
                    str(srf.n_obs),
                    _f(np.nanmin(srf.values)),
                    _f(np.nanmax(srf.values)),
                    _f(np.nanmedian(srf.std)),
                    f"{100 * cc.get(1, 0) / tot:.0f}%",
                    f"{100 * cc.get(3, 0) / tot:.0f}%",
                    f"{100 * cc.get(2, 0) / tot:.0f}%",
                ]
            )
        parts.append(
            _table(
                [
                    "System",
                    "Observations",
                    "Head min (m)",
                    "Head max (m)",
                    "Median sigma",
                    "Confined",
                    "Possible leakage",
                    "Artesian",
                ],
                rows,
                num_cols=(1, 2, 3, 4),
            )
        )
        perched = [o for o in h.observations if o.perched]
        if perched:
            parts.append(
                "<h3>Perched water</h3>"
                + _table(
                    ["Hole", "Unit", "Head (m)", "Note"],
                    [[_e(o.location_id), _e(o.unit), _f(o.head), _e(o.note)] for o in perched],
                )
            )
    # ---------------------------------------------------------------- HDD
    if res.hdd is not None:
        a = res.hdd
        sm = a.summary
        parts.append("<h2>6. HDD ground-risk assessment</h2>")
        parts.append(
            _table(
                [
                    "Alignment",
                    "Bore length (m)",
                    "Reamed dia (mm)",
                    "Min cover (m)",
                    "Units crossed",
                    "Overall",
                    "Red (m)",
                    "Amber (m)",
                    "Min frac-out FoS",
                ],
                [
                    [
                        _e(sm["name"]),
                        _f(sm["bore_length_m"], 0),
                        _f(sm["reamed_diameter_m"] * 1000, 0),
                        _f(sm["min_cover_m"], 1),
                        _e(", ".join(sm["units_crossed"])),
                        f"<span class='tag {sm['overall_rag']}'>{sm['overall_rag']}</span>",
                        _f(sm["red_length_m"], 0),
                        _f(sm["amber_length_m"], 0),
                        _f(sm["min_frac_fos"]),
                    ]
                ],
                num_cols=(1, 2, 3, 6, 7, 8),
            )
        )
        zones = [z for z in a.risk_zones if z["hazard"] == "overall"]
        parts.append(
            "<h3>Risk zones</h3>"
            + _table(
                ["From (m)", "To (m)", "Rating", "Reasons"],
                [
                    [
                        _f(z["from"], 0),
                        _f(z["to"], 0),
                        f"<span class='tag {z['rag']}'>{z['rag']}</span>",
                        _e("; ".join(z["reasons"])),
                    ]
                    for z in zones
                ],
                num_cols=(0, 1),
            )
        )
        parts.append(
            "<h3>Formation changes along the bore</h3>"
            + _table(
                ["Chainage (m)", "From", "To", "Chainage uncertainty (m)"],
                [
                    [_f(c["chainage"], 1), _e(c["from_unit"]), _e(c["to_unit"]), _f(c["chainage_uncertainty_m"], 1)]
                    for c in a.formation_changes
                ],
                num_cols=(0, 3),
            )
        )
        parts.append(
            "<h3>Engineering constraints</h3>"
            + _table(
                ["Check", "Result", "Detail"],
                [
                    [_e(c["type"]), "pass" if c["pass"] else "<b class='sev-error'>FAIL</b>", _e(c["message"])]
                    for c in a.constraints
                ],
            )
        )
        if a.notes:
            parts.append("<ul>" + "".join(f"<li class='small'>{_e(n)}</li>" for n in a.notes) + "</ul>")
    # ---------------------------------------------------------------- provenance
    parts.append("<h2>7. Provenance and limitations</h2>")
    if res.notes:
        parts.append("<ul>" + "".join(f"<li>{_e(n)}</li>" for n in res.notes) + "</ul>")
    parts.append(_table(["Stage", "Time (s)"], [[_e(k), _f(v, 1)] for k, v in res.timings.items()], num_cols=(1,)))
    parts.append(
        "<p class='small'>Interpretations are produced by an order-constrained probabilistic model using "
        "the BS 5930 description, logged codes and neighbouring holes, with a knowledge base keyed to BGS Lexicon "
        "codes. It does not replace review by a competent engineering geologist. Uncertainty classes describe "
        "data support and model variance, not all sources of error (e.g. logging bias, unknown faults).</p>"
    )
    parts.append("</main></body></html>")
    path = Path(path)
    path.write_text("".join(parts), encoding="utf-8")
    return path
