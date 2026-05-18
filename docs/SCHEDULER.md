# Scheduler

This document covers how niggless-jobber is run automatically every day at 12:00 PM in your
local timezone: the cron setup script, timezone resolution, idempotency guarantees, and the
retry strategy for failed applications.

---

## Mechanism

The scheduler is a system **crontab** entry. No Python scheduler process is required - the OS
wakes the process at the correct time and it exits when done.

```
crontab entry (installed by setup_cron.sh):

0 12 * * *  /path/to/.venv/bin/python /path/to/run.py >> ~/.niggless-jobber/logs/cron.log 2>&1
```

---

## Setup Script (`setup_cron.sh`)

`setup_cron.sh` automates the crontab installation. Run it once after setup:

```bash
bash setup_cron.sh
```

### What the script does

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="${REPO_DIR}/.venv/bin/python"
RUN_SCRIPT="${REPO_DIR}/run.py"
LOG_DIR="${HOME}/.niggless-jobber/logs"
LOG_FILE="${LOG_DIR}/cron.log"
CRON_TAG="# niggless-jobber"

# Resolve local timezone
if [ -f /etc/timezone ]; then
    LOCAL_TZ="$(cat /etc/timezone)"
elif [ -L /etc/localtime ]; then
    LOCAL_TZ="$(readlink /etc/localtime | sed 's|.*/zoneinfo/||')"
else
    LOCAL_TZ="$(python3 -c 'import tzlocal; print(tzlocal.get_localzone_name())')"
fi

echo "Detected timezone: ${LOCAL_TZ}"

# Convert "12:00 local" to UTC for cron
CRON_HOUR_UTC="$(python3 -c "
import datetime, zoneinfo
local_noon = datetime.datetime.now(zoneinfo.ZoneInfo('${LOCAL_TZ}')).replace(hour=12, minute=0, second=0)
utc_hour = local_noon.astimezone(datetime.timezone.utc).hour
print(utc_hour)
")"

CRON_LINE="0 ${CRON_HOUR_UTC} * * * TZ=${LOCAL_TZ} ${VENV_PYTHON} ${RUN_SCRIPT} >> ${LOG_FILE} 2>&1 ${CRON_TAG}"

# Remove any existing niggless-jobber cron entries
( crontab -l 2>/dev/null | grep -v "${CRON_TAG}" ; echo "${CRON_LINE}" ) | crontab -

mkdir -p "${LOG_DIR}"

echo "Cron job installed:"
echo "  ${CRON_LINE}"
echo ""
echo "It will fire at 12:00 ${LOCAL_TZ} (UTC hour ${CRON_HOUR_UTC}) every day."
echo "Logs will be appended to: ${LOG_FILE}"
```

### Timezone handling

The script:

1. Detects the system timezone from `/etc/timezone`, `/etc/localtime`, or `tzlocal`.
2. Uses Python's `zoneinfo` module to calculate what UTC hour corresponds to 12:00 in that zone.
3. Sets both the cron hour (UTC) and the `TZ=` env prefix so Python's logging timestamps are
   correct.

**DST note**: cron fires at a fixed UTC hour. During daylight saving time transitions (clocks
move forward or back) the local time of the run shifts by up to 1 hour. This is acceptable for
a daily job application run. If you want exact local time at all times, replace cron with a
Python `schedule` loop (see the alternative below).

### Re-running after timezone change

If you move to a different timezone, re-run `setup_cron.sh` to update the cron entry.

### Removing the cron job

```bash
crontab -l | grep -v "# niggless-jobber" | crontab -
```

---

## Alternative: Python `schedule` Loop

If you prefer a persistent daemon that respects DST exactly, use the `schedule` library:

```bash
# Start as a background process (or use a launchd/systemd service)
python run.py --daemon
```

In daemon mode `run.py` enters:

```python
import schedule, time

schedule.every().day.at("12:00", tz).do(main)

while True:
    schedule.run_pending()
    time.sleep(30)
