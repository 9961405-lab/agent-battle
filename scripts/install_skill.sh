#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_SOURCE="$ROOT_DIR/skills/agent-battle"

# Install the skill for whichever agent runtimes are present. Claude Code loads
# skills from ~/.claude/skills, Codex from ~/.codex/skills. We install to every
# applicable location so the same repo works regardless of which agent the
# player uses. If neither config dir exists yet, default to Claude Code.
install_to() {
  local target="$1"
  mkdir -p "$(dirname "$target")"
  rm -rf "$target"
  cp -R "$SKILL_SOURCE" "$target"
  echo "Installed agent-battle skill to $target"
}

installed=0

# Claude Code
CLAUDE_HOME="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
if [ -d "$CLAUDE_HOME" ]; then
  install_to "$CLAUDE_HOME/skills/agent-battle"
  installed=1
fi

# Codex
CODEX_DIR="${CODEX_HOME:-$HOME/.codex}"
if [ -d "$CODEX_DIR" ]; then
  install_to "$CODEX_DIR/skills/agent-battle"
  installed=1
fi

# If neither runtime dir exists yet, default to Claude Code.
if [ "$installed" -eq 0 ]; then
  install_to "$HOME/.claude/skills/agent-battle"
fi
