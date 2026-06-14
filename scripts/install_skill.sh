#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_SOURCE="$ROOT_DIR/skills/agent-battle"

# Install the agent-battle skill for EVERY agent runtime present on this machine.
# Different runtimes load skills from different folders, so each candidate below
# is "<runtime root dir>:<skills subdir>". We install into each root that exists
# (and dedupe by real path, since e.g. macOS is case-insensitive and ~/.workbuddy
# and ~/.workBuddy resolve to the same folder). Env overrides win when set.
#
# To support a new runtime, just add a line to CANDIDATES.
CANDIDATES=(
  "${CLAUDE_CONFIG_DIR:-$HOME/.claude}:skills"        # Claude Code
  "${CODEX_HOME:-$HOME/.codex}:skills"                # Codex
  "${WORKBUDDY_HOME:-$HOME/.workbuddy}:skills"        # WorkBuddy
  "$HOME/.workBuddy:skills"                           # WorkBuddy (case-sensitive FS)
  "${CURSOR_HOME:-$HOME/.cursor}:skills-cursor"       # Cursor
)

install_to() {
  local target="$1"
  mkdir -p "$(dirname "$target")"
  rm -rf "$target"
  cp -R "$SKILL_SOURCE" "$target"
  echo "Installed agent-battle skill -> $target"
}

# Identify a directory by device:inode so that two paths pointing at the SAME
# folder (e.g. ~/.workbuddy vs ~/.workBuddy on a case-insensitive macOS volume)
# dedupe, while genuinely distinct folders on a case-sensitive FS do not.
inode_key() {
  stat -f '%d:%i' "$1" 2>/dev/null || stat -c '%d:%i' "$1" 2>/dev/null || echo "$1"
}

installed=0
seen=""
for entry in "${CANDIDATES[@]}"; do
  root="${entry%%:*}"
  sub="${entry##*:}"
  [ -d "$root" ] || continue
  key="$(inode_key "$root"):$sub"
  case " $seen " in *" $key "*) continue ;; esac   # same real folder -> skip
  seen="$seen $key"
  target="$root/$sub/agent-battle"
  install_to "$target"
  installed=1

  # Clean up any older differently-named copy of this skill in the same folder.
  legacy="$root/$sub/agent-battle-skill"
  if [ -d "$legacy" ]; then
    rm -rf "$legacy"
    echo "Removed stale copy   -> $legacy"
  fi
done

# If no known runtime dir exists yet, default to Claude Code.
if [ "$installed" -eq 0 ]; then
  install_to "$HOME/.claude/skills/agent-battle"
fi
