# Dashboard Health & Load-Time Monitor — Project Plan

## 1. Objective

Replace manual daily review of dashboard load times and panel data validity with an automated, scheduled check that:
- Measures real-world dashboard load time (browser-rendered, not just API latency)
- Confirms every panel on each monitored dashboard is actually returning data
- Alerts when a dashboard degrades or a panel goes empty/stale
- Produces a historical trend view instead of a point-in-time manual note

## 2. Scope

**In scope**
- A defined set of daily-reviewed dashboards (start with current manual list; expand via Saved Objects discovery later)
- Load-time measurement per dashboard
- Per-panel data-presence validation
- Alerting via Kibana Alerting rules
- Historical trending via a dedicated ES index + Kibana dashboard

**Out of scope (for v1)**
- Full visual regression testing (pixel-diffing panels)
- Auto-remediation of broken dashboards/queries
- Non-daily-reviewed dashboards (can be added in Phase 4)

## 3. Architecture

```
┌────────────────────┐     ┌──────────────────────┐     ┌───────────────────────┐
│ dashboard_registry │────▶│ dashboard_health_     │────▶│ .dashboard-health-     │
│ (Saved Objects API │     │ check.py (collector)  │     │ monitor index          │
│  or config list)   │     │  - Playwright: load    │     └───────────────────────┘
└────────────────────┘     │    time capture        │              │
                            │  - Direct ES query:    │              ▼
                            │    per-panel data check │     ┌───────────────────────┐
                            └──────────────────────┘     │ Kibana Alerting rules  │
                                                           │ (load time threshold,  │
                                                           │  empty panel, dead     │
                                                           │  man's switch)         │
                                                           └───────────────────────┘
                                                                      │
                                                                      ▼
                                                           ┌───────────────────────┐
                                                           │ Kibana trend dashboard │
                                                           │ (load time over time,  │
                                                           │  panel health history) │
                                                           └───────────────────────┘
```

One check path, run by the same collector, works for any dashboard without needing to know what's behind it:

| Check | Method | Why |
|---|---|---|
| Load time | Playwright headless browser navigates to dashboard URL, waits for panel-loaded state, records elapsed ms | Only a rendered-browser measurement matches what you currently record manually |
| Data presence | Read the *rendered panel state* Kibana itself shows — did it display a chart, or Kibana's own "no results" / error message? | Requires nothing about the panel's underlying query or index — it's just reading what's already on screen, so it works uniformly across every dashboard |

Optionally, for panels where you *do* know or can easily look up the underlying query/data view, you can add a direct-ES-query check as enrichment (freshness, exact hit count). That's additive and per-panel opt-in — never a requirement for the core check to work.

### 3.1 How "loaded" and per-panel state are detected

The entire approach rests on reliably reading Kibana's own render state off the page, so it's worth being concrete about the mechanism (and de-risking it early — see the Phase 0 spike in Section 5):

- **"Dashboard loaded" signal**: Kibana emits a stable render-complete signal per embeddable. Wait for every panel's `[data-render-complete="true"]` (equivalently `[data-loading="false"]`) attribute rather than a fixed `sleep` or `networkidle`. Load time = elapsed from navigation start to the moment the last panel reports render-complete, capped by a hard timeout.
- **Per-panel render state**: read each panel's on-screen state via its `data-test-subj` markers — a rendered visualization, Kibana's own "No results found" / empty state, an error embeddable (`embeddableStackTrace` / error icon), or no resolution before the timeout → `ok | empty | error | timeout`.
- **Version sensitivity**: these selectors are Kibana-version-dependent. Pin the target Kibana version(s) and centralize all selectors in one module so a version bump is a one-file change.

## 4. Data model

Index: `.dashboard-health-monitor`

Design principle: every field below is populated purely from what the browser observes when the dashboard loads — dashboard ID, panel ID/title, and on-screen render state. Nothing requires knowing or resolving a panel's underlying query, index, or data view. This is what makes it generic across any dashboard you point it at.

