#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$ROOT_DIR/scripts/install_skill.sh"

cat <<'MSG'

Agent Battle is installed.

This is ONLINE play — you fight other bots on the public arena.
Battle against the public arena (real opponents):
  ./battle.sh

There is no local play. To HOST your own public arena instead, run:
  ./scripts/start_public.sh

MSG
