"""HDD long section: geological section along the bore with design profile, groundwater and RAG strips."""

from __future__ import annotations

from ..models.enums import RAG
from ..visualisation.colours import RAG_HEX
from ..visualisation.drawing import Drawing, Style
from ..visualisation.sections import compute_section, render_section
from .assessment import HAZARDS, HDDAssessment

_RAG_BY_LEVEL = {0: RAG_HEX[RAG.GREEN], 1: RAG_HEX[RAG.AMBER], 2: RAG_HEX[RAG.RED]}
LABELS = {
    "overall": "Overall",
    "running_sand": "Running sand",
    "groundwater": "Groundwater",
    "boulders": "Boulders",
    "rock": "Rock / hard bands",
    "mixed_face": "Mixed face",
    "loss_of_circulation": "Loss of circulation",
    "confidence": "Geological confidence",
}


def render_hdd_long_section(
    assessment: HDDAssessment,
    model,
    project=None,
    interpretation=None,
    hydro=None,
    page: str = "A3",
    vertical_exaggeration: float = 5.0,
    corridor: float = 30.0,
) -> Drawing:
    al = assessment.alignment
    water = {k: (s.grid, s.values) for k, s in (hydro.surfaces.items() if hydro else [])}
    sec = compute_section(
        model,
        al.line,
        name=f"{al.name} - long section",
        project=project,
        interpretation=interpretation,
        corridor=corridor,
        water_surfaces=water,
        alignment_profile=(al.profile.chainage, al.profile.elevation),
    )
    for c in assessment.constraints:
        if c["type"] == "crossing clearance":
            sec.crossings.append((c["name"], c["chainage"], c.get("required_m")))
    s = assessment.summary
    sub = (
        f"Bore length {s['bore_length_m']:.0f} m, reamed diameter {s['reamed_diameter_m'] * 1000:.0f} mm, "
        f"min cover {s['min_cover_m']:.1f} m, overall {s['overall_rag']}"
    )
    rows = ["overall"] + list(HAZARDS) + ["confidence"]
    strip_h = 4.0
    d = render_section(
        sec,
        model.column.kb,
        page=page,
        vertical_exaggeration=vertical_exaggeration,
        title=f"{al.name}: geological long section and ground-risk assessment",
        subtitle=sub,
        bottom_reserve_mm=strip_h * len(rows) + 8,
    )
    m = d.meta
    y0 = m["mT"] + m["plot_h"] + 14
    X = lambda c: m["mL"] + c * m["sx"]  # noqa: E731
    stations = assessment.stations
    for r, key in enumerate(rows):
        y = y0 + r * strip_h
        d.text(m["mL"] - 2, y + strip_h / 2, LABELS[key], Style(font_size=2.0, anchor="end", baseline="middle"))
        if not stations:
            continue
        lv = [
            s.overall if key == "overall" else s.confidence if key == "confidence" else s.hazards.get(key, 0)
            for s in stations
        ]
        start = 0
        for i in range(1, len(stations) + 1):
            if i == len(stations) or lv[i] != lv[start]:
                c0 = stations[start].chainage
                c1 = stations[i - 1].chainage if i < len(stations) else stations[-1].chainage
                if i < len(stations):
                    c1 = 0.5 * (stations[i - 1].chainage + stations[i].chainage)
                if start > 0:
                    c0 = 0.5 * (stations[start - 1].chainage + stations[start].chainage)
                d.rect(
                    X(c0), y, max(X(c1) - X(c0), 0.05), strip_h - 0.6, Style(fill=_RAG_BY_LEVEL[lv[start]], stroke=None)
                )
                start = i
    lx, ly = m["legend_x"], m["legend_y"] + 6
    d.text(lx, ly, "RAG", Style(font_size=2.8, bold=True))
    for k, (lvl, name) in enumerate(((0, "Green"), (1, "Amber"), (2, "Red"))):
        d.rect(lx, ly + 3 + 4.5 * k, 5, 3.5, Style(fill=_RAG_BY_LEVEL[lvl], stroke="#000000", width=0.15))
        d.text(lx + 6.5, ly + 4.75 + 4.5 * k, name, Style(font_size=2.1, baseline="middle"))
    ly += 21
    d.line(lx, ly, lx + 5, ly, Style(stroke="#D50000", width=0.8))
    d.text(lx + 6.5, ly, "Design bore profile", Style(font_size=2.1, baseline="middle"))
    ly += 7
    for n in assessment.notes[:3]:
        for line in wrap(n, 46):
            d.text(lx, ly, line, Style(font_size=1.8, fill="#555555"))
            ly += 2.6
        ly += 1.5
    return d


def wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines
