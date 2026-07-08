#!/usr/bin/env python3
"""Run one collection cycle: load every dashboard, write results to ES.

Usage:
    python scripts/run_collector.py                 # load settings + registry, write to ES
    python scripts/run_collector.py --dry-run       # collect but print docs instead of writing
    python scripts/run_collector.py --out run.json  # also save the raw docs locally
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dhm.config import load_settings  # noqa: E402
from dhm.collector import run  # noqa: E402
from dhm.es_writer import bulk_index  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--settings", default="config/settings.yaml")
    ap.add_argument("--dry-run", action="store_true", help="Do not write to ES")
    ap.add_argument("--out", help="Optional path to also save the raw documents")
    args = ap.parse_args()

    settings = load_settings(args.settings)
    with open(settings.collector.registry_path) as f:
        registry = json.load(f)

    print(f"Collecting {registry['dashboard_count']} dashboards for app "
          f"'{settings.app}' (cluster={settings.cluster}, space={settings.kibana_space})")
    docs = run(settings, registry)

    if args.out:
        with open(args.out, "w") as f:
            json.dump(docs, f, indent=2)
        print(f"Saved raw documents to {args.out}")

    if args.dry_run:
        print("--dry-run: not writing to Elasticsearch")
        return 0

    result = bulk_index(settings, docs)
    print(f"Indexed {len(docs)} documents into {settings.elasticsearch.index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
