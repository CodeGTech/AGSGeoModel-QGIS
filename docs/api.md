# Python API

The headless core can be used without QGIS (scripts, notebooks, servers). The functions in
`agsgeomodel.workflows` and `agsgeomodel.pipeline` are the same ones the plugin and the Processing
algorithms call. The QGIS shell (`plugin.py`, `gui/`, `algorithms/`) is documented in the
[user guide](user_guide.md) and the [Processing reference](reference/processing.md).

```python
from agsgeomodel import workflows as wf

cfg = wf.new_config("my_project", ["site.ags"])          # project folder + configuration
cfg.dtm_path, cfg.faults_path = "dtm.asc", "faults.gpkg"
wf.save_config(cfg, "my_project")
res = wf.run_project("my_project")                        # import, interpret, model, outputs
items = wf.review_items("my_project")                     # intervals flagged for review
wf.decide("my_project", items[0], "accepted", "A. Reviewer", "consistent with neighbours")
sec = wf.make_section("my_project", [[545100, 180300], [546150, 180300]], "B-B")
```

## Workflows and pipeline

```{eval-rst}
.. automodule:: agsgeomodel.workflows

.. automodule:: agsgeomodel.pipeline
```

## Configuration

```{eval-rst}
.. automodule:: agsgeomodel.core.config
```

## AGS import and validation

```{eval-rst}
.. automodule:: agsgeomodel.importers.ags.reader

.. automodule:: agsgeomodel.importers.ags.validator

.. automodule:: agsgeomodel.importers.ags.crs

.. automodule:: agsgeomodel.importers.ags.mapper
```

## Geology (GeoReasoner)

```{eval-rst}
.. automodule:: agsgeomodel.geology.reasoner

.. automodule:: agsgeomodel.geology.knowledge.kb

.. automodule:: agsgeomodel.geology.sequence

.. automodule:: agsgeomodel.geology.validation
```

## Modelling

```{eval-rst}
.. automodule:: agsgeomodel.modelling.builder

.. automodule:: agsgeomodel.modelling.kriging

.. automodule:: agsgeomodel.modelling.variogram

.. automodule:: agsgeomodel.modelling.uncertainty

.. automodule:: agsgeomodel.modelling.stochastic
```

## Engineering, groundwater and HDD

```{eval-rst}
.. automodule:: agsgeomodel.processors.cpt

.. automodule:: agsgeomodel.processors.spt

.. automodule:: agsgeomodel.processors.ground_model

.. automodule:: agsgeomodel.hydrogeology.engine

.. automodule:: agsgeomodel.hdd.profile

.. automodule:: agsgeomodel.hdd.assessment
```

## BGS, machine learning and correlation

```{eval-rst}
.. automodule:: agsgeomodel.bgs.connector

.. automodule:: agsgeomodel.bgs.discovery

.. automodule:: agsgeomodel.ai.formation_model

.. automodule:: agsgeomodel.ai.clustering

.. automodule:: agsgeomodel.correlation.engine
```

## Outputs and storage

```{eval-rst}
.. automodule:: agsgeomodel.visualisation.sections

.. automodule:: agsgeomodel.exports

.. automodule:: agsgeomodel.database.store

.. automodule:: agsgeomodel.reports.html
```
