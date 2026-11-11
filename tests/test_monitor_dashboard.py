"""The generated Kibana dashboard is internally consistent (no Kibana needed)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import build_monitor_dashboard as bd
import monitor_vega as mv

HERE = os.path.dirname(__file__)
OBJECTS = bd.build("light")
BY_ID = {(o["type"], o["id"]): o for o in OBJECTS}
MAPPING = json.load(open(os.path.join(HERE, "..", "es", "index_template.json")))["template"]["mappings"]["properties"]


def test_ids_are_unique_and_stable_and_the_same_in_both_themes():
    assert len(BY_ID) == len(OBJECTS)
    assert [o["id"] for o in bd.build("light")] == [o["id"] for o in OBJECTS]
    assert [o["id"] for o in bd.build("dark")] == [o["id"] for o in OBJECTS]


def test_every_reference_points_at_an_object_in_the_file():
    for o in OBJECTS:
        for r in o["references"]:
            assert (r["type"], r["id"]) in BY_ID, (o["attributes"]["title"], r)


def test_dashboard_panels_and_references_match_one_to_one():
    d = BY_ID[("dashboard", bd.DASHBOARD_ID)]
    panels = json.loads(d["attributes"]["panelsJSON"])
    names = {r["name"] for r in d["references"] if ":panel_" in r["name"]}
    assert names == {f"{p['panelIndex']}:{p['panelRefName']}" for p in panels}
    assert len(panels) == len(OBJECTS) - 2        # every object but the data view and the dashboard is a panel
    for p in panels:                              # the grid is 48 wide and panels do not overlap
        g = p["gridData"]
        assert g["x"] + g["w"] <= 48 and g["i"] == p["panelIndex"]
    cells = {(x, g["y"] + dy) for p in panels for g in [p["gridData"]]
             for x in range(g["x"], g["x"] + g["w"]) for dy in range(g["h"])}
    assert len(cells) == sum(p["gridData"]["w"] * p["gridData"]["h"] for p in panels)


def test_sections_run_in_order_down_the_page():
    d = BY_ID[("dashboard", bd.DASHBOARD_ID)]
    ys = {p["panelIndex"]: p["gridData"]["y"] for p in json.loads(d["attributes"]["panelsJSON"])}
    order = [ys[bd._id("panel/" + n)] for n in ("banner", "section-s1", "section-s2", "section-s3", "section-s4", "section-s5")]
    assert order == sorted(order) and len(set(order)) == 6


def test_controls_use_the_data_view_and_have_references():
    d = BY_ID[("dashboard", bd.DASHBOARD_ID)]
    controls = json.loads(d["attributes"]["controlGroupInput"]["panelsJSON"])
    assert {c["explicitInput"]["fieldName"] for c in controls.values()} == {"dashboard_title", "load_status"}
    refs = {r["name"] for r in d["references"] if r["name"].startswith("controlGroup_")}
    assert refs == {f"controlGroup_{cid}:optionsListDataView" for cid in controls}


def test_every_lens_object_is_self_consistent():
    lenses = [o for o in OBJECTS if o["type"] == "lens"]
    assert len(lenses) == 2
    for o in lenses:
        st = o["attributes"]["state"]
        (layer_id, layer), = st["datasourceStates"]["formBased"]["layers"].items()
        assert o["references"] == [{"type": "index-pattern", "id": bd.DATA_VIEW_ID,
                                    "name": f"indexpattern-datasource-layer-{layer_id}"}]
        cols = layer["columns"]
        assert layer["columnOrder"] == list(cols)
        buckets = [i for i, c in enumerate(layer["columnOrder"]) if cols[layer["columnOrder"][i]]["isBucketed"]]
        assert buckets == list(range(len(buckets)))                       # bucketed columns come first
        for ly in st["visualization"]["layers"]:
            assert ly["layerId"] == layer_id
            for c in ly["accessors"] + [ly["xAccessor"]] + ([ly["splitAccessor"]] if "splitAccessor" in ly else []):
                assert c in cols
        for c in cols.values():
            assert c["sourceField"] == "___records___" or c["sourceField"] in MAPPING
            ob = (c.get("params") or {}).get("orderBy")
            if ob and ob.get("type") == "column":
                assert ob["columnId"] in cols and not cols[ob["columnId"]]["isBucketed"]


def _vega_specs(objects=OBJECTS):
    for o in objects:
        if o["type"] == "visualization" and json.loads(o["attributes"]["visState"])["type"] == "vega":
            yield o["attributes"]["title"], json.loads(json.loads(o["attributes"]["visState"])["params"]["spec"])


def _sources(spec):
    """Every Elasticsearch request body in a Vega or Vega-Lite spec."""
    data = spec["data"]
    for d in (data if isinstance(data, list) else [data]):
        if "url" in d:
            yield d["url"]


def _fields(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "field" and isinstance(v, str):
                yield v
            if k == "includes":
                yield from v
            yield from _fields(v)
    elif isinstance(node, list):
        for v in node:
            yield from _fields(v)


def test_vega_panels_query_fields_that_exist_in_the_index_template():
    panels = MAPPING["panels"]
    assert panels["type"] == "nested"
    seen = 0
    for title, spec in _vega_specs():
        for url in _sources(spec):
            seen += 1
            assert url["%context%"] is True and url["%timefield%"] == "@timestamp" and url["index"] == "dashboard-health-monitor*"
            for f in _fields(url["body"]):
                if f.startswith("panels."):
                    assert f.split(".", 1)[1] in panels["properties"], (title, f)
                else:
                    assert f in MAPPING, (title, f)
    assert seen >= 8


def test_vega_panels_that_have_no_data_source_are_banners():
    banners = [t for t, s in _vega_specs() if not any(True for _ in _sources(s))]
    assert sorted(banners) == ["Section 1", "Section 2", "Section 3", "Section 4", "Section 5", "Start here"]


def test_panel_health_covers_every_status_the_collector_can_write():
    from dhm import render_detection as rd
    assert set(mv.PANEL_STATUSES) == {rd.OK, rd.EMPTY, rd.ERROR, rd.MISSING, rd.TIMEOUT}


def test_the_state_rule_uses_the_documented_threshold_and_four_states():
    expr = next(t["expr"] for t in mv._latest_source()["transform"] if t.get("as") == "state")
    assert f">= {mv.ACTION_SHARE}" in expr
    for word in ("Action", "Attention", "Slow", "Healthy"):
        assert f"'{word}'" in expr
    assert "0.5" in bd.HELP or "half" in bd.HELP        # the reference panel says the same


def test_themes_differ_only_in_colour():
    light, dark = bd.build("light"), bd.build("dark")
    assert bd.to_ndjson(light) != bd.to_ndjson(dark)
    swap = {mv.THEMES["dark"][k]: mv.THEMES["light"][k] for k in mv.THEMES["dark"] if k != "band_opacity"}
    swap["0.22"] = "0.1"
    a, b = dict(_vega_specs(light)), dict(_vega_specs(dark))
    assert a.keys() == b.keys()
    for title in a:
        text = json.dumps(b[title])
        for dark_colour, light_colour in swap.items():
            text = text.replace(dark_colour, light_colour)
        assert json.loads(text) == a[title], title


def test_zero_is_green_and_the_page_never_relies_on_colour_alone():
    # every state card also carries the word; the KPI colour expressions put green first for zero
    kpi = json.dumps(mv.kpi_cards(mv.THEMES["light"]))
    assert mv.THEMES["light"]["green"] in kpi
    assert "'Action'" in json.dumps(mv.status_grid(mv.THEMES["light"]))


def test_links_cover_the_monitored_dashboards_and_use_the_space():
    text = bd.links_markdown()
    for title, dash_id in bd.MONITORED:
        assert f"[{title}](/s/{bd.LINK_SPACE}/app/dashboards#/view/{dash_id})" in text
    assert len(bd.MONITORED) == 22
    ids = {json.loads(l)["id"] for l in open(os.path.join(HERE, "..", "federal_overview_dashboard.ndjson")) if l.strip()
           and json.loads(l).get("type") == "dashboard"}
    assert {i for _, i in bd.MONITORED} == ids


def test_ndjson_is_one_object_per_line_with_a_summary():
    lines = bd.to_ndjson(OBJECTS).splitlines()
    parsed = [json.loads(l) for l in lines]
    assert parsed[-1]["exportedCount"] == len(OBJECTS) and len(parsed) == len(OBJECTS) + 1


def test_the_checked_in_files_are_what_the_builder_produces():
    for theme, filename in bd.OUTPUTS.items():
        path = os.path.join(HERE, "..", "kibana", filename)
        assert open(path, encoding="utf-8").read() == bd.to_ndjson(bd.build(theme)), filename
