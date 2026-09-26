#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$project_dir"
if [[ "$EUID" -eq 0 ]]; then echo 'Run as your normal SSH user, not root.' >&2; exit 2; fi
# Keep generated unit paths unambiguous without shell or systemd escaping.
if [[ ! "$project_dir" =~ ^/[A-Za-z0-9_./-]+$ ]]; then
  echo 'Move project to a path without spaces or special characters before installing the service.' >&2
  exit 2
fi
[[ -f .env && -x .venv/bin/python ]] || { echo 'Run install-ubuntu.sh and configure .env first.' >&2; exit 2; }
set -a
source .env
set +a
.venv/bin/python x_monitor.py --check-config
# Does not send a test message or start the monitor; login and --once are manual acceptance steps.
service_user="$(id -un)"
service_group="$(id -gn)"
unit_file="$(mktemp)"
trap 'rm -f -- "$unit_file"' EXIT
cat > "$unit_file" <<UNIT
[Unit]
Description=X monitor to Telegram and DingTalk
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=$service_user
Group=$service_group
WorkingDirectory=$project_dir
EnvironmentFile=$project_dir/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/bin/bash $project_dir/scripts/run-monitor.sh
Restart=always
RestartSec=30
TimeoutStopSec=90
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
UNIT
sudo install -m 644 "$unit_file" /etc/systemd/system/x-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable x-monitor.service
echo 'Unit installed and enabled for boot. After successful --once verification, start it:'
echo 'sudo systemctl start x-monitor'
echo 'sudo journalctl -u x-monitor -f'
