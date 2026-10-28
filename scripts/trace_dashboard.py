#!/usr/bin/env python3
"""Trace ONE dashboard under the collector's own conditions, and report whether panels
that look `empty` at the moment the collector stops waiting are still empty afterwards.

Why this exists. On CyHy Overview the collector reports 5 panels as `empty`, but a person
looking at the same dashboard sees data in all of them. The collector stops waiting the
instant every panel counts as resolved (rendered, errored OR showing an empty placeholder)
and classifies from that snapshot, so an empty placeholder that is only a loading state
would be frozen into the result. This script tests that directly.

It is NOT `debug_spike.py`. That script opens a fresh page, so every run pays Kibana's
full cold start (about 10 s on the production host) and cannot say anything about
settling. The collector reuses ONE page and moves between dashboards by changing the URL
fragment, which Kibana handles without rebooting. This script does the same:

  1. load the dashboard that precedes the target in the collector's order (warm-up);
  2. navigate to the target the way the collector does;
  3. poll with the collector's own panel-state reader, using the collector's own stop
     condition to decide when it WOULD have stopped;
  4. keep watching for --window-ms after that, and compare.

Read-only: it opens a browser against Kibana and reads the page. Nothing is written to
Elasticsearch, and nothing is written to disk. Output contains panel titles only.

Usage:
    python scripts/trace_dashboard.py --dashboard-title "CyHy Overview"
    python scripts/trace_dashboard.py --dashboard-title "CyHy Overview" --repeat 3
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dhm.collect_core import dashboard_url, panel_key  # noqa: E402
from dhm.config import load_settings  # noqa: E402
from dhm.render_detection import classify_panel, is_resolved  # noqa: E402
from dhm.secrets import connection_warnings, describe_sources, resolve_connection  # noqa: E402

Sample = Tuple[int, List[Dict[str, Any]]]   # (elapsed_ms since navigation, panel states)

WARMUP_CAP_MS = 30000   # longest we wait for the warm-up dashboard to settle


# --------------------------------------------------------------------------- analysis
def _status_map(states: List[Dict[str, Any]]) -> Dict[str, Tuple[str, str]]:
    """key -> (title, status) using the collector's own classifier."""
    return {
        panel_key(s): ((s.get("title") or "").strip() or f"(panel #{s.get('index')})",
                       classify_panel(s))
        for s in states
    }


def collector_would_stop(states: List[Dict[str, Any]], expected_min: int) -> bool:
    """Exactly the collector's exit condition (collect_core.collect_dashboard)."""
    return bool(states) and len(states) >= expected_min and all(is_resolved(s) for s in states)


def analyze(samples: List[Sample], expected_min: int) -> Dict[str, Any]:
    """Compare the panel states at the moment the collector would have stopped with the
    states at the end of the observation window. Pure; no browser."""
    exit_idx: Optional[int] = None
    for i, (_t, states) in enumerate(samples):
        if collector_would_stop(states, expected_min):
            exit_idx = i
            break

    out: Dict[str, Any] = {
        "samples": len(samples),
        "window_ms": samples[-1][0] if samples else 0,
        "exit_ms": None,
        "at_exit": {},
        "final": {},
        "changed": [],
    }
    if samples:
        out["final"] = _status_map(samples[-1][1])
    if exit_idx is None:
        return out

    out["exit_ms"] = samples[exit_idx][0]
    at_exit = _status_map(samples[exit_idx][1])
    final = out["final"]
    out["at_exit"] = at_exit

    for key, (title, before) in at_exit.items():
        after = final.get(key, (title, "gone"))[1]
        if after == before:
            continue
        # When did it reach its final state and stay there? = the first sample after the
        # LAST sample in which it was still something else.
        history = [(t, _status_map(st).get(key, (title, "gone"))[1]) for t, st in samples[exit_idx:]]
        last_other = max(i for i, (_t, s) in enumerate(history) if s != after)
        settled_ms = history[last_other + 1][0] if last_other + 1 < len(history) else history[-1][0]
        out["changed"].append({"title": title, "at_exit": before, "final": after, "settled_ms": settled_ms})
    return out


