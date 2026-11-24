# Roll out per-dashboard thresholds and fail-fast (collector 0.2.0)

Run on the prd appserver as `root`. Every command names `.venv/bin/python`, so no alias can
interfere. Nothing here prints a secret.

**1. Get the code and check it**

```bash
cd /opt/kibana_dashboard_runtime_monitor && git pull origin main
.venv/bin/python -c "import dhm; print(dhm.__version__)"      # 0.2.0
.venv/bin/python scripts/run_collector.py --dry-run --out /var/lib/dhm/run.json 2>&1 | tail -30
```

The dry run reads `config/thresholds.yaml`. A bad entry stops it with `invalid per-dashboard
thresholds`; a dashboard named there that Kibana does not return is a WARNING. Look for
`(skipped)` rows: there should be none.

**2. Update the index template** (new fields `degraded_over_ms`, `failed_over_ms`, `timeout_ms`,
`load_attempts`, `elapsed_ms`)

```bash
sudo -u dhm env HOME=/var/lib/dhm bash -c \
  'cd /opt/kibana_dashboard_runtime_monitor && .venv/bin/python scripts/setup_elasticsearch.py'
```

If it answers 403, the collector key lacks `manage_index_templates`: have an admin run the same
script, or paste `es/index_template.json` into Dev Tools as `PUT _index_template/dashboard-health-monitor`.

**3. Roll the data stream over** (an admin, in Dev Tools on `cdm-fdb`). The template only applies to
new backing indices, and the new fields are searchable only in those.

```
POST dashboard-health-monitor/_rollover
```

**4. Run the collector once, then let the schedule carry on**

```bash
sudo -u dhm env HOME=/var/lib/dhm bash -c \
  'cd /opt/kibana_dashboard_runtime_monitor && .venv/bin/python scripts/run_collector.py' 2>&1 | tail -30
```

**5. Import the dashboard again** (steps below): its baseline chart now marks each dashboard's own
degraded (amber) and failed (red) limit.

**6. In about two weeks**, once Software Overview and Priority Risks have uncapped results
(timeout 300 s instead of 180 s), regenerate their limits and everyone else's:

```bash
.venv/bin/python scripts/baseline.py --days 14 --timeout-s 300 --yaml > /tmp/thresholds.new.yaml
diff config/thresholds.yaml /tmp/thresholds.new.yaml
```

Review the difference, put the new values into `config/thresholds.yaml` (keep `timeout_ms:
300000` on the two slow dashboards), commit and pull. The report lists what was left out: outage
runs, results with not-ok panels, load errors, and results cut off by the timeout.

What changed in behaviour: a timeout is no longer retried; three dashboards in a row failing to
load skips the rest of the run (recorded as `failed`, `load_error: "skipped: ..."`, so every run
still has 22 documents); no run starts new dashboards after 25 minutes. `load_time_ms` is now the
last attempt only; `elapsed_ms` is the total wall time and `load_attempts` the number of tries.

---

# Import the redesigned Dashboard Health Monitor dashboard

Two files, one dashboard. Import the one that matches your Kibana theme (Kibana's Vega panels
cannot follow the theme, so the colours are baked in):

| Your Kibana theme | File |
|---|---|
| Light (default) | `kibana/dashboard_health_monitor.ndjson` |
| Dark | `kibana/dashboard_health_monitor_dark.ndjson` |

Both use the same object ids, so importing the other one later overwrites the first.

## Import

1. Switch to the target space. Download the file from GitHub (**Download raw file**).
2. **Stack Management > Saved Objects > Import**, choose the file, and when it reports existing
   objects choose **Automatically overwrite conflicts**. This replaces the earlier version.
3. Open **Analytics > Dashboard > Dashboard Health Monitor**. It defaults to the last 24 hours and
   refreshes every 5 minutes.

## What the page is

| Section | Panels |
|---|---|
| **Start here** banner | How to read the page |
| **1 · Health right now** | Five cards (Overall health, Dashboards checked, Slow by load time, Panels not ok, Last run) and one card per dashboard, worst first, each showing its state, load time and panels ok |
| **2 · What needs attention now** | Table of dashboards not healthy, with coloured state pills; **Latest result per dashboard** (all 22, sortable); Slowest panels; Panel health heatmap |
| **3 · Trends and baseline** | **Load time over time** (the trend line, left); Dashboards by collector status; Baseline chart (median to p95 and slowest per dashboard, with the 30 s and 120 s thresholds drawn across it); Latest load time per dashboard; **Load time by dashboard (baseline)** table (runs, median, p95, slowest, not-ok panels); Collector run duration against the 30 minute schedule |
| **4 · Monitored dashboards** | Links to all 22 (they open in space `fed2`) |
| **5 · Reference** | The rules behind the states |

The states are one word each: **Healthy**, **Slow** (all panels rendered but the load time is over
the collector's threshold), **Attention** (some panels not ok) and **Action** (half or more not ok).
Overall health ignores Slow, because the 30 s and 120 s thresholds are global and Software Overview
would otherwise keep the page red until you settle the baseline.

## Panels brought back from the previous version

The two tables that the redesign had replaced are back as Lens tables, with their old titles:
**Latest result per dashboard** (section 2) and **Load time by dashboard (baseline)** (section 3).
**Load time over time** was already on the page, in section 3, to the left of *Dashboards by collector
status*. If you do not see it, tell me what is in that spot.

## New since the first version

- **Last run** card: turns amber after 45 minutes and red after 90, so a stopped collector shows up.
- **Collector run duration**: how long each run takes against the 30 minute schedule.
- **Dashboards by collector status** over time.
- **Baseline** as a range chart with the thresholds on it, plus the share of results that were ok.
- **Needs attention** table and one card per dashboard, built from each dashboard's *latest* result.
- Filters renamed with icons: Dashboard and Collector status.

## If a panel shows an error

Seven panels are Vega charts that query Elasticsearch. I rendered all of them in Chromium against
mock responses shaped like the real ones, in both themes, but I have no Elasticsearch to run the
queries on. Open the panel menu, choose **Inspect**, and send me the message as text. Do not upload
screenshots or exports of the data to this repository.

## Regenerating

Edit `scripts/build_monitor_dashboard.py` (layout) or `scripts/monitor_vega.py` (the Vega panels),
run `python scripts/build_monitor_dashboard.py`, and commit both files. `pytest` checks that every
panel, reference and field agrees, and that the two themes differ only in colour.
