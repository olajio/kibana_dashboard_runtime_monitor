"""baseline.py: the aggregation reply to a per-dashboard summary and suggested thresholds."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import baseline as bl


def bucket(title, n, p50, p95, p99, mx, not_ok=0, status=None):
    return {"key": title, "doc_count": n,
            "load": {"values": {"50.0": p50, "95.0": p95, "99.0": p99}},
            "slowest": {"value": mx}, "not_ok": {"value": not_ok},
            "status": {"buckets": [{"key": k, "doc_count": v} for k, v in (status or {"ok": n}).items()]}}


def reply(*buckets, runs=3):
    return {"aggregations": {"runs": {"value": runs}, "by_dashboard": {"buckets": list(buckets)}}}


def test_rows_are_sorted_slowest_first_and_carry_suggestions():
    s = bl.summarise(reply(bucket("Fast", 3, 200, 300, 300, 300),
                           bucket("Slow", 3, 92000, 168000, 168000, 168800, status={"degraded": 2, "failed": 1})))
    assert [r["dashboard"] for r in s["rows"]] == ["Slow", "Fast"]
    assert s["runs"] == 3 and s["rows"][0]["status"] == {"degraded": 2, "failed": 1}


def test_suggestion_scales_with_the_dashboard_and_never_undercuts_its_slowest_run():
    fast = bl.suggest(300, 300, 300)
    slow = bl.suggest(168000, 168000, 168800)
    assert fast == {"degraded_over_ms": 5000, "failed_over_ms": 10000}
    assert slow["degraded_over_ms"] == 255000 and slow["failed_over_ms"] >= 168800 * 1.25


def test_a_handful_of_runs_is_flagged_as_not_a_baseline():
    s = bl.summarise(reply(bucket("A", 3, 1000, 1200, 1200, 1300)))
    assert "NOT YET A BASELINE" in bl.format_report(s, 14, 48)
    enough = bl.summarise(reply(bucket("A", 50, 1000, 1200, 1200, 1300), runs=50))
    assert "NOT YET A BASELINE" not in bl.format_report(enough, 14, 48)


def test_not_ok_panels_are_surfaced_in_the_report():
    s = bl.summarise(reply(bucket("A", 3, 1000, 1200, 1200, 1300, not_ok=4)))
    assert s["rows"][0]["not_ok"] == 4


def test_query_is_a_pure_aggregation_over_the_window():
    q = bl.build_query(7)
    assert q["size"] == 0 and q["query"]["range"]["@timestamp"]["gte"] == "now-7d"
    assert "by_dashboard" in q["aggs"] and "runs" in q["aggs"]
