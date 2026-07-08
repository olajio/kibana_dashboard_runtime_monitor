"""Write collector documents to Elasticsearch and apply index assets.

Uses the REST API directly (requests) so there is no elasticsearch-py version
coupling to the target cluster.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

import requests

from .config import Settings

_ASSET_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "es")


def _headers(settings: Settings) -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if settings.elasticsearch.api_key:
        h["Authorization"] = f"ApiKey {settings.elasticsearch.api_key}"
    return h


def bulk_index(settings: Settings, docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Index every document in one _bulk request. Returns the parsed response."""
    if not docs:
        return {"errors": False, "items": []}
    index = settings.elasticsearch.index
    lines = []
    for d in docs:
        lines.append(json.dumps({"create": {"_index": index}}))
        lines.append(json.dumps(d))
    body = "\n".join(lines) + "\n"

    resp = requests.post(
        f"{settings.elasticsearch.base_url}/_bulk",
        headers=_headers(settings),
        data=body,
        verify=settings.elasticsearch.verify_tls,
        timeout=60,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("errors"):
        # surface the first error to the caller/logs
        for item in result.get("items", []):
            err = item.get("create", {}).get("error")
            if err:
                raise RuntimeError(f"bulk index error: {err}")
    return result


def apply_assets(settings: Settings) -> None:
    """Create/refresh the ILM policy and index template from es/*.json."""
    es = settings.elasticsearch.base_url

    with open(os.path.join(_ASSET_DIR, "ilm_policy.json")) as f:
        ilm = json.load(f)
    r = requests.put(
        f"{es}/_ilm/policy/dashboard-health-monitor",
        headers=_headers(settings),
        data=json.dumps(ilm),
        verify=settings.elasticsearch.verify_tls,
        timeout=30,
    )
    r.raise_for_status()
    print("Applied ILM policy: dashboard-health-monitor")

    with open(os.path.join(_ASSET_DIR, "index_template.json")) as f:
        tmpl = json.load(f)
    r = requests.put(
        f"{es}/_index_template/dashboard-health-monitor",
        headers=_headers(settings),
        data=json.dumps(tmpl),
        verify=settings.elasticsearch.verify_tls,
        timeout=30,
    )
    r.raise_for_status()
    print("Applied index template: dashboard-health-monitor")
