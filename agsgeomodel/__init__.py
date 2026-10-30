"""AGSGeoModel — defensible geological interpretation and ground modelling from AGS data.

The package is deliberately split in two layers:

* a **headless core** (everything except :mod:`agsgeomodel.gui`, :mod:`agsgeomodel.algorithms`
  and :mod:`agsgeomodel.plugin`) that depends only on NumPy and the standard library, can be
  used from Jupyter, CI or a server, and is unit tested without QGIS; and
* a **thin QGIS shell** that wraps the core in a toolbar, dock, import wizard and a
  Processing provider so that every GUI action is also a reproducible Processing algorithm.

``classFactory`` is the QGIS entry point; importing :mod:`agsgeomodel` itself never imports
``qgis`` so the library stays usable outside QGIS.
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__", "classFactory"]


def classFactory(iface):  # noqa: N802 - name required by QGIS
    """QGIS plugin entry point (called by QGIS with the ``QgisInterface``)."""
    from .plugin import AGSGeoModelPlugin

    return AGSGeoModelPlugin(iface)
