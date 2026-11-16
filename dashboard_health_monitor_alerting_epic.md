# Dashboard Health Monitor: alerting epic and tasks

For Jira: one epic and eleven tasks, each with the three required fields (Summary, Description,
Acceptance Criteria). Tasks are numbered in the order of work; Jira assigns its own keys.
**Nothing here has been created in Kibana.** This replaces the older alerting items in
`dashboard_health_monitor_jira_tasks.md` (DHM-G and DHM-15 to DHM-18).

---

# EPIC

## Summary
DHM: Alerting for the Dashboard Health Monitor

## Description
The Dashboard Health Monitor collector opens the 22 Federal Overview dashboards every 30 minutes and
writes one result per dashboard to the `dashboard-health-monitor` data stream: load time, status,
panels ok and not ok, and per-panel records. The "Dashboard Health Monitor" Kibana dashboard shows
the results, but nobody is notified. Someone has to open it to find out that a dashboard is broken
or that the collector has stopped.

This epic adds the few alerts that need a human, and no more. Goal: notify the right people about
those conditions quickly and without noise.

Design rules for every alert in the epic:
- Alert only on conditions a person would act on. Everything else stays on the dashboard.
- Alert on persistence, not on one result. A run is one browser session, and one-off errors happen.
- Send one notification per rule run that summarises every dashboard affected (Kibana "Summary of
  alerts"), with a recovery notice and a reminder every 6 hours.
- Load-time alerts use `load_status` only once each dashboard has its own thresholds (Task 5), and
  only for timing (all panels ok), so they never duplicate the panel alert (Task 3).
- Silence planned work, such as the agency upgrade window, with a Kibana maintenance window.
- Every message names the dashboard and links to the Dashboard Health Monitor dashboard and the runbook.

Scope: six must-have tasks (Tasks 1 to 6) and five good-to-have tasks (Tasks 7 to 11) to pick up
later. Out of scope: alerting on the content of the Federal Overview data itself.

Order of work: Task 1, then Tasks 2 and 3 in parallel, then Task 4, then (after the baseline)
Tasks 5 and 6. Tasks 7 to 11 only after the must-haves are enabled and quiet.

Dependencies and risks:
- Task 6 needs two weeks of results (the schedule started around 10 November) and Task 5.
- Creating rules needs Kibana alerting write access. The collector's key deliberately lacks it, so
  an administrator account creates them.
- The Kibana alerts run in the cluster they watch. If Elasticsearch or Kibana is down, none of them
  fires. Task 4 covers that.
- ES|QL rules (Task 2) must be available in the production Kibana; a fallback is given there.
- Software Overview takes 92 to 169 seconds against a 180 second per-dashboard timeout, so its
  threshold needs care (Task 5).

## Acceptance Criteria
- Tasks 1 to 6 are done.
- The alerts from Tasks 2, 3, 4 and 6 are enabled in the production space, and each has been proven
  to fire and to recover.
- On the day each alert is enabled, it does not fire falsely when run against the previous 14 days
  of data.
- The runbook has an entry for each enabled alert: what it means and the first three things to check.
- Messages for every alert are delivered on the agreed route (page or ticket).

---

# TASKS: MUST-HAVE

## Task 1 - Notification setup: connector, routes and maintenance window

### Summary
Task 1 - Notification setup: connector, routes and maintenance window

### Description
Everything the alerts share. Decide who gets notified of what, create the connector(s), and prepare
the way to silence planned work. Tasks 2 to 6 depend on this.

Work:
- Choose the connector(s): email, Teams, ServiceNow or PagerDuty.
- Define two routes: **page** (someone is interrupted) and **ticket** (work-hours follow-up).
- Rule actions use "Summary of alerts", run on a status change, with a reminder every 6 hours and a
  recovery message.
- Create a Kibana maintenance window for planned work (the agency upgrade) and document how to start one.
- Confirm which account creates rules: an administrator, not the collector's API key.
- Write the runbook skeleton, with one section per alert.

### Acceptance Criteria
- A test message is delivered on each of the two routes.
- A maintenance window suppresses a test alert, and the alert resumes after the window ends.
- The runbook location is agreed and linked from every alert message.

## Task 2 - Collector stopped or run incomplete

### Summary
Task 2 - Collector stopped or run incomplete

### Description
The monitor's own heartbeat. Fires when the collector has not produced results for all expected
dashboards recently: it has stopped, crashed partway, or a dashboard dropped out of the run.

Depends on Task 1. Priority: highest.

Rule design:
- Rule type: ES|QL (Elasticsearch query rule), checked every 15 minutes. Route: **page**.
- Query (22 is the number of monitored dashboards):
  ```
  FROM dashboard-health-monitor
  | WHERE @timestamp > NOW() - 65 minutes
  | STATS dashboards = COUNT_DISTINCT(dashboard_title)
  | WHERE dashboards < 22
  ```
- The rule fires when the query returns a row. With no results at all, `dashboards` is 0, so a
  stopped collector fires too.
- 65 minutes is two schedule periods (30 each) plus a run (about 7) plus slack. A dashboard missing
  from one run but present in the previous one does not fire.
- The message shows how many of 22 were seen and the time of the last result.
- Fallback if the ES|QL rule type is unavailable: an Elasticsearch query rule with a count under 1
  over 65 minutes. That detects a stopped collector but not a partial run.

### Acceptance Criteria
- With the cron entry disabled, the alert fires within 80 minutes and recovers after the next run.
- Changing 22 to 23 in a copy of the rule makes it fire, which proves the partial-run path.
- The expected count (22) is in the runbook as the value to change when dashboards are added.
- `es/alerting/dead_mans_switch_rule.json` is replaced to match the rule.

## Task 3 - Panels not ok on a dashboard (persisting)

### Summary
Task 3 - Panels not ok on a dashboard (persisting)

### Description
The monitor's reason for existing: a dashboard shows empty, errored, timed-out or missing panels.
The alert fires per dashboard, and only when the problem persists. Depends on Task 1.

Rule design:
- Rule type: Index threshold (or an Elasticsearch query rule with group-by, whichever groups by
  `dashboard_title` in the production Kibana). Index `dashboard-health-monitor*`, time field
  `@timestamp`, checked every 15 minutes.
- Filter (KQL): `panels_not_ok > 0`. Group by `dashboard_title` (top 30). Count of 3 or more in the
  last 100 minutes, which is about three runs in a row.
- Route: **ticket**. One summary message per rule run listing each dashboard with `panels_not_ok`
  and the counts of empty, error, timeout and missing panels.
- Silence with a maintenance window during the agency upgrades.
- The rule does not look at load time.

### Acceptance Criteria
- The rule run over the previous 14 days raises no alert (every panel is ok today).
- Delivery is proven by temporarily changing the filter to `panels_ok > 0` with the same threshold,
  then restoring it.
- A recovery notice arrives when a dashboard returns to ok.
- `es/alerting/panel_health_rule.json` is replaced to match, and `es/alerting/load_time_rule.json` is removed.

## Task 4 - Out-of-band heartbeat in AWS CloudWatch

### Summary
Task 4 - Out-of-band heartbeat in AWS CloudWatch

### Description
The Task 2 alert runs inside the Elasticsearch and Kibana it watches. If either is down, nothing
fires. This alarm lives in AWS and only needs the collector host. Depends on Task 1.

Design:
- After a successful run, the cron job publishes a metric (for example `DHM/RunSucceeded = 1` with
  `put-metric-data`), using the exit status of `run_collector.py`.
- CloudWatch alarm: fewer than 1 in 90 minutes, **missing data treated as breaching**, with the
  action sending to an SNS topic delivered on the **page** route.
- Needs `cloudwatch:PutMetricData` on the host's instance role (prd account) and a change in
  `deploy/dhm-deploy.yml`.

### Acceptance Criteria
- With the cron entry disabled, the alarm goes off within 100 minutes while Kibana is unreachable.
- A failed run (non-zero exit status) does not publish the metric.
- The alarm recovers on the next successful run.
- The instance role change and the playbook change are reviewed and deployed to the prd host.

## Task 5 - Per-dashboard load-time thresholds in the collector

### Summary
Task 5 - Per-dashboard load-time thresholds in the collector

### Description
Today `degraded_over_ms` (30 s) and `failed_over_ms` (120 s) are global, so `load_status` cannot
tell a normal slow dashboard from a regression. Give each dashboard its own thresholds so
`load_status` means the same thing everywhere: on the dashboard, in queries and in alerts. Task 6
depends on this. The threshold values come from the two-week baseline (`scripts/baseline.py`).

Work:
- Collector: a `collector.thresholds` map in `settings.yaml`, keyed by dashboard title, each with
  `degraded_over_ms` and `failed_over_ms`. Dashboards not in the map keep the global values. Reject
  `failed` below `degraded`, and warn when `failed` is at or above the per-dashboard timeout
  (`dashboard_timeout_ms`, 180 s).
- Playbook: a variable and the template line in `deploy/settings.prod.yaml.j2`.
- Record the thresholds applied in every result (`degraded_over_ms`, `failed_over_ms`), so history
  stays readable when they are retuned. Update `es/index_template.json`, bump `schema_version`,
  and roll the data stream over so the new fields are indexed.
- `scripts/baseline.py --yaml`: print the override block from the baseline numbers, using the
  multiplier agreed on review.
- Software Overview takes 92 to 169 s against the 180 s timeout. Decide whether to raise its
  timeout, optimise the dashboard, or both, and record the decision.
- Update the reference panel on the Dashboard Health Monitor dashboard, and decide whether "Overall
  health" should include Slow now that it is meaningful.
- Review the thresholds every month or quarter; put the date in the runbook.

### Acceptance Criteria
- Shipped first with an empty map: every status is identical to today's.
- A test override changes one dashboard's status, and the thresholds appear in its result document.
- Validation works: `failed` below `degraded` is rejected, and a `failed` at or above the timeout warns.
- After the two-week baseline, the block from `baseline.py --yaml` is loaded by the collector and
  committed together with the baseline numbers and the review date.
- The decision on Software Overview's timeout is recorded.
- Tests cover the map, the fallback to the global values and the validation.

## Task 6 - Dashboard load time above its own normal (persisting)

### Summary
Task 6 - Dashboard load time above its own normal (persisting)

### Description
A load-time regression per dashboard, judged against that dashboard's own thresholds, which the
collector now applies in `load_status`. Depends on Task 5 and on two weeks of data.

Rule design:
- Same rule type as Task 3. Filter (KQL):
  `load_status : (degraded or failed) and panels_not_ok : 0`. The second part keeps the rule to
  timing only, so a broken dashboard alerts once (Task 3), not twice.
- Group by `dashboard_title`. Count of 3 or more in the last 100 minutes. Route: **ticket**.
- The message shows the load time, the thresholds applied and the dashboard's median.

### Acceptance Criteria
- Backtest: run over the 14 baseline days with the final thresholds, the rule raises no more than
  one alert a week in total.
- Delivery is proven by temporarily lowering one dashboard's threshold below its normal load time.
- A recovery notice arrives when the load time is back under the threshold.

---

# TASKS: GOOD TO HAVE (less important, pick up later)

Do not start these before the must-have alerts are enabled and quiet.

## Task 7 - Dashboard in Action: half or more panels not ok

### Summary
Task 7 - Dashboard in Action: half or more panels not ok

### Description
A louder version of Task 3 for a dashboard that is largely broken. Filter `panels_not_ok >=
panel_count / 2` (computed in a runtime field, or in ES|QL), 2 results in 70 minutes, route
**page**. Build it only if Task 3 turns out to be too slow for real outages. Good to have.

### Acceptance Criteria
- The alert fires on a test condition and recovers.
- Run against the previous 14 days, it raises no alert.

## Task 8 - Platform-wide failure: five or more dashboards affected

### Summary
Task 8 - Platform-wide failure: five or more dashboards affected

### Description
Five or more dashboards with not-ok panels in the same run points at the cluster, a remote cluster or
Kibana rather than at a dashboard. One alert instead of many. ES|QL: count dashboards with
`panels_not_ok > 0` in the latest run, at least 5. Route **page**. The message tells the responder to
check the platform first. Good to have.

### Acceptance Criteria
- Temporarily lowering the threshold to 1 makes it fire.
- The message tells the responder to check the platform first.

## Task 9 - Collector run too long

### Summary
Task 9 - Collector run too long

### Description
A run taking more than 25 minutes is close to the 30 minute schedule, and the next run is skipped by
the lock. ES|QL: for each `collector_run_id`, the latest minus the earliest `@timestamp`, over 25
minutes. Route **ticket**. Good to have.

### Acceptance Criteria
- The alert fires with the threshold lowered to 5 minutes.
- It stays quiet at 25 minutes on the previous 14 days.

## Task 10 - Every dashboard slow against its baseline

### Summary
Task 10 - Every dashboard slow against its baseline

### Description
The median load time across all dashboards is more than twice its baseline for three runs. That
points at Kibana or the cluster rather than at one dashboard. Route **ticket**. Depends on Task 5.
Good to have.

### Acceptance Criteria
- A backtest over the baseline window raises no alert.
- It fires when the threshold is temporarily lowered.

## Task 11 - Weekly digest report

### Summary
Task 11 - Weekly digest report

### Description
A scheduled weekly report, not an alert: the share of ok results per dashboard, the slowest movers
against baseline, and the dashboards that had not-ok panels. Uses the Dashboard Health Monitor
dashboard as a report. Good to have.

### Acceptance Criteria
- The first report arrives on the first Monday after it is created.
- It is readable without opening Kibana.

---

## Notes (not for Jira)

What was cut from the earlier list, and why:
- Alert on `load_status` with the global 30 s and 120 s thresholds: replaced by Tasks 5 and 6.
- Separate rules for error, timeout and missing panels, and for a dashboard that will not load: all
  are `panels_not_ok > 0`, covered by Task 3.
- "Dashboard stopped being collected": folded into Task 2.
- Hard load-time ceiling: covered by the per-dashboard thresholds in Task 5.
- Slow single-panel alert: informational; the dashboard already shows it.
