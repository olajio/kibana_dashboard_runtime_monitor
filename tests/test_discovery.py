"""Tests for live Saved Objects API discovery (no real Kibana)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dhm import discovery
from dhm.config import Settings
from dhm.registry import registry_from_objects


def _dash_obj(did, title, panels):
    """panels: list of (type, title) tuples."""
    pj = json.dumps([
        {"type": t, "title": ti, "panelIndex": f"pi{i}", "panelRefName": f"panel_pi{i}"}
        for i, (t, ti) in enumerate(panels)
    ])
    refs = [
        {"name": f"pi{i}:panel_pi{i}", "type": t, "id": f"so{i}"}
        for i, (t, ti) in enumerate(panels)
    ]
    return {"type": "dashboard", "id": did, "attributes": {"title": title, "panelsJSON": pj},
            "references": refs}


class FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, pages):
        self._pages = list(pages)
        self.calls = []

    def get(self, url, headers=None, params=None, verify=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return FakeResp(self._pages.pop(0))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _settings():
    s = Settings()
    s.kibana.base_url = "https://kib.example"
    s.kibana.auth.api_key = "id:key"
    s.kibana_space = "fed2"
    return s


def test_space_prefix():
    s = _settings()
    assert discovery._space_prefix(s) == "/s/fed2"
    s.kibana_space = "default"
    assert discovery._space_prefix(s) == ""


def test_kibana_headers_api_key():
    s = _settings()
    h = discovery._kibana_headers(s)
    assert h["Authorization"] == "ApiKey id:key"
    assert h["kbn-xsrf"] == "dhm"


def test_fetch_dashboard_objects_single_page(monkeypatch):
    s = _settings()
    page = {"saved_objects": [_dash_obj("d1", "A", [("lens", "x")]),
                              _dash_obj("d2", "B", [("lens", "y")])],
            "total": 2, "per_page": 1000, "page": 1}
    session = FakeSession([page])
    monkeypatch.setattr(discovery.requests, "Session", lambda: session)

    objs = discovery.fetch_dashboard_objects(s)
    assert len(objs) == 2
    assert session.calls[0]["params"]["type"] == "dashboard"
    assert session.calls[0]["url"] == "https://kib.example/s/fed2/api/saved_objects/_find"


def test_build_registry_from_api_all(monkeypatch):
    s = _settings()
    s.app = "federal_overview"
    monkeypatch.setattr(discovery, "fetch_dashboard_objects", lambda settings: [
        _dash_obj("hub", "Federal Overview", [("lens", "Connected Agencies"), ("links", "Nav")]),
        _dash_obj("d2", "Agency Details", [("lens", "y")]),
    ])
    reg = discovery.build_registry_from_api(s)
    assert reg.dashboard_count == 2
    hub = [d for d in reg.dashboards if d.is_hub][0]
    assert hub.title == "Federal Overview"
    # links panel recorded but not a data panel
    assert hub.data_panel_count == 1


def test_build_registry_from_api_include_titles(monkeypatch):
    s = _settings()
    monkeypatch.setattr(discovery, "fetch_dashboard_objects", lambda settings: [
        _dash_obj("hub", "Federal Overview", [("lens", "x")]),
        _dash_obj("d2", "Agency Details", [("lens", "y")]),
        _dash_obj("d3", "Unrelated Dashboard", [("lens", "z")]),
    ])
    reg = discovery.build_registry_from_api(s, include_titles=["Federal Overview", "Agency Details"])
    titles = {d.title for d in reg.dashboards}
    assert titles == {"Federal Overview", "Agency Details"}


def test_registry_from_objects_matches_shape():
    objs = [_dash_obj("hub", "Federal Overview", [("lens", "x"), ("visualization", "v")])]
    reg = registry_from_objects(objs, "federal_overview", generated_from="unit")
    assert reg.dashboard_count == 1
    assert reg.dashboards[0].data_panel_count == 2
    assert reg.generated_from == "unit"
