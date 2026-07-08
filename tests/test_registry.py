"""Tests for the registry builder, run against the real Federal Overview export."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from dhm.registry import (
    build_registry,
    DATA_PANEL_TYPES,
    NAV_PANEL_TYPES,
)

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
