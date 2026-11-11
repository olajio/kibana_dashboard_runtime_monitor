#!/usr/bin/env python3
"""Build kibana/dashboard_health_monitor.ndjson (light theme) and
kibana/dashboard_health_monitor_dark.ndjson: a Kibana dashboard over `dashboard-health-monitor`.

Look and feel follows the Operations, Infrastructure Overview and MIM dashboards: a "Start here"
banner, numbered and icon-headed sections, RAG cards, status pills, the worst items first, zero
shown green. The Vega panels do not follow Kibana's light/dark theme, so the dashboard is built
once per theme; import whichever matches your Kibana (both use the same ids, so the second
import overwrites the first).

The panels that read the nested `panels` field (slowest panels, panel health) are Vega charts,
because Lens cannot aggregate nested fields.

Import the file into any space (Stack Management > Saved Objects > Import). It brings its own
data view. Object ids are fixed, so importing again overwrites instead of duplicating.

Run `python scripts/build_monitor_dashboard.py` to regenerate both files after editing the layout.
Nothing in this file talks to Kibana or Elasticsearch.
"""
from __future__ import annotations

import json
import sys
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(__file__))

import monitor_vega as mv  # noqa: E402

KIBANA_DIR = os.path.join(os.path.dirname(__file__), "..", "kibana")
OUTPUTS = {"light": "dashboard_health_monitor.ndjson", "dark": "dashboard_health_monitor_dark.ndjson"}

NS = uuid.UUID("5b0f3b8e-6c1c-4d57-9a1e-6f2d1c9d2a10")   # fixed, so every build gives the same ids
DATA_VIEW_ID = "dashboard-health-monitor"
DASHBOARD_ID = str(uuid.uuid5(NS, "dashboard"))
TIME_FIELD = "@timestamp"
DEGRADED_MS, FAILED_MS = 30000, 120000

CORE = "8.8.0"


def _id(name: str) -> str:
    return str(uuid.uuid5(NS, name))


# --------------------------------------------------------------------------- Lens columns
def count(label: str, kql: Optional[str] = None) -> Dict[str, Any]:
    c = {"label": label, "customLabel": True, "dataType": "number", "operationType": "count",
         "isBucketed": False, "scale": "ratio", "sourceField": "___records___",
         "params": {"emptyAsNull": True}}
    if kql:
        c["filter"] = {"query": kql, "language": "kuery"}
    return c


def metric(op: str, field: str, label: str, **params: Any) -> Dict[str, Any]:
    return {"label": label, "customLabel": True, "dataType": "number", "operationType": op,
            "isBucketed": False, "scale": "ratio", "sourceField": field,
            "params": {"emptyAsNull": True, **params}}


def last_value(field: str, label: str, data_type: str = "number") -> Dict[str, Any]:
    return {"label": label, "customLabel": True, "dataType": data_type, "operationType": "last_value",
            "isBucketed": False, "scale": "ratio" if data_type == "number" else "ordinal",
            "sourceField": field, "params": {"sortField": TIME_FIELD, "showArrayValues": False}}


def date_histogram(label: str = " ") -> Dict[str, Any]:
    return {"label": label, "customLabel": True, "dataType": "date", "operationType": "date_histogram",
            "isBucketed": True, "scale": "interval", "sourceField": TIME_FIELD,
            "params": {"interval": "auto", "includeEmptyRows": True, "dropPartials": False}}


def terms(field: str, label: str, size: int, order_by: Dict[str, Any], direction: str = "desc") -> Dict[str, Any]:
    return {"label": label, "customLabel": True, "dataType": "string", "operationType": "terms",
            "isBucketed": True, "scale": "ordinal", "sourceField": field,
            "params": {"size": size, "orderBy": order_by, "orderDirection": direction,
                       "otherBucket": False, "missingBucket": False,
                       "parentFormat": {"id": "terms"}, "include": [], "exclude": [],
                       "includeIsRegex": False, "excludeIsRegex": False}}


def by_column(column_id: str) -> Dict[str, Any]:
    return {"type": "column", "columnId": column_id}


ALPHA = {"type": "alphabetical", "fallback": True}


