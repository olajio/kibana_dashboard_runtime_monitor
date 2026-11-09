"""check_access.py: pattern discovery and the per-pattern verdicts (no network)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import check_access as ca


class Resp:
    def __init__(self, status, body, headers=None, text=""):
        self.status_code, self._body = status, body
        self.headers, self.text = headers or {}, text

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class Session:
    """Scripted GETs keyed by URL suffix; records what was asked."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def post(self, url, headers=None, json=None, verify=None, timeout=None):
        self.calls.append((url, dict(headers or {})))
        for suffix, resp in self.routes.items():
            if url.endswith(suffix):
                return resp
        return Resp(404, {"error": {"type": "not_found"}})

    def get(self, url, headers=None, verify=None, timeout=None):
        self.calls.append((url, dict(headers or {})))
        for suffix, resp in self.routes.items():
            if url.endswith(suffix):
                return resp
        return Resp(404, {"error": {"type": "index_not_found_exception"}})


def test_patterns_come_from_the_export_and_split_on_commas(tmp_path):
    f = tmp_path / "x.ndjson"
    f.write_text("\n".join(json.dumps(o) for o in [
        {"type": "index-pattern", "attributes": {"title": "a*:one,a*:two"}},
        {"type": "index-pattern", "attributes": {"title": "cdm_cyhy_vuln"}},
        {"type": "dashboard", "attributes": {"title": "not a data view"}},
        {"type": "index-pattern", "attributes": {"title": "cdm_cyhy_vuln"}},   # duplicate
    ]) + "\nnot json\n")
    assert ca.parse_patterns(str(f)) == ["a*:one", "a*:two", "cdm_cyhy_vuln"]


def test_the_real_export_yields_the_expected_patterns():
    export = ca.DEFAULT_EXPORT
    if not os.path.exists(export):
        return
    found = ca.parse_patterns(export)
    assert "cdm_cyhy_vuln" in found and "cdm_cyhy_host_scans" in found
    assert any(":" in p for p in found)          # cross-cluster patterns are included


def test_readable_empty_denied_and_error_are_told_apart():
    s = Session({
        "/p_ok/_count": Resp(200, {"count": 1234}),
        "/p_zero/_count": Resp(200, {"count": 0}),
        "/p_denied/_count": Resp(403, {"error": {"type": "security_exception"}}),
    })
    rows = {p: ca.count_pattern(s, "https://es", {}, True, p)
            for p in ("p_ok", "p_zero", "p_denied", "p_missing")}
    assert {p: ca.classify(r) for p, r in rows.items()} == {
        "p_ok": "readable", "p_zero": "EMPTY", "p_denied": "DENIED", "p_missing": "ERROR"}
    assert rows["p_denied"]["reason"] == "security_exception"
    assert rows["p_missing"]["reason"] == "index_not_found_exception"


def test_empty_is_the_silent_case_a_panel_would_show_as_no_results():
    # a count of 0 with HTTP 200 is exactly what makes a panel render as "empty" rather than error
    r = ca.count_pattern(Session({"/x/_count": Resp(200, {"count": 0})}), "https://es", {}, True, "x")
    assert ca.classify(r) == "EMPTY"


def test_a_network_failure_is_reported_not_raised():
    import requests

    class Boom:
        def get(self, *a, **k):
            raise requests.ConnectionError("down")
    r = ca.count_pattern(Boom(), "https://es", {}, True, "x")
    assert r["status"] == 0 and ca.classify(r) == "ERROR"


def test_authenticate_reports_identity_but_never_the_key():
    body = {"username": "svc", "authentication_type": "api_key", "roles": ["r1"],
            "api_key": {"id": "abc", "name": "dhm"}, "authentication_realm": {"name": "_es_api_key"}}
    s = Session({"/_security/_authenticate": Resp(200, body)})
    who = ca.authenticate(s, "https://es", {"Authorization": "ApiKey SECRET"}, True)
    assert who["username"] == "svc" and who["api_key"] == "dhm" and who["roles"] == ["r1"]
    assert "SECRET" not in json.dumps(who)


