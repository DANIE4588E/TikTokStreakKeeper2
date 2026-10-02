#!/usr/bin/env bash
# One-time setup on the Debian server. Run with:  bash setup_server.sh
set -euo pipefail
cd "$(dirname "$0")"

sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip cron

python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
./.venv/bin/playwright install --with-deps chromium

mkdir -p logs
sudo systemctl enable --now cron

echo
echo "Setup done. Next:"
echo "  1. Copy .env, friends.json, message.txt and storage_state.json here"
echo "     (see README: 'Running on a Debian server')."
echo "  2. Test:  ./.venv/bin/python send_messages.py --test --unattended"
echo "  3. Schedule it daily:  bash install_cron.sh"
