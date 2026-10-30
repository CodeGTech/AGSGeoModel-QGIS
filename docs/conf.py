"""Sphinx configuration (MyST Markdown); build with ``make docs``."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agsgeomodel import __version__  # noqa: E402

project = "AGSGeoModel"
author = "AGSGeoModel contributors"
copyright = "AGSGeoModel contributors, GPL-2.0-or-later"
version = release = __version__

extensions = ["myst_parser", "sphinx.ext.autodoc", "sphinx.ext.napoleon", "sphinx.ext.viewcode"]
source_suffix = {".md": "markdown"}
root_doc = "index"
exclude_patterns = ["_build", "diagrams/*.mmd", "Thumbs.db", ".DS_Store"]
myst_enable_extensions = ["colon_fence", "deflist", "dollarmath"]
myst_heading_anchors = 3  # links such as methods.md#13-correlation
suppress_warnings = ["myst.header"]

# The QGIS shell (plugin.py, gui/, algorithms/) is not documented through autodoc: it needs QGIS, and
# its behaviour is documented in the user guide and the generated Processing reference.
autodoc_mock_imports = ["qgis", "processing", "osgeo"]
autodoc_default_options = {"members": True, "undoc-members": False, "show-inheritance": True}
autodoc_typehints = "description"
autodoc_member_order = "bysource"
napoleon_google_docstring = True
napoleon_numpy_docstring = True

try:
    import sphinx_rtd_theme  # noqa: F401

    html_theme = "sphinx_rtd_theme"
except ImportError:  # pragma: no cover
    html_theme = "alabaster"
html_title = f"AGSGeoModel {__version__}"
html_static_path: list[str] = []
html_show_sourcelink = False
