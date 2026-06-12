"""Agent Battle configuration, overridable via environment variables."""

import os

MAX_TURNS = int(os.environ.get("AGENT_BATTLE_MAX_TURNS", "200"))
FIXED_STAKE = int(os.environ.get("AGENT_BATTLE_STAKE", "100"))
INITIAL_BALANCE = int(os.environ.get("AGENT_BATTLE_INITIAL_BALANCE", "1000"))
INITIAL_HP = int(os.environ.get("AGENT_BATTLE_INITIAL_HP", "100"))
INITIAL_MP = int(os.environ.get("AGENT_BATTLE_INITIAL_MP", "50"))
MAX_HP = int(os.environ.get("AGENT_BATTLE_MAX_HP", "100"))
MAX_MP = int(os.environ.get("AGENT_BATTLE_MAX_MP", "50"))
DB_PATH = os.environ.get("AGENT_BATTLE_DB_PATH", "")
RATE_LIMIT_PER_MINUTE = int(os.environ.get("AGENT_BATTLE_RATE_LIMIT", "60"))
