"""Backend-agnostic collection core.

The timing loop, per-panel reconciliation, and Elasticsearch document assembly
are identical whether the browser is driven by Playwright or Selenium. Both
backends provide a small `Driver` (goto / wait_for_panel / read_panel_states)
and call `collect_dashboard` here, so the health logic and document schema live
in exactly one place.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol

from . import __version__
from .config import Settings
from .render_detection import is_resolved, reconcile, summarize


class Driver(Protocol):
    """What the core needs from a browser backend."""

    def goto(self, url: str, timeout_ms: int) -> None: ...

    def wait_for_panel(self, timeout_ms: int) -> None: ...

    def read_panel_states(self) -> List[Dict[str, Any]]: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dashboard_url(settings: Settings, dashboard_id: str) -> str:
    base = settings.kibana.base_url
    space = settings.kibana_space
    prefix = "" if space in ("", "default") else f"/s/{space}"
    g = f"(time:(from:{settings.kibana.time_from},to:{settings.kibana.time_to}))"
    return f"{base}{prefix}/app/dashboards#/view/{dashboard_id}?_g={g}"


def panel_key(state: Dict[str, Any]) -> str:
    return state.get("id") or state.get("title") or f"idx:{state.get('index')}"


def load_status(
    settings: Settings, load_time_ms: int, roll: Dict[str, int], load_error: Optional[str]
) -> str:
    if load_error or roll["panels_missing"] or roll["panels_error"] or roll["panels_timeout"]:
        return "failed"
    if load_time_ms >= settings.collector.failed_over_ms:
        return "failed"
    if load_time_ms >= settings.collector.degraded_over_ms or roll["panels_empty"]:
        return "degraded"
    return "ok"


def _assemble_doc(
    settings: Settings,
    dashboard: Dict[str, Any],
    run_id: str,
    url: str,
    load_time_ms: int,
    load_error: Optional[str],
    panel_records: List[Dict[str, Any]],
    roll: Dict[str, int],
) -> Dict[str, Any]:
    """Build the single ES document. One place owns the schema."""
    expected_panels = dashboard.get("panels", [])
    return {
        "@timestamp": _now_iso(),
        "schema_version": 1,
        "app": settings.app,
        "cluster": settings.cluster,
        "kibana_space": settings.kibana_space,
        "dashboard_id": dashboard["dashboard_id"],
        "dashboard_title": dashboard["title"],
        "is_hub": dashboard.get("is_hub", False),
        "dashboard_url": url,
        "load_time_ms": load_time_ms,
        "load_status": load_status(settings, load_time_ms, roll, load_error),
        "load_error": load_error,
        "expected_data_panels": dashboard.get("data_panel_count", len(expected_panels)),
        "panel_count": dashboard.get("panel_count"),
        **roll,
        "panels": panel_records,
        "collector_run_id": run_id,
        "collector_version": __version__,
    }


def collect_dashboard(
    driver: Driver,
    settings: Settings,
    dashboard: Dict[str, Any],
    run_id: str,
) -> Dict[str, Any]:
    """Load one dashboard through `driver` and return its health document."""
    url = dashboard_url(settings, dashboard["dashboard_id"])
    timeout_ms = settings.collector.dashboard_timeout_ms
    poll_ms = settings.collector.poll_interval_ms
    attempts = max(1, settings.collector.load_retries + 1)

    resolve_times: Dict[str, int] = {}
    last_states: List[Dict[str, Any]] = []
    load_error: Optional[str] = None
    t0 = time.monotonic()

    # Navigate, retrying the whole load a bounded number of times.
    for attempt in range(1, attempts + 1):
        t0 = time.monotonic()
        load_error = None
        try:
            driver.goto(url, timeout_ms)
            # Wait until at least one panel exists (dashboard shell rendered).
            driver.wait_for_panel(timeout_ms)
            break
        except Exception as exc:  # navigation/auth failure
            load_error = f"navigation failed (attempt {attempt}/{attempts}): {type(exc).__name__}: {exc}"

    if load_error is None:
        deadline = t0 + timeout_ms / 1000.0
        while True:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            states = driver.read_panel_states() or []
            if states:
                last_states = states
                for st in states:
                    key = panel_key(st)
                    if key not in resolve_times and is_resolved(st):
                        resolve_times[key] = elapsed_ms
                # done when every panel currently on the page has resolved
                if all(is_resolved(s) for s in states):
                    break
            if time.monotonic() >= deadline:
                break
            time.sleep(poll_ms / 1000.0)

    expected_panels = dashboard.get("panels", [])
    panel_records = reconcile(expected_panels, last_states, resolve_times)
    roll = summarize(panel_records)

    # Dashboard load time = when the last expected data panel resolved.
    panel_times = [r["render_ms"] for r in panel_records if r["render_ms"] is not None]
    load_time_ms = max(panel_times) if panel_times else int((time.monotonic() - t0) * 1000)

    return _assemble_doc(
        settings, dashboard, run_id, url, load_time_ms, load_error, panel_records, roll
    )


def safe_collect_dashboard(
    driver: Driver, settings: Settings, dashboard: Dict[str, Any], run_id: str
) -> Dict[str, Any]:
    """Collect a dashboard, never raising: an unexpected error becomes a `failed`
    document so one bad dashboard cannot abort the whole run."""
    try:
        return collect_dashboard(driver, settings, dashboard, run_id)
    except Exception as exc:
        url = dashboard_url(settings, dashboard.get("dashboard_id", ""))
        panel_records = reconcile(dashboard.get("panels", []), [], {})
        roll = summarize(panel_records)
        return _assemble_doc(
            settings, dashboard, run_id, url, 0,
            f"collector error: {type(exc).__name__}: {exc}", panel_records, roll,
        )


def collect_all(
    driver: Driver, settings: Settings, dashboards: List[Dict[str, Any]], run_id: str
) -> List[Dict[str, Any]]:
    """Collect every dashboard in order, pacing between loads and isolating
    per-dashboard failures. Shared by both browser backends."""
    docs: List[Dict[str, Any]] = []
    delay = settings.collector.inter_request_delay_ms / 1000.0
    total = len(dashboards)
    for i, d in enumerate(dashboards):
        doc = safe_collect_dashboard(driver, settings, d, run_id)
        docs.append(doc)
        print_row(doc)
        if delay and i < total - 1:
            time.sleep(delay)
    return docs


def print_row(doc: Dict[str, Any]) -> None:
    print(
        f"  {doc['dashboard_title'][:40]:40s} "
        f"{doc['load_status']:8s} "
        f"{doc['load_time_ms']:6d}ms "
        f"ok={doc['panels_ok']} not_ok={doc['panels_not_ok']}"
    )
