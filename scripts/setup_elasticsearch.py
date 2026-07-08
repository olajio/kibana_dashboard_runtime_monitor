#!/usr/bin/env python3
"""Create the ILM policy and index template in Elasticsearch.

Run once per cluster before the first collection cycle.

Usage:
    python scripts/setup_elasticsearch.py --settings config/settings.yaml
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dhm.config import load_settings  # noqa: E402
from dhm.es_writer import apply_assets  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--settings", default="config/settings.yaml")
    args = ap.parse_args()
    settings = load_settings(args.settings)
    apply_assets(settings)
    print("Elasticsearch setup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
