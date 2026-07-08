"""Playwright browser backend.

Thin `Driver` over Playwright that the backend-agnostic `collect_core` uses for
timing and document assembly. The version-sensitive DOM reading lives in
`selectors`; classification in `render_detection`.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List

from playwright.sync_api import sync_playwright

from .collect_core import collect_dashboard, print_row
from .config import Settings
from .selectors import PANEL, PANEL_STATE_JS


class PlaywrightDriver:
    def __init__(self, page):
        self.page = page

    def goto(self, url: str, timeout_ms: int) -> None:
        self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    def wait_for_panel(self, timeout_ms: int) -> None:
        self.page.wait_for_selector(PANEL, timeout=timeout_ms)

    def read_panel_states(self) -> List[Dict[str, Any]]:
        try:
            return self.page.evaluate(PANEL_STATE_JS)
        except Exception:
            return []


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


def run(settings: Settings, registry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect every dashboard in the registry using Playwright."""
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
        driver = PlaywrightDriver(page)
        try:
            for d in dashboards:
                doc = collect_dashboard(driver, settings, d, run_id)
                docs.append(doc)
                print_row(doc)
        finally:
            context.close()
            browser.close()

    return docs
