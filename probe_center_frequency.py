#!/usr/bin/env python
"""Probe real NISAR GSLC products for the metadata needed to set dolphin's wavelength.

Context: isce-framework/dolphin#704. dolphin cannot currently auto-detect the
radar wavelength for NISAR GSLC inputs, so `timeseries/` outputs silently stay in
radians. Two fixes have been floated in that issue:

  1. Correct the `NISAR_L_FREQUENCY` constant (reuse the UAVSAR L-band value).
  2. Sniff the mission from the filename with a regex, as is done for OPERA-S1.

This script measures what real products actually contain, so those proposals can
be checked against data rather than assumption.

For every granule in `granules.json` it reads:

  - /science/LSAR/identification/{missionId,radarBand,listOfFrequencies,...}
  - /science/LSAR/GSLC/grids/frequency{A,B}/centerFrequency

and derives `wavelength = c / centerFrequency`, comparing it against both
proposals.

No bulk download: each GSLC is ~22 GB, but HDF5 byte-range reads over HTTPS pull
only the metadata (a few MB per granule). This uses `opera_utils._remote.open_h5`
-- the same code path dolphin itself already uses to read remote NISAR HDF5
(`dolphin.io._core.read_nisar_grid_metadata`).

Requires Earthdata Login credentials in ~/.netrc with mode 0600; see README.md.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from opera_utils._remote import open_h5

HERE = Path(__file__).parent

# dolphin/src/dolphin/constants.py
SPEED_OF_LIGHT = 299_792_458
DOLPHIN_NISAR_L_FREQUENCY = 1.25e9
DOLPHIN_NISAR_L_WAVELENGTH = SPEED_OF_LIGHT / DOLPHIN_NISAR_L_FREQUENCY
# The value isce-framework/dolphin#704 proposes reusing for NISAR L-band.
UAVSAR_WAVELENGTH = 0.238403545

IDENT_PATH = "/science/LSAR/identification"
GRIDS_PATH = "/science/LSAR/GSLC/grids"
IDENT_KEYS = (
    "missionId",
    "radarBand",
    "listOfFrequencies",
    "isMixedMode",
    "productSpecificationVersion",
    "productVersion",
)


@dataclass
class FrequencyResult:
    """One frequency sub-band (`frequencyA` or `frequencyB`) of a GSLC."""

    name: str
    present: bool
    center_frequency_hz: float | None = None
    wavelength_m: float | None = None
    polarizations: list[str] = field(default_factory=list)

    @property
    def err_vs_dolphin_mm(self) -> float | None:
        """Error, in mm, of dolphin's current constant against this measurement."""
        if self.wavelength_m is None:
            return None
        return 1000 * (self.wavelength_m - DOLPHIN_NISAR_L_WAVELENGTH)

    @property
    def err_vs_uavsar_mm(self) -> float | None:
        """Error, in mm, of #704's proposed UAVSAR value against this measurement."""
        if self.wavelength_m is None:
            return None
        return 1000 * (self.wavelength_m - UAVSAR_WAVELENGTH)


@dataclass
class GranuleResult:
    """Everything read from a single GSLC granule."""

    config: str
    granule_id: str
    identification: dict[str, str] = field(default_factory=dict)
    frequencies: list[FrequencyResult] = field(default_factory=list)
    error: str | None = None


def _decode(value: object) -> str:
    """Render an h5py scalar/array value as a plain string."""
    if isinstance(value, bytes):
        return value.decode()
    if hasattr(value, "tolist"):
        v = value.tolist()
        if isinstance(v, list):
            return ",".join(x.decode() if isinstance(x, bytes) else str(x) for x in v)
        return _decode(v)
    return str(value)


def probe(base_url: str, config: str, granule_id: str) -> GranuleResult:
    """Read wavelength-relevant metadata from one remote GSLC."""
    result = GranuleResult(config=config, granule_id=granule_id)
    url = f"{base_url}/{granule_id}/{granule_id}.h5"
    try:
        with open_h5(url) as hf:
            ident = hf.get(IDENT_PATH)
            if ident is not None:
                for key in IDENT_KEYS:
                    if key in ident:
                        result.identification[key] = _decode(ident[key][()])

            for name in ("frequencyA", "frequencyB"):
                grp = hf.get(f"{GRIDS_PATH}/{name}")
                if grp is None or "centerFrequency" not in grp:
                    result.frequencies.append(FrequencyResult(name=name, present=False))
                    continue
                fc = float(grp["centerFrequency"][()])
                pols = [_decode(p) for p in grp["listOfPolarizations"][()]]
                result.frequencies.append(
                    FrequencyResult(
                        name=name,
                        present=True,
                        center_frequency_hz=fc,
                        wavelength_m=SPEED_OF_LIGHT / fc,
                        polarizations=pols,
                    )
                )
    except Exception as e:  # noqa: BLE001 - one bad granule must not kill the run
        result.error = f"{type(e).__name__}: {e}"
    return result


