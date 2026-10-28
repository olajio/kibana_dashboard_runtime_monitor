# Picking this back up after time away

Use this when you return to the monitor after a break and need to get from "nothing
works" to "a clean dry run" without re-reading the whole HOWTO. For the full runbook
see [`HOWTO.md`](HOWTO.md); this file is the short path.

## Symptom: every value says `<-unset`

```
Connection: elasticsearch.api_key<-unset, elasticsearch.base_url<-unset,
            kibana.auth.api_key<-unset, kibana.base_url<-unset
```

Nothing is broken. The script only contacts AWS Secrets Manager when it knows the
secret's name, and nothing is telling it. That name comes from `aws_secret_id` in
`config/settings.yaml`, or from the `DHM_AWS_SECRET_ID` environment variable.

If you used `export DHM_AWS_SECRET_ID=...` last time, that only lasted for that
terminal session. A new login starts clean, so the setting is gone. Put it in a
settings file instead, which persists.

## Steps

### 1. Get the code and dependencies

```bash
cd /opt/kibana_dashboard_runtime_monitor
source .venv/bin/activate          # the prompt must start with (.venv)
git pull origin main
python -m pip install -r requirements.txt
```

Use `python -m pip`, not bare `pip`. The bare command can belong to a different
interpreter and install packages where the venv cannot see them.

If there is no `.venv` (a fresh clone or a rebuilt host):

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pytest -q                # a full pass confirms the install is sound
```

### 2. Create `config/settings.yaml`

This file is git-ignored and holds no secrets, only the pointer to the AWS secret.

```yaml
aws_secret_id: federal_store
aws_region: us-east-1
kibana_space: fed2
ca_bundle: /etc/pki/tls/certs/ca-bundle.crt   # needed behind the TLS-inspecting proxy

collector:
  registry_source: api                         # read the dashboard list live from Kibana
  # Browser: see step 3. Edge is the default, so add nothing if Edge is installed.
```

`ca_bundle` is what fixed the earlier `self signed certificate in certificate chain`
error. Drop it if the host talks to Elastic Cloud with no inspecting proxy in the way.

### 3. Make sure a browser is installed

The collector drives an already-installed browser and downloads nothing.

```bash
ls /opt/microsoft/msedge/msedge /opt/google/chrome/chrome /usr/bin/chromium 2>/dev/null
```

Add one matching line under `collector:` in `settings.yaml`:

| What is installed | Add this |
|---|---|
| Edge | nothing, it is the default |
| Chrome | `browser_channel: chrome` |
| distro chromium | `browser_executable: /usr/bin/chromium` |

Edge and Chrome are both Chromium, so render detection behaves the same on either.
If none of them is installed, follow [`LINUX_BROWSER_SETUP.md`](LINUX_BROWSER_SETUP.md)
(RHEL 8 on EC2: repo checks, install options, the service-account home directory, and
verification).

### 4. Dry run

```bash
python scripts/run_collector.py --dry-run --out run.json
```

`--dry-run` writes nothing to Elasticsearch. Check the first line:

| Marker | Meaning |
|---|---|
| `<-aws-bundle` | read from the secret, which is what you want |
| `<-unset` | the secret was not consulted, or the field is missing from it |
| `<-cli` | you passed a flag that overrides the secret |
| `<-settings` | it fell through to a literal value in `settings.yaml` |

`kibana.auth.api_key<-elasticsearch.api_key` is normal: one key serves both.

### 5. Write for real

Only once step 4 is clean:

```bash
python scripts/setup_elasticsearch.py    # ILM policy + index template (once per cluster)
python scripts/run_collector.py          # first real write creates the data stream
```

Expect `Indexed 22 documents into dashboard-health-monitor`.

When that is reliable, hand scheduling to Ansible with `deploy/dhm-deploy.yml`
(HOWTO §3B.4).

## If a step fails

| Error | Cause and fix |
|---|---|
| `ModuleNotFoundError: No module named 'yaml'` | Dependencies are not in the venv. Activate it, then `python -m pip install -r requirements.txt`. |
| `boto3 is not installed` | Same cause. `boto3` is in `requirements.txt`. |
| `NoCredentialsError` / `AccessDenied` | The host's AWS role cannot read the secret. It needs `secretsmanager:GetSecretValue` on `federal_store`. |
| `CERTIFICATE_VERIFY_FAILED` | Set `ca_bundle` as in step 2. HOWTO troubleshooting explains how to confirm the bundle with `curl` first. |
| `Chromium distribution 'msedge' is not found` | No browser installed. See step 3. |
| A library is missing when the browser starts | Find it with `ldd <browser> \| grep 'not found'` and `dnf install` it. Do **not** use `playwright install-deps` on RHEL: it only knows `apt-get`. See `LINUX_BROWSER_SETUP.md`. |
| `<-unset` after creating `settings.yaml` | Run from the repo root so `config/settings.yaml` resolves, or pass `--settings /full/path`. |

When asking for help, include the `Connection:` line. It usually localises the problem.

## Loose ends from last time

These were open when work paused and are easy to forget:

- **Rotate the pre-staging Elasticsearch API key.** It was pasted into a chat
  transcript and should be treated as exposed.
- **Pre-staging cron on the Mac** was set to every 5 minutes for testing. Revert it to
  every 30 minutes, or remove it once production is running.
- **Trend dashboard export** (the Kibana dashboard that displays the collected data)
  has not been committed. Export it from Kibana with related objects included and add
  it under `es/dashboards/`.
- **Alerting rules** in `es/alerting/` are written but not created in Kibana
  (HOWTO §4).
- **Not yet verified end to end on the real host:** a full non-dry-run write, the
  Ansible playbook, and the browser launch. The AWS secret, TLS and live discovery of
  all 22 dashboards were confirmed working.
