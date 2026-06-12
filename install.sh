#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$ROOT_DIR/scripts/install_skill.sh"

cat <<'MSG'

Agent Battle is installed.

Run the arena:
  ./run.sh

Test a sample battle in another terminal:
  python3 -m examples.strategy_battle --base-url http://127.0.0.1:8080 --agent-a balanced --agent-b aggressive

MSG
