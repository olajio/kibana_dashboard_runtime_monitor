# Import the Dashboard Health Monitor dashboard into Kibana

The file is `kibana/dashboard_health_monitor.ndjson`. It carries its own data view
(`dashboard-health-monitor*`, time field `@timestamp`), so nothing has to exist in the space first.

## 1. Get the file

On GitHub open `kibana/dashboard_health_monitor.ndjson` and use **Download raw file**, or on a
host with the checkout: `cd /opt/kibana_dashboard_runtime_monitor && git fetch origin && git reset --hard origin/main`.

## 2. Import it into the space you want

1. In Kibana, switch to the target space (top left).
2. **Stack Management > Saved Objects > Import**, choose the file.
3. If it says an object already exists (a data view with the same id, or an earlier import), choose
   **Automatically overwrite conflicts**. The ids are fixed, so importing again updates in place.
4. Open **Analytics > Dashboard > Dashboard Health Monitor**.

Your account needs read access to `dashboard-health-monitor*` to see data; you already have it in
Discover. It is the same account that imports, so nothing else is required.

## 3. What is on it

| Panel | Shows |
|---|---|
| Collector runs | Distinct runs in the time range |
| Results with not-ok panels | Results where `panels_not_ok > 0` |
| Degraded or failed results | Results with that `load_status` |
| Slowest load (ms) | Highest `load_time_ms` in the range |
| Average load time over time | The 10 dashboards with the highest average, one line each |
| Results by status over time | Stacked counts of ok, degraded and failed |
| Slowest load per dashboard | Heatmap: dashboard by time, colour is the slowest load |
| Load time by dashboard (baseline) | Per dashboard: runs, median, p95, slowest, not-ok panels |
| Not-ok panels per dashboard | Heatmap of the worst run per time bucket |
| Dashboards with not-ok panels | Table split into empty, error, timeout and missing |
| How to read this dashboard | Short explanation of the statuses |

Controls at the top filter every panel by dashboard and by status. The default range is the last
7 days with a 5 minute refresh; both are saved with the dashboard.

## 4. If something does not import or render

The import dialog lists each object that failed and why. Copy that text into the chat. Do not
upload screenshots or exports of the data to this repository.

## 5. Changing the layout

Edit `scripts/build_monitor_dashboard.py`, run `python scripts/build_monitor_dashboard.py`, and
commit the regenerated file. `pytest` checks that every panel, reference and column in it agrees.

## Meanwhile: the scheduled collector

After a :00 or :30, `tail -n 30 /var/lib/dhm/collector.log` should end with
`Indexed 22 documents into dashboard-health-monitor`.
