# HOW TO — Implement the Federal Overview Dashboard Health Monitor

A step-by-step runbook to stand this up from scratch, in the **test** environment
(Chrome, API key on the command line) and then in **production** (Edge, API key
from AWS Secrets Manager). Follow the steps in order; each has a "verify" check so
we know it worked before moving on.

Two environment differences are baked into the tooling, so the same code runs in
both:

| | Test | Production |
|---|---|---|
| Browser | Google **Chrome** (`browser_channel: chrome`) | Microsoft **Edge** (`browser_channel: msedge`, default) |
| ES API key | passed on the command line (`--es-api-key`) | read from **AWS Secrets Manager** |
| Dashboard list | static export (`registry_source: export`) | live Kibana API (`registry_source: api`) — no file on the server |
| Space (`DHM_SPACE`) | `fed2` | set per environment |

---

## 0. Prerequisites (once per machine)

- Python 3.9+
- The browser already installed: **Chrome** (test) or **Edge** (prod). No browser
  is downloaded.
- Network access from the machine to Kibana and Elasticsearch.
- An Elasticsearch API key with: **read** on the monitored Kibana space, and
  **write** to `.dashboard-health-monitor`.

```bash
git clone <this repo> && cd kibana_dashboard_runtime_monitor
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**Verify:** `python -m pytest -q` → all tests pass.

> If the `playwright` pip package is blocked in the boundary, use the Selenium
> backend instead — see [Appendix A](#appendix-a--selenium-fallback).

---

## 1. Registry (what we monitor) — two modes

The "registry" is just the list of dashboards to monitor and the panels expected on
each (so the collector can flag a **missing** panel). **Nothing is created or
changed in the cluster** — the "Federal Overview" dashboard and its linked
dashboards already exist and are left untouched. There are two ways to get the
list:

- **`api` mode (production):** query Kibana's Saved Objects API live on every run.
  No file on the server, and any dashboard/panel added, removed, or renamed is
  picked up automatically the next cycle. **This is the recommended production
  path** — see [3B](#3b-run-in-production-edge--aws-secrets-manager). Nothing to do
  in this step.
- **`export` mode (test / offline):** build a static manifest from a `.ndjson`
  export. Handy for a first run without hitting the API. Do this step only for
  `export` mode:

```bash
python scripts/build_registry.py federal_overview.ndjson \
    --app federal_overview \
    --out config/dashboards.generated.json
```

**Verify (export mode):** output shows `dashboards: 22` and `data panels total:
215`, and the hub is `Federal Overview`. Re-run it whenever the export changes —
this is exactly the staleness that `api` mode avoids.

---

## 2. Configure

```bash
cp config/settings.example.yaml config/settings.yaml
```

Edit `config/settings.yaml`:

- `kibana.base_url` — the Kibana URL.
- `elasticsearch.base_url` — the Elasticsearch URL.
- `kibana_space` — the space **ID** the dashboards live in. The space is displayed
  as "Federal"; its ID (the `/s/<id>` URL slug) is almost certainly `federal`.
  Confirm by opening the Federal Overview dashboard and reading the segment right
  after `/s/` in the URL.
- Leave `elasticsearch.api_key` **empty** — we supply it per-run (test) or via AWS
  (prod).

**Do not commit `config/settings.yaml`** — it is git-ignored.

---

## 3A. Run in the TEST environment (Chrome + key on the command line)

Set the browser to Chrome (either in `settings.yaml` or with an env var):

```bash
export DHM_BROWSER_CHANNEL=chrome
```

### 3A.1 Create the index (once per cluster)

```bash
python scripts/setup_elasticsearch.py --es-api-key "<id:key>"
```

**Verify:**

```bash
curl -s "$DHM_ES_URL/_index_template/dashboard-health-monitor" \
  -H "Authorization: ApiKey <id:key>" | head
```

### 3A.2 Smoke test the browser (render-detection spike)

```bash
python scripts/run_collector.py --es-api-key "<id:key>" --dry-run --out spike.json
```

**Verify:** open `spike.json` — panels have `render_ms` values and a mix of real
`render_status` values (`ok`, maybe `empty`). If everything is `timeout`/`missing`,
the DOM selectors need adjusting for the Kibana version — see
[Troubleshooting](#troubleshooting).

### 3A.3 Full dry run, then write for real

```bash
# collect all 22 dashboards, write nothing
python scripts/run_collector.py --es-api-key "<id:key>" --dry-run --out run.json

# looks good? write to Elasticsearch
python scripts/run_collector.py --es-api-key "<id:key>"
```

**Verify:**

```bash
curl -s "$DHM_ES_URL/.dashboard-health-monitor/_search?size=1" \
  -H "Authorization: ApiKey <id:key>" | python -m json.tool
