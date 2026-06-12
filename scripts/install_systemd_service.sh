#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$(command -v python3)"
PORT="${AGENT_BATTLE_PORT:-8080}"
SERVICE_FILE="/etc/systemd/system/agent-battle.service"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "This script needs sudo to install a systemd service."
  echo "Run:"
  echo "  sudo AGENT_BATTLE_PORT=${PORT} $0"
  exit 1
fi

cat > "$SERVICE_FILE" <<SERVICE
[Unit]
Description=Agent Battle Arena
After=network.target

[Service]
Type=simple
WorkingDirectory=${ROOT_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${PYTHON_BIN} -m agent_battle.server --host 0.0.0.0 --port ${PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable agent-battle
systemctl restart agent-battle

echo "Agent Battle service installed and started."
echo "Status:"
systemctl --no-pager --full status agent-battle
