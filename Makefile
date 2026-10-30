PY ?= python

.PHONY: test test-all lint format package docs example clean

test:            ## core tests (no QGIS needed)
	$(PY) -m pytest -m "not performance and not qgis"

test-all:        ## everything incl. QGIS integration (run inside a QGIS Python environment)
	$(PY) -m pytest -m "not performance"

lint:
	ruff check agsgeomodel tests scripts docs/conf.py && ruff format --check agsgeomodel tests scripts docs/conf.py

format:
	ruff format agsgeomodel tests scripts docs/conf.py && ruff check --fix agsgeomodel tests scripts docs/conf.py

package:         ## plugin zip for QGIS (dist/AGSGeoModel-<version>.zip)
	$(PY) scripts/package_plugin.py --out dist

docs:            ## HTML documentation (Sphinx + MyST)
	$(PY) -m sphinx -b html docs docs/_build/html

example:         ## synthetic example project (NOT REAL DATA) in ./example_project
	$(PY) -m agsgeomodel example example_project

clean:
	rm -rf dist build docs/_build .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
