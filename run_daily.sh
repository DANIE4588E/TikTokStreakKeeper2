#!/usr/bin/env bash
# The one-command run: full send to everyone in friends.json, no prompts.
# Run it by hand OR let cron call it daily at 00:01 (install_cron.sh).
# Output is shown live and appended to logs/cron.log.
cd "$(dirname "$0")"
mkdir -p logs
PY="./.venv/bin/python"
[ -x "$PY" ] || PY="python3"
echo "========== run $(date '+%F %T %Z') ==========" >> logs/cron.log
"$PY" send_messages.py --unattended 2>&1 | tee -a logs/cron.log
rc=${PIPESTATUS[0]}
echo "========== exit code $rc ======" >> logs/cron.log
exit $rc
