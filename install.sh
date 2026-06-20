#!/usr/bin/env bash
# Set up venv, install deps, backfill history, and enable the systemd user service.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Creating virtualenv (.venv)"
python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt

echo "==> Backfilling history from existing logs"
./.venv/bin/python -m monitor.backfill || true

echo "==> Installing systemd user service"
mkdir -p "$HOME/.config/systemd/user"
cp ai-usage-monitor.service "$HOME/.config/systemd/user/ai-usage-monitor.service"
systemctl --user daemon-reload
systemctl --user enable --now ai-usage-monitor.service

# keep the service running after logout
loginctl enable-linger "$USER" 2>/dev/null || true

echo
echo "==> Done. Dashboard:  http://127.0.0.1:8787"
echo "    Logs:    journalctl --user -u ai-usage-monitor -f"
echo "    Status:  systemctl --user status ai-usage-monitor"
