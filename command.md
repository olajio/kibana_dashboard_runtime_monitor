# Why do the collector's panels read "empty"? Two checks, run in this order

Run on the production host. Both are read-only. Output contains panel titles, index
patterns and document counts, never the API key.

## What the CyHy trace showed (the last run you sent)

- **CyHy Overview was STABLE on all three repeats.** The 7 panels that were `empty` when the
  collector would have stopped were still empty 30 seconds later, identically each time. So
  waiting longer does not fix them; timing is not the cause.
- The empty panels read `cdm_cyhy_vuln` and `cdm_cyhy_host_scans`. The panel on
  `cdm_cyhy_vuln_trending` and the navigation panel were ok. That split by index is what a
  credential that cannot see (or sees fewer documents in) some indices looks like.
- **Priority Risks crashed the browser page** (`Page crashed`). That is a resource problem on
  the host, not a Kibana verdict. Check 3 below looks at memory.
- **AIM Score Overview was inconsistent between repeats** (errors on one, none on another),
  which points at intermittent backend errors rather than a fixed cause.

I earlier said the false "empty" results were a timing problem. The trace shows that was wrong
for CyHy Overview, so the next step is the access check below, not a longer wait.

## 1. Get the checkout ready

```bash
cd /opt/kibana_dashboard_runtime_monitor && source .venv/bin/activate && git pull origin main
```

## 2. Count documents the way the collector's key sees them

One request per data-view pattern in the dashboard export (32 patterns).

```bash
python scripts/check_access.py
```

Each row is `readable`, `EMPTY` (HTTP 200, zero documents), `DENIED` (401/403), or `ERROR`.
Then, **in Kibana Dev Tools logged in as yourself**, run for any pattern that is not `readable`:

```
GET cdm_cyhy_vuln/_count
GET cdm_cyhy_host_scans/_count
```

| Collector's key says | Your Dev Tools says | Meaning |
|---|---|---|
| `EMPTY` (0) | a real count | The key is filtered: document- or field-level security, or a remote cluster it is not authorised on. Fix the role. |
| `DENIED` | a real count | The key lacks `read` on that index. Fix the role in `es/api_key_collector*.json`. |
| `readable`, same count | same count | The key sees the data. The cause is elsewhere (Kibana-side). |

## 3. Why did Priority Risks crash the page?

```bash
free -m
df -h /dev/shm
dmesg -T 2>/dev/null | grep -iE "oom|killed process" | tail -5
```

A low `available` memory figure, a small `/dev/shm`, or an OOM line naming a browser process
means the host is too small for that dashboard. Paste all three outputs.

## 4. Send the result

Paste the output into the chat. Do not commit it. This repository is public.

---

# Do the "empty" panels settle? Trace one dashboard (already run once; kept for reference)

### Why

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

### Trace step 1. Get the checkout ready

```bash
cd /opt/kibana_dashboard_runtime_monitor && source .venv/bin/activate && git pull origin main
```

### Trace step 2. Trace CyHy Overview

Each repeat takes about a minute (warm-up, then a 30 second window). Three repeats show
whether the result is the same every time.

```bash
python scripts/trace_dashboard.py --dashboard-title "CyHy Overview" --repeat 3
```

### Trace step 3. Two more, for comparison

Priority Risks is the dashboard whose result flipped between two runs (15 empty panels, then
none). AIM Score Overview reported every panel ok, so it checks that the script reports
"ALL OK" when nothing is wrong.

```bash
python scripts/trace_dashboard.py --dashboard-title "Priority Risks" --repeat 3
python scripts/trace_dashboard.py --dashboard-title "AIM Score Overview"
```

### Trace step 4. How to read the output

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

### Trace step 5. Send the result

Paste the output into the chat. Do not commit it. This repository is public.
