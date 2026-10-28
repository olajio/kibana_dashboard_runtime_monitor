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

## Step 5. Test as the `dhm` service account

Cron will run the collector as `dhm`, not root, and root hides a problem: **the account
used to be created with no home directory.** Chromium and Edge write a profile, crash
reports and a certificate store under `HOME`, so a browser that starts for root can fail
for an account with nowhere to write. The playbook now creates `dhm` with `/var/lib/dhm`
as its home.

Testing as `dhm` means running the playbook first, because it creates the account and the
checkout `dhm` runs from.

### 5a. Know what the playbook targets

The playbook says `hosts: dhm_runners`. Ansible only runs it against hosts in a group of
that name. **If your inventory has no such group, nothing is run, and the command still
exits 0:**

```
[WARNING]: Could not match supplied host pattern, ignoring: dhm_runners
PLAY [Deploy the Dashboard Health Monitor]
skipping: no hosts matched
PLAY RECAP
```

That looks like success and did nothing (tested). `deploy/inventory.local.ini` defines the
group as the machine you are on, for running the playbook on the host itself:

```ini
[dhm_runners]
localhost ansible_connection=local
```

Check it matched before trusting any run: `--list-hosts` must show a host.

```bash
ansible-playbook -i deploy/inventory.local.ini deploy/dhm-deploy.yml --list-hosts
```

### 5b. Check the host first

Each of these is something the playbook silently depends on:

```bash
ansible --version | head -1            # the playbook uses ansible.builtin.* names
sudo python3 --version                 # the playbook runs under sudo, see below
which git crontab                      # both must exist (crontab comes from cronie)
cat /etc/cron.allow 2>/dev/null        # if this file exists, dhm must be listed in it
git --version
```

- **`sudo python3 --version` must be 3.10 or newer.** Under sudo, `PATH` is reset to the
  system default, so `python3` may resolve to RHEL 8's own 3.6 rather than your 3.14 in
  `/usr/local`. If it does, pass the real interpreter:
  `-e dhm_python=/usr/local/python-3.14/bin/python3`.
- **Ansible version.** The playbook uses fully qualified names (`ansible.builtin.git`).
  It was tested on ansible-core 2.19. The host has the Ansible Engine 2 repo configured,
  which is the 2.9 line; I have not confirmed 2.9 accepts these names. A
  `--syntax-check` fails before touching anything, so it is safe to find out:
  `ansible-playbook -i deploy/inventory.local.ini deploy/dhm-deploy.yml --syntax-check`.
- **`/etc/cron.allow`.** If it exists and does not list `dhm`, cron silently refuses to
  run `dhm`'s jobs even though installing the entry succeeds.

### 5c. Rehearse, then deploy without scheduling

```bash
# 1. read-only rehearsal: runs the preflight for real, previews every change
ansible-playbook -i deploy/inventory.local.ini deploy/dhm-deploy.yml --check --diff

# 2. deploy for real, but leave the schedule commented out
ansible-playbook -i deploy/inventory.local.ini deploy/dhm-deploy.yml -e dhm_cron_disabled=true
```

Add `-e dhm_python=...` to both if 5b said so. The `--check` run can fail on its last
task (the cron entry) if `dhm` does not exist yet, because check mode never creates the
account. That is a limit of rehearsing, not a fault; the real run creates the account
first.

**Why `dhm_cron_disabled=true` first.** Without it the playbook installs a live `*/30`
entry, which starts writing to Elasticsearch within half an hour. If the ILM policy and
index template do not exist by then, the first write auto-creates the data stream with
**default mappings instead of ours**, and fixing that means deleting the data stream and
its data. Apply the cluster assets first (`--tags setup`, or
`python scripts/setup_elasticsearch.py` by hand), then remove the flag.

### 5d. Run the collector as `dhm`

From the checkout the playbook deployed (`/opt/dashboard-health-monitor`, a **separate**
directory from your manual `/opt/kibana_dashboard_runtime_monitor`):

```bash
sudo -u dhm env HOME=/var/lib/dhm bash -c \
  'cd /opt/dashboard-health-monitor && .venv/bin/python scripts/run_collector.py --dry-run --out /var/lib/dhm/run.json'
```

A result that differs from the root run points at the account, not the collector. The
likely failures are a browser that cannot create its profile (the home directory), a
virtualenv `dhm` cannot execute because it cannot traverse the Python install, or a
settings file `dhm` cannot read.

### Other things to be wary of

| Risk | What happens |
|---|---|
| Playbook creates a second checkout | It deploys to `/opt/dashboard-health-monitor`. Your manual `/opt/kibana_dashboard_runtime_monitor` stays as it is; pass `-e dhm_root=...` to reuse it instead. |
| The playbook writes its own `config/settings.yaml` | It renders from `deploy/settings.prod.yaml.j2` and replaces any hand-written one in its checkout. `ca_bundle` now defaults to the system bundle so the proxy's CA is trusted. |
| It needs GitHub | The `git` task clones from `github.com` through the host's network and trust store. |
| It changes the host | Creates the `dhm` account, `/var/lib/dhm`, `/var/log/dhm`, a logrotate rule and (unless disabled) `dhm`'s crontab. |
| A real run writes to the cluster | The cron entry runs `run_collector.py` without `--dry-run`. |

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

## When it works

Return to [`RESUME.md`](RESUME.md) step 5: `setup_elasticsearch.py`, then a real run, then
the Ansible playbook for the schedule.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Chromium distribution 'msedge' is not found at /opt/microsoft/msedge/msedge` | Edge is not installed. Step 2. |
| `HTTP 000` or timeout in step 1 | The host cannot reach that repo. Try the other, or option C. |
| `HTTP 200` but `dnf install` fails on the GPG key | The key URL is unreachable through the proxy. `rpm --import <key url>` after downloading it elsewhere. |
| `error while loading shared libraries: libXXX.so` | Step 3: `dnf install` the named library. |
| `BEWARE: your OS is not officially supported by Playwright; installing dependencies for ubuntu24.04 as a fallback`, then an `apt-get` failure | You ran `playwright install-deps`. It is apt-only. Use step 3 instead. |
| Works as root, fails under cron | Step 5: the `dhm` account and its home directory. |
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
- That the playbook runs on this host's Ansible version.
- That `dhm` can run the browser (step 5d). The missing home directory is the likely
  cause of any root-versus-`dhm` difference, but it has not been reproduced.
- That `dhm` can traverse the Python install and read the checkout.
- That the cron entry actually fires as `dhm` (`/etc/cron.allow`, SELinux).
- SELinux behaviour with the installed browser under cron.
