"""Minimal vector drawing model rendered to SVG and PDF without third-party dependencies.

Coordinates are millimetres on the page with the origin at the top-left (SVG convention).
The PDF writer flips the y-axis and uses the standard Helvetica fonts (WinAnsi encoding), so
outputs are true vector drawings suitable for reports and printing at scale.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

MM_TO_PT = 72.0 / 25.4

PAGE_SIZES = {
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
}

# Helvetica AFM advance widths (1/1000 em) for ASCII 32..126
_HELV = [
    278,
    278,
    355,
    556,
    556,
    889,
    667,
    191,
    333,
    333,
    389,
    584,
    278,
    333,
    278,
    278,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    556,
    278,
    278,
    584,
    584,
    584,
    556,
    1015,
    667,
    667,
    722,
    722,
    667,
    611,
    778,
    722,
    278,
    500,
    667,
    556,
    833,
    722,
    778,
    667,
    778,
    722,
    667,
    611,
    722,
    667,
    944,
    667,
    667,
    611,
    278,
    278,
    278,
    469,
    556,
    333,
    556,
    556,
    500,
    556,
    556,
    278,
    556,
    556,
    222,
    222,
    500,
    222,
    833,
    556,
    556,
    556,
    556,
    333,
    500,
    278,
    556,
    500,
    722,
    500,
    500,
    500,
    334,
    260,
    334,
    584,
]


def text_width_mm(text: str, size_mm: float, bold: bool = False) -> float:
    w = sum(_HELV[ord(c) - 32] if 32 <= ord(c) <= 126 else 556 for c in text)
    return w / 1000.0 * size_mm * (1.05 if bold else 1.0)


@dataclass
class Style:
    fill: str | None = None
    stroke: str | None = "#000000"
    width: float = 0.25  # mm
    dash: tuple[float, ...] | None = None
    opacity: float = 1.0
    font_size: float = 2.5  # mm
    bold: bool = False
    anchor: str = "start"  # start | middle | end
    baseline: str = "alphabetic"  # alphabetic | middle | hanging

    def __post_init__(self):
        if isinstance(self.fill, str) and self.fill.lower() in ("none", "transparent", ""):
            self.fill = None
        if isinstance(self.stroke, str) and self.stroke.lower() in ("none", "transparent", ""):
            self.stroke = None


@dataclass
class Item:
    kind: str  # polygon | polyline | text | circle
    points: list[tuple[float, float]] = field(default_factory=list)
    style: Style = field(default_factory=Style)
    text: str = ""
    rotate: float = 0.0
    radius: float = 0.0
    layer: str = "main"


class Drawing:
    def __init__(self, width_mm: float, height_mm: float, title: str = ""):
        self.width = width_mm
        self.height = height_mm
        self.title = title
        self.items: list[Item] = []

    @classmethod
    def page(cls, size: str = "A3", landscape: bool = True, title: str = "") -> Drawing:
        w, h = PAGE_SIZES[size]
        return cls(w, h, title) if landscape else cls(h, w, title)

    # ------------------------------------------------------------------ primitives
    def polygon(self, pts, style: Style, layer: str = "main") -> None:
        pts = [(float(x), float(y)) for x, y in pts]
        if len(pts) >= 3:
            self.items.append(Item("polygon", pts, style, layer=layer))

    def polyline(self, pts, style: Style, layer: str = "main") -> None:
        pts = [(float(x), float(y)) for x, y in pts]
        if len(pts) >= 2:
            self.items.append(Item("polyline", pts, style, layer=layer))

    def line(self, x1, y1, x2, y2, style: Style, layer: str = "main") -> None:
        self.polyline([(x1, y1), (x2, y2)], style, layer)

    def rect(self, x, y, w, h, style: Style, layer: str = "main") -> None:
        self.polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], style, layer)

    def circle(self, cx, cy, r, style: Style, layer: str = "main") -> None:
        self.items.append(Item("circle", [(float(cx), float(cy))], style, radius=float(r), layer=layer))

    def text(self, x, y, s: str, style: Style, rotate: float = 0.0, layer: str = "text") -> None:
        self.items.append(Item("text", [(float(x), float(y))], style, text=str(s), rotate=rotate, layer=layer))

    # ------------------------------------------------------------------ SVG
    def to_svg(self) -> str:
        out = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}mm" height="{self.height}mm" '
            f'viewBox="0 0 {self.width} {self.height}" font-family="Helvetica, Arial, sans-serif">'
        ]
        if self.title:
            out.append(f"<title>{escape(self.title)}</title>")
        out.append(f'<rect x="0" y="0" width="{self.width}" height="{self.height}" fill="#ffffff"/>')
        layers: dict[str, list[str]] = {}
        for it in self.items:
            s = it.style
            common = []
            if it.kind != "text":
                common.append(f'fill="{s.fill}"' if s.fill else 'fill="none"')
                common.append(f'stroke="{s.stroke}" stroke-width="{s.width:.3f}"' if s.stroke else 'stroke="none"')
                if s.dash:
                    common.append(f'stroke-dasharray="{" ".join(f"{d:.2f}" for d in s.dash)}"')
                common.append('stroke-linejoin="round"')
            if s.opacity < 1:
                common.append(f'opacity="{s.opacity:.3f}"')
            attrs = " ".join(common)
            if it.kind in ("polygon", "polyline"):
                pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in it.points)
                tag = "polygon" if it.kind == "polygon" else "polyline"
                el = f'<{tag} points="{pts}" {attrs}/>'
            elif it.kind == "circle":
                ((cx, cy),) = it.points
                el = f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{it.radius:.2f}" {attrs}/>'
            else:
                ((x, y),) = it.points
                anchor = {"start": "start", "middle": "middle", "end": "end"}[s.anchor]
                dom = {"alphabetic": "auto", "middle": "central", "hanging": "hanging"}[s.baseline]
                rot = f' transform="rotate({it.rotate:.1f} {x:.2f} {y:.2f})"' if it.rotate else ""
                weight = ' font-weight="bold"' if s.bold else ""
                fill = s.fill or "#000000"
                el = (
                    f'<text x="{x:.2f}" y="{y:.2f}" font-size="{s.font_size:.2f}" text-anchor="{anchor}" '
                    f'dominant-baseline="{dom}" fill="{fill}"{weight}{rot} {attrs}>{escape(it.text)}</text>'
                )
            layers.setdefault(it.layer, []).append(el)
        for name, els in layers.items():
            out.append(f'<g id="{escape(name)}">')
            out.extend(els)
            out.append("</g>")
        out.append("</svg>")
        return "\n".join(out)

    def save_svg(self, path) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.to_svg())

    # ------------------------------------------------------------------ PDF
    def to_pdf(self) -> bytes:
        W, H = self.width * MM_TO_PT, self.height * MM_TO_PT
        ops: list[str] = []
        gstates: dict[float, str] = {}

        def pt(x, y):
            return x * MM_TO_PT, H - y * MM_TO_PT

        def col(hexs: str) -> tuple[float, float, float]:
            h = hexs.lstrip("#")
            return tuple(int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]

        for it in self.items:
            s = it.style
            ops.append("q")
            if s.opacity < 1:
                key = round(s.opacity, 3)
                gs = gstates.setdefault(key, f"GS{len(gstates) + 1}")
                ops.append(f"/{gs} gs")
            if it.kind in ("polygon", "polyline", "circle"):
                if s.fill:
                    ops.append("{:.4f} {:.4f} {:.4f} rg".format(*col(s.fill)))
                if s.stroke:
                    ops.append("{:.4f} {:.4f} {:.4f} RG".format(*col(s.stroke)))
                    ops.append(f"{s.width * MM_TO_PT:.3f} w 1 j 1 J")
                    if s.dash:
                        ops.append("[" + " ".join(f"{d * MM_TO_PT:.2f}" for d in s.dash) + "] 0 d")
                if it.kind == "circle":
                    ((cx, cy),) = it.points
                    r = it.radius
                    k = 0.5523 * r
                    pts = [
                        (cx + r, cy),
                        (cx + r, cy - k, cx + k, cy - r, cx, cy - r),
                        (cx - k, cy - r, cx - r, cy - k, cx - r, cy),
                        (cx - r, cy + k, cx - k, cy + r, cx, cy + r),
                        (cx + k, cy + r, cx + r, cy + k, cx + r, cy),
                    ]
                    x0, y0 = pt(*pts[0])
                    path = [f"{x0:.2f} {y0:.2f} m"]
                    for c in pts[1:]:
                        a = [pt(c[i], c[i + 1]) for i in (0, 2, 4)]
                        path.append(" ".join(f"{u:.2f} {v:.2f}" for u, v in a) + " c")
                    path.append("h")
                else:
                    x0, y0 = pt(*it.points[0])
                    path = [f"{x0:.2f} {y0:.2f} m"] + [f"{u:.2f} {v:.2f} l" for u, v in (pt(*p) for p in it.points[1:])]
                    if it.kind == "polygon":
                        path.append("h")
                ops.append(" ".join(path))
                closed = it.kind in ("polygon", "circle")
                if closed and s.fill and s.stroke:
                    ops.append("B")
                elif closed and s.fill:
                    ops.append("f")
                elif s.stroke:
                    ops.append("S")
                else:
                    ops.append("n")
            else:
                ((x, y),) = it.points
                size = s.font_size * MM_TO_PT
                w = text_width_mm(it.text, s.font_size, s.bold) * MM_TO_PT
                dx = {"start": 0.0, "middle": -w / 2, "end": -w}[s.anchor]
                dy = {"alphabetic": 0.0, "middle": -0.35 * size, "hanging": -0.75 * size}[s.baseline]
                px, py = pt(x, y)
                import math

                a = math.radians(-it.rotate)
                ca, sa = math.cos(a), math.sin(a)
                tx = px + dx * ca - dy * sa
                ty = py + dx * sa + dy * ca
                ops.append("{:.4f} {:.4f} {:.4f} rg".format(*col(s.fill or "#000000")))
                font = "F2" if s.bold else "F1"
                ops.append(
                    f"BT /{font} {size:.2f} Tf {ca:.5f} {sa:.5f} {-sa:.5f} {ca:.5f} {tx:.2f} {ty:.2f} Tm "
                    f"({_pdf_escape(it.text)}) Tj ET"
                )
            ops.append("Q")
        content = zlib.compress("\n".join(ops).encode("latin-1"))
        objs: list[bytes] = []
        objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
        objs.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
        gs_dict = " ".join(f"/{name} << /Type /ExtGState /CA {op} /ca {op} >>" for op, name in gstates.items())
        objs.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {W:.2f} {H:.2f}] /Contents 4 0 R "
                f"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> /ExtGState << {gs_dict} >> >> >>"
            ).encode()
        )
        objs.append(
            b"<< /Length "
            + str(len(content)).encode()
            + b" /Filter /FlateDecode >>\nstream\n"
            + content
            + b"\nendstream"
        )
        objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        info = f"<< /Title ({_pdf_escape(self.title)}) /Producer (AGSGeoModel) >>".encode("latin-1", "replace")
        objs.append(info)
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, o in enumerate(objs, start=1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += (
            f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info {len(objs)} 0 R >>\nstartxref\n{xref}\n%%EOF\n"
        ).encode()
        return bytes(out)

    def save_pdf(self, path) -> None:
        with open(path, "wb") as fh:
            fh.write(self.to_pdf())


def _pdf_escape(s: str) -> str:
    b = s.encode("cp1252", errors="replace")
    out = []
    for ch in b:
        c = chr(ch)
        if c in "()\\":
            out.append("\\" + c)
        elif ch < 32 or ch > 126:
            out.append(f"\\{ch:03o}")
        else:
            out.append(c)
    return "".join(out)
