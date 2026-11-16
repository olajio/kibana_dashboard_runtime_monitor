# Dashboard Health Monitor: alerting epic and tasks

Content to create in Jira. Task ids here (ALR-1 to ALR-10) are local labels; replace them with the
keys Jira assigns. **Nothing in this file has been created in Kibana.** It replaces the older
alerting items in `dashboard_health_monitor_jira_tasks.md` (DHM-G and DHM-15 to DHM-18).

## What was cut, and why

An alert is worth having only if a person would act on it and nothing else already tells them.

| Dropped | Why |
|---|---|
| Alert on `load_status` degraded or failed | The 30 s and 120 s thresholds are global. Software Overview, Priority Risks and Agency Details would alert every run for load times that are normal for them. Replaced by ALR-5, per dashboard, after the baseline. |
| Separate rules for error, timeout and missing panels, and for a dashboard that will not load | All of them are already `panels_not_ok > 0`. One rule (ALR-4) covers them; the message shows the breakdown. |
| "Dashboard stopped being collected" as its own rule | Folded into ALR-2, which counts how many dashboards produced a result. |
| Hard load-time ceiling | Covered by the per-dashboard thresholds in ALR-5. |
| Slow single-panel regression | Informational. The dashboard already shows it. |

---

## Epic

**Summary:** DHM: Alerting for the Dashboard Health Monitor

**Type:** Epic · **Priority:** High · **Labels:** `dhm`, `alerting`, `kibana`

**Background**

The collector opens the 22 Federal Overview dashboards every 30 minutes and writes one result per
dashboard to the `dashboard-health-monitor` data stream (load time, status, panels ok and not ok,
per-panel records). The "Dashboard Health Monitor" Kibana dashboard shows it. Nobody is notified
yet: someone has to open the dashboard to find out that a dashboard is broken, or that the
collector has stopped.

**Goal**

Notify the right people about the few conditions that need a human, quickly and without noise.

**Design rules**

- Alert only on conditions a person would act on. Everything else stays on the dashboard.
- Alert on persistence, not one result: a run is one browser session, and one-off errors happen.
- One notification per rule run, summarising every dashboard affected (Kibana "Summary of alerts"),
  with recovery notices and a reminder interval, so a platform-wide outage is one message.
- Do not alert on `load_status`: it depends on global thresholds that do not fit every dashboard.
- Planned work (such as the agency upgrade window) is silenced with a Kibana maintenance window.
- Every alert message says which dashboard, what was seen, and links to the Dashboard Health
  Monitor dashboard and the runbook.

**Scope**

In: four must-have alerts (ALR-2 to ALR-5), the shared notification setup (ALR-1), and a short
list of good-to-have alerts (ALR-6 to ALR-10) to pick up later.

Out: alerting on the content of the Federal Overview data itself, and changes to the collector's
status thresholds.

**Definition of done**

- The four must-have alerts are enabled in the production space and each has been proven to fire
  and to recover (see each task).
- On the day they are enabled, none fires falsely (checked against the previous 14 days of data).
- The runbook lists, per alert, what it means and the first three things to check.

**Dependencies and risks**

- ALR-5 needs about two weeks of results (the schedule started around 10 November; run
  `scripts/baseline.py --days 14`). The other three do not.
- Creating rules needs Kibana alerting write access. The collector's key deliberately lacks it, so
  an administrator account creates them.
- The Kibana alerts live in the cluster they watch. If Elasticsearch or Kibana is down, none of
  them fires. ALR-3 covers that.
- ES|QL rules (ALR-2) must be available in the production Kibana; a fallback is given.

**Order:** ALR-1, then ALR-2 and ALR-4 in parallel, then ALR-3, then ALR-5 once the baseline exists.

---

## Must-have

### ALR-1: Notification setup: connector, routes, maintenance window
- **Type:** Task · **Priority:** High · **Size:** M · **Blocks:** ALR-2 to ALR-5
- **Description:** Everything the alerts share. Decide who gets what, create the connector(s), and
  prepare the way to silence planned work.
