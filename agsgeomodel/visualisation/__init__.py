"""Sections, fence diagrams, drawings (SVG/PDF) and colour conventions.

QGIS-specific styling lives in :mod:`agsgeomodel.visualisation.qgis_styles` and is imported only
inside QGIS.
"""

from .drawing import Drawing, Style
from .fence import FencePanel, auto_network, build_fence, fence_mesh, render_fence_2d
from .sections import SectionData, compute_section, render_section, section_to_features

__all__ = [
    "Drawing",
    "Style",
    "FencePanel",
    "auto_network",
    "build_fence",
    "fence_mesh",
    "render_fence_2d",
    "SectionData",
    "compute_section",
    "render_section",
    "section_to_features",
]
