"""Live registry discovery from Kibana's Saved Objects API.

This is the production path: instead of reading a static `.ndjson` export from
disk, we ask Kibana for the current dashboards and their panels on every run. That
means the monitor always reflects production as it is *right now* — a dashboard or
panel added, removed, or renamed in Kibana is picked up automatically on the next
cycle, with no export file to keep on the server.

The Saved Objects `_find` response returns objects in the same shape as an export
line, so we reuse `registry.registry_from_objects` and get an identical registry.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

import requests

from .config import Settings
from .registry import Registry, registry_from_objects

_RETRYABLE = {429, 502, 503, 504}


def _kibana_headers(settings: Settings) -> Dict[str, str]:
    h = {"kbn-xsrf": "dhm", "Content-Type": "application/json"}
    auth = settings.kibana.auth
    if auth.method == "api_key" and auth.api_key:
        h["Authorization"] = f"ApiKey {auth.api_key}"
    elif auth.method == "cookie" and auth.cookie_value:
        h["Cookie"] = f"{auth.cookie_name}={auth.cookie_value}"
    return h


def _space_prefix(settings: Settings) -> str:
    space = settings.kibana_space
    return "" if space in ("", "default") else f"/s/{space}"


def _get_with_retries(session: requests.Session, settings: Settings, url: str, params: Dict[str, Any]):
    es = settings.elasticsearch  # reuse the shared HTTP resilience knobs
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = session.get(
                url,
                headers=_kibana_headers(settings),
                params=params,
                verify=settings.kibana.verify_tls,
                timeout=es.request_timeout_s,
            )
        except (requests.ConnectionError, requests.Timeout):
            if attempt > es.max_retries:
                raise
            time.sleep(es.retry_backoff_s * (2 ** (attempt - 1)))
            continue
        if resp.status_code in _RETRYABLE and attempt <= es.max_retries:
            time.sleep(es.retry_backoff_s * (2 ** (attempt - 1)))
            continue
        resp.raise_for_status()
        return resp


def fetch_saved_objects(settings: Settings) -> List[Dict[str, Any]]:
    """Page through the Saved Objects API and return all `dashboard` and `links`
    objects in the configured space. Links objects are needed to resolve
    navigation-menu destinations for reachability."""
    base = settings.kibana.base_url + _space_prefix(settings)
    url = f"{base}/api/saved_objects/_find"
    per_page = 1000
    page = 1
    out: List[Dict[str, Any]] = []

    with requests.Session() as session:
        while True:
            # multiple `type` params -> type=dashboard&type=links
            params = {"type": ["dashboard", "links"], "per_page": per_page, "page": page}
            data = _get_with_retries(session, settings, url, params).json()
            objs = data.get("saved_objects", [])
            out.extend(objs)
            total = data.get("total", len(out))
            if page * per_page >= total or not objs:
                break
            page += 1
    return out


def build_registry_from_api(settings: Settings) -> Registry:
    """Build a registry of every dashboard in the space from the live Saved
    Objects API. Which dashboards we actually monitor is decided by the caller
    (`registry.select_registry`), so both registry sources behave identically."""
    objs = fetch_saved_objects(settings)
    return registry_from_objects(
        objs, settings.app, generated_from=f"kibana-api:{settings.kibana_space}"
    )
