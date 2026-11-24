#!/usr/bin/env python3
"""Per-dashboard load-time baseline from what the collector has already written, and the
per-dashboard limits to put in config/thresholds.yaml.

The global limits (degraded at 30 s, failed at 120 s) cannot describe dashboards that range from
0.2 s to 170 s, so each dashboard gets its own, judged against what it normally does.

A baseline must describe NORMAL behaviour, so these results are left out:
  - runs in which several dashboards had not-ok panels at the same time (a platform outage or
    blip: in the 15 to 23 Nov data, one run had every dashboard at the 180 s timeout);
  - results with not-ok panels (a broken panel changes the load time: Agency Details' errored
    panel failed after about 18 s);
  - results with a load error, and results at the timeout (the real time is unknown: it was cut off).
It reports percentiles (median, p95, p99), not the maximum, which one bad result would set.

Read-only: three small queries with the collector's key.

Usage:
    python scripts/baseline.py                       # the table
    python scripts/baseline.py --yaml > config/thresholds.yaml
    python scripts/baseline.py --days 14 --timeout-s 300
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import sys
from typing import Any, Dict, List, Optional

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dhm.config import load_settings, tls_verify  # noqa: E402
from dhm.secrets import describe_sources, resolve_connection  # noqa: E402

INDEX = "dashboard-health-monitor"
PERCENTS = [50, 95, 99]


def _window(days: int) -> Dict[str, Any]:
    return {"range": {"@timestamp": {"gte": f"now-{days}d"}}}


def build_outage_query(days: int, min_dashboards: int) -> Dict[str, Any]:
    """Runs in which at least `min_dashboards` dashboards had not-ok panels."""
    return {"size": 0, "query": _window(days), "aggs": {"runs": {
        "terms": {"field": "collector_run_id", "size": 10000},
        "aggs": {"bad": {"filter": {"range": {"panels_not_ok": {"gt": 0}}}},
                 "keep": {"bucket_selector": {"buckets_path": {"n": "bad>_count"},
                                              "script": f"params.n >= {int(min_dashboards)}"}}}}}}


def outage_run_ids(resp: Dict[str, Any]) -> List[str]:
    return [b["key"] for b in ((resp.get("aggregations") or {}).get("runs") or {}).get("buckets", [])]


def build_stats_query(days: int, timeout_s: float, exclude_runs: List[str]) -> Dict[str, Any]:
    must_not: List[Dict[str, Any]] = [{"exists": {"field": "load_error"}}]
    if exclude_runs:
        must_not.append({"terms": {"collector_run_id": exclude_runs}})
    return {
        "size": 0, "track_total_hits": True,
        "query": {"bool": {
            "filter": [_window(days), {"term": {"panels_not_ok": 0}},
                       {"range": {"load_time_ms": {"lt": int(timeout_s * 1000) - 1000}}}],
            "must_not": must_not}},
        "aggs": {"runs": {"cardinality": {"field": "collector_run_id"}},
                 "by_dashboard": {"terms": {"field": "dashboard_title", "size": 200},
                                  "aggs": {"load": {"percentiles": {"field": "load_time_ms", "percents": PERCENTS}}}}},
    }


def _ceil5(seconds: float) -> int:
    return int(math.ceil(seconds / 5.0) * 5)


def suggest(p95_s: float, timeout_s: float = 180, multiplier: float = 1.5) -> Optional[Dict[str, int]]:
    """Limits in ms for a dashboard whose p95 load time is `p95_s`.

    degraded = multiplier x p95, failed = twice that, both rounded UP to 5 s (so a fast dashboard
    never gets a limit under 5 s) and failed kept 10 s under the timeout. None when degraded would
    not fit under the timeout: the dashboard needs a longer timeout before it can have limits."""
    cap = int(timeout_s) - 10
    degraded = _ceil5(p95_s * multiplier)
    if degraded >= cap:
        return None
    failed = max(min(_ceil5(2 * degraded), cap), degraded + 5)
    return {"degraded_over_ms": degraded * 1000, "failed_over_ms": min(failed, cap) * 1000}


def summarise(resp: Dict[str, Any], timeout_s: float, multiplier: float) -> Dict[str, Any]:
    aggs = resp.get("aggregations") or {}
    rows: List[Dict[str, Any]] = []
    for b in (aggs.get("by_dashboard") or {}).get("buckets", []):
        pct = (b.get("load") or {}).get("values") or {}
        p50, p95, p99 = ((pct.get(f"{p}.0") or 0.0) / 1000.0 for p in PERCENTS)
        rows.append({"dashboard": b["key"], "results": b["doc_count"], "p50": p50, "p95": p95, "p99": p99,
                     "limits": suggest(p95, timeout_s, multiplier)})
    rows.sort(key=lambda r: -r["p95"])
    total = resp.get("hits", {}).get("total", {})
    return {"runs": int((aggs.get("runs") or {}).get("value") or 0),
            "results": total.get("value", 0) if isinstance(total, dict) else int(total or 0), "rows": rows}


def format_report(s: Dict[str, Any], days: int, min_runs: int, timeout_s: float, total_results: int,
                  excluded_runs: int, multiplier: float) -> str:
    out = [f"last {days} days: {total_results} results; {s['results']} used "
           f"({total_results - s['results']} left out: {excluded_runs} outage run(s), results with not-ok "
           f"panels, load errors, and results at the {timeout_s:g} s timeout)",
           f"runs contributing: {s['runs']}"]
    if s["runs"] < min_runs:
        out.append(f"NOT YET A BASELINE: {s['runs']} run(s), want at least {min_runs}, spread over "
                   f"business hours, nights and a weekend.")
    out += ["", f"{'dashboard':42} {'n':>5} {'p50 s':>7} {'p95 s':>7} {'p99 s':>7} {'degraded>':>10} {'failed>':>8}  note"]
    for r in s["rows"]:
        lim = r["limits"]
        if lim:
            d, f, note = f"{lim['degraded_over_ms'] / 1000:.0f}s", f"{lim['failed_over_ms'] / 1000:.0f}s", ""
        else:
            d = f = "-"
            note = f"p95 too close to the {timeout_s:g} s timeout: raise timeout_ms first"
        out.append(f"{r['dashboard'][:42]:42} {r['results']:>5} {r['p50']:7.1f} {r['p95']:7.1f} {r['p99']:7.1f} "
                   f"{d:>10} {f:>8}  {note}")
    out += ["", f"limits: degraded = {multiplier:g} x p95, failed = 2 x degraded (kept 10 s under the timeout), "
                f"rounded up to 5 s. `--yaml` prints them as config/thresholds.yaml."]
    return "\n".join(out)


def format_yaml(s: Dict[str, Any], days: int, timeout_s: float, multiplier: float, generated: str) -> str:
    lines = [f"# Per-dashboard load-time limits. Generated by scripts/baseline.py --yaml on {generated}",
             f"# from {days} days, {s['runs']} runs, {s['results']} results (outage runs, not-ok panels, load errors",
             f"# and timeouts left out). degraded = {multiplier:g} x p95, failed = 2 x degraded, rounded up to 5 s.",
             "# A dashboard not listed here uses the global limits. Review monthly: rerun the script and compare.",
             "thresholds:"]
    for r in s["rows"]:
        stats = f"p50 {r['p50']:.1f} s, p95 {r['p95']:.1f} s"
        if r["limits"]:
            lim = r["limits"]
            lines.append(f'  "{r["dashboard"]}": {{degraded_over_ms: {lim["degraded_over_ms"]}, '
                         f'failed_over_ms: {lim["failed_over_ms"]}}}   # {stats}')
        else:
            lines.append(f'  # "{r["dashboard"]}": {stats}, too close to the {timeout_s:g} s timeout: '
                         f'give it a longer timeout_ms, collect, then rerun')
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--settings", default="config/settings.yaml")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--min-runs", type=int, default=48)
    ap.add_argument("--timeout-s", type=float, default=180,
                    help="the collector's per-dashboard timeout, to leave out results cut off by it")
    ap.add_argument("--multiplier", type=float, default=1.5, help="degraded limit = multiplier x p95")
    ap.add_argument("--outage-dashboards", type=int, default=5,
                    help="leave out runs in which at least this many dashboards had not-ok panels")
    ap.add_argument("--yaml", action="store_true", help="print config/thresholds.yaml instead of the table")
    ap.add_argument("--es-api-key", default=None)
    ap.add_argument("--es-url", default=None)
    ap.add_argument("--aws-secret-id", default=None)
    args = ap.parse_args()

    settings = load_settings(args.settings)
    if args.aws_secret_id:
        settings.aws_secret_id = args.aws_secret_id
    sources = resolve_connection(settings, cli_es_api_key=args.es_api_key, cli_es_url=args.es_url)
    print(f"Connection: {describe_sources(sources)}", file=sys.stderr)
    if not settings.elasticsearch.base_url or not settings.elasticsearch.api_key:
        print("ERROR: no Elasticsearch URL or API key resolved.", file=sys.stderr)
        return 2
    base = settings.elasticsearch.base_url
    headers = {"Authorization": f"ApiKey {settings.elasticsearch.api_key}"}
    verify = tls_verify(settings, settings.elasticsearch.verify_tls)

    def search(body: Dict[str, Any]) -> Dict[str, Any]:
        resp = requests.post(f"{base}/{INDEX}/_search", headers=headers, json=body, verify=verify, timeout=120)
        if resp.status_code != 200:
            raise SystemExit(f"ERROR: HTTP {resp.status_code} from {INDEX}/_search")
        return resp.json()

    outage = outage_run_ids(search(build_outage_query(args.days, args.outage_dashboards)))
    total = search({"size": 0, "track_total_hits": True, "query": _window(args.days)})["hits"]["total"]["value"]
    summary = summarise(search(build_stats_query(args.days, args.timeout_s, outage)), args.timeout_s, args.multiplier)
    if args.yaml:
        print(format_yaml(summary, args.days, args.timeout_s, args.multiplier,
                          dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")), end="")
    else:
        print(format_report(summary, args.days, args.min_runs, args.timeout_s, total, len(outage), args.multiplier))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
