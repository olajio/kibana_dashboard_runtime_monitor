# Installing a browser on the RHEL 8 EC2 host

The collector drives an already-installed browser and downloads nothing. The production
host has none, so this is the path from "no browser" to a clean dry run.

**Scope.** Red Hat Enterprise Linux 8.10 on an EC2 instance (confirmed from
`/etc/os-release`). The package commands are `dnf`, the CA trust commands are RHEL's,
and the paths are RHEL's. Do not use them as-is on Debian or Ubuntu.

For the general short path back into the project, see [`RESUME.md`](RESUME.md).

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
for an account with nowhere to write.

The playbook now creates `dhm` with `/var/lib/dhm` as its home, and sets `HOME` in the
cron line. Apply it:

```bash
git pull origin main
ansible-playbook -i inventory deploy/dhm-deploy.yml
```

If the account already exists from an earlier run, the playbook also creates the
directory, so a re-run repairs it. By hand, the equivalent is:

```bash
sudo usermod -d /var/lib/dhm dhm
sudo install -d -o dhm -g dhm -m 0750 /var/lib/dhm
```

Then run the collector as that account, from the same checkout the playbook deployed:

```bash
sudo -u dhm env HOME=/var/lib/dhm bash -c \
  'cd /opt/dashboard-health-monitor && .venv/bin/python scripts/run_collector.py --dry-run --out /var/lib/dhm/run.json'
```

A result that differs from the root run points at the account, not the collector.

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

**Checked against evidence:**
- The host is RHEL 8.10 on EC2 (`/etc/os-release`, the hostname).
- Secret resolution, TLS via `ca_bundle` and live discovery of all 22 dashboards work on
  this host (from your last run).
- `playwright install-deps` is `apt-get` only, and Playwright adds `--no-sandbox` by
  default (read from the Playwright 1.62 source).

**Not verified, so confirm as you go:**
- That either vendor repo is reachable from this host. Step 1 tests it.
- That an Edge build exists for this host's architecture. `uname -m` in step 1.
- That the missing home directory is what breaks the browser under `dhm`. It is the
  likely cause of a root-versus-`dhm` difference, but it has not been reproduced.
- That the browser trusts the proxy's CA without extra steps (step 6).
- SELinux behaviour with the installed browser (step 7).
