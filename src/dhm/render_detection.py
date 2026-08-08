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


def _record_for(exp, obs, resolve_times_ms):
    """Build a per-panel record from a matched expected/observed pair."""
    pid = str(exp.get("panel_id") or "")
    title = exp.get("title") or ""
    status = classify_panel(obs)
    key = obs.get("id") or obs.get("title") or f"idx:{obs.get('index')}"
    return {
        "panel_id": pid or (obs.get("id") or ""),
        "panel_title": title or obs.get("title") or "",
        "panel_type": exp.get("panel_type"),
        "render_status": status,
        "render_status_detail": obs.get("emptyText"),
        "render_ms": resolve_times_ms.get(key),
    }


def reconcile(
    expected_panels: List[Dict[str, Any]],
    observed_states: List[Dict[str, Any]],
    resolve_times_ms: Dict[str, int],
) -> List[Dict[str, Any]]:
    """Merge expected (registry) data panels with what the browser observed.

    Matches in three passes:
      1. by embeddable id (== panelIndex, when Kibana exposes it),
      2. by exact title (case-sensitive, non-empty),
      3. by DOM order among still-unmatched panels — a robust fallback for
         Kibana versions that hide panel ids/titles from the DOM (e.g. 8.19),
         since Kibana renders panels in gridData order and the registry lists
         them in that same panelsJSON order.

    Returns one record per expected data panel with a final `render_status`
    and `render_ms`.
    """
    expected_data = [e for e in expected_panels if e.get("is_data_panel")]
    matched: Dict[int, Dict[str, Any]] = {}  # index into expected_data -> observed
    used_obs_indexes = set()

    # Pass 1 + 2: id match, then title match.
    for i, exp in enumerate(expected_data):
        pid = str(exp.get("panel_id") or "")
        title = exp.get("title") or ""
        obs = _match_observed(pid, title, observed_states)
        if obs is not None and obs.get("index") not in used_obs_indexes:
            matched[i] = obs
            used_obs_indexes.add(obs.get("index"))

    # Pass 3: positional fallback for anything still unmatched.
    remaining_expected = [i for i in range(len(expected_data)) if i not in matched]
    remaining_observed = [
        o for o in observed_states if o.get("index") not in used_obs_indexes
    ]
    remaining_observed.sort(key=lambda o: o.get("index", 0))
    for i, obs in zip(remaining_expected, remaining_observed):
        matched[i] = obs
        used_obs_indexes.add(obs.get("index"))

    # Build records in the registry's order.
    records: List[Dict[str, Any]] = []
    for i, exp in enumerate(expected_data):
        obs = matched.get(i)
        if obs is None:
            records.append({
                "panel_id": str(exp.get("panel_id") or ""),
                "panel_title": exp.get("title") or "",
                "panel_type": exp.get("panel_type"),
                "render_status": MISSING,
                "render_status_detail": "expected panel not found on page",
                "render_ms": None,
            })
        else:
            records.append(_record_for(exp, obs, resolve_times_ms))

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
