#!/usr/bin/env python3
"""Ask Elasticsearch what the COLLECTOR'S OWN credential can see on every data view behind
the monitored dashboards.

Why this exists. A person looking at CyHy Overview sees data in every panel, while the
collector's browser session shows 7 of those panels as `empty` for the whole observation
window (scripts/trace_dashboard.py reports STABLE, so timing is ruled out). The panels read
different indices, and in that session the ones reading cdm_cyhy_vuln and cdm_cyhy_host_scans
are empty while the one reading cdm_cyhy_vuln_trending is fine. That is what it looks like
when the credential cannot see some indices, or sees them with fewer documents. A panel
whose query is DENIED shows an error; one whose query silently returns nothing (document- or
field-level security, or a remote cluster that is not authorised) shows as empty. This
script separates those cases by counting documents per pattern as the collector sees them.

For each data-view pattern in the exported dashboards it prints the HTTP status and the
document count. Compare the counts with what you get for the same index in Kibana Dev Tools
(`GET <pattern>/_count`) while logged in as yourself: a pattern that shows 0 or an error here
and a real count there is the difference.

Read-only: `_security/_authenticate` and one `_count` per pattern. The credential itself is
never printed.

Usage:
    python scripts/check_access.py
    python scripts/check_access.py --patterns cdm_cyhy_vuln,cdm_cyhy_host_scans
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dhm.config import load_settings, tls_verify  # noqa: E402
from dhm.secrets import connection_warnings, describe_sources, resolve_connection  # noqa: E402

DEFAULT_EXPORT = os.path.join(os.path.dirname(__file__), "..", "federal_overview_dashboard.ndjson")


def parse_patterns(ndjson_path: str) -> List[str]:
    """Distinct data-view patterns from a Saved Objects export. A data view's title can
    hold several comma-separated patterns, which are probed one at a time."""
    found = set()
    with open(ndjson_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("type") == "index-pattern":
                title = (obj.get("attributes") or {}).get("title") or ""
                found.update(p.strip() for p in title.split(",") if p.strip())
    return sorted(found)


def _reason(resp: requests.Response) -> str:
    try:
        err = resp.json().get("error", {})
    except ValueError:
        return f"HTTP {resp.status_code}"
    if isinstance(err, dict):
        return err.get("type") or f"HTTP {resp.status_code}"
    return str(err)[:60]


def authenticate(session, base: str, headers: Dict[str, str], verify) -> Dict[str, Any]:
    """Who Elasticsearch thinks the collector is. Contains no secret."""
    resp = session.get(f"{base}/_security/_authenticate", headers=headers, verify=verify, timeout=30)
    if resp.status_code != 200:
        return {"error": _reason(resp), "status": resp.status_code}
    d = resp.json()
    return {
        "username": d.get("username"),
        "type": d.get("authentication_type"),
        "roles": d.get("roles") or [],
        "api_key": (d.get("api_key") or {}).get("name"),
        "realm": (d.get("authentication_realm") or {}).get("name"),
    }


def count_pattern(session, base: str, headers: Dict[str, str], verify, pattern: str) -> Dict[str, Any]:
    """Documents the credential can see in one pattern: {'status', 'count' | 'reason'}."""
    try:
        resp = session.get(f"{base}/{pattern}/_count", headers=headers, verify=verify, timeout=60)
    except requests.RequestException as exc:
        return {"status": 0, "reason": type(exc).__name__}
    if resp.status_code == 200:
        return {"status": 200, "count": resp.json().get("count")}
    return {"status": resp.status_code, "reason": _reason(resp)}


def classify(row: Dict[str, Any]) -> str:
    if row["status"] == 200:
        return "EMPTY" if not row.get("count") else "readable"
    if row["status"] in (401, 403):
        return "DENIED"
    return "ERROR"


def format_table(rows: Dict[str, Dict[str, Any]]) -> str:
    width = max((len(p) for p in rows), default=10)
    lines = [f"{'pattern'.ljust(width)}  {'http':>4}  {'result':<9} detail"]
    for pattern, r in rows.items():
        detail = f"{r['count']:,} docs" if r["status"] == 200 else r.get("reason", "")
        lines.append(f"{pattern.ljust(width)}  {r['status']:>4}  {classify(r):<9} {detail}")
    tally: Dict[str, int] = {}
    for r in rows.values():
        tally[classify(r)] = tally.get(classify(r), 0) + 1
    lines.append("")
    lines.append("summary: " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--settings", default="config/settings.yaml")
    ap.add_argument("--export", default=DEFAULT_EXPORT,
                    help="Saved Objects export to read the data-view patterns from")
    ap.add_argument("--patterns", default=None, help="comma-separated patterns, instead of the export's")
    ap.add_argument("--es-api-key", default=None)
    ap.add_argument("--es-url", default=None)
    ap.add_argument("--aws-secret-id", default=None)
    args = ap.parse_args()

    settings = load_settings(args.settings)
    if args.aws_secret_id:
        settings.aws_secret_id = args.aws_secret_id
    sources = resolve_connection(settings, cli_es_api_key=args.es_api_key, cli_es_url=args.es_url)
    print(f"Connection: {describe_sources(sources)}")
    for w in connection_warnings(settings):
        print(f"WARNING: {w}", file=sys.stderr)
    if not settings.elasticsearch.base_url or not settings.elasticsearch.api_key:
        print("ERROR: no Elasticsearch URL or API key resolved.", file=sys.stderr)
        return 2

    patterns = ([p.strip() for p in args.patterns.split(",") if p.strip()]
                if args.patterns else parse_patterns(args.export))
    base = settings.elasticsearch.base_url
    headers = {"Authorization": f"ApiKey {settings.elasticsearch.api_key}"}
    verify = tls_verify(settings, settings.elasticsearch.verify_tls)

    with requests.Session() as session:
        who = authenticate(session, base, headers, verify)
        if "error" in who:
            print(f"authenticate: FAILED ({who['error']}, HTTP {who['status']})")
        else:
            print(f"authenticated as: {who['username']}  type={who['type']}  realm={who['realm']}  "
                  f"api_key_name={who['api_key']!r}")
            print(f"roles: {who['roles'] or '(none listed)'}")
        print(f"\nprobing {len(patterns)} pattern(s)...\n")
        rows = {p: count_pattern(session, base, headers, verify, p) for p in patterns}
    print(format_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
