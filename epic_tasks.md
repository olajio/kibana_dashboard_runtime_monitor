# Dashboard Health Monitor: alerting epic and tasks

For Jira: the epic and the must-have tasks (Tasks 1 to 6), each with the three required fields
(Summary, Description, Acceptance Criteria). The good-to-have tasks (Tasks 7 to 11) are added below
once written. Tasks are numbered in the order of work; Jira assigns its own keys. **Nothing here
has been created in Kibana.**

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
Set up everything the alerts share, so that when Tasks 2, 3, 4 and 6 are built, a person actually
receives the message, on the right channel, and planned work does not cause noise. Tasks 2 to 6
depend on this task.

Decisions needed, and recorded in the runbook:
- **Connector.** Which tool delivers the alerts: email, Teams, ServiceNow or PagerDuty. One or two
  connectors are enough.
- **Two routes.** **Page**: someone is interrupted at any hour (Tasks 2 and 4). **Ticket**: a
  work-hours follow-up (Tasks 3 and 6). Name the recipients or distribution list for each route.
- **Notification behaviour for every DHM rule.** Use "Summary of alerts" (one message per rule run
  listing every dashboard affected, so a platform-wide outage is one message, not twenty-two), send
  on a status change, send a recovery message, and remind every 6 hours while it stays active.
- **Message content.** Every message names the dashboard, says what was seen, and links to the
  Dashboard Health Monitor dashboard and the runbook entry.

Work:
- Create the connector(s) and the two routes.
- Agree a naming convention for rules (`DHM - <what it detects>`) and tag every rule `dhm`.
- Create a Kibana maintenance window for planned work, such as the agency upgrade period when empty
  panels are expected, scoped to the DHM rules where the Kibana version allows it. Document how to
  start one and who may.
- Confirm which account creates rules. It must be an administrator account: the collector's API key
  deliberately has no alerting privileges.
- Write the runbook skeleton, one section per alert: what it means, the first three things to check,
  who owns it.

Dependencies and risks:
- Needs a person who can create connectors and rules in the production space.
- If the chosen connector needs a service account or an API token from another team, request it
  early; it is the likeliest delay.

### Acceptance Criteria
- A test message is delivered on the page route and on the ticket route, to the agreed recipients.
- A maintenance window suppresses a test alert, and the alert resumes after the window ends.
- The naming convention and the `dhm` tag are documented.
- The runbook location is agreed and linked from every alert message.
- The runbook has a section for each of Tasks 2, 3, 4 and 6 (they can be filled in as each is built).

## Task 2 - Collector stopped or run incomplete

### Summary
Task 2 - Collector stopped or run incomplete

### Description
The monitor's own heartbeat, and the highest-priority alert. Every other alert assumes the collector
is writing results. This one fires when it has not produced results for all expected dashboards
recently: the collector has stopped, a run crashed partway, or a dashboard dropped out of the run.
Depends on Task 1.

Rule design:
- **Rule type:** ES|QL (Elasticsearch query rule), checked every 15 minutes. **Route:** page.
- **Query** (22 is the number of monitored dashboards):
  ```
  FROM dashboard-health-monitor
  | WHERE @timestamp > NOW() - 65 minutes
  | STATS dashboards = COUNT_DISTINCT(dashboard_title)
  | WHERE dashboards < 22
  ```
- The rule fires when the query returns a row. With no results at all, `dashboards` is 0, so a
  stopped collector fires too. There is no separate "stopped" rule.
- **Why 65 minutes:** two schedule periods (30 minutes each) plus a run (about 7 minutes) plus slack.
  A dashboard missing from one run but present in the previous one does not fire, so a single failed
  load never pages anyone.
- **Message:** how many of 22 dashboards were seen, which are missing if the query can list them, and
  the time of the last result.
- **Fallback** if the ES|QL rule type is not available in the production Kibana: an Elasticsearch
  query rule with a count under 1 over 65 minutes. That detects a stopped collector but not a partial
  run.

Known behaviour to document:
- The number 22 is the expected dashboard count. If a monitored dashboard is added or removed, the
  rule fires until 22 is updated. Put the value to change in the runbook.
- This alert runs in the same Elasticsearch and Kibana it watches. If either is down it cannot fire;
  Task 4 covers that case.

### Acceptance Criteria
- With the collector's cron entry disabled, the alert fires within 80 minutes and recovers after the
  next successful run.
