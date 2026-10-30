"""Exception hierarchy. Every error raised deliberately by the package derives from
:class:`AGSGeoModelError` so callers (GUI, Processing, CLI) can catch one type."""

from __future__ import annotations


class AGSGeoModelError(Exception):
    """Base class for all package errors."""


class AGSParseError(AGSGeoModelError):
    """An AGS file could not be read at all (as opposed to validation findings)."""

    def __init__(self, message: str, line: int | None = None):
        super().__init__(f"line {line}: {message}" if line else message)
        self.line = line


class ConfigError(AGSGeoModelError):
    """Invalid configuration or user input."""


class KnowledgeBaseError(AGSGeoModelError):
    """The stratigraphic knowledge base is inconsistent (e.g. cyclic order, unknown code)."""


class GeologyError(AGSGeoModelError):
    """A geological operation cannot be performed defensibly with the data supplied."""


class ModelError(AGSGeoModelError):
    """Interpolation / model construction failure."""


class ConnectorError(AGSGeoModelError):
    """External data service failure (BGS, HTTP, cache)."""


class StoreError(AGSGeoModelError):
    """GeoPackage / database failure."""


class DependencyMissing(AGSGeoModelError):
    """An optional dependency needed for a feature is not installed."""

    def __init__(self, package: str, feature: str):
        hint = (
            f"The optional package '{package}' is required for {feature}. Install it into the "
            f"Python used by QGIS, e.g. from the OSGeo4W Shell: python -m pip install {package}"
        )
        super().__init__(hint)
        self.package = package
        self.feature = feature
