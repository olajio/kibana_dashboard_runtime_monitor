"""Vega specs for the Dashboard Health Monitor dashboard (look and feel: numbered section
headers, a 'Start here' banner, RAG cards, status pills, a baseline range chart).

Kibana's Vega panels do not follow the light/dark theme, so every colour comes from a THEME
dict and the dashboard is built once per theme. Nothing here talks to Kibana or Elasticsearch.

Design rules kept from the Operations / Infrastructure / MIM dashboards:
  - section headers are numbered and carry an icon; a blue "Start here" banner explains the page;
  - status is a word and a colour, never colour alone (Healthy / Slow / Attention / Action);
  - zero is green; the worst items come first;
  - a row of cards for the headline, then the detail behind it.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

TIME_FIELD = "@timestamp"
INDEX = "dashboard-health-monitor*"
VEGA = "https://vega.github.io/schema/vega/v5.json"
VEGA_LITE = "https://vega.github.io/schema/vega-lite/v5.json"

THEMES: Dict[str, Dict[str, str]] = {
    "light": {"text": "#1A2433", "muted": "#5A6B80", "card": "#F6F8FB", "line": "#D5DCE6",
              "blue": "#0B64DD", "green": "#1E9E6A", "amber": "#D68A00", "red": "#D13B34",
              "violet": "#6F5BD0", "grey": "#8794A6", "band": "#0B64DD", "band_opacity": "0.10"},
    "dark": {"text": "#E4EAF3", "muted": "#9AA8BC", "card": "#18212E", "line": "#2B3749",
             "blue": "#5B9BFF", "green": "#3CC48F", "amber": "#F0B429", "red": "#F2665D",
             "violet": "#A394F0", "grey": "#8794A6", "band": "#0B64DD", "band_opacity": "0.22"},
}

# a dashboard is "Action" when this share of its panels is not ok
ACTION_SHARE = 0.5
DEGRADED_S, FAILED_S = 30, 120          # the collector's global thresholds, drawn on the baseline
STALE_OK_MIN, STALE_WARN_MIN = 45, 90   # collector runs every 30 minutes


def S(expr: str) -> Dict[str, str]:
    return {"signal": expr}


def V(value: Any) -> Dict[str, Any]:
    return {"value": value}


def _txt(x: Any, y: Any, text: Any, fill: Any, size: int = 12, weight: str = "normal",
         align: str = "left", baseline: str = "alphabetic", limit: Any = None,
         opacity: Any = None) -> Dict[str, Any]:
    u: Dict[str, Any] = {"x": x if isinstance(x, dict) else V(x), "y": y if isinstance(y, dict) else V(y),
                         "text": text if isinstance(text, dict) else V(text),
                         "fill": fill if isinstance(fill, dict) else V(fill),
                         "fontSize": V(size), "fontWeight": V(weight), "align": V(align),
                         "baseline": V(baseline)}
    if limit is not None:
        u["limit"] = limit if isinstance(limit, dict) else V(limit)
    if opacity is not None:
        u["opacity"] = opacity if isinstance(opacity, dict) else V(opacity)
    return {"type": "text", "encode": {"update": u}}


def _rect(x: Any, y: Any, w: Any, h: Any, fill: Any, stroke: Any = None, radius: int = 0,
          opacity: Any = None, strokeWidth: int = 1) -> Dict[str, Any]:
    wrap = lambda v: v if isinstance(v, dict) else V(v)       # noqa: E731
    u: Dict[str, Any] = {"x": wrap(x), "y": wrap(y), "width": wrap(w), "height": wrap(h), "fill": wrap(fill),
                         "cornerRadius": V(radius)}
    if stroke is not None:
        u["stroke"] = wrap(stroke)
        u["strokeWidth"] = V(strokeWidth)
    if opacity is not None:
        u["fillOpacity"] = wrap(opacity)
    return {"type": "rect", "encode": {"update": u}}


def _vega(data: List[Dict[str, Any]], marks: List[Dict[str, Any]], signals: Optional[List[Dict[str, Any]]] = None,
          scales: Optional[List[Dict[str, Any]]] = None, axes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    spec: Dict[str, Any] = {"$schema": VEGA, "autosize": {"type": "fit", "contains": "padding"},
                            "padding": 0, "data": data, "marks": marks}
    if signals:
        spec["signals"] = signals
    if scales:
        spec["scales"] = scales
    if axes:
        spec["axes"] = axes
    return spec


# --------------------------------------------------------------------------- banners
def start_here(t: Dict[str, str], title: str, text: str) -> Dict[str, Any]:
    """The blue 'Start here' band with a numbered circle."""
    return _vega([], [
        _rect(0, 0, S("width"), S("height"), t["band"], radius=6, opacity=float(t["band_opacity"])),
        _rect(0, 0, 6, S("height"), t["band"], radius=3),
        {"type": "symbol", "encode": {"update": {"x": V(34), "y": S("height/2"), "size": V(900),
                                                  "fill": V(t["band"]), "shape": V("circle")}}},
        _txt(34, S("height/2 + 1"), "1", "#FFFFFF", 15, "bold", "center", "middle"),
        _txt(62, S("height/2 - 6"), title, t["blue"], 14, "bold", limit=S("width - 80")),
        _txt(62, S("height/2 + 14"), text, t["muted"], 12, limit=S("width - 80")),
    ])


def section_header(t: Dict[str, str], icon: str, number: int, title: str, note: str = "") -> Dict[str, Any]:
    marks = [
        _txt(4, S("height/2 + 1"), f"{icon}  {number} · {title}", t["text"], 17, "bold", baseline="middle"),
        _rect(0, S("height - 2"), S("width"), 1, t["line"]),
    ]
    if note:
        marks.append(_txt(S("width - 4"), S("height/2 + 1"), note, t["muted"], 12, align="right", baseline="middle"))
    return _vega([], marks)


# --------------------------------------------------------------------------- data
def _latest_source(extra_transform: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The most recent result of each dashboard in the time range, one row each, classified."""
    src = ["load_status", "load_time_ms", "panels_not_ok", "panel_count", "panels_checked",
           "expected_data_panels", TIME_FIELD, "collector_run_id"]
    body = {"size": 0, "aggs": {"d": {"terms": {"field": "dashboard_title", "size": 100}, "aggs": {
        "latest": {"top_hits": {"size": 1, "sort": [{TIME_FIELD: {"order": "desc"}}],
                                "_source": {"includes": src}}}}}}}
    transform: List[Dict[str, Any]] = [
        {"type": "formula", "as": "src", "expr": "datum.latest.hits.hits[0]._source"},
        {"type": "formula", "as": "name", "expr": "datum.key"},
        {"type": "formula", "as": "status", "expr": "datum.src.load_status"},
        {"type": "formula", "as": "ms", "expr": "datum.src.load_time_ms || 0"},
        {"type": "formula", "as": "nok", "expr": "datum.src.panels_not_ok || 0"},
        {"type": "formula", "as": "total", "expr": "datum.src.panel_count || datum.src.panels_checked || 0"},
        {"type": "formula", "as": "ts", "expr": f"time(toDate(datum.src['{TIME_FIELD}']))"},
        {"type": "formula", "as": "age_min", "expr": "(now() - datum.ts) / 60000"},
        {"type": "formula", "as": "share", "expr": "datum.total > 0 ? datum.nok / datum.total : 0"},
        {"type": "formula", "as": "state",
         "expr": f"datum.share >= {ACTION_SHARE} ? 'Action' : (datum.nok > 0 ? 'Attention' : "
                 f"(datum.status === 'ok' ? 'Healthy' : 'Slow'))"},
        {"type": "formula", "as": "rank",
         "expr": "datum.state === 'Action' ? 0 : (datum.state === 'Attention' ? 1 : (datum.state === 'Slow' ? 2 : 3))"},
    ]
    return {"name": "latest",
            "url": {"%context%": True, "%timefield%": TIME_FIELD, "index": INDEX, "body": body},
            "format": {"property": "aggregations.d.buckets"},
            "transform": transform + (extra_transform or [])}