- **Work:**
  - Choose the connector(s): email, Teams, ServiceNow or PagerDuty.
  - Two routes: **page** (someone is woken or interrupted) and **ticket** (work-hours follow-up).
  - Rule actions use "Summary of alerts", run on a status change, with a reminder every 6 hours
    and a recovery message.
  - Create a Kibana maintenance window for planned work (the agency upgrade) and document how to
    start one.
  - Confirm which account creates rules (an administrator, not the collector key).
  - Write the runbook skeleton: one section per alert.
- **Acceptance criteria:**
  - A test message reaches each route.
  - A maintenance window suppresses a test alert, and the alert resumes after it ends.
  - Runbook location agreed and linked from every alert message.

### ALR-2: Collector stopped, or a run came back incomplete
- **Type:** Task · **Priority:** Highest · **Size:** S · **Blocked by:** ALR-1
- **Description:** The monitor's own heartbeat. Fires when the collector has not produced results
  for all expected dashboards recently: it has stopped, crashed partway, or a dashboard dropped out.
- **Rule design:**
  - Rule type: ES|QL (Elasticsearch query rule), check every 15 minutes.
  - Query (22 is the number of monitored dashboards):
    ```
    FROM dashboard-health-monitor
    | WHERE @timestamp > NOW() - 65 minutes
    | STATS dashboards = COUNT_DISTINCT(dashboard_title)
    | WHERE dashboards < 22
    ```
  - Fires when the query returns a row. With no results at all, `dashboards` is 0, so a stopped
    collector fires too.
  - 65 minutes is two schedule periods (30) plus a run (about 7) plus slack. A dashboard missing
    from one run but present in the one before does not fire.
  - Route: **page**. Message shows how many of 22 were seen and the time of the last result.
  - Fallback if the ES|QL rule type is unavailable: an Elasticsearch query rule, count under 1
    over 65 minutes (detects a stopped collector, not a partial run).
- **Acceptance criteria:**
  - With the cron entry disabled, it fires within 80 minutes and recovers after the next run.
  - Changing 22 to 23 in a copy of the rule fires it (proves the partial-run path).
  - The expected count (22) is in the runbook as the value to change when dashboards are added.
  - `es/alerting/dead_mans_switch_rule.json` replaced to match.

### ALR-3: Out-of-band heartbeat (AWS CloudWatch)
- **Type:** Task · **Priority:** High · **Size:** M · **Blocked by:** ALR-1
- **Description:** ALR-2 runs inside the Elasticsearch and Kibana it watches. If either is down,
  nothing fires. This alarm lives in AWS and only needs the collector host.
- **Design:**
  - After a successful run, the cron job publishes a metric (for example `DHM/RunSucceeded = 1`
    with `cloudwatch put-metric-data`), using the exit status of `run_collector.py`.
  - CloudWatch alarm: fewer than 1 in 90 minutes, **missing data treated as breaching**, action to
    an SNS topic delivered on the **page** route.
  - Needs `cloudwatch:PutMetricData` on the host's instance role (prd account), and the change in
    `deploy/dhm-deploy.yml`.
- **Acceptance criteria:**
  - With the cron entry disabled, the alarm goes off within 100 minutes, with Kibana unreachable.
  - A failed run (exit status not zero) does not publish the metric.
  - It recovers on the next successful run.

### ALR-4: A dashboard has panels that are not ok (persisting)
- **Type:** Task · **Priority:** High · **Size:** S · **Blocked by:** ALR-1
- **Description:** The monitor's reason for existing: a dashboard shows empty, errored, timed-out
  or missing panels. Fires per dashboard, only when it persists.
- **Rule design:**
  - Rule type: Index threshold (or an Elasticsearch query rule with group-by, whichever groups by
    `dashboard_title` in the production Kibana). Index `dashboard-health-monitor*`, time field
    `@timestamp`, check every 15 minutes.
  - Filter (KQL): `panels_not_ok > 0`. Group by `dashboard_title` (top 30). Count at or above 3
    in the last 100 minutes, which is about three runs in a row.
  - Route: **ticket**. Summary of alerts, one message per rule run. The message lists each
    dashboard with `panels_not_ok` and the counts of empty, error, timeout and missing.
  - Silence with a maintenance window during the agency upgrades.
  - Does not use `load_status`.
