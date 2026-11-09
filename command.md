# First real write on the prd appserver

Run these on the prd host after the key has been rotated and `check_access.py` shows
`authenticated as: svc_dhm` and `write access to dashboard-health-monitor: yes`.

The setup script already ran (ILM policy and index template), so it does not repeat.

## 1. Check the key still works

```bash
cd /opt/kibana_dashboard_runtime_monitor
.venv/bin/python scripts/check_access.py --patterns cdm_aim
```

## 2. Run the collector, which writes

```bash
sudo -u dhm env HOME=/var/lib/dhm bash -c \
  'cd /opt/kibana_dashboard_runtime_monitor && .venv/bin/python scripts/run_collector.py'
```

Expect one line per dashboard, then `Indexed 22 documents into dashboard-health-monitor`.

## 3. Check it in Kibana Dev Tools

```
GET _data_stream/dashboard-health-monitor
GET dashboard-health-monitor/_count
```

Expect `template` and `ilm_policy` both `dashboard-health-monitor`, and a count of 22.
A different template name, or no `ilm_policy`, means a write happened before the setup script.

## 4. Send the result

Paste the output of steps 1 and 2 into the chat as text. Do not commit it, do not upload it to
this repository, and do not export the Dev Tools history: it records request bodies and key
responses.

## After a clean write

The repository history was rewritten to remove pasted output. On every host that has a clone
(the dev and prd appservers, and the AAP project's source), run
`git fetch origin && git reset --hard origin/main`. A plain `git pull` will refuse.
