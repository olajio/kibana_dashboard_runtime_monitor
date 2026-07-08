# Dashboard Health & Load-Time Monitor — Jira Breakdown

This breaks `dashboard_health_monitor_project_plan.md` into a Jira hierarchy:

- **1 Epic** — the whole project.
- **Tasks** — one per plan phase / workstream (formerly the "epics").
- **Sub-tasks** (`DHM-*`) — the individual, ticketable units under each Task,
  each sized to fit a single card (roughly 0.5–3 days).

**Legend** — sub-task `Type`: Story / Task / Spike. `Size`: S (≤1d) / M (1–3d) /
L (3–5d, consider splitting). Dependencies reference other `DHM-*` IDs.

---

# EPIC — Dashboard Health & Load-Time Monitor

Replace manual daily dashboard review with an automated, scheduled check that
measures browser-rendered load time, confirms every panel is returning data,
alerts on degradation, and produces a historical trend. Delivered across the
Tasks below.

---

## TASK 1 — Discovery & Scaffolding
*Plan Phase 0. Project skeleton + a trustworthy list of dashboards to check.*

### DHM-1 — Project scaffolding & CI
- **Type:** Task · **Size:** S
- **Description:** Stand up the repo layout, Python packaging/deps, linting/formatting, and a CI check. Add Playwright as a dependency (browser install handled in DHM-9).
- **Acceptance criteria:**
  - Repo has a runnable Python project (deps pinned), lint + format configured.
  - CI runs lint + tests on push.
  - README with setup/run instructions.
- **Dependencies:** none

### DHM-2 — Dashboard allow-list config
- **Type:** Story · **Size:** S
- **Description:** Define the explicit allow-list of currently-manually-checked dashboards (per cluster) as config the collector reads. This is the starting registry before dynamic discovery.
- **Acceptance criteria:**
  - Config format holds cluster, dashboard_id, title, space, and URL (or enough to build it).
  - Loadable and validated at startup with a clear error on malformed entries.
- **Dependencies:** DHM-1

### DHM-3 — Saved Objects API client (dashboard discovery)
- **Type:** Story · **Size:** M
- **Description:** `dashboard_registry.py` — call `GET /api/saved_objects/_find?type=dashboard` and return dashboard definitions.
- **Acceptance criteria:**
  - Returns dashboards with id, title, space.
  - Handles pagination and API errors gracefully.
  - Auth via the same secret mechanism as the rest of the tooling.
- **Dependencies:** DHM-1, DHM-14

### DHM-4 — Resolve panel references per dashboard
- **Type:** Story · **Size:** M
- **Description:** Extend the registry to resolve each dashboard's panel references to their visualization/lens/search objects, producing panel id/title/type.
- **Acceptance criteria:**
  - For a given dashboard, returns its panels with id, title, and type.
  - Handles both Lens and classic visualization reference shapes.
- **Dependencies:** DHM-3

---

## TASK 2 — Render-Detection Spike (De-risk)
*Plan Phase 0.5. Do this before Task 4 — the whole MVP depends on it.*

### DHM-5 — Spike: prove render-complete + per-panel state detection
- **Type:** Spike · **Size:** M
- **Description:** Throwaway Playwright script that loads ONE real dashboard and proves we can reliably read the render-complete signal and classify each panel as ok/empty/error/timeout off the DOM (see plan §3.1), against the actual target Kibana version.
- **Acceptance criteria:**
  - Demonstrates a stable "all panels rendered" signal (not a fixed sleep).
  - Demonstrates correct classification for at least one ok panel and one empty/error panel.
  - Written up: which selectors/attributes work, Kibana version tested, and any gaps. Go/no-go recommendation for the §3.1 approach.
- **Dependencies:** DHM-9 (browser install), DHM-13 (auth answer), DHM-2

---

## TASK 3 — Auth Decision & Identity
*Cross-cutting; resolve early — see plan §6. Blocks the MVP.*

### DHM-13 — Decide & confirm Kibana browser-auth approach
- **Type:** Spike · **Size:** S
- **Description:** Confirm with Jesse/Cloud Automation whether API-key/basic-auth, a service-account session token, or RolesAnywhere/existing service identity can front the automation. Output the concrete approach DHM-7 implements.
- **Acceptance criteria:**
  - Documented decision on auth method for the browser path.
  - Confirmed the automation identity can be provisioned with least privilege.
- **Dependencies:** none — **do first**

