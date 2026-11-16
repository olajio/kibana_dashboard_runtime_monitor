# Dashboard Health Monitor: alerting epic and tasks

Content to create in Jira: one epic and eleven tasks. Task numbers below are the order of work;
Jira will assign its own keys. **Nothing in this file has been created in Kibana.** It replaces the
older alerting items in `dashboard_health_monitor_jira_tasks.md` (DHM-G and DHM-15 to DHM-18).

## What was cut, and why

An alert is worth having only if a person would act on it and nothing else already tells them.

| Dropped | Why |
|---|---|
| Alert on `load_status` with the global 30 s and 120 s thresholds | Software Overview, Priority Risks and Agency Details would alert every run for load times that are normal for them. Instead, Task 5 makes the thresholds per dashboard, and Task 6 then alerts on `load_status`. |
| Separate rules for error, timeout and missing panels, and for a dashboard that will not load | All of them are already `panels_not_ok > 0`. One rule (Task 3) covers them; the message shows the breakdown. |
| "Dashboard stopped being collected" as its own rule | Folded into Task 2, which counts how many dashboards produced a result. |
| Hard load-time ceiling | Covered by the per-dashboard thresholds in Task 5. |
| Slow single-panel alert | Informational. The dashboard already shows it. |

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
- Load-time alerts use `load_status` only once each dashboard has its own thresholds (Task 5), and
  only for timing (all panels ok), so they never duplicate the panel alert (Task 3).
- Planned work (such as the agency upgrade window) is silenced with a Kibana maintenance window.
- Every alert message says which dashboard, what was seen, and links to the Dashboard Health
  Monitor dashboard and the runbook.

**Scope**

In: six must-have tasks (Tasks 1 to 6) and five good-to-have tasks (Tasks 7 to 11) to pick up later.

Out: alerting on the content of the Federal Overview data itself.

**Definition of done**

- The must-have alerts (Tasks 2, 3, 4 and 6) are enabled in the production space, and each has been
  proven to fire and to recover (see each task).
- On the day each is enabled, it does not fire falsely (checked against the previous 14 days).
- The runbook lists, per alert, what it means and the first three things to check.

**Dependencies and risks**

- Task 6 needs two weeks of results (the schedule started around 10 November) and Task 5.
- Creating rules needs Kibana alerting write access. The collector's key deliberately lacks it, so
  an administrator account creates them.
- The Kibana alerts live in the cluster they watch. If Elasticsearch or Kibana is down, none of
  them fires. Task 4 covers that.
- ES|QL rules (Task 2) must be available in the production Kibana; a fallback is given.
- Software Overview takes 92 to 169 s against a 180 s per-dashboard timeout (seen in the first
  runs). Its threshold has to be set with that limit in mind (Task 5).

**Order:** Task 1, then Tasks 2 and 3 in parallel, then Task 4, then (after the baseline) Task 5
and Task 6. Tasks 7 to 11 only after the must-haves are enabled and quiet.

---

## Must-have

### Task 1 - Notification setup: connector, routes and maintenance window
- **Type:** Task · **Priority:** High · **Size:** M · **Blocks:** Tasks 2 to 6
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

### Task 2 - Collector stopped or run incomplete
- **Type:** Task · **Priority:** Highest · **Size:** S · **Blocked by:** Task 1
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

### Task 3 - Panels not ok on a dashboard (persisting)
- **Type:** Task · **Priority:** High · **Size:** S · **Blocked by:** Task 1
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
  - Does not look at load time.
- **Acceptance criteria:**
  - Query run over the previous 14 days returns no alert (today every panel is ok).
  - Delivery proven by temporarily changing the filter to `panels_ok > 0` with the same threshold,
    then restoring it.
  - Recovery notice arrives when the dashboard returns to ok.
  - `es/alerting/panel_health_rule.json` replaced; `es/alerting/load_time_rule.json` removed.

### Task 4 - Out-of-band heartbeat in AWS CloudWatch
- **Type:** Task · **Priority:** High · **Size:** M · **Blocked by:** Task 1
- **Description:** Task 2 runs inside the Elasticsearch and Kibana it watches. If either is down,
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

### Task 5 - Per-dashboard load-time thresholds in the collector
- **Type:** Task · **Priority:** High · **Size:** M · **Blocks:** Task 6
- **Description:** Today `degraded_over_ms` (30 s) and `failed_over_ms` (120 s) are global, so
  `load_status` cannot tell a normal slow dashboard from a regression. Let each dashboard have its
  own thresholds, so `load_status` means the same thing everywhere: on the dashboard, in queries
  and in alerts.
