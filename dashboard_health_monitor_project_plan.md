# Dashboard Health & Load-Time Monitor — Project Plan

## 1. Objective

Replace the manual daily review of the **Federal Overview** dashboard family
with an automated, scheduled check that:

- Measures real-world load time (browser-rendered, not just API latency) for the
  Federal Overview hub dashboard and every dashboard it links to.
- Measures per-panel render time, so we know which specific visualization is slow.
- Confirms every expected panel actually rendered data — and flags panels that are
  empty, errored, timed out, or missing entirely.
- Alerts when a dashboard degrades or a panel goes unhealthy.
- Produces a historical trend instead of a point-in-time manual note.

## 2. Scope

The monitored target is the single `federal_overview.ndjson` saved-objects
export, which we already have in this repo. It describes an application bundle:

- **22 dashboards** — the "Federal Overview" hub plus the 21 dashboards it
  reaches through Links panels and panel drilldowns.
- **215 data panels** across those dashboards (131 Lens, 61 visualizations,
  23 saved searches), plus 70 Links (navigation) panels.

Everything runs against **one cluster and one Kibana space**. We are not
monitoring multiple dashboards across different clusters, so there is no
multi-cluster rollout in this plan — cluster and space are recorded on each
document as single configurable labels.

**In scope**
- Load-time measurement per dashboard (all 22).
- Render-time measurement per data panel.
- Per-panel health: `ok | empty | error | timeout | missing`.
- Alerting via Kibana Alerting rules.
- Historical trending via a dedicated ES data stream + Kibana dashboard.

**Out of scope (for v1)**
- Full visual regression testing (pixel-diffing panels).
- Auto-remediation of broken dashboards/queries.
- Monitoring dashboards outside this export.

## 3. Why the export changes the design

Because the export enumerates exactly what we monitor, we do not have to
discover dashboards live or guess what "should" be on a page:

- **The registry is derived from the `.ndjson`**, deterministically. No
  Saved-Objects crawl is required for v1 (`scripts/build_registry.py` →
  `config/dashboards.generated.json`).
- **We know the expected panel inventory per dashboard**, so the collector can
  detect a panel that failed to appear at all (`missing`), not only one that
  rendered empty. Manual review could never do this reliably.
- **Per-panel runtime is a first-class output**, because we watch each
  embeddable resolve individually.

## 4. Architecture

```
federal_overview.ndjson
        │  build_registry.py (parse export)
        ▼
config/dashboards.generated.json ──▶ dashboard_health_check (collector)
   (22 dashboards, expected panels)     - Playwright loads each dashboard
                                        - waits for per-panel render-complete
                                        - records load time + per-panel render_ms
                                        - classifies each panel's health
                                        │
                                        ▼
                            .dashboard-health-monitor  (ES data stream)
                                        │
                        ┌───────────────┼────────────────┐
                        ▼                                ▼
              Kibana Alerting rules            Kibana trend dashboard
       (load degraded/failed, panel           (load time over time,
        unhealthy, dead-man's-switch)          panel-health heatmap)
```

One check path works for every dashboard without needing to know what is behind
each panel:

| Check | Method | Why |
|---|---|---|
| Load time | Headless browser navigates to each dashboard, waits for every panel's render-complete signal, records elapsed ms | Only a rendered-browser measurement matches what we record manually today |
| Per-panel render time | Poll each embeddable's render-complete state and record when each one first resolves | Gives us the runtime of each individual visualization |
| Panel health | Read the rendered state Kibana itself shows, and compare the panels we see against the panels the registry says should be there | Needs nothing about a panel's underlying query — it reads what is on screen and reconciles it against the export |

Optional enrichment (later, only where convenient): for panels whose underlying
query/data view is easy to resolve, add a direct-ES query for exact hit count
and freshness. This is additive and per-panel opt-in — never required for the
core check.

### 4.1 How "loaded" and per-panel state are detected

