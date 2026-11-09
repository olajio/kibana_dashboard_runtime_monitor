#!/usr/bin/env python3
"""Per-dashboard load-time baseline from what the collector has already written.

The status thresholds (degraded at 30 s, failed at 120 s) are global, but the dashboards are
very different: Software Overview takes 90 to 170 s while HVA Software takes 0.2 s. This reads
`dashboard-health-monitor` with the collector's own key, summarises each dashboard's load time
(median, p95, p99, max) over a window, and suggests thresholds derived from what each one
actually does, so "degraded" means slower than usual instead of slower than 30 s.

Read-only: one aggregation query. With fewer than --min-runs runs the numbers are printed but
flagged as not yet a baseline. Run it after the collector has been on its schedule for a while;
a few back-to-back runs only show a warm cache.

Usage:
    python scripts/baseline.py
    python scripts/baseline.py --days 14 --min-runs 48
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from typing import Any, Dict, List

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dhm.config import load_settings, tls_verify  # noqa: E402
from dhm.secrets import describe_sources, resolve_connection  # noqa: E402

INDEX = "dashboard-health-monitor"


def build_query(days: int) -> Dict[str, Any]:
    return {
        "size": 0,
        "query": {"range": {"@timestamp": {"gte": f"now-{days}d"}}},
        "aggs": {
            "runs": {"cardinality": {"field": "collector_run_id"}},
            "by_dashboard": {
                "terms": {"field": "dashboard_title", "size": 200},
                "aggs": {
                    "load": {"percentiles": {"field": "load_time_ms", "percents": [50, 95, 99]}},
                    "slowest": {"max": {"field": "load_time_ms"}},
                    "not_ok": {"sum": {"field": "panels_not_ok"}},
                    "status": {"terms": {"field": "load_status", "size": 5}},
                },
            },
        },
    }


def _round_up(ms: float, step_ms: int = 5000) -> int:
    return int(math.ceil(ms / step_ms) * step_ms)


def suggest(p95_ms: float, p99_ms: float, max_ms: float) -> Dict[str, int]:
    """degraded: 1.5x the p95 (slower than this dashboard usually is). failed: twice that, and
    never below the slowest run seen. Both rounded up to 5 s. Suggestions, not decisions."""
    degraded = _round_up(p95_ms * 1.5)
    failed = _round_up(max(degraded * 2, max_ms * 1.25, p99_ms * 2))
    return {"degraded_over_ms": degraded, "failed_over_ms": failed}


def summarise(resp: Dict[str, Any]) -> Dict[str, Any]:
    aggs = resp.get("aggregations") or {}
    rows: List[Dict[str, Any]] = []
    for b in (aggs.get("by_dashboard") or {}).get("buckets", []):
        pct = (b.get("load") or {}).get("values") or {}
        p50, p95, p99 = (pct.get(k) or 0.0 for k in ("50.0", "95.0", "99.0"))
        mx = (b.get("slowest") or {}).get("value") or 0.0
        rows.append({
            "dashboard": b["key"], "runs": b["doc_count"], "p50": p50, "p95": p95, "p99": p99,
            "max": mx, "not_ok": int((b.get("not_ok") or {}).get("value") or 0),
            "status": {s["key"]: s["doc_count"] for s in (b.get("status") or {}).get("buckets", [])},
            **suggest(p95, p99, mx),
        })
    rows.sort(key=lambda r: -r["p95"])
    return {"runs": int((aggs.get("runs") or {}).get("value") or 0), "rows": rows}


def format_report(s: Dict[str, Any], days: int, min_runs: int) -> str:
    out = [f"collector runs in the last {days} days: {s['runs']}"]
    if s["runs"] < min_runs:
        out.append(f"NOT YET A BASELINE: {s['runs']} run(s), want at least {min_runs}, spread over "
                   f"business hours, nights and a weekend. Back-to-back runs only show a warm cache.")
    out += ["", f"{'dashboard':42} {'runs':>4} {'p50 s':>6} {'p95 s':>6} {'max s':>6} {'not_ok':>6} "
            f"{'degraded>':>9} {'failed>':>8}  statuses"]
    for r in s["rows"]:
        out.append(f"{r['dashboard'][:42]:42} {r['runs']:>4} {r['p50']/1000:6.1f} {r['p95']/1000:6.1f} "
                   f"{r['max']/1000:6.1f} {r['not_ok']:>6} {r['degraded_over_ms']/1000:8.0f}s "
                   f"{r['failed_over_ms']/1000:7.0f}s  {r['status']}")
    out += ["", "degraded>/failed> are SUGGESTIONS (1.5x p95, then twice that), per dashboard. The",
            "collector currently applies one global pair under `collector:` in config/settings.yaml."]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--settings", default="config/settings.yaml")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--min-runs", type=int, default=48)
    ap.add_argument("--es-api-key", default=None)
    ap.add_argument("--es-url", default=None)
    ap.add_argument("--aws-secret-id", default=None)
    args = ap.parse_args()

    settings = load_settings(args.settings)
    if args.aws_secret_id:
        settings.aws_secret_id = args.aws_secret_id
    sources = resolve_connection(settings, cli_es_api_key=args.es_api_key, cli_es_url=args.es_url)
    print(f"Connection: {describe_sources(sources)}")
    if not settings.elasticsearch.base_url or not settings.elasticsearch.api_key:
        print("ERROR: no Elasticsearch URL or API key resolved.", file=sys.stderr)
        return 2
    resp = requests.post(
        f"{settings.elasticsearch.base_url}/{INDEX}/_search",
        headers={"Authorization": f"ApiKey {settings.elasticsearch.api_key}"},
        json=build_query(args.days), verify=tls_verify(settings, settings.elasticsearch.verify_tls),
        timeout=120)
    if resp.status_code != 200:
        print(f"ERROR: HTTP {resp.status_code} from {INDEX}/_search", file=sys.stderr)
        return 1
    print(format_report(summarise(resp.json()), args.days, args.min_runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