def test_authenticate_failure_is_reported():
    s = Session({"/_security/_authenticate": Resp(401, {"error": {"type": "security_exception"}})})
    assert ca.authenticate(s, "https://es", {}, True) == {"error": "security_exception", "status": 401}


def test_table_has_a_summary_line():
    text = ca.format_table({"a": {"status": 200, "count": 5}, "b": {"status": 403, "reason": "security_exception"},
                            "c": {"status": 200, "count": 0}})
    assert "readable" in text and "DENIED" in text and "EMPTY" in text
    assert text.strip().splitlines()[-1] == "summary: DENIED=1, EMPTY=1, readable=1"


def test_cluster_identity_reports_name_and_uuid():
    s = Session({"es/": Resp(200, {"cluster_name": "fed", "cluster_uuid": "abc", "version": {"number": "9.1.0"}})})
    assert ca.cluster_identity(s, "http://es", {}, True) == {
        "cluster_name": "fed", "cluster_uuid": "abc", "version": "9.1.0"}


def test_cluster_identity_failure_is_reported_not_raised():
    s = Session({"es/": Resp(401, {"error": {"type": "security_exception"}})})
    assert ca.cluster_identity(s, "http://es", {}, True) == {"error": "security_exception"}


def test_a_non_elasticsearch_404_says_what_answered():
    page = Resp(404, ValueError("no json"), {"Content-Type": "text/html; charset=utf-8"},
                "<html>\n  Not   Found </html>")
    s = Session({"es/": page})
    out = ca.cluster_identity(s, "http://es", {}, True)
    assert "not-elasticsearch" in out["error"] and "text/html" in out["error"]
    assert "Not Found" in out["error"]


def test_elastic_cloud_proxy_message_is_shown():
    s = Session({"es/": Resp(404, {"ok": False, "message": "Unknown resource."})})
    assert ca.cluster_identity(s, "http://es", {}, True) == {"error": "HTTP 404 'Unknown resource.'"}


def test_write_access_reports_the_missing_privileges():
    body = {"has_all_requested": False, "index": {"dashboard-health-monitor": {
        "create_doc": True, "auto_configure": False, "create_index": False}}}
    s = Session({"_has_privileges": Resp(200, body)})
    assert ca.check_write_access(s, "http://es", {}, True) == {
        "ok": False, "missing": ["auto_configure", "create_index"]}


def test_write_access_ok_when_everything_is_granted():
    body = {"has_all_requested": True, "index": {"dashboard-health-monitor": {
        "create_doc": True, "auto_configure": True, "create_index": True}}}
    s = Session({"_has_privileges": Resp(200, body)})
    assert ca.check_write_access(s, "http://es", {}, True) == {"ok": True, "missing": []}


def test_write_access_failure_is_reported_not_raised():
    s = Session({"_has_privileges": Resp(401, {"error": {"type": "security_exception"}})})
    assert ca.check_write_access(s, "http://es", {}, True) == {"error": "security_exception"}


def test_data_stream_status_reports_template_policy_and_count():
    ds = {"data_streams": [{"template": "dashboard-health-monitor", "ilm_policy": "dashboard-health-monitor",
                            "indices": [{"index_name": ".ds-x-1"}]}]}
    s = Session({"_data_stream/dashboard-health-monitor": Resp(200, ds),
                 "dashboard-health-monitor/_count": Resp(200, {"count": 22})})
    assert ca.data_stream_status(s, "http://es", {}, True) == {
        "exists": True, "template": "dashboard-health-monitor",
        "ilm_policy": "dashboard-health-monitor", "backing_indices": 1, "count": 22}


def test_data_stream_status_when_nothing_was_written():
    s = Session({"_data_stream/dashboard-health-monitor": Resp(404, {"error": {"type": "index_not_found_exception"}})})
    assert ca.data_stream_status(s, "http://es", {}, True) == {"exists": False}
