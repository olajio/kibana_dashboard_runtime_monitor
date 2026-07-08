"""Pure classification logic for panel render state.

Kept free of any browser/Playwright dependency so it can be unit tested with
plain dictionaries. `selectors.PANEL_STATE_JS` produces the raw signals; the
functions here turn those signals into a health verdict and reconcile the
observed panels against the expected panels from the registry.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Terminal render states, most-to-least severe for rollups.
OK = "ok"
EMPTY = "empty"
ERROR = "error"
TIMEOUT = "timeout"
MISSING = "missing"  # expected (per registry) but never seen in the DOM


def classify_panel(state: Dict[str, Any]) -> str:
    """Classify one panel's raw DOM signals into a render status.

    Order matters: an error or empty panel has also "finished rendering", so
    those must be checked before OK. A panel that never resolved is a timeout.
    """
    if state.get("hasError"):
        return ERROR
    if state.get("emptyText"):
        return EMPTY
    if state.get("renderComplete"):
        return OK
    return TIMEOUT


def is_resolved(state: Dict[str, Any]) -> bool:
    """True once a panel has reached any terminal state (used for timing)."""
    return bool(
        state.get("hasError")
        or state.get("emptyText")
        or state.get("renderComplete")
    )


def _match_observed(
    expected_id: str,
    expected_title: str,
    observed: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Find the observed panel for an expected registry panel.

    Prefer the embeddable id (== panelIndex), then a non-empty title match.
    """
    if expected_id:
        for o in observed:
            if o.get("id") and o["id"] == expected_id:
                return o
    if expected_title:
        for o in observed:
            if o.get("title") and o["title"] == expected_title:
                return o
    return None


def reconcile(
    expected_panels: List[Dict[str, Any]],
    observed_states: List[Dict[str, Any]],
    resolve_times_ms: Dict[str, int],
) -> List[Dict[str, Any]]:
    """Merge expected (registry) data panels with what the browser observed.

    `expected_panels` items are registry panel dicts (panel_id, title,
    panel_type, is_data_panel). `observed_states` are the last raw states read
    from the page. `resolve_times_ms` maps a panel key -> ms from nav start to
    first terminal state.

    Returns one record per expected data panel, plus any unexpected observed
    data panels, each with a final `render_status` and `render_ms`.
    """
    records: List[Dict[str, Any]] = []
    used_indexes = set()

    for exp in expected_panels:
        if not exp.get("is_data_panel"):
            continue
        pid = str(exp.get("panel_id") or "")
        title = exp.get("title") or ""
        obs = _match_observed(pid, title, observed_states)

        if obs is None:
            records.append(
                {
                    "panel_id": pid,
                    "panel_title": title,
                    "panel_type": exp.get("panel_type"),
                    "render_status": MISSING,
                    "render_status_detail": "expected panel not found on page",
                    "render_ms": None,
                }
            )
            continue

        used_indexes.add(obs.get("index"))
        status = classify_panel(obs)
        key = obs.get("id") or obs.get("title") or f"idx:{obs.get('index')}"
        records.append(
            {
                "panel_id": pid or (obs.get("id") or ""),
                "panel_title": title or obs.get("title") or "",
                "panel_type": exp.get("panel_type"),
                "render_status": status,
                "render_status_detail": obs.get("emptyText"),
                "render_ms": resolve_times_ms.get(key),
            }
        )

    return records


def summarize(records: List[Dict[str, Any]]) -> Dict[str, int]:
    """Roll up per-panel statuses into top-level counters for alerting."""
    counts = {OK: 0, EMPTY: 0, ERROR: 0, TIMEOUT: 0, MISSING: 0}
    for r in records:
        counts[r["render_status"]] = counts.get(r["render_status"], 0) + 1
    ok = counts[OK]
    not_ok = sum(v for k, v in counts.items() if k != OK)
    return {
        "panels_checked": len(records),
        "panels_ok": ok,
        "panels_not_ok": not_ok,
        "panels_empty": counts[EMPTY],
        "panels_error": counts[ERROR],
        "panels_timeout": counts[TIMEOUT],
        "panels_missing": counts[MISSING],
    }
