# Import the Dashboard Health Monitor dashboard into Kibana

The file is `kibana/dashboard_health_monitor.ndjson`. It carries its own data view
(`dashboard-health-monitor*`, time field `@timestamp`), so nothing has to exist in the space first.
It replaces the first version of this file: the panels are now the ones recommended in HOWTO
section 6.

## 1. Get the file

On GitHub open `kibana/dashboard_health_monitor.ndjson` and use **Download raw file**.

## 2. Import it into the space you want

1. In Kibana, switch to the target space (top left).
2. **Stack Management > Saved Objects > Import**, choose the file.
3. If it says an object already exists (the data view, or the earlier version of this dashboard),
   choose **Automatically overwrite conflicts**. The ids are fixed, so importing again updates in place.
4. Open **Analytics > Dashboard > Dashboard Health Monitor**.

## 3. What is on it

| Panel | Shows |
|---|---|
| Four tiles (green, amber, red, plain) | Dashboards ok, degraded, failed, and dashboards with not-ok panels, in the time range |
| Load time over time | Average load time, one line per dashboard (all of them) |
| Latest load time per dashboard | One bar per dashboard: its most recent load time |
| Latest result per dashboard | Table: most recent status, load time, not-ok panels and run time |
| Slowest panels | Average render time of individual panels, from the nested `panels` records |
| Panel health by dashboard | Heatmap: share of each dashboard's panel results that were ok, empty, error, timeout or missing |
| Load time by dashboard (baseline) | Per dashboard: runs, median, p95, slowest, not-ok panels |
| How to read this dashboard | Short explanation |

The default range is the last hour, refreshing every 30 seconds, both saved with the dashboard.
Make the range longer for the trend line and the baseline table.

## 4. Two panels are Vega charts

Slowest panels and Panel health read the nested `panels` field, which Lens cannot aggregate, so
they query Elasticsearch directly. I rendered both specs with Vega-Lite against mock responses
here, but I have no Elasticsearch to run the queries against. If either shows an error, open the
panel menu, choose Inspect, and send me the message as text.

## 5. If something does not import or render

The import dialog lists each object that failed and why. Copy that text into the chat. Do not
upload screenshots or exports of the data to this repository.

## 6. Changing the layout

Edit `scripts/build_monitor_dashboard.py`, run `python scripts/build_monitor_dashboard.py`, and
commit the regenerated file. `pytest` checks that every panel, reference, column and field in it agrees.
