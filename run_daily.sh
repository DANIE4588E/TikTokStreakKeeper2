#!/usr/bin/env bash
# Daily wrapper used by cron: runs the messenger unattended, appends to logs/cron.log
cd "$(dirname "$0")"
mkdir -p logs
PY="./.venv/bin/python"
[ -x "$PY" ] || PY="python3"
echo "========== run $(date '+%F %T %Z') ==========" >> logs/cron.log
"$PY" send_messages.py --unattended >> logs/cron.log 2>&1
echo "========== exit code $? ======" >> logs/cron.log
