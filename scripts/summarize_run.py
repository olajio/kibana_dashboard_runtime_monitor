#!/usr/bin/env python3
"""Explain a run.json: WHY dashboards are degraded or failed, not just that they are.

The collector prints one line per dashboard with `ok=` and `not_ok=` counts, but not_ok
lumps together panels that are empty, missing, timed out and errored, and those mean
very different things:

    empty    rendered, but with no data   -> usually a data-side signal, or a panel
                                             that is not really a data panel
    missing  the registry expects it, the page never showed it
    timeout  still loading when the dashboard's time budget ran out
    error    Kibana showed an error state

The second table lists panel titles that fail on MORE THAN ONE dashboard. A title that
recurs is more likely a false positive (a navigation widget classified as a data panel,
say) than a dozen independent data problems.

Usage:
    python scripts/summarize_run.py run.json
"""
from __future__ import annotations

import argparse
import collections
import json
import sys


def summarize(docs, out=sys.stdout):
    print("== per dashboard: status of the panels that are NOT ok ==", file=out)
    for d in docs:
        bad = collections.Counter(
            p["render_status"] for p in d.get("panels", []) if p["render_status"] != "ok"
        )
        print(f'{d["dashboard_title"][:34]:34} {d["load_status"]:9} {dict(bad) or "-"}', file=out)

    seen = collections.defaultdict(list)
    for d in docs:
        for p in d.get("panels", []):
            if p["render_status"] != "ok":
                seen[p["panel_title"]].append(d["dashboard_title"])
    repeated = sorted(((t, ds) for t, ds in seen.items() if len(ds) > 1),
                      key=lambda kv: -len(kv[1]))

    print("\n== panel titles that are not ok on MORE THAN ONE dashboard ==", file=out)
    if not repeated:
        print("(none)", file=out)
    for t, ds in repeated:
        print(f"{len(ds):>3}x  {t}", file=out)
    return repeated


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", default="run.json", help="file written by --out")
    args = ap.parse_args()
    with open(args.path) as f:
        summarize(json.load(f))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
