#!/usr/bin/env bash
set -euo pipefail

HOST="${AGENT_BATTLE_HOST:-127.0.0.1}"
PORT="${AGENT_BATTLE_PORT:-8080}"

python3 -m agent_battle.server --host "$HOST" --port "$PORT"
