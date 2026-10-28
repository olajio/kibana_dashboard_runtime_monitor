# Installing a browser on the RHEL 8 EC2 host

The collector drives an already-installed browser and downloads nothing. The production
host has none, so this is the path from "no browser" to a clean dry run.

**Scope.** Red Hat Enterprise Linux 8.10 on an EC2 instance (confirmed from
`/etc/os-release`). The package commands are `dnf`, the CA trust commands are RHEL's,
and the paths are RHEL's. Do not use them as-is on Debian or Ubuntu.

For the general short path back into the project, see [`RESUME.md`](RESUME.md).

## What happened on this host (28 Oct 2026)

Followed through step 4 and it worked. For the record, so the next person knows what to
expect:

| Check | Result |
|---|---|
| `uname -m` | `x86_64`, so the Edge RPM applies |
| Proxy environment variables | none set |
| Microsoft repo, Google repo | both `HTTP 200` through the host's trust store |
| Install | Option A, Edge `156.0.4314.50` from Microsoft's repo, 38 packages |
| `ldd` on the browser | no missing libraries (dnf pulled in GTK, Mesa, fonts, ALSA, ...) |
| `dnf list available \| grep chromium` | nothing, as predicted: Chromium is not in RHEL's repos |
| Dry run as root | all 22 dashboards collected: 7 ok, 12 degraded, 3 failed |
| Browser certificate trust | worked with no extra step (panels rendered, so no `ERR_CERT_*`) |

Things the install did that a host owner may want to know:

- About **1 GB** of disk and 38 packages, including GTK, fonts and Mesa drivers.
- It enabled the **`llvm-toolset`** module stream as a dependency side effect.
- It left **`/etc/yum.repos.d/microsoft-edge.repo`**, so a routine `dnf update` upgrades
  Edge. The collector's selectors are tied to browser behaviour, so after any major Edge
  upgrade, suspect the browser version first if results shift suddenly
  (`/opt/microsoft/msedge/msedge --version`).

The `degraded` and `failed` counts are the collector reporting on the dashboards, not a
browser problem. To see why, run `python scripts/summarize_run.py run.json`: it breaks the
not-ok panels down by status and lists panel titles that fail on several dashboards.

## What is different about this host

| Fact | Why it matters |
|---|---|
| RHEL 8.10, `dnf` | Package names and repo files below are RPM-based. |
| EC2, repos via Red Hat's RHUI | A browser RPM's dependencies (`nss`, `atk`, `cups-libs`, ...) come from the normal RHEL repos, so `dnf` resolves them for you. |
| Chromium is not in RHEL's own repos | A distro `chromium` is the least likely option here. Edge and Chrome come from vendor repos. |
| Runs as `root` by hand, as `dhm` from cron | Root has a home directory; the service account may not. See step 5. |
| Behind a TLS-inspecting proxy | `dnf` and `curl` need the proxy's CA too, not just Python. |
| Python 3.14 built under `/usr/local` | Unrelated to the browser, but it is why `pip` and `python` paths look unusual. |

## Step 1. Check architecture and what the host can reach

```bash
uname -m                                   # x86_64 or aarch64
cat /etc/os-release | head -3
env | grep -i proxy                        # does dnf/curl go through a proxy?
dnf repolist
```

Edge's Linux RPM is published for **x86_64**. If `uname -m` says `aarch64` (Graviton),
confirm an Edge build exists for it before relying on it; Chrome or Chromium may be the
only choice.

Then test the two vendor repos through whatever proxy the host uses:

```bash
curl -sS --cacert /etc/pki/tls/certs/ca-bundle.crt -o /dev/null -w 'Microsoft: HTTP %{http_code}\n' \
  https://packages.microsoft.com/yumrepos/edge/repodata/repomd.xml
curl -sS --cacert /etc/pki/tls/certs/ca-bundle.crt -o /dev/null -w 'Google:    HTTP %{http_code}\n' \
  https://dl.google.com/linux/chrome/rpm/stable/x86_64/repodata/repomd.xml
```

