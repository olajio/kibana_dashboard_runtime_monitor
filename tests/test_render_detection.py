"""Tests for the panel render-state classifier and reconciliation.

These are the highest-risk logic in the collector, so they are tested directly
with the raw signal dictionaries that selectors.PANEL_STATE_JS produces — no
browser required.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dhm.render_detection import (
    classify_panel,
    is_resolved,
    reconcile,
    summarize,
    OK,
    EMPTY,
    ERROR,
    TIMEOUT,
    MISSING,
)


def _state(**kw):
    base = {
        "id": None,
        "title": "",
        "index": 0,
        "renderComplete": False,
        "loading": False,
        "hasError": False,
        "emptyText": None,
    }
    base.update(kw)
    return base


def test_classify_ok():
    assert classify_panel(_state(renderComplete=True)) == OK


def test_classify_empty_even_when_render_complete():
    # An empty panel finished rendering; it must classify as empty, not ok.
    s = _state(renderComplete=True, emptyText="No results found")
    assert classify_panel(s) == EMPTY


def test_classify_error_wins_over_everything():
    s = _state(renderComplete=True, emptyText="No results found", hasError=True)
    assert classify_panel(s) == ERROR


def test_classify_timeout_when_unresolved():
    assert classify_panel(_state(loading=True)) == TIMEOUT


def test_is_resolved():
    assert is_resolved(_state(renderComplete=True))
    assert is_resolved(_state(emptyText="No results found"))
    assert is_resolved(_state(hasError=True))
    assert not is_resolved(_state(loading=True))


def test_reconcile_matches_by_id_and_records_timing():
    expected = [
        {"panel_id": "p1", "title": "Connected Agencies", "panel_type": "lens", "is_data_panel": True},
    ]
    observed = [_state(id="p1", title="Connected Agencies", renderComplete=True)]
    times = {"p1": 1234}
    recs = reconcile(expected, observed, times)
    assert len(recs) == 1
    assert recs[0]["render_status"] == OK
    assert recs[0]["render_ms"] == 1234


def test_reconcile_matches_by_title_when_id_absent():
    expected = [
        {"panel_id": "", "title": "Disconnected Agencies", "panel_type": "lens", "is_data_panel": True},
    ]
    observed = [_state(id=None, title="Disconnected Agencies", emptyText="No results found")]
    recs = reconcile(expected, observed, {"Disconnected Agencies": 555})
    assert recs[0]["render_status"] == EMPTY
    assert recs[0]["render_ms"] == 555


def test_reconcile_flags_missing_expected_panel():
    expected = [
        {"panel_id": "gone", "title": "Vanished Panel", "panel_type": "lens", "is_data_panel": True},
    ]
    recs = reconcile(expected, [], {})
    assert recs[0]["render_status"] == MISSING
    assert recs[0]["render_ms"] is None


def test_reconcile_skips_navigation_panels():
    expected = [
        {"panel_id": "nav", "title": "Main Navigation", "panel_type": "links", "is_data_panel": False},
        {"panel_id": "p1", "title": "Chart", "panel_type": "lens", "is_data_panel": True},
    ]
    observed = [_state(id="p1", title="Chart", renderComplete=True)]
    recs = reconcile(expected, observed, {"p1": 10})
    # only the data panel is checked
    assert len(recs) == 1
    assert recs[0]["panel_id"] == "p1"


def test_summarize_counts():
    recs = [
        {"render_status": OK},
        {"render_status": OK},
        {"render_status": EMPTY},
        {"render_status": ERROR},
        {"render_status": TIMEOUT},
        {"render_status": MISSING},
    ]
    s = summarize(recs)
    assert s["panels_checked"] == 6
    assert s["panels_ok"] == 2
    assert s["panels_not_ok"] == 4
    assert s["panels_empty"] == 1
    assert s["panels_error"] == 1
    assert s["panels_timeout"] == 1
    assert s["panels_missing"] == 1