### DHM-14 — Provision least-privilege automation identity + secret
- **Type:** Task · **Size:** S
- **Description:** Create the automation service account/API key with read on monitored spaces/dashboards + write to `.dashboard-health-monitor`, stored in Secrets Manager (`elastic/kibana/...`), with a rotation cadence.
- **Acceptance criteria:**
  - Credential exists with least-privilege scope, retrievable from Secrets Manager.
  - Rotation cadence documented.
- **Dependencies:** DHM-13

---

## TASK 4 — Index + Core Collector (MVP)
*Plan Phase 1. Load time + data-presence from one page load. This is the MVP.*

### DHM-6 — Index template, mapping & ILM policy
- **Type:** Task · **Size:** M
- **Description:** Create the `.dashboard-health-monitor` index template + mapping (per plan §4, including `schema_version`, `env`, `kibana_space`, `panels` nested, rollup fields) and an ILM/retention policy sized to the trend window (e.g. 90–180d).
- **Acceptance criteria:**
  - Index template applies cleanly; `panels` mapped as nested.
  - ILM policy attached; retention documented.
  - A sample doc round-trips and is queryable.
- **Dependencies:** DHM-1

### DHM-7 — Playwright auth session injection
- **Type:** Story · **Size:** M
- **Description:** Implement the chosen browser-auth mechanism (API-key header injection, or service-account cookie via `context.add_cookies()`) so Playwright reaches an authenticated dashboard. Follows the outcome of DHM-13.
- **Acceptance criteria:**
  - Playwright loads a protected dashboard URL authenticated, no interactive login.
  - Credentials pulled from Secrets Manager, never hard-coded.
  - Clear failure/`load_error` when auth fails.
- **Dependencies:** DHM-13, DHM-9

### DHM-8 — Load-time capture
- **Type:** Story · **Size:** M
- **Description:** Navigate to a dashboard and measure load time as elapsed from navigation start to last-panel render-complete, capped by a per-dashboard hard timeout (§7).
- **Acceptance criteria:**
  - Emits `load_time_ms` and `load_status` (ok/degraded/failed).
  - Enforces per-dashboard timeout; a hung dashboard yields `failed`, not a stalled run.
- **Dependencies:** DHM-5, DHM-7

### DHM-9 — Playwright headless Chromium install (FedRAMP-safe)
- **Type:** Task · **Size:** S
- **Description:** Get headless Chromium installed within the approved package/network allowlist and reproducible in the deploy environment.
- **Acceptance criteria:**
  - Documented, repeatable install that works inside the boundary (no unapproved fetches).
  - Verified running headless in the target environment.
- **Dependencies:** DHM-1

### DHM-10 — Per-panel render-state classifier
- **Type:** Story · **Size:** M
- **Description:** Productionize the spike logic: read each panel's on-screen state → `ok | empty | error | timeout` plus `render_status_detail`. Centralize selectors in one module (§3.1).
- **Acceptance criteria:**
  - Classifies each panel and captures Kibana's on-screen message where shown.
  - All selectors live in one module for easy version bumps.
- **Dependencies:** DHM-5, DHM-8

### DHM-11 — Assemble & write health docs to ES
- **Type:** Story · **Size:** M
- **Description:** `dashboard_health_check.py` orchestration: loop the registry, produce one doc per dashboard (full §4 schema incl. `collector_run_id`, `collector_version`, rollups), and write to `.dashboard-health-monitor`.
- **Acceptance criteria:**
  - One complete, valid doc written per dashboard per run.
  - Shared `collector_run_id` across a cycle; `panels_ok`/`panels_not_ok` populated.
  - Structured logging; a single dashboard failure doesn't abort the run.
- **Dependencies:** DHM-6, DHM-8, DHM-10

### DHM-12 — Render-detection tests + known-good/known-bad fixtures
- **Type:** Task · **Size:** M
- **Description:** Unit tests over captured DOM snapshots for each render state, plus a designated healthy and broken dashboard for deploy smoke tests (plan §8).
- **Acceptance criteria:**
  - Tests assert correct classification for ok/empty/error/timeout snapshots.
  - Smoke test runs the collector against the two fixtures and checks expected results.
- **Dependencies:** DHM-10

---

## TASK 5 — Optional Query Enrichment
*Plan Phase 2. Purely additive; skip per-panel where inconvenient.*

### DHM-15 — Panel-to-query resolver (Lens + classic)
- **Type:** Story · **Size:** L (split if needed)
- **Description:** For opt-in panels, resolve the underlying query/data view — handling both Lens and classic aggregation-based visualizations.
- **Acceptance criteria:**
  - Resolves data view + query for supported panel types.
  - Cleanly skips/marks unsupported panels without failing the run.