def _state_color(t: Dict[str, str], field: str = "datum.state") -> str:
    return (f"{field} === 'Action' ? '{t['red']}' : ({field} === 'Attention' ? '{t['amber']}' : "
            f"({field} === 'Slow' ? '{t['violet']}' : '{t['green']}'))")


def _age_text(expr: str) -> str:
    return f"({expr}) < 90 ? round({expr}) + ' min ago' : round(({expr}) / 60) + ' h ago'"


# --------------------------------------------------------------------------- KPI cards
def kpi_cards(t: Dict[str, str]) -> Dict[str, Any]:
    kpi = {"name": "kpi", "source": "latest", "transform": [
        {"type": "formula", "as": "is_action", "expr": "datum.state === 'Action' ? 1 : 0"},
        {"type": "formula", "as": "is_attention", "expr": "datum.state === 'Attention' ? 1 : 0"},
        {"type": "formula", "as": "is_slow", "expr": "datum.state === 'Slow' ? 1 : 0"},
        {"type": "formula", "as": "has_nok", "expr": "datum.nok > 0 ? 1 : 0"},
        {"type": "aggregate", "fields": ["is_action", "is_attention", "is_slow", "has_nok", "nok", "ts", "ms"],
         "ops": ["sum", "sum", "sum", "sum", "sum", "max", "max"],
         "as": ["action", "attention", "slow", "with_nok", "nok_total", "newest", "slowest"]},
        {"type": "formula", "as": "n", "expr": "length(data('latest'))"},
        {"type": "formula", "as": "age", "expr": "(now() - datum.newest) / 60000"},
    ]}
    signals = [{"name": "k", "update": "data('kpi')[0] || {action: 0, attention: 0, slow: 0, with_nok: 0, "
                                       "nok_total: 0, n: 0, age: 0, slowest: 0}"},
               {"name": "verdict", "update": "k.action > 0 ? 'Action' : (k.attention > 0 ? 'Attention' : 'Healthy')"},
               {"name": "verdict_color", "update": _state_color(t, "verdict")},
               {"name": "age_color", "update": f"k.n === 0 ? '{t['grey']}' : (k.age <= {STALE_OK_MIN} ? '{t['green']}' : "
                                                f"(k.age <= {STALE_WARN_MIN} ? '{t['amber']}' : '{t['red']}'))"}]

    def card(slot: int, span: int, title: str, value: str, color: str, sub: str, size: int = 30) -> List[Dict[str, Any]]:
        x, w = f"{slot} * width / 6 + 4", f"{span} * width / 6 - 8"
        return [
            _rect(S(x), 4, S(w), S("height - 8"), t["card"], t["line"], 6),
            _rect(S(x), 4, S(w), 4, S(color), radius=2),
            _txt(S(x + " + 16"), 30, title, t["muted"], 12, "600"),
            _txt(S(x + " + 16"), S("height / 2 + 14"), S(value), S(color), size, "bold"),
            _txt(S(x + " + 16"), S("height - 18"), S(sub), t["muted"], 11, limit=S(w + " - 32")),
        ]

    marks: List[Dict[str, Any]] = []
    marks += card(0, 2, "🧭  Overall health", "verdict", "verdict_color",
                  "k.action + ' action · ' + k.attention + ' attention · ' + k.slow + ' slow'", 34)
    marks += card(2, 1, "📋  Dashboards checked", "k.n", f"'{t['blue']}'", "'latest result of each'")
    marks += card(3, 1, "🐢  Slow by load time", "k.slow", f"k.slow > 0 ? '{t['violet']}' : '{t['green']}'",
                  "'all panels ok, but slow'")
    marks += card(4, 1, "⚠️  Panels not ok", "k.nok_total",
                  f"k.action > 0 ? '{t['red']}' : (k.nok_total > 0 ? '{t['amber']}' : '{t['green']}')",
                  "'across ' + k.with_nok + ' dashboards'")
    marks += card(5, 1, "⏱  Last run", _age_text("k.age"), "age_color",
                  f"'collector runs every 30 min'", 24)
    return _vega([_latest_source(), kpi], marks, signals)