```json
{
  "@timestamp": "<run timestamp>",
  "schema_version": 1,
  "env": "<dev | qa | prod | ccs>",
  "cluster": "<hhs | nasa | dos | nara>",
  "kibana_space": "<space id, default 'default'>",
  "dashboard_id": "<saved object id>",
  "dashboard_title": "<dashboard name, as shown in Kibana>",
  "dashboard_url": "<full URL the collector navigated to>",
  "load_time_ms": <int>,
  "load_status": "ok | degraded | failed",
  "load_error": "<dashboard-level failure reason, e.g. auth/nav timeout, if load_status=failed>",
  "panel_count": <int>,
  "panels_ok": <int>,
  "panels_not_ok": <int>,
  "panels": [
    {
      "panel_id": "<panel/embeddable id — this comes from the dashboard's own layout, not a query>",
      "panel_title": "<panel title as displayed>",
      "panel_type": "<lens | visualization | map | saved_search | etc. — descriptive only>",
      "render_status": "ok | empty | error | timeout",
      "render_status_detail": "<Kibana's own on-screen message, e.g. 'No results found', if shown>"
    }
  ],
  "collector_run_id": "<uuid — shared by every doc in one collector cycle>",
  "collector_version": "<git sha or semver of the collector build>",
  "check_duration_ms": <int>
}
```

Notes:
- `schema_version` lets the trend dashboard and alerting queries survive future field changes without silently breaking.
- `env` / `kibana_space` make cross-cluster and multi-space rollout (Phase 4) filterable from day one, even if only one value is used at first.
- `load_error` and the `panels_ok` / `panels_not_ok` rollups let alerting rules key off a single top-level field instead of scanning the nested `panels` array on every evaluation.

`render_status` is set from what's actually visible after the page finishes loading: did the panel render a chart/table, did Kibana show its own "No results found" state, an error icon, or did it never resolve (timeout)? That's it — no query lookup involved, so it works the same way for a dashboard you built yourself or one you've never opened before.

