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
| **2 · What needs attention now** | Table of dashboards not healthy, with coloured state pills; Slowest panels; Panel health heatmap |
| **3 · Trends and baseline** | Load time over time; Dashboards by collector status; Baseline (median to p95 and slowest per dashboard, with the 30 s and 120 s thresholds drawn across it); Latest load time per dashboard; Collector run duration against the 30 minute schedule |
| **4 · Monitored dashboards** | Links to all 22 (they open in space `fed2`) |
| **5 · Reference** | The rules behind the states |

The states are one word each: **Healthy**, **Slow** (all panels rendered but the load time is over
the collector's threshold), **Attention** (some panels not ok) and **Action** (half or more not ok).
Overall health ignores Slow, because the 30 s and 120 s thresholds are global and Software Overview
would otherwise keep the page red until you settle the baseline.

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