def lens(name: str, title: str, description: str, vis_type: str,
         columns: List[Tuple[str, Dict[str, Any]]], visualization: Dict[str, Any],
         kql: str = "") -> Dict[str, Any]:
    """One Lens object with a single data layer. `columns` is [(column_id, column)] in display order
    (bucketed columns first); `visualization` refers to those ids and gets its layer id filled in."""
    layer_id = _id(name + "/layer")
    if "layers" not in visualization:           # XY carries its layers inside; the others are one layer
        visualization = dict(visualization, layerId=layer_id, layerType="data")
    state = {
        "visualization": visualization,
        "query": {"query": kql, "language": "kuery"},
        "filters": [],
        "datasourceStates": {"formBased": {"layers": {layer_id: {
            "columns": {cid: col for cid, col in columns},
            "columnOrder": [cid for cid, _ in columns],
            "incompleteColumns": {}}}}},
        "internalReferences": [],
        "adHocDataViews": {},
    }
    return {
        "type": "lens", "id": _id(name), "coreMigrationVersion": CORE, "typeMigrationVersion": "8.9.0",
        "managed": False,
        "attributes": {"title": title, "description": description, "visualizationType": vis_type,
                       "state": state},
        "references": [{"type": "index-pattern", "id": DATA_VIEW_ID,
                        "name": f"indexpattern-datasource-layer-{layer_id}"}],
    }


# --------------------------------------------------------------------------- the objects
def data_view() -> Dict[str, Any]:
    return {
        "type": "index-pattern", "id": DATA_VIEW_ID, "coreMigrationVersion": CORE,
        "typeMigrationVersion": "8.0.0", "managed": False, "references": [],
        "attributes": {"title": "dashboard-health-monitor*", "name": "dashboard-health-monitor",
                       "timeFieldName": TIME_FIELD, "allowHidden": False, "fields": "[]",
                       "fieldAttrs": "{}", "fieldFormatMap": "{}", "runtimeFieldMap": "{}",
                       "sourceFilters": "[]"},
    }




MONITORED = [   # (title, dashboard id in the Federal Overview export); the links assume the space `fed2`
    ("Federal Overview", "7a339ff0-09ce-11ed-9940-d955314b400b"), ("Federal Landing Page", "e0c843b7-7a67-435a-b6b9-a1ca3d5c6497"),
    ("Agency Details", "499e1290-1742-11ed-a299-d92566ee6433"), ("AIM Score Overview", "0401d180-af3c-11ed-84a9-8fda8c5e892a"),
    ("CyHy Overview", "6bd93ba5-c63e-4ad5-9f83-f815f70506a0"), ("Hardware Overview", "4bcb0fc0-09d0-11ed-bd84-33153167e15c"),
    ("HVA Devices", "e889c7d0-5efb-11ee-ab05-71cf3e789c61"), ("HVA Misconfigurations", "592c4970-afda-11ee-b534-dfb0d7976a5a"),
    ("HVA Software", "1a1cb8b0-9f3f-11ee-ae0a-450e1d1025ea"), ("HVA System Boundaries", "10ac5643-c368-43f1-8251-929ce5297b91"),
    ("HVA Vulnerabilities", "b50dd990-6d02-11ee-a7ac-eb84a370e59e"), ("Misconfiguration Overview", "c2fa8940-09d0-11ed-bd84-33153167e15c"),
    ("Misconfiguration Records", "615a8c72-14bd-4610-96ec-8cfd457dc9e4"), ("Priority Risks", "6b48e19c-c987-4094-901a-78ee82269654"),
    ("Remediated Vulnerabilities", "4597b731-8fcb-43f6-aa9b-46159428f804"),
    ("Remote Cluster Connectivity Status Details", "775e7a40-8396-4105-8f39-3eba7b4ee12c"),
    ("SCuBA Overview", "d5237989-e551-4fe9-aafc-32c7ba7c4810"), ("SCuBA Overview - All", "2e10c794-657d-4132-ab3d-ed312975253d"),
    ("Software Overview", "9807e500-0919-11ed-bd84-33153167e15c"), ("SSVC Overview", "b350410f-c5fa-4468-9a35-ab153b0462c8"),
    ("Vulnerability Overview", "66833a00-0916-11ed-9940-d955314b400b"), ("Vulnerability Records", "c78fa370-1c34-11ed-a299-d92566ee6433"),
]
LINK_SPACE = "fed2"


