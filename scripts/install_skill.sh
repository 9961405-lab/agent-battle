#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_SOURCE="$ROOT_DIR/skills/agent-battle"
SKILL_TARGET="${CODEX_HOME:-$HOME/.codex}/skills/agent-battle"

mkdir -p "$(dirname "$SKILL_TARGET")"
rm -rf "$SKILL_TARGET"
cp -R "$SKILL_SOURCE" "$SKILL_TARGET"

echo "Installed agent-battle skill to $SKILL_TARGET"