- Changing 22 to 23 in a copy of the rule makes it fire, which proves the partial-run path.
- The alert message shows the number of dashboards seen and the time of the last result.
- The runbook entry says what to check first: the cron entry for the `dhm` account, the tail of
  `collector.log`, and the output of `scripts/check_access.py`.
- The expected count (22) and where to change it are in the runbook.
- `es/alerting/dead_mans_switch_rule.json` is replaced to match the rule.

## Task 3 - Panels not ok on a dashboard (persisting)

### Summary
Task 3 - Panels not ok on a dashboard (persisting)

### Description
This is the reason the monitor exists: a monitored dashboard shows empty, errored, timed-out or
missing panels. The alert fires per dashboard, and only when the problem persists, because one run is
one browser session and one-off errors happen. Depends on Task 1.

One rule covers every kind of unhealthy panel. `panels_not_ok` is the count of empty, error, timeout
and missing panels together, and a dashboard that cannot load at all shows up as missing panels. The
message breaks the count down so the responder sees which kind it is.

Rule design:
- **Rule type:** Index threshold, or an Elasticsearch query rule with group-by, whichever groups by
  `dashboard_title` in the production Kibana. **Index:** `dashboard-health-monitor*`. **Time field:**
  `@timestamp`. Checked every 15 minutes.
- **Filter (KQL):** `panels_not_ok > 0`. **Group by:** `dashboard_title` (top 30).
- **Condition:** a count of 3 or more matching results in the last 100 minutes, which is about three
  runs in a row.
- **Route:** ticket. One summary message per rule run listing each affected dashboard with
  `panels_not_ok` and the counts of empty, error, timeout and missing panels.
- The rule does not look at load time. Slow dashboards are Task 6.

Known behaviour to document:
- During the agency upgrade period, empty panels are expected. Silence the rule with the maintenance
  window from Task 1.
- Today every panel on every dashboard is ok, so the rule should be quiet when enabled.

### Acceptance Criteria
- The rule's filter, run over the previous 14 days, raises no alert.
- Delivery is proven by temporarily changing the filter to `panels_ok > 0` with the same threshold,
  then restoring the real filter.
- A recovery notice arrives when a dashboard returns to ok.
- The message lists the affected dashboards with the counts of empty, error, timeout and missing panels.
- The maintenance window from Task 1 silences the rule during planned work.
- `es/alerting/panel_health_rule.json` is replaced to match the rule, and
  `es/alerting/load_time_rule.json` is removed.

## Task 4 - Out-of-band heartbeat in AWS CloudWatch

### Summary
Task 4 - Out-of-band heartbeat in AWS CloudWatch

### Description
The Task 2 alert runs inside the Elasticsearch and Kibana it watches. If either one is down, nothing
fires, and nobody learns that monitoring has stopped. This alarm lives in AWS and depends only on the
collector host, so it still fires when the cluster does not. Depends on Task 1.

Design:
- **Heartbeat.** After a successful run, the cron job publishes a metric, for example
  `DHM/RunSucceeded = 1`, using the exit status of `run_collector.py`. A failed run publishes nothing.
  Publish with the AWS CLI if it is installed on the host, otherwise with a small script that uses
  the Python `boto3` package already in the virtualenv.
- **Alarm.** Evaluate the sum of the metric over 30-minute periods, 3 periods in a row. Fire when it is
  under 1. Treat missing data as breaching, so a host that stops reporting at all also fires. The
  alarm sends to an SNS topic delivered on the **page** route.
- **Permissions.** `cloudwatch:PutMetricData` on the collector host's instance role in the prd
  account, limited to the `DHM` namespace.
- **Deployment.** The change to the cron job goes in `deploy/dhm-deploy.yml`, so the playbook installs
  it.

Dependencies and risks:
- Needs an IAM change in the prd account and a decision on how the SNS topic reaches the page route.
- The metric is published only when the run succeeds, so a run that writes nothing does not count as
  alive.

### Acceptance Criteria
- With the cron entry disabled, the alarm goes off within 100 minutes, and the notification reaches
  the page route.
- The alarm and its notification do not depend on Kibana or Elasticsearch: confirmed by disabling the
  Task 2 rule during the test.
- A failed run (non-zero exit status) does not publish the metric.
- The alarm recovers on the next successful run.
- The instance role change and the playbook change are reviewed and deployed to the prd host.
- The runbook entry says what to check first.

## Task 5 - Per-dashboard load-time thresholds in the collector

### Summary
Task 5 - Per-dashboard load-time thresholds in the collector

