# First real write on the prd appserver

The dry run is clean: 22 dashboards, no panel that is not ok. This is the first run that writes
to Elasticsearch.

**Order matters.** The setup script first, then the collector. A write before the index
template exists creates the data stream with default mappings, and the only way back is to
delete the data.

## 1. Pull

```bash
cd /opt/kibana_dashboard_runtime_monitor && git pull origin main
```

## 2. Apply the ILM policy and index template

```bash
sudo -u dhm env HOME=/var/lib/dhm bash -c \
  'cd /opt/kibana_dashboard_runtime_monitor && .venv/bin/python scripts/setup_elasticsearch.py'
```

Expect `Applied ILM policy: dashboard-health-monitor` and
`Applied index template: dashboard-health-monitor`. A 403 here means the account that owns the
API key lacks `manage_ilm` or `manage_index_templates`. Stop and tell me; do not run step 3.

## 3. Run the collector and write

```bash
sudo -u dhm env HOME=/var/lib/dhm bash -c \
  'cd /opt/kibana_dashboard_runtime_monitor && .venv/bin/python scripts/run_collector.py'
```

Expect one line per dashboard, then `Indexed 22 documents into dashboard-health-monitor`.

## 4. Check it in Kibana Dev Tools

```
GET _data_stream/dashboard-health-monitor
GET dashboard-health-monitor/_count
```

Expect `template` and `ilm_policy` both `dashboard-health-monitor`, and a count of 22.
A different template name, or no `ilm_policy`, means a write got in before the setup script.

## 5. Send the result

Paste the output of steps 2 and 3 into the chat as text. Do not commit it; do not upload it to
this repository.

## Note on the statuses

`degraded` starts at 30 s of load time and `failed` at 120 s (`degraded_over_ms` and
`failed_over_ms` under `collector:` in `config/settings.yaml`). In the dry run Software Overview
took 83 to 159 s, Priority Risks 31 to 67 s and Agency Details 27 to 39 s, so those will show as
degraded or failed until the thresholds are changed. Decide on that before turning on alerts.
