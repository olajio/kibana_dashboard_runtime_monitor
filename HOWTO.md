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

- Python 3.10+ (required by the pinned Playwright version)
- The browser already installed: **Chrome** (test) or **Edge** (prod). No browser
  is downloaded.
- Network access from the machine to Kibana and Elasticsearch.
- An Elasticsearch API key with: **read** on the monitored Kibana space, and
  **write** to `dashboard-health-monitor`. See
  [§0.1 API key privileges](#01-api-key-privileges) for the exact set — getting this
  wrong is the most common cause of a fleet-wide false failure.

```bash
git clone <this repo> && cd kibana_dashboard_runtime_monitor
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**Verify:** `python -m pytest -q` → all tests pass.

> If the `playwright` pip package is blocked in the boundary, use the Selenium
> backend instead — see [Appendix A](#appendix-a--selenium-fallback).

---

### 0.1 API key privileges

The collector authenticates to **both** Elasticsearch and Kibana with the same API
key, so the key needs three distinct kinds of privilege. A ready-to-post request is
in [`es/api_key_collector.json`](es/api_key_collector.json) — paste it into Dev Tools
after `POST /_security/api_key`.

| What the collector does | Privilege it needs |
|---|---|
| `PUT /_ilm/policy/dashboard-health-monitor` (setup only) | cluster `manage_ilm` |
| `PUT /_index_template/dashboard-health-monitor` (setup only) | cluster `manage_index_templates` |
| `POST /_bulk` with `create` into the data stream | index `create_doc`, `auto_configure` on `dashboard-health-monitor` |
| `GET /s/fed2/api/saved_objects/_find` (live discovery) | Kibana application `feature_dashboard.read` on `space:fed2` |
| Browser opens each dashboard and every panel runs its own searches | Kibana `feature_dashboard.read` on `space:fed2` **plus** ES `read` + `view_index_metadata` on every index those panels query |

Three things are easy to get wrong:

- **Kibana access is *not* an index privilege.** Kibana authorizes through
  *application* privileges under the application name `kibana-.kibana`. A key with
  `"applications": []` has no Kibana access at all, however broad its cluster and
  index privileges are — the Saved Objects call and the browser's dashboard loads
  both 403.
- **The `manage` index privilege cannot read or write documents.** It covers index
  *administration* (settings, mappings, aliases, refresh, open/close) plus all
  `monitor` privileges. Writing needs `create_doc` (or `write`); reading needs
  `read`. A key holding only `manage` fails `_bulk` with
  `action [indices:data/write/bulk] is unauthorized`.
- **Roughly half the data views are cross-cluster.** 13 of the 31 data views behind
  these dashboards are CCS patterns (`agency-dashboard*:cdm_…`). Local `read` does
  not cover them — they need `read_cross_cluster` on the remote side. The exact
  syntax depends on which remote-cluster trust model this deployment uses: the
  `remote_indices` block in `api_key_collector.json` is the API-key-based model
  (ES 8.14+); a certificate-based trust instead requires the role to exist with
  `read` + `read_cross_cluster` on the **remote** cluster. Confirm with whoever owns
  the remote cluster configuration.

**The reliable shortcut:** the analysts who review these dashboards by hand already
have exactly the read access the collector needs. Mirror their role's read
privileges and add only `create_doc` + `auto_configure` on
`dashboard-health-monitor`. That sidesteps having to re-derive the CCS setup.

**Tightening after setup:** the two cluster privileges are only used by
`scripts/setup_elasticsearch.py`, which runs once per cluster. Run that step with an
admin credential and drop `"cluster"` to `[]` on the long-lived collector key, so
the key that sits in Secrets Manager and runs every 30 minutes can create no
templates and no policies.

**Validating a new key** — swap it in and compare against a known-good run:

```bash
python scripts/run_collector.py --es-api-key "<new id:key>" --dry-run --out newkey.json
```

A privilege gap shows up as a *sharp* change in shape, not a subtle one: a Kibana
application-privilege gap makes every dashboard `failed` with a `load_error`, and a
missing ES `read` makes panels `empty` en masse while load times stay fast. Compare
the per-status counts to the previous run before trusting the new key.

> **Note on `expiration`.** A key created with `"expiration": "365d"` stops working
> on day 365 with no warning — the collector simply starts 403ing. Put the rotation
> in the calendar ahead of that date. Because the key is read from Secrets Manager,
> rotation is a secret update with no redeploy and no file edit on the server.

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
- `kibana_space` — the space **ID** the dashboards live in. This is **`fed2`**, which
  is also the built-in default in code, so it normally needs no change. It is the
  `/s/<id>` URL slug, not the display name — confirm by opening the Federal Overview
  dashboard and reading the segment right after `/s/` in the URL. Override per
  environment with `DHM_SPACE`.
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
# collect the monitored dashboards (default: Federal Overview + all linked), write nothing
python scripts/run_collector.py --es-api-key "<id:key>" --dry-run --out run.json

# looks good? write to Elasticsearch
python scripts/run_collector.py --es-api-key "<id:key>"
```

**Verify:**

```bash
curl -s "$DHM_ES_URL/dashboard-health-monitor/_search?size=1" \
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
# Which dashboards to monitor (collector.selection, default 'linked'):
#   - linked (default) -> the "Federal Overview" hub + every dashboard reachable
#                         from its navigation (the dashboards linked from that page)
#   - titles           -> exactly collector.include_titles
#   - all              -> every dashboard in the space
# Override via DHM_SELECTION / DHM_HUB_TITLE / DHM_INCLUDE_TITLES.
```

No `build_registry.py` step and no export file are needed in production. Each run
re-reads the current dashboards and their panels from Kibana, then applies the
selection.

### 3B.1 Store the connection settings in AWS Secrets Manager

Production holds nothing environment-specific and nothing sensitive on disk. One
secret carries the Kibana URL, the Elasticsearch URL and the API key as a JSON
object:

```bash
aws secretsmanager create-secret \
  --name elastic/dhm/connection \
  --region us-east-1 \
  --description "Dashboard Health Monitor: Kibana/ES endpoints + API key" \
  --secret-string '{
    "kibana_url": "https://<host>.kb.<domain>:9243",
    "es_url":     "https://<host>.es.<domain>:9243",
    "api_key":    "<base64 id:key>"
  }'
```

Two things to get right:

- **`kibana_url` and `es_url` are different hosts** (typically `...kb...` vs
  `...es...`). Pointing both at Kibana makes every `_bulk` write 404 — we hit this
  in pre-staging. The collector now warns when the two resolve to the same host.
- **`api_key`** is the base64 `id:key` value, the same string we pass to
  `--es-api-key` in test. Add `"kibana_api_key"` only if Kibana needs a *different*
  key; otherwise `api_key` is used for both.

To rotate, update this one secret — no redeploy, no file edit on the server.

### 3B.2 Point the deployment at the secret

Only two values, and both can come from the environment, so Ansible does not have
to template a settings file at all:

```yaml
aws_region: us-east-1                # [DHM_AWS_REGION] the default; AWS_REGION also works
aws_secret_id: elastic/dhm/connection # [DHM_AWS_SECRET_ID] the secret name or ARN
```

Leave `kibana.base_url`, `elasticsearch.base_url` and every `api_key` field empty
— the bundle supplies them. (If `settings.yaml` does carry URLs, the bundle wins:
the file holds defaults, the secret is the source of truth.)

The runner needs AWS credentials with `secretsmanager:GetSecretValue` on that
secret (instance role / task role / `AWS_PROFILE` — however this host normally gets
AWS access):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "secretsmanager:GetSecretValue",
    "Resource": "arn:aws:secretsmanager:us-east-1:<account-id>:secret:elastic/dhm/connection-*"
  }]
}
```

### 3B.3 Create the index, then run — **no URLs, no keys on the command line**

```bash
python scripts/setup_elasticsearch.py          # URL + key from AWS
python scripts/run_collector.py                # Edge; URL + key from AWS
```

Each run opens with a secret-free provenance line, so we can confirm the values
really came from AWS:

```
Connection: elasticsearch.api_key<-aws-bundle, elasticsearch.base_url<-aws-bundle, kibana.auth.api_key<-elasticsearch.api_key, kibana.base_url<-aws-bundle
```

`kibana.auth.api_key<-elasticsearch.api_key` is the expected reading when one
Elastic key authorizes both. Anything showing `<-settings` that we meant to come
from AWS means the field is missing from the secret (check the field names against
`aws_secret_keys`).

**Verify:** same search query as 3A.3 returns fresh documents.

> **Precedence recap:** CLI flag > env var > AWS bundle > value-specific AWS
> secret > `settings.yaml`. Passing `--es-api-key` or `--kibana-url` in prod would
> override AWS, which is why we omit them. A one-off override is still available
> for debugging: `python scripts/run_collector.py --kibana-url https://... --dry-run`.

