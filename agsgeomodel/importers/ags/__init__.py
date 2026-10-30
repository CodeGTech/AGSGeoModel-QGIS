"""AGS4 / AGS3 import engine: reading, validation, CRS detection and mapping to the data model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .crs import CRSGuess, DatumGuess, detect_crs, detect_vertical_datum
from .dictionary import AGSDictionary, load_dictionary
from .mapper import ImportResult, ags_to_project, classify_location_type
from .reader import AGSFile, AGSTable, ParseIssue, parse_float, read_ags, write_ags4
from .validator import ValidationReport, run_official_checker, validate_ags


@dataclass
class AGSImport:
    ags: AGSFile
    validation: ValidationReport
    result: ImportResult

    @property
    def project(self):
        return self.result.project


def import_ags(path: str | Path, target_crs: str | None = None, crs_override: str | None = None) -> AGSImport:
    """One-call import: read -> validate -> map. Never raises on validation findings."""
    ags = read_ags(path)
    rep = validate_ags(ags, base_dir=Path(path).parent if Path(str(path)).exists() else None)
    res = ags_to_project(ags, target_crs=target_crs, crs_override=crs_override)
    rep.log.extend(res.issues)
    return AGSImport(ags, rep, res)


__all__ = [
    "AGSDictionary",
    "AGSFile",
    "AGSImport",
    "AGSTable",
    "CRSGuess",
    "DatumGuess",
    "ImportResult",
    "ParseIssue",
    "ValidationReport",
    "ags_to_project",
    "classify_location_type",
    "detect_crs",
    "detect_vertical_datum",
    "import_ags",
    "load_dictionary",
    "parse_float",
    "read_ags",
    "run_official_checker",
    "validate_ags",
    "write_ags4",
]
