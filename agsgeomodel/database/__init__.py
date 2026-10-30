"""Persistence: GeoPackage (pure sqlite3), schema + migrations, GeoTIFF / 2DM writers, ProjectStore."""

from .gpkg import GeoPackage, decode_geometry, encode_geometry
from .raster import read_geotiff, write_2dm, write_geotiff
from .schema import MIGRATIONS, SCHEMA_VERSION, current_version, migrate
from .store import ProjectStore, StoredInterpretation, StoredLocation

__all__ = [
    "GeoPackage",
    "decode_geometry",
    "encode_geometry",
    "read_geotiff",
    "write_2dm",
    "write_geotiff",
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "current_version",
    "migrate",
    "ProjectStore",
    "StoredInterpretation",
    "StoredLocation",
]
