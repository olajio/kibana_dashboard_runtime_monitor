"""Selenium backend — fallback for environments where the `playwright` pip
package cannot be installed.

It drives the same system browser (Microsoft Edge via `msedgedriver`, or Chrome
via `chromedriver`) and runs the *same* `selectors.PANEL_STATE_JS` extraction, so
the health logic and document schema (in `collect_core`) are shared with the
Playwright backend — nothing is duplicated except the browser plumbing.

Enable it with `collector.backend: selenium` in settings. Requires:
    pip install -r requirements-selenium.txt
and either `msedgedriver`/`chromedriver` on PATH or `collector.webdriver_path` set.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .collect_core import collect_dashboard, print_row
from .config import Settings
from .selectors import PANEL, PANEL_STATE_JS

# The extraction constant is an arrow function; wrap it so execute_script calls it.
_READ_STATES_JS = "return (" + PANEL_STATE_JS + ")();"


class SeleniumDriver:
    def __init__(self, driver):
        self.driver = driver

    def goto(self, url: str, timeout_ms: int) -> None:
        self.driver.set_page_load_timeout(timeout_ms / 1000.0)
        self.driver.get(url)

    def wait_for_panel(self, timeout_ms: int) -> None:
        WebDriverWait(self.driver, timeout_ms / 1000.0).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, PANEL))
        )

    def read_panel_states(self) -> List[Dict[str, Any]]:
        try:
            return self.driver.execute_script(_READ_STATES_JS) or []
        except Exception:
            return []


def _build_driver(settings: Settings):
    """Create an Edge (default) or Chrome WebDriver for the system browser."""
    channel = (settings.collector.browser_channel or "msedge").strip().lower()
    headless = settings.collector.headless
    webdriver_path = settings.collector.webdriver_path

    if channel == "chrome":
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service

        opts = Options()
        make = webdriver.Chrome
    else:  # msedge / default
        from selenium.webdriver.edge.options import Options
        from selenium.webdriver.edge.service import Service

        opts = Options()
        make = webdriver.Edge

    if headless:
        opts.add_argument("--headless=new")
    if not settings.kibana.verify_tls:
        opts.add_argument("--ignore-certificate-errors")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")

    # If a driver binary path is configured, use it; otherwise rely on PATH /
    # Selenium Manager to locate msedgedriver/chromedriver.
    service = Service(executable_path=webdriver_path) if webdriver_path else Service()
    return make(service=service, options=opts)


def _apply_auth(driver, settings: Settings) -> None:
    """Apply Kibana auth to the Edge/Chrome session (both are Chromium, so CDP
    works for header injection)."""
    auth = settings.kibana.auth
    if auth.method == "api_key" and auth.api_key:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd(
            "Network.setExtraHTTPHeaders",
            {"headers": {"Authorization": f"ApiKey {auth.api_key}", "kbn-xsrf": "dhm"}},
        )
    elif auth.method == "cookie" and auth.cookie_value:
        # A cookie can only be set once we are on the target domain.
        driver.get(settings.kibana.base_url)
        driver.add_cookie(
            {
                "name": auth.cookie_name,
                "value": auth.cookie_value,
                "path": "/",
                "domain": urlparse(settings.kibana.base_url).hostname,
            }
        )


def run(settings: Settings, registry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect every dashboard in the registry using Selenium."""
    run_id = str(uuid.uuid4())
    docs: List[Dict[str, Any]] = []
    dashboards = registry.get("dashboards", [])

    driver = _build_driver(settings)
    try:
        _apply_auth(driver, settings)
        sd = SeleniumDriver(driver)
        for d in dashboards:
            doc = collect_dashboard(sd, settings, d, run_id)
            docs.append(doc)
            print_row(doc)
    finally:
        driver.quit()

    return docs