- **Acceptance criteria:**
  - Query run over the previous 14 days returns no alert (today every panel is ok).
  - Delivery proven by temporarily changing the filter to `panels_ok > 0` with the same threshold,
    then restoring it.
  - Recovery notice arrives when the dashboard returns to ok.
  - `es/alerting/panel_health_rule.json` replaced; `es/alerting/load_time_rule.json` removed.

### ALR-5: A dashboard is much slower than its own normal
- **Type:** Task · **Priority:** High · **Size:** M · **Blocked by:** ALR-1 and two weeks of data
- **Description:** A load-time regression per dashboard, judged against that dashboard's own
  baseline instead of the global 30 s and 120 s.
- **Rule design:**
  - Run `scripts/baseline.py --days 14`. The threshold for each dashboard is 2 times its p95,
    rounded up (the script prints 1.5 times as `degraded>`; use the multiplier agreed after
    reviewing the numbers).
  - One rule, same type as ALR-4. The KQL filter holds one clause per dashboard:
    `(dashboard_title : "A" and load_time_ms > 12000) or (dashboard_title : "B" and load_time_ms > 190000) or ...`
  - Group by `dashboard_title`, count at or above 3 in the last 100 minutes. Route: **ticket**.
  - Add a `--kql` option to `scripts/baseline.py` to print that filter, so the rule can be
    regenerated when the baseline is reviewed.
- **Acceptance criteria:**
  - Backtest: the filter run over the 14 baseline days gives no more than one alert a week in total.
  - Delivery proven by temporarily lowering one dashboard's threshold below its normal.
  - The baseline numbers and the review date are recorded in the task.

---

## Good to have (less important, pick up later)

Each is optional. Do not start these before the must-haves are enabled and quiet.

### ALR-6: Dashboard in Action (half or more panels not ok)
- **Type:** Task · **Priority:** Low · **Size:** S · **Importance:** Good to have
- **Description:** A louder version of ALR-4 for a dashboard that is largely broken. Filter
  `panels_not_ok >= panel_count / 2` (computed in a runtime field, or ES|QL), 2 results in 70
  minutes, route **page**. Build it only if ALR-4 turns out to be too slow for real outages.
- **Acceptance criteria:** fires on a test condition, recovers, and does not fire on the previous 14 days.

### ALR-7: Platform-wide failure
- **Type:** Task · **Priority:** Low · **Size:** S · **Importance:** Good to have
- **Description:** Five or more dashboards with not-ok panels in the same run points at the cluster,
  a remote cluster or Kibana rather than a dashboard. One alert instead of many. ES|QL: count
  dashboards with `panels_not_ok > 0` in the latest run, at least 5. Route **page**.
- **Acceptance criteria:** fires when the threshold is lowered to 1 temporarily; the message says to check the platform first.

### ALR-8: Collector run too long
- **Type:** Task · **Priority:** Low · **Size:** S · **Importance:** Good to have
- **Description:** A run taking over 25 minutes is close to the 30 minute schedule; the next run
  is skipped by the lock. ES|QL: for each `collector_run_id`, latest minus earliest `@timestamp`
  over 25 minutes. Route **ticket**.
- **Acceptance criteria:** fires with the threshold lowered to 5 minutes; stays quiet at 25.

### ALR-9: Every dashboard is slow
- **Type:** Task · **Priority:** Low · **Size:** S · **Importance:** Good to have · **Blocked by:** baseline
- **Description:** The median load time across all dashboards is more than twice its baseline for
  three runs. Points at Kibana or the cluster. Route **ticket**.
- **Acceptance criteria:** backtest over the baseline window gives no alerts.

### ALR-10: Weekly digest
- **Type:** Task · **Priority:** Low · **Size:** S · **Importance:** Good to have
- **Description:** A scheduled weekly report (not an alert): share of ok results per dashboard,
  slowest movers against baseline, dashboards that had not-ok panels. Uses the Dashboard Health
  Monitor dashboard as a report.
- **Acceptance criteria:** arrives on the first Monday after it is created; readable without opening Kibana.
