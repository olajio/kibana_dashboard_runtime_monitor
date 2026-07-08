"""Tests for the backend-agnostic collection core.

A fake driver stands in for Playwright/Selenium so the timing loop, panel
reconciliation, and document assembly (shared by both backends) are exercised
without a real browser.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dhm.collect_core import collect_dashboard, dashboard_url
from dhm.config import Settings


class FakeDriver:
    def __init__(self, states=None, raise_on_goto=False):
        # `states` is the list returned by every read_panel_states() call.
        self._states = states or []
        self._raise = raise_on_goto

    def goto(self, url, timeout_ms):
        if self._raise:
            raise RuntimeError("boom")

    def wait_for_panel(self, timeout_ms):
        pass

    def read_panel_states(self):
        return self._states


def _fast_settings():
    s = Settings()
    s.kibana.base_url = "https://kibana.example.gov"
    s.collector.dashboard_timeout_ms = 60
    s.collector.poll_interval_ms = 10
    return s


def _panel(pid, title, **kw):
    st = {
        "id": pid,
        "title": title,
        "index": 0,
        "renderComplete": False,
        "loading": False,
        "hasError": False,
        "emptyText": None,
    }
    st.update(kw)
    return st


def _dash(panels):
    return {
        "dashboard_id": "d1",
        "title": "Test Dashboard",
        "is_hub": True,
        "panel_count": len(panels),
        "data_panel_count": sum(1 for p in panels if p["is_data_panel"]),
        "panels": panels,
    }


def test_dashboard_url_default_space_has_no_prefix():
    s = _fast_settings()
    url = dashboard_url(s, "abc")
    assert "/app/dashboards#/view/abc" in url
    assert "/s/" not in url


def test_dashboard_url_named_space_has_prefix():
    s = _fast_settings()
    s.kibana_space = "fed"
    assert "/s/fed/app/dashboards#/view/abc" in dashboard_url(s, "abc")


def test_happy_path_ok():
    s = _fast_settings()
    dash = _dash([{"panel_id": "p1", "title": "Chart", "panel_type": "lens", "is_data_panel": True}])
    driver = FakeDriver(states=[_panel("p1", "Chart", renderComplete=True)])
    doc = collect_dashboard(driver, s, dash, "run1")
    assert doc["load_status"] == "ok"
    assert doc["panels_ok"] == 1
    assert doc["panels_not_ok"] == 0
    assert doc["panels"][0]["render_ms"] is not None
    assert doc["collector_run_id"] == "run1"


def test_empty_panel_is_degraded():
    s = _fast_settings()
    dash = _dash([{"panel_id": "p1", "title": "Chart", "panel_type": "lens", "is_data_panel": True}])
    driver = FakeDriver(states=[_panel("p1", "Chart", renderComplete=True, emptyText="No results found")])
    doc = collect_dashboard(driver, s, dash, "run1")
    assert doc["load_status"] == "degraded"
    assert doc["panels_empty"] == 1


def test_missing_panel_is_failed():
    s = _fast_settings()
    dash = _dash([{"panel_id": "gone", "title": "Vanished", "panel_type": "lens", "is_data_panel": True}])
    driver = FakeDriver(states=[])  # nothing renders
    doc = collect_dashboard(driver, s, dash, "run1")
    assert doc["panels_missing"] == 1
    assert doc["load_status"] == "failed"


def test_navigation_failure_sets_load_error():
    s = _fast_settings()
    dash = _dash([{"panel_id": "p1", "title": "Chart", "panel_type": "lens", "is_data_panel": True}])
    driver = FakeDriver(states=[_panel("p1", "Chart", renderComplete=True)], raise_on_goto=True)
    doc = collect_dashboard(driver, s, dash, "run1")
    assert doc["load_error"] is not None
    assert doc["load_status"] == "failed"


def test_navigation_panels_are_not_health_checked():
    s = _fast_settings()
    dash = _dash([
        {"panel_id": "nav", "title": "Main Navigation", "panel_type": "links", "is_data_panel": False},
        {"panel_id": "p1", "title": "Chart", "panel_type": "lens", "is_data_panel": True},
    ])
    driver = FakeDriver(states=[_panel("p1", "Chart", renderComplete=True)])
    doc = collect_dashboard(driver, s, dash, "run1")
    assert doc["panels_checked"] == 1  # only the data panel
