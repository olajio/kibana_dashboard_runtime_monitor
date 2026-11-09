# Check the first write, then schedule it

The first write worked: `Indexed 22 documents into dashboard-health-monitor`.

## 1. Check what the write created

Your own account gets a 403 on `dashboard-health-monitor` in Dev Tools (its roles grant nothing
on that index), so check it with the collector's key, which can read it:

```bash
cd /opt/kibana_dashboard_runtime_monitor && git fetch origin && git reset --hard origin/main
.venv/bin/python scripts/check_access.py --verify-write
```

Expect `template=dashboard-health-monitor  ilm_policy=dashboard-health-monitor` and
`documents=22`. A different template name or no ILM policy means a write happened before the
setup script, and the data stream has default mappings.

## 2. See the data in Kibana

To open the data in Discover as yourself, grant your account the `dhm_collector` role (or a
read-only role on `dashboard-health-monitor*`) through your role mapping, then create a data
view on `dashboard-health-monitor*`.

## 3. Slow dashboards: decide the thresholds

`degraded` starts at 30 s of load time and `failed` at 120 s. In the real run:

| Dashboard | Load time | Status |
|---|---|---|
| Software Overview | 168.8 s | failed |
| Priority Risks | 100.8 s | degraded |
| Agency Details | 40.3 s | degraded |

Every panel on every dashboard was `ok`. If these are normal for the dashboards, raise
`degraded_over_ms` and `failed_over_ms` under `collector:` in `config/settings.yaml` before
turning on alerts. A full run takes about 6 minutes, so keep the schedule at 30 minutes or more.

## 4. Schedule it

Only after step 1 looks right. See `PRD_SETUP.md` step 8 (AAP) or run the playbook with
`dhm_cron_disabled=false`.

## 5. Send the result

Paste the output into the chat as text. Do not commit it or upload it to this repository.
