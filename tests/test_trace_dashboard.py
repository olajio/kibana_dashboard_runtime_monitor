"""trace_dashboard.analyze: does a not-ok panel at the collector's stop moment recover?

These use the same state-dict shape that selectors.PANEL_STATE_JS produces, so they
exercise the collector's own classifier and stop condition, with no browser.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import trace_dashboard as td


def S(title, *, done=False, empty=None, err=False, loading=False, idx=0, pid=None):
    return {"id": pid, "title": title, "index": idx, "renderComplete": done,
            "loading": loading, "hasError": err, "emptyText": empty}


def sample(t, *panels):
    return (t, list(panels))


# -- the collector's stop condition ---------------------------------------------------
def test_stop_needs_every_panel_resolved():
    assert td.collector_would_stop([S("a", done=True), S("b", empty="No results")], 2)
    assert not td.collector_would_stop([S("a", done=True), S("b", loading=True)], 2)


def test_stop_needs_the_expected_panel_count():
    # one resolved panel is not enough when the registry says there are two
    assert not td.collector_would_stop([S("a", done=True)], 2)
    assert not td.collector_would_stop([], 0)


def test_an_empty_placeholder_counts_as_resolved():
    # the property under test: this is what lets the collector stop on a loading state
    assert td.collector_would_stop([S("a", empty="No results found")], 1)


# -- analysis --------------------------------------------------------------------------
def test_transient_empty_is_reported_as_read_too_early():
    samples = [
        sample(500, S("A", loading=True, idx=0), S("B", done=True, idx=1)),       # not stopped yet
        sample(1000, S("A", empty="No results", idx=0), S("B", done=True, idx=1)), # collector stops here
        sample(2500, S("A", empty="No results", idx=0), S("B", done=True, idx=1)),
        sample(4000, S("A", done=True, idx=0), S("B", done=True, idx=1)),          # settles
        sample(6000, S("A", done=True, idx=0), S("B", done=True, idx=1)),
    ]
    r = td.analyze(samples, expected_min=2)
    assert r["exit_ms"] == 1000
    assert r["at_exit"]["A"][1] == "empty" and r["final"]["A"][1] == "ok"
    assert [c["title"] for c in r["changed"]] == ["A"]
    assert r["changed"][0]["settled_ms"] == 4000
    assert td.verdict(r).startswith("READ TOO EARLY")


def test_genuinely_empty_panel_is_reported_stable():
    samples = [sample(t, S("A", empty="No results", idx=0), S("B", done=True, idx=1))
               for t in (1000, 5000, 20000)]
    r = td.analyze(samples, expected_min=2)
    assert r["exit_ms"] == 1000 and r["changed"] == []
    assert td.verdict(r).startswith("STABLE")


def test_all_ok_dashboard():
    r = td.analyze([sample(800, S("A", done=True), S("B", done=True, idx=1))], 2)
    assert td.verdict(r).startswith("ALL OK")


def test_never_stopping_is_reported():
    samples = [sample(t, S("A", loading=True), S("B", done=True, idx=1)) for t in (1000, 9000)]
    r = td.analyze(samples, expected_min=2)
    assert r["exit_ms"] is None
    assert td.verdict(r).startswith("NEVER STOPPED")


def test_panel_that_gets_worse_is_flagged():
    samples = [sample(1000, S("A", done=True), S("B", done=True, idx=1)),
               sample(3000, S("A", empty="No results"), S("B", done=True, idx=1))]
    r = td.analyze(samples, expected_min=2)
    assert td.verdict(r).startswith("UNSTABLE")


def test_a_panel_that_disappears_is_marked_gone():
    samples = [sample(1000, S("A", done=True), S("B", empty="x", idx=1)),
               sample(3000, S("A", done=True))]
    r = td.analyze(samples, expected_min=2)
    assert {c["title"]: c["final"] for c in r["changed"]} == {"B": "gone"}


def test_recovery_after_a_flap_reports_the_last_stable_time():
    # empty -> ok -> empty -> ok: settled time is when it finally stays ok
    samples = [sample(1000, S("A", empty="x")), sample(2000, S("A", done=True)),
               sample(3000, S("A", empty="x")), sample(4000, S("A", done=True)),
               sample(5000, S("A", done=True))]
    r = td.analyze(samples, expected_min=1)
    assert r["changed"][0]["settled_ms"] == 4000


def test_report_renders_both_outcomes():
    recovered = td.analyze([sample(1000, S("Slow table", empty="x")), sample(3000, S("Slow table", done=True))], 1)
    text = td.format_report(recovered, "CyHy Overview", 1)
    assert "Slow table" in text and "empty" in text and "READ TOO EARLY" in text
    stable = td.analyze([sample(1000, S("Real gap", empty="x")), sample(3000, S("Real gap", empty="x"))], 1)
    text = td.format_report(stable, "X", 1)
    assert "still not ok" in text and "Real gap" in text and "STABLE" in text


def test_report_lists_what_stays_not_ok_even_when_others_recover():
    # the mixed case from the real dashboard: some panels settle, one never does
    samples = [sample(1000, S("Settles", empty="x", idx=0), S("CDM-Nav-LCKUP", empty="x", idx=1)),
               sample(3000, S("Settles", done=True, idx=0), S("CDM-Nav-LCKUP", empty="x", idx=1))]
    text = td.format_report(td.analyze(samples, 2), "Mixed", 2)
    assert "Settles" in text and "READ TOO EARLY" in text
    tail = text.split("still not ok at the end of the window:")[1]
    assert "CDM-Nav-LCKUP" in tail and "Settles" not in tail


def test_empty_samples_do_not_crash():
    r = td.analyze([], 3)
    assert r["exit_ms"] is None and "NEVER STOPPED" in td.verdict(r)


def test_titles_are_cleaned_like_the_collector_does():
    # real DOM text from the production host: screen-reader label, newline, visible title
    raw = "Panel: CyHy Vulnerability Severity Levels\nCyHy Vulnerability Severity Levels"
    r = td.analyze([sample(1000, S(raw, empty="x"))], expected_min=1)
    assert [t for t, _s in r["at_exit"].values()] == ["CyHy Vulnerability Severity Levels"]
    report = td.format_report(r, "X", 1)
    assert "Panel:" not in report                         # the screen-reader prefix does not leak
    assert report.count("CyHy Vulnerability Severity Levels") == 1   # and the title is not doubled


def test_untitled_error_panels_are_named_by_position():
    r = td.analyze([sample(1000, S("", err=True, idx=7))], expected_min=1)
    assert [t for t, _s in r["at_exit"].values()] == ["(untitled panel #7)"]