# --------------------------------------------------------------------------- status grid
def status_grid(t: Dict[str, str]) -> Dict[str, Any]:
    extra = [
        {"type": "collect", "sort": {"field": ["rank", "ms"], "order": ["ascending", "descending"]}},
        {"type": "window", "ops": ["row_number"], "as": ["idx"]},
        {"type": "formula", "as": "col", "expr": "(datum.idx - 1) % ncols"},
        {"type": "formula", "as": "row", "expr": "floor((datum.idx - 1) / ncols)"},
        {"type": "formula", "as": "color", "expr": _state_color(t)},
        {"type": "formula", "as": "detail",
         "expr": "format(datum.ms / 1000, '.1f') + ' s  ·  ' + (datum.total - datum.nok) + '/' + datum.total + ' panels ok'"},
    ]
    signals = [{"name": "ncols", "update": "max(1, floor(width / 190))"},
               {"name": "cw", "update": "width / ncols"}, {"name": "rh", "value": 88}]
    X, Y = "datum.col * cw + 4", "datum.row * rh + 4"
    marks = [
        {"type": "rect", "from": {"data": "latest"}, "encode": {"update": {
            "x": S(X), "y": S(Y), "width": S("cw - 8"), "height": S("rh - 8"), "fill": V(t["card"]),
            "stroke": V(t["line"]), "cornerRadius": V(6),
            "tooltip": S("{'Dashboard': datum.name, 'State': datum.state, 'Collector status': datum.status, "
                         "'Load time (s)': format(datum.ms / 1000, '.1f'), 'Panels not ok': datum.nok + ' of ' + datum.total}")}}},
        {"type": "rect", "from": {"data": "latest"}, "encode": {"update": {
            "x": S(X), "y": S(Y), "width": S("cw - 8"), "height": V(4), "fill": {"field": "color"},
            "cornerRadius": V(2)}}},
        {"type": "text", "from": {"data": "latest"}, "encode": {"update": {
            "x": S(X + " + 12"), "y": S(Y + " + 30"), "text": {"field": "name"}, "fill": V(t["text"]),
            "fontSize": V(12), "fontWeight": V("600"), "limit": S("cw - 32")}}},
        {"type": "text", "from": {"data": "latest"}, "encode": {"update": {
            "x": S(X + " + 12"), "y": S(Y + " + 52"), "text": {"field": "state"}, "fill": {"field": "color"},
            "fontSize": V(15), "fontWeight": V("bold")}}},
        {"type": "text", "from": {"data": "latest"}, "encode": {"update": {
            "x": S(X + " + 12"), "y": S(Y + " + 69"), "text": {"field": "detail"}, "fill": V(t["muted"]),
            "fontSize": V(11), "limit": S("cw - 32")}}},
    ]
    return _vega([_latest_source(extra)], marks, signals)