**Optional enrichment (add later, only for panels where it's easy):**

```json
{
  "hit_count": <int>,
  "latest_doc_ts": "<timestamp of the most recent backing document>"
}
```

This is a separate, opt-in layer for freshness/volume detail. Skip it entirely for panels where the underlying query isn't known or convenient to resolve — the core schema above doesn't depend on it.

## 5. Phases & milestones

| Phase | Deliverable | Notes |
|---|---|---|
| **Phase 0 — Discovery & scaffolding** | `dashboard_registry.py`: pulls dashboard + panel definitions via Saved Objects API (`GET /api/saved_objects/_find?type=dashboard`, resolve panel references to visualization/lens/search objects) | Start with an explicit allow-list of the currently-manually-checked dashboards; registry becomes dynamic once trusted |
| **Phase 0.5 — Render-detection spike (de-risk)** | A throwaway Playwright script that loads *one* real dashboard and proves we can reliably read render-complete + per-panel `ok/empty/error/timeout` off the DOM (Section 3.1) | **Do this before committing to Phase 1.** The whole MVP assumes on-screen state is readable and stable; validate that assumption against the actual target Kibana version first |
| **Phase 1 — Index + core collector** | Create the `.dashboard-health-monitor` index template, mapping, and ILM/retention policy. Then `dashboard_health_check.py`: Playwright authenticates to Kibana, loads each dashboard URL, waits for panel render-complete, records load time, and reads each panel's rendered state (ok/empty/error/timeout) straight off the page. Logs to `.dashboard-health-monitor` | This is the whole MVP — load time + data-presence, both from the same page load, no query knowledge needed. Auth is the main build item here — see Section 6 |
| **Phase 2 — Optional query enrichment** | For panels where the underlying query/data view is known and easy to resolve, add a direct-ES-query lookup for hit count + freshness | Purely additive; skip for panels where this isn't convenient |
| **Phase 3 — Alerting** | Kibana Alerting rules (Elasticsearch Query rule type, per your CCS project's stated preference over Watcher): load time > threshold, `data_status: empty/stale`, and a collector dead-man's-switch (no new doc in expected interval) | Reuse the four-layer degradation / dead-man's-switch pattern from Project 13 |
| **Phase 4 — Trend dashboard & rollout** | Kibana dashboard over `.dashboard-health-monitor` (load time trend, panel health heatmap); expand from allow-list to full dynamic discovery; roll out across dev/qa/prod/ccs | Final step — dashboard-of-dashboards |

## 6. Auth strategy (the one real open question)

- **Browser path (Phase 1, the MVP)**: Playwright needs an authenticated Kibana session — this is the auth that actually gates the MVP.
  - If Kibana auth allows API-key-based basic auth or a service account bypass for internal automation, this is straightforward — inject the API key as a request header before navigation.
  - If it's PKI/SAML-only, the cleanest option is a dedicated automation service account with a long-lived Kibana session token/cookie that the script injects directly (via `context.add_cookies()` in Playwright) rather than scripting the interactive login flow.
  - Recommend confirming with Jesse/Cloud Automation whether RolesAnywhere or an existing service identity can front this before building the login flow.
- **Direct-query path (Phase 2 enrichment)**: use an API key via Secrets Manager, same pattern as your other tooling (`elastic/kibana/...` secret naming convention). No browser involved, so no SSO complexity.
- **Credential hygiene (both paths)**: least-privilege — the automation identity needs only read access to the monitored spaces/dashboards plus write to `.dashboard-health-monitor`. Store secrets in Secrets Manager (never in the registry or repo) and define a rotation cadence up front.

## 7. Scheduling & deployment

- Cron (or AWX job, consistent with your `find_duplicate_dataviews_awx.py` pattern) — run per cluster (dev/qa/prod/ccs), staggered to avoid load spikes.
- Recommended cadence: every 15–30 min for load time/data checks (finer-grained than the current once-daily manual check, since automation makes this cheap).
- **Per-dashboard hard timeout**: cap each dashboard load (e.g. 60–90s) so one hung dashboard can't stall or overrun the whole cycle — record it as `load_status: failed` / panel `timeout` and move on.
- **Concurrency**: decide how many dashboards load in parallel per run. A small bounded pool keeps the cycle short without hammering the cluster; measure browser memory before raising it.
- **Load-time thresholds should be per-dashboard baselines**, not one flat number — a heavy dashboard and a light one have very different "normal." Seed baselines from the first week of collected data, then alert on deviation from baseline.
- Collector liveness: dead-man's-switch alert if no new `.dashboard-health-monitor` doc for a cluster within 2x the expected interval.
- **Retention**: apply an ILM policy to `.dashboard-health-monitor` sized to the trend window you actually need (e.g. 90–180 days) so the history index doesn't grow unbounded.

## 8. Testing & validation

- **Render-detection unit coverage**: snapshot the DOM/`data-test-subj` markers for each render state (ok/empty/error/timeout) from real dashboards and assert the parser classifies each correctly — this is the highest-risk logic, so it gets tests first.
- **Known-good / known-bad fixtures**: keep at least one dashboard known to have a healthy panel and one known to be empty/broken, so every deploy can be smoke-tested against a predictable result.
- **Idempotent re-runs**: two back-to-back runs of the collector should each write a clean, complete doc set with distinct `collector_run_id`s and no partial/duplicate writes.
- **Alert dry-run**: validate each Kibana Alerting rule against historical data (or an injected bad doc) before enabling notifications, to confirm it fires and recovers as expected.

## 9. Risks / open questions

- **Auth to Kibana for the browser path** — gates the Phase 1 MVP, not just enrichment (see Section 6). Resolve early.
- **Render-state detection fragility** — the core check depends on reading Kibana's own on-screen state; selectors are version-dependent and can shift on a Kibana upgrade. Mitigated by the Phase 0.5 spike, centralized selectors (Section 3.1), and render-detection tests (Section 8).
- **Panel-to-query resolution complexity** — Lens-based visualizations store query definitions differently than classic aggregation-based visualizations; the registry/resolver needs to handle both.
- **False positives on "empty" during legitimately quiet periods** — may need a per-panel expected-volume baseline rather than a flat "hit_count > 0" rule for low-traffic panels.
- **FedRAMP boundary** — confirm Playwright's headless Chromium install doesn't require anything outside your approved package/network allowlist.

## 10. Success criteria

- Zero manual daily dashboard checks required for the allow-listed dashboards.
- Alert fires within one collector cycle of a genuine load-time regression or empty panel.
- Historical load-time trend visible in Kibana (something the manual process never produced).