### 3B.4 Deploying with Ansible

The repo is the deployment artifact — clone it to the existing server, install the
requirements into a virtualenv, drop in a `settings.yaml` (or just the two AWS env
vars), and schedule it with cron/systemd. A minimal sketch:

```yaml
- name: Deploy the Dashboard Health Monitor
  hosts: dhm_runners
  vars:
    dhm_root: /opt/dashboard-health-monitor
    dhm_aws_secret_id: elastic/dhm/connection
    dhm_aws_region: us-east-1
  tasks:
    - name: Check out the repository
      ansible.builtin.git:
        repo: https://github.com/olajio/kibana_dashboard_runtime_monitor.git
        dest: "{{ dhm_root }}"
        version: main

    - name: Install Python requirements
      ansible.builtin.pip:
        requirements: "{{ dhm_root }}/requirements.txt"
        virtualenv: "{{ dhm_root }}/.venv"
        virtualenv_command: python3 -m venv

    - name: Install settings (no secrets; endpoints come from AWS)
      ansible.builtin.template:
        src: settings.yaml.j2
        dest: "{{ dhm_root }}/config/settings.yaml"
        mode: "0644"

    - name: Schedule the collection cycle
      ansible.builtin.cron:
        name: dashboard-health-monitor
        minute: "*/30"
        job: >-
          cd {{ dhm_root }} &&
          DHM_AWS_SECRET_ID={{ dhm_aws_secret_id }}
          DHM_AWS_REGION={{ dhm_aws_region }}
          .venv/bin/python scripts/run_collector.py
          >> /var/log/dhm/collector.log 2>&1
```

