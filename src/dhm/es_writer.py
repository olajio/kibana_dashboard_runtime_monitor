"""Write collector documents to Elasticsearch and apply index assets.

Uses the REST API directly (requests) so there is no elasticsearch-py version
coupling to the target cluster. Operational hardening:
  - a reused requests.Session (connection pooling);
  - retries with exponential backoff on 429 / 5xx / connection errors, honouring
    a Retry-After header when present;
  - bulk writes chunked to a configurable size so one request never gets huge;
  - configurable request timeouts so a hung cluster cannot stall the run.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

import requests

from .config import Settings

_ASSET_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "es")

# HTTP statuses worth retrying (transient).
_RETRYABLE = {429, 502, 503, 504}


def _headers(settings: Settings) -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if settings.elasticsearch.api_key:
        h["Authorization"] = f"ApiKey {settings.elasticsearch.api_key}"
    return h


def _request_with_retries(
    session: requests.Session,
    settings: Settings,
    method: str,
    url: str,
    data: str,
) -> requests.Response:
    """Issue a request, retrying transient failures with exponential backoff."""
    es = settings.elasticsearch
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = session.request(
                method,
                url,
                headers=_headers(settings),
                data=data,
                verify=es.verify_tls,
                timeout=es.request_timeout_s,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt > es.max_retries:
                raise
            _sleep_backoff(settings, attempt, reason=f"{type(exc).__name__}")
            continue

        if resp.status_code in _RETRYABLE and attempt <= es.max_retries:
            # Respect Retry-After (seconds) if the server sent one.
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.isdigit() else None
            _sleep_backoff(settings, attempt, reason=f"HTTP {resp.status_code}", override=wait)
            continue

        resp.raise_for_status()
        return resp


def _sleep_backoff(settings: Settings, attempt: int, reason: str, override: float = None) -> None:
    wait = override if override is not None else settings.elasticsearch.retry_backoff_s * (2 ** (attempt - 1))
    print(f"    ES retry {attempt}/{settings.elasticsearch.max_retries} after {reason}; waiting {wait:.1f}s")
    time.sleep(wait)


def _chunks(items: List[Any], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def bulk_index(settings: Settings, docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Index every document via _bulk, chunked and retried. Raises on any item error."""
    if not docs:
        return {"errors": False, "indexed": 0}

    index = settings.elasticsearch.index
    url = f"{settings.elasticsearch.base_url}/_bulk"
    indexed = 0

    with requests.Session() as session:
        for chunk in _chunks(docs, settings.elasticsearch.bulk_chunk_size):
            lines = []
            for d in chunk:
                lines.append(json.dumps({"create": {"_index": index}}))
                lines.append(json.dumps(d))
            body = "\n".join(lines) + "\n"

            resp = _request_with_retries(session, settings, "POST", url, body)
            result = resp.json()
            if result.get("errors"):
                for item in result.get("items", []):
                    err = item.get("create", {}).get("error")
                    if err:
                        raise RuntimeError(f"bulk index error: {err}")
            indexed += len(chunk)

    return {"errors": False, "indexed": indexed}


def apply_assets(settings: Settings) -> None:
    """Create/refresh the ILM policy and index template from es/*.json."""
    es = settings.elasticsearch.base_url
    with requests.Session() as session:
        with open(os.path.join(_ASSET_DIR, "ilm_policy.json")) as f:
            ilm = f.read()
        _request_with_retries(
            session, settings, "PUT", f"{es}/_ilm/policy/dashboard-health-monitor", ilm
        )
        print("Applied ILM policy: dashboard-health-monitor")

        with open(os.path.join(_ASSET_DIR, "index_template.json")) as f:
            tmpl = f.read()
        _request_with_retries(
            session, settings, "PUT", f"{es}/_index_template/dashboard-health-monitor", tmpl
        )
        print("Applied index template: dashboard-health-monitor")
