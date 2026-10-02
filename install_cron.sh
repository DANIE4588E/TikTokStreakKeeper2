#!/usr/bin/env bash
# Installs the daily 00:01 cron job for the current user.
# NOTE: the time is in the SERVER's timezone — check with:  timedatectl
set -euo pipefail
cd "$(dirname "$0")"
chmod +x run_daily.sh
DIR="$PWD"

# Rebuild the crontab from the existing one: drop exact-duplicate lines and any
# old run_daily entry, then add ours. (grep exits 1 when nothing matches, so
# don't let pipefail treat that as fatal.)
TMP="$(mktemp)"
crontab -l 2>/dev/null | awk '!seen[$0]++' | grep -vF "run_daily.sh" > "$TMP" || true
echo "1 0 * * * $DIR/run_daily.sh" >> "$TMP"
crontab "$TMP"
rm -f "$TMP"

echo "Installed cron entry:"
crontab -l | grep -F run_daily.sh
