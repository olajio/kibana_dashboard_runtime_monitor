# Dashboard Health & Load-Time Monitor — Jira Breakdown

This breaks `dashboard_health_monitor_project_plan.md` into a Jira hierarchy:

- **1 Epic** — the whole project.
- **Tasks** — one per plan phase / workstream.
- **Sub-tasks** (`DHM-*`) — the individual, ticketable units under each Task,
  each sized to fit a single card (roughly 0.5–3 days).

Scope reminder: we monitor **one** application bundle — the Federal Overview
family of 22 dashboards and their 215 data panels — in **one** cluster and space.
There is no multi-cluster rollout.

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

### DHM-4 — Refresh flow when the export changes
- **Type:** Story · **Size:** S
- **Description:** Document and script the "we re-exported the dashboards" flow:
  drop in a new `.ndjson`, re-run `build_registry.py`, review the diff of the
  generated registry, commit.
- **Acceptance criteria:**
  - A one-command refresh, and a reviewed diff shows added/removed panels.

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
- **Type:** Task · **Size:** S
- **Description:** Create the automation credential (read on the monitored space +
  write to `.dashboard-health-monitor`), store it in Secrets Manager, and wire it
  through the collector's environment variables. Define a rotation cadence.
- **Acceptance criteria:**
  - Collector authenticates using only environment-supplied secrets.
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
- **Acceptance criteria:**
  - Template + ILM apply cleanly; a cycle's documents index via `_bulk`.
  - Document fields match the mapping exactly.

### DHM-12 — End-to-end dry run against real Kibana
- **Type:** Story · **Size:** M
- **Description:** Run a full cycle with `--dry-run --out run.json` against the real
  cluster; review load times and panel health for all 22 dashboards; then do a live
  write and confirm the documents land.
- **Acceptance criteria:**
  - All 22 dashboards produce sensible load times and per-panel results.
  - Documents are queryable in `.dashboard-health-monitor`.
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
- **Description:** Create a Kibana data view over `.dashboard-health-monitor` and a
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
