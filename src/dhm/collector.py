"""The browser collector.

For each dashboard in the registry it:
  1. navigates a headless browser to the dashboard,
  2. polls every panel's render state until all resolve or the timeout hits,
  3. records per-panel time-to-render and a health status,
  4. builds one Elasticsearch document describing the dashboard's health.

The version-sensitive DOM reading lives in `selectors`; the classification in
`render_detection`. This module orchestrates timing and document assembly.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from playwright.sync_api import sync_playwright

from . import __version__
from .config import Settings
from .render_detection import is_resolved, reconcile, summarize
from .selectors import PANEL, PANEL_STATE_JS


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dashboard_url(settings: Settings, dashboard_id: str) -> str:
    base = settings.kibana.base_url
    space = settings.kibana_space
    prefix = "" if space in ("", "default") else f"/s/{space}"
    g = f"(time:(from:{settings.kibana.time_from},to:{settings.kibana.time_to}))"
    return f"{base}{prefix}/app/dashboards#/view/{dashboard_id}?_g={g}"


def _panel_key(state: Dict[str, Any]) -> str:
    return state.get("id") or state.get("title") or f"idx:{state.get('index')}"


def _new_context(browser, settings: Settings):
    """Apply auth to a fresh browser context."""
    auth = settings.kibana.auth
    context = browser.new_context(ignore_https_errors=not settings.kibana.verify_tls)
    if auth.method == "api_key" and auth.api_key:
        context.set_extra_http_headers(
            {"Authorization": f"ApiKey {auth.api_key}", "kbn-xsrf": "dhm"}
        )
    elif auth.method == "cookie" and auth.cookie_value:
        from urllib.parse import urlparse

        host = urlparse(settings.kibana.base_url).hostname
        context.add_cookies(
            [
                {
                    "name": auth.cookie_name,
                    "value": auth.cookie_value,
                    "domain": host,
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                }
            ]
        )
    return context


def collect_dashboard(
    page,
    settings: Settings,
    dashboard: Dict[str, Any],
    run_id: str,
) -> Dict[str, Any]:
    """Load one dashboard and return its health document."""
    url = dashboard_url(settings, dashboard["dashboard_id"])
    timeout_ms = settings.collector.dashboard_timeout_ms
    poll_ms = settings.collector.poll_interval_ms

    resolve_times: Dict[str, int] = {}
    last_states: List[Dict[str, Any]] = []
    load_error: Optional[str] = None

    t0 = time.monotonic()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        # Wait until at least one panel exists (dashboard shell rendered).
        page.wait_for_selector(PANEL, timeout=timeout_ms)
    except Exception as exc:  # navigation/auth failure
        load_error = f"navigation failed: {type(exc).__name__}: {exc}"

    if load_error is None:
        deadline = t0 + timeout_ms / 1000.0
        while True:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            try:
                states = page.evaluate(PANEL_STATE_JS)
            except Exception:
                states = []
            if states:
                last_states = states
                for st in states:
                    key = _panel_key(st)
                    if key not in resolve_times and is_resolved(st):
                        resolve_times[key] = elapsed_ms
                # done when every panel currently on the page has resolved
                if states and all(is_resolved(s) for s in states):
                    break
            if time.monotonic() >= deadline:
                break
            time.sleep(poll_ms / 1000.0)

    expected_panels = dashboard.get("panels", [])
    panel_records = reconcile(expected_panels, last_states, resolve_times)
    roll = summarize(panel_records)

    # Dashboard load time = when the last expected data panel resolved.
    panel_times = [r["render_ms"] for r in panel_records if r["render_ms"] is not None]
    load_time_ms = max(panel_times) if panel_times else int((time.monotonic() - t0) * 1000)

    load_status = _load_status(settings, load_time_ms, roll, load_error)

    return {
        "@timestamp": _now_iso(),
        "schema_version": 1,
        "app": settings.app,
        "cluster": settings.cluster,
        "kibana_space": settings.kibana_space,
        "dashboard_id": dashboard["dashboard_id"],
        "dashboard_title": dashboard["title"],
        "is_hub": dashboard.get("is_hub", False),
        "dashboard_url": url,
        "load_time_ms": load_time_ms,
        "load_status": load_status,
        "load_error": load_error,
        "expected_data_panels": dashboard.get("data_panel_count", len(expected_panels)),
        "panel_count": dashboard.get("panel_count"),
        **roll,
        "panels": panel_records,
        "collector_run_id": run_id,
        "collector_version": __version__,
    }


def _load_status(
    settings: Settings, load_time_ms: int, roll: Dict[str, int], load_error: Optional[str]
) -> str:
    if load_error or roll["panels_missing"] or roll["panels_error"] or roll["panels_timeout"]:
        return "failed"
    if load_time_ms >= settings.collector.failed_over_ms:
        return "failed"
    if load_time_ms >= settings.collector.degraded_over_ms or roll["panels_empty"]:
        return "degraded"
    return "ok"


def run(settings: Settings, registry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect every dashboard in the registry. Returns the list of documents."""
    run_id = str(uuid.uuid4())
    docs: List[Dict[str, Any]] = []
    dashboards = registry.get("dashboards", [])

    with sync_playwright() as pw:
        launch_kwargs = {"headless": settings.collector.headless}
        channel = (settings.collector.browser_channel or "").strip().lower()
        if channel and channel not in ("chromium", "bundled"):
            # Drive an already-installed browser (e.g. msedge, chrome) via a
            # Playwright channel — no downloaded Chromium required.
            launch_kwargs["channel"] = channel
        browser = pw.chromium.launch(**launch_kwargs)
        context = _new_context(browser, settings)
        page = context.new_page()
        page.set_default_timeout(settings.collector.dashboard_timeout_ms)
        try:
            for d in dashboards:
                doc = collect_dashboard(page, settings, d, run_id)
                docs.append(doc)
                print(
                    f"  {doc['dashboard_title'][:40]:40s} "
                    f"{doc['load_status']:8s} "
                    f"{doc['load_time_ms']:6d}ms "
                    f"ok={doc['panels_ok']} not_ok={doc['panels_not_ok']}"
                )
        finally:
            context.close()
            browser.close()

    return docs
