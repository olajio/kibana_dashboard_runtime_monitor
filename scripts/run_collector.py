#!/usr/bin/env python3
"""Run one collection cycle: load every dashboard, write results to ES.

API keys are resolved by precedence:
    --es-api-key      >  $DHM_ES_API_KEY      >  AWS Secrets Manager (elasticsearch.aws_secret_id)
    --kibana-api-key  >  $DHM_KIBANA_API_KEY  >  AWS Secrets Manager (kibana.auth.aws_secret_id)
The Kibana key falls back to the Elasticsearch key when not separately set
(common when one Elastic API key authorizes both).

So in test we pass the key(s) on the command line; in production we omit them and
they are read from AWS Secrets Manager.

Usage:
    # test env (Chrome, key on the command line)
    python scripts/run_collector.py --es-api-key "<id:key>" --dry-run --out run.json

    # production (Edge, key from AWS Secrets Manager)
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
from dhm.secrets import resolve_es_api_key, resolve_kibana_api_key  # noqa: E402


def _select_backend(backend: str):
    """Import the chosen backend lazily so only its dependency is required."""
    if backend == "selenium":
        from dhm.collector_selenium import run
    else:
        from dhm.collector import run
    return run


def _load_registry(settings) -> dict:
    """Get the dashboards to monitor, from the live API or the export file."""
    if settings.collector.registry_source == "api":
        from dhm.discovery import build_registry_from_api
        from dhm.registry import registry_to_dict

        reg = build_registry_from_api(settings, include_titles=settings.collector.include_titles)
        return registry_to_dict(reg)
    with open(settings.collector.registry_path) as f:
        import json as _json
        return _json.load(f)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--settings", default="config/settings.yaml")
    ap.add_argument("--dry-run", action="store_true", help="Do not write to ES")
    ap.add_argument("--out", help="Optional path to also save the raw documents")
    ap.add_argument("--es-api-key", default=None,
                    help="Elasticsearch API key (id:key). Overrides env/AWS.")
    ap.add_argument("--kibana-api-key", default=None,
                    help="Kibana API key. Overrides env/AWS. Defaults to the ES key.")
    args = ap.parse_args()

    settings = load_settings(args.settings)

    # Resolve the ES key (needed unless it's a pure dry run).
    settings.elasticsearch.api_key = resolve_es_api_key(
        settings, cli_value=args.es_api_key, env_value=os.environ.get("DHM_ES_API_KEY")
    )
    # Resolve the Kibana browser-auth key, falling back to the ES key.
    if settings.kibana.auth.method == "api_key":
        settings.kibana.auth.api_key = resolve_kibana_api_key(
            settings,
            cli_value=args.kibana_api_key,
            env_value=os.environ.get("DHM_KIBANA_API_KEY"),
            fallback=settings.elasticsearch.api_key,
        )

    if not args.dry_run and not settings.elasticsearch.api_key:
        print("ERROR: no Elasticsearch API key (pass --es-api-key, set DHM_ES_API_KEY, "
              "or configure elasticsearch.aws_secret_id).", file=sys.stderr)
        return 2

    registry = _load_registry(settings)

    print(f"Collecting {registry['dashboard_count']} dashboards for app "
          f"'{settings.app}' (cluster={settings.cluster}, space={settings.kibana_space}) "
          f"[backend={settings.collector.backend}, browser={settings.collector.browser_channel}, "
          f"registry={settings.collector.registry_source}]")
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
