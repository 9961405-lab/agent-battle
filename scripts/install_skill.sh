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

mark_seen() {  # returns 0 if newly seen, 1 if duplicate
  local key="$1"
  case " $seen " in *" $key "*) return 1 ;; esac
  seen="$seen $key"; return 0
}

# 1) Auto-detect known runtimes — install only into roots that already exist.
for entry in "${CANDIDATES[@]}"; do
  root="${entry%%:*}"; sub="${entry##*:}"
  [ -d "$root" ] || continue
  mark_seen "$(inode_key "$root"):$sub" || continue
  install_to "$root/$sub/agent-battle"
  installed=1
  legacy="$root/$sub/agent-battle-skill"   # remove cruft from earlier manual installs
  [ -d "$legacy" ] && { rm -rf "$legacy"; echo "Removed stale copy   -> $legacy"; }
done

# 2) Escape hatch for ANY agent we don't know about: the user points us straight
# at one or more skills folders (colon- or comma-separated). These are FORCED —
# created if missing — so it works even for a runtime we've never heard of:
#   AGENT_SKILLS_DIR=~/.myagent/skills ./install.sh
if [ -n "${AGENT_SKILLS_DIR:-}" ]; then
  IFS=':,' read -r -a _extra <<< "${AGENT_SKILLS_DIR}"
  for d in "${_extra[@]}"; do
    [ -n "$d" ] || continue
    mkdir -p "$d"
    mark_seen "$(inode_key "$d"):." || continue
    install_to "$d/agent-battle"
    installed=1
  done
fi

# 3) Nothing matched: don't guess a location. Tell the user how to finish.
if [ "$installed" -eq 0 ]; then
  cat <<MSG
No known agent runtime detected on this machine.

This skill works with ANY agent — pick one:

  • If your agent loads "skills" from a folder, copy the skill there:
      cp -R "$SKILL_SOURCE" /path/to/your-agent/skills/agent-battle
    or re-run pointing us at it:
      AGENT_SKILLS_DIR=/path/to/your-agent/skills ./install.sh

  • Or skip installing entirely and just hand your agent this URL:
      https://raw.githubusercontent.com/9961405-lab/agent-battle/main/skills/agent-battle/SKILL.md
    It's self-contained — the agent reads it and plays over the public HTTP API.
MSG
fi
