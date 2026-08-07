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


def _nav_dashboard_ids(
    refs: List[Dict[str, Any]], links_by_id: Dict[str, Dict[str, Any]]
) -> List[str]:
    """Dashboards this dashboard navigates to — the set a user can reach in one
    click from it: panel drilldowns (direct `dashboard` references) plus the
    destinations of its Links-panel navigation menus (`links` references resolved
    to the Links saved object's own `dashboard` references)."""
    out: List[str] = []

    def add(did: str):
        if did and did not in out:
            out.append(did)

    for r in refs:
        rtype = r.get("type")
        if rtype == "dashboard":
            add(r.get("id"))  # panel drilldown
        elif rtype == "links":
            lo = links_by_id.get(r.get("id"))
            if lo:
                for lr in lo.get("references", []) or []:
                    if lr.get("type") == "dashboard":
                        add(lr.get("id"))
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


def _dashboard_from_object(
    d: Dict[str, Any], links_by_id: Dict[str, Dict[str, Any]]
) -> Dashboard:
    """Build one Dashboard from a saved-object dict (from an export line OR a
    Saved Objects API `_find` result — both share this shape)."""
    attrs = d.get("attributes", {}) or {}
    refs = d.get("references", []) or []
    panels_json = attrs.get("panelsJSON") or "[]"
    try:
        raw_panels = json.loads(panels_json)
    except json.JSONDecodeError:
        raw_panels = []

    panels = [_parse_panel(p, refs) for p in raw_panels]
    data_panels = [p for p in panels if p.is_data_panel]
    return Dashboard(
        dashboard_id=d["id"],
        title=attrs.get("title", ""),
        is_hub=False,
        panel_count=len(panels),
        data_panel_count=len(data_panels),
        panels=panels,
        linked_dashboards=_nav_dashboard_ids(refs, links_by_id),
    )


def registry_from_objects(
    objects: List[Dict[str, Any]], app: str, generated_from: str
) -> Registry:
    """Build a Registry from a list of saved-object dicts (source-agnostic).

    `objects` should include both `dashboard` and `links` saved objects so that
    navigation-menu destinations can be resolved for reachability.
    """
    links_by_id = {o["id"]: o for o in objects if o.get("type") == "links"}
    dash_objs = [o for o in objects if o.get("type") == "dashboard"]
    dashboards = [_dashboard_from_object(d, links_by_id) for d in dash_objs]

    hub_id = _detect_hub(dashboards, app)
    for d in dashboards:
        d.is_hub = d.dashboard_id == hub_id

    # Hub first, then the rest alphabetically for stable output.
    dashboards.sort(key=lambda d: (not d.is_hub, d.title.lower()))

    return Registry(
        app=app,
        generated_from=generated_from,
        generated_at=datetime.now(timezone.utc).isoformat(),
        hub_dashboard_id=hub_id,
        dashboard_count=len(dashboards),
        dashboards=dashboards,
    )


def build_registry(ndjson_path: str, app: str = "federal_overview") -> Registry:
    """Build a registry from a saved-objects export file (.ndjson)."""
    objs = _load_objects(ndjson_path)
    return registry_from_objects(objs, app, generated_from=ndjson_path)


def registry_to_dict(reg: Registry) -> Dict[str, Any]:
    return asdict(reg)


def filter_registry_dict(reg: Dict[str, Any], include_titles: List[str]) -> Dict[str, Any]:
    """Keep only dashboards whose title is in `include_titles` (case-insensitive).

    An empty/falsy `include_titles` means "keep everything". This is the single
    place that applies the title filter, so it behaves identically whether the
    registry came from the .ndjson export or the live Saved Objects API.
    """
    if not include_titles:
        return reg
    wanted = {t.strip().lower() for t in include_titles}
    dashboards = [
        d for d in reg.get("dashboards", [])
        if (d.get("title") or "").strip().lower() in wanted
    ]
    out = dict(reg)
    out["dashboards"] = dashboards
    out["dashboard_count"] = len(dashboards)
    return out


def _subset_registry(reg: Dict[str, Any], keep_ids: set) -> Dict[str, Any]:
    dashboards = [d for d in reg.get("dashboards", []) if d.get("dashboard_id") in keep_ids]
    out = dict(reg)
    out["dashboards"] = dashboards
    out["dashboard_count"] = len(dashboards)
    return out


def reachable_from_hub(reg: Dict[str, Any], hub_title: str) -> set:
    """Ids of the hub (matched by title, case-insensitive) plus every dashboard
    reachable from it by following `linked_dashboards` transitively — i.e. every
    dashboard a user can click into starting from the hub."""
    want = (hub_title or "").strip().lower()
    by_id = {d["dashboard_id"]: d for d in reg.get("dashboards", [])}
    start = next((d["dashboard_id"] for d in reg.get("dashboards", [])
                  if (d.get("title") or "").strip().lower() == want), None)
    if start is None:
        return set()
    seen = {start}
    stack = [start]
    while stack:
        cur = stack.pop()
        for nxt in by_id.get(cur, {}).get("linked_dashboards", []) or []:
            if nxt in by_id and nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def select_registry(
    reg: Dict[str, Any], selection: str, hub_title: str, include_titles: List[str]
) -> Dict[str, Any]:
    """Narrow the full registry to the dashboards we actually monitor.

    selection:
      - "linked" (default): the hub (`hub_title`) plus everything reachable from
        its navigation — the Federal Overview dashboard and all the dashboards
        linked to it.
      - "titles": exactly the dashboards named in `include_titles`.
      - "all": every dashboard in the space.
    """
    if selection == "all":
        return reg
    if selection == "titles":
        return filter_registry_dict(reg, include_titles)
    # default: linked / reachable from the hub
    ids = reachable_from_hub(reg, hub_title)
    return _subset_registry(reg, ids)


def write_registry(reg: Registry, out_path: str) -> None:
    with open(out_path, "w") as f:
        json.dump(registry_to_dict(reg), f, indent=2)
        f.write("\n")