def vega(name: str, title: str, description: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "visualization", "id": _id(name), "coreMigrationVersion": CORE,
        "typeMigrationVersion": "8.5.0", "managed": False, "references": [],
        "attributes": {
            "title": title, "description": description, "version": 1, "uiStateJSON": "{}",
            "visState": json.dumps({"title": title, "type": "vega", "aggs": [],
                                    "params": {"spec": mv.spec_json(spec)}}),
            "kibanaSavedObjectMeta": {"searchSourceJSON": "{}"}},
    }


def markdown(name: str, title: str, text: str, font: int = 12) -> Dict[str, Any]:
    return {
        "type": "visualization", "id": _id(name), "coreMigrationVersion": CORE,
        "typeMigrationVersion": "8.5.0", "managed": False, "references": [],
        "attributes": {
            "title": title, "description": "", "version": 1, "uiStateJSON": "{}",
            "visState": json.dumps({"title": title, "type": "markdown", "aggs": [],
                                    "params": {"fontSize": font, "openLinksInNewTab": True, "markdown": text}}),
            "kibanaSavedObjectMeta": {"searchSourceJSON": json.dumps(
                {"query": {"query": "", "language": "kuery"}, "filter": []})}},
    }


def links_markdown() -> str:
    rows = [f"[{t}](/s/{LINK_SPACE}/app/dashboards#/view/{i})" for t, i in MONITORED]
    while len(rows) % 3:
        rows.append("")
    lines = ["| | | |", "|---|---|---|"]
    lines += ["| " + " | ".join(rows[i:i + 3]) + " |" for i in range(0, len(rows), 3)]
    return "\n".join(lines) + (f"\n\n*Links open the dashboard in the `{LINK_SPACE}` space. A name here is the same "
                               f"`dashboard_title` the collector records.*")


HELP = f"""## How to read this dashboard

Each collector run (every 30 minutes) records one result per Kibana dashboard it opens. Everything
above the baseline uses each dashboard's **latest result in the time range** (default: the last 24
hours); the trends and the baseline use every result in the range.

**State** is one word per dashboard, from its latest result:

| State | Meaning |
|---|---|
| **Healthy** | every panel rendered and the load time is under {mv.DEGRADED_S} s |
| **Slow** | every panel rendered, but the collector rated the load time `degraded` ({mv.DEGRADED_S} s or more) or `failed` ({mv.FAILED_S} s or more) |
| **Attention** | some panels were empty, errored, timed out or never appeared |
| **Action** | half or more of the panels were not ok |

**Overall health** is Action if any dashboard is in Action, Attention if any is in Attention, otherwise
Healthy. Slow dashboards do not change it: the load-time thresholds are global, so a dashboard that
is always slow (Software Overview) would otherwise keep the page red. Judge those against their own
baseline.

**Baseline** shows, per dashboard, the median to p95 range and the slowest load over the time range,
with the {mv.DEGRADED_S} s and {mv.FAILED_S} s thresholds drawn across it. Make the time range longer (7 to 14 days) for it
to mean something. Run `python scripts/baseline.py` on the collector host for suggested per-dashboard
thresholds.

**Last run** turns amber after {mv.STALE_OK_MIN} minutes and red after {mv.STALE_WARN_MIN}: the collector missed runs.

**Slowest panels** and **Panel health** read the per-panel records. If a Vega panel shows an error, open
its menu, choose Inspect, and read the message.
"""


