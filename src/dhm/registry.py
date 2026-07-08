"""Build the monitoring registry from a Kibana saved-objects export (.ndjson).

The Federal Overview export is a self-contained description of everything we
monitor: the dashboards, and the panels on each dashboard. Because the export
lists the *expected* panels, the collector can later detect not just empty
panels but panels that failed to appear at all.

This module is deliberately browser-free and side-effect-free so it can be unit
tested against the real export file.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Panel types that render data and therefore have a health state we care about.
DATA_PANEL_TYPES = {"lens", "visualization", "search", "map"}
# Panel types that are navigation chrome, not data. We record them but do not
# health-check them (a "Links" panel is never "empty" in the data sense).
NAV_PANEL_TYPES = {"links"}


@dataclass
class Panel:
    panel_id: str          # runtime embeddable id (panelIndex) — matches the DOM
    title: str
    panel_type: str
    saved_object_id: Optional[str]  # the underlying lens/visualization/search SO
    is_data_panel: bool


@dataclass
class Dashboard:
    dashboard_id: str
    title: str
    is_hub: bool
    panel_count: int
    data_panel_count: int
    panels: List[Panel]
    linked_dashboards: List[str] = field(default_factory=list)


@dataclass
class Registry:
    app: str
    generated_from: str
    generated_at: str
    hub_dashboard_id: Optional[str]
    dashboard_count: int
    dashboards: List[Dashboard]


def _load_objects(ndjson_path: str) -> List[Dict[str, Any]]:
    objs = []
    with open(ndjson_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # Kibana appends a non-object summary line to some exports.
                continue
            if isinstance(obj, dict) and "type" in obj:
                objs.append(obj)
    return objs


def _resolve_ref(
    refs: List[Dict[str, Any]], ref_name: str, panel_id: str
) -> Optional[Dict[str, Any]]:
    """Find the saved object a panel points to.

    Kibana names a panel's reference either exactly `panelRefName`, or with a
    `<panelIndex>:` prefix (e.g. "ed33..:panel_ed33.."). Match both, and fall
    back to any reference scoped to this panelIndex.
    """
    if not ref_name and not panel_id:
        return None
    for r in refs:
        name = r.get("name", "")
        if name == ref_name:
            return r
    for r in refs:
        name = r.get("name", "")
        if ref_name and name.endswith(":" + ref_name):
            return r
    for r in refs:
        name = r.get("name", "")
        if panel_id and name == f"{panel_id}:panel_{panel_id}":
            return r
    return None


def _parse_panel(p: Dict[str, Any], refs: List[Dict[str, Any]]) -> Panel:
    """Turn one entry of a dashboard's panelsJSON into a Panel.

    Handles both the modern format (panel carries `type` + `panelRefName`) and
    the older by-reference-only format (type must be resolved via references).
    """
    ptype = p.get("type")
    ref_name = p.get("panelRefName")
    saved_object_id = None

    # panelIndex is the id Kibana uses for the embeddable in the DOM at runtime.
    panel_id = str(p.get("panelIndex") or p.get("gridData", {}).get("i") or ref_name or "")

    match = _resolve_ref(refs, ref_name or "", panel_id)
    if match:
        saved_object_id = match.get("id")
        if not ptype:
            ptype = match.get("type")
    ptype = ptype or "unknown"

    title = (
        p.get("title")
        or (p.get("embeddableConfig", {}) or {}).get("title")
        or ""
    )

    return Panel(
        panel_id=panel_id,
        title=title,
        panel_type=ptype,
        saved_object_id=saved_object_id,
        is_data_panel=ptype in DATA_PANEL_TYPES,
    )


def _linked_dashboard_ids(refs: List[Dict[str, Any]]) -> List[str]:
    """Dashboard ids this dashboard drills down / links to (deduped, ordered)."""
    out: List[str] = []
    for r in refs:
        if r.get("type") == "dashboard" and r.get("id") not in out:
            out.append(r["id"])
    return out


def _detect_hub(dashboards: List[Dashboard], app: str) -> Optional[str]:
    """Pick the hub dashboard: prefer a title matching the app, else the one
    that links to the most other dashboards."""
    if not dashboards:
        return None
    app_norm = app.replace("_", " ").lower()
    for d in dashboards:
        if d.title.strip().lower() == app_norm:
            return d.dashboard_id
    return max(dashboards, key=lambda d: len(d.linked_dashboards)).dashboard_id


def build_registry(ndjson_path: str, app: str = "federal_overview") -> Registry:
    objs = _load_objects(ndjson_path)
    dash_objs = [o for o in objs if o.get("type") == "dashboard"]

    dashboards: List[Dashboard] = []
    for d in dash_objs:
        attrs = d.get("attributes", {})
        refs = d.get("references", []) or []
        panels_json = attrs.get("panelsJSON") or "[]"
        try:
            raw_panels = json.loads(panels_json)
        except json.JSONDecodeError:
            raw_panels = []

        panels = [_parse_panel(p, refs) for p in raw_panels]
        data_panels = [p for p in panels if p.is_data_panel]
        dashboards.append(
            Dashboard(
                dashboard_id=d["id"],
                title=attrs.get("title", ""),
                is_hub=False,
                panel_count=len(panels),
                data_panel_count=len(data_panels),
                panels=panels,
                linked_dashboards=_linked_dashboard_ids(refs),
            )
        )

    hub_id = _detect_hub(dashboards, app)
    for d in dashboards:
        d.is_hub = d.dashboard_id == hub_id

    # Hub first, then the rest alphabetically for stable output.
    dashboards.sort(key=lambda d: (not d.is_hub, d.title.lower()))

    return Registry(
        app=app,
        generated_from=ndjson_path,
        generated_at=datetime.now(timezone.utc).isoformat(),
        hub_dashboard_id=hub_id,
        dashboard_count=len(dashboards),
        dashboards=dashboards,
    )


def registry_to_dict(reg: Registry) -> Dict[str, Any]:
    return asdict(reg)


def write_registry(reg: Registry, out_path: str) -> None:
    with open(out_path, "w") as f:
        json.dump(registry_to_dict(reg), f, indent=2)
        f.write("\n")
