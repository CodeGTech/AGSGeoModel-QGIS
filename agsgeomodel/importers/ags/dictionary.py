"""AGS4 data dictionary access.

A subset of the AGS4 Standard Dictionary (v4.1.1 and v4.2) covering the groups used for modelling
is bundled (``data/ags4_core_dict_*.json``; source: AGS Data Format, distributed with the
LGPL-3.0 ``python-ags4`` library). If ``python-ags4`` is installed its complete dictionaries are
used instead, so rules 7 and 9 are checked for every group.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).with_name("data")


@dataclass(frozen=True)
class HeadingDef:
    name: str
    status: str  # KEY | REQUIRED | KEY+REQUIRED | OTHER
    type: str
    unit: str
    description: str

    @property
    def is_key(self) -> bool:
        return "KEY" in self.status

    @property
    def is_required(self) -> bool:
        return "REQUIRED" in self.status


@dataclass
class GroupDef:
    name: str
    description: str = ""
    parent: str = ""
    headings: list[HeadingDef] = field(default_factory=list)

    def heading(self, name: str) -> HeadingDef | None:
        for h in self.headings:
            if h.name == name:
                return h
        return None

    @property
    def key_fields(self) -> list[str]:
        return [h.name for h in self.headings if h.is_key]

    @property
    def required_fields(self) -> list[str]:
        return [h.name for h in self.headings if h.is_required]

    @property
    def order(self) -> list[str]:
        return [h.name for h in self.headings]


@dataclass
class AGSDictionary:
    version: str
    groups: dict[str, GroupDef]
    complete: bool  # True when loaded from the full standard dictionary

    def get(self, group: str) -> GroupDef | None:
        return self.groups.get(group)

    def __contains__(self, group: str) -> bool:
        return group in self.groups


def _from_rows(groups_raw: dict) -> dict[str, GroupDef]:
    out: dict[str, GroupDef] = {}
    for g, d in groups_raw.items():
        gd = GroupDef(g, d.get("desc") or "", d.get("parent") or "")
        for h in d.get("h", []):
            gd.headings.append(HeadingDef(h[0], h[1] or "OTHER", h[2], h[3], h[4]))
        out[g] = gd
    return out


def _parse_dictionary_file(path: Path) -> dict[str, GroupDef]:
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8", errors="replace"))))
    hdr = None
    groups: dict[str, dict] = {}
    for r in rows:
        if not r:
            continue
        if r[0] == "HEADING":
            hdr = r
            continue
        if r[0] == "DATA" and hdr:
            d = dict(zip(hdr, r))
            if d.get("DICT_TYPE") == "GROUP":
                g = groups.setdefault(d["DICT_GRP"], {"h": []})
                g["desc"], g["parent"] = d.get("DICT_DESC", ""), d.get("DICT_PGRP", "")
            elif d.get("DICT_TYPE") == "HEADING":
                groups.setdefault(d["DICT_GRP"], {"h": []})["h"].append(
                    [d["DICT_HDNG"], d["DICT_STAT"], d["DICT_DTYP"], d["DICT_UNIT"], d["DICT_DESC"]]
                )
    return _from_rows(groups)


@lru_cache(maxsize=8)
def load_dictionary(version: str = "4.1.1") -> AGSDictionary:
    """Return the best available dictionary for an AGS4 edition string such as ``'4.1.1'``."""
    v = (version or "").strip()
    norm = (
        "4.2"
        if v.startswith("4.2")
        else "4.1.1"
        if v.startswith("4.1")
        else "4.0.4"
        if v.startswith("4.0")
        else "4.1.1"
    )
    try:  # full dictionary from python-ags4 (optional)
        import python_ags4  # type: ignore

        fname = {
            "4.2": "Standard_dictionary_v4_2.ags",
            "4.1.1": "Standard_dictionary_v4_1_1.ags",
            "4.0.4": "Standard_dictionary_v4_0_4.ags",
        }[norm]
        p = Path(python_ags4.__file__).with_name(fname)
        if p.exists():
            return AGSDictionary(norm, _parse_dictionary_file(p), complete=True)
    except Exception:
        pass
    bundled = "ags4_core_dict_4_2.json" if norm == "4.2" else "ags4_core_dict_4_1_1.json"
    data = json.loads((_DATA / bundled).read_text(encoding="utf-8"))
    return AGSDictionary(norm, _from_rows(data["groups"]), complete=False)
