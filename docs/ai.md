# Machine learning

AGSGeoModel uses machine learning where it helps a geologist and keeps it where it can be checked:

> **ML proposes, the GeoReasoner disposes.** Every learned component supplies *evidence* or *suggestions*.
> Interpretations are always decoded by the order-constrained sequence model, so no learned model can
> produce a stratigraphically impossible interpretation, and every prediction can be explained in
> geological terms.

| Component | What it does | Where |
|---|---|---|
| Trainable formation model | learns how a contractor or a site describes each unit, and adds that as evidence to the GeoReasoner | *Train formation model from reviewed projects*; Settings › GeoReasoner › Learned formation model |
| Description similarity | compares descriptions between holes, robust to spelling variants and abbreviations | hybrid and AI-assisted correlation |
| Order-preserving log alignment | aligns two logs on description similarity and elevation | *Correlate exploratory holes* (`ai_assisted`) |
| Domain clustering | groups holes by interpretable features and explains each group | *Cluster holes into geological domains* |

## 1. Trainable formation model

### Training data

Labels come only from data a reviewer can stand behind (`ai.training_set`):

* intervals with a **reviewer override** (`lithology.override_unit`), and
* intervals the GeoReasoner adopted from the logger's codes or classified from the description
  (`stated` or `classified`) with a posterior probability of at least 0.9 (configurable).

Inferred intervals (no usable description) and unreviewed low-confidence intervals are never used, so the
model does not learn from its own guesses. Several reviewed projects can be combined (the algorithm takes a
list of `project.gpkg` files). Classes with fewer than three examples are left out.

### Features

The same human-readable tokens produced by the BS 5930 description parser (`pt:clay`, `sec:sandy`,
`cons:stiff`, `col:blue_grey`, `incl:selenite`, `chalk:grade_B`…), with formation names that appear in the
text removed (they are already used as stated evidence), plus the log of the mid-depth and thickness and the
elevation of the interval. Optional character n-grams are available from the Python API
(`TrainedFormationModel.train(..., ngram=3)`).

### Learners

| Backend | Requires | Explanation |
|---|---|---|
| `numpy` | nothing (built in) | multinomial logistic regression, L2, full-batch Adam; coefficient × value contributions |
| `sklearn` | scikit-learn | `LogisticRegression`; coefficient contributions |
| `lightgbm` | LightGBM | gradient-boosted trees; SHAP-style `pred_contrib` |
| `xgboost` | XGBoost | gradient-boosted trees; SHAP-style `pred_contrib` |
| `auto` | | LightGBM if installed, else scikit-learn, else NumPy |

### Validation and calibration

Training runs a 5-fold cross-validation and stores in the **model card**: the number of samples and class
counts, cross-validated accuracy and Brier score, the confusion matrix, the hash of the training data, the
top features per class, the intended use and the limitations. Probabilities are calibrated by a temperature
fitted on the out-of-fold predictions; the search is restricted to T ≥ 1, so calibration may soften the
model but never make it more confident than its cross-validation supports.

Models are saved as JSON (no pickle): they can be read, diffed and archived with the project, and loading
one never executes code.

### Use in the GeoReasoner

With a model selected in **Settings › GeoReasoner** (or `learned_model_path` in the configuration), each
interval receives an extra evidence term: the model's log-probabilities, with probabilities floored at
0.0025 and centred, so that a confident model shifts the balance by a few log-units while a single learned
opinion cannot outweigh a logged Lexicon code and the stratigraphic order. The term appears as
`learned_model` in the evidence breakdown of each interval and is explained feature by feature
(`TrainedFormationModel.explain`). *Train a learned model from this project on the next run* writes
`formation_model.json` to the project folder after the interpretation.

On the synthetic example (141 training intervals, 27 holes) the built-in learner reaches a cross-validated
accuracy of 1.0. That figure reflects the synthetic logs, which are generated from consistent descriptions;
expect lower accuracy on real logs, and check the confusion matrix and the top features before using a
model. A model trained in one province and logging style should not be assumed to transfer to another.

## 2. Description similarity

`ai.similarity` compares descriptions with character trigram TF-IDF vectors and cosine similarity. It needs
no external model and copes with spelling variants and abbreviations common in logs ("v. stiff", "fissd",
"slty"). When `sentence-transformers` is installed **and** a model is available locally, sentence embeddings
can be used instead; the plugin never downloads models silently.

## 3. Correlation and log alignment

The `ai_assisted` correlation mode aligns the units of two holes with an order-preserving dynamic-programming
alignment (Needleman-Wunsch) on a score combining description similarity (0.7) and elevation agreement
(0.3). Because the alignment preserves order, it cannot produce crossing correlations; where it pairs
different units, or a pair not supported by the formation correlation, the link is flagged for review. See
[methods](methods.md#13-correlation).

## 4. Domain clustering

Each interpreted hole is described by interpretable features: cover thickness by domain (artificial,
alluvial, glacial, slope, aeolian), rockhead level, presence of each bedrock unit and mean SPT N in the top
10 m. Features are standardised and clustered with k-means (k-means++ initialisation); k (2 to 6 by
default) is chosen by the silhouette score. Each cluster is explained by the three features that differ
most from the site mean, in original units (for example *thk_alluvial_m high (10.41 vs site mean 5.66)*),
and compared with the rule-based engineering-geological domain of each hole. The agreement figure and the
per-hole rule domain make disagreements visible; clusters are suggestions for the geologist, not
replacements for the geological domains.

## Limitations

* A learned model is only as good as its reviewed labels; it inherits any systematic error a reviewer made.
* Cross-validation within one project is optimistic for use on another site.
* The clustering uses a handful of engineered features; it is a screening aid for large datasets, not a
  geological classification.
