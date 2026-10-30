"""AGSGeoModel Processing provider (id ``agsgeomodel``)."""

from __future__ import annotations

from pathlib import Path

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .. import __version__
from .ags_algorithms import ImportAGSAlgorithm, ValidateAGSAlgorithm
from .ai_algorithms import ClusterDomainsAlgorithm, CorrelateHolesAlgorithm, TrainFormationModelAlgorithm
from .bgs_algorithms import BGSDiscoveryAlgorithm
from .engineering_algorithms import CPTInterpretationAlgorithm, HDDAssessmentAlgorithm
from .model_algorithms import BuildModelAlgorithm, CrossSectionsAlgorithm, ExportModelAlgorithm, RebuildModelAlgorithm

ALGORITHMS = [
    ValidateAGSAlgorithm,
    ImportAGSAlgorithm,
    BuildModelAlgorithm,
    RebuildModelAlgorithm,
    CrossSectionsAlgorithm,
    ExportModelAlgorithm,
    CPTInterpretationAlgorithm,
    HDDAssessmentAlgorithm,
    BGSDiscoveryAlgorithm,
    TrainFormationModelAlgorithm,
    CorrelateHolesAlgorithm,
    ClusterDomainsAlgorithm,
]


class AGSGeoModelProvider(QgsProcessingProvider):
    def id(self):
        return "agsgeomodel"

    def name(self):
        return "AGSGeoModel"

    def longName(self):
        return f"AGSGeoModel {__version__} - geological interpretation and modelling from AGS data"

    def icon(self):
        return QIcon(str(Path(__file__).resolve().parent.parent / "icons" / "agsgeomodel.svg"))

    def versionInfo(self):
        return __version__

    def loadAlgorithms(self):
        for cls in ALGORITHMS:
            self.addAlgorithm(cls())
