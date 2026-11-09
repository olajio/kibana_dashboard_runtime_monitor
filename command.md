# Build the load-time baseline, then set the thresholds

Three runs are in `dashboard-health-monitor` so far (66 documents). Every panel on every
dashboard was `ok` in all of them. Load times for the slow ones:

| Dashboard | Fastest | Median | Slowest | Statuses seen |
|---|---|---|---|---|
| Software Overview | 92 s | 92 s | 169 s | degraded, degraded, failed |
| Priority Risks | 56 s | 60 s | 101 s | degraded x3 |
| Agency Details | 30.1 s | 33.7 s | 40.3 s | degraded x3 |

The global thresholds (degraded 30 s, failed 120 s) cannot describe both Software Overview and
HVA Software (0.2 s), and Agency Details sits on the 30 s line. Three runs minutes apart also
only show a warm cache. So collect first, then decide.

## 1. Put the collector on a schedule (every 30 minutes)

A run takes about 7 minutes. `flock -n` makes a run skip itself if the previous one is still
going, so a hung dashboard cannot stack browsers and distort the numbers.

```bash
sudo -u dhm crontab -l 2>/dev/null; echo "(that was the current crontab, if any)"
```

```bash
( sudo -u dhm crontab -l 2>/dev/null; echo '*/30 * * * * cd /opt/kibana_dashboard_runtime_monitor && flock -n /var/lib/dhm/collector.lock env HOME=/var/lib/dhm /opt/kibana_dashboard_runtime_monitor/.venv/bin/python scripts/run_collector.py >> /var/lib/dhm/collector.log 2>&1' ) | sudo -u dhm crontab -
sudo -u dhm crontab -l
```

(Under AAP the playbook installs the same job, with the same `flock`, at `dhm_cron_minute`.)

## 2. Check that it is running

After the next :00 or :30:

```bash
tail -n 30 /var/lib/dhm/collector.log
```

You should see `Indexed 22 documents into dashboard-health-monitor`.

## 3. Read the baseline

Once there are at least 48 runs (about a day), preferably 1 to 2 weeks with a weekend:

```bash
cd /opt/kibana_dashboard_runtime_monitor && git fetch origin && git reset --hard origin/main
.venv/bin/python scripts/baseline.py --days 14
```

It prints, per dashboard, the runs, p50, p95 and slowest load time, the not-ok panels, the
statuses it was given, and suggested `degraded>` and `failed>` thresholds (1.5x p95, then twice
that). Until there are 48 runs it says `NOT YET A BASELINE`. Paste the table into the chat.

## 4. Meanwhile

- Do not turn on alerts on `load_status`: it is a function of the global thresholds.
- A dashboard with `not_ok` panels is the signal that does not depend on timing.
- To stop collecting: `sudo -u dhm crontab -r`.
