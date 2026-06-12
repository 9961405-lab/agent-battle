#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARENA_URL="${AGENT_BATTLE_URL:-http://101.43.87.232:8080}"

python3 "$ROOT_DIR/skills/agent-battle/scripts/agent_battle_client.py" \
  --base-url "$ARENA_URL" \
  "$@"
