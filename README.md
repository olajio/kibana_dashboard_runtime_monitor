# Federal Overview — Dashboard Health & Load-Time Monitor

Automated, scheduled monitoring for the **Federal Overview** dashboard family.
It loads every dashboard in a headless browser, measures how long each dashboard
and each individual visualization takes to render, checks that every expected
panel actually returned data, and writes the results to Elasticsearch for
trending and alerting — replacing the manual daily review.

- **What we monitor:** the single `federal_overview.ndjson` export — 22
  interlinked dashboards and their 215 data panels — in one cluster and space.
- **What we measure:** per-dashboard load time, per-panel render time, and each
  panel's health (`ok | empty | error | timeout | missing`).

For the rationale and roadmap see
[`dashboard_health_monitor_project_plan.md`](dashboard_health_monitor_project_plan.md)
and the Jira breakdown in
[`dashboard_health_monitor_jira_tasks.md`](dashboard_health_monitor_jira_tasks.md).

---

## Repository layout

```
federal_overview.ndjson            # the saved-objects export we monitor (source of truth)
requirements.txt                   # base deps (Playwright backend)
requirements-selenium.txt          # extra deps for the Selenium fallback backend
config/
  settings.example.yaml            # copy to settings.yaml (git-ignored) and fill in
  dashboards.generated.json        # registry, produced from the export
src/dhm/
  config.py                        # settings + env overrides
  registry.py                      # parse the export into the registry
  selectors.py                     # centralized, version-sensitive Kibana DOM selectors
  render_detection.py              # classify panel health (pure, unit-tested)
  collect_core.py                  # backend-agnostic timing + document assembly
  collector.py                     # Playwright backend (thin driver over collect_core)
  collector_selenium.py            # Selenium fallback backend (same core)
  es_writer.py                     # write documents / apply ES assets
scripts/
  build_registry.py                # export -> config/dashboards.generated.json
  setup_elasticsearch.py           # create ILM policy + index template
  run_collector.py                 # run one collection cycle
es/
  index_template.json              # data stream mapping (nested panels)
  ilm_policy.json                  # retention (rollover daily, delete after 180d)
  alerting/*.json                  # three Kibana Alerting rule payloads
tests/                             # unit tests (registry + render detection)
```

The stages below run in order. Stages 1–4 stand up the collector; Stages 5–6
add alerting and the trend dashboard.

---

## Prerequisites