- **Work:**
  - Collector: a `collector.thresholds` map in `settings.yaml`, keyed by dashboard title, each with
    `degraded_over_ms` and `failed_over_ms`. Dashboards not in the map keep the global values.
    Reject `failed` below `degraded`, and warn when `failed` is at or above the per-dashboard
    timeout (`dashboard_timeout_ms`, 180 s).
  - Playbook: a variable and the template line in `deploy/settings.prod.yaml.j2`.
  - Record the thresholds applied in every result (`degraded_over_ms`, `failed_over_ms`) so history
    stays readable when they are retuned later. Update `es/index_template.json`, bump
    `schema_version`, and roll the data stream over so the new fields are indexed.
  - `scripts/baseline.py --yaml`: print the override block from the baseline numbers, using the
    multiplier agreed on review.
  - Software Overview (92 to 169 s against the 180 s timeout): decide whether to raise its
    timeout, optimise the dashboard, or both, and record the decision.
  - Update the Dashboard Health Monitor dashboard's reference panel, and decide whether "Overall
    health" should include Slow now that it is meaningful.
  - Review the thresholds every month or quarter; put the date in the runbook.
- **Acceptance criteria:**
  - Shipped first with an empty map: statuses are identical to today's.
  - A test override changes one dashboard's status and shows in its result document.
  - After the two-week baseline, the block from `baseline.py --yaml` is loaded by the collector
    and committed with the baseline numbers and review date.
  - Tests cover the map, the fallback and the validation.

### Task 6 - Dashboard load time above its own normal (persisting)
- **Type:** Task · **Priority:** High · **Size:** S · **Blocked by:** Task 5 and two weeks of data
- **Description:** A load-time regression per dashboard, judged against that dashboard's own
  thresholds, which the collector now applies in `load_status`.
- **Rule design:**
  - Same rule type as Task 3. Filter (KQL):
    `load_status : (degraded or failed) and panels_not_ok : 0`. The second part keeps it to
    timing only, so a broken dashboard alerts once (Task 3), not twice.
  - Group by `dashboard_title`. Count at or above 3 in the last 100 minutes. Route: **ticket**.
  - Message shows the load time, the thresholds applied and the dashboard's median.
- **Acceptance criteria:**
  - Backtest: run over the 14 baseline days with the final thresholds; no more than one alert a
    week in total.
  - Delivery proven by temporarily lowering one dashboard's threshold below its normal.
  - Recovery notice arrives when the load time is back under the threshold.

---

## Good to have (less important, pick up later)

Each is optional. Do not start these before the must-haves are enabled and quiet.

### Task 7 - Dashboard in Action: half or more panels not ok
- **Type:** Task · **Priority:** Low · **Size:** S · **Importance:** Good to have
- **Description:** A louder version of Task 3 for a dashboard that is largely broken. Filter
  `panels_not_ok >= panel_count / 2` (computed in a runtime field, or ES|QL), 2 results in 70
  minutes, route **page**. Build it only if Task 3 turns out to be too slow for real outages.
- **Acceptance criteria:** fires on a test condition, recovers, and does not fire on the previous 14 days.

### Task 8 - Platform-wide failure: five or more dashboards affected
- **Type:** Task · **Priority:** Low · **Size:** S · **Importance:** Good to have
- **Description:** Five or more dashboards with not-ok panels in the same run points at the cluster,
  a remote cluster or Kibana rather than a dashboard. One alert instead of many. ES|QL: count
  dashboards with `panels_not_ok > 0` in the latest run, at least 5. Route **page**.
- **Acceptance criteria:** fires when the threshold is lowered to 1 temporarily; the message says to check the platform first.

### Task 9 - Collector run too long
- **Type:** Task · **Priority:** Low · **Size:** S · **Importance:** Good to have
- **Description:** A run taking over 25 minutes is close to the 30 minute schedule; the next run
  is skipped by the lock. ES|QL: for each `collector_run_id`, latest minus earliest `@timestamp`
  over 25 minutes. Route **ticket**.
- **Acceptance criteria:** fires with the threshold lowered to 5 minutes; stays quiet at 25.

### Task 10 - Every dashboard slow against its baseline
- **Type:** Task · **Priority:** Low · **Size:** S · **Importance:** Good to have · **Blocked by:** Task 5
- **Description:** The median load time across all dashboards is more than twice its baseline for
  three runs. Points at Kibana or the cluster. Route **ticket**.
- **Acceptance criteria:** backtest over the baseline window gives no alerts.

### Task 11 - Weekly digest report
- **Type:** Task · **Priority:** Low · **Size:** S · **Importance:** Good to have
- **Description:** A scheduled weekly report (not an alert): share of ok results per dashboard,
  slowest movers against baseline, dashboards that had not-ok panels. Uses the Dashboard Health
  Monitor dashboard as a report.
- **Acceptance criteria:** arrives on the first Monday after it is created; readable without opening Kibana.