# --------------------------------------------------------------------------- attention table
def attention_table(t: Dict[str, str], max_rows: int = 8) -> Dict[str, Any]:
    extra = [
        {"type": "filter", "expr": "datum.rank < 3"},
        {"type": "collect", "sort": {"field": ["rank", "ms"], "order": ["ascending", "descending"]}},
        {"type": "window", "ops": ["row_number"], "as": ["idx"]},
        {"type": "formula", "as": "color", "expr": _state_color(t)},
        {"type": "formula", "as": "age_text", "expr": _age_text("datum.age_min")},
    ]
    shown = {"name": "shown", "source": "latest", "transform": [{"type": "filter", "expr": f"datum.idx <= {max_rows}"}]}
    cols = [("#", 0.01), ("Dashboard", 0.045), ("State", 0.36), ("Not-ok panels", 0.47),
            ("Load time", 0.60), ("Collector status", 0.72), ("Last result", 0.86)]
    X = lambda f: f"width * {f}"                                    # noqa: E731
    Y = "34 + (datum.idx - 1) * 26"
    marks: List[Dict[str, Any]] = [_rect(0, 0, S("width"), 28, t["card"], radius=4)]
    for label, f in cols:
        marks.append(_txt(S(X(f)), 19, label, t["muted"], 11, "600"))
    marks += [
        {"type": "rect", "from": {"data": "shown"}, "encode": {"update": {
            "x": V(0), "y": S(Y + " - 4"), "width": S("width"), "height": V(1), "fill": V(t["line"])}}},
        {"type": "text", "from": {"data": "shown"}, "encode": {"update": {
            "x": S(X(0.01)), "y": S(Y + " + 14"), "text": {"field": "idx"}, "fill": V(t["muted"]), "fontSize": V(11)}}},
        {"type": "text", "from": {"data": "shown"}, "encode": {"update": {
            "x": S(X(0.045)), "y": S(Y + " + 14"), "text": {"field": "name"}, "fill": V(t["text"]),
            "fontSize": V(12), "fontWeight": V("600"), "limit": S(X(0.30))}}},
        {"type": "rect", "from": {"data": "shown"}, "encode": {"update": {
            "x": S(X(0.36)), "y": S(Y + " + 1"), "width": V(84), "height": V(18), "cornerRadius": V(9),
            "fill": {"field": "color"}}}},
        {"type": "text", "from": {"data": "shown"}, "encode": {"update": {
            "x": S(X(0.36) + " + 42"), "y": S(Y + " + 14"), "text": {"field": "state"}, "fill": V("#FFFFFF"),
            "fontSize": V(11), "fontWeight": V("bold"), "align": V("center")}}},
        {"type": "text", "from": {"data": "shown"}, "encode": {"update": {
            "x": S(X(0.47)), "y": S(Y + " + 14"), "text": {"signal": "datum.nok + ' of ' + datum.total"},
            "fill": V(t["text"]), "fontSize": V(12)}}},
        {"type": "text", "from": {"data": "shown"}, "encode": {"update": {
            "x": S(X(0.60)), "y": S(Y + " + 14"), "text": {"signal": "format(datum.ms / 1000, '.1f') + ' s'"},
            "fill": V(t["text"]), "fontSize": V(12)}}},
        {"type": "text", "from": {"data": "shown"}, "encode": {"update": {
            "x": S(X(0.72)), "y": S(Y + " + 14"), "text": {"field": "status"}, "fill": V(t["muted"]), "fontSize": V(12)}}},
        {"type": "text", "from": {"data": "shown"}, "encode": {"update": {
            "x": S(X(0.86)), "y": S(Y + " + 14"), "text": {"field": "age_text"}, "fill": V(t["muted"]), "fontSize": V(12)}}},
        _txt(S("width / 2"), 70, "✅  Nothing needs attention: every dashboard's latest result is healthy.", t["green"], 14,
             "600", "center", opacity=S("length(data('latest')) == 0 ? 1 : 0")),
        _txt(S("width - 4"), S("height - 6"), S(f"length(data('latest')) > {max_rows} ? '+ ' + (length(data('latest')) - {max_rows}) + ' more not shown' : ''"),
             t["muted"], 11, align="right"),
    ]
    return _vega([_latest_source(extra), shown], marks)


