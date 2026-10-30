"""Explainable machine learning: trainable formation evidence, description similarity, domain clustering.

Every model here *proposes* evidence; the order-constrained GeoReasoner decides, so no learned model
can produce a stratigraphically impossible interpretation.
"""

from .clustering import ClusterResult, cluster_domains, hole_features
from .features import FeatureSpace, interval_features
from .formation_model import BACKENDS, TrainedFormationModel, training_set
from .similarity import EmbeddingSimilarity, TfidfSimilarity, get_similarity

__all__ = [
    "ClusterResult",
    "cluster_domains",
    "hole_features",
    "FeatureSpace",
    "interval_features",
    "BACKENDS",
    "TrainedFormationModel",
    "training_set",
    "EmbeddingSimilarity",
    "TfidfSimilarity",
    "get_similarity",
]