```

---

## 3B. Run in PRODUCTION (Edge + AWS Secrets Manager + live discovery)

Edge is the default, so no browser setting is needed. Two production differences
from test: the key comes from AWS, and the dashboard list comes from Kibana's live
API — so **no `.ndjson` file is kept on the server** and dashboard/panel changes are
picked up automatically.

### 3B.0 Use live registry discovery

Set the registry source to `api` (in `settings.yaml` or via env):

```bash
export DHM_REGISTRY_SOURCE=api
# optional: monitor only specific dashboards (empty = every dashboard in the space)
# set collector.include_titles in settings.yaml, e.g. ["Federal Overview", ...]
```

No `build_registry.py` step and no export file are needed in production. Each run
re-reads the current dashboards and their panels from Kibana.

### 3B.1 Store the key in AWS Secrets Manager

Create a secret holding the Elasticsearch API key. Either form works:

- a **plain string** (the `id:key` value), or
- **JSON**: `{"api_key": "<id:key>"}`.

### 3B.2 Configure the secret in `settings.yaml`

```yaml
aws_region: us-gov-west-1            # or set AWS_REGION
elasticsearch:
  aws_secret_id: elastic/kibana/dhm-es      # the secret name/ARN
  aws_secret_json_key: api_key              # field name if the secret is JSON
```

The runner needs AWS credentials with `secretsmanager:GetSecretValue` on that
secret (instance role / task role / `AWS_PROFILE` — however this host normally
gets AWS access).

### 3B.3 Create the index, then run — **no `--es-api-key`**

```bash
python scripts/setup_elasticsearch.py          # key comes from AWS
python scripts/run_collector.py                # Edge + key from AWS
```

**Verify:** same search query as 3A.3 returns fresh documents.

> **Precedence recap:** `--es-api-key` > `DHM_ES_API_KEY` > AWS Secrets Manager.
> Passing `--es-api-key` in prod would override AWS, which is why we omit it.

---

## 4. Alerting

Add a notification connector id to each rule in `es/alerting/*.json`, then create
them in Kibana:

```bash
for rule in es/alerting/*.json; do
  curl -sS -X POST "$DHM_KIBANA_URL/api/alerting/rule" \
    -H "Authorization: ApiKey <kibana-id:key>" \
    -H "kbn-xsrf: true" -H "Content-Type: application/json" \
    -d @"$rule"
done
```

Rules: load degraded/failed, any unhealthy panel (`panels_not_ok > 0`), and a
collector dead-man's-switch. **Validate each rule fires before attaching
notifications** (Kibana → Stack Management → Rules → Run rule).

---

## 5. Schedule it

Run one cycle every 15–30 minutes. Example cron for **production** (key from AWS,
so nothing secret is on the command line):

```cron
*/20 * * * * cd /opt/dhm && /opt/dhm/.venv/bin/python scripts/run_collector.py >> /var/log/dhm.log 2>&1
```

Set the dead-man's-switch rule's window to 2× this interval (e.g. 40m).

---

## 6. Trend dashboard

In Kibana: create a data view over `.dashboard-health-monitor` (time field
`@timestamp`), then build load-time-over-time (split by `dashboard_title`),
per-panel render time (nested `panels.render_ms`), and a panel-health heatmap over
`panels.render_status`. Once it and the alerts have run cleanly for an agreed soak
period, retire the manual daily review.

---

## Operational notes (built in)

- **Timeouts:** each dashboard is capped at `collector.dashboard_timeout_ms` (90s);
  a hung dashboard is recorded `failed` and the cycle continues.
- **Politeness / request limits:** `collector.inter_request_delay_ms` (500ms) paces
  loads so Kibana is not hammered; dashboards load sequentially by default
  (`concurrency: 1`).
- **Retries:** a failed dashboard load is retried `collector.load_retries` (1) time;
  ES writes retry on 429/5xx/connection errors with exponential backoff
  (`elasticsearch.max_retries`, `retry_backoff_s`), honouring `Retry-After`.
- **Bulk sizing:** documents are written in chunks of `elasticsearch.bulk_chunk_size`
  (500) so no single `_bulk` request is oversized.
- **Isolation:** an unexpected error on one dashboard becomes a `failed` document,
  never an aborted run.
- **Retention:** the ILM policy rolls the data stream daily and deletes after 180
  days.

---

## Troubleshooting

- **All panels `timeout`/`missing`** — DOM selectors don't match this Kibana
  version. Everything version-specific is in `src/dhm/selectors.py`; adjust it and
  re-run the spike (step 3A.2).
- **`no Elasticsearch API key`** — pass `--es-api-key`, set `DHM_ES_API_KEY`, or set
  `elasticsearch.aws_secret_id` (+ AWS credentials).
- **`boto3 is not installed`** — the AWS path needs boto3 (`pip install -r
  requirements.txt` includes it), or pass the key on the command line instead.
- **Navigation/auth failures (`load_error`)** — check the browser can reach Kibana
  and the key/space is correct.
- **Selenium `WebDriverException`** — install `msedgedriver`/`chromedriver` on PATH
  or set `collector.webdriver_path`; the driver major version must match the browser.

---

## Appendix A — Selenium fallback

If the `playwright` pip package cannot be installed:

```bash
pip install -r requirements-selenium.txt
export DHM_BACKEND=selenium
# Edge (prod): ensure msedgedriver is on PATH, or set collector.webdriver_path
# Chrome (test): export DHM_BROWSER_CHANNEL=chrome  (chromedriver on PATH)
```

Everything else (steps 1–6) is identical — the Selenium backend produces the same
documents.
