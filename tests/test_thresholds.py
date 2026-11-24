"""Per-dashboard load-time limits, and the fail-fast behaviour of a run."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dhm import collect_core
from dhm.collect_core import (
    collect_all, collect_dashboard, is_timeout, load_status, safe_collect_dashboard, skipped_doc,
)
from dhm.config import (
    Settings, dashboard_limits, load_settings, threshold_problems, unknown_threshold_titles,
)

ROLL_OK = {"panels_checked": 5, "panels_not_ok": 0, "panels_error": 0}


def settings_with(**thresholds):
    s = Settings()
    s.kibana.base_url = "https://kibana.example.gov"
    s.collector.thresholds = thresholds
    return s


# ---------------------------------------------------------------------- limits
def test_a_dashboard_without_an_override_uses_the_global_limits():
    s = settings_with()
    assert dashboard_limits(s, "Anything") == {"degraded_over_ms": 30000, "failed_over_ms": 120000,
                                               "timeout_ms": 180000}


def test_an_override_replaces_only_the_keys_it_names():
    s = settings_with(**{"A": {"degraded_over_ms": 5000, "failed_over_ms": 10000}, "B": {"timeout_ms": 300000}})
    assert dashboard_limits(s, "A") == {"degraded_over_ms": 5000, "failed_over_ms": 10000, "timeout_ms": 180000}
    assert dashboard_limits(s, "B") == {"degraded_over_ms": 30000, "failed_over_ms": 120000, "timeout_ms": 300000}


def test_status_follows_the_dashboards_own_limits():
    s = settings_with(**{"Fast": {"degraded_over_ms": 5000, "failed_over_ms": 10000}})
    assert load_status(s, 7000, ROLL_OK, None, "Fast") == "degraded"
    assert load_status(s, 12000, ROLL_OK, None, "Fast") == "failed"
    assert load_status(s, 7000, ROLL_OK, None, "Other") == "ok"          # global limits
    assert load_status(s, 7000, ROLL_OK, None) == "ok"                   # no title: global


def test_not_ok_panels_and_errors_still_override_the_load_time_limits():
    s = settings_with(**{"Slow": {"degraded_over_ms": 200000, "failed_over_ms": 250000}})
    assert load_status(s, 1000, {"panels_checked": 5, "panels_not_ok": 1, "panels_error": 0}, None, "Slow") == "degraded"
    assert load_status(s, 1000, {"panels_checked": 5, "panels_not_ok": 1, "panels_error": 1}, None, "Slow") == "failed"
    assert load_status(s, 1000, ROLL_OK, "boom", "Slow") == "failed"


# ---------------------------------------------------------------------- validation
def test_failed_below_degraded_is_an_error():
    errors, _ = threshold_problems(settings_with(**{"A": {"degraded_over_ms": 9000, "failed_over_ms": 5000}}))
    assert len(errors) == 1 and "below" in errors[0]


def test_an_unknown_key_is_an_error_so_a_typo_cannot_pass_silently():
    errors, _ = threshold_problems(settings_with(**{"A": {"degrade_over_ms": 5000}}))
    assert errors and "unknown key" in errors[0]


def test_a_failed_limit_at_or_above_the_timeout_is_only_a_warning():
    errors, warnings = threshold_problems(settings_with(**{"A": {"degraded_over_ms": 100000, "failed_over_ms": 200000}}))
    assert not errors and len(warnings) == 1 and "timeout" in warnings[0]


def test_a_longer_timeout_clears_that_warning():
    s = settings_with(**{"A": {"degraded_over_ms": 100000, "failed_over_ms": 200000, "timeout_ms": 300000}})
    assert threshold_problems(s) == ([], [])


def test_unknown_titles_are_reported():
    s = settings_with(**{"Real": {"timeout_ms": 1}, "Typo Overview": {"timeout_ms": 1}})
    assert unknown_threshold_titles(s, ["Real", "Other"]) == ["Typo Overview"]


# ---------------------------------------------------------------------- loading
def test_thresholds_load_from_the_file_beside_the_settings_file(tmp_path, monkeypatch):
    (tmp_path / "settings.yaml").write_text("collector:\n  degraded_over_ms: 30000\n")
    (tmp_path / "thresholds.yaml").write_text(
        'thresholds:\n  "Agency Details": {degraded_over_ms: 100000, failed_over_ms: 170000}\n')
    s = load_settings(str(tmp_path / "settings.yaml"))
    assert dashboard_limits(s, "Agency Details")["failed_over_ms"] == 170000


def test_inline_settings_win_over_the_file(tmp_path):
    (tmp_path / "settings.yaml").write_text(
        'collector:\n  thresholds:\n    "A": {degraded_over_ms: 7000}\n')
    (tmp_path / "thresholds.yaml").write_text(
        'thresholds:\n  "A": {degraded_over_ms: 5000, failed_over_ms: 20000}\n')
    s = load_settings(str(tmp_path / "settings.yaml"))
    assert dashboard_limits(s, "A")["degraded_over_ms"] == 7000
    assert dashboard_limits(s, "A")["failed_over_ms"] == 20000


def test_a_missing_thresholds_file_means_no_overrides(tmp_path):
    (tmp_path / "settings.yaml").write_text("app: x\n")
    assert load_settings(str(tmp_path / "settings.yaml")).collector.thresholds == {}


def test_invalid_thresholds_stop_the_load(tmp_path):
    (tmp_path / "settings.yaml").write_text("app: x\n")
    (tmp_path / "thresholds.yaml").write_text('thresholds:\n  "A": {degraded_over_ms: 9000, failed_over_ms: 5000}\n')
    with pytest.raises(ValueError, match="invalid per-dashboard thresholds"):
        load_settings(str(tmp_path / "settings.yaml"))


def test_the_checked_in_thresholds_file_is_valid_and_covers_known_dashboards():
    here = os.path.dirname(__file__)
    s = load_settings(os.path.join(here, "..", "config", "settings.yaml"))   # may not exist: file still loads
    assert s.collector.thresholds, "config/thresholds.yaml should hold overrides"
    assert threshold_problems(s)[0] == []
    import json
    titles = {json.loads(l)["attributes"]["title"] for l in open(os.path.join(here, "..", "federal_overview_dashboard.ndjson"))
              if l.strip() and json.loads(l).get("type") == "dashboard"}
    assert unknown_threshold_titles(s, titles) == []


# ---------------------------------------------------------------------- documents
class Driver:
    def __init__(self, goto_error=None):
        self.goto_error, self.goto_calls = goto_error, 0

    def goto(self, url, timeout_ms):
        self.goto_calls += 1
        if self.goto_error:
            raise self.goto_error

    def wait_for_panel(self, timeout_ms):
        pass

    def read_panel_states(self):
        return [{"id": "p1", "title": "T", "index": 0, "renderComplete": True, "hasError": False,
                 "emptyText": None, "loading": False}]


def dash(title, n=0):
    return {"dashboard_id": f"id{n}", "title": title, "data_panel_count": 1, "panel_count": 1,
            "panels": [{"panel_id": "p1", "title": "T", "panel_type": "lens", "is_data_panel": True}]}


def fast_settings(**kw):
    s = settings_with(**kw)
    s.collector.dashboard_timeout_ms = 200
    s.collector.poll_interval_ms = 5
    s.collector.inter_request_delay_ms = 0
    s.collector.load_retries = 1
    return s


def test_every_document_records_the_limits_it_was_judged_against():
    s = fast_settings(**{"A": {"degraded_over_ms": 5000, "failed_over_ms": 10000, "timeout_ms": 150}})
    doc = collect_dashboard(Driver(), s, dash("A"), "run")
    assert (doc["degraded_over_ms"], doc["failed_over_ms"], doc["timeout_ms"]) == (5000, 10000, 150)
    assert doc["schema_version"] == 2 and doc["load_attempts"] == 1 and doc["elapsed_ms"] >= 0


def test_a_dashboard_uses_its_own_timeout_for_the_browser():
    seen = []

    class D(Driver):
        def goto(self, url, timeout_ms):
            seen.append(timeout_ms)

    collect_dashboard(D(), fast_settings(**{"A": {"timeout_ms": 150}}), dash("A"), "run")
    assert seen == [150]


def test_a_timeout_is_not_retried_but_another_error_is():
    s = fast_settings()
    d = Driver(goto_error=TimeoutError("page took too long"))
    doc = collect_dashboard(d, s, dash("A"), "run")
    assert d.goto_calls == 1 and doc["load_attempts"] == 1 and doc["load_error"]
    d = Driver(goto_error=RuntimeError("connection refused"))
    doc = collect_dashboard(d, s, dash("A"), "run")
    assert d.goto_calls == 2 and doc["load_attempts"] == 2


def test_retry_on_timeout_can_be_turned_back_on():
    s = fast_settings()
    s.collector.retry_on_timeout = True
    d = Driver(goto_error=TimeoutError("x"))
    collect_dashboard(d, s, dash("A"), "run")
    assert d.goto_calls == 2


def test_is_timeout_recognises_playwright_and_selenium_names():
    class TimeoutException(Exception):
        pass

    assert is_timeout(TimeoutError()) and is_timeout(TimeoutException())
    assert not is_timeout(RuntimeError("x"))


# ---------------------------------------------------------------------- fail fast
def test_three_load_failures_in_a_row_skip_the_rest_of_the_run():
    s = fast_settings()
    s.collector.load_retries = 0
    d = Driver(goto_error=RuntimeError("down"))
    docs = collect_all(d, s, [dash(f"D{i}", i) for i in range(6)], "run")
    assert len(docs) == 6 and d.goto_calls == 3                      # only three were tried
    skipped = docs[3:]
    assert all(x["load_error"].startswith("skipped:") and x["load_status"] == "failed" for x in skipped)
    assert all(x["load_time_ms"] == 0 and x["load_attempts"] == 0 for x in skipped)
    assert all(x["panels_missing"] == 1 for x in skipped)            # still one record per expected panel
    assert "Kibana looks unreachable" in skipped[0]["load_error"]


def test_a_success_resets_the_failure_count():
    s = fast_settings()
    s.collector.load_retries = 0

    class Flaky(Driver):
        def goto(self, url, timeout_ms):
            self.goto_calls += 1
            if self.goto_calls % 3:                                    # fail, fail, succeed, ...
                raise RuntimeError("down")

    docs = collect_all(Flaky(), s, [dash(f"D{i}", i) for i in range(9)], "run")
    assert not any(str(x["load_error"]).startswith("skipped:") for x in docs)


def test_the_limit_can_be_disabled():
    s = fast_settings()
    s.collector.load_retries = 0
    s.collector.consecutive_failure_limit = 0
    d = Driver(goto_error=RuntimeError("down"))
    collect_all(d, s, [dash(f"D{i}", i) for i in range(5)], "run")
    assert d.goto_calls == 5


def test_the_run_time_cap_skips_what_is_left(monkeypatch):
    s = fast_settings()
    s.collector.max_run_seconds = 100
    clock = {"t": 0.0}
    monkeypatch.setattr(collect_core.time, "monotonic", lambda: clock["t"])
    real = collect_core.safe_collect_dashboard

    def slow(driver, settings, dashboard, run_id):
        clock["t"] += 60                                              # each dashboard "takes" 60 s
        return real(driver, settings, dashboard, run_id)

    monkeypatch.setattr(collect_core, "safe_collect_dashboard", slow)
    docs = collect_all(Driver(), s, [dash(f"D{i}", i) for i in range(5)], "run")
    assert [bool(str(x["load_error"] or "").startswith("skipped:")) for x in docs] == [False, False, True, True, True]
    assert "run time limit of 100 s" in docs[2]["load_error"]


def test_skipped_documents_have_the_same_shape_as_loaded_ones():
    s = fast_settings()
    loaded = collect_dashboard(Driver(), s, dash("A"), "run")
    skipped = skipped_doc(s, dash("A"), "run", "why")
    assert set(skipped) == set(loaded)


def test_an_unexpected_error_still_counts_toward_the_limit():
    s = fast_settings()
    s.collector.load_retries = 0

    class Broken(Driver):
        def read_panel_states(self):
            raise RuntimeError("browser crashed")

    # a crash while reading the page is recorded as a collector error and counts as a failure
    docs = collect_all(Broken(), s, [dash(f"D{i}", i) for i in range(4)], "run")
    assert docs[0]["load_error"].startswith("collector error")
    assert docs[3]["load_error"].startswith("skipped:")