def build(theme: str = "light") -> List[Dict[str, Any]]:
    t = mv.THEMES[theme]
    o: List[Dict[str, Any]] = [data_view()]

    # ---- banners (Vega, no data)
    o.append(vega("banner", "Start here", "How to read the page.", mv.start_here(
        t, "Start here: a red or amber card shows where to look first.",
        "Every panel follows the time range at the top (default: the last 24 hours). "
        "Pick a dashboard or a status in the filters above to narrow the page.")))
    for key, icon, n, title, note in (
            ("s1", "🧭", 1, "Health right now", "latest result of each dashboard"),
            ("s2", "🚨", 2, "What needs attention now", "worst first"),
            ("s3", "📈", 3, "Trends and baseline", "every result in the time range"),
            ("s4", "🔗", 4, "Monitored dashboards", ""),
            ("s5", "📚", 5, "Reference", "")):
        o.append(vega(f"section-{key}", f"Section {n}", "", mv.section_header(t, icon, n, title, note)))

    # ---- section 1
    o.append(vega("kpis", "Health summary", "Overall health, dashboards checked, slow, panels not ok, last run.",
                  mv.kpi_cards(t)))
    o.append(vega("grid", "Status by dashboard", "One card per dashboard, worst first.", mv.status_grid(t)))

    # ---- section 2
    o.append(vega("attention", "Needs attention", "Dashboards whose latest result is not healthy.",
                  mv.attention_table(t)))
    o.append(vega("panel-render", "Slowest panels",
                  "Average render time of individual panels, from the nested panels records.", mv.slowest_panels(t)))
    o.append(vega("panel-health", "Panel health by dashboard",
                  "Share of panel results that were ok, empty, error, timeout or missing.", mv.panel_health(t)))

    # ---- section 3
    o.append(lens("load-time-trend", "Load time over time",
                  "Average load_time_ms per dashboard, one line each.", "lnsXY",
                  [("x", date_histogram()),
                   ("split", terms("dashboard_title", "Dashboard", 30, by_column("y"))),
                   ("y", metric("average", "load_time_ms", "Average load time (ms)"))],
                  {"preferredSeriesType": "line", "valueLabels": "hide",
                   "legend": {"isVisible": True, "position": "right", "showSingleSeries": True},
                   "axisTitlesVisibilitySettings": {"x": False, "yLeft": True, "yRight": False},
                   "yTitle": "Load time (ms)",
                   "layers": [{"layerId": "", "layerType": "data", "accessors": ["y"], "xAccessor": "x",
                               "splitAccessor": "split", "seriesType": "line", "position": "top",
                               "showGridlines": False}]}))
    _fix_xy_layer(o[-1])
    o.append(vega("status-time", "Dashboards by collector status", "ok, degraded and failed per time bucket.",
                  mv.status_over_time(t)))
    o.append(vega("baseline", "Load time baseline", "Median to p95 and slowest load per dashboard, with thresholds.",
                  mv.baseline_chart(t)))
    o.append(lens("latest-cycle", "Latest load time per dashboard",
                  "The most recent load_time_ms of each dashboard.", "lnsXY",
                  [("x", terms("dashboard_title", "Dashboard", 100, by_column("y"))),
                   ("y", last_value("load_time_ms", "Latest load time (ms)"))],
                  {"preferredSeriesType": "bar_horizontal", "valueLabels": "hide",
                   "legend": {"isVisible": False, "position": "right"},
                   "axisTitlesVisibilitySettings": {"x": False, "yLeft": True, "yRight": False},
                   "yTitle": "Latest load time (ms)",
                   "layers": [{"layerId": "", "layerType": "data", "accessors": ["y"], "xAccessor": "x",
                               "seriesType": "bar_horizontal", "position": "top", "showGridlines": False}]}))
    _fix_xy_layer(o[-1])
    o.append(vega("run-duration", "Collector run duration", "Minutes each run took, against the 30 minute schedule.",
                  mv.run_duration(t)))

    # ---- sections 4 and 5
    o.append(markdown("links", "Monitored dashboards", links_markdown(), 13))
    o.append(markdown("help", "How to read this dashboard", HELP))
    o.append(dashboard())
    return o


def _fix_xy_layer(obj: Dict[str, Any]) -> None:
    layer_id = next(iter(obj["attributes"]["state"]["datasourceStates"]["formBased"]["layers"]))
    for layer in obj["attributes"]["state"]["visualization"]["layers"]:
        layer["layerId"] = layer_id


# --------------------------------------------------------------------------- the dashboard
# (object name, type, x, y, w, h, hide the panel title)
LAYOUT: List[Tuple[str, str, int, int, int, int, bool]] = [
    ("banner", "visualization", 0, 0, 48, 3, True),
    ("section-s1", "visualization", 0, 3, 48, 2, True),
    ("kpis", "visualization", 0, 5, 48, 7, True),
    ("grid", "visualization", 0, 12, 48, 14, True),
    ("section-s2", "visualization", 0, 26, 48, 2, True),
    ("attention", "visualization", 0, 28, 48, 10, True),
    ("panel-render", "visualization", 0, 38, 22, 22, True),
    ("panel-health", "visualization", 22, 38, 26, 22, True),
    ("section-s3", "visualization", 0, 60, 48, 2, True),
    ("load-time-trend", "lens", 0, 62, 28, 16, False),
    ("status-time", "visualization", 28, 62, 20, 16, True),
    ("baseline", "visualization", 0, 78, 30, 22, True),
    ("latest-cycle", "lens", 30, 78, 18, 22, False),
    ("run-duration", "visualization", 0, 100, 48, 11, True),
    ("section-s4", "visualization", 0, 111, 48, 2, True),
    ("links", "visualization", 0, 113, 48, 12, True),
    ("section-s5", "visualization", 0, 125, 48, 2, True),
    ("help", "visualization", 0, 127, 48, 18, True),
]


