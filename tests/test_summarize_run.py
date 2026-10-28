"""summarize_run.py: the per-dashboard breakdown and the recurring-title detector."""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import summarize_run


def _doc(title, status, panels):
    return {"dashboard_title": title, "load_status": status,
            "panels": [{"panel_title": t, "render_status": s} for t, s in panels]}


def test_breaks_not_ok_down_by_status():
    out = io.StringIO()
    summarize_run.summarize(
        [_doc("A", "failed", [("x", "ok"), ("y", "empty"), ("z", "empty"), ("w", "missing")])], out)
    line = [l for l in out.getvalue().splitlines() if l.startswith("A")][0]
    assert "'empty': 2" in line and "'missing': 1" in line and "failed" in line


def test_all_ok_dashboard_shows_a_dash():
    out = io.StringIO()
    summarize_run.summarize([_doc("A", "ok", [("x", "ok")])], out)
    assert [l for l in out.getvalue().splitlines() if l.startswith("A")][0].rstrip().endswith("-")


def test_flags_titles_that_recur_across_dashboards():
    docs = [_doc("A", "degraded", [("CDM-Nav-LCKUP", "empty"), ("only-a", "empty")]),
            _doc("B", "degraded", [("CDM-Nav-LCKUP", "empty")]),
            _doc("C", "ok", [("CDM-Nav-LCKUP", "ok")])]
    repeated = summarize_run.summarize(docs, io.StringIO())
    assert repeated == [("CDM-Nav-LCKUP", ["A", "B"])]     # not 'only-a', and not the ok one


def test_no_recurring_titles():
    out = io.StringIO()
    assert summarize_run.summarize([_doc("A", "ok", [("x", "ok")])], out) == []
    assert "(none)" in out.getvalue()
