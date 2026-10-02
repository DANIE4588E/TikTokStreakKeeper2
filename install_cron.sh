#!/usr/bin/env bash
# Installs the daily 00:01 cron job for the current user.
# NOTE: the time is in the SERVER's timezone — check with:  timedatectl
set -euo pipefail
cd "$(dirname "$0")"
chmod +x run_daily.sh
DIR="$PWD"
( crontab -l 2>/dev/null | grep -vF "run_daily.sh"; echo "1 0 * * * $DIR/run_daily.sh" ) | crontab -
echo "Installed cron entry:"
crontab -l | grep -F run_daily.sh
