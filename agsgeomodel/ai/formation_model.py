"""Trainable formation classifier ("ML proposes, the GeoReasoner disposes").

Backends: a dependency-free multinomial logistic regression (NumPy, L2, full-batch Adam on the
convex log-loss), scikit-learn ``LogisticRegression``, LightGBM or XGBoost. Whatever the backend:

* training labels come from reviewed data (manual overrides) and high-confidence GeoReasoner
  interpretations — never from unvalidated output;
* the model only supplies *evidence* (log-probabilities) to the order-constrained sequence model,
  so it cannot produce a stratigraphically impossible interpretation;
* models are serialised to text/JSON (no pickle), with a model card (data hash, class balance,
  cross-validated accuracy, calibration, top features per class);
* :meth:`explain` returns per-feature contributions for a prediction (coefficients x values for
  linear models, SHAP-style ``pred_contrib`` for LightGBM / XGBoost).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..core.errors import DependencyMissing, ModelError
from ..core.optional import optional_import
from ..core.provenance import stable_hash, utc_now
from .features import FeatureSpace, interval_features

BACKENDS = ("numpy", "sklearn", "lightgbm", "xgboost")


def _softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


class _NumpyLogReg:
    def __init__(self, l2: float = 1.0, iters: int = 400, lr: float = 0.5, seed: int = 0):
        self.l2, self.iters, self.lr, self.seed = l2, iters, lr, seed

    def fit(self, X: np.ndarray, y: np.ndarray, n_classes: int) -> _NumpyLogReg:
        n, d = X.shape
        self.mu = X.mean(axis=0)
        self.sd = X.std(axis=0) + 1e-9
        Xs = (X - self.mu) / self.sd
        W = np.zeros((d, n_classes))
        b = np.zeros(n_classes)
        Y = np.eye(n_classes)[y]
        m_w, v_w, m_b, v_b = np.zeros_like(W), np.zeros_like(W), np.zeros_like(b), np.zeros_like(b)
        for t in range(1, self.iters + 1):  # Adam on the convex log-loss
            P = _softmax(Xs @ W + b)
            G = (P - Y) / n
            gW = Xs.T @ G + self.l2 * W / n
            gb = G.sum(axis=0)
            for g, m, v, prm in ((gW, m_w, v_w, W), (gb, m_b, v_b, b)):
                m *= 0.9
                m += 0.1 * g
                v *= 0.999
                v += 0.001 * g * g
                prm -= self.lr * (m / (1 - 0.9**t)) / (np.sqrt(v / (1 - 0.999**t)) + 1e-8) * 0.05
        self.W, self.b = W, b
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return _softmax(((X - self.mu) / self.sd) @ self.W + self.b)

    def contributions(self, x: np.ndarray) -> np.ndarray:
        """(n_features, n_classes) contribution of each standardised feature to each class logit."""
        return (((x - self.mu) / self.sd)[:, None]) * self.W

    def to_dict(self) -> dict:
        return {
            "W": self.W.tolist(),
            "b": self.b.tolist(),
            "mu": self.mu.tolist(),
            "sd": self.sd.tolist(),
            "l2": self.l2,
        }

    @classmethod
    def from_dict(cls, d: dict) -> _NumpyLogReg:
        m = cls(d.get("l2", 1.0))
        m.W, m.b, m.mu, m.sd = (np.array(d[k]) for k in ("W", "b", "mu", "sd"))
        return m


@dataclass
class TrainedFormationModel:
    classes: list[str]
    space: FeatureSpace
    backend: str = "numpy"
    model: object = None
    card: dict = field(default_factory=dict)
    use_depth: bool = True
    temperature: float = 1.0  # calibration of log-probabilities when used as evidence

    # ------------------------------------------------------------------ training
    @classmethod
    def train(
        cls,
        intervals,
        project=None,
        backend: str = "auto",
        use_depth: bool = True,
        ngram: int = 0,
        seed: int = 0,
        cv_folds: int = 5,
        min_per_class: int = 3,
    ) -> TrainedFormationModel:
        labelled = [iv for iv in intervals if iv.final_unit]
        if not labelled:
            raise ModelError("no labelled intervals to train on")
        gl = {lid: loc.ground_level for lid, loc in (project.locations.items() if project else [])}
        feats = [interval_features(iv, gl.get(iv.location_id), use_depth) for iv in labelled]
        texts = [iv.description for iv in labelled]
        y_lab = [iv.final_unit for iv in labelled]
        counts = {c: y_lab.count(c) for c in set(y_lab)}
        classes = sorted(c for c, n in counts.items() if n >= min_per_class)
        keep = [i for i, c in enumerate(y_lab) if c in classes]
        if len(classes) < 2:
            raise ModelError("need at least two classes with enough examples")
        feats = [feats[i] for i in keep]
        texts = [texts[i] for i in keep]
        y = np.array([classes.index(y_lab[i]) for i in keep])
        space = FeatureSpace(ngram=ngram).fit(feats, texts)
        X = space.transform(feats, texts)
        if backend == "auto":
            backend = (
                "lightgbm" if optional_import("lightgbm") else "sklearn" if optional_import("sklearn") else "numpy"
            )
        obj = cls(classes, space, backend, None, {}, use_depth)
        rng = np.random.default_rng(seed)
        folds = np.arange(len(y)) % cv_folds
        rng.shuffle(folds)
        oof = np.zeros((len(y), len(classes)))
        for k in range(cv_folds):
            tr, te = folds != k, folds == k
            if te.sum() == 0 or len(np.unique(y[tr])) < 2:
                continue
            m = obj._fit_backend(X[tr], y[tr], len(classes), seed)
            oof[te] = obj._proba(m, X[te], len(classes))
        acc = float((oof.argmax(axis=1) == y).mean())
        brier = float(np.mean(np.sum((oof - np.eye(len(classes))[y]) ** 2, axis=1)))
        # temperature calibration on out-of-fold probabilities (1-D grid search on log-loss)
        best_t, best_ll = 1.0, np.inf
        logp = np.log(np.clip(oof, 1e-9, 1))
        for t in np.linspace(1.0, 4.0, 31):  # calibration may soften but never sharpen (conservative)
            pt = _softmax(logp / t)
            ll = float(-np.mean(np.log(np.clip(pt[np.arange(len(y)), y], 1e-12, 1))))
            if ll < best_ll:
                best_t, best_ll = t, ll
        obj.temperature = float(best_t)
        obj.model = obj._fit_backend(X, y, len(classes), seed)
        conf = np.zeros((len(classes), len(classes)), dtype=int)
        for a, b in zip(y, oof.argmax(axis=1)):
            conf[a, b] += 1
        obj.card = {
            "created": utc_now(),
            "backend": backend,
            "n_samples": int(len(y)),
            "classes": classes,
            "class_counts": {c: int((y == i).sum()) for i, c in enumerate(classes)},
            "features": len(space.names),
            "uses_depth": use_depth,
            "ngram": ngram,
            "cv_folds": cv_folds,
            "cv_accuracy": round(acc, 4),
            "cv_brier": round(brier, 4),
            "calibration_temperature": round(best_t, 3),
            "calibrated_log_loss": round(best_ll, 4),
            "confusion_matrix": conf.tolist(),
            "training_data_hash": stable_hash(
                [(iv.location_id, iv.top, iv.base, iv.description, iv.final_unit) for iv in labelled]
            ),
            "intended_use": "evidence for the order-constrained GeoReasoner; not a standalone classifier",
            "limitations": "trained on this project's (or supplied) logs; performance outside the training "
            "province and logging style is unknown",
            "top_features": obj.top_features(8),
        }
        return obj

    def _fit_backend(self, X, y, n_classes, seed):
        if self.backend == "numpy":
            return _NumpyLogReg(seed=seed).fit(X, y, n_classes)
        if self.backend == "sklearn":
            sk = optional_import("sklearn.linear_model")
            if sk is None:
                raise DependencyMissing("scikit-learn", "the sklearn formation model")
            return sk.LogisticRegression(C=1.0, max_iter=2000, random_state=seed).fit(X, y)
        if self.backend == "lightgbm":
            lgb = optional_import("lightgbm")
            if lgb is None:
                raise DependencyMissing("lightgbm", "the LightGBM formation model")
            params = {
                "objective": "multiclass",
                "num_class": n_classes,
                "learning_rate": 0.1,
                "num_leaves": 15,
                "min_data_in_leaf": 3,
                "verbose": -1,
                "seed": seed,
                "deterministic": True,
            }
            return lgb.train(params, lgb.Dataset(X, y), num_boost_round=80)
        if self.backend == "xgboost":
            xgb = optional_import("xgboost")
            if xgb is None:
                raise DependencyMissing("xgboost", "the XGBoost formation model")
            params = {
                "objective": "multi:softprob",
                "num_class": n_classes,
                "eta": 0.2,
                "max_depth": 4,
                "seed": seed,
                "verbosity": 0,
            }
            return xgb.train(params, xgb.DMatrix(X, label=y), num_boost_round=60)
        raise ModelError(f"unknown backend {self.backend}")

    def _proba(self, model, X, n_classes) -> np.ndarray:
        if self.backend in ("numpy", "sklearn"):
            P = model.predict_proba(X)
            if self.backend == "sklearn" and P.shape[1] != n_classes:  # class absent in a CV fold
                full = np.full((len(X), n_classes), 1e-6)
                full[:, model.classes_] = P
                P = full / full.sum(axis=1, keepdims=True)
            return P
        if self.backend == "lightgbm":
            return np.asarray(model.predict(X)).reshape(len(X), n_classes)
        xgb = optional_import("xgboost")
        return np.asarray(model.predict(xgb.DMatrix(X))).reshape(len(X), n_classes)

    # ------------------------------------------------------------------ inference (LearnedModel protocol)
    def predict_proba_intervals(self, intervals, ground_levels=None) -> np.ndarray:
        feats = [interval_features(iv, (ground_levels or {}).get(iv.location_id), self.use_depth) for iv in intervals]
        X = self.space.transform(feats, [iv.description for iv in intervals])
        P = self._proba(self.model, X, len(self.classes))
        return _softmax(np.log(np.clip(P, 1e-9, 1)) / self.temperature)

    def log_proba(self, parsed, interval, floor: float = 0.0025) -> dict[str, float]:
        """Evidence for the GeoReasoner: log-probabilities floored at ``floor`` so the learned model is
        one bounded source of evidence among others (max swing ~6 log-units)."""
        P = self.predict_proba_intervals([interval])[0]
        return {c: float(np.log(max(p, floor))) for c, p in zip(self.classes, P)}

    # ------------------------------------------------------------------ explainability
    def explain(self, interval, ground_level: float | None = None, n: int = 6) -> list[tuple[str, float]]:
        f = interval_features(interval, ground_level, self.use_depth)
        X = self.space.transform([f], [interval.description])
        k = int(np.argmax(self._proba(self.model, X, len(self.classes))[0]))
        if self.backend == "numpy":
            c = self.model.contributions(X[0])[:, k]
        elif self.backend == "sklearn":
            coef = self.model.coef_
            row = coef[k] if coef.shape[0] == len(self.classes) else coef[0]
            c = X[0] * row
        elif self.backend == "lightgbm":
            contrib = np.asarray(self.model.predict(X, pred_contrib=True)).reshape(len(self.classes), -1)
            c = contrib[k, :-1]
        else:
            xgb = optional_import("xgboost")
            contrib = np.asarray(self.model.predict(xgb.DMatrix(X), pred_contribs=True)).reshape(len(self.classes), -1)
            c = contrib[k, :-1]
        order = np.argsort(-np.abs(c))[:n]
        return [(self.space.names[i], float(c[i])) for i in order if abs(c[i]) > 1e-9]

    def top_features(self, n: int = 8) -> dict[str, list[str]]:
        out = {}
        if self.backend == "numpy" and self.model is not None:
            for k, cl in enumerate(self.classes):
                w = self.model.W[:, k]
                out[cl] = [self.space.names[i] for i in np.argsort(-w)[:n] if w[i] > 0]
        elif self.backend == "sklearn" and self.model is not None and self.model.coef_.shape[0] == len(self.classes):
            for k, cl in enumerate(self.classes):
                w = self.model.coef_[k]
                out[cl] = [self.space.names[i] for i in np.argsort(-w)[:n] if w[i] > 0]
        elif self.backend == "lightgbm" and self.model is not None:
            imp = self.model.feature_importance("gain")
            out["all"] = [self.space.names[i] for i in np.argsort(-imp)[:n]]
        return out

    # ------------------------------------------------------------------ persistence (no pickle)
    def save(self, path) -> Path:
        path = Path(path)
        d = {
            "classes": self.classes,
            "space": self.space.to_dict(),
            "backend": self.backend,
            "card": self.card,
            "use_depth": self.use_depth,
            "temperature": self.temperature,
        }
        if self.backend == "numpy":
            d["model"] = self.model.to_dict()
        elif self.backend == "sklearn":
            d["model"] = {
                "coef": self.model.coef_.tolist(),
                "intercept": self.model.intercept_.tolist(),
                "classes_": self.model.classes_.tolist(),
            }
        elif self.backend == "lightgbm":
            d["model"] = self.model.model_to_string()
        else:
            raw = self.model.save_raw("json")
            d["model"] = json.loads(bytes(raw).decode())
        path.write_text(json.dumps(d))
        return path

    @classmethod
    def load(cls, path) -> TrainedFormationModel:
        d = json.loads(Path(path).read_text())
        obj = cls(
            d["classes"],
            FeatureSpace.from_dict(d["space"]),
            d["backend"],
            None,
            d.get("card", {}),
            d.get("use_depth", True),
            d.get("temperature", 1.0),
        )
        if obj.backend == "numpy":
            obj.model = _NumpyLogReg.from_dict(d["model"])
        elif obj.backend == "sklearn":
            sk = optional_import("sklearn.linear_model")
            if sk is None:
                raise DependencyMissing("scikit-learn", "loading this model")
            m = sk.LogisticRegression()
            m.coef_ = np.array(d["model"]["coef"])
            m.intercept_ = np.array(d["model"]["intercept"])
            m.classes_ = np.array(d["model"]["classes_"])
            obj.model = m
        elif obj.backend == "lightgbm":
            lgb = optional_import("lightgbm")
            obj.model = lgb.Booster(model_str=d["model"])
        else:
            xgb = optional_import("xgboost")
            b = xgb.Booster()
            b.load_model(bytearray(json.dumps(d["model"]).encode()))
            obj.model = b
        return obj


def training_set(project, interpretation=None, min_probability: float = 0.9):
    """Reviewed (overridden) intervals plus high-confidence stated/classified interpretations."""
    out = []
    for iv in project.intervals:
        if (
            iv.override is not None
            or iv.unit
            and iv.probability is not None
            and iv.probability >= min_probability
            and iv.status.value in ("stated", "classified")
        ):
            out.append(iv)
    return out
