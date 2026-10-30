"""British Geological Survey integration: OGC API, AGS API, Lexicon, WMS context and discovery."""

from .connector import AreaAnalysis, BGSGeoConnector, buffer_polygon, parse_lexicon_turtle
from .discovery import BGSCandidate, candidates_from_records, merge_bgs_ags, precision_m, rank_candidates
from .http import HttpCache, set_fetcher
from .services import ATTRIBUTION, COLLECTIONS, VERIFIED, WMS_LAYERS

__all__ = [
    "AreaAnalysis",
    "BGSGeoConnector",
    "buffer_polygon",
    "parse_lexicon_turtle",
    "BGSCandidate",
    "candidates_from_records",
    "merge_bgs_ags",
    "precision_m",
    "rank_candidates",
    "HttpCache",
    "set_fetcher",
    "ATTRIBUTION",
    "COLLECTIONS",
    "VERIFIED",
    "WMS_LAYERS",
]
