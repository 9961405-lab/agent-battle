#!/usr/bin/env bash
set -euo pipefail

PORT="${AGENT_BATTLE_PORT:-8080}"

echo "Starting Agent Battle arena on 0.0.0.0:${PORT}"
echo
echo "Share this URL if your server has a public IP:"
echo "  http://<YOUR_PUBLIC_IP>:${PORT}"
echo
echo "If you do not know the public IP, run:"
echo "  curl https://api.ipify.org"
echo
python3 -m agent_battle.server --host 0.0.0.0 --port "$PORT"
