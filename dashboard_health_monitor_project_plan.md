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

## 4. Data model

Index: `.dashboard-health-monitor`

Design principle: every field below is populated purely from what the browser observes when the dashboard loads — dashboard ID, panel ID/title, and on-screen render state. Nothing requires knowing or resolving a panel's underlying query, index, or data view. This is what makes it generic across any dashboard you point it at.

```json
{
  "@timestamp": "<run timestamp>",
  "cluster": "<dev | qa | prod | ccs>",
  "dashboard_id": "<saved object id>",
  "dashboard_title": "<dashboard name, as shown in Kibana>",
  "load_time_ms": <int>,
  "load_status": "ok | degraded | failed",
  "panel_count": <int>,
  "panels": [
    {
      "panel_id": "<panel/embeddable id — this comes from the dashboard's own layout, not a query>",
      "panel_title": "<panel title as displayed>",
      "panel_type": "<lens | visualization | map | saved_search | etc. — descriptive only>",
      "render_status": "ok | empty | error | timeout",
      "render_status_detail": "<Kibana's own on-screen message, e.g. 'No results found', if shown>"
    }
  ],
  "collector_run_id": "<uuid>",
  "check_duration_ms": <int>
}
```

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
| **Phase 1 — Core collector** | `dashboard_health_check.py`: Playwright authenticates to Kibana, loads each dashboard URL, waits for panel-loaded state, records load time, and reads each panel's rendered state (ok/empty/error/timeout) straight off the page. Logs to `.dashboard-health-monitor` | This is the whole MVP — load time + data-presence, both from the same page load, no query knowledge needed. Auth is the main build item here — see Section 6 |
| **Phase 2 — Optional query enrichment** | For panels where the underlying query/data view is known and easy to resolve, add a direct-ES-query lookup for hit count + freshness | Purely additive; skip for panels where this isn't convenient |
| **Phase 3 — Alerting** | Kibana Alerting rules (Elasticsearch Query rule type, per your CCS project's stated preference over Watcher): load time > threshold, `data_status: empty/stale`, and a collector dead-man's-switch (no new doc in expected interval) | Reuse the four-layer degradation / dead-man's-switch pattern from Project 13 |
| **Phase 4 — Trend dashboard & rollout** | Kibana dashboard over `.dashboard-health-monitor` (load time trend, panel health heatmap); expand from allow-list to full dynamic discovery; roll out across dev/qa/prod/ccs | Final step — dashboard-of-dashboards |

## 6. Auth strategy (the one real open question)

- **Direct-query path (Phase 1)**: use an API key via Secrets Manager, same pattern as your other tooling (`elastic/kibana/...` secret naming convention). No browser involved, so no SSO complexity.
- **Browser path (Phase 2)**: Playwright needs an authenticated Kibana session.
  - If Kibana auth allows API-key-based basic auth or a service account bypass for internal automation, this is straightforward — inject the API key as a request header before navigation.
  - If it's PKI/SAML-only, the cleanest option is a dedicated automation service account with a long-lived Kibana session token/cookie that the script injects directly (via `context.add_cookies()` in Playwright) rather than scripting the interactive login flow.
  - Recommend confirming with Jesse/Cloud Automation whether RolesAnywhere or an existing service identity can front this before building the login flow.

## 7. Scheduling & deployment

- Cron (or AWX job, consistent with your `find_duplicate_dataviews_awx.py` pattern) — run per cluster (dev/qa/prod/ccs), staggered to avoid load spikes.
- Recommended cadence: every 15–30 min for load time/data checks (finer-grained than the current once-daily manual check, since automation makes this cheap).
- Collector liveness: dead-man's-switch alert if no new `.dashboard-health-monitor` doc for a cluster within 2x the expected interval.

## 8. Risks / open questions

- **Auth to Kibana for the browser path** — needs resolution before Phase 2 (see Section 6).
- **Panel-to-query resolution complexity** — Lens-based visualizations store query definitions differently than classic aggregation-based visualizations; the registry/resolver needs to handle both.
- **False positives on "empty" during legitimately quiet periods** — may need a per-panel expected-volume baseline rather than a flat "hit_count > 0" rule for low-traffic panels.
- **FedRAMP boundary** — confirm Playwright's headless Chromium install doesn't require anything outside your approved package/network allowlist.

## 9. Success criteria

- Zero manual daily dashboard checks required for the allow-listed dashboards.
- Alert fires within one collector cycle of a genuine load-time regression or empty panel.
- Historical load-time trend visible in Kibana (something the manual process never produced).
