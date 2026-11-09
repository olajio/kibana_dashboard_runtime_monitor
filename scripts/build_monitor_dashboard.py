#!/usr/bin/env python3
"""Build kibana/dashboard_health_monitor.ndjson: a Kibana dashboard over `dashboard-health-monitor`.

The panels are the ones recommended in HOWTO section 6: status tiles (ok / degraded / failed in
green / amber / red), load time over time split by dashboard, the latest result per dashboard,
per-panel render time and a panel-health heatmap. The last two read the nested `panels` field,
which Lens cannot aggregate, so they are Vega charts that query Elasticsearch directly.

Import the file into any space (Stack Management > Saved Objects > Import). It brings its own
data view, so nothing else has to exist in the space. Object ids are fixed, so importing again
overwrites instead of duplicating.

Run `python scripts/build_monitor_dashboard.py` to regenerate it after editing the layout here.
Nothing in this file talks to Kibana or Elasticsearch.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

OUT = os.path.join(os.path.dirname(__file__), "..", "kibana", "dashboard_health_monitor.ndjson")

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


GREEN, AMBER, RED = "#54B399", "#F5A700", "#BD271E"


def metric_tile(name: str, title: str, column: Dict[str, Any], kql: str = "",
                color: Optional[str] = None) -> Dict[str, Any]:
    vis: Dict[str, Any] = {"metricAccessor": "m", "showBar": False}
    if color:
        vis["color"] = color          # Lens Metric has no per-value colours, hence one tile per status
    return lens(name, title, "", "lnsMetric", [("m", column)], vis, kql)


def vega(name: str, title: str, description: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "visualization", "id": _id(name), "coreMigrationVersion": CORE,
        "typeMigrationVersion": "8.5.0", "managed": False, "references": [],
        "attributes": {
            "title": title, "description": description, "version": 1, "uiStateJSON": "{}",
            "visState": json.dumps({"title": title, "type": "vega", "aggs": [],
                                    "params": {"spec": json.dumps(spec, indent=2)}}),
            "kibanaSavedObjectMeta": {"searchSourceJSON": "{}"}},
    }


def _vega_data(aggs: Dict[str, Any], prop: str) -> Dict[str, Any]:
    """Data source for a Vega-Lite chart: the dashboard's time range and filters are applied
    (`%context%`, `%timefield%`), and `prop` is the bucket list to chart."""
    return {"url": {"%context%": True, "%timefield%": TIME_FIELD, "index": "dashboard-health-monitor*",
                    "body": {"size": 0, "aggs": aggs}},
            "format": {"property": prop}}


PANEL_STATUSES = ["ok", "empty", "error", "timeout", "missing"]
VEGA_LITE = "https://vega.github.io/schema/vega-lite/v5.json"


def per_panel_render_spec() -> Dict[str, Any]:
    """Slowest individual panels: nested panels.render_ms, one bar per panel, labelled with its
    dashboard. Grouped by panel id, because a title alone repeats across dashboards."""
    aggs = {"panels": {"nested": {"path": "panels"}, "aggs": {"by_panel": {
        "terms": {"field": "panels.panel_id", "size": 25, "order": {"avg_ms": "desc"}},
        "aggs": {"avg_ms": {"avg": {"field": "panels.render_ms"}},
                 "max_ms": {"max": {"field": "panels.render_ms"}},
                 "title": {"terms": {"field": "panels.panel_title", "size": 1}},
                 "page": {"reverse_nested": {},
                          "aggs": {"dash": {"terms": {"field": "dashboard_title", "size": 1}}}}}}}}}
    return {
        "$schema": VEGA_LITE,
        "title": "Slowest panels (average render time)",
        "data": _vega_data(aggs, "aggregations.panels.by_panel.buckets"),
        "transform": [
            {"calculate": "datum.page.dash.buckets[0].key + ' / ' + datum.title.buckets[0].key", "as": "label"},
            {"calculate": "datum.avg_ms.value", "as": "avg"},
            {"calculate": "datum.max_ms.value", "as": "max"}],
        "mark": {"type": "bar", "color": "#6092C0"},
        "encoding": {
            "y": {"field": "label", "type": "nominal", "sort": "-x", "title": None,
                  "axis": {"labelLimit": 320}},
            "x": {"field": "avg", "type": "quantitative", "title": "Average render time (ms)"},
            "tooltip": [{"field": "label", "title": "Panel"},
                        {"field": "avg", "title": "Average (ms)", "format": ",.0f"},
                        {"field": "max", "title": "Slowest (ms)", "format": ",.0f"},
                        {"field": "doc_count", "title": "Samples"}]},
    }


def panel_health_spec() -> Dict[str, Any]:
    """Share of each dashboard's panel results that were ok / empty / error / timeout / missing,
    from nested panels.render_status. One nested count per status keeps the rows flat."""
    per_dash = {st: {"nested": {"path": "panels"},
                     "aggs": {"f": {"filter": {"term": {"panels.render_status": st}}}}}
                for st in PANEL_STATUSES}
    aggs = {"by_dash": {"terms": {"field": "dashboard_title", "size": 100, "order": {"_key": "asc"}},
                        "aggs": per_dash}}
    return {
        "$schema": VEGA_LITE,
        "title": "Panel health by dashboard (share of panel results)",
        "data": _vega_data(aggs, "aggregations.by_dash.buckets"),
        "transform": (
            [{"calculate": "datum.key", "as": "dashboard"}]
            + [{"calculate": f"datum.{st}.f.doc_count", "as": st} for st in PANEL_STATUSES]
            + [{"fold": PANEL_STATUSES, "as": ["status", "count"]},
               {"joinaggregate": [{"op": "sum", "field": "count", "as": "total"}], "groupby": ["dashboard"]},
               {"calculate": "datum.total > 0 ? datum.count / datum.total : 0", "as": "share"}]),
        "encoding": {
            "x": {"field": "status", "type": "ordinal", "sort": PANEL_STATUSES, "title": None,
                  "axis": {"orient": "top", "labelAngle": 0}},
            "y": {"field": "dashboard", "type": "nominal", "title": None, "axis": {"labelLimit": 260}}},
        "layer": [
            {"mark": "rect",
             "encoding": {"color": {"field": "share", "type": "quantitative", "title": "Share",
                                    "scale": {"domain": [0, 1], "scheme": "yellowgreenblue"}},
                          "tooltip": [{"field": "dashboard"}, {"field": "status"},
                                      {"field": "count", "title": "Panel results"},
                                      {"field": "share", "format": ".1%"}]}},
            {"mark": {"type": "text", "fontSize": 10},
             "encoding": {"text": {"field": "share", "type": "quantitative", "format": ".0%"},
                          "color": {"condition": {"test": "datum.share > 0.6", "value": "white"},
                                    "value": "black"}}}],
    }


HELP = f"""## How to read this dashboard