Because `settings.yaml` now holds no endpoints and no credentials, it is safe to
template from the repo's `config/settings.example.yaml` and commit the Jinja
template alongside the playbook. Set `collector.browser_channel: msedge` and
`collector.registry_source: api` for production.

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

### 6.1 Create the data view in Kibana

1. Kibana → **Stack Management → Data Views → Create data view**.
2. **Name:** `Dashboard Health & Load-Time Monitor` (or anything).
3. **Index pattern:** `dashboard-health-monitor*` (asterisk matches every ILM
   rollover of the data stream).
4. **Timestamp field:** `@timestamp`.
5. Save.

If the pattern picker doesn't list any matching stream, run one live cycle
first — the data stream is created by the first `_bulk` write, not by
`setup_elasticsearch.py`. Then reload the picker.

### 6.2 Build the dashboard

- Load-time over time — Lens on `load_time_ms`, split by `dashboard_title`.
- Per-panel render time — Lens on nested `panels.render_ms`, split by
  `panels.panel_title`.
- Panel-health heatmap — count over `panels.render_status`.
- Top-line tiles — "dashboards failed now", "panels not_ok now" from the
  top-level rollups.

Once it and the alerts have run cleanly for an agreed soak period, retire the
manual daily review.

### 6.3 Migrating from a previous `.dashboard-health-monitor` stream

