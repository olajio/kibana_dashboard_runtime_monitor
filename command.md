# Timing experiment: do the "empty" panels settle?

Run on the production host. Read-only: it opens a browser against Kibana and reads the page;
nothing is written to Elasticsearch or the cluster.

## Why

On CyHy Overview the collector reports 5 panels as `empty`, but a person looking at the same
dashboard in Kibana sees data in all of them. Every dashboard that has control dropdowns
(the Agency / IP / Operating System filters across the top) has not-ok panels in the
collector's results, and none of the dashboards without controls do. The collector stops
waiting the moment every panel counts as resolved, and an empty placeholder counts as
resolved, so it may be reading panels before they have finished loading.

This loads CyHy Overview four times, waiting longer each time, and counts the panels the
page marks as empty at each point.

## 1. Get the checkout ready

```bash
cd /opt/kibana_dashboard_runtime_monitor && source .venv/bin/activate && git pull origin main
```

## 2. Run the experiment

It takes a few minutes: each pass launches the browser and loads the dashboard.

```bash
for w in 1000 3000 8000 15000; do
  python scripts/debug_spike.py --dashboard-title "CyHy Overview" --wait-ms $w --out debug_$w.json > /dev/null
  python - $w <<'PY'
import json, sys
w = sys.argv[1]
panels = json.load(open(f"debug_{w}.json"))["panels"]
complete = [p for p in panels if p.get("descRenderCompleteTrueValue") or p.get("selfRenderComplete") == "true"]
flagged  = [p for p in panels if p.get("emptyText") is not None]
print(f"wait {w:>5} ms: {len(panels)} panels on page, {len(complete)} render-complete, {len(flagged)} flagged empty")
PY
done
```

The `debug_*.json` files are written into the checkout and are git-ignored.

## 3. How to read the output

One line per wait time. Look at the last number, "flagged empty", as the wait grows.

| What you see | What it means | What follows |
|---|---|---|
| Falls toward 0 (for example 5, 4, 1, 0) | The "empty" state is transient: panels show a placeholder while loading. | The collector needs to wait for panels to settle, or for the controls to finish loading, before reading them. |
| Stays high even at 15000 ms | Timing is not the cause. The collector's session sees something a person does not. | Look at identity and environment instead: which user or API key, which Kibana hostname, headless against headed. |
| "render-complete" climbs while "flagged empty" stays flat | Panels finish rendering but the placeholder is still in the page. | The empty check is matching an element that remains after rendering. |

## 4. Send the result

Paste the four lines into the chat.

Do not commit the output, screenshots or the `debug_*.json` files. This repository is
public, and those files contain real Kibana hostnames and agency data.
