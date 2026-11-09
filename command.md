# Dry run on the prd appserver

Read-only: it reads Kibana and runs the browser, and writes nothing to Elasticsearch.
Run it after `check_access.py` shows the `cdm-fdb` URL and counts that match your Dev Tools.

## 1. Pull and run as root

```bash
cd /opt/kibana_dashboard_runtime_monitor && git pull origin main
.venv/bin/python scripts/run_collector.py --dry-run --out run.json
```

## 2. Run as the `dhm` service account

```bash
sudo -u dhm env HOME=/var/lib/dhm bash -c \
  'cd /opt/kibana_dashboard_runtime_monitor && .venv/bin/python scripts/run_collector.py --dry-run --out /var/lib/dhm/run.json'
```

## 3. Send the result

Paste the summary lines into the chat as text: the dashboard count, and ok, degraded or failed
for each dashboard. Do not paste or commit `run.json`; it holds the full result, and this
repository must not receive pasted output.

If a browser or permission error appears, see the troubleshooting table in
`LINUX_BROWSER_SETUP.md` step 5.

## 4. After a clean dry run

The first write (`PRD_SETUP.md` step 7): the ILM policy and index template first, then the
collector. Do not run the collector before the setup script.
