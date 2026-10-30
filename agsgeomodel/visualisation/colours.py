"""Colour helpers for units, uncertainty classes and RAG ratings (shared by SVG/PDF and QGIS styles)."""

from __future__ import annotations

from ..models.enums import RAG, ConfidenceClass

RAG_HEX = {RAG.GREEN: "#2E7D32", RAG.AMBER: "#F9A825", RAG.RED: "#C62828"}
CLASS_HEX = {
    ConfidenceClass.OBSERVED: "#1B5E20",
    ConfidenceClass.INTERPOLATED: "#66BB6A",
    ConfidenceClass.INFERRED: "#F9A825",
    ConfidenceClass.SPECULATIVE: "#C62828",
}
CLASS_BY_CODE = [
    ConfidenceClass.OBSERVED,
    ConfidenceClass.INTERPOLATED,
    ConfidenceClass.INFERRED,
    ConfidenceClass.SPECULATIVE,
]
LOCATION_SYMBOLS = {
    "BH": ("circle", "#1565C0"),
    "CPT": ("triangle_down", "#6A1B9A"),
    "TP": ("square", "#2E7D32"),
    "WS": ("diamond", "#EF6C00"),
    "DP": ("cross", "#455A64"),
    "BGS": ("triangle", "#AD1457"),
    "OTHER": ("circle", "#616161"),
}


def unit_colour(kb, code: str) -> str:
    u = kb.get(code) if kb is not None else None
    return u.colour if u is not None else "#BDBDBD"


def darker(hex_colour: str, factor: float = 0.6) -> str:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"#{int(r * factor):02X}{int(g * factor):02X}{int(b * factor):02X}"


def text_on(hex_colour: str) -> str:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "#FFFFFF"