# --------------------------------------------------------------------------- baseline range chart
def baseline_chart(t: Dict[str, str]) -> Dict[str, Any]:
    body = {"size": 0, "aggs": {"d": {"terms": {"field": "dashboard_title", "size": 100}, "aggs": {
        "load": {"percentiles": {"field": "load_time_ms", "percents": [50, 95]}},
        "mx": {"max": {"field": "load_time_ms"}},
        "ok": {"filter": {"term": {"load_status": "ok"}}}}}}}
    base = {"name": "base", "url": {"%context%": True, "%timefield%": TIME_FIELD, "index": INDEX, "body": body},
            "format": {"property": "aggregations.d.buckets"},
            "transform": [
                {"type": "formula", "as": "name", "expr": "datum.key"},
                {"type": "formula", "as": "p50", "expr": "max(0.1, datum.load.values['50.0'] / 1000)"},
                {"type": "formula", "as": "p95", "expr": "max(0.1, datum.load.values['95.0'] / 1000)"},
                {"type": "formula", "as": "mx_s", "expr": "max(0.1, datum.mx.value / 1000)"},
                {"type": "formula", "as": "ok_pct", "expr": "datum.doc_count > 0 ? datum.ok.doc_count / datum.doc_count : 0"},
                {"type": "collect", "sort": {"field": "p95", "order": "descending"}},
                {"type": "window", "ops": ["row_number"], "as": ["idx"]},
                {"type": "formula", "as": "y", "expr": "40 + (datum.idx - 1) * 24"}]}
    top = {"name": "top", "source": "base", "transform": [
        {"type": "aggregate", "fields": ["mx_s"], "ops": ["max"], "as": ["m"]}]}
    signals = [{"name": "left", "value": 230}, {"name": "right", "update": "width - 250"},
               {"name": "dmax", "update": f"max({FAILED_S} * 1.4, (data('top')[0] || {{m: 10}}).m * 1.3)"}]
    scales = [{"name": "x", "type": "log", "domain": [0.1, {"signal": "dmax"}], "range": [{"signal": "left"}, {"signal": "right"}],
               "clamp": True, "zero": False}]
    marks: List[Dict[str, Any]] = [
        _rect(0, 0, S("width"), 28, t["card"], radius=4),
        _txt(12, 19, "Dashboard", t["muted"], 11, "600"),
        _txt(S("left"), 19, "bar = median to p95 · dot = median · tick = slowest   (seconds, log scale)", t["muted"], 11, "600"),
        _txt(S("width - 8"), 19, "median · p95 · slowest · ok %", t["muted"], 11, "600", "right"),
    ]
    for secs in (0.1, 1, 10, 100):
        marks.append({"type": "rule", "encode": {"update": {
            "x": S(f"scale('x', {secs})"), "x2": S(f"scale('x', {secs})"), "y": V(32), "y2": S("height - 14"),
            "stroke": V(t["line"]), "opacity": V(0.8)}}})
        marks.append(_txt(S(f"scale('x', {secs})"), S("height - 2"), f"{secs:g} s", t["muted"], 10, align="center"))
    for secs, label, col in ((DEGRADED_S, f"{DEGRADED_S} s degraded", t["amber"]), (FAILED_S, f"{FAILED_S} s failed", t["red"])):
        marks.append({"type": "rule", "encode": {"update": {
            "x": S(f"scale('x', {secs})"), "x2": S(f"scale('x', {secs})"), "y": V(32), "y2": S("height - 14"),
            "stroke": V(col), "strokeDash": V([4, 3]), "opacity": V(0.7)}}})
        marks.append(_txt(S(f"scale('x', {secs}) " + ("- 4" if secs == DEGRADED_S else "+ 4")), S("height - 18"), label, col, 10,
                          align="right" if secs == DEGRADED_S else "left"))
    marks += [
        {"type": "text", "from": {"data": "base"}, "encode": {"update": {
            "x": V(12), "y": {"field": "y", "offset": 14}, "text": {"field": "name"}, "fill": V(t["text"]),
            "fontSize": V(12), "limit": V(200)}}},
        {"type": "rect", "from": {"data": "base"}, "encode": {"update": {
            "x": S("scale('x', datum.p50)"), "x2": S("scale('x', max(datum.p95, datum.p50 * 1.02))"),
            "y": {"field": "y", "offset": 6}, "height": V(8), "cornerRadius": V(4), "fill": V(t["blue"]),
            "fillOpacity": V(0.45),
            "tooltip": S("{'Dashboard': datum.name, 'Median (s)': format(datum.p50, '.1f'), 'p95 (s)': format(datum.p95, '.1f'), "
                         "'Slowest (s)': format(datum.mx_s, '.1f'), 'Runs': datum.doc_count}")}}},
        {"type": "symbol", "from": {"data": "base"}, "encode": {"update": {
            "x": S("scale('x', datum.p50)"), "y": {"field": "y", "offset": 10}, "size": V(60), "fill": V(t["blue"])}}},
        {"type": "rect", "from": {"data": "base"}, "encode": {"update": {
            "x": S("scale('x', datum.mx_s) - 1"), "width": V(2), "y": {"field": "y", "offset": 4}, "height": V(12),
            "fill": V(t["amber"])}}},
        {"type": "text", "from": {"data": "base"}, "encode": {"update": {
            "x": S("width - 8"), "y": {"field": "y", "offset": 14}, "align": V("right"), "fill": V(t["muted"]), "fontSize": V(11),
            "text": S("format(datum.p50, '.1f') + '  ·  ' + format(datum.p95, '.1f') + '  ·  ' + format(datum.mx_s, '.1f') + "
                      "'  ·  ' + round(datum.ok_pct * 100) + '%'")}}},
    ]
    return _vega([base, top], marks, signals, scales)