def dashboard() -> Dict[str, Any]:
    panels, refs = [], []
    for name, typ, x, y, w, h, hide_title in LAYOUT:
        pid = _id("panel/" + name)
        cfg: Dict[str, Any] = {"enhancements": {}}
        if hide_title:
            cfg["hidePanelTitles"] = True
        panels.append({"type": typ, "embeddableConfig": cfg, "panelIndex": pid,
                       "gridData": {"x": x, "y": y, "w": w, "h": h, "i": pid},
                       "panelRefName": f"panel_{pid}"})
        refs.append({"type": typ, "id": _id(name), "name": f"{pid}:panel_{pid}"})

    def control(name: str, field: str, title: str, order: int) -> Tuple[str, Dict[str, Any]]:
        cid = _id("control/" + name)
        return cid, {"grow": True, "order": order, "type": "optionsListControl", "width": "medium",
                     "explicitInput": {"dataViewId": DATA_VIEW_ID, "fieldName": field, "title": title,
                                       "searchTechnique": "prefix", "selectedOptions": [],
                                       "sort": {"by": "_count", "direction": "desc"}}}
    controls = dict([control("dashboard", "dashboard_title", "🗂️ Dashboard", 0),
                     control("status", "load_status", "🚦 Collector status", 1)])
    return {
        "type": "dashboard", "id": DASHBOARD_ID, "coreMigrationVersion": CORE,
        "typeMigrationVersion": "10.3.0", "managed": False,
        "attributes": {
            "title": "Dashboard Health Monitor",
            "description": "Health and load time of the monitored Kibana dashboards, from the "
                           "dashboard-health-monitor collector.",
            "timeRestore": True, "timeFrom": "now-24h", "timeTo": "now",
            "refreshInterval": {"pause": False, "value": 300000},
            "optionsJSON": json.dumps({"useMargins": True, "syncColors": False, "syncCursor": True,
                                       "syncTooltips": False, "hidePanelTitles": False}),
            "panelsJSON": json.dumps(panels),
            "controlGroupInput": {
                "chainingSystem": "HIERARCHICAL", "controlStyle": "oneLine",
                "ignoreParentSettingsJSON": json.dumps({"ignoreFilters": False, "ignoreQuery": False,
                                                        "ignoreTimerange": False, "ignoreValidations": False}),
                "panelsJSON": json.dumps(controls)},
            "kibanaSavedObjectMeta": {"searchSourceJSON": json.dumps(
                {"query": {"query": "", "language": "kuery"}, "filter": []})},
            "version": 1,
        },
        "references": refs + [{"type": "index-pattern", "id": DATA_VIEW_ID,
                               "name": f"controlGroup_{cid}:optionsListDataView"} for cid in controls],
    }


def to_ndjson(objects: List[Dict[str, Any]]) -> str:
    lines = [json.dumps(o, separators=(",", ":"), ensure_ascii=False) for o in objects]
    lines.append(json.dumps({"excludedObjects": [], "excludedObjectsCount": 0,
                             "exportedCount": len(objects), "missingRefCount": 0,
                             "missingReferences": []}, separators=(",", ":")))
    return "\n".join(lines) + "\n"


def main() -> int:
    os.makedirs(KIBANA_DIR, exist_ok=True)
    for theme, filename in OUTPUTS.items():
        objects = build(theme)
        path = os.path.join(KIBANA_DIR, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(to_ndjson(objects))
        kinds: Dict[str, int] = {}
        for obj in objects:
            kinds[obj["type"]] = kinds.get(obj["type"], 0) + 1
        print(f"wrote {os.path.relpath(path)}: {len(objects)} objects {kinds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
