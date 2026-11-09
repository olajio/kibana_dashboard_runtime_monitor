#!/usr/bin/env python3
"""Build kibana/dashboard_health_monitor.ndjson: a Kibana dashboard over `dashboard-health-monitor`.

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


def metric_tile(name: str, title: str, column: Dict[str, Any], kql: str = "") -> Dict[str, Any]:
    return lens(name, title, "", "lnsMetric", [("m", column)],
                {"metricAccessor": "m", "showBar": False}, kql)


HELP = f"""## How to read this dashboard

Each collector run (every 30 minutes) records one result per Kibana dashboard it opens.

**Status** is decided by the collector: `ok`, `degraded` (load time of {DEGRADED_MS // 1000} s or more, or
some panels not ok) or `failed` (load time of {FAILED_MS // 1000} s or more, or half the panels not ok, or a
panel in an error state). Those two thresholds are global, so slow dashboards such as Software
Overview read `degraded` even when they are healthy for what they are.

**Not-ok panels** (`panels_not_ok`) do not depend on timing. A dashboard with a value above zero
had panels that came back empty, errored, timed out or never appeared.

**Baseline table**: use the median and p95 per dashboard to decide what "slow" means for each one.
Run `python scripts/baseline.py` on the collector host for suggested per-dashboard thresholds.

Change the time range at the top; the filters above narrow every panel to one dashboard or status.
"""


def build() -> List[Dict[str, Any]]:
    o: List[Dict[str, Any]] = [data_view()]

    # --- headline numbers
    o.append(metric_tile("runs", "Collector runs", metric("unique_count", "collector_run_id", "Collector runs")))
    o.append(metric_tile("not-ok-results", "Results with not-ok panels",
                         count("Results with not-ok panels", "panels_not_ok > 0")))
    o.append(metric_tile("degraded-failed", "Degraded or failed results",
                         count("Degraded or failed", "load_status : (degraded or failed)")))
    o.append(metric_tile("slowest", "Slowest load (ms)", metric("max", "load_time_ms", "Slowest load (ms)")))

    # --- load time over time, slowest dashboards
    avg = metric("average", "load_time_ms", "Average load time (ms)")
    o.append(lens("load-time-trend", "Average load time over time (10 slowest dashboards)",
                  "Average load_time_ms per dashboard; the 10 with the highest average in the range.",
                  "lnsXY",
                  [("x", date_histogram()),
                   ("split", terms("dashboard_title", "Dashboard", 10, by_column("y"))),
                   ("y", avg)],
                  {"preferredSeriesType": "line", "valueLabels": "hide",
                   "legend": {"isVisible": True, "position": "right", "showSingleSeries": True},
                   "axisTitlesVisibilitySettings": {"x": False, "yLeft": True, "yRight": False},
                   "yTitle": "Load time (ms)",
                   "layers": [{"layerId": "", "layerType": "data", "accessors": ["y"], "xAccessor": "x",
                               "splitAccessor": "split", "seriesType": "line", "position": "top",
                               "showGridlines": False}]}))
    # the layer id inside "layers" must match the datasource layer
    _fix_xy_layer(o[-1])

    # --- status over time
    o.append(lens("status-trend", "Results by status over time",
                  "Number of dashboard results per status, per time bucket.", "lnsXY",
                  [("x", date_histogram()),
                   ("split", terms("load_status", "Status", 5, by_column("y"))),
                   ("y", count("Results"))],
                  {"preferredSeriesType": "bar_stacked", "valueLabels": "hide",
                   "legend": {"isVisible": True, "position": "right", "showSingleSeries": True},
                   "axisTitlesVisibilitySettings": {"x": False, "yLeft": True, "yRight": False},
                   "yTitle": "Results",
                   "layers": [{"layerId": "", "layerType": "data", "accessors": ["y"], "xAccessor": "x",
                               "splitAccessor": "split", "seriesType": "bar_stacked", "position": "top",
                               "showGridlines": False}]}))
    _fix_xy_layer(o[-1])

    # --- heatmaps
    heat_vis = {"shape": "heatmap", "xAccessor": "x", "yAccessor": "y", "valueAccessor": "v",
                "gridConfig": {"type": "heatmap_grid", "isCellLabelVisible": False,
                               "isYAxisLabelVisible": True, "isXAxisLabelVisible": True,
                               "isYAxisTitleVisible": False, "isXAxisTitleVisible": False},
                "legend": {"type": "heatmap_legend", "isVisible": True, "position": "right"}}
    o.append(lens("load-heatmap", "Slowest load per dashboard (ms)",
                  "One row per dashboard, one column per time bucket; the colour is the slowest load time.",
                  "lnsHeatmap",
                  [("y", terms("dashboard_title", "Dashboard", 100, ALPHA, "asc")),
                   ("x", date_histogram()),
                   ("v", metric("max", "load_time_ms", "Slowest load (ms)"))], heat_vis))
    o.append(lens("not-ok-heatmap", "Not-ok panels per dashboard",
                  "The highest number of not-ok panels any run had, per dashboard and time bucket.",
                  "lnsHeatmap",
                  [("y", terms("dashboard_title", "Dashboard", 100, ALPHA, "asc")),
                   ("x", date_histogram()),
                   ("v", metric("max", "panels_not_ok", "Not-ok panels"))], heat_vis))

    # --- tables
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
    o.append(lens("not-ok-table", "Dashboards with not-ok panels",
                  "Results where at least one panel was not ok, by dashboard and kind of failure.",
                  "lnsDatatable",
                  [("d", terms("dashboard_title", "Dashboard", 100, by_column("nok"))),
                   ("r", count("Results")),
                   ("nok", metric("sum", "panels_not_ok", "Not-ok panels")),
                   ("e", metric("sum", "panels_empty", "Empty")),
                   ("er", metric("sum", "panels_error", "Error")),
                   ("t", metric("sum", "panels_timeout", "Timeout")),
                   ("m", metric("sum", "panels_missing", "Missing"))],
                  {"columns": [{"columnId": c} for c in ("d", "r", "nok", "e", "er", "t", "m")],
                   "sorting": {"columnId": "nok", "direction": "desc"},
                   "paging": {"size": 30, "enabled": False}},
                  kql="panels_not_ok > 0"))

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
        ("runs", "lens", 0, 0, 12, 6), ("not-ok-results", "lens", 12, 0, 12, 6),
        ("degraded-failed", "lens", 24, 0, 12, 6), ("slowest", "lens", 36, 0, 12, 6),
        ("load-time-trend", "lens", 0, 6, 28, 16), ("status-trend", "lens", 28, 6, 20, 16),
        ("load-heatmap", "lens", 0, 22, 48, 20),
        ("baseline-table", "lens", 0, 42, 48, 18),
        ("not-ok-heatmap", "lens", 0, 60, 48, 16),
        ("not-ok-table", "lens", 0, 76, 48, 14),
        ("help", "visualization", 0, 90, 48, 14),
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
            "timeRestore": True, "timeFrom": "now-7d", "timeTo": "now",
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