# --------------------------------------------------------------------------- Vega-Lite charts
def vl_config(t: Dict[str, str]) -> Dict[str, Any]:
    return {"view": {"stroke": None}, "background": None,
            "axis": {"labelColor": t["muted"], "titleColor": t["muted"], "gridColor": t["line"],
                     "domainColor": t["line"], "tickColor": t["line"], "labelFontSize": 11},
            "legend": {"labelColor": t["text"], "titleColor": t["muted"]},
            "title": {"color": t["text"], "fontSize": 13, "anchor": "start"}}


def _vl_data(aggs: Dict[str, Any], prop: str, size: int = 0) -> Dict[str, Any]:
    return {"url": {"%context%": True, "%timefield%": TIME_FIELD, "index": INDEX,
                    "body": {"size": size, "aggs": aggs}}, "format": {"property": prop}}


def status_over_time(t: Dict[str, str]) -> Dict[str, Any]:
    """Dashboards per collector status in each time bucket: how many were ok, slow or failed."""
    aggs = {"h": {"date_histogram": {"field": TIME_FIELD, "interval": {"%autointerval%": 24}, "min_doc_count": 1},
                  "aggs": {st: {"filter": {"term": {"load_status": st}},
                                "aggs": {"n": {"cardinality": {"field": "dashboard_title"}}}}
                           for st in ("ok", "degraded", "failed")}}}
    return {"$schema": VEGA_LITE, "title": "Dashboards by collector status", "config": vl_config(t),
            "data": _vl_data(aggs, "aggregations.h.buckets"),
            "transform": [{"calculate": "datum.key", "as": "t"}] +
                         [{"calculate": f"datum.{st}.n.value", "as": st} for st in ("ok", "degraded", "failed")] +
                         [{"fold": ["ok", "degraded", "failed"], "as": ["status", "count"]}],
            "mark": {"type": "bar", "cornerRadiusTopLeft": 2, "cornerRadiusTopRight": 2},
            "encoding": {
                "x": {"field": "t", "type": "temporal", "title": None},
                "y": {"field": "count", "type": "quantitative", "title": "Dashboards", "stack": "zero"},
                "color": {"field": "status", "type": "nominal", "title": None,
                          "scale": {"domain": ["ok", "degraded", "failed"], "range": [t["green"], t["amber"], t["red"]]}},
                "order": {"field": "status", "type": "nominal", "sort": "descending"},
                "tooltip": [{"field": "t", "type": "temporal", "title": "Time"}, {"field": "status"},
                            {"field": "count", "title": "Dashboards"}]}}