```

Where `tz` is loaded from `.env TZ=`. This approach requires the process to stay running
(suitable for a server; less suitable for a laptop that sleeps).

### macOS launchd (recommended for laptop)

For macOS, a `launchd` plist is more reliable than cron because it handles wake-from-sleep:

`setup_cron.sh` also generates `~/Library/LaunchAgents/com.niggless-jobber.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.niggless-jobber</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/.venv/bin/python</string>
        <string>/path/to/run.py</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>12</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>EnvironmentVariables</key>
    <dict>
        <key>TZ</key>
        <string>Europe/Kyiv</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/you/.niggless-jobber/logs/cron.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/you/.niggless-jobber/logs/cron.log</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

Load it:

```bash
launchctl load ~/Library/LaunchAgents/com.niggless-jobber.plist
```

Unload it:

```bash
launchctl unload ~/Library/LaunchAgents/com.niggless-jobber.plist
```

`setup_cron.sh` detects macOS and installs the plist automatically instead of the crontab entry.

---

## Idempotency

A run is safe to execute multiple times on the same day. Nothing is applied twice.

### Job-level idempotency

Before applying to any job, the engine checks:

```sql
SELECT 1 FROM applications
WHERE job_id = ?
  AND status IN ('applied', 'dry_run')
```

If a row exists, the job is skipped with `reason: already_applied`.

The job fingerprint `id` is a SHA-256 hash of `(source, company, title, posted_at_date)`. This
means even if the same job is re-scraped (e.g. it was re-posted by the employer), the fingerprint
is the same and it will not be re-applied to.

### Run-level idempotency

The orchestrator checks at startup:

```sql
SELECT 1 FROM run_log
WHERE status = 'running'
  AND started_at > datetime('now', '-2 hours')
```

If another run is already in progress (detected via `status = 'running'`), the new run exits
immediately to prevent two instances running in parallel. The 2-hour window guards against a
crashed run that left a stale `running` record.

---

## Retry Strategy

Applications that end with `status = 'failed'` are automatically retried on the next run.

### Retry eligibility

```sql
SELECT a.*, j.*
FROM applications a JOIN jobs j ON a.job_id = j.id
WHERE a.status = 'failed'
  AND a.retry_count < 3
ORDER BY a.created_at ASC
LIMIT 10   -- max retries per run to cap total work
```

### Retry increment

On each retry attempt the `retry_count` is incremented before trying. If the retry succeeds,
`status` is updated to `applied`. If it fails again and `retry_count` reaches 3, `status` is
set to `failed` with `reason: max_retries_exceeded` and no further retries are attempted.

### Retry backoff

Retries happen on the next daily run (natural 24-hour backoff). There is no intra-run retry loop
for application failures - each job is attempted once per run.

### What is retried vs not

| Failure reason | Retried |
|---|---|
| `latex_compile_error` | YES (recompile on retry) |
| `captcha_unsolvable` | YES |
| `network_timeout` | YES |
| `ats_form_changed` | YES (adapter re-runs) |
| `max_retries_exceeded` | NO |
| `already_applied` | NO |
| `blocked_company` | NO |
| `salary_below_threshold` | NO |

---

## Manual Trigger

To run outside the scheduled time:

```bash
# Full run
source .venv/bin/activate
python run.py

# Dry run (no submissions)
python run.py --dry-run

# Only retry failed applications (skip scraping)
python run.py --retry-only

# Only scrape and generate PDFs (skip submission)
python run.py --no-apply

# Process a single specific job URL
python run.py --url "https://boards.greenhouse.io/acme/jobs/12345"
```

---

## Log Rotation

`logs/cron.log` is appended to forever by default. To rotate it:

```bash
# Add to crontab (or let setup_cron.sh handle it):
0 0 * * 0   find ~/.niggless-jobber/logs -name "*.log" -mtime +30 -delete
```

Or configure `LOG_ROTATE_DAYS` in `.env` and the orchestrator will clean logs at the end of
each run (see `config.yaml: storage.log_retention_days`).
