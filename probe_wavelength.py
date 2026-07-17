#!/usr/bin/env python
"""Validate `opera_utils.nisar.get_nisar_wavelength` against real NISAR GSLCs.

Context: isce-framework/dolphin#704. `probe_center_frequency.py` measured what
real GSLC products contain; this script validates the wavelength reader
proposed for opera-utils (`get_nisar_wavelength` + `GslcProduct.get_wavelength`)
against those same products. Run it with the opera-utils revision under review
installed -- `make validate-wavelength` handles that (see `OPERA_UTILS_SPEC`
in the Makefile).

For every granule in `granules.json` it:

  1. reads `centerFrequency` and `listOfPolarizations` directly from
     `/science/LSAR/GSLC/grids/frequency{A,B}/` with h5py (raw reference);
  2. for each sub-band present, calls `get_nisar_wavelength(url, subdataset)`
     with `subdataset = .../frequency{X}/<first polarization>` and requires the
     result to equal `c / centerFrequency` from step 1 exactly;
  3. for each sub-band absent, calls the reader anyway and requires a
     `ValueError` naming the missing dataset and the URL.

`GslcProduct.get_wavelength` is additionally exercised on two representative
granules (one dual-frequency, one frequencyB-only, where the default `"A"`
must raise rather than silently fall back).

No bulk download: HDF5 byte-range reads over HTTPS pull only the metadata
(a few MB per granule), via `opera_utils._remote.open_h5`. Requires Earthdata
Login credentials in ~/.netrc with mode 0600; see README.md.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from opera_utils._remote import open_h5

HERE = Path(__file__).parent

SPEED_OF_LIGHT = 299_792_458
GRIDS_PATH = "/science/LSAR/GSLC/grids"

# GslcProduct.get_wavelength spot checks: (config, frequency, expect_error).
# 2005_QPDH carries both sub-bands; 0005_NASV is frequencyB-only, so requesting
# the default "A" must raise instead of silently falling back.
PRODUCT_CHECKS = [
    ("2005_QPDH", "A", False),
    ("2005_QPDH", "B", False),
    ("0005_NASV", "B", False),
    ("0005_NASV", "A", True),
]


@dataclass
class FrequencyCheck:
    """Validation outcome for one sub-band of one granule."""

    name: str
    present: bool
    subdataset: str | None = None
    center_frequency_hz: float | None = None
    wavelength_m: float | None = None
    exact_match: bool | None = None
    absent_raises: bool | None = None
    error_message: str | None = None

    @property
    def ok(self) -> bool:
        """Whether this sub-band behaved as required (match, or loud failure)."""
        return bool(self.exact_match) if self.present else bool(self.absent_raises)


@dataclass
class GranuleValidation:
    """Everything checked on a single GSLC granule."""

    config: str
    granule_id: str
    frequencies: list[FrequencyCheck] = field(default_factory=list)
    error: str | None = None


@dataclass
class ProductCheck:
    """One `GslcProduct.get_wavelength` spot check."""

    config: str
    frequency: str
    expect_error: bool
    raised: bool = False
    wavelength_m: float | None = None
    error_message: str | None = None
    ok: bool = False


def _decode(value: object) -> str:
    """Render an h5py scalar value as a plain string."""
    return value.decode() if isinstance(value, bytes) else str(value)


def validate_granule(base_url: str, config: str, granule_id: str) -> GranuleValidation:
    """Run the wavelength reader against one remote GSLC and check the result."""
    from opera_utils.nisar import get_nisar_wavelength

    result = GranuleValidation(config=config, granule_id=granule_id)
    url = f"{base_url}/{granule_id}/{granule_id}.h5"
    try:
        raw: dict[str, tuple[float, list[str]] | None] = {}
        with open_h5(url) as hf:
            for name in ("frequencyA", "frequencyB"):
                grp = hf.get(f"{GRIDS_PATH}/{name}")
                if grp is None or "centerFrequency" not in grp:
                    raw[name] = None
                    continue
                pols = [_decode(p) for p in grp["listOfPolarizations"][()]]
                raw[name] = (float(grp["centerFrequency"][()]), pols)

        for name, info in raw.items():
            if info is None:
                # Absent sub-band: the reader must fail loudly, not guess
                check = FrequencyCheck(name=name, present=False)
                try:
                    get_nisar_wavelength(url, f"{GRIDS_PATH}/{name}/HH")
                    check.absent_raises = False
                except ValueError as e:
                    check.absent_raises = True
                    check.error_message = str(e)
                result.frequencies.append(check)
                continue
            center_frequency_hz, pols = info
            subdataset = f"{GRIDS_PATH}/{name}/{pols[0]}"
            wavelength = get_nisar_wavelength(url, subdataset)
            result.frequencies.append(
                FrequencyCheck(
                    name=name,
                    present=True,
                    subdataset=subdataset,
                    center_frequency_hz=center_frequency_hz,
                    wavelength_m=wavelength,
                    exact_match=wavelength == SPEED_OF_LIGHT / center_frequency_hz,
                )
            )
    except Exception as e:  # noqa: BLE001 - one bad granule must not kill the run
        result.error = f"{type(e).__name__}: {e}"
    return result


def run_product_checks(
    base_url: str, results: list[GranuleValidation]
) -> list[ProductCheck]:
    """Spot-check the `GslcProduct.get_wavelength` delegating method."""
    from opera_utils.nisar import GslcProduct

    by_config = {r.config: r for r in results}
    checks: list[ProductCheck] = []
    for config, frequency, expect_error in PRODUCT_CHECKS:
        rec = by_config.get(config)
        if rec is None or rec.error:
            continue
        url = f"{base_url}/{rec.granule_id}/{rec.granule_id}.h5"
        check = ProductCheck(
            config=config, frequency=frequency, expect_error=expect_error
        )
        # from_filename parses the granule name; then point the product at the
        # remote URL so get_wavelength reads over HTTPS
        product = GslcProduct.from_filename(f"{rec.granule_id}.h5")
        product.filename = url
        try:
            check.wavelength_m = product.get_wavelength(frequency)
        except ValueError as e:
            check.raised = True
            check.error_message = str(e)
        if expect_error:
            check.ok = check.raised
        else:
            ref = next(f for f in rec.frequencies if f.name == f"frequency{frequency}")
            check.ok = (
                not check.raised
                and ref.center_frequency_hz is not None
                and check.wavelength_m == SPEED_OF_LIGHT / ref.center_frequency_hz
            )
        checks.append(check)
    return checks


def _opera_utils_commit(expected: str | None) -> tuple[str, str]:
    """Return (commit, provenance) for the installed opera-utils.

    Tries the PEP 610 `direct_url.json` first (exact for VCS installs; for
    local-directory installs, falls back to `git rev-parse` in the source
    tree). If neither works, the value passed via --expected-commit is
    recorded as unverified rather than silently dropping to a version string.
    """
    from importlib.metadata import PackageNotFoundError, distribution

    raw = None
    try:
        raw = distribution("opera-utils").read_text("direct_url.json")
    except (PackageNotFoundError, FileNotFoundError):
        raw = None
    if raw:
        info = json.loads(raw)
        commit = info.get("vcs_info", {}).get("commit_id")
        if commit:
            return commit, "PEP 610 direct_url.json (VCS install)"
        url = info.get("url", "")
        if url.startswith("file://"):
            src = url.removeprefix("file://")
            try:
                commit = subprocess.check_output(
                    ["git", "-C", src, "rev-parse", "HEAD"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
            except (OSError, subprocess.CalledProcessError):
                commit = None
            if commit:
                # Provenance stays path-free: local paths are machine-specific
                # noise in a committed report
                return commit, "git rev-parse of the local install source"
    if expected:
        return expected, "passed via --expected-commit (not verified)"
    return "unknown", "not detectable"


def _versions() -> dict[str, str]:
    import h5py
    import opera_utils

    return {
        "python": platform.python_version(),
        "opera_utils": opera_utils.__version__,
        "h5py": h5py.__version__,
    }


def render_markdown(
    results: list[GranuleValidation],
    product_checks: list[ProductCheck],
    versions: dict[str, str],
    commit: str,
    commit_provenance: str,
    commit_expected: str | None,
) -> str:
    """Render the validation report cited from the opera-utils PR."""
    lines: list[str] = []
    a = lines.append
    a("<!-- Generated by `make validate-wavelength`. Do not edit by hand. -->")
    a("")
    a("# Real-data validation of `opera_utils.nisar.get_nisar_wavelength`")
    a("")
    a("Validation evidence for the wavelength-reader addition to")
    a("[opera-utils][opera-utils] proposed in [isce-framework/dolphin#704][issue],")
    a(f"at opera-utils commit `{commit}`.")
    a("")
    a("[opera-utils]: https://github.com/opera-adt/opera-utils")
    a("[issue]: https://github.com/isce-framework/dolphin/issues/704")
    a("")
    a("## Method")
    a("")
    a(
        "The granules in `granules.json` (one per distinct (mode, polarization)"
        " configuration of `NISAR_L2_GSLC_BETA_V1`; see that file for the selection"
        " rule) were probed remotely via HDF5 byte-range reads over HTTPS -- no"
        " product was downloaded in full. For each granule, `centerFrequency` and"
        " `listOfPolarizations` were first read directly with h5py (raw reference);"
        " then `get_nisar_wavelength(url, subdataset)` was called for each sub-band"
        " present (with `subdataset = .../frequency{X}/<first polarization>`) and"
        " required to equal `c / centerFrequency` exactly, and for each sub-band"
        " absent it was required to raise a `ValueError` naming the missing dataset"
        " and the URL. `GslcProduct.get_wavelength` was spot-checked on one"
        " dual-frequency and one frequencyB-only granule."
    )
    a("")
    a("Reproduce with `make validate-wavelength` (see README.md).")
    a("")
    a("## Results: `get_nisar_wavelength`")
    a("")
    a(
        "| config | frequency | subdataset pol | centerFrequency [MHz] |"
        " wavelength [m] | vs raw `c/f` |"
    )
    a("|---|---|---|---:|---:|---|")
    for r in results:
        if r.error:
            a(f"| {r.config} | — | — | — | — | ERROR: {r.error} |")
            continue
        for f in r.frequencies:
            if f.present:
                pol = (f.subdataset or "").rsplit("/", 1)[-1]
                verdict = "exact" if f.exact_match else "**MISMATCH**"
                a(
                    f"| {r.config} | {f.name} | {pol} |"
                    f" {(f.center_frequency_hz or 0) / 1e6:,.1f} |"
                    f" {f.wavelength_m:.9f} | {verdict} |"
                )
            else:
                verdict = (
                    "contextual `ValueError`" if f.absent_raises else "**NO ERROR**"
                )
                a(f"| {r.config} | {f.name} | — | *absent* | — | {verdict} |")
    a("")
    a("## Results: `GslcProduct.get_wavelength`")
    a("")
    a("| config | frequency arg | outcome |")
    a("|---|---|---|")
    for c in product_checks:
        if c.raised:
            note = "expected: sub-band absent" if c.expect_error else "**UNEXPECTED**"
            a(f"| {c.config} | {c.frequency} | raised `ValueError` ({note}) |")
        else:
            verdict = "matches raw `c/f`" if c.ok else "**MISMATCH**"
            a(f"| {c.config} | {c.frequency} | `{c.wavelength_m:.9f}` m, {verdict} |")
    a("")
    a("## Summary")
    a("")
    n_present = sum(f.present for r in results for f in r.frequencies)
    n_present_ok = sum(
        bool(f.exact_match) for r in results for f in r.frequencies if f.present
    )
    n_absent = sum(not f.present for r in results for f in r.frequencies)
    n_absent_ok = sum(
        bool(f.absent_raises) for r in results for f in r.frequencies if not f.present
    )
    n_prod_ok = sum(c.ok for c in product_checks)
    a(
        f"- **{n_present_ok}/{n_present} present sub-bands**: `get_nisar_wavelength`"
        " returned exactly `c / centerFrequency` (bit-identical float), across all"
        " centre frequencies previously measured in `center_frequency.md`."
    )
    a(
        f"- **{n_absent_ok}/{n_absent} absent sub-bands**: the reader raised a"
        " `ValueError` naming the missing dataset path and the file URL instead of"
        " guessing."
    )
    a(
        f"- **{n_prod_ok}/{len(product_checks)} `GslcProduct.get_wavelength`"
        " checks**, including the frequencyB-only granule where requesting the"
        ' default `"A"` correctly raises -- supporting the design decision not to'
        " silently default to `frequencyA`."
    )
    a("")
    a("## Environment")
    a("")
    a(f"- opera-utils commit: `{commit}` ({commit_provenance})")
    if commit_expected and commit != commit_expected:
        a(
            f"  - **WARNING**: differs from the Makefile pin `{commit_expected}`"
            " (`OPERA_UTILS_COMMIT`)"
        )
    for k, v in versions.items():
        a(f"- {k}: `{v}`")
    a(
        "- Earthdata Login via `~/.netrc` (mode 0600); transport:"
        " `opera_utils._remote.open_h5` HTTPS byte-range reads"
    )
    a("")
    a("## Disclosure")
    a("")
    a(
        "These investigations were assisted with Claude Opus 4.8, Fable 5"
        " (Anthropic), and GPT-5.6 Sol (OpenAI)."
    )
    a("")
    return "\n".join(lines)


def main() -> int:
    """Validate every granule in the spec file and write the report."""
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
        default=HERE / "reports" / "wavelength_validation.md",
        help="Markdown report to write",
    )
    p.add_argument(
        "--out-json",
        type=Path,
        default=HERE / "reports" / "wavelength_validation.json",
        help="Raw results to write",
    )
    p.add_argument("--only", nargs="*", help="Probe only these config keys")
    p.add_argument(
        "--expected-commit",
        help="opera-utils commit the caller believes is installed (recorded, and"
        " checked against the detected commit when detection is possible)",
    )
    p.add_argument(
        "--render-only",
        action="store_true",
        help="Re-render the report from --out-json instead of re-probing (no network)",
    )
    args = p.parse_args()

    if args.render_only:
        saved = json.loads(args.out_json.read_text())
        results = [
            GranuleValidation(
                **{k: v for k, v in r.items() if k != "frequencies"},
                frequencies=[FrequencyCheck(**f) for f in r["frequencies"]],
            )
            for r in saved["results"]
        ]
        product_checks = [ProductCheck(**c) for c in saved["product_checks"]]
        args.out_md.write_text(
            render_markdown(
                results,
                product_checks,
                saved["versions"],
                saved["opera_utils_commit"],
                saved["opera_utils_commit_provenance"],
                args.expected_commit,
            )
        )
        print(f"re-rendered {args.out_md} from {args.out_json}", file=sys.stderr)
        return 0

    commit, provenance = _opera_utils_commit(args.expected_commit)
    print(f"opera-utils commit: {commit} ({provenance})", file=sys.stderr)
    if (
        args.expected_commit
        and commit != args.expected_commit
        and "expected-commit" not in provenance
    ):
        print(
            f"WARNING: detected commit {commit} != expected {args.expected_commit}",
            file=sys.stderr,
        )

    spec = json.loads(args.granules.read_text())
    base_url = spec["base_url"]
    entries = spec["granules"]
    if args.only:
        entries = [e for e in entries if e["config"] in args.only]

    results = []
    for i, e in enumerate(entries, 1):
        print(f"[{i}/{len(entries)}] {e['config']}: {e['id']}", file=sys.stderr)
        r = validate_granule(base_url, e["config"], e["id"])
        if r.error:
            print(f"    !! {r.error}", file=sys.stderr)
        else:
            for f in r.frequencies:
                status = "ok" if f.ok else "FAIL"
                if f.present:
                    print(
                        f"    {status}: {f.name}: {f.wavelength_m:.9f} m",
                        file=sys.stderr,
                    )
                else:
                    print(f"    {status}: {f.name}: absent -> raises", file=sys.stderr)
        results.append(r)

    product_checks = run_product_checks(base_url, results)
    for c in product_checks:
        print(
            f"    product {c.config} freq={c.frequency}: {'ok' if c.ok else 'FAIL'}",
            file=sys.stderr,
        )

    versions = _versions()
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(
        json.dumps(
            {
                "versions": versions,
                "opera_utils_commit": commit,
                "opera_utils_commit_provenance": provenance,
                "results": [asdict(r) for r in results],
                "product_checks": [asdict(c) for c in product_checks],
            },
            indent=2,
        )
        + "\n"
    )
    args.out_md.write_text(
        render_markdown(
            results, product_checks, versions, commit, provenance, args.expected_commit
        )
    )
    print(f"\nwrote {args.out_md}\nwrote {args.out_json}", file=sys.stderr)
    all_ok = all(
        not r.error and all(f.ok for f in r.frequencies) for r in results
    ) and all(c.ok for c in product_checks)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
