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
