"""baseline.py: what it leaves out, and the limits it suggests."""
import os
import sys

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import baseline as bl
from dhm.config import Settings, dashboard_limits, threshold_problems


def bucket(title, n, p50, p95, p99):
    return {"key": title, "doc_count": n, "load": {"values": {"50.0": p50, "95.0": p95, "99.0": p99}}}


def reply(*buckets, runs=300, total=1000):
    return {"hits": {"total": {"value": total}},
            "aggregations": {"runs": {"value": runs}, "by_dashboard": {"buckets": list(buckets)}}}


# ---------------------------------------------------------------------- what is left out
def test_the_stats_query_leaves_out_not_ok_results_errors_timeouts_and_outage_runs():
    q = bl.build_stats_query(14, 180, ["run-a", "run-b"])["query"]["bool"]
    assert {"term": {"panels_not_ok": 0}} in q["filter"]
    assert {"range": {"load_time_ms": {"lt": 179000}}} in q["filter"]          # under the cut-off, not at it
    assert {"exists": {"field": "load_error"}} in q["must_not"]
    assert {"terms": {"collector_run_id": ["run-a", "run-b"]}} in q["must_not"]


def test_with_no_outage_runs_there_is_no_empty_terms_filter():
    q = bl.build_stats_query(14, 180, [])["query"]["bool"]
    assert all("terms" not in c for c in q["must_not"])


def test_the_timeout_cut_off_follows_the_timeout_in_use():
    q = bl.build_stats_query(14, 300, [])["query"]["bool"]
    assert {"range": {"load_time_ms": {"lt": 299000}}} in q["filter"]


def test_outage_runs_are_those_with_several_dashboards_not_ok():
    q = bl.build_outage_query(14, 5)
    sel = q["aggs"]["runs"]["aggs"]["keep"]["bucket_selector"]
    assert sel["script"] == "params.n >= 5" and sel["buckets_path"] == {"n": "bad>_count"}
    resp = {"aggregations": {"runs": {"buckets": [{"key": "r1", "doc_count": 22}, {"key": "r2", "doc_count": 22}]}}}
    assert bl.outage_run_ids(resp) == ["r1", "r2"]
    assert bl.outage_run_ids({}) == []


def test_it_reports_percentiles_and_never_the_maximum():
    q = bl.build_stats_query(14, 180, [])
    p = q["aggs"]["by_dashboard"]["aggs"]["load"]["percentiles"]
    assert p["percents"] == [50, 95, 99]
    assert "max" not in str(q["aggs"]).replace("percentiles", "")


# ---------------------------------------------------------------------- suggested limits
def test_a_fast_dashboard_gets_a_five_second_floor():
    assert bl.suggest(0.4) == {"degraded_over_ms": 5000, "failed_over_ms": 10000}


def test_limits_scale_with_the_dashboard_and_round_up_to_five_seconds():
    assert bl.suggest(33.8) == {"degraded_over_ms": 55000, "failed_over_ms": 110000}
    assert bl.suggest(22.1) == {"degraded_over_ms": 35000, "failed_over_ms": 70000}


def test_failed_is_kept_under_the_timeout():
    assert bl.suggest(63.5) == {"degraded_over_ms": 100000, "failed_over_ms": 170000}      # 2 x 100 would be 200 s


def test_a_dashboard_too_close_to_the_timeout_gets_no_limits():
    assert bl.suggest(173.1) is None and bl.suggest(126.6) is None
    assert bl.suggest(173.1, timeout_s=300) == {"degraded_over_ms": 260000, "failed_over_ms": 290000}


def test_failed_is_always_above_degraded():
    for p95 in (0.1, 1, 3.3, 10, 40, 70, 100):
        lim = bl.suggest(p95)
        assert lim and lim["failed_over_ms"] > lim["degraded_over_ms"]


# ---------------------------------------------------------------------- output
def test_rows_are_slowest_first_and_slow_ones_say_why_they_have_no_limits():
    s = bl.summarise(reply(bucket("Fast", 500, 300, 500, 900), bucket("Slow", 500, 143000, 173100, 175000)), 180, 1.5)
    assert [r["dashboard"] for r in s["rows"]] == ["Slow", "Fast"]
    text = bl.format_report(s, 14, 48, 180, 1000, 1, 1.5)
    assert "raise timeout_ms first" in text and "1 outage run(s)" in text


def test_a_short_window_says_it_is_not_yet_a_baseline():
    s = bl.summarise(reply(bucket("A", 10, 300, 500, 900), runs=10), 180, 1.5)
    assert "NOT YET A BASELINE" in bl.format_report(s, 14, 48, 180, 20, 0, 1.5)


def test_the_yaml_is_loadable_and_passes_the_collectors_own_validation():
    s = bl.summarise(reply(bucket("Fast", 500, 300, 500, 900), bucket("Agency Details", 500, 39500, 63500, 116000),
                           bucket("Slow", 500, 143000, 173100, 175000)), 180, 1.5)
    text = bl.format_yaml(s, 14, 180, 1.5, "2026-11-24")
    data = yaml.safe_load(text)["thresholds"]
    assert set(data) == {"Fast", "Agency Details"}                  # "Slow" is a comment, not an entry
    assert "too close to the 180 s timeout" in text
    st = Settings()
    st.collector.thresholds = data
    assert threshold_problems(st) == ([], [])
    assert dashboard_limits(st, "Agency Details")["failed_over_ms"] == 170000