Older versions used a dot-prefixed name that Kibana hides from the data-view
picker. If a stream by that old name already exists, delete it and its template
before applying the new ones:

```bash
curl -sS -X DELETE "$DHM_ES_URL/_data_stream/.dashboard-health-monitor" \
  -H "Authorization: ApiKey <id:key>"
curl -sS -X DELETE "$DHM_ES_URL/_index_template/dashboard-health-monitor" \
  -H "Authorization: ApiKey <id:key>"
python scripts/setup_elasticsearch.py --es-api-key "<id:key>"
python scripts/run_collector.py --es-api-key "<id:key>"        # writes first docs
```

Historical data on the old stream is lost by the delete — but at this point in
rollout there shouldn't be much of it yet. If keeping the old data matters, use
[Reindex API](https://www.elastic.co/guide/en/elasticsearch/reference/current/docs-reindex.html)
into the new stream before deleting.

---

## Operational notes (built in)

- **Timeouts:** each dashboard is capped at `collector.dashboard_timeout_ms`
  (180s, sized for `time_from=now-30d` and heavy dashboards); a hung dashboard is
  recorded `failed` and the cycle continues.
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
- **Retention:** the ILM policy rolls the data stream monthly (or at 5 GB) and
  deletes after 365 days — roughly one year of historical performance kept per
  cluster. Tune both in `es/ilm_policy.json`.

---

## Troubleshooting

- **`pip install` fails building `greenlet` (e.g. `"this header requires
  Py_BUILD_CORE define"`, or a C++ compile error under `TMainGreenlet.cpp`)** —
  the installed Playwright version is too old for the Python version in use, so
  pip falls back to compiling an incompatible `greenlet` from source instead of
  using a prebuilt wheel. `requirements.txt` pins a Playwright version that
  supports current Python releases; make sure the environment is on that pinned
  version (`pip install -r requirements.txt` again, in a clean virtualenv), and
  confirm Python is 3.10+.
- **All panels `timeout`/`missing`** — DOM selectors don't match this Kibana
  version. Everything version-specific is in `src/dhm/selectors.py`; adjust it and
  re-run the spike (step 3A.2). To see exactly what the browser sees on this
  version, run the diagnostic: `python scripts/debug_spike.py --es-api-key
  "$APIKEY" --dashboard-title "Federal Overview" --out debug.json`. Compare the
  observed attributes to what `selectors.py` expects and adjust. Kibana 8.19
  needed us to follow `aria-labelledby` for titles and add a DOM-order fallback,
  both included.
- **`no Elasticsearch API key`** — pass `--es-api-key`, set `DHM_ES_API_KEY`, or set
  `aws_secret_id` / `elasticsearch.aws_secret_id` (+ AWS credentials).
- **`no Kibana URL` / `no Elasticsearch URL`** — pass `--kibana-url` / `--es-url`,
  set `DHM_KIBANA_URL` / `DHM_ES_URL`, add `kibana_url` / `es_url` to the AWS
  bundle, or set them in `settings.yaml`. The provenance line printed at the top of
  every run says which source each value actually came from.
- **`is not a JSON object`** — the secret named by `aws_secret_id` holds a bare
  string. The bundle must be a JSON object (`{"kibana_url": ..., "api_key": ...}`).
  For a secret that holds only a key, use `elasticsearch.aws_secret_id` instead.
- **Values show `<-settings` when they should come from AWS** — the field is missing
  from the secret, or named differently; check it against `aws_secret_keys`.
- **`boto3 is not installed`** — the AWS path needs boto3 (`pip install -r
  requirements.txt` includes it), or pass the values on the command line instead.
- **`both ... 404 on _bulk`** — the warning means Kibana's and Elasticsearch's URLs
  resolved to the same host. Fix `es_url` in the secret (`...es...`, not `...kb...`).
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
