"""Ground domain detection (artificial, alluvial, glacial, marine, slope, aeolian, bedrock)."""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from .contacts import UnitRun
from .knowledge.kb import StratColumn

DOMAINS = ["artificial", "alluvial", "glacial", "marine", "slope", "aeolian", "bedrock"]
DOMAIN_CODES = {d: i for i, d in enumerate(DOMAINS)}


def location_domain(
    runs: list[UnitRun],
    column: StratColumn,
    ground_level: float | None = None,
    artificial_min_m: float = 1.5,
    cover_min_m: float = 1.0,
) -> tuple[str, dict[str, float]]:
    """Dominant engineering-geological domain of a hole, with the thickness by domain as evidence.

    * Artificial Ground Domain where made ground is at least ``artificial_min_m`` thick;
    * Bedrock Domain where natural cover is thinner than ``cover_min_m``;
    * otherwise the domain with the greatest natural cover thickness (alluvial, glacial, slope,
      aeolian). Marine is assigned where the collar is below datum (offshore / intertidal data).
    """
    kb = column.kb
    thk: dict[str, float] = defaultdict(float)
    for r in runs:
        u = kb.get(r.unit)
        if u is None:
            continue
        if u.unit_class == "bedrock":
            continue
        if u.code == "TOPSOIL":
            continue
        thk[u.domain] += r.thickness
    if thk.get("artificial", 0.0) >= artificial_min_m:
        return "artificial", dict(thk)
    natural = {d: v for d, v in thk.items() if d != "artificial"}
    if ground_level is not None and ground_level < 0 and sum(natural.values()) > 0:
        return "marine", dict(thk)
    if sum(natural.values()) < cover_min_m:
        return "bedrock", dict(thk)
    return max(natural, key=natural.get), dict(thk)


def domain_grid(
    thickness_grids: dict[str, np.ndarray],
    column: StratColumn,
    artificial_min_m: float = 1.5,
    cover_min_m: float = 1.0,
    ground: np.ndarray | None = None,
) -> np.ndarray:
    """Classify every model grid node into a domain from modelled cover thickness grids (``thk:<code>``)."""
    kb = column.kb
    shape = next(iter(thickness_grids.values())).shape
    by_dom: dict[str, np.ndarray] = defaultdict(lambda: np.zeros(shape))
    for key, g in thickness_grids.items():
        code = key.split(":", 1)[1] if ":" in key else key
        u = kb.get(code)
        if u is None or code == "TOPSOIL":
            continue
        by_dom[u.domain] = by_dom[u.domain] + np.nan_to_num(g)
    out = np.full(shape, DOMAIN_CODES["bedrock"], dtype=int)
    natural = [d for d in by_dom if d != "artificial"]
    if natural:
        stack = np.stack([by_dom[d] for d in natural])
        best = np.argmax(stack, axis=0)
        total = stack.sum(axis=0)
        codes = np.array([DOMAIN_CODES.get(d, DOMAIN_CODES["bedrock"]) for d in natural])
        out = np.where(total >= cover_min_m, codes[best], out)
        if ground is not None:
            out = np.where((total >= cover_min_m) & (ground < 0), DOMAIN_CODES["marine"], out)
    if "artificial" in by_dom:
        out = np.where(by_dom["artificial"] >= artificial_min_m, DOMAIN_CODES["artificial"], out)
    return out
