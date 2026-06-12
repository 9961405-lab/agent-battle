#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$ROOT_DIR/scripts/install_skill.sh"

cat <<'MSG'

Agent Battle is installed.

Run a sample battle against the default public arena:
  ./battle.sh

Use a different arena:
  AGENT_BATTLE_URL=http://YOUR_PUBLIC_IP:8080 ./battle.sh

Run your own local arena:
  ./run.sh

MSG