The whole approach rests on reliably reading Kibana's own render state, so we are
concrete about the mechanism (and de-risk it early — see the Phase 0.5 spike):

- **"Loaded" signal**: Kibana stamps `data-render-complete="true"` on each panel
  when its embeddable finishes. We wait for every panel to reach that state rather
  than using a fixed `sleep` or `networkidle`. Per-panel render time = elapsed
  from navigation start to the moment that panel first resolves; dashboard load
  time = the last panel to resolve, capped by a hard timeout.
- **Per-panel state**: we read each panel via its `data-test-subj` markers — a
  rendered chart, Kibana's own "No results found" empty state, an error
  embeddable, or no resolution before the timeout → `ok | empty | error | timeout`.
  A registry panel we never see on the page → `missing`.
- **Version sensitivity**: these selectors depend on the Kibana version. We pin the
  target version and keep every selector in one module (`src/dhm/selectors.py`), so
  a version bump is a one-file change.

## 5. Data model

Data stream: `.dashboard-health-monitor` (one document per dashboard per cycle).

```json
{
  "@timestamp": "<run timestamp>",
  "schema_version": 1,
  "app": "federal_overview",
  "cluster": "<single cluster label>",
  "kibana_space": "<space id, default 'default'>",
  "dashboard_id": "<saved object id>",
  "dashboard_title": "<dashboard name>",
  "is_hub": true,
  "dashboard_url": "<url the collector navigated to>",
  "load_time_ms": 0,
  "load_status": "ok | degraded | failed",
  "load_error": "<dashboard-level failure reason, if any>",
  "expected_data_panels": 0,
  "panel_count": 0,
  "panels_checked": 0,
  "panels_ok": 0,
  "panels_not_ok": 0,
  "panels_empty": 0,
  "panels_error": 0,
  "panels_timeout": 0,
  "panels_missing": 0,
  "panels": [
    {
      "panel_id": "<panelIndex — matches the DOM embeddable id>",
      "panel_title": "<panel title>",
      "panel_type": "lens | visualization | search",
      "render_status": "ok | empty | error | timeout | missing",
      "render_status_detail": "<Kibana's on-screen message, if any>",
      "render_ms": 0
    }
  ],
  "collector_run_id": "<uuid — shared by every doc in one cycle>",
  "collector_version": "<collector build>"
}
```

Notes:
- The top-level `panels_*` rollups let alerting key off a single field instead of
  scanning the nested `panels` array on every evaluation.
- `cluster` / `kibana_space` are single values today; keeping them as fields means
  the trend dashboard and alerts already filter correctly if scope ever widens.
- `panels` is mapped as a `nested` type so per-panel queries are exact.

## 6. Phases & milestones

| Phase | Deliverable | Notes |
|---|---|---|
| **Phase 0 — Registry** | `scripts/build_registry.py` parses the export into `config/dashboards.generated.json` (22 dashboards, expected panels) | Deterministic; unit tested against the real export. Done in this repo. |
| **Phase 0.5 — Render-detection spike (de-risk)** | A throwaway Playwright run against one real dashboard proving we can read render-complete + per-panel `ok/empty/error/timeout` off the DOM against our Kibana version | Do this before trusting Phase 1 at scale. Validates the §4.1 selectors. |
| **Phase 1 — Index + collector (MVP)** | ES data stream (template + ILM), then the collector loads every registry dashboard, records load time + per-panel render time + health, writes to `.dashboard-health-monitor` | The whole MVP. Auth to Kibana is the main build risk — see Section 7. |
| **Phase 2 — Optional query enrichment** | For easy-to-resolve panels, add hit count + freshness from a direct ES query | Purely additive; skip where inconvenient. |
| **Phase 3 — Alerting** | Kibana Alerting rules: load degraded/failed, panel unhealthy, collector dead-man's-switch (`es/alerting/*.json`) | Elasticsearch Query rule type. Validate against historical data before enabling notifications. |
| **Phase 4 — Trend dashboard** | A Kibana dashboard over `.dashboard-health-monitor`: load-time trend and panel-health heatmap; then hand the daily review over to it | Final step — the dashboard that replaces the manual check. |

