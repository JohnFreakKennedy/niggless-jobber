#!/usr/bin/env bash
# setup_cron.sh
# Installs (or updates) the niggless-jobber daily cron / launchd entry.
# Run once after initial setup: bash setup_cron.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="${REPO_DIR}/.venv/bin/python"
RUN_SCRIPT="${REPO_DIR}/run.py"
LOG_DIR="${HOME}/.niggless-jobber/logs"
LOG_FILE="${LOG_DIR}/cron.log"
CRON_TAG="# niggless-jobber"
LAUNCH_AGENT_PLIST="${HOME}/Library/LaunchAgents/com.niggless-jobber.plist"

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
if [ ! -f "${VENV_PYTHON}" ]; then
    echo "ERROR: virtualenv not found at ${VENV_PYTHON}"
    echo "       Run: python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# ---------------------------------------------------------------------------
# Detect local timezone
# ---------------------------------------------------------------------------
if [ -n "${TZ:-}" ]; then
    LOCAL_TZ="${TZ}"
elif [ -f /etc/timezone ]; then
    LOCAL_TZ="$(cat /etc/timezone)"
elif [ -L /etc/localtime ]; then
    LOCAL_TZ="$(readlink /etc/localtime | sed 's|.*/zoneinfo/||')"
else
    LOCAL_TZ="$("${VENV_PYTHON}" -c 'import tzlocal; print(tzlocal.get_localzone_name())')"
fi

echo "Detected timezone: ${LOCAL_TZ}"

# Convert 12:00 local -> UTC hour for cron
CRON_HOUR_UTC="$("${VENV_PYTHON}" -c "
import datetime, zoneinfo
tz = zoneinfo.ZoneInfo('${LOCAL_TZ}')
local_noon = datetime.datetime.now(tz).replace(hour=12, minute=0, second=0, microsecond=0)
print(local_noon.astimezone(datetime.timezone.utc).hour)
")"

mkdir -p "${LOG_DIR}"

# ---------------------------------------------------------------------------
# macOS: use launchd (handles wake-from-sleep correctly)
# ---------------------------------------------------------------------------
if [[ "$(uname)" == "Darwin" ]]; then
    echo "macOS detected: installing launchd plist at ${LAUNCH_AGENT_PLIST}"

    cat > "${LAUNCH_AGENT_PLIST}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.niggless-jobber</string>
    <key>ProgramArguments</key>
    <array>
        <string>${VENV_PYTHON}</string>
        <string>${RUN_SCRIPT}</string>
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
        <string>${LOCAL_TZ}</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:${REPO_DIR}/.venv/bin</string>
    </dict>
    <key>StandardOutPath</key>
    <string>${LOG_FILE}</string>
    <key>StandardErrorPath</key>
    <string>${LOG_FILE}</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
PLIST

    # Unload existing (ignore errors if not loaded)
    launchctl unload "${LAUNCH_AGENT_PLIST}" 2>/dev/null || true
    launchctl load "${LAUNCH_AGENT_PLIST}"

    echo ""
    echo "launchd agent installed and loaded."
    echo "It will fire at 12:00 ${LOCAL_TZ} every day."
    echo "Logs: ${LOG_FILE}"
    echo ""
    echo "To remove: launchctl unload ${LAUNCH_AGENT_PLIST} && rm ${LAUNCH_AGENT_PLIST}"

# ---------------------------------------------------------------------------
# Linux: use crontab
# ---------------------------------------------------------------------------
else
    echo "Linux detected: installing crontab entry (UTC hour ${CRON_HOUR_UTC})"

    CRON_LINE="0 ${CRON_HOUR_UTC} * * * TZ=${LOCAL_TZ} ${VENV_PYTHON} ${RUN_SCRIPT} >> ${LOG_FILE} 2>&1 ${CRON_TAG}"

    # Remove old entry, add new one
    ( crontab -l 2>/dev/null | grep -v "${CRON_TAG}" ; echo "${CRON_LINE}" ) | crontab -

    echo ""
    echo "Cron job installed:"
    echo "  ${CRON_LINE}"
    echo ""
    echo "It will fire at 12:00 ${LOCAL_TZ} (UTC hour ${CRON_HOUR_UTC}) every day."
    echo "Logs: ${LOG_FILE}"
    echo ""
    echo "To remove: crontab -l | grep -v '${CRON_TAG}' | crontab -"
fi