def run_duration(t: Dict[str, str]) -> Dict[str, Any]:
    """How long each collector run took, against the 30 minute schedule."""
    aggs = {"r": {"terms": {"field": "collector_run_id", "size": 200, "order": {"first": "desc"}},
                  "aggs": {"first": {"min": {"field": TIME_FIELD}}, "last": {"max": {"field": TIME_FIELD}}}}}
    return {"$schema": VEGA_LITE, "title": "Collector run duration (minutes)", "config": vl_config(t),
            "data": _vl_data(aggs, "aggregations.r.buckets"),
            "transform": [{"calculate": "datum.first.value", "as": "start"},
                          {"calculate": "(datum.last.value - datum.first.value) / 60000", "as": "minutes"}],
            "layer": [
                {"mark": {"type": "bar", "color": t["blue"], "opacity": 0.75, "cornerRadiusTopLeft": 2,
                          "cornerRadiusTopRight": 2},
                 "encoding": {"x": {"field": "start", "type": "temporal", "title": None},
                              "y": {"field": "minutes", "type": "quantitative", "title": "Minutes"},
                              "tooltip": [{"field": "start", "type": "temporal", "title": "Run started"},
                                          {"field": "minutes", "title": "Minutes", "format": ".1f"},
                                          {"field": "doc_count", "title": "Results"}]}},
                {"data": {"values": [{"y": 30}]}, "mark": {"type": "rule", "color": t["red"], "strokeDash": [5, 4]},
                 "encoding": {"y": {"field": "y", "type": "quantitative"}}},
                {"data": {"values": [{"y": 30}]}, "mark": {"type": "text", "align": "left", "dx": 4, "dy": -6,
                                                            "color": t["red"], "fontSize": 10, "text": "next run starts (30 min)"},
                 "encoding": {"y": {"field": "y", "type": "quantitative"}}}]}


PANEL_STATUSES = ["ok", "empty", "error", "timeout", "missing"]


