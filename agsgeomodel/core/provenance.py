"""Provenance and reproducibility helpers.

A model run is identified by the hash of everything that determines it: input file hashes,
knowledge-base version, configuration and random seed. Re-running with the same inputs gives
the same ``input_hash`` and, because every stochastic step is seeded, the same outputs.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import platform
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _default(o: Any):
    if isinstance(o, np.ndarray):
        return {"__ndarray__": sha256_bytes(np.ascontiguousarray(o).tobytes()), "shape": o.shape}
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if hasattr(o, "to_dict"):
        return o.to_dict()
    if hasattr(o, "__dataclass_fields__"):
        return asdict(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"not hashable for provenance: {type(o)!r}")


def stable_hash(obj: Any) -> str:
    """Order-independent SHA-256 of a JSON-able object (dict keys sorted)."""
    return sha256_bytes(json.dumps(obj, sort_keys=True, default=_default, separators=(",", ":")).encode())


def utc_now() -> str:
    return _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class RunRecord:
    """Everything needed to explain and reproduce one modelling run."""

    kind: str  # e.g. "stratigraphic_model", "hdd_assessment"
    input_hash: str
    config: dict[str, Any]
    kb_version: str
    seed: int | None = None
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created: str = field(default_factory=utc_now)
    software: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.software:
            from .. import __version__

            self.software = {
                "agsgeomodel": __version__,
                "python": platform.python_version(),
                "numpy": np.__version__,
            }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