`HTTP 200` means that repo is reachable. If `dnf` itself must use a proxy, set
`proxy=http://host:port` in `/etc/dnf/dnf.conf`.

## Step 2. Install one browser

Pick the first row whose repo you can reach. Edge and Chrome are both Chromium, so the
collector's render detection behaves the same on either.

| Option | When | Setting to add under `collector:` in `config/settings.yaml` |
|---|---|---|
| **A. Edge** | Microsoft's repo reachable | nothing, `msedge` is the default |
| **B. Chrome** | Google's repo reachable | `browser_channel: chrome` |
| **C. Offline RPM** | Neither repo reachable from the host, but you can download elsewhere | as A or B |
| **D. Chromium** | A package exists in a repo you can see | `browser_executable: /usr/bin/chromium` |

### A. Edge

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
```

Installs to `/opt/microsoft/msedge/msedge`, which is where Playwright looks.

### B. Chrome

```bash
sudo tee /etc/yum.repos.d/google-chrome.repo >/dev/null <<'REPO'
[google-chrome]
name=google-chrome
baseurl=https://dl.google.com/linux/chrome/rpm/stable/x86_64
enabled=1
gpgcheck=1
gpgkey=https://dl.google.com/linux/linux_signing_key.pub
REPO
sudo dnf install -y google-chrome-stable
```

Installs to `/opt/google/chrome/chrome`.

### C. Offline RPM

If the host cannot reach the vendor repo, download the RPM on a machine that can and copy
it over. Chrome's is at
`https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm`; Edge's
RPMs are listed at `https://packages.microsoft.com/yumrepos/edge/`.

```bash
scp google-chrome-stable_current_x86_64.rpm ec2-user@<host>:/tmp/
sudo dnf install -y /tmp/google-chrome-stable_current_x86_64.rpm
```

Installing a local file with `dnf` still resolves the browser's dependencies from RHEL's
own repos, so it only needs the browser file, not the vendor repo.

### D. Chromium

```bash
dnf list available 2>/dev/null | grep -i chromium
```

If a package appears, install it and set `browser_executable`. Note the difference:
`browser_channel: chromium` means Playwright's **own downloaded** build, which is a
different thing from a system Chromium and needs `playwright install`, a download we
avoid. A system binary always goes in `browser_executable`.

### Do not run `playwright install` or `playwright install-deps`

