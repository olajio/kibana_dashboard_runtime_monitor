#!/usr/bin/env python3
"""Create the ILM policy and index template in Elasticsearch.

Run once per cluster before the first collection cycle.

The Elasticsearch URL and API key are resolved by precedence:
    --es-url      >  AWS connection bundle (elastic_url)  >  settings.yaml
                     (no env step: the endpoint belongs to the secret)
    --es-api-key  >  $DHM_ES_API_KEY  >  AWS connection bundle
                     (ans_dashboard_health_monitor)
                  >  elasticsearch.aws_secret_id  >  settings.yaml

So in test we pass --es-api-key; in production we set only `aws_secret_id` and
both the URL and the key come from that one AWS Secrets Manager secret.

Usage:
    python scripts/setup_elasticsearch.py --settings config/settings.yaml --es-api-key "<id:key>"
    python scripts/setup_elasticsearch.py                        # prod: URL + key from AWS
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dhm.config import load_settings  # noqa: E402
from dhm.es_writer import apply_assets  # noqa: E402
from dhm.secrets import connection_warnings, describe_sources, resolve_connection  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--settings", default="config/settings.yaml")
    ap.add_argument("--es-api-key", default=None,
                    help="Elasticsearch API key (id:key). Overrides env/AWS.")
    ap.add_argument("--es-url", default=None,
                    help="Elasticsearch base URL. Overrides env/AWS/settings.")
    ap.add_argument("--aws-secret-id", default=None,
                    help="AWS Secrets Manager secret holding the connection bundle "
                         "(JSON: kibana_url, elastic_url, ans_dashboard_health_monitor).")
    ap.add_argument("--aws-region", default=None,
                    help="AWS region for Secrets Manager (default us-east-1).")
    args = ap.parse_args()

    settings = load_settings(args.settings)
    if args.aws_secret_id:
        settings.aws_secret_id = args.aws_secret_id
    if args.aws_region:
        settings.aws_region = args.aws_region

    sources = resolve_connection(
        settings, cli_es_api_key=args.es_api_key, cli_es_url=args.es_url
    )
    print(f"Connection: {describe_sources(sources)}")
    for warning in connection_warnings(settings):
        print(f"WARNING: {warning}", file=sys.stderr)

    if not settings.elasticsearch.base_url:
        print("ERROR: no Elasticsearch URL. Put 'elastic_url' in the AWS secret named "
              "by aws_secret_id, set elasticsearch.base_url in settings.yaml, or pass "
              "--es-url.", file=sys.stderr)
        return 2
    if not settings.elasticsearch.api_key:
        print("ERROR: no Elasticsearch API key (pass --es-api-key, set DHM_ES_API_KEY, "
              "or configure aws_secret_id / elasticsearch.aws_secret_id).", file=sys.stderr)
        return 2

    apply_assets(settings)
    print("Elasticsearch setup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
