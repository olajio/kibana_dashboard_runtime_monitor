"""Tests for the backend-agnostic collection core.

A fake driver stands in for Playwright/Selenium so the timing loop, panel
reconciliation, and document assembly (shared by both backends) are exercised
without a real browser.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dhm.collect_core import (
    collect_dashboard,
    collect_all,
    safe_collect_dashboard,
    dashboard_url,
)
from dhm.config import Settings


class FakeDriver:
    def __init__(self, states=None, raise_on_goto=False, goto_fail_times=0, read_raises=False):
        # `states` is the list returned by every read_panel_states() call.
        self._states = states or []
        self._raise = raise_on_goto
        self._goto_fail_times = goto_fail_times
        self._goto_calls = 0
        self._read_raises = read_raises

    def goto(self, url, timeout_ms):
        self._goto_calls += 1
        if self._raise or self._goto_calls <= self._goto_fail_times:
            raise RuntimeError("boom")

    def wait_for_panel(self, timeout_ms):
        pass

    def read_panel_states(self):
        if self._read_raises:
            raise RuntimeError("read boom")
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


def test_load_retry_recovers_after_transient_failure():
    s = _fast_settings()
    s.collector.load_retries = 1  # one extra attempt
    dash = _dash([{"panel_id": "p1", "title": "Chart", "panel_type": "lens", "is_data_panel": True}])
    driver = FakeDriver(states=[_panel("p1", "Chart", renderComplete=True)], goto_fail_times=1)
    doc = collect_dashboard(driver, s, dash, "run1")
    assert doc["load_error"] is None
    assert doc["load_status"] == "ok"


def test_load_retry_exhausted_is_failed():
    s = _fast_settings()
    s.collector.load_retries = 1
    dash = _dash([{"panel_id": "p1", "title": "Chart", "panel_type": "lens", "is_data_panel": True}])
    driver = FakeDriver(goto_fail_times=99)  # always fails
    doc = collect_dashboard(driver, s, dash, "run1")
    assert doc["load_error"] is not None
    assert doc["load_status"] == "failed"


def test_safe_collect_isolates_unexpected_error():
    s = _fast_settings()
    dash = _dash([{"panel_id": "p1", "title": "Chart", "panel_type": "lens", "is_data_panel": True}])
    driver = FakeDriver(read_raises=True)  # blows up mid-poll
    doc = safe_collect_dashboard(driver, s, dash, "run1")
    assert doc["load_status"] == "failed"
    assert "collector error" in doc["load_error"]


def test_collect_all_returns_one_doc_per_dashboard():
    s = _fast_settings()
    s.collector.inter_request_delay_ms = 0
    dashboards = [
        _dash([{"panel_id": "p1", "title": "A", "panel_type": "lens", "is_data_panel": True}]),
        _dash([{"panel_id": "p1", "title": "A", "panel_type": "lens", "is_data_panel": True}]),
    ]
    dashboards[0]["dashboard_id"] = "d1"
    dashboards[1]["dashboard_id"] = "d2"
    driver = FakeDriver(states=[_panel("p1", "A", renderComplete=True)])
    docs = collect_all(driver, s, dashboards, "run1")
    assert len(docs) == 2
    assert {d["dashboard_id"] for d in docs} == {"d1", "d2"}