`playwright install` downloads a browser, which is what this setup avoids and which a
restricted boundary may block. `playwright install-deps` is hardcoded to `apt-get`: on
any distro that is not Ubuntu, Debian or Mint it falls back to an Ubuntu profile and
runs `apt-get`, which does not exist on RHEL. (Checked in Playwright 1.62's source.)

## Step 3. Confirm the browser is installed and can start

```bash
ls -l /opt/microsoft/msedge/msedge /opt/google/chrome/chrome 2>/dev/null
/opt/microsoft/msedge/msedge --version          # or the Chrome path
ldd /opt/microsoft/msedge/msedge | grep -i "not found"
```

The `ldd` line must print nothing. Anything it prints is a missing shared library; install
it by name with `dnf install`. The usual suspects on a minimal image are `nss`, `nspr`,
`atk`, `at-spi2-atk`, `cups-libs`, `libdrm`, `libxkbcommon`, `libXcomposite`,
`libXdamage`, `libXrandr`, `mesa-libgbm`, `alsa-lib` and `pango`. Installing the vendor
RPM normally pulls these in already.

## Step 4. Dry run as root

```bash
cd /opt/kibana_dashboard_runtime_monitor
source .venv/bin/activate
python scripts/run_collector.py --dry-run --out run.json
```

Playwright disables the Chromium sandbox by default, so running as root is not a problem
(checked in its source: `--no-sandbox` is added unless `chromiumSandbox` is true).

Success looks like the `Connection:` line with `<-aws-bundle`, then
`Collecting 22 dashboards ...`, then results per dashboard instead of a launch error.

## Step 5. Test as the `dhm` service account, by hand

Cron will run the collector as `dhm`, not root, and root hides a problem: **the account
used to be created with no home directory.** Chromium and Edge write a profile, crash
reports and a certificate store under `HOME`, so a browser that starts for root can fail
for an account with nowhere to write. The playbook now creates `dhm` with `/var/lib/dhm`
as its home.

This test does not need Ansible. The account and the checkout you already have are
enough, and the playbook later converges onto the same account (step 9).

### 5a. Create the account

```bash
id dhm 2>/dev/null || useradd --system --home-dir /var/lib/dhm --create-home --shell /usr/sbin/nologin dhm
chmod 0750 /var/lib/dhm
```

This is what the playbook's `user` task does, so a later AAP run finds it already there.

### 5b. Check `dhm` can reach and read what it needs

```bash
umask
namei -l /opt/kibana_dashboard_runtime_monitor/.venv/bin/python
sudo -u dhm test -r /opt/kibana_dashboard_runtime_monitor/config/settings.yaml && echo "settings readable"
```

`namei -l` lists the permissions of every directory on the way to the virtualenv's Python,
including the symlink into `/usr/local/python-3.14`. Each one must be traversable (`x`) by
`dhm`. A `umask` of `027` or `077` means files you created are unreadable to other
accounts. If anything fails, fix permissions without changing ownership:

```bash
chmod -R u=rwX,g=rX,o=rX /opt/kibana_dashboard_runtime_monitor
```

### 5c. Run the collector as `dhm`

```bash
sudo -u dhm env HOME=/var/lib/dhm bash -c \
  'cd /opt/kibana_dashboard_runtime_monitor && .venv/bin/python scripts/run_collector.py --dry-run --out /var/lib/dhm/run.json'
```

Expect the same as the root run: `Connection:` showing `<-aws-bundle`, then all 22
dashboards. Anything different points at the account, not the collector.

| Symptom as `dhm` | Likely cause |
|---|---|
| `Permission denied` on `.venv/bin/python` | `dhm` cannot traverse the path to Python. `namei -l` in 5b shows which directory. |
| Browser fails to start, or cannot write a profile | The home directory, or SELinux (`sudo ausearch -m avc -ts recent`) |
| Cannot read `settings.yaml` | File permissions; the `chmod` in 5b |
| Same results as root | The account is fine |

## Step 6. Certificates in the browser

The collector's own HTTP calls use `ca_bundle`. The **browser** does not: it reads the
operating system's trust store. Your `ca_bundle` already validates the endpoint, which
suggests the proxy's CA is in the system store, so the browser will probably trust it too.

If dashboards fail to load with `net::ERR_CERT_AUTHORITY_INVALID`, install the CA
system-wide:

```bash
sudo cp <proxy-ca>.crt /etc/pki/ca-trust/source/anchors/
sudo update-ca-trust
```

To prove the rest of the chain first, one diagnostic run with verification off:

```bash
DHM_KIBANA_VERIFY_TLS=false python scripts/run_collector.py --dry-run --out run.json
```

That is a diagnostic only. The run prints a warning while it is set; do not leave it on.

## Step 7. SELinux

RHEL 8 ships with SELinux enforcing. A binary under `/opt` normally runs fine, but if the
browser fails to start with no useful message:

```bash
getenforce
sudo ausearch -m avc -ts recent | tail
```

An AVC denial naming the browser confirms it. Do not respond by disabling SELinux; ask
for a policy fix.

## Step 8. Write to Elasticsearch by hand

Do this before AAP. The write path has never run on this cluster, and AAP adds layers
(inventory, credential, SSH, `sudo`, execution environment, project sync). If the first AAP
run fails you could not tell whether the collector or AAP is at fault. By hand there is
one variable.

**Order matters: the cluster assets first, then the write.** Applying the ILM policy and
index template creates no index. The first `_bulk` write auto-creates the data stream from
that template. If a write happens first, the data stream is created with **default mappings
instead of ours**, and undoing that means deleting the data stream and its data.

```bash
# 1. ILM policy + index template
sudo -u dhm env HOME=/var/lib/dhm bash -c \
  'cd /opt/kibana_dashboard_runtime_monitor && .venv/bin/python scripts/setup_elasticsearch.py'

# 2. the first real write, which creates the data stream
sudo -u dhm env HOME=/var/lib/dhm bash -c \
  'cd /opt/kibana_dashboard_runtime_monitor && .venv/bin/python scripts/run_collector.py'
```

Success: `Applied ILM policy: dashboard-health-monitor`,
`Applied index template: dashboard-health-monitor`, then
`Indexed 22 documents into dashboard-health-monitor`.

Step 1 needs cluster privileges (`manage_ilm`, `manage_index_templates`). If it fails with a
**403**, the key in the secret does not hold them (the long-lived key should not). Run that
one command with an admin key, supplied without leaving it in shell history:

```bash
read -rsp 'Admin ES API key: ' DHM_ES_API_KEY; echo; export DHM_ES_API_KEY
sudo -u dhm --preserve-env=DHM_ES_API_KEY env HOME=/var/lib/dhm bash -c \
  'cd /opt/kibana_dashboard_runtime_monitor && .venv/bin/python scripts/setup_elasticsearch.py'
unset DHM_ES_API_KEY
```

Passing `--es-api-key` works too but puts the key in history and the process list. If
`sudo` refuses to preserve the variable, set it inside the `bash -c` instead.

**Verify** (the `ES_URL` and `APIKEY` shorthand is from HOWTO §0.0):

```bash
# the data stream exists and is using OUR template and ILM policy
curl -s --cacert /etc/pki/tls/certs/ca-bundle.crt "$ES_URL/_data_stream/dashboard-health-monitor" \
  -H "Authorization: ApiKey $APIKEY" | python3 -m json.tool | grep -E '"(name|template|ilm_policy)"'

# and holds the 22 documents
curl -s --cacert /etc/pki/tls/certs/ca-bundle.crt "$ES_URL/dashboard-health-monitor/_count" \
  -H "Authorization: ApiKey $APIKEY"
```

Expect `"template": "dashboard-health-monitor"`, `"ilm_policy": "dashboard-health-monitor"`
and a count of 22. A different template name, or no `ilm_policy`, means a write got in
before the assets did.

## Step 9. Deploy from Ansible Automation Platform

The playbook is meant to run from AAP, so **Ansible does not need installing on this
host.** AAP runs Ansible from its own execution environment. The host needs SSH, `sudo`
and a Python the modules can use. Over SSH the modules use RHEL's own Python, which has the
SELinux bindings, so the custom 3.14 in `/usr/local` is not in their path.

**Start the prerequisites in step 9a now**, in parallel with steps 5 to 8. Inventory
entries, an SSH credential, a security-group rule for the controller and GitHub access can
take other teams days.

### 9a. What AAP needs

| Object | Setting |
|---|---|
| **Inventory** | Add this host to a group named **`dhm_runners`**. `deploy/inventory.local.ini` is for running Ansible on the host itself and is irrelevant here. |
| **Credential (Machine)** | SSH user and key for the host, with privilege escalation (`sudo`). The playbook uses `become: true`. |
| **Project** | `https://github.com/olajio/kibana_dashboard_runtime_monitor.git`, branch `main`. A private repository needs a Source Control credential. |
| **Job template** | Playbook `deploy/dhm-deploy.yml`, with this inventory, credential and project. Extra variables below. Tick **Prompt on launch** for Job Type and Job Tags. |

Extra variables on the template:

```yaml
dhm_python: /usr/local/python-3.14/bin/python3
dhm_cron_disabled: true
```

`dhm_python` only builds the virtualenv. Under `sudo`, `PATH` resets, so bare `python3` may
be RHEL 8's own 3.6, and the playbook's preflight needs 3.10 or newer. **Do not** set
`ansible_python_interpreter` to the 3.14 path as well: that points the modules at an
interpreter with no SELinux bindings, which fails on the first file task.

AAP needs **no AWS credential**. The secret is read by the host's instance role.

Menu names vary a little between AAP versions.

### 9b. Launch in this order

1. **Run Command, module `ping`**, from Inventories, Hosts, the host, Run Command. This
   checks SSH, `sudo` and Python before any playbook runs, and it is cheap. A failure here
   is an inventory, credential or network problem, not a playbook problem.
2. **Job Type: Check** (with Show Changes on). This runs the read-only preflight for real
   and previews every change. Its last task, the cron entry, may fail if `dhm` does not
   exist yet, because check mode never creates the account. If you did step 5a it does
   exist.
3. **A real run** with `dhm_cron_disabled: true`. This creates the checkout at
   `/opt/dashboard-health-monitor`, the virtualenv, `settings.yaml`, the log directory and
   a logrotate rule, and installs the schedule commented out.
4. **Test as `dhm` from the checkout the playbook made**, the same command as step 5c with
   `cd /opt/dashboard-health-monitor`.
5. **Enable the schedule**: re-run with `dhm_cron_disabled` removed or `false`.

Step 8 already applied the ILM policy and template, so you do not need the playbook's
`setup` tag. If you ever do (a new cluster), put `setup` in Job Tags: it runs only that
task, as `dhm`, and needs step 3 to have created the checkout.

### 9c. Things to watch for

| Risk | What happens |
|---|---|
| **A green job is not proof** | With no host in `dhm_runners` the job prints `skipping: no hosts matched`, runs nothing and reports success. Check the host appears in the play recap. |
| **GitHub is needed twice** | The controller syncs the project, and the host runs `git` itself. Both need `github.com`. |
| **Execution environment's ansible-core** | Newer ansible-core drops support for Python 3.6 on the target, and RHEL 8's system Python is 3.6. If the first task fails with a Python version error, that is it. Paste the exact message. |
| **`sudo` needs a password** | The machine credential needs the privilege-escalation password, or `NOPASSWD` for that user. |
| **`/etc/cron.allow`** | If it exists and does not list `dhm`, cron silently skips `dhm`'s jobs even though installing the entry succeeds. `ls -l /etc/cron.allow /etc/cron.deny` (printing nothing from `cat` is ambiguous: a missing file and an empty one look the same). |
| **Second checkout** | The playbook deploys to `/opt/dashboard-health-monitor`. Your manual `/opt/kibana_dashboard_runtime_monitor` stays as it is. Delete it once AAP works, or pass `dhm_root` to reuse it. |
| **Its own `settings.yaml`** | The playbook renders `config/settings.yaml` from `deploy/settings.prod.yaml.j2` and replaces any hand-written one in its checkout. `ca_bundle` defaults to the system bundle so the proxy's CA is trusted. |
| **It changes the host** | Creates the `dhm` account, `/var/lib/dhm`, `/var/log/dhm`, a logrotate rule and, unless disabled, `dhm`'s crontab. |
| **A real run writes to the cluster** | The cron entry runs `run_collector.py` without `--dry-run`. |

### 9d. Alternative: run Ansible on the host itself

Not the plan, noted so the files in `deploy/` make sense. `deploy/inventory.local.ini`
defines `dhm_runners` as the machine you are on, so
`ansible-playbook -i deploy/inventory.local.ini deploy/dhm-deploy.yml --check --diff`
works once Ansible is installed (the host has none; `pip install ansible-core` in a
separate virtualenv works on Python 3.14). Expect trouble from running it against the
custom 3.14 interpreter on an SELinux host, which AAP over SSH avoids.

## When it works

The sequence is: browser (steps 1 to 4), `dhm` by hand (5), certificates and SELinux if
needed (6, 7), Elasticsearch by hand (8), then AAP (9). After step 9 enables the schedule,
the remaining work is the Kibana side: the data view, the trend dashboard and the alert
rules. Return to [`RESUME.md`](RESUME.md) for those.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Chromium distribution 'msedge' is not found at /opt/microsoft/msedge/msedge` | Edge is not installed. Step 2. |
| `HTTP 000` or timeout in step 1 | The host cannot reach that repo. Try the other, or option C. |
| `HTTP 200` but `dnf install` fails on the GPG key | The key URL is unreachable through the proxy. `rpm --import <key url>` after downloading it elsewhere. |
| `error while loading shared libraries: libXXX.so` | Step 3: `dnf install` the named library. |
| `BEWARE: your OS is not officially supported by Playwright; installing dependencies for ubuntu24.04 as a fallback`, then an `apt-get` failure | You ran `playwright install-deps`. It is apt-only. Use step 3 instead. |
| Works as root, fails under cron | Step 5: the `dhm` account and its home directory. |
| `Permission denied` as `dhm` on `.venv/bin/python` | Step 5b: `namei -l` shows the directory `dhm` cannot traverse. |
| `setup_elasticsearch.py` returns 403 | Step 8: the key lacks `manage_ilm` / `manage_index_templates`. Run that one command with an admin key. |
| Data stream exists but `template` is not `dashboard-health-monitor` | A write happened before the assets existed. Step 8 explains the order; the fix is deleting the data stream. |
| AAP job is green but nothing changed; `skipping: no hosts matched` | `dhm_runners` has no hosts in the inventory the template uses. Step 9c. |
| AAP fails on the first task with a Python error | The execution environment's ansible-core is newer than the host's Python 3.6 supports. Step 9c. |
| `net::ERR_CERT_AUTHORITY_INVALID` on dashboards | Step 6. |
| Launch fails, no useful message | Step 7. |

## What has and has not been verified

**Confirmed on this host (28 Oct 2026):**
- RHEL 8.10 on EC2, x86_64.
- Both vendor repos reachable; Edge installs from Microsoft's repo; no missing libraries.
- A dry run as root collects all 22 dashboards. The browser trusts the proxy's CA.
- Secret resolution, TLS via `ca_bundle` and live discovery of all 22 dashboards.

**Confirmed from Playwright's source:** `install-deps` is `apt-get` only, and
`--no-sandbox` is added by default.

**Confirmed in a sandbox with ansible-core 2.19** (not on this host):
- An inventory with no `dhm_runners` group makes the playbook match nothing and exit 0.
- The new Edge detection passes when Edge is installed the RPM way and fails when it is
  absent; the old multi-name `which` failed even with Edge present.
- Root running git in a repository owned by another user is refused ("dubious
  ownership"), which is why the playbook no longer chowns the checkout to `dhm`.
- The playbook passes `--syntax-check`, and `--check` walks through every task.

**Not verified, so confirm as you go:**
- That `dhm` can run the browser (step 5c). The missing home directory is the likely cause
  of any root-versus-`dhm` difference, but it has not been reproduced.
- That `dhm` can traverse the Python install and read the checkout.
- **The Elasticsearch write path on this cluster** (step 8). Neither the setup nor a real
  write has run here yet.
- **The AAP flow end to end** (step 9): inventory, credential, project sync, the job
  template, and the execution environment against this host's Python.
- That the playbook runs on the AAP execution environment's ansible-core version.
- That the cron entry actually fires as `dhm` (`/etc/cron.allow`, SELinux).
- SELinux behaviour with the installed browser under cron.
