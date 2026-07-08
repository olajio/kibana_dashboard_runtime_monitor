#!/usr/bin/env python3
"""Generate config/dashboards.generated.json from a saved-objects export.

Usage:
    python scripts/build_registry.py federal_overview.ndjson \
        --app federal_overview \
        --out config/dashboards.generated.json
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dhm.registry import build_registry, write_registry  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ndjson", help="Path to the Kibana saved-objects export")
    ap.add_argument("--app", default="federal_overview", help="Logical app name")
    ap.add_argument("--out", default="config/dashboards.generated.json")
    args = ap.parse_args()

    reg = build_registry(args.ndjson, app=args.app)
    write_registry(reg, args.out)

    data_panels = sum(d.data_panel_count for d in reg.dashboards)
    print(f"Wrote {args.out}")
    print(f"  app:               {reg.app}")
    print(f"  hub dashboard:     {reg.hub_dashboard_id}")
    print(f"  dashboards:        {reg.dashboard_count}")
    print(f"  data panels total: {data_panels}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
