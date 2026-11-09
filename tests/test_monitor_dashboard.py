"""The generated Kibana dashboard is internally consistent (no Kibana needed)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import build_monitor_dashboard as bd

OBJECTS = bd.build()
BY_ID = {(o["type"], o["id"]): o for o in OBJECTS}


def test_ids_are_unique_and_stable():
    assert len(BY_ID) == len(OBJECTS)
    assert [o["id"] for o in bd.build()] == [o["id"] for o in OBJECTS]       # same on every build


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


def test_controls_use_the_data_view_and_have_references():
    d = BY_ID[("dashboard", bd.DASHBOARD_ID)]
    controls = json.loads(d["attributes"]["controlGroupInput"]["panelsJSON"])
    assert {c["explicitInput"]["fieldName"] for c in controls.values()} == {"dashboard_title", "load_status"}
    refs = {r["name"] for r in d["references"] if r["name"].startswith("controlGroup_")}
    assert refs == {f"controlGroup_{cid}:optionsListDataView" for cid in controls}


def test_every_lens_object_is_self_consistent():
    lenses = [o for o in OBJECTS if o["type"] == "lens"]
    assert len(lenses) == 8
    for o in lenses:
        st = o["attributes"]["state"]
        (layer_id, layer), = st["datasourceStates"]["formBased"]["layers"].items()
        assert o["references"] == [{"type": "index-pattern", "id": bd.DATA_VIEW_ID,
                                    "name": f"indexpattern-datasource-layer-{layer_id}"}]
        cols = layer["columns"]
        assert layer["columnOrder"] == list(cols)
        buckets = [i for i, c in enumerate(layer["columnOrder"]) if cols[layer["columnOrder"][i]]["isBucketed"]]
        assert buckets == list(range(len(buckets)))                       # bucketed columns come first
        vis = st["visualization"]
        used = []
        if "layers" in vis:                                              # XY
            for ly in vis["layers"]:
                assert ly["layerId"] == layer_id
                used += ly["accessors"] + [ly["xAccessor"]] + ([ly["splitAccessor"]] if "splitAccessor" in ly else [])
        else:
            assert vis["layerId"] == layer_id
            used += [vis[k] for k in ("metricAccessor", "xAccessor", "yAccessor", "valueAccessor") if k in vis]
            used += [c["columnId"] for c in vis.get("columns", [])]
        assert used and all(u in cols for u in used), (o["attributes"]["title"], used)
        for c in cols.values():
            ob = (c.get("params") or {}).get("orderBy")
            if ob and ob.get("type") == "column":
                assert ob["columnId"] in cols and not cols[ob["columnId"]]["isBucketed"]


def test_fields_used_exist_in_the_index_template():
    mapping = json.load(open(os.path.join(os.path.dirname(__file__), "..", "es", "index_template.json")))
    props = mapping["template"]["mappings"]["properties"]
    for o in OBJECTS:
        if o["type"] != "lens":
            continue
        layer, = o["attributes"]["state"]["datasourceStates"]["formBased"]["layers"].values()
        for c in layer["columns"].values():
            f = c["sourceField"]
            assert f == "___records___" or f in props, f


def test_ndjson_is_one_object_per_line_with_a_summary():
    lines = bd.to_ndjson(OBJECTS).splitlines()
    parsed = [json.loads(l) for l in lines]
    assert parsed[-1]["exportedCount"] == len(OBJECTS) and len(parsed) == len(OBJECTS) + 1


def test_the_checked_in_file_is_what_the_builder_produces():
    path = os.path.join(os.path.dirname(__file__), "..", "kibana", "dashboard_health_monitor.ndjson")
    assert open(path, encoding="utf-8").read() == bd.to_ndjson(bd.build())


def _vega_specs():
    for o in OBJECTS:
        if o["type"] == "visualization" and json.loads(o["attributes"]["visState"])["type"] == "vega":
            yield o["attributes"]["title"], json.loads(json.loads(o["attributes"]["visState"])["params"]["spec"])


def _strings(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)
    elif isinstance(node, str):
        yield node


def test_vega_charts_query_the_nested_panel_fields_that_exist():
    mapping = json.load(open(os.path.join(os.path.dirname(__file__), "..", "es", "index_template.json")))
    props = mapping["template"]["mappings"]["properties"]
    assert props["panels"]["type"] == "nested"
    specs = dict(_vega_specs())
    assert len(specs) == 2
    for title, spec in specs.items():
        url = spec["data"]["url"]
        assert url["%context%"] is True and url["%timefield%"] == "@timestamp"
        assert url["index"] == "dashboard-health-monitor*"
        for text in _strings(url["body"]):
            if text.startswith("panels."):
                assert text.split(".", 1)[1] in props["panels"]["properties"], (title, text)
            if text == "dashboard_title":
                assert text in props


def test_panel_health_covers_every_status_the_collector_can_write():
    from dhm import render_detection as rd
    written = {rd.OK, rd.EMPTY, rd.ERROR, rd.MISSING, rd.TIMEOUT}
    assert set(bd.PANEL_STATUSES) == written


def test_the_status_tiles_are_green_amber_red_and_filter_on_the_status():
    tiles = {o["attributes"]["title"]: o for o in OBJECTS if o["id"] in
             {bd._id("tile-ok"), bd._id("tile-degraded"), bd._id("tile-failed")}}
    colours = {t: o["attributes"]["state"]["visualization"]["color"] for t, o in tiles.items()}
    assert colours == {"Dashboards ok": bd.GREEN, "Dashboards degraded": bd.AMBER, "Dashboards failed": bd.RED}
    queries = {t: o["attributes"]["state"]["query"]["query"] for t, o in tiles.items()}
    assert queries == {"Dashboards ok": "load_status : ok", "Dashboards degraded": "load_status : degraded",
                       "Dashboards failed": "load_status : failed"}