def slowest_panels(t: Dict[str, str]) -> Dict[str, Any]:
    aggs = {"panels": {"nested": {"path": "panels"}, "aggs": {"by_panel": {
        "terms": {"field": "panels.panel_id", "size": 20, "order": {"avg_ms": "desc"}},
        "aggs": {"avg_ms": {"avg": {"field": "panels.render_ms"}}, "max_ms": {"max": {"field": "panels.render_ms"}},
                 "title": {"terms": {"field": "panels.panel_title", "size": 1}},
                 "page": {"reverse_nested": {}, "aggs": {"dash": {"terms": {"field": "dashboard_title", "size": 1}}}}}}}}}
    return {"$schema": VEGA_LITE, "title": "Slowest panels (average render time)", "config": vl_config(t),
            "data": _vl_data(aggs, "aggregations.panels.by_panel.buckets"),
            "transform": [
                {"calculate": "datum.page.dash.buckets[0].key + ' / ' + datum.title.buckets[0].key", "as": "label"},
                {"calculate": "datum.avg_ms.value / 1000", "as": "avg"},
                {"calculate": "datum.max_ms.value / 1000", "as": "max"}],
            "mark": {"type": "bar", "color": t["blue"], "opacity": 0.8, "cornerRadiusEnd": 3},
            "encoding": {
                "y": {"field": "label", "type": "nominal", "sort": "-x", "title": None, "axis": {"labelLimit": 340}},
                "x": {"field": "avg", "type": "quantitative", "title": "Average render time (s)"},
                "tooltip": [{"field": "label", "title": "Panel"}, {"field": "avg", "title": "Average (s)", "format": ".1f"},
                            {"field": "max", "title": "Slowest (s)", "format": ".1f"},
                            {"field": "doc_count", "title": "Samples"}]}}


def panel_health(t: Dict[str, str]) -> Dict[str, Any]:
    per = {st: {"nested": {"path": "panels"}, "aggs": {"f": {"filter": {"term": {"panels.render_status": st}}}}}
           for st in PANEL_STATUSES}
    aggs = {"by_dash": {"terms": {"field": "dashboard_title", "size": 100, "order": {"_key": "asc"}}, "aggs": per}}
    return {"$schema": VEGA_LITE, "title": "Panel health by dashboard (share of panel results)", "config": vl_config(t),
            "data": _vl_data(aggs, "aggregations.by_dash.buckets"),
            "transform": ([{"calculate": "datum.key", "as": "dashboard"}] +
                          [{"calculate": f"datum.{st}.f.doc_count", "as": st} for st in PANEL_STATUSES] +
                          [{"fold": PANEL_STATUSES, "as": ["status", "count"]},
                           {"joinaggregate": [{"op": "sum", "field": "count", "as": "total"}], "groupby": ["dashboard"]},
                           {"calculate": "datum.total > 0 ? datum.count / datum.total : 0", "as": "share"}]),
            "encoding": {
                "x": {"field": "status", "type": "ordinal", "sort": PANEL_STATUSES, "title": None,
                      "axis": {"orient": "top", "labelAngle": 0}},
                "y": {"field": "dashboard", "type": "nominal", "title": None, "axis": {"labelLimit": 260}}},
            "layer": [
                {"mark": {"type": "rect", "cornerRadius": 3},
                 "encoding": {"color": {"field": "status", "type": "nominal", "legend": None,
                                        "scale": {"domain": PANEL_STATUSES,
                                                  "range": [t["green"], t["amber"], t["red"], t["red"], t["grey"]]}},
                              "opacity": {"field": "share", "type": "quantitative", "legend": None,
                                          "scale": {"domain": [0, 1], "range": [0.07, 1]}},
                              "tooltip": [{"field": "dashboard"}, {"field": "status"},
                                          {"field": "count", "title": "Panel results"},
                                          {"field": "share", "format": ".1%"}]}},
                {"mark": {"type": "text", "fontSize": 10},
                 "encoding": {"text": {"field": "share", "type": "quantitative", "format": ".0%"},
                              "color": {"condition": {"test": "datum.share > 0.6", "value": "white"}, "value": t["text"]}}}]}


def spec_json(spec: Dict[str, Any]) -> str:
    return json.dumps(spec, indent=1)
