#!/usr/bin/env python
"""Re-derive the (mode, polarization) configurations present in NISAR_L2_GSLC_BETA_V1.

`granules.json` claims to hold one granule per distinct configuration. This
script is how that claim was produced, and how it can be rechecked as the beta
collection grows: it pages through CMR, splits each SDS granule name into its
fields, and reports every (mode, polarization) pair it sees with one example.

Metadata search only -- no granule is opened or downloaded, and no credentials
are needed.
"""

from __future__ import annotations

import argparse
import collections
import json
import urllib.request

CMR = "https://cmr.earthdata.nasa.gov/search/granules.umm_json"
COLLECTION_CONCEPT_ID = "C2850259510-ASF"

# NISAR SDS granule name:
#   NISAR_L2_PR_GSLC_002_109_D_064_4005_DHDH_A_2025...
#   0     1  2  3    4   5   6 7   8    9    10
MODE_FIELD = 8
POL_FIELD = 9


def survey(pages: int, page_size: int) -> dict[str, list[str]]:
    """Return {config_key: [granule_id, ...]} across the first `pages` of CMR."""
    seen: dict[str, list[str]] = collections.defaultdict(list)
    for page in range(1, pages + 1):
        url = (
            f"{CMR}?collection_concept_id={COLLECTION_CONCEPT_ID}"
            f"&page_size={page_size}&page_num={page}"
        )
        with urllib.request.urlopen(url) as resp:
            payload = json.load(resp)
        if not payload["items"]:
            break
        for item in payload["items"]:
            name = item["umm"]["GranuleUR"]
            fields = name.split("_")
            seen[f"{fields[MODE_FIELD]}_{fields[POL_FIELD]}"].append(name)
    return dict(seen)


def main() -> None:
    """Print every (mode, polarization) configuration CMR reports, with an example."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pages", type=int, default=3)
    p.add_argument("--page-size", type=int, default=2000)
    args = p.parse_args()

    seen = survey(args.pages, args.page_size)
    total = sum(len(v) for v in seen.values())
    print(f"scanned {total} granules; {len(seen)} distinct configurations\n")
    print(f"{'config':<12} {'count':>6}  example")
    for config in sorted(seen):
        print(f"{config:<12} {len(seen[config]):>6}  {seen[config][0]}")


if __name__ == "__main__":
    main()