- Python 3.9+
- Network access from the runner to Kibana and Elasticsearch
- An automation credential for Kibana (see [Stage 2](#stage-2--configure--set-up-elasticsearch))
- **Microsoft Edge or Google Chrome already installed.** The collector drives the
  existing system browser via Playwright channels — it does **not** download a
  browser. Edge is the default (`msedge`); Chrome (`chrome`) is typical in test.

---

## Install

```bash
pip install -r requirements.txt
```

That is all: no browser download. The `playwright` pip package ships its own
driver, and the collector launches the already-installed Edge/Chrome selected by
`collector.browser_channel` (see Stage 2).

> Only if a policy requires Playwright's bundled Chromium instead of the system
> browser: set `collector.browser_channel: chromium` and run
> `python -m playwright install chromium` from the approved mirror. Using the
> org-managed Edge/Chrome is the recommended path.

### Fallback: Selenium backend

If the `playwright` pip package itself cannot be installed in the boundary, use
the Selenium backend instead — it drives the same system Edge/Chrome and runs the
same render-detection logic (only the browser plumbing differs):

```bash
pip install -r requirements-selenium.txt
```

Then set `collector.backend: selenium` (Stage 2). Selenium needs the matching
WebDriver — `msedgedriver` for Edge or `chromedriver` for Chrome — on `PATH`, or
set `collector.webdriver_path`. Edge ships a managed `msedgedriver`, which is
usually the easiest driver to get approved.

---

## Stage 1 — Build the registry from the export

The registry is the exact list of what we monitor, derived from the export.

```bash
python scripts/build_registry.py federal_overview.ndjson \
    --app federal_overview \
    --out config/dashboards.generated.json
```

Expected output:

```
Wrote config/dashboards.generated.json
  app:               federal_overview
  hub dashboard:     7a339ff0-09ce-11ed-9940-d955314b400b
  dashboards:        22
  data panels total: 215
```

**Validate (automated):**

```bash
python -m pytest tests/test_registry.py -q
```

**Validate (manual):** open `config/dashboards.generated.json` and confirm the
hub is "Federal Overview", 22 dashboards are present, and each dashboard lists its
panels with `is_data_panel` set correctly.

**When the dashboards change:** re-export to `federal_overview.ndjson`, re-run the
command above, and review the diff of `config/dashboards.generated.json` — added or
removed panels show up there — then commit.

---

## Stage 2 — Configure & set up Elasticsearch

### Configure

```bash
cp config/settings.example.yaml config/settings.yaml
# edit config/settings.yaml — or supply secrets via environment variables
```

`config/settings.yaml` is git-ignored. Every value has an environment-variable
override (shown in the example file), so on cron/CI we can keep secrets out of the
file entirely, e.g.:

```bash
export DHM_KIBANA_URL="https://kibana.example.gov"
export DHM_KIBANA_API_KEY="<base64 id:key>"
export DHM_ES_URL="https://es.example.gov:9200"
export DHM_ES_API_KEY="<base64 id:key>"
```

### Authentication

The headless browser needs an authenticated Kibana session. Pick one method in
`settings.yaml` under `kibana.auth`:

- `api_key` — injected as `Authorization: ApiKey <key>`. Simplest when Kibana
  accepts API-key auth for automation.
- `cookie` — a pre-obtained session cookie (`cookie_name` + `cookie_value`), for
  PKI/SAML-only Kibana where we use a service-account session.

The credential needs only **read** on the monitored space and **write** to
`.dashboard-health-monitor`.

### Choose the browser

Set `collector.browser_channel` in `settings.yaml` (or `DHM_BROWSER_CHANNEL`) to
the browser already installed on the runner:

- `msedge` — system Microsoft Edge (default; the production environment's browser)
- `chrome` — system Google Chrome (e.g. the test environment)
- `chromium` — Playwright's own downloaded Chromium (only if the system browser
  cannot be used; requires `python -m playwright install chromium`)

Because Edge and Chrome are both Chromium-based, the render-detection logic is
identical across them — only the launch target changes.

`collector.backend` selects how the browser is driven: `playwright` (default) or
`selenium` (the fallback described under [Install](#fallback-selenium-backend)).
Both backends honour `browser_channel` and produce identical documents.

### Set up the index (once per cluster)

```bash
python scripts/setup_elasticsearch.py --settings config/settings.yaml
```

This creates the ILM policy and the data-stream index template.

**Validate:**

```bash
curl -s "$DHM_ES_URL/_index_template/dashboard-health-monitor" -H "Authorization: ApiKey $DHM_ES_API_KEY" | head
curl -s "$DHM_ES_URL/_ilm/policy/dashboard-health-monitor"     -H "Authorization: ApiKey $DHM_ES_API_KEY" | head
```

---

## Stage 3 — Render-detection spike (do this once, early)

Before we trust the collector across all 22 dashboards, we confirm the DOM signals
in `src/dhm/selectors.py` match our Kibana version. With the Kibana auth from
Stage 2 in place, point the collector at the dashboards in a dry run (nothing is
written to ES) and inspect the result:

```bash
python scripts/run_collector.py --dry-run --out spike.json
```

Open `spike.json` and confirm, for at least one dashboard, that panels report
`render_ms` values and a mix of real `render_status` values. If panels come back
empty or all `timeout`, the selectors need updating for our version — adjust
`selectors.py` only (everything version-specific lives there) and re-run.

---

## Stage 4 — Run a collection cycle

Dry run first — collect everything, write nothing to ES:

```bash
python scripts/run_collector.py --dry-run --out run.json
```

Per-dashboard progress prints as it goes:

```
Collecting 22 dashboards for app 'federal_overview' (cluster=fed2, space=default)
  Federal Overview                         ok         2841ms ok=6 not_ok=0
  Agency Details                           degraded  16522ms ok=10 not_ok=1
  ...
```

**Validate (manual):** open `run.json` and confirm each of the 22 dashboards has a
sensible `load_time_ms`, a `panels` array with `render_ms` and `render_status` per
panel, and correct rollups (`panels_ok`, `panels_not_ok`, `panels_missing`, …).

Then run for real (writes to Elasticsearch):

```bash
python scripts/run_collector.py
```

**Validate:**

```bash
curl -s "$DHM_ES_URL/.dashboard-health-monitor/_search?size=1" \
  -H "Authorization: ApiKey $DHM_ES_API_KEY" | python -m json.tool
```

---

## Testing

Unit tests cover the two browser-free, high-risk pieces and run in under a second:

```bash
python -m pytest -q
```

- `tests/test_registry.py` — parses the real export and asserts 22 dashboards,
  the hub, panel counts, 215 data panels, and data/nav classification.
- `tests/test_render_detection.py` — asserts every render status classifies
  correctly (including error-over-empty precedence and missing-panel
  reconciliation) from raw signal fixtures.
- `tests/test_collect_core.py` — drives the shared collection core with a fake
  browser driver: load status, per-panel timing, missing-panel and nav-failure
  handling, and URL building. This core is what both backends call.

Only the browser plumbing in `collector.py` / `collector_selenium.py` needs a live
Kibana/ES; it is validated by the Stage 4 dry run and live run above.

---

## Stage 5 — Alerting

Three Elasticsearch Query rules live in `es/alerting/` as ready-to-POST payloads.
Add a notification connector id to each rule's `actions` array, then create them
in Kibana:

```bash
KIBANA="$DHM_KIBANA_URL"
for rule in es/alerting/*.json; do
  curl -sS -X POST "$KIBANA/api/alerting/rule" \
    -H "Authorization: ApiKey $DHM_KIBANA_API_KEY" \
    -H "kbn-xsrf: true" -H "Content-Type: application/json" \
    -d @"$rule"
done
```

- `load_time_rule.json` — fires when any dashboard is `degraded`/`failed`.
- `panel_health_rule.json` — fires when any dashboard has `panels_not_ok > 0`.
- `dead_mans_switch_rule.json` — fires when no document is written within 2x the
  cadence (set `timeWindowSize` to 2x the real cron interval).

**Validate before enabling notifications:** run the collector a few times so there
is data, then confirm each rule evaluates as expected in Kibana → **Stack
Management → Rules** (use "Run rule" / check the rule's last status). Only attach
the notification connector once the rule fires and recovers correctly.

---

## Stage 6 — Trend dashboard

1. In Kibana, create a **data view** over `.dashboard-health-monitor`
   (time field `@timestamp`).
2. Build a dashboard with:
   - Load time over time, split by `dashboard_title` (line).
   - Per-panel render time (use the nested `panels` — a Lens on `panels.render_ms`
     split by `panels.panel_title`).
   - A panel-health heatmap over `panels.render_status`.
   - Top-line tiles: dashboards `failed`/`degraded` now, panels `not_ok` now.
3. Once this dashboard and the alerts have run cleanly for an agreed soak period,
   retire the manual daily review.

---

## Scheduling

Run one cycle every 15–30 minutes. Example cron (secrets sourced from the
environment):

```cron
*/20 * * * * cd /opt/dhm && /opt/dhm/.venv/bin/python scripts/run_collector.py >> /var/log/dhm.log 2>&1
```

The per-dashboard hard timeout (`collector.dashboard_timeout_ms`, default 90s)
ensures one hung dashboard never stalls the cycle. Raise `collector.concurrency`
only after measuring browser memory on the runner.

---

## Troubleshooting

- **All panels come back `timeout` or `missing`** — the DOM selectors do not match
  this Kibana version. Everything version-specific is in `src/dhm/selectors.py`;
  update it there and re-run the Stage 3 spike. Nothing else should need changing.
- **Navigation/auth failures (`load_error` set)** — check the auth method and that
  the credential can read the space. For `cookie` auth, confirm the session cookie
  is still valid.
- **Browser not found on launch** — the selected `browser_channel` is not installed.
  Confirm Edge (`msedge`) or Chrome (`chrome`) is present, or point at the binary
  directly. The default drives the org-managed system browser, so no download is
  needed. Only if a policy forces Playwright's bundled Chromium
  (`browser_channel: chromium`) does `python -m playwright install chromium` apply —
  run it from the approved mirror and confirm it stays inside the package/network
  allowlist.
- **Selenium: `WebDriverException` / driver not found** — on the Selenium backend,
  install `msedgedriver` (Edge) or `chromedriver` (Chrome) on `PATH` or set
  `collector.webdriver_path`. The driver major version must match the installed
  browser's major version.
- **TLS to a lab cluster** — set `elasticsearch.verify_tls: false` (or
  `DHM_ES_VERIFY_TLS=false`) for non-production only.

---

## Security notes

- No secrets in the repo: `config/settings.yaml` is git-ignored and every secret
  has an environment override.
- The automation identity is least-privilege (read the monitored space, write the
  results data stream) and on a defined rotation cadence.
- The results data stream contains dashboard/panel titles and timing only — no
  underlying record data.
