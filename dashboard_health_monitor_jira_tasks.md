# Dashboard Health & Load-Time Monitor — Jira Breakdown

This file has two sections:

1. **[Current delivery status](#current-delivery-status)** — the concise 7-task
   view (DHM-A → DHM-G) that reflects what has actually been delivered in
   pre-staging and what remains to reach production. **Use this for planning
   the production rollout.**
2. **[Original planning breakdown](#original-planning-breakdown)** — the full
   pre-implementation task decomposition (DHM-1 → DHM-24) kept for historical
   reference.

Scope reminder: we monitor **one** application bundle — the Federal Overview
family of ~22 dashboards reachable from the hub's navigation — in **one**
cluster and space. There is no multi-cluster rollout.

Deployment shape: the code lives in GitHub and Ansible installs it on an existing
server — no dedicated host is provisioned. Every environment-specific value (both
endpoints and the API key) comes from one AWS Secrets Manager secret, region
`us-east-1` by default.

---

# Current delivery status

The seven tasks below capture what shipped through pre-staging validation and
what remains for production. Keep them all under a single **Epic** (Dashboard
Health & Load-Time Monitor). Blocking links: **DHM-F blocked by DHM-E; DHM-G
blocked by DHM-F.**

## ✅ Completed (mark as Done)

### DHM-A — Collector: implementation and validation
- **Type:** Story · **Size:** L · **Status:** Done
- **Description:** Built a Python collector that opens each dashboard in a
  headless browser, times per-dashboard and per-panel render, classifies health
  (`ok | degraded | failed`), and writes one nested document per dashboard per
  cycle. Includes:
  - Reachability-based scope — starts at the "Federal Overview" hub and follows
    its navigation transitively (22 dashboards in fed2).
  - Live Kibana Saved Objects API discovery (no `.ndjson` on server).
  - Playwright with support for Chrome (test) and Edge (prod) via
    `browser_channel`; Selenium fallback path.
  - Kibana 8.19-compatible panel detection (`aria-labelledby` title extraction
    + DOM-order positional fallback).
  - Threshold-based classification (`failed_not_ok_pct: 50%`, per-dashboard
    load-time bands).
  - Operational hardening: retry/backoff on 429/5xx, per-request timeout, bulk
    chunking, per-dashboard hard timeout, error isolation, inter-request pacing.
  - Secret resolution: CLI arg > env > AWS Secrets Manager.
- **Acceptance criteria:**
  - Collector produces valid docs for all 22 dashboards in one cycle.
  - 74 unit tests pass locally.
  - Codebase merged to `main`.

### DHM-B — Elasticsearch storage: data stream, template, ILM
- **Type:** Task · **Size:** M · **Status:** Done
- **Description:** Created the `dashboard-health-monitor` data stream (no dot
  prefix — visible in Kibana data-view picker), an index template with nested
  `panels` mapping, and an ILM policy for one year of history.
- **Acceptance criteria:**
  - Data stream `dashboard-health-monitor` exists in fed2.
  - ILM: rollover monthly (or 5 GB), delete after 365 days.
  - Index template contains all rollup + nested per-panel fields.

### DHM-C — Pre-staging deployment on macOS
- **Type:** Task · **Size:** S · **Status:** Done
- **Description:** Deployed the collector on a Mac laptop running against the
  fed2 pre-staging cluster; scheduled via cron; verified end-to-end data flow
  into `dashboard-health-monitor`.
- **Acceptance criteria:**
  - Cron writes new docs on every cycle.
  - Wrapper handles env, secrets, logs (`~/Library/Logs/dhm/`).
  - Docs queryable in Kibana Dev Tools.

### DHM-D — Kibana trend dashboard (initial)
- **Type:** Story · **Size:** M · **Status:** Done (initial pass)
- **Description:** Built the Kibana trend dashboard on
  `dashboard-health-monitor*`: three health tiles (ok / degraded / failed),
  latest-load-time table per dashboard, load-time-over-time line, latest-cycle
  bar chart.
- **Acceptance criteria:**
  - Dashboard reflects live data with 30-second auto-refresh.
  - Health tiles are color-coded (green/amber/red).
  - Load-time trend line shows per-dashboard series.

## 🔨 Remaining work

### DHM-E — Create the AWS Secrets Manager connection secret + grant access
- **Type:** Task · **Size:** S · **Status:** To Do
- **Description:** Create one Secrets Manager secret holding the production
  connection settings, and grant the deployment host read access to it. The
  collector reads the Kibana URL, the Elasticsearch URL and the API key from this
  one secret, so nothing environment-specific or sensitive lands on the server.
- **Steps:**
  - Create the secret (default region `us-east-1`), e.g.
    `federal_store`, with a JSON payload:
    `{"kibana_url": "https://<host>.kb.<domain>:9243", "es_url":
    "https://<host>.es.<domain>:9243", "api_key": "<base64 id:key>"}`.
    Add `kibana_api_key` only if Kibana needs a different key.
  - Mint the least-privilege Elastic API key: read on the monitored space, write
    to the `dashboard-health-monitor` data stream.
  - Attach an IAM policy granting the host's instance/task role
    `secretsmanager:GetSecretValue` on that secret's ARN only.
  - Agree and document a rotation cadence. Rotation is a secret update — no
    redeploy and no file change on the server.
- **Acceptance criteria:**
  - `kibana_url` and `es_url` point at **different** hosts (`...kb...` vs
    `...es...`). The collector warns if they match; pointing both at Kibana makes
    every `_bulk` write 404.
  - The deploy host can `GetSecretValue` on the secret and nothing else.
  - Rotation cadence documented.

### DHM-F — Deploy to the existing server with Ansible + schedule
- **Type:** Story · **Size:** M · **Status:** To Do · **Blocked by:** DHM-E
- **Description:** Deploy this repo to the existing production server with an
  Ansible playbook and run it on a schedule against the production Kibana /
  Elasticsearch cluster. No dedicated server is required — GitHub is the artifact
  source and Ansible does the install. See `HOWTO.md` §3B.4 for the playbook sketch.
- **Steps (high level):**
  - Ansible: `git` checkout of the repo, `pip install -r requirements.txt` into a
    virtualenv, template `config/settings.yaml`, install the cron/systemd unit.
  - Set only two environment-specific values: `DHM_AWS_SECRET_ID` (the DHM-E
    secret) and `DHM_AWS_REGION` (`us-east-1`). Leave `kibana.base_url`,
    `elasticsearch.base_url` and every `api_key` field empty — the secret supplies
    them.
  - In `settings.yaml` set `browser_channel: msedge`,
    `registry_source: api`, and the production `kibana_space`.
  - Confirm the target host has Python 3.10+, Microsoft Edge on `PATH`, outbound
    HTTPS to both endpoints, and the instance role from DHM-E.
  - Run `python scripts/setup_elasticsearch.py` once to apply the template + ILM
    in prod.
  - Run one manual `python scripts/run_collector.py` to confirm end-to-end, then
    enable the schedule (default: every 20–30 minutes).
- **Acceptance criteria:**
  - The playbook is idempotent — a second run reports no changes.
  - Data stream `dashboard-health-monitor` exists in production.
  - Scheduled cycles land docs on schedule; log rotation working.
  - No secrets or endpoints on disk in the repo, the templated settings file, or
    the crontab. The run's opening `Connection:` line shows every value resolving
    `<-aws-bundle` (with `kibana.auth.api_key<-elasticsearch.api_key` when one
    Elastic key authorizes both).

### DHM-G — Kibana alerting rules (dashboard failure + collector liveness)
- **Type:** Story · **Size:** M · **Status:** To Do · **Blocked by:** DHM-F
- **Description:** Configure Kibana Alerting rules so operators are notified
  when dashboards regress or when the collector itself stops running. Use
  Elasticsearch Query rules and an existing notification connector (Slack /
  email / PagerDuty — whichever ops uses).
- **Sub-work:**

  **G.1 — Dashboard-failed alert**
  - Rule type: Elasticsearch Query
  - Index: `dashboard-health-monitor*`
  - Query: `load_status : "failed"` over the last `2 × cron cadence` (e.g.
    60 min if cron runs every 30 min)
  - Threshold: `count > 0`
  - Group by: `dashboard_title` (so ops sees which dashboard(s) failed)
  - Notification: Slack channel / email — include `dashboard_title`,
    `load_time_ms`, and count of `panels_not_ok`.

  **G.2 — Panel-unhealthy alert (optional but recommended)**
  - Rule type: Elasticsearch Query
  - Query: `panels_not_ok > 0`
  - Threshold: `count > 0` in the last 2× cron cadence
  - Group by: `dashboard_title`
  - Notification: same channel, lower severity.

  **G.3 — Collector liveness / dead-man's switch**
  - Rule type: Elasticsearch Query
  - Index: `dashboard-health-monitor*`
  - Query: `match_all`
  - Threshold: `count < expected_docs_per_cycle` in the last
    `2 × cron cadence`
  - Fires when the collector stops writing (script crash, host down, cron
    broken, secret expired).
  - Notification: same channel, high severity — the tag "monitor is silent"
    makes it clear this is meta-alerting.

  Rule payload skeletons are already in the repo at `es/alerting/*.json` — G.1
  and G.3 can be adapted from `load_time_rule.json` and
  `dead_mans_switch_rule.json`.

- **Acceptance criteria:**
  - All three rules exist in prod and are enabled.
  - Each rule dry-runs successfully against historical data (or an injected bad
    doc).
  - A test failure — e.g. temporarily stopping the collector — triggers G.3
    within 2× cron cadence.
  - A dashboard forced into the `failed` state triggers G.1.
  - Notification channel confirmed reaching the on-call recipient.

---

# Original planning breakdown

*Historical — the pre-implementation task decomposition. Kept for reference;
DHM-A → DHM-G above is the current source of truth.*

This section breaks `dashboard_health_monitor_project_plan.md` into a Jira
hierarchy:

- **1 Epic** — the whole project.
- **Tasks** — one per plan phase / workstream.
- **Sub-tasks** (`DHM-*`) — the individual, ticketable units under each Task,
  each sized to fit a single card (roughly 0.5–3 days).

**Legend** — sub-task `Type`: Story / Task / Spike. `Size`: S (≤1d) / M (1–3d) /
L (3–5d). `Status` notes where a sub-task is already implemented in this repo.

---

# EPIC — Federal Overview Dashboard Health & Load-Time Monitor

Replace the manual daily review of the Federal Overview dashboard family with an
automated, scheduled check that measures per-dashboard and per-panel load time,
verifies every expected panel rendered data, alerts on degradation, and produces
a historical trend. Delivered across the Tasks below.

---

## TASK 1 — Registry from the export
*Plan Phase 1. Turn the `.ndjson` into the exact list of what we monitor.*

### DHM-1 — Project scaffolding & tests
- **Type:** Task · **Size:** S · **Status:** done in repo
- **Description:** Python package layout (`src/dhm`), `requirements.txt`, config
  loader with environment overrides, `.gitignore` for secrets, and a pytest setup.
- **Acceptance criteria:**
  - `pip install -r requirements.txt` succeeds; `pytest` runs.
  - `config/settings.yaml` is git-ignored; every secret has an env override.

### DHM-2 — Parse the export into a registry
- **Type:** Story · **Size:** M · **Status:** done in repo
- **Description:** `scripts/build_registry.py` / `src/dhm/registry.py` parse
  `federal_overview.ndjson` into `config/dashboards.generated.json`: 22 dashboards,
  the hub, and each dashboard's expected panels (id, title, type, data-vs-nav).
- **Acceptance criteria:**
  - Output lists all 22 dashboards, marks the hub, and 215 data panels.
  - Navigation (Links) panels are recorded but flagged non-data.
  - Underlying saved-object ids resolve for by-reference panels.

### DHM-3 — Registry unit tests
- **Type:** Task · **Size:** S · **Status:** done in repo
- **Description:** `tests/test_registry.py` asserts dashboard/panel counts, hub
  detection, and classification against the real export.
- **Acceptance criteria:**
  - Tests pass and fail loudly if the export changes shape.

### DHM-4 — Live registry discovery (production) + export refresh (test)
- **Type:** Story · **Size:** M · **Status:** implemented in repo (`src/dhm/discovery.py`)
- **Description:** Production uses `registry_source: api` — `discovery.py` builds the
  registry from Kibana's Saved Objects API each run, so no `.ndjson` lives on the
  server and dashboard/panel changes are picked up automatically. Test/offline uses
  `registry_source: export` — drop in a new `.ndjson`, re-run `build_registry.py`,
  review the diff, commit.
  Both sources are narrowed by `collector.selection` (default `linked`): the hub
  (`hub_title`) plus every dashboard reachable from its navigation (Links panels +
  drilldowns, transitively) — applied uniformly by `registry.select_registry`.
  `titles` monitors an explicit list; `all` monitors the whole space.
- **Acceptance criteria:**
  - `api` mode returns the current dashboards/panels from the space with no export
    file present.
  - Default (`linked`) monitors "Federal Overview" + all dashboards reachable from
    it (22 in the current export); `titles`/`all` change the scope.
  - `export` mode still works for offline/test.
  - Reachability, selection, discovery, and config precedence are unit-tested
    (`tests/test_registry.py`, `tests/test_discovery.py`, `tests/test_config.py`).

---

## TASK 2 — Render-detection spike (de-risk)
*Plan Phase 2. Do this before trusting Task 4 at scale.*

### DHM-5 — Spike: prove render + per-panel timing on our Kibana
- **Type:** Spike · **Size:** M
- **Description:** Run the collector against one real dashboard and confirm we can
  read render-complete, per-panel `ok/empty/error/timeout`, and per-panel render
  time off the DOM (see plan §4.1) on our actual Kibana version.
- **Acceptance criteria:**
  - Demonstrates a stable "all panels rendered" signal (no fixed sleep).
  - Correct classification for at least one ok panel and one empty/error panel.
  - Written up: which selectors work, Kibana version, gaps; go/no-go on §4.1.
- **Dependencies:** DHM-8, DHM-11

---

## TASK 3 — Auth decision & identity
*Plan Section 7. Gates the MVP — do first.*

### DHM-6 — Decide the Kibana browser-auth approach
- **Type:** Spike · **Size:** S
- **Description:** Confirm with Cloud Automation whether an API key/basic auth, or
  a service-account session cookie, fronts the automated browser. Output the method
  the collector uses.
- **Acceptance criteria:**
  - Documented decision (api_key vs cookie) and that the identity can be provisioned
    least-privilege.
- **Dependencies:** none — **do first**

### DHM-7 — Provision the least-privilege automation identity + secret
- **Type:** Task · **Size:** S · **Status:** resolution implemented (`src/dhm/secrets.py`)
- **Description:** Create the automation credential (read on the monitored space +
  write to `dashboard-health-monitor`) and store it in AWS Secrets Manager. The code
  resolves the Kibana URL, the Elasticsearch URL and the API key by the same
  precedence: CLI flag > env var > the `aws_secret_id` connection bundle >
  a value-specific AWS secret > `settings.yaml` (test passes the key on the CLI;
  prod reads everything from AWS). **Superseded for the production rollout by
  DHM-E**, which carries the concrete secret/IAM steps.
- **Acceptance criteria:**
  - Test run authenticates with `--es-api-key`; prod run authenticates from AWS with
    nothing on the command line.
  - Rotation cadence documented.
- **Dependencies:** DHM-6

---

## TASK 4 — Index + collector (MVP)
*Plan Phase 3. Load time + per-panel runtime + health, written to ES.*

### DHM-8 — Browser collector: load + per-panel timing + health
- **Type:** Story · **Size:** L · **Status:** implemented in repo (needs live Kibana to validate)
- **Description:** `src/dhm/collect_core.py` holds the backend-agnostic timing +
  document assembly; `src/dhm/collector.py` (Playwright) drives it. Loads each
  dashboard, waits for render-complete, records load time and per-panel `render_ms`,
  and classifies each panel via `render_detection`. Centralized selectors in
  `selectors.py`.
- **Acceptance criteria:**
  - Produces one document per dashboard matching the plan §5 schema.
  - Enforces the per-dashboard hard timeout; a hung dashboard is `failed`.
  - Detects `missing` panels by reconciling against the registry.
  - Core timing/health logic is unit-tested with a fake driver (`tests/test_collect_core.py`).
- **Dependencies:** DHM-7, DHM-10

### DHM-8b — Selenium fallback backend
- **Type:** Story · **Size:** M · **Status:** implemented in repo (needs live Kibana to validate)
- **Description:** `src/dhm/collector_selenium.py` drives the same system Edge/Chrome
  (via `msedgedriver`/`chromedriver`) and the same `collect_core`, selected by
  `collector.backend: selenium`. Fallback for boundaries where the `playwright` pip
  package cannot be installed. `requirements-selenium.txt` holds its deps.
- **Acceptance criteria:**
  - `backend: selenium` produces documents identical in shape to the Playwright path.
  - Auth works for both api_key (via CDP headers) and cookie methods.
- **Dependencies:** DHM-8

### DHM-9 — Render-detection classifier + unit tests
- **Type:** Task · **Size:** M · **Status:** done in repo
- **Description:** `render_detection.py` (`classify_panel`, `reconcile`,
  `summarize`) plus `tests/test_render_detection.py` over raw-signal fixtures.
- **Acceptance criteria:**
  - Every status classifies correctly, including error-over-empty precedence and
    missing-panel reconciliation.

### DHM-10 — Browser on the runner (system Edge/Chrome)
- **Type:** Task · **Size:** S · **Status:** collector supports `browser_channel`
- **Description:** Confirm the system browser the collector will drive is present
  and set `collector.browser_channel` (`msedge` in prod, `chrome` in test). No
  browser download — the collector launches the installed Edge/Chrome via a
  Playwright channel. Only if the system browser is disallowed do we fall back to
  `browser_channel: chromium` + `playwright install` from the approved mirror.
- **Acceptance criteria:**
  - Collector launches headless against the runner's Edge/Chrome; no download.
  - `playwright` pip install is confirmed permitted in the boundary.
- **Dependencies:** DHM-1

### DHM-11 — Index template, ILM, and ES writer
- **Type:** Task · **Size:** M · **Status:** done in repo
- **Description:** `es/index_template.json` (data stream, nested `panels`),
  `es/ilm_policy.json`, `scripts/setup_elasticsearch.py`, and `es_writer.bulk_index`.
  The writer reuses an HTTP session, retries 429/5xx/connection errors with
  exponential backoff (honouring `Retry-After`), chunks `_bulk` to
  `bulk_chunk_size`, and applies a per-request timeout.
- **Acceptance criteria:**
  - Template + ILM apply cleanly; a cycle's documents index via `_bulk`.
  - Document fields match the mapping exactly.
  - Retry/backoff and chunking are unit-tested (`tests/test_es_writer.py`).

### DHM-12 — End-to-end dry run against real Kibana
- **Type:** Story · **Size:** M
- **Description:** Run a full cycle with `--dry-run --out run.json` against the real
  cluster; review load times and panel health for all 22 dashboards; then do a live
  write and confirm the documents land.
- **Acceptance criteria:**
  - All 22 dashboards produce sensible load times and per-panel results.
  - Documents are queryable in `dashboard-health-monitor`.
- **Dependencies:** DHM-8, DHM-11, DHM-7

---

## TASK 5 — Optional query enrichment
*Plan Phase 4. Additive; skip where inconvenient.*

### DHM-13 — Panel-to-query resolver (Lens + classic)
- **Type:** Story · **Size:** L
- **Description:** For opt-in panels, resolve the underlying query/data view from the
  registry's saved-object ids — handling both Lens and classic visualizations.
- **Acceptance criteria:**
  - Resolves data view + query for supported panel types; skips others cleanly.
- **Dependencies:** DHM-2

### DHM-14 — Direct-ES enrichment (hit count + freshness)
- **Type:** Story · **Size:** M
- **Description:** For resolved panels, add `hit_count` and `latest_doc_ts` to the
  panel record via a direct ES query.
- **Acceptance criteria:**
  - Enrichment present for opt-in panels, absent (not erroring) for others; failure
    never breaks the core document.
- **Dependencies:** DHM-13, DHM-8

---

## TASK 6 — Alerting
*Plan Phase 5. Elasticsearch Query rule type.*

### DHM-15 — Seed per-dashboard load-time baselines
- **Type:** Task · **Size:** S
- **Description:** From the first week of data, compute per-dashboard baseline load
  times and tune `degraded_over_ms` / `failed_over_ms`.
- **Acceptance criteria:**
  - Baselines documented and reflected in the thresholds.
- **Dependencies:** DHM-12

### DHM-16 — Load-degraded/failed alert
- **Type:** Story · **Size:** S · **Status:** rule JSON in repo
- **Description:** Create `es/alerting/load_time_rule.json` in Kibana with a real
  connector; fires on `load_status` degraded/failed.
- **Acceptance criteria:**
  - Fires within one cycle of a genuine regression; dry-run validated first.
- **Dependencies:** DHM-12

### DHM-17 — Panel-unhealthy alert
- **Type:** Story · **Size:** S · **Status:** rule JSON in repo
- **Description:** Create `es/alerting/panel_health_rule.json`; fires on
  `panels_not_ok > 0` (empty/error/timeout/missing).
- **Acceptance criteria:**
  - Fires on a genuinely unhealthy panel; dry-run validated first.
- **Dependencies:** DHM-12

### DHM-18 — Collector dead-man's-switch alert
- **Type:** Story · **Size:** S · **Status:** rule JSON in repo
- **Description:** Create `es/alerting/dead_mans_switch_rule.json`; fires if no
  document is written within 2x the cadence.
- **Acceptance criteria:**
  - Fires when the collector stops; recovers when it resumes.
- **Dependencies:** DHM-12

---

## TASK 7 — Trend dashboard & scheduling
*Plan Phase 6. The dashboard that replaces the manual review.*

### DHM-19 — Data view + trend dashboard
- **Type:** Story · **Size:** M
- **Description:** Create a Kibana data view over `dashboard-health-monitor` and a
  dashboard: load-time trend (per dashboard, per panel) and a panel-health heatmap.
- **Acceptance criteria:**
  - Load-time trend and panel-health history are visible and filterable.
- **Dependencies:** DHM-12

### DHM-20 — Schedule the collector (cron/AWX)
- **Type:** Task · **Size:** M
- **Description:** Deploy the collector on a 15–30 min cadence with bounded
  concurrency and the per-dashboard timeout (plan §8).
- **Acceptance criteria:**
  - Runs on schedule; one hung dashboard never stalls the cycle.
- **Dependencies:** DHM-12

### DHM-21 — Cut over from manual review
- **Type:** Story · **Size:** S
- **Description:** Retire the manual daily check once the trend dashboard and alerts
  have run cleanly for an agreed soak period.
- **Acceptance criteria:**
  - The team relies on the dashboard/alerts; the manual step is removed.
- **Dependencies:** DHM-19, DHM-16, DHM-17, DHM-18

---

## Suggested ordering / critical path

1. **DHM-6** (auth decision) — unblocks the browser work.
2. **DHM-1, DHM-2, DHM-3** (registry) and **DHM-9, DHM-11** (classifier, index) —
   already done in this repo.
3. **DHM-7, DHM-10** — credential and browser on the runner.
4. **DHM-5** (spike) — go/no-go on render detection against our Kibana.
5. **DHM-8 → DHM-12** — the MVP end to end.
6. **DHM-15–18** (alerting), then **DHM-19–21** (trend dashboard, schedule, cutover).
7. **DHM-13/14** (enrichment) can slot in any time after DHM-12.
