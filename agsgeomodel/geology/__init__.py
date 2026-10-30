"""Geological reasoning: knowledge base, description parsing, evidence, order-constrained
sequence inference, validation, contact typing and ground domains."""

from .classifier import Evidence, FormationClassifier
from .contacts import (
    Observation,
    ObservationSet,
    UnitRun,
    bedrock_stack,
    extract_observations,
    isochores,
    stack_surfaces,
)
from .description import ParsedDescription, parse_description
from .domains import DOMAINS, domain_grid, location_domain
from .knowledge import KnowledgeBase, StratColumn, default_kb, load_column, load_kb
from .reasoner import GeoReasoner, LocationInterpretation, ProjectInterpretation, proposal_key
from .sequence import SequenceResult, infer_sequence

__all__ = [
    "Evidence",
    "FormationClassifier",
    "Observation",
    "ObservationSet",
    "UnitRun",
    "bedrock_stack",
    "extract_observations",
    "isochores",
    "stack_surfaces",
    "ParsedDescription",
    "parse_description",
    "DOMAINS",
    "domain_grid",
    "location_domain",
    "KnowledgeBase",
    "StratColumn",
    "default_kb",
    "load_column",
    "load_kb",
    "GeoReasoner",
    "proposal_key",
    "LocationInterpretation",
    "ProjectInterpretation",
    "SequenceResult",
    "infer_sequence",
]