def verdict(result: Dict[str, Any]) -> str:
    if result["exit_ms"] is None:
        return ("NEVER STOPPED: the collector's stop condition was not met within the window, "
                "so the real collector would have run to its timeout on this dashboard.")
    not_ok_at_exit = [t for t, s in result["at_exit"].values() if s != "ok"]
    recovered = [c for c in result["changed"] if c["at_exit"] != "ok" and c["final"] == "ok"]
    worsened = [c for c in result["changed"] if c["at_exit"] == "ok" and c["final"] != "ok"]
    if recovered:
        return (f"READ TOO EARLY: {len(recovered)} panel(s) were not ok when the collector would "
                f"have stopped but were ok by the end of the window. Those 'empty' verdicts are "
                f"a loading state, not a result.")
    if worsened:
        return (f"UNSTABLE: {len(worsened)} panel(s) were ok at the stop moment and not ok later.")
    if not_ok_at_exit:
        return (f"STABLE: {len(not_ok_at_exit)} panel(s) were not ok at the stop moment and stayed "
                f"that way for the whole window. Timing does not explain them.")
    return "ALL OK at the stop moment and at the end of the window."


def format_report(result: Dict[str, Any], title: str, expected_min: int) -> str:
    lines = [f"--- trace: {title} (collector expects {expected_min} data panels) ---"]
    if result["exit_ms"] is None:
        lines.append(f"collector would NOT have stopped within {result['window_ms']} ms")
    else:
        def tally(m):
            c = collections.Counter(s for _t, s in m.values())
            return ", ".join(f"{k}={v}" for k, v in sorted(c.items())) or "no panels"
        lines.append(f"collector would have stopped at {result['exit_ms']} ms   -> {tally(result['at_exit'])}")
        lines.append(f"end of window ({result['window_ms']} ms)                   -> {tally(result['final'])}")
        if result["changed"]:
            lines.append("panels that changed state AFTER the collector would have stopped:")
            for c in sorted(result["changed"], key=lambda c: c["settled_ms"]):
                lines.append(f"    {c['title'][:56]:56} {c['at_exit']:>8} -> {c['final']:<8} (by {c['settled_ms']} ms)")
        else:
            lines.append("no panel changed state after the collector would have stopped")
        # Always say what is STILL not ok at the end: those are the ones timing does not explain.
        still = [(t, s) for t, s in result["final"].values() if s != "ok"]
        if still:
            lines.append("still not ok at the end of the window:")
            for t, s in still:
                lines.append(f"    {t[:56]:56} {s}")
    lines.append(verdict(result))
    return "\n".join(lines)


# --------------------------------------------------------------------------- browser
def _selected_registry(settings) -> Dict[str, Any]:
    from dhm.registry import registry_to_dict, select_registry
    if settings.collector.registry_source == "api":
        from dhm.discovery import build_registry_from_api
        reg = registry_to_dict(build_registry_from_api(settings))
    else:
        with open(settings.collector.registry_path) as f:
            reg = json.load(f)
    return select_registry(reg, settings.collector.selection, settings.collector.hub_title,
                           settings.collector.include_titles)


def _find(dashboards: List[Dict[str, Any]], title: str) -> int:
    for i, d in enumerate(dashboards):
        if (d.get("title") or "").strip().lower() == title.strip().lower():
            return i
    raise SystemExit(f"Dashboard not found in the monitored set: {title!r}. "
                     f"Available: {sorted(d.get('title') for d in dashboards)}")


