# Setting the monitor up on the prd appserver

Why this host: the secret `federal_store` exists once per AWS account, and the collector reads
the one in the account its instance role belongs to. The dev account's copy
(a different secret ARN) still pointed at an Elasticsearch endpoint that no longer exists
(Elastic Cloud answered `HTTP 404 'Unknown resource.'`). The prd account's copy
(its own ARN) holds the `cdm-fdb` endpoints. Nothing in the repo names an account,
so the same code works on both; only the host decides which secret is read.

Commands run as `root` unless they say `sudo -u dhm`. This repository is public: paste
results into chat, never commit them.

**Not verified yet:** the Edge install, the `dhm` account and SELinux on this specific host;
whether the prd role and network allow the secret read and the `cdm-fdb` egress; whether the
prd collector sees the same 22 dashboards.

The same procedure with more explanation is in `LINUX_BROWSER_SETUP.md` (the "doc" below);
step numbers refer to it.

## 0. Before touching the host (prd AWS account, region `us-east-1`)

1. `federal_store` has the fields `elastic_url`, `kibana_url` and `ans_dashboard_health_monitor`.
   The two URLs are the `cdm-fdb` `.es.` and `.kb.` endpoints.
2. The API key is created **on the `cdm-fdb` cluster**, in Dev Tools there:
   `POST /_security/api_key` with the contents of `es/api_key_collector_ccs.json`.
   Put the returned `encoded` value in `ans_dashboard_health_monitor`. A key only exists on the
   cluster that issued it, so a key from the old cluster fails here with a 401.
3. The appserver's instance role may call `secretsmanager:GetSecretValue` on the prd secret.
4. The appserver can reach the `cdm-fdb` `.es.` and `.kb.` hostnames (`us-gov-east-1`) on 443.

## 1. Check the host (doc step 1)

```bash
uname -m; cat /etc/os-release | head -3; env | grep -i proxy
python3 --version        # needs 3.10 or newer; RHEL 8's own python3 is 3.6
```

## 2. Python 3.10+ and the code

```bash
cd /opt && git clone https://github.com/olajio/kibana_dashboard_runtime_monitor.git
cd kibana_dashboard_runtime_monitor
/path/to/python3.1x -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
```

Use `python -m pip`, so pip and the interpreter always match. The playbook uses
`/opt/dashboard-health-monitor` as its checkout; decide which path you want before step 8.

## 3. Install a browser (doc steps 2 and 3)

Edge (option A in the doc):

```bash
sudo tee /etc/yum.repos.d/microsoft-edge.repo >/dev/null <<'REPO'
[microsoft-edge]
name=microsoft-edge
baseurl=https://packages.microsoft.com/yumrepos/edge
enabled=1
gpgcheck=1
gpgkey=https://packages.microsoft.com/keys/microsoft.asc
REPO
sudo dnf install -y microsoft-edge-stable
/opt/microsoft/msedge/msedge --version
ldd /opt/microsoft/msedge/msedge | grep -i "not found"     # must print nothing
```

If the Microsoft repo is not reachable, use Chrome, an offline RPM or a distro Chromium (doc
step 2, options B to D). Do not run `playwright install` or `playwright install-deps`.

## 4. Create `config/settings.yaml`

```yaml
aws_secret_id: federal_store
aws_region: us-east-1
kibana_space: fed2
ca_bundle: /etc/pki/tls/certs/ca-bundle.crt   # only behind a TLS-inspecting proxy
```

`config/settings.example.yaml` lists the other fields. Put no URL and no key in this file:
both come from the secret.

## 5. Check what the host reads, before anything is written

```bash
python scripts/check_access.py 2>&1 | head -12
```

It must show all of these:

| Line | Expected |
|---|---|
| `Secret read:` | the **prd** AWS account id (the number in the ARN) and `stages=AWSCURRENT` |
| `Elasticsearch URL:` | the `cdm-fdb` host |
| `cluster:` | the `cluster_uuid` that `GET /` returns in your Dev Tools on `cdm-fdb` |
| the counts | like yours in Dev Tools: `cdm_aim` about 124, `cdm_cyhy_vuln` about 73,938 |

Do not continue until they match. `HTTP 404 'Unknown resource.'` means the endpoint in the
secret does not exist; a 401 means the key is not from this cluster; zero counts where you
see data mean the key is filtered by its role.

## 6. Dry run, as root and then as `dhm` (doc steps 4 and 5)

```bash
python scripts/run_collector.py --dry-run --out run.json

id dhm 2>/dev/null || sudo useradd --system --home-dir /var/lib/dhm --create-home --shell /usr/sbin/nologin dhm
sudo chmod 0750 /var/lib/dhm
sudo chmod -R u=rwX,g=rX,o=rX /opt/kibana_dashboard_runtime_monitor
sudo -u dhm env HOME=/var/lib/dhm bash -c \
  'cd /opt/kibana_dashboard_runtime_monitor && .venv/bin/python scripts/run_collector.py --dry-run --out /var/lib/dhm/run.json'
```

Expect `Connection:` showing `<-aws-bundle`, all dashboards found, and a result per dashboard.
Nothing is written to Elasticsearch in a dry run. This is the first real read of prd, so send
the summary into chat.

## 7. Write to Elasticsearch by hand (doc step 8)

The cluster assets first, then the write. A write before the template creates the data
stream with default mappings, which can only be undone by deleting the data.

```bash
sudo -u dhm env HOME=/var/lib/dhm bash -c \
  'cd /opt/kibana_dashboard_runtime_monitor && .venv/bin/python scripts/setup_elasticsearch.py'
sudo -u dhm env HOME=/var/lib/dhm bash -c \
  'cd /opt/kibana_dashboard_runtime_monitor && .venv/bin/python scripts/run_collector.py'
```

Success reads `Applied ILM policy: dashboard-health-monitor`,
`Applied index template: dashboard-health-monitor`, then `Indexed N documents`. A 403 on the
first command means the key lacks `manage_ilm` or `manage_index_templates`; the doc's step 8
has the admin-key variant that keeps the key out of shell history.

## 8. Deploy with Ansible Automation Platform (doc step 9)

Only after step 7 is clean. The inventory group `dhm_runners` must contain the prd appserver
and the project must be on `main`. Set `dhm_python` to the 3.10+ interpreter and run first with
`dhm_cron_disabled=true`; the `setup` tag runs the cluster setup; leave the disabled flag off
when you want the schedule on.