Each collector run (every 30 minutes) records one result per Kibana dashboard it opens.

**The four tiles** count dashboards that had at least one result of that kind in the time range
(default: the last hour, so roughly the latest run). They are not exclusive: a dashboard that
flipped between statuses in the range is counted in each.

**Status** is decided by the collector: `ok`, `degraded` (load time of {DEGRADED_MS // 1000} s or more, or some
panels not ok) or `failed` ({FAILED_MS // 1000} s or more, or half the panels not ok, or a panel in an error
state). Those thresholds are global, so a dashboard that is always slow, such as Software Overview,
reads `degraded` even when it is healthy for what it is.

**Latest result per dashboard** shows each dashboard's most recent status, load time and not-ok
panels, whatever the time range. **Load time by dashboard (baseline)** gives the median and p95
to decide what "slow" means for each one.

**Slowest panels** and **Panel health** read the per-panel records. They are Vega charts: if one
shows an error, open the panel menu, choose Inspect, and read the message.
"""


def build() -> List[Dict[str, Any]]:
    o: List[Dict[str, Any]] = [data_view()]

    # --- status tiles: one per status so each can have its own colour
    def dashboards(label: str) -> Dict[str, Any]:
        return metric("unique_count", "dashboard_title", label)

    o.append(metric_tile("tile-ok", "Dashboards ok", dashboards("OK"), "load_status : ok", GREEN))
    o.append(metric_tile("tile-degraded", "Dashboards degraded", dashboards("Degraded"),
                         "load_status : degraded", AMBER))
    o.append(metric_tile("tile-failed", "Dashboards failed", dashboards("Failed"),
                         "load_status : failed", RED))
    o.append(metric_tile("tile-not-ok-panels", "Dashboards with not-ok panels",
                         dashboards("With not-ok panels"), "panels_not_ok > 0"))

    # --- load time over time, split by dashboard (every dashboard)
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

    # --- latest cycle, one bar per dashboard
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

    # --- latest result table
    o.append(lens("latest-table", "Latest result per dashboard",
                  "Each dashboard's most recent status, load time and not-ok panels.", "lnsDatatable",
                  [("d", terms("dashboard_title", "Dashboard", 100, by_column("t"))),
                   ("s", last_value("load_status", "Status", "string")),
                   ("t", last_value("load_time_ms", "Load time (ms)")),
                   ("n", last_value("panels_not_ok", "Not-ok panels")),
                   ("w", last_value(TIME_FIELD, "Last run", "date"))],
                  {"columns": [{"columnId": c} for c in ("d", "s", "t", "n", "w")],
                   "sorting": {"columnId": "t", "direction": "desc"},
                   "paging": {"size": 30, "enabled": False}}))

    # --- nested panel records: Vega
    o.append(vega("panel-render", "Slowest panels",
                  "Average render time of individual panels, from the nested panels records.",
                  per_panel_render_spec()))
    o.append(vega("panel-health", "Panel health by dashboard",
                  "Share of panel results that were ok, empty, error, timeout or missing.",
                  panel_health_spec()))

    # --- baseline table
    o.append(lens("baseline-table", "Load time by dashboard (baseline)",
                  "Per dashboard over the selected range: runs, median, p95 and slowest load time, "
                  "and not-ok panels. Sorted by p95.", "lnsDatatable",
                  [("d", terms("dashboard_title", "Dashboard", 100, by_column("p95"))),
                   ("n", count("Runs")),
                   ("p50", metric("median", "load_time_ms", "Median (ms)")),
                   ("p95", metric("percentile", "load_time_ms", "p95 (ms)", percentile=95)),
                   ("mx", metric("max", "load_time_ms", "Slowest (ms)")),
                   ("nok", metric("sum", "panels_not_ok", "Not-ok panels"))],
                  {"columns": [{"columnId": c} for c in ("d", "n", "p50", "p95", "mx", "nok")],
                   "sorting": {"columnId": "p95", "direction": "desc"},
                   "paging": {"size": 30, "enabled": False}}))

    # --- help text
    o.append({
        "type": "visualization", "id": _id("help"), "coreMigrationVersion": CORE,
        "typeMigrationVersion": "8.5.0", "managed": False, "references": [],
        "attributes": {
            "title": "How to read this dashboard", "description": "", "version": 1, "uiStateJSON": "{}",
            "visState": json.dumps({"title": "How to read this dashboard", "type": "markdown", "aggs": [],
                                    "params": {"fontSize": 12, "openLinksInNewTab": False,
                                               "markdown": HELP}}),
            "kibanaSavedObjectMeta": {"searchSourceJSON": json.dumps(
                {"query": {"query": "", "language": "kuery"}, "filter": []})}},
    })

    o.append(dashboard())
    return o


def _fix_xy_layer(obj: Dict[str, Any]) -> None:
    layer_id = next(iter(obj["attributes"]["state"]["datasourceStates"]["formBased"]["layers"]))
    for layer in obj["attributes"]["state"]["visualization"]["layers"]:
        layer["layerId"] = layer_id


# --------------------------------------------------------------------------- the dashboard
def dashboard() -> Dict[str, Any]:
    # (object name, type, x, y, w, h)
    layout: List[Tuple[str, str, int, int, int, int]] = [
        ("tile-ok", "lens", 0, 0, 12, 6), ("tile-degraded", "lens", 12, 0, 12, 6),
        ("tile-failed", "lens", 24, 0, 12, 6), ("tile-not-ok-panels", "lens", 36, 0, 12, 6),
        ("load-time-trend", "lens", 0, 6, 30, 22), ("latest-cycle", "lens", 30, 6, 18, 22),
        ("latest-table", "lens", 0, 28, 48, 18),
        ("panel-render", "visualization", 0, 46, 24, 22), ("panel-health", "visualization", 24, 46, 24, 22),
        ("baseline-table", "lens", 0, 68, 48, 18),
        ("help", "visualization", 0, 86, 48, 14),
    ]
    panels, refs = [], []
    for name, typ, x, y, w, h in layout:
        pid = _id("panel/" + name)
        panels.append({"type": typ, "embeddableConfig": {"enhancements": {}}, "panelIndex": pid,
                       "gridData": {"x": x, "y": y, "w": w, "h": h, "i": pid},
                       "panelRefName": f"panel_{pid}"})
        refs.append({"type": typ, "id": _id(name), "name": f"{pid}:panel_{pid}"})

    def control(name: str, field: str, title: str, order: int) -> Tuple[str, Dict[str, Any]]:
        cid = _id("control/" + name)
        return cid, {"grow": True, "order": order, "type": "optionsListControl", "width": "medium",
                     "explicitInput": {"dataViewId": DATA_VIEW_ID, "fieldName": field, "title": title,
                                       "searchTechnique": "prefix", "selectedOptions": [],
                                       "sort": {"by": "_count", "direction": "desc"}}}
    controls = dict([control("dashboard", "dashboard_title", "Dashboard", 0),
                     control("status", "load_status", "Status", 1)])
    return {
        "type": "dashboard", "id": DASHBOARD_ID, "coreMigrationVersion": CORE,
        "typeMigrationVersion": "10.3.0", "managed": False,
        "attributes": {
            "title": "Dashboard Health Monitor",
            "description": "Load time and panel health of the monitored Kibana dashboards, from the "
                           "dashboard-health-monitor collector.",
            "timeRestore": True, "timeFrom": "now-1h", "timeTo": "now",
            "refreshInterval": {"pause": False, "value": 30000},
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
    objects = build()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(to_ndjson(objects))
    kinds: Dict[str, int] = {}
    for obj in objects:
        kinds[obj["type"]] = kinds.get(obj["type"], 0) + 1
    print(f"wrote {os.path.relpath(OUT)}: {len(objects)} objects {kinds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