def _watch(driver, expected_min: int, timeout_ms: int, poll_ms: int, window_ms: Optional[int]) -> List[Sample]:
    """Poll the page. window_ms=None -> stop when the collector's own condition is met
    (used for the warm-up); otherwise observe for the full window."""
    samples: List[Sample] = []
    t0 = time.monotonic()
    while True:
        elapsed = int((time.monotonic() - t0) * 1000)
        states = driver.read_panel_states() or []
        samples.append((elapsed, states))
        if window_ms is None:
            if collector_would_stop(states, expected_min) or elapsed >= timeout_ms:
                return samples
        elif elapsed >= window_ms:
            return samples
        time.sleep(poll_ms / 1000.0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--settings", default="config/settings.yaml")
    ap.add_argument("--dashboard-title", required=True, help="the dashboard to trace")
    ap.add_argument("--after", default=None,
                    help="dashboard to load first (default: the one before the target in the "
                         "collector's own order, so the page is warm as it is in a real run)")
    ap.add_argument("--window-ms", type=int, default=30000,
                    help="how long to keep watching after navigating (default 30000)")
    ap.add_argument("--repeat", type=int, default=1, help="repeat the whole trace N times")
    ap.add_argument("--es-api-key", default=None)
    ap.add_argument("--kibana-api-key", default=None)
    ap.add_argument("--kibana-url", default=None)
    ap.add_argument("--aws-secret-id", default=None)
    args = ap.parse_args()

    settings = load_settings(args.settings)
    if args.aws_secret_id:
        settings.aws_secret_id = args.aws_secret_id
    sources = resolve_connection(settings, cli_es_api_key=args.es_api_key,
                                 cli_kibana_api_key=args.kibana_api_key, cli_kibana_url=args.kibana_url)
    print(f"Connection: {describe_sources(sources)}")
    for w in connection_warnings(settings):
        print(f"WARNING: {w}", file=sys.stderr)

    dashboards = _selected_registry(settings).get("dashboards", [])
    ti = _find(dashboards, args.dashboard_title)
    target = dashboards[ti]
    if args.after:
        warm = dashboards[_find(dashboards, args.after)]
    else:
        warm = dashboards[ti - 1] if ti > 0 else dashboards[min(1, len(dashboards) - 1)]
    expected_min = int(target.get("data_panel_count") or 0)
    warm_min = int(warm.get("data_panel_count") or 0)
    timeout_ms = settings.collector.dashboard_timeout_ms
    poll_ms = settings.collector.poll_interval_ms
    print(f"warm-up: {warm['title']!r}  ->  target: {target['title']!r}  "
          f"(window {args.window_ms} ms, poll {poll_ms} ms)\n")

    from playwright.sync_api import sync_playwright   # lazy: tests import this module without it
    from dhm.collector import PlaywrightDriver, _new_context

    with sync_playwright() as pw:
        launch = {"headless": settings.collector.headless}
        channel = (settings.collector.browser_channel or "").strip().lower()
        executable = (settings.collector.browser_executable or "").strip()
        if executable:
            launch["executable_path"] = executable
        elif channel and channel not in ("chromium", "bundled"):
            launch["channel"] = channel
        try:
            browser = pw.chromium.launch(**launch)
        except Exception as exc:
            from dhm.browsers import launch_help
            raise SystemExit(f"{launch_help(channel or 'chromium')}\n\n  Playwright said: {exc}")
        context = _new_context(browser, settings)
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        driver = PlaywrightDriver(page)
        try:
            for n in range(1, args.repeat + 1):
                driver.goto(dashboard_url(settings, warm["dashboard_id"]), timeout_ms)
                driver.wait_for_panel(timeout_ms)
                # Let the warm-up dashboard finish, but only up to WARMUP_CAP_MS: the point is a
                # warm page, not a completed warm-up, and one stuck panel on it must not make
                # this probe wait out the collector's full per-dashboard timeout.
                _watch(driver, warm_min, min(timeout_ms, WARMUP_CAP_MS), poll_ms, window_ms=None)
                time.sleep(settings.collector.inter_request_delay_ms / 1000.0)      # as the collector does

                driver.goto(dashboard_url(settings, target["dashboard_id"]), timeout_ms)
                driver.wait_for_panel(timeout_ms)
                samples = _watch(driver, expected_min, timeout_ms, poll_ms, window_ms=args.window_ms)
                if args.repeat > 1:
                    print(f"=== run {n} of {args.repeat} ===")
                print(format_report(analyze(samples, expected_min), target["title"], expected_min))
                print()
        finally:
            context.close()
            browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
