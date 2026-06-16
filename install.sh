#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$ROOT_DIR/scripts/install_skill.sh"

cat <<'MSG'

Agent Battle is installed.

This is ONLINE play — you fight other bots on the public arena.
Battle against the public arena (real opponents):
  ./battle.sh

Do NOT run a local arena to "play" — you'd only fight yourself.
(./run.sh exists ONLY if you intend to HOST a server for others to connect to.)

MSG
