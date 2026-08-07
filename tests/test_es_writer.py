"""Tests for the ES writer's retry/backoff and bulk chunking (no network)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
import requests

from dhm import es_writer as esw
from dhm.config import Settings


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {"errors": False, "items": []}
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    """Records requests and returns a scripted sequence of responses."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def request(self, method, url, headers=None, data=None, verify=None, timeout=None):
        self.calls.append({"method": method, "url": url, "data": data})
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(esw.time, "sleep", lambda s: None)


def _settings(**es):
    s = Settings()
    for k, v in es.items():
        setattr(s.elasticsearch, k, v)
    s.elasticsearch.base_url = "https://es.example"
    return s


def test_chunks_splits_evenly():
    assert list(esw._chunks(list(range(5)), 2)) == [[0, 1], [2, 3], [4]]


def test_retries_transient_then_succeeds():
    s = _settings(max_retries=3)
    session = FakeSession([FakeResponse(503), FakeResponse(200)])
    resp = esw._request_with_retries(session, s, "POST", "https://es.example/_bulk", "{}")
    assert resp.status_code == 200
    assert len(session.calls) == 2  # retried once


def test_retries_exhausted_raises():
    s = _settings(max_retries=2)
    session = FakeSession([FakeResponse(503), FakeResponse(503), FakeResponse(503)])
    with pytest.raises(requests.HTTPError):
        esw._request_with_retries(session, s, "POST", "https://es.example/_bulk", "{}")
    assert len(session.calls) == 3  # initial + 2 retries


def test_retries_on_connection_error():
    s = _settings(max_retries=3)
    session = FakeSession([requests.ConnectionError("reset"), FakeResponse(200)])
    resp = esw._request_with_retries(session, s, "POST", "https://es.example/_bulk", "{}")
    assert resp.status_code == 200
    assert len(session.calls) == 2


def test_bulk_index_chunks(monkeypatch):
    s = _settings(bulk_chunk_size=2)
    session = FakeSession([FakeResponse(200), FakeResponse(200), FakeResponse(200)])
    monkeypatch.setattr(esw.requests, "Session", lambda: session)

    docs = [{"n": i} for i in range(5)]  # 5 docs, chunk size 2 -> 3 requests
    result = esw.bulk_index(s, docs)
    assert result["indexed"] == 5
    assert len(session.calls) == 3
    # each request body is newline-delimited: 2 lines per doc, joined + trailing newline
    first_body = session.calls[0]["data"]
    assert first_body.count("\n") == 4  # 2 docs -> 4 lines -> 3 joins + 1 trailing


def test_bulk_index_empty_is_noop():
    s = _settings()
    assert esw.bulk_index(s, [])["indexed"] == 0


def test_bulk_index_item_error_raises(monkeypatch):
    s = _settings()
    err = {"errors": True, "items": [{"create": {"error": {"type": "mapper_parsing"}}}]}
    session = FakeSession([FakeResponse(200, json_data=err)])
    monkeypatch.setattr(esw.requests, "Session", lambda: session)
    with pytest.raises(RuntimeError):
        esw.bulk_index(s, [{"n": 1}])
