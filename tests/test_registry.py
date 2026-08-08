"""Tests for the registry builder, run against the real Federal Overview export."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from dhm.registry import (
    build_registry,
    filter_registry_dict,
    reachable_from_hub,
    select_registry,
    registry_from_objects,
    registry_to_dict,
    DATA_PANEL_TYPES,
    NAV_PANEL_TYPES,
)


def _reg_dict():
    # hub -> d2 (drilldown) and via nav -> d3; d4 is unrelated/unreachable
    return {
        "dashboard_count": 4,
        "dashboards": [
            {"title": "Federal Overview", "dashboard_id": "hub", "linked_dashboards": ["d2", "d3"]},
            {"title": "Agency Details", "dashboard_id": "d2", "linked_dashboards": []},
            {"title": "CyHy Overview", "dashboard_id": "d3", "linked_dashboards": ["hub"]},
            {"title": "Unrelated Dashboard", "dashboard_id": "d4", "linked_dashboards": []},
        ],
    }


def test_filter_titles_case_insensitive():
    out = filter_registry_dict(_reg_dict(), ["federal overview", "AGENCY DETAILS"])
    assert {d["dashboard_id"] for d in out["dashboards"]} == {"hub", "d2"}


def test_filter_empty_means_all():
    assert filter_registry_dict(_reg_dict(), [])["dashboard_count"] == 4


def test_reachable_from_hub_includes_hub_and_linked():
    ids = reachable_from_hub(_reg_dict(), "Federal Overview")
    assert ids == {"hub", "d2", "d3"}  # d4 unreachable


def test_reachable_hub_not_found_is_empty():
    assert reachable_from_hub(_reg_dict(), "Nonexistent") == set()


def test_select_linked_is_hub_plus_reachable():
    out = select_registry(_reg_dict(), "linked", "Federal Overview", [])
    assert {d["dashboard_id"] for d in out["dashboards"]} == {"hub", "d2", "d3"}


def test_select_all():
    assert select_registry(_reg_dict(), "all", "Federal Overview", [])["dashboard_count"] == 4


def test_select_titles():
    out = select_registry(_reg_dict(), "titles", "Federal Overview", ["Agency Details"])
    assert {d["dashboard_id"] for d in out["dashboards"]} == {"d2"}

NDJSON = os.path.join(os.path.dirname(__file__), "..", "federal_overview.ndjson")

pytestmark = pytest.mark.skipif(
    not os.path.exists(NDJSON), reason="federal_overview.ndjson not present"
)


@pytest.fixture(scope="module")
def reg():
    return build_registry(NDJSON, app="federal_overview")


def test_all_dashboards_discovered(reg):
    assert reg.dashboard_count == 22
    assert len(reg.dashboards) == 22


def test_hub_is_federal_overview(reg):
    hub = [d for d in reg.dashboards if d.is_hub]
    assert len(hub) == 1
    assert hub[0].title == "Federal Overview"
    assert reg.hub_dashboard_id == hub[0].dashboard_id
    # hub is emitted first for stable ordering
    assert reg.dashboards[0].is_hub


def test_every_dashboard_has_an_id_and_title(reg):
    for d in reg.dashboards:
        assert d.dashboard_id
        assert d.title


def test_panel_counts_are_consistent(reg):
    for d in reg.dashboards:
        assert d.panel_count == len(d.panels)
        assert d.data_panel_count == sum(1 for p in d.panels if p.is_data_panel)


def test_data_vs_nav_classification(reg):
    for d in reg.dashboards:
        for p in d.panels:
            if p.panel_type in DATA_PANEL_TYPES:
                assert p.is_data_panel is True
            elif p.panel_type in NAV_PANEL_TYPES:
                assert p.is_data_panel is False


def test_total_data_panels(reg):
    total = sum(d.data_panel_count for d in reg.dashboards)
    # 131 lens + 61 visualization + 23 search = 215 renderable data panels
    assert total == 215


def test_saved_object_resolution_rate(reg):
    total = resolved = 0
    for d in reg.dashboards:
        for p in d.panels:
            if p.is_data_panel:
                total += 1
                if p.saved_object_id:
                    resolved += 1
    # by-value panels legitimately have no saved object; most are by-reference
    assert resolved / total > 0.9


def test_hub_links_to_other_dashboards(reg):
    hub = next(d for d in reg.dashboards if d.is_hub)
    assert len(hub.linked_dashboards) >= 1
    known_ids = {d.dashboard_id for d in reg.dashboards}
    for did in hub.linked_dashboards:
        assert did in known_ids


def test_federal_overview_reaches_all_22(reg):
    # Following the hub's navigation transitively reaches every dashboard.
    reg_dict = registry_to_dict(reg)
    ids = reachable_from_hub(reg_dict, "Federal Overview")
    assert len(ids) == 22
    linked = select_registry(reg_dict, "linked", "Federal Overview", [])
    assert linked["dashboard_count"] == 22