def _versions() -> dict[str, str]:
    import h5py
    import opera_utils

    return {
        "python": platform.python_version(),
        "opera_utils": opera_utils.__version__,
        "h5py": h5py.__version__,
    }


def render_markdown(results: list[GranuleResult], versions: dict[str, str]) -> str:
    """Render the measurement table cited by the issue comment."""
    lines: list[str] = []
    a = lines.append
    a("<!-- Generated by `make validate`. Do not edit by hand. -->")
    a("")
    a("# NISAR GSLC `centerFrequency`: measurements from real products")
    a("")
    a("Evidence for [isce-framework/dolphin#704][issue]. See `README.md` for how to")
    a("reproduce, and `granules.json` for the granule list and selection rule.")
    a("")
    a("[issue]: https://github.com/isce-framework/dolphin/issues/704")
    a("")
    a("## Method")
    a("")
    a(
        "One granule per distinct (mode, polarization) configuration in the"
        " `NISAR_L2_GSLC_BETA_V1` collection. For each, `centerFrequency` was read"
        " from `/science/LSAR/GSLC/grids/frequency{A,B}/` via HDF5 byte-range reads"
        " over HTTPS (`opera_utils._remote.open_h5`), and the wavelength derived as"
        " `c / centerFrequency`. No product was downloaded in full."
    )
    a("")
    a("Compared against:")
    a("")
    a(
        f"- **dolphin today**: `NISAR_L_FREQUENCY = {DOLPHIN_NISAR_L_FREQUENCY:.3g}`"
        f" -> `{DOLPHIN_NISAR_L_WAVELENGTH:.9f}` m"
    )
    a(f"- **#704's proposal**: reuse `UAVSAR_WAVELENGTH = {UAVSAR_WAVELENGTH}` m")
    a("")
    a("## Results")
    a("")
    a(
        "| config | frequency | centerFrequency [MHz] | wavelength [m] | pols |"
        " vs dolphin [mm] | vs UAVSAR [mm] |"
    )
    a("|---|---|---:|---:|---|---:|---:|")
    for r in results:
        if r.error:
            a(f"| {r.config} | — | — | — | ERROR: {r.error} | — | — |")
            continue
        for f in r.frequencies:
            if not f.present:
                a(f"| {r.config} | {f.name} | *absent* | — | — | — | — |")
                continue
            a(
                f"| {r.config} | {f.name} | {f.center_frequency_hz / 1e6:,.1f} |"
                f" {f.wavelength_m:.9f} | {'+'.join(f.polarizations)} |"
                f" {f.err_vs_dolphin_mm:+.2f} | {f.err_vs_uavsar_mm:+.2f} |"
            )
    a("")

    wavelengths = sorted(
        {f.wavelength_m for r in results for f in r.frequencies if f.wavelength_m}
    )
    freqs = sorted(
        {
            f.center_frequency_hz
            for r in results
            for f in r.frequencies
            if f.center_frequency_hz
        }
    )
    a("## Summary")
    a("")
    if wavelengths:
        spread_mm = 1000 * (wavelengths[-1] - wavelengths[0])
        a(
            f"- **{len(freqs)} distinct `centerFrequency` values** observed across"
            f" {len(results)} granules:"
            f" {', '.join(f'{f / 1e6:,.1f}' for f in freqs)} MHz."
        )
        a(
            f"- Wavelengths span `{wavelengths[0]:.9f}` .. `{wavelengths[-1]:.9f}` m"
            f" -- a spread of **{spread_mm:.1f} mm**"
            f" ({100 * spread_mm / 1000 / wavelengths[0]:.1f}% of a wavelength)."
        )
        exact = [
            r.config
            for r in results
            for f in r.frequencies
            if f.wavelength_m and abs(f.err_vs_uavsar_mm) < 1e-6
        ]
        n_bands = sum(1 for r in results for f in r.frequencies if f.present)
        worst_uavsar = max(
            abs(f.err_vs_uavsar_mm)
            for r in results
            for f in r.frequencies
            if f.wavelength_m
        )
        a(
            f"- The UAVSAR value proposed in #704 is exact for {len(exact)} of"
            f" {n_bands} measured sub-bands ({', '.join(exact) if exact else 'none'});"
            f" elsewhere it is off by up to {worst_uavsar:.1f} mm."
        )
        dual = [r.config for r in results if sum(f.present for f in r.frequencies) > 1]
        a(
            f"- {len(dual)} of {len(results)} granules carry **both** `frequencyA` and"
            " `frequencyB`, with **two different** centre frequencies in one file."
            " Which one applies is decided by dolphin's `subdataset` setting, not by"
            " anything in the filename."
        )
        no_a = [
            r.config
            for r in results
            if not next(f.present for f in r.frequencies if f.name == "frequencyA")
        ]
        if no_a:
            a(
                f"- `frequencyA` is **absent** in {len(no_a)} configuration(s)"
                f" ({', '.join(no_a)}), so its presence cannot be assumed;"
                " `identification/listOfFrequencies` lists what exists."
            )
    a("")
    a("### Does the filename's mode field predict the centre frequencies?")
    a("")
    a(
        "The SDS granule name carries a mode field (e.g. `4005`), so it is fair to ask"
        " whether a lookup table keyed on it would do. Grouping the measurements by"
        " mode:"
    )
    a("")
    by_mode: dict[str, set[tuple[str, float]]] = {}
    for r in results:
        mode = r.config.split("_")[0]
        for f in r.frequencies:
            if f.center_frequency_hz:
                by_mode.setdefault(mode, set()).add((f.name, f.center_frequency_hz))
    a("| mode | configs | (frequency, centerFrequency [MHz]) observed |")
    a("|---|---|---|")
    for mode in sorted(by_mode):
        configs = [r.config for r in results if r.config.startswith(f"{mode}_")]
        pairs = ", ".join(f"{n}={fc / 1e6:,.1f}" for n, fc in sorted(by_mode[mode]))
        a(f"| {mode} | {len(configs)} | {pairs} |")
    a("")
    consistent = all(
        len({n for n, _ in pairs}) == len(pairs) for pairs in by_mode.values()
    )
    if consistent:
        a(
            "Within this sample each mode maps to a single set of centre frequencies,"
            " so a mode-keyed table is not obviously wrong. It is still the weaker"
            " option: it would hardcode an undocumented mapping that has to track SDS"
            " changes, it cannot say whether `frequencyA` or `frequencyB` is the one"
            " being read, and it can only ever approximate a value the product already"
            " states exactly."
        )
    else:
        a(
            "The same mode maps to **different** centre frequencies across granules, so"
            " a mode-keyed table would be wrong outright."
        )
    a("")
    a("## Environment")
    a("")
    for k, v in versions.items():
        a(f"- {k}: `{v}`")
    a("")
    return "\n".join(lines)