## 7. Auth strategy (the main open question)

The collector needs an authenticated Kibana session to load dashboards in a
browser.

- If Kibana allows API-key or basic auth for automation, we inject the key as an
  `Authorization: ApiKey <key>` header before navigation (the collector already
  supports this).
- If Kibana is PKI/SAML-only, we use a dedicated automation service account with a
  long-lived session cookie that the collector injects directly (the collector
  supports a cookie method too), rather than scripting an interactive login.
- We confirm with Cloud Automation whether an existing service identity can front
  this before building anything more elaborate.
- **Credential hygiene**: the automation identity needs only read on the monitored
  space plus write to `.dashboard-health-monitor`. Secrets come from the
  environment / Secrets Manager, never the repo. `config/settings.yaml` is
  git-ignored; every secret has an environment-variable override. We define a
  rotation cadence up front.

## 8. Scheduling & deployment

- Cron or an AWX job runs one collection cycle on a fixed cadence. Recommended
  cadence: every 15–30 min (far finer-grained than the once-daily manual check).
- **Per-dashboard hard timeout** (default 90s) caps each load so one hung
  dashboard cannot stall the cycle — it is recorded as `failed` and its panels as
  `timeout`, and we move on.
- **Concurrency** starts at 1. We raise it only after measuring browser memory on
  the runner; a small bounded pool keeps the cycle short without hammering Kibana.
- **Load-time thresholds** are configurable (`degraded_over_ms`, `failed_over_ms`).
  Because a heavy dashboard and a light one have different "normal," we seed
  per-dashboard baselines from the first week of data and refine the thresholds
  from there.
- **Retention**: an ILM policy on the data stream (default: roll over daily,
  delete after 180 days) keeps history bounded.
- **Liveness**: the dead-man's-switch rule fires if no document is written within
  2x the expected interval.

## 9. Testing & validation

Every stage has a concrete check; see the README for the exact commands.

- **Registry** — unit tests assert all 22 dashboards, the hub, panel counts, and
  data/nav classification against the real export (`tests/test_registry.py`).
- **Render detection** — unit tests over raw signal fixtures assert every status
  (`ok/empty/error/timeout/missing`) classifies correctly; this is the highest-risk
  logic (`tests/test_render_detection.py`).
- **Collector smoke** — a single-dashboard dry run prints load time and per-panel
  results without writing to ES.
- **End-to-end dry run** — a full cycle with `--dry-run --out run.json` to inspect
  the documents before any are indexed.
- **Alert dry-run** — validate each rule against historical data (or an injected
  bad document) before enabling notifications.

## 10. Risks / open questions

- **Auth to Kibana for the browser path** — gates the MVP (Section 7). Resolve
  early.
- **Render-state detection fragility** — the core check reads Kibana's on-screen
  state; selectors depend on the Kibana version and can shift on upgrade. Mitigated
  by the Phase 0.5 spike, centralized selectors (§4.1), and render-detection tests.
- **False "empty" during legitimately quiet periods** — low-traffic panels may be
  genuinely empty at times; per-panel expected-volume baselines can refine this if
  it produces noise.
- **FedRAMP boundary** — confirm the headless Chromium install stays within the
  approved package/network allowlist.

## 11. Success criteria

- No manual daily review of the Federal Overview family is required.
- An alert fires within one collection cycle of a genuine load-time regression, an
  empty/errored panel, or a missing panel.
- A per-dashboard and per-panel load-time trend is visible in Kibana — something
  the manual process never produced.

## 12. Jira mapping

The work is tracked as a single Epic; see
`dashboard_health_monitor_jira_tasks.md`. The Tasks map to the phases above, and
each `DHM-*` sub-task sits under its Task.
