# Do the "empty" panels settle? Trace one dashboard under the collector's conditions

Run on the production host. Read-only: it opens a browser against Kibana and reads the page.
Nothing is written to Elasticsearch or to disk, and the output contains panel titles only.

## Why

On CyHy Overview the collector reports 5 panels as `empty`, but a person looking at the same
dashboard in Kibana sees data in all of them. The collector stops waiting the instant every
panel counts as resolved, and an empty placeholder counts as resolved, so a placeholder that
is only a loading state would be frozen into the result.

This script loads the dashboard the way the collector does, works out when the collector
**would have stopped**, keeps watching for 30 seconds longer, and reports which panels
changed state after that point.

### The earlier experiment was withdrawn

The first version of this file ran `debug_spike.py` with different waits. That was invalid:
`debug_spike.py` opens a fresh page each time, so every run pays Kibana's full cold start
(about 10 seconds on this host), and its results said nothing about settling. The collector
reuses one page and moves between dashboards by changing the URL fragment, which Kibana
handles without rebooting. `trace_dashboard.py` does the same.

## 1. Get the checkout ready

```bash
cd /opt/kibana_dashboard_runtime_monitor && source .venv/bin/activate && git pull origin main
```

## 2. Trace CyHy Overview

Each repeat takes about a minute (warm-up, then a 30 second window). Three repeats show
whether the result is the same every time.

```bash
python scripts/trace_dashboard.py --dashboard-title "CyHy Overview" --repeat 3
```

## 3. Two more, for comparison

Priority Risks is the dashboard whose result flipped between two runs (15 empty panels, then
none). AIM Score Overview reported every panel ok, so it checks that the script reports
"ALL OK" when nothing is wrong.

```bash
python scripts/trace_dashboard.py --dashboard-title "Priority Risks" --repeat 3
python scripts/trace_dashboard.py --dashboard-title "AIM Score Overview"
```

## 4. How to read the output

Each trace ends with one verdict line.

| Verdict | What it means | What follows |
|---|---|---|
| `READ TOO EARLY` | Some panels were not ok when the collector would have stopped, but were ok by the end of the window. Those "empty" results are a loading state. | The collector needs to keep watching panels that look empty, or wait for the page to settle, before it classifies them. |
| `STABLE` | Panels that were not ok at the stop moment stayed that way for the whole window. | Timing does not explain them. Look at identity and environment instead: which user or API key the collector uses, and which Kibana host. |
| `NEVER STOPPED` | The collector's stop condition was never met within 30 seconds. | The real collector would run to its timeout on this dashboard. Something on the page never finishes. |
| `UNSTABLE` | Panels that were ok at the stop moment became not ok later. | The page is changing underneath the collector. |
| `ALL OK` | Nothing wrong at either point. | Expected for AIM Score Overview. If it says anything else there, the script itself needs a look. |

The section "still not ok at the end of the window" matters most. Those are the panels that
timing does not explain. `CDM-Nav-LCKUP` (a static text label) is expected to appear there if
it really is empty.

## 5. Send the result

Paste the output into the chat. Do not commit it. This repository is public.