def main() -> int:
    """Probe every granule in the spec file and write the report."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--granules",
        type=Path,
        default=HERE / "granules.json",
        help="JSON file listing the granules to probe (default: granules.json)",
    )
    p.add_argument(
        "--out-md",
        type=Path,
        default=HERE / "reports" / "center_frequency.md",
        help="Markdown report to write",
    )
    p.add_argument(
        "--out-json",
        type=Path,
        default=HERE / "reports" / "center_frequency.json",
        help="Raw results to write",
    )
    p.add_argument("--only", nargs="*", help="Probe only these config keys")
    p.add_argument(
        "--render-only",
        action="store_true",
        help="Re-render the report from --out-json instead of re-probing (no network)",
    )
    args = p.parse_args()

    if args.render_only:
        saved = json.loads(args.out_json.read_text())
        results = [
            GranuleResult(
                **{k: v for k, v in r.items() if k != "frequencies"},
                frequencies=[FrequencyResult(**f) for f in r["frequencies"]],
            )
            for r in saved["results"]
        ]
        args.out_md.write_text(render_markdown(results, saved["versions"]))
        print(f"re-rendered {args.out_md} from {args.out_json}", file=sys.stderr)
        return 0

    spec = json.loads(args.granules.read_text())
    base_url = spec["base_url"]
    entries = spec["granules"]
    if args.only:
        entries = [e for e in entries if e["config"] in args.only]

    results = []
    for i, e in enumerate(entries, 1):
        print(f"[{i}/{len(entries)}] {e['config']}: {e['id']}", file=sys.stderr)
        r = probe(base_url, e["config"], e["id"])
        if r.error:
            print(f"    !! {r.error}", file=sys.stderr)
        else:
            for f in r.frequencies:
                if f.present:
                    print(
                        f"    {f.name}: {f.center_frequency_hz / 1e6:,.1f} MHz"
                        f" -> {f.wavelength_m:.9f} m",
                        file=sys.stderr,
                    )
        results.append(r)

    versions = _versions()
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(
        json.dumps(
            {"versions": versions, "results": [asdict(r) for r in results]}, indent=2
        )
        + "\n"
    )
    args.out_md.write_text(render_markdown(results, versions))
    print(f"\nwrote {args.out_md}\nwrote {args.out_json}", file=sys.stderr)
    return 1 if any(r.error for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