### Description
Today `degraded_over_ms` (30 seconds) and `failed_over_ms` (120 seconds) are global, so `load_status`
cannot tell a dashboard that is normally slow from one that has regressed: Software Overview,
Priority Risks and Agency Details read degraded or failed every run. Give each dashboard its own
thresholds, so `load_status` means the same thing everywhere: on the dashboard, in queries and in
alerts. Task 6 depends on this. The threshold values come from the two-week baseline
(`scripts/baseline.py`), so the code ships first and the values are added afterwards.

Work:
- **Collector.** A `collector.thresholds` map in `settings.yaml`, keyed by dashboard title, each entry
  with `degraded_over_ms` and `failed_over_ms`. Dashboards not in the map keep the global values.
  Reject `failed` below `degraded`, and warn when `failed` is at or above the per-dashboard timeout
  (`dashboard_timeout_ms`, 180 seconds).
- **Playbook.** A variable and the template line in `deploy/settings.prod.yaml.j2`.
- **History.** Record the thresholds applied in every result (`degraded_over_ms`, `failed_over_ms`),
  so old results stay readable when thresholds are retuned. Add the two fields to
  `es/index_template.json`, bump `schema_version`, apply the template with
  `scripts/setup_elasticsearch.py`, and roll the data stream over so the new fields are indexed. The
  rollover needs an administrator, because the collector's key cannot do it.
- **Baseline output.** `scripts/baseline.py --yaml` prints the override block from the baseline
  numbers, using the multiplier agreed on review. Thresholds are rounded up to 5-second steps, which
  gives fast dashboards a 5-second floor so a cold start does not alert.
- **Software Overview.** It takes 92 to 169 seconds against the 180-second timeout, so a multiple of
  its p95 can land above the timeout and never fire. Decide whether to raise its timeout, optimise the
  dashboard, or both, and record the decision.
- **Dashboard.** Update the reference panel on the Dashboard Health Monitor dashboard, and decide
  whether "Overall health" should include the Slow state now that it is meaningful.
- **Review.** Re-run the baseline every month or quarter; put the date in the runbook.

Rollout: ship the code with an empty map (statuses unchanged), then add the values after the
two-week baseline.

### Acceptance Criteria
- Shipped first with an empty map: every status is identical to what it would have been before.
- A test override changes one dashboard's status, and the applied thresholds appear in its result
  document.
- Validation works: `failed` below `degraded` is rejected, and a `failed` at or above the timeout warns.
- The index template has the two new fields, the data stream has been rolled over, and new results show
  them as searchable fields.
- After the two-week baseline, the block from `baseline.py --yaml` is loaded by the collector and
  committed together with the baseline numbers and the review date.
- The decision on Software Overview's timeout is recorded.
- The reference panel on the dashboard describes the per-dashboard thresholds.
- Tests cover the map, the fallback to the global values and the validation.

## Task 6 - Dashboard load time above its own normal (persisting)

### Summary
Task 6 - Dashboard load time above its own normal (persisting)

### Description
Alert when a dashboard has become slower than its own normal, and stays that way. Once Task 5 has put
per-dashboard thresholds in the collector, `load_status` already says whether a result was slower than
that dashboard's threshold, so the rule is simple. Depends on Task 5 and on two weeks of data. Do not
enable it with the global thresholds: three dashboards would alert on every run.

Rule design:
- **Rule type:** the same as Task 3.
- **Filter (KQL):** `load_status : (degraded or failed) and panels_not_ok : 0`. The second part keeps
  the rule to timing only, so a broken dashboard alerts once (Task 3), not twice.
- **Group by:** `dashboard_title`. **Condition:** a count of 3 or more in the last 100 minutes.
  **Route:** ticket.
- **Message:** the load time, the thresholds applied (taken from the result), and the dashboard's
  median load time.

Known behaviour to document:
- After the thresholds are retuned, review the rule: a tighter threshold alerts sooner.
- Results from before Task 5 do not carry the thresholds, which is why the rule uses `load_status`
  as recorded at the time.

### Acceptance Criteria
- Backtest: run over the 14 baseline days with the final thresholds, the rule raises no more than one
  alert a week in total.
- Delivery is proven by temporarily lowering one dashboard's threshold below its normal load time.
- A recovery notice arrives when the load time is back under the threshold.
- The message shows the load time, the thresholds and the median.
- The runbook entry says what to check first: Kibana and cluster load, the Collector run duration
  panel, and the dashboard's own queries.
