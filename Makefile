.DEFAULT_GOAL := help

# The granule list lives in granules.json, not here: the report cites the same
# file the probe reads, so the IDs in the report cannot drift from the IDs
# actually measured. Point PYTHON at any interpreter that has opera-utils
# installed (e.g. your dolphin env) to skip `make setup` entirely.
PYTHON ?= .venv/bin/python

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup:  ## Create .venv and install pinned deps with uv
	uv sync
	@$(MAKE) --no-print-directory link-gdal

link-gdal:  ## Expose the system GDAL bindings to .venv (opera-utils needs osgeo)
	@osgeo_dir="$$(/usr/bin/python3 -c 'import osgeo, os; print(os.path.dirname(osgeo.__file__))' 2>/dev/null)"; \
	if [ -z "$$osgeo_dir" ]; then \
	    echo "No system osgeo found. Install GDAL's Python bindings (Debian/Ubuntu:"; \
	    echo "  sudo apt install python3-gdal), or run with PYTHON=<your dolphin env>."; \
	    exit 1; \
	fi; \
	sys_ver="$$(/usr/bin/python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"; \
	venv_ver="$$(.venv/bin/python -c 'import sys; print("%d.%d" % sys.version_info[:2])')"; \
	if [ "$$sys_ver" != "$$venv_ver" ]; then \
	    echo "Python version mismatch: .venv is $$venv_ver, system GDAL is built for $$sys_ver."; \
	    echo "The osgeo C extensions are ABI-locked to the interpreter minor version."; \
	    echo "Set .python-version to $$sys_ver and re-run 'make clean setup'."; \
	    exit 1; \
	fi; \
	site="$$(.venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"; \
	ln -sfn "$$osgeo_dir" "$$site/osgeo"; \
	echo "linked $$osgeo_dir -> $$site/osgeo"

validate:  ## Probe every granule in granules.json, regenerate reports/
	$(PYTHON) probe_center_frequency.py

render:  ## Rebuild the markdown report from the saved JSON (no network, no auth)
	$(PYTHON) probe_center_frequency.py --render-only

# The opera-utils revision under review (the wavelength reader proposed for
# isce-framework/dolphin#704). OPERA_UTILS_SPEC is anything `uv pip install`
# accepts; override it with a local clone to validate work in progress:
#   make validate-wavelength OPERA_UTILS_SPEC=../opera-utils
OPERA_UTILS_COMMIT ?= 720bca9cb425049424015a430da5cc0466394eea
OPERA_UTILS_SPEC ?= git+https://github.com/s-sasaki-earthsea-wizard/opera-utils@$(OPERA_UTILS_COMMIT)

# rioxarray, scipy, geopandas, pyogrio: `opera_utils.nisar` imports all of
# these at package-import time, but (as of 720bca9) the `nisar` extra declares
# none of them -- the same class of issue as the `rich` workaround in
# pyproject.toml. numpy stays <2 because the system GDAL bindings linked by
# `make link-gdal` are compiled against NumPy 1.x.
setup-wavelength:  ## Install the opera-utils revision under review into .venv
	uv pip install -p .venv "$(OPERA_UTILS_SPEC)" \
		rioxarray scipy geopandas pyogrio "numpy<2"

validate-wavelength: setup-wavelength  ## Validate get_nisar_wavelength on real GSLCs, regenerate reports/
	$(PYTHON) probe_wavelength.py --expected-commit $(OPERA_UTILS_COMMIT)

render-wavelength:  ## Rebuild the wavelength report from the saved JSON (no network, no auth)
	$(PYTHON) probe_wavelength.py --render-only

survey:  ## Re-derive the (mode, polarization) configurations from CMR
	$(PYTHON) survey_configurations.py

check-auth:  ## Verify ~/.netrc is readable by Python's netrc module
	@$(PYTHON) -c "import netrc; n = netrc.netrc(); \
	    h = 'urs.earthdata.nasa.gov'; \
	    print('OK:', h, 'found in ~/.netrc') if h in n.hosts else \
	    (_ for _ in ()).throw(SystemExit(f'MISSING: no {h} entry in ~/.netrc'))"

lint:  ## Run ruff
	uv run --group dev ruff check . && uv run --group dev ruff format --check .

clean:  ## Remove the venv
	rm -rf .venv

.PHONY: help setup link-gdal validate render survey check-auth lint clean