- **Dependencies:** DHM-4

### DHM-16 — Direct-ES enrichment (hit count + freshness)
- **Type:** Story · **Size:** M
- **Description:** For resolved panels, run a direct ES query to add `hit_count` and `latest_doc_ts` to the panel record.
- **Acceptance criteria:**
  - Enrichment fields populated for opt-in panels; absent (not null-erroring) for others.
  - Enrichment failure never breaks the core doc.
- **Dependencies:** DHM-15, DHM-11

---

## TASK 6 — Alerting
*Plan Phase 3. Elasticsearch Query rule type; reuse Project 13 dead-man's-switch pattern.*

### DHM-17 — Seed per-dashboard load-time baselines
- **Type:** Task · **Size:** S
- **Description:** From the first week of collected data, compute per-dashboard baseline load times to alert on deviation rather than a flat threshold (§7).
- **Acceptance criteria:**
  - Baseline value derivable per dashboard and referenceable by the load-time rule.
- **Dependencies:** DHM-11

### DHM-18 — Load-time degradation alert
- **Type:** Story · **Size:** S
- **Description:** Kibana Alerting rule that fires when load time exceeds the dashboard's baseline/threshold.
- **Acceptance criteria:**
  - Fires within one collector cycle of a genuine regression; recovers when normal.
  - Dry-run validated against historical/injected data before notifications enabled.
- **Dependencies:** DHM-17

### DHM-19 — Empty/error panel alert
- **Type:** Story · **Size:** S
- **Description:** Rule that fires when a panel is `empty`/`error` (keyed off the top-level `panels_not_ok` rollup).
- **Acceptance criteria:**
  - Fires on a genuinely empty/broken panel; identifies dashboard + panel.
  - Dry-run validated before enabling.
- **Dependencies:** DHM-11

### DHM-20 — Collector dead-man's-switch alert
- **Type:** Story · **Size:** S
- **Description:** Rule that fires if no new `.dashboard-health-monitor` doc for a cluster within 2× the expected interval.
- **Acceptance criteria:**
  - Fires when the collector stops writing for a cluster; recovers when it resumes.
- **Dependencies:** DHM-11

---

## TASK 7 — Trend Dashboard & Rollout
*Plan Phase 4. The dashboard-of-dashboards + going wide.*

### DHM-21 — Kibana trend dashboard
- **Type:** Story · **Size:** M
- **Description:** Build the Kibana dashboard over `.dashboard-health-monitor`: load-time-over-time trend and panel-health heatmap.
- **Acceptance criteria:**
  - Load-time trend and panel-health history visible and filterable by env/cluster/space.
- **Dependencies:** DHM-11

### DHM-22 — Dynamic discovery (retire the allow-list)
- **Type:** Story · **Size:** M
- **Description:** Switch the registry from the static allow-list to dynamic Saved Objects discovery once trusted.
- **Acceptance criteria:**
  - Collector runs against dynamically discovered dashboards.
  - Opt-out mechanism for dashboards that shouldn't be monitored.
- **Dependencies:** DHM-3, DHM-11

### DHM-23 — Scheduling & deployment (cron/AWX, staggered, bounded concurrency)
- **Type:** Task · **Size:** M
- **Description:** Deploy the collector on a 15–30 min cadence per cluster (dev/qa/prod/ccs), staggered, with bounded parallelism (§7).
- **Acceptance criteria:**
  - Runs on schedule per cluster; staggered start; concurrency bounded and documented.
- **Dependencies:** DHM-11

### DHM-24 — Multi-cluster / multi-space rollout
- **Type:** Story · **Size:** M
- **Description:** Roll out across dev/qa/prod/ccs and any additional spaces, populating `env`/`cluster`/`kibana_space` correctly.
- **Acceptance criteria:**
  - All target clusters reporting into the index and visible on the trend dashboard.
- **Dependencies:** DHM-21, DHM-23

---

## Suggested ordering / critical path

1. **DHM-13** (auth decision, Task 3) — unblocks everything browser-related.
2. **DHM-1, DHM-14, DHM-9** — scaffolding, credential, browser.
3. **DHM-5** (spike, Task 2) — go/no-go on the core detection approach.
4. **DHM-6 → DHM-7 → DHM-8 → DHM-10 → DHM-11 → DHM-12** (Task 4) — the MVP.
5. **DHM-17–20** (Task 6, alerting), then **DHM-21–24** (Task 7, dashboard + rollout).
6. **DHM-2/3/4** (Task 1) feed discovery; **DHM-15/16** (Task 5) enrichment can slot in any time after the MVP.
