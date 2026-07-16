# dolphin-nisar-sample

Real-data evidence for how dolphin should determine the radar wavelength of
NISAR GSLC inputs — [isce-framework/dolphin#704][issue].

This is a **sibling project** to a [dolphin][dolphin] clone: it lives inside the
clone's tree but is a fully independent git repository, excluded from the clone
via `.git/info/exclude`. Nothing here is proposed for upstream — it is the
*evidence* that backs a proposal made in the issue.

[issue]: https://github.com/isce-framework/dolphin/issues/704
[dolphin]: https://github.com/isce-framework/dolphin

## Why this exists

dolphin auto-detects the wavelength only for OPERA-S1, by matching the burst ID
in the filename ([`_displacement.py`][post_init]). NISAR GSLC filenames don't
match, so `input_options.wavelength` stays `None` and `timeseries/` outputs
silently remain in radians. Issue #704 reports this and proposes two fixes:

1. correct the `NISAR_L_FREQUENCY` constant, reusing the UAVSAR L-band value;
2. sniff the mission from the filename with a regex, as is done for OPERA-S1.

Both proposals are testable against real products. This harness reads
`centerFrequency` out of actual NISAR GSLCs so the discussion can be settled with
measurements instead of assumptions. The result is in
[`reports/center_frequency.md`](reports/center_frequency.md).

[post_init]: https://github.com/isce-framework/dolphin/blob/main/src/dolphin/workflows/config/_displacement.py#L187-L198

## What it does

For each granule in [`granules.json`](granules.json) it reads

- `/science/LSAR/identification/{missionId,radarBand,listOfFrequencies,…}`
- `/science/LSAR/GSLC/grids/frequency{A,B}/centerFrequency`

and derives `wavelength = c / centerFrequency`, tabulating it against both
proposals.

Two design choices are worth stating, since they are what make the evidence
worth anything:

- **Nothing is downloaded.** Each GSLC is ~22 GB, but HDF5 byte-range reads over
  HTTPS fetch only the metadata — a few MB per granule, a couple of minutes for
  the whole run. So the numbers can be rechecked on a laptop.
- **The reader is dolphin's own.** Access goes through
  `opera_utils._remote.open_h5`, the same call
  `dolphin.io._core.read_nisar_grid_metadata` already uses for remote NISAR
  HDF5. This measures what dolphin would see, not what a lookalike reader sees.

`granules.json` is the single source of truth: the probe and the generated report
read the same file, so the granule IDs cited in the report are by construction
the ones that were measured.

## Reproducing

You need [Earthdata Login][edl] credentials in `~/.netrc`:

```
machine urs.earthdata.nasa.gov
login <username>
password <password>
```

**The file must be mode `0600`.** Python's `netrc` module refuses to parse a
group- or world-readable file containing a password, and the failure surfaces
much later as a confusing "no credentials found" error. `curl` does not care, so
a `.netrc` that works for `curl` can still fail here:

```bash
chmod 600 ~/.netrc
```

[edl]: https://urs.earthdata.nasa.gov/

### Option A — use an environment you already have

If you have a working dolphin (or opera-utils) environment, skip the setup
entirely and point `PYTHON` at it:

```bash
make validate PYTHON=/path/to/your/dolphin-env/bin/python
```

### Option B — build an isolated venv here

```bash
make setup      # uv sync + link the system GDAL bindings
make check-auth # confirm Python can read your ~/.netrc
make validate   # probe every granule, regenerate reports/
```

`make help` lists every target.

Two packaging wrinkles you may hit with Option B, both in the dependency chain
rather than in this harness:

- **GDAL.** `opera-utils` imports `osgeo`, which pip cannot build. `make setup`
  symlinks the system bindings (Debian/Ubuntu: `sudo apt install python3-gdal`)
  into the venv. Those C extensions are ABI-locked to the interpreter's minor
  version, so `.python-version` must match `/usr/bin/python3`; `make link-gdal`
  checks this and tells you if it doesn't. Option A sidesteps all of it.
- **`rich`.** As of opera-utils 0.25.8, `import opera_utils` fails without
  `rich`, which is not declared in any of its extras. `pyproject.toml` adds it
  explicitly as a workaround.

## Layout

- `granules.json` — granules to probe, with the selection rule. Single source of
  truth for both the probe and the report.
- `probe_center_frequency.py` — the measurement; writes `reports/`.
- `survey_configurations.py` — how `granules.json` was derived from CMR
  (`make survey`). Metadata search only; no credentials needed.
- `reports/center_frequency.md` — the generated report cited from the issue.
- `reports/center_frequency.json` — the same results, raw.
- `pyproject.toml` / `uv.lock` — pinned environment used to produce `reports/`.
