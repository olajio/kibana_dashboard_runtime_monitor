# Fix: the collector's key is refused the first write

## What happened

The setup script worked (`Applied ILM policy`, `Applied index template`) and the collector read
all 22 dashboards, but the write failed:

```
action [indices:admin/auto_create] is unauthorized for API key id [...] of user [<your account>]
on indices [dashboard-health-monitor], this action is granted by the index privileges
[auto_configure,create_index,manage,all]
```

An API key can never hold more than the account that created it. The key's own role lists
`auto_configure`, `create_doc` and `create_index` on `dashboard-health-monitor*`, but your
account's nine roles grant nothing on that index, so the key ends up with none either. Your own
Dev Tools returns 403 on that index for the same reason. Nothing was written.

The fix is a role and a service user that hold exactly what the monitor needs, and a key created
as that user. It also stops the monitor depending on a person's account.

## 1. In Kibana Dev Tools on `cdm-fdb`: the role

```
PUT _security/role/dhm_collector
{
  "cluster": [
    "manage_index_templates",
    "manage_ilm"
  ],
  "indices": [
    {
      "names": [
        "dashboard-health-monitor*"
      ],
      "privileges": [
        "create_doc",
        "auto_configure",
        "create_index",
        "read",
        "view_index_metadata"
      ]
    },
    {
      "names": [
        "cdm_*",
        "data_dictionary"
      ],
      "privileges": [
        "read",
        "view_index_metadata"
      ]
    }
  ],
  "remote_indices": [
    {
      "clusters": [
        "agency-dashboard*"
      ],
      "names": [
        "cdm_*"
      ],
      "privileges": [
        "read",
        "read_cross_cluster",
        "view_index_metadata"
      ]
    }
  ],
  "applications": [
    {
      "application": "kibana-.kibana",
      "privileges": [
        "read"
      ],
      "resources": [
        "space:fed2"
      ]
    }
  ]
}
```

## 2. The service user

Generate a password on the host and keep it out of chat and out of this repository:

```bash
openssl rand -base64 24
```

```
PUT _security/user/svc_dhm
{
  "password": "PASTE-THE-GENERATED-PASSWORD-HERE",
  "roles": ["dhm_collector"],
  "full_name": "Dashboard Health Monitor (service)"
}
```

## 3. The key, created as that user

```
POST _security/api_key/grant
{
  "grant_type": "password",
  "username": "svc_dhm",
  "password": "PASTE-THE-SAME-PASSWORD-HERE",
  "api_key": { "name": "dashboard_health_monitor", "expiration": "365d" }
}
```

Leave `role_descriptors` out: the key then holds everything the `dhm_collector` role grants.
Copy `encoded` from the response; do not paste it in chat.

If steps 1 to 3 return 403, your account lacks `manage_security` or `grant_api_key`: ask a
cluster administrator to run them.

## 4. Remove the key that does not work

```
DELETE _security/api_key
{ "ids": ["fw21vaEBsBECZRUfD7Gi"] }
```

## 5. Put the new key in the secret

In AWS Secrets Manager (prd account, `us-east-1`), set `ans_dashboard_health_monitor` in
`federal_store` to the new `encoded` value, with no spaces or quotes.

## 6. Check it before writing anything

```bash
cd /opt/kibana_dashboard_runtime_monitor && git pull origin main
.venv/bin/python scripts/check_access.py --patterns cdm_aim
```

It must show `authenticated as: svc_dhm` and `write access to dashboard-health-monitor: yes`.
If it says `NO, missing ...`, stop: the collector's write will fail the same way again.

## 7. Run the collector again (the setup script does not need repeating)

```bash
sudo -u dhm env HOME=/var/lib/dhm bash -c \
  'cd /opt/kibana_dashboard_runtime_monitor && .venv/bin/python scripts/run_collector.py'
```

Expect `Indexed 22 documents into dashboard-health-monitor`. Then in Dev Tools:

```
GET _data_stream/dashboard-health-monitor
GET dashboard-health-monitor/_count
```

Expect `template` and `ilm_policy` both `dashboard-health-monitor`, and a count of 22.

## 8. Send the result

Paste the output of steps 6 and 7 into the chat as text. Do not commit it or upload it.
