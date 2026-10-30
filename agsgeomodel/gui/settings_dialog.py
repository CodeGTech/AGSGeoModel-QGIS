"""Settings dialog (Qt Designer form ``ui/settings_dialog.ui``).

Project settings (modelling, GeoReasoner, HDD) live in the project configuration and are recorded
with every run; reviewer and BGS-service settings are per user (``QgsSettings``)."""

from __future__ import annotations

from pathlib import Path

from qgis.gui import QgsFileWidget
from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox

from ..bgs.services import AGS_API, OGC_API
from ..core.config import ProjectSettings
from . import qgis_bridge as qb

FORM_CLASS, _ = uic.loadUiType(str(Path(__file__).parent / "ui" / "settings_dialog.ui"))

METHODS = [
    ("kriging", "Ordinary / universal kriging"),
    ("rbf", "Radial basis functions"),
    ("idw", "Inverse distance weighting"),
    ("tin", "Linear (TIN)"),
]
VARIOGRAMS = ["auto", "spherical", "exponential", "gaussian", "linear"]
DRIFTS = [("constant", "constant (ordinary kriging)"), ("linear", "linear (universal kriging)")]


def bgs_user_settings() -> dict:
    return {
        "cache_path": qb.user_setting("bgs/cache_path", "") or None,
        "ttl_days": float(qb.user_setting("bgs/ttl_days", 30)),
        "offline": str(qb.user_setting("bgs/offline", "false")).lower() == "true",
        "ogc_api": qb.user_setting("bgs/ogc_api", "") or None,
        "ags_api": qb.user_setting("bgs/ags_api", "") or None,
    }


class SettingsDialog(QDialog, FORM_CLASS):
    def __init__(
        self, settings: ProjectSettings, learned_model_path: str | None = None, train: bool = False, parent=None
    ):
        super().__init__(parent)
        self.setupUi(self)
        for key, label in METHODS:
            self.methodCombo.addItem(label, key)
        for v in VARIOGRAMS:
            self.variogramCombo.addItem(v, v)
        for key, label in DRIFTS:
            self.driftCombo.addItem(label, key)
        self.learnedFile.setFilter("Formation model (*.json)")
        self.cacheFile.setStorageMode(QgsFileWidget.StorageMode.SaveFile)
        self.cacheFile.setFilter("SQLite (*.sqlite)")
        self.buttonBox.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            lambda: self.load(ProjectSettings(), None, False)
        )
        self.load(settings, learned_model_path, train)
        self.reviewerEdit.setText(qb.reviewer_name())
        b = bgs_user_settings()
        self.cacheFile.setFilePath(b["cache_path"] or "")
        self.ttlSpin.setValue(int(b["ttl_days"]))
        self.offlineCheck.setChecked(b["offline"])
        self.ogcEdit.setText(b["ogc_api"] or OGC_API)
        self.agsEdit.setText(b["ags_api"] or AGS_API)
        self.hydroEdit.setText(qb.user_setting("bgs/hydro_wms", "") or "")

    def load(self, st: ProjectSettings, learned: str | None, train: bool) -> None:
        m, ip = st.model, st.model.interpolation
        self.methodCombo.setCurrentIndex(max(0, self.methodCombo.findData(ip.method)))
        self.variogramCombo.setCurrentIndex(max(0, self.variogramCombo.findData(ip.variogram_model)))
        self.neighboursSpin.setValue(ip.n_neighbours)
        self.driftCombo.setCurrentIndex(max(0, self.driftCombo.findData(ip.drift)))
        self.faultsCheck.setChecked(m.respect_faults)
        self.mapCheck.setChecked(m.use_map_constraints)
        self.primarySigmaSpin.setValue(m.primary_sigma_m)
        self.secondarySigmaSpin.setValue(m.secondary_sigma_m)
        self.mapToleranceSpin.setValue(m.map_tolerance_m)
        self.seedSpin.setValue(int(m.seed))
        r = st.reasoner
        self.reviewSpin.setValue(r.review_below_probability)
        self.neighbourSigmaSpin.setValue(r.neighbour_elevation_sigma_m)
        self.splitSpin.setValue(r.unknown_split_m)
        self.learnedFile.setFilePath(learned or "")
        self.trainCheck.setChecked(train)
        h = st.hdd
        self.spacingSpin.setValue(h.station_spacing_m)
        self.minCoverSpin.setValue(h.min_cover_m)
        self.mixedSpin.setValue(h.mixed_face_tolerance_m)
        self.lowCoverSpin.setValue(h.low_cover_ratio)
        self._base = st

    def project_settings(self) -> ProjectSettings:
        st = ProjectSettings.from_dict(self._base.to_dict())
        ip = st.model.interpolation
        ip.method = self.methodCombo.currentData()
        ip.variogram_model = self.variogramCombo.currentData()
        ip.n_neighbours = self.neighboursSpin.value()
        ip.drift = self.driftCombo.currentData()
        st.model.respect_faults = self.faultsCheck.isChecked()
        st.model.use_map_constraints = self.mapCheck.isChecked()
        st.model.primary_sigma_m = self.primarySigmaSpin.value()
        st.model.secondary_sigma_m = self.secondarySigmaSpin.value()
        st.model.map_tolerance_m = self.mapToleranceSpin.value()
        st.model.seed = self.seedSpin.value()
        st.reasoner.review_below_probability = self.reviewSpin.value()
        st.reasoner.neighbour_elevation_sigma_m = self.neighbourSigmaSpin.value()
        st.reasoner.unknown_split_m = self.splitSpin.value()
        st.hdd.station_spacing_m = self.spacingSpin.value()
        st.hdd.min_cover_m = self.minCoverSpin.value()
        st.hdd.mixed_face_tolerance_m = self.mixedSpin.value()
        st.hdd.low_cover_ratio = self.lowCoverSpin.value()
        return st

    @property
    def learned_model_path(self) -> str | None:
        return self.learnedFile.filePath().strip() or None

    @property
    def train(self) -> bool:
        return self.trainCheck.isChecked()

    def save_user_settings(self) -> None:
        qb.set_user_setting("reviewer", self.reviewerEdit.text().strip())
        qb.set_user_setting("bgs/cache_path", self.cacheFile.filePath().strip())
        qb.set_user_setting("bgs/ttl_days", self.ttlSpin.value())
        qb.set_user_setting("bgs/offline", "true" if self.offlineCheck.isChecked() else "false")
        ogc, ags = self.ogcEdit.text().strip(), self.agsEdit.text().strip()
        qb.set_user_setting("bgs/ogc_api", "" if ogc == OGC_API else ogc)
        qb.set_user_setting("bgs/ags_api", "" if ags == AGS_API else ags)
        qb.set_user_setting("bgs/hydro_wms", self.hydroEdit.text().strip())
