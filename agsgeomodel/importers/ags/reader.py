"""Low-level AGS reader and writer (AGS4; AGS3 is handled by :mod:`.ags3`).

The reader never silently "fixes" a file. Structural problems are recorded as
:class:`ParseIssue` objects (rule numbers refer to the AGS4 rules) and parsing continues where
it is safe to do so, because a partially non-compliant file is still valuable evidence.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ...core.errors import AGSParseError
from ...core.provenance import sha256_bytes

DESCRIPTORS = ("GROUP", "HEADING", "UNIT", "TYPE", "DATA")
_QUOTED_LINE = re.compile(r'^"(?:[^"]|"")*"(?:,"(?:[^"]|"")*")*$')
_NUM = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


@dataclass
class ParseIssue:
    rule: str
    severity: str  # info | warning | error | critical
    message: str
    line: int | None = None
    group: str | None = None
    heading: str | None = None


@dataclass
class AGSTable:
    name: str
    headings: list[str] = field(default_factory=list)
    units: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    line_numbers: list[int] = field(default_factory=list)
    group_line: int | None = None

    def __len__(self) -> int:
        return len(self.rows)

    def index(self, heading: str) -> int:
        try:
            return self.headings.index(heading)
        except ValueError:
            return -1

    def has(self, heading: str) -> bool:
        return heading in self.headings

    def column(self, heading: str, default: str = "") -> list[str]:
        k = self.index(heading)
        if k < 0:
            return [default] * len(self.rows)
        return [r[k] if k < len(r) else default for r in self.rows]

    def float_column(self, heading: str) -> np.ndarray:
        return np.array([parse_float(v) for v in self.column(heading)], dtype=float)

    def unit_of(self, heading: str) -> str:
        k = self.index(heading)
        return self.units[k] if 0 <= k < len(self.units) else ""

    def type_of(self, heading: str) -> str:
        k = self.index(heading)
        return self.types[k] if 0 <= k < len(self.types) else ""

    def records(self) -> list[dict[str, str]]:
        return [dict(zip(self.headings, r)) for r in self.rows]

    def to_dataframe(self):  # pragma: no cover - optional dependency
        from ...core.optional import require

        pd = require("pandas", "DataFrame export")
        return pd.DataFrame(self.rows, columns=self.headings)


@dataclass
class AGSFile:
    tables: dict[str, AGSTable]
    format: str = "AGS4"  # AGS4 | AGS3 | AGS3->AGS4
    version: str = "unknown"
    encoding: str = "utf-8"
    line_endings: str = "CRLF"
    non_ascii: bool = False
    issues: list[ParseIssue] = field(default_factory=list)
    source_name: str = ""
    sha256: str = ""
    n_lines: int = 0

    def __contains__(self, name: str) -> bool:
        return name in self.tables

    def __getitem__(self, name: str) -> AGSTable:
        return self.tables[name]

    def get(self, name: str) -> AGSTable | None:
        return self.tables.get(name)

    @property
    def groups(self) -> list[str]:
        return list(self.tables)

    def tran_value(self, heading: str, default: str = "") -> str:
        t = self.tables.get("TRAN")
        if not t or not t.rows:
            return default
        k = t.index(heading)
        return t.rows[0][k] if k >= 0 and t.rows[0][k] != "" else default

    @property
    def delimiter(self) -> str:
        return self.tran_value("TRAN_DLIM", "+")

    @property
    def concatenator(self) -> str:
        return self.tran_value("TRAN_RCON", "+")


# ============================================================================ helpers
def parse_float(value: Any) -> float:
    """Parse an AGS numeric field. Empty / non-numeric (e.g. '<0.5', 'NR') -> NaN."""
    if value is None:
        return np.nan
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s or not _NUM.match(s):
        return np.nan
    return float(s)


def decode_bytes(data: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc), enc.replace("-sig", "")
        except UnicodeDecodeError:
            continue
    raise AGSParseError("could not decode file")  # pragma: no cover - latin-1 always succeeds


def _line_endings(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    if crlf and not lf:
        return "CRLF"
    if lf and not crlf:
        return "LF"
    return "mixed" if crlf and lf else "none"


def _split_csv(line: str) -> list[str]:
    return next(csv.reader([line], delimiter=",", quotechar='"', doublequote=True, skipinitialspace=False))


def detect_format(text: str) -> str:
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith(('"**', "**")):
            return "AGS3"
        if s.startswith(('"GROUP"', "GROUP,")):
            return "AGS4"
        return "unknown"
    return "unknown"


# ============================================================================ reading
def read_ags(source: str | Path | bytes | io.IOBase, name: str | None = None) -> AGSFile:
    """Read an AGS4 or AGS3 file. AGS3 files are converted to AGS4 group/heading names."""
    if isinstance(source, (str, Path)) and Path(str(source)).exists():
        data = Path(source).read_bytes()
        name = name or Path(source).name
    elif isinstance(source, bytes):
        data = source
    elif hasattr(source, "read"):
        raw = source.read()
        data = raw.encode("utf-8") if isinstance(raw, str) else raw
    elif isinstance(source, str):
        data = source.encode("utf-8")
    else:  # pragma: no cover
        raise AGSParseError(f"unsupported source type {type(source)!r}")
    text, enc = decode_bytes(data)
    fmt = detect_format(text)
    if fmt == "AGS3":
        from .ags3 import convert_ags3_to_ags4, read_ags3_text

        ags3 = read_ags3_text(text)
        ags3.encoding, ags3.sha256, ags3.source_name = enc, sha256_bytes(data), name or ""
        return convert_ags3_to_ags4(ags3)
    if fmt != "AGS4":
        raise AGSParseError('not an AGS file: first data line must start with "GROUP" (AGS4) or "**" (AGS3)')
    ags = read_ags4_text(text)
    ags.encoding = enc
    ags.sha256 = sha256_bytes(data)
    ags.source_name = name or ""
    return ags


def read_ags4_text(text: str) -> AGSFile:
    tables: dict[str, AGSTable] = {}
    issues: list[ParseIssue] = []
    current: AGSTable | None = None
    seen_after_group: list[str] = []
    lines = text.splitlines()
    non_ascii = any(ord(c) > 127 for c in text)
    if non_ascii:
        issues.append(ParseIssue("AGS4-1", "warning", "file contains non-ASCII characters"))
    for ln, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        line = raw.rstrip("\r")
        if not _QUOTED_LINE.match(line.strip()):
            issues.append(
                ParseIssue(
                    "AGS4-5",
                    "error",
                    "fields are not all enclosed in double quotes or are not separated by commas",
                    ln,
                    current.name if current else None,
                )
            )
        try:
            fields = _split_csv(line.strip())
        except csv.Error as exc:
            issues.append(ParseIssue("AGS4-4", "error", f"unparseable line: {exc}", ln))
            continue
        if not fields:
            continue
        desc = fields[0]
        if desc not in DESCRIPTORS:
            issues.append(
                ParseIssue(
                    "AGS4-3",
                    "error",
                    f"line starts with unknown data descriptor {desc!r}",
                    ln,
                    current.name if current else None,
                )
            )
            continue
        if desc == "GROUP":
            gname = fields[1] if len(fields) > 1 else ""
            if not re.fullmatch(r"[A-Z0-9]{4}", gname):
                issues.append(ParseIssue("AGS4-19", "error", f"invalid group name {gname!r}", ln, gname))
            if gname in tables:
                issues.append(
                    ParseIssue(
                        "AGS4-structure",
                        "error",
                        f"group {gname} appears more than once; rows will be appended if headings match",
                        ln,
                        gname,
                    )
                )
                current = tables[gname]
                current._dup = True  # type: ignore[attr-defined]
            else:
                current = AGSTable(gname, group_line=ln)
                tables[gname] = current
            seen_after_group = []
            continue
        if current is None:
            issues.append(ParseIssue("AGS4-2b", "error", f"{desc} row before any GROUP row", ln))
            continue
        seen_after_group.append(desc)
        if desc == "HEADING":
            heads = fields[1:]
            if getattr(current, "_dup", False) and current.headings:
                if heads != current.headings:
                    issues.append(
                        ParseIssue(
                            "AGS4-structure",
                            "error",
                            "repeated group has different headings; second block ignored",
                            ln,
                            current.name,
                        )
                    )
                    current = AGSTable(current.name + "_DUP")
                continue
            dup = {h for h in heads if heads.count(h) > 1}
            if dup:
                issues.append(ParseIssue("AGS4-7", "error", f"duplicate headings {sorted(dup)}", ln, current.name))
                seen: dict[str, int] = {}
                renamed = []
                for h in heads:
                    seen[h] = seen.get(h, 0) + 1
                    renamed.append(h if seen[h] == 1 else f"{h}_{seen[h]}")
                heads = renamed
            current.headings = heads
        elif desc in ("UNIT", "TYPE"):
            vals = fields[1:]
            if current.headings and len(vals) != len(current.headings):
                issues.append(
                    ParseIssue(
                        "AGS4-4",
                        "error",
                        f"{desc} row has {len(vals)} fields, HEADING has {len(current.headings)}",
                        ln,
                        current.name,
                    )
                )
                vals = (vals + [""] * len(current.headings))[: len(current.headings)]
            if getattr(current, "_dup", False) and (current.units if desc == "UNIT" else current.types):
                continue
            if desc == "UNIT":
                current.units = vals
            else:
                current.types = vals
        else:  # DATA
            if not current.headings:
                issues.append(ParseIssue("AGS4-4", "error", "DATA row before HEADING row", ln, current.name))
                continue
            vals = fields[1:]
            if len(vals) != len(current.headings):
                issues.append(
                    ParseIssue(
                        "AGS4-4",
                        "error",
                        f"DATA row has {len(vals)} fields, HEADING has {len(current.headings)}",
                        ln,
                        current.name,
                    )
                )
                vals = (vals + [""] * len(current.headings))[: len(current.headings)]
            current.rows.append(vals)
            current.line_numbers.append(ln)
    for t in tables.values():
        if not t.units:
            issues.append(ParseIssue("AGS4-2b", "error", "group has no UNIT row", t.group_line, t.name))
            t.units = [""] * len(t.headings)
        if not t.types:
            issues.append(ParseIssue("AGS4-2b", "error", "group has no TYPE row", t.group_line, t.name))
            t.types = [""] * len(t.headings)
        if hasattr(t, "_dup"):
            delattr(t, "_dup")
    ags = AGSFile(
        tables=tables,
        format="AGS4",
        issues=issues,
        n_lines=len(lines),
        line_endings=_line_endings(text),
        non_ascii=non_ascii,
    )
    ags.version = ags.tran_value("TRAN_AGS", "unknown") or "unknown"
    return ags


# ============================================================================ writing
def _q(value: Any) -> str:
    s = "" if value is None else str(value)
    if isinstance(value, float) and np.isnan(value):
        s = ""
    return '"' + s.replace('"', '""') + '"'


def write_ags4(tables: Iterable[AGSTable], path: str | Path | None = None) -> str:
    """Serialise tables to AGS4 text (CRLF line endings, all fields quoted)."""
    out: list[str] = []
    for t in tables:
        out.append(",".join([_q("GROUP"), _q(t.name)]))
        out.append(",".join([_q("HEADING")] + [_q(h) for h in t.headings]))
        out.append(",".join([_q("UNIT")] + [_q(u) for u in (t.units or [""] * len(t.headings))]))
        out.append(",".join([_q("TYPE")] + [_q(u) for u in (t.types or [""] * len(t.headings))]))
        for r in t.rows:
            out.append(",".join([_q("DATA")] + [_q(v) for v in r]))
        out.append("")
    text = "\r\n".join(out) + "\r\n"
    if path is not None:
        Path(path).write_bytes(text.encode("ascii", errors="replace"))
    return text
