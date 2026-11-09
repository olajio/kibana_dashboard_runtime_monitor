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
from dhm.secrets import (  # noqa: E402
    connection_warnings, describe_sources, resolve_connection, secret_origin,
)

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
        body = resp.json()
        err = body.get("error", {})
        # Elastic Cloud's proxy answers {"ok": false, "message": "Unknown resource."} for a
        # deployment endpoint that does not exist; there is no "error" key to read.
        if not err and isinstance(body.get("message"), str):
            return f"HTTP {resp.status_code} {body['message'][:60]!r}"
    except ValueError:
        # Not Elasticsearch's JSON: a proxy, a load balancer or a web app answered.
        # Say what, so a wrong URL can be told apart from a missing permission.
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0] or "no content-type"
        snippet = " ".join((resp.text or "")[:80].split())
        return f"HTTP {resp.status_code} not-elasticsearch ({ctype}) {snippet!r}"
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


WRITE_INDEX = "dashboard-health-monitor"
WRITE_PRIVILEGES = ["create_doc", "auto_configure", "create_index"]


def check_write_access(session, base: str, headers: Dict[str, str], verify,
                       index: str = WRITE_INDEX) -> Dict[str, Any]:
    """Can the credential create and write the monitor's data stream? An API key can never
    hold more than its owner, so a key whose role lists these privileges can still be refused
    when the account that created it does not have them. Asking Elasticsearch directly
    (`_has_privileges` on itself) shows the effective answer before a real write fails."""
    body = {"index": [{"names": [index], "privileges": WRITE_PRIVILEGES}]}
    try:
        resp = session.post(f"{base}/_security/user/_has_privileges", headers=headers,
                            json=body, verify=verify, timeout=30)
    except requests.RequestException as exc:
        return {"error": type(exc).__name__}
    if resp.status_code != 200:
        return {"error": _reason(resp)}
    d = resp.json()
    have = (d.get("index") or {}).get(index) or {}
    return {"ok": bool(d.get("has_all_requested")),
            "missing": [p for p in WRITE_PRIVILEGES if not have.get(p)]}


def data_stream_status(session, base: str, headers: Dict[str, str], verify,
                       name: str = WRITE_INDEX, timeout: int = 60) -> Dict[str, Any]:
    """What the first write created: the data stream, the template and ILM policy it picked up,
    and how many documents it holds. Read with the collector's own key (it can read and view
    metadata on `dashboard-health-monitor*`), because a person's account usually cannot."""
    try:
        r = session.get(f"{base}/_data_stream/{name}", headers=headers, verify=verify, timeout=timeout)
    except requests.RequestException as exc:
        return {"error": type(exc).__name__}
    if r.status_code == 404:
        return {"exists": False}
    if r.status_code != 200:
        return {"error": _reason(r)}
    streams = r.json().get("data_streams") or []
    if not streams:
        return {"exists": False}
    ds = streams[0]
    out = {"exists": True, "template": ds.get("template"), "ilm_policy": ds.get("ilm_policy"),
           "backing_indices": len(ds.get("indices") or [])}
    c = count_pattern(session, base, headers, verify, name, timeout)
    out["count"] = c.get("count") if c.get("status") == 200 else None
    return out


def cluster_identity(session, base: str, headers: Dict[str, str], verify) -> Dict[str, Any]:
    """Which cluster answered: name and uuid (`GET /`). Compare with the same call in the
    Dev Tools you normally use; different uuids mean the two sessions are on different
    clusters, whatever the URLs look like."""
    try:
        resp = session.get(f"{base}/", headers=headers, verify=verify, timeout=30)
    except requests.RequestException as exc:
        return {"error": type(exc).__name__}
    if resp.status_code != 200:
        return {"error": _reason(resp)}
    d = resp.json()
    return {"cluster_name": d.get("cluster_name"), "cluster_uuid": d.get("cluster_uuid"),
            "version": (d.get("version") or {}).get("number")}


def count_pattern(session, base: str, headers: Dict[str, str], verify, pattern: str,
                  timeout: int = 60) -> Dict[str, Any]:
    """Documents the credential can see in one pattern: {'status', 'count' | 'reason'}."""
    try:
        resp = session.get(f"{base}/{pattern}/_count", headers=headers, verify=verify, timeout=timeout)
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
    ap.add_argument("--verify-write", action="store_true",
                    help="after a first write, show the data stream, its template and ILM policy, "
                         "and its document count (and skip the per-pattern probe)")
    ap.add_argument("--timeout", type=int, default=180,
                    help="seconds to wait for each count; trending patterns across ~100 remote "
                         "clusters hold billions of documents and can exceed 60 s")
    args = ap.parse_args()

    settings = load_settings(args.settings)
    if args.aws_secret_id:
        settings.aws_secret_id = args.aws_secret_id
    sources = resolve_connection(settings, cli_es_api_key=args.es_api_key, cli_es_url=args.es_url)
    print(f"Connection: {describe_sources(sources)}")
    origin = secret_origin(settings.aws_secret_id)
    if origin:
        print(f"Secret read: {origin}")
    for w in connection_warnings(settings):
        print(f"WARNING: {w}", file=sys.stderr)
    if not settings.elasticsearch.base_url or not settings.elasticsearch.api_key:
        print("ERROR: no Elasticsearch URL or API key resolved.", file=sys.stderr)
        return 2

    patterns = ([p.strip() for p in args.patterns.split(",") if p.strip()]
                if args.patterns else parse_patterns(args.export))
    base = settings.elasticsearch.base_url
    print(f"Elasticsearch URL: {base}")
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
        write = check_write_access(session, base, headers, verify)
        if "error" in write:
            print(f"write access to {WRITE_INDEX}: unavailable ({write['error']})")
        elif write["ok"]:
            print(f"write access to {WRITE_INDEX}: yes ({', '.join(WRITE_PRIVILEGES)})")
        else:
            print(f"write access to {WRITE_INDEX}: NO, missing {', '.join(write['missing'])}. The "
                  f"collector's first write will fail. A key holds only what its OWNER holds.")
        cluster = cluster_identity(session, base, headers, verify)
        if "error" in cluster:
            print(f"cluster: unavailable ({cluster['error']})")
        else:
            print(f"cluster: name={cluster['cluster_name']}  uuid={cluster['cluster_uuid']}  "
                  f"version={cluster['version']}")
        if args.verify_write:
            ds = data_stream_status(session, base, headers, verify)
            if "error" in ds:
                print(f"\ndata stream {WRITE_INDEX}: unavailable ({ds['error']})")
            elif not ds["exists"]:
                print(f"\ndata stream {WRITE_INDEX}: does not exist yet (no write has happened)")
            else:
                print(f"\ndata stream {WRITE_INDEX}: template={ds['template']}  "
                      f"ilm_policy={ds['ilm_policy']}  backing_indices={ds['backing_indices']}  "
                      f"documents={ds['count']}")
            return 0
        print(f"\nprobing {len(patterns)} pattern(s)...\n")
        rows = {p: count_pattern(session, base, headers, verify, p, args.timeout) for p in patterns}
    print(format_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
