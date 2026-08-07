#!/usr/bin/env python3
"""Create the ILM policy and index template in Elasticsearch.

Run once per cluster before the first collection cycle.

The Elasticsearch API key is resolved by precedence:
    --es-api-key  >  $DHM_ES_API_KEY  >  AWS Secrets Manager (elasticsearch.aws_secret_id)

So in test we pass --es-api-key; in production we omit it and it is read from
AWS Secrets Manager.

Usage:
    python scripts/setup_elasticsearch.py --settings config/settings.yaml --es-api-key "<id:key>"
    python scripts/setup_elasticsearch.py                                   # prod: key from AWS
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dhm.config import load_settings  # noqa: E402
from dhm.es_writer import apply_assets  # noqa: E402
from dhm.secrets import resolve_es_api_key  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--settings", default="config/settings.yaml")
    ap.add_argument("--es-api-key", default=None,
                    help="Elasticsearch API key (id:key). Overrides env/AWS.")
    args = ap.parse_args()

    settings = load_settings(args.settings)
    settings.elasticsearch.api_key = resolve_es_api_key(
        settings, cli_value=args.es_api_key, env_value=os.environ.get("DHM_ES_API_KEY")
    )
    if not settings.elasticsearch.api_key:
        print("ERROR: no Elasticsearch API key (pass --es-api-key, set DHM_ES_API_KEY, "
              "or configure elasticsearch.aws_secret_id).", file=sys.stderr)
        return 2

    apply_assets(settings)
    print("Elasticsearch setup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
