#!/usr/bin/env python3
"""Run one collection cycle: load every dashboard, write results to ES.

The two URLs resolve, most explicit first:

    1. command-line flag         --kibana-url / --es-url   (for debugging)
    2. AWS connection bundle     one JSON secret named by `aws_secret_id`
                                 (fields: kibana_url, elastic_url)
    3. config/settings.yaml

There is deliberately no environment variable for the URLs — the endpoints belong to
the AWS secret, so a stale export cannot redirect a run.

The API key(s) keep an env step, because passing the key by hand is how the test
environment runs:

    1. command-line flag         --es-api-key / --kibana-api-key
    2. environment variable      DHM_ES_API_KEY / DHM_KIBANA_API_KEY
    3. AWS connection bundle     (field: ans_dashboard_health_monitor)
    4. value-specific AWS secret elasticsearch.aws_secret_id / kibana.auth.aws_secret_id
    5. config/settings.yaml

The Kibana key falls back to the Elasticsearch key when not separately set
(common when one Elastic API key authorizes both).

So in test we pass the key on the command line and keep the URLs in
settings.yaml; in production we set only `aws_secret_id` (plus `aws_region`,
default us-east-1) and pass nothing at all.

Usage:
    # test env (Chrome, key on the command line)
    python scripts/run_collector.py --es-api-key "<id:key>" --dry-run --out run.json

    # production (Edge; URLs and key all from AWS Secrets Manager)
    python scripts/run_collector.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dhm.config import load_settings  # noqa: E402
from dhm.es_writer import bulk_index  # noqa: E402
from dhm.secrets import (  # noqa: E402
    connection_warnings,
    describe_sources,
    resolve_connection,
)


def _select_backend(backend: str):
    """Import the chosen backend lazily so only its dependency is required."""
    if backend == "selenium":
        from dhm.collector_selenium import run
    else:
        from dhm.collector import run
    return run


def _load_registry(settings) -> dict:
    """Get the full registry (from the live API or the export file), then narrow it
    to the dashboards we monitor. Default: the Federal Overview hub plus every
    dashboard reachable from its navigation."""
    from dhm.registry import select_registry

    if settings.collector.registry_source == "api":
        from dhm.discovery import build_registry_from_api
        from dhm.registry import registry_to_dict

        reg = registry_to_dict(build_registry_from_api(settings))
    else:
        with open(settings.collector.registry_path) as f:
            reg = json.load(f)

    return select_registry(
        reg,
        settings.collector.selection,
        settings.collector.hub_title,
        settings.collector.include_titles,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--settings", default="config/settings.yaml")
    ap.add_argument("--dry-run", action="store_true", help="Do not write to ES")
    ap.add_argument("--out", help="Optional path to also save the raw documents")
    ap.add_argument("--es-api-key", default=None,
                    help="Elasticsearch API key (id:key). Overrides env/AWS.")
    ap.add_argument("--kibana-api-key", default=None,
                    help="Kibana API key. Overrides env/AWS. Defaults to the ES key.")
    ap.add_argument("--kibana-url", default=None,
                    help="Kibana base URL. Overrides env/AWS/settings.")
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

    # Resolve URLs and keys together: the browser and Saved Objects discovery both
    # need kibana.base_url before we can build the registry below.
    sources = resolve_connection(
        settings,
        cli_es_api_key=args.es_api_key,
        cli_kibana_api_key=args.kibana_api_key,
        cli_kibana_url=args.kibana_url,
        cli_es_url=args.es_url,
    )
    print(f"Connection: {describe_sources(sources)}")
    for warning in connection_warnings(settings):
        print(f"WARNING: {warning}", file=sys.stderr)

    if not settings.kibana.base_url:
        print("ERROR: no Kibana URL. Put 'kibana_url' in the AWS secret named by "
              "aws_secret_id, set kibana.base_url in settings.yaml, or pass "
              "--kibana-url.", file=sys.stderr)
        return 2
    if not args.dry_run and not settings.elasticsearch.base_url:
        print("ERROR: no Elasticsearch URL. Put 'elastic_url' in the AWS secret named "
              "by aws_secret_id, set elasticsearch.base_url in settings.yaml, or pass "
              "--es-url.", file=sys.stderr)
        return 2
    if not args.dry_run and not settings.elasticsearch.api_key:
        print("ERROR: no Elasticsearch API key (pass --es-api-key, set DHM_ES_API_KEY, "
              "or configure aws_secret_id / elasticsearch.aws_secret_id).", file=sys.stderr)
        return 2

    # Check the browser BEFORE discovery: a missing browser used to surface only
    # after a full Saved Objects crawl had already run.
    from dhm.browsers import find_channel, describe_availability
    channel = (settings.collector.browser_channel or "").strip().lower()
    if channel and channel not in ("chromium", "bundled") and not find_channel(channel):
        print(f"WARNING: no '{channel}' browser found on this host. Visible browsers:\n"
              f"{describe_availability()}\n"
              f"         Attempting the launch anyway — Playwright may still find it.",
              file=sys.stderr)

    registry = _load_registry(settings)

    sel = settings.collector.selection
    if sel == "linked":
        scope = f"linked (hub='{settings.collector.hub_title}' + reachable)"
    elif sel == "titles":
        scope = f"titles={settings.collector.include_titles}"
    else:
        scope = "all in space"
    print(f"Collecting {registry['dashboard_count']} dashboards for app "
          f"'{settings.app}' (cluster={settings.cluster}, space={settings.kibana_space}) "
          f"[backend={settings.collector.backend}, browser={settings.collector.browser_channel}, "
          f"registry={settings.collector.registry_source}, selection={scope}]")
    if registry["dashboard_count"] == 0:
        print("WARNING: selection matched no dashboards; nothing to collect. "
              "Check selection/hub_title/include_titles.", file=sys.stderr)
    run = _select_backend(settings.collector.backend)
    docs = run(settings, registry)

    if args.out:
        with open(args.out, "w") as f:
            json.dump(docs, f, indent=2)
        print(f"Saved raw documents to {args.out}")

    if args.dry_run:
        print("--dry-run: not writing to Elasticsearch")
        return 0

    result = bulk_index(settings, docs)
    print(f"Indexed {result['indexed']} documents into {settings.elasticsearch.index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
