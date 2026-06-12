"""Agent Battle config — overridable via environment variables."""

import os

MAX_TURNS = int(os.environ.get("AGENT_BATTLE_MAX_TURNS", "200"))
FIXED_STAKE = int(os.environ.get("AGENT_BATTLE_STAKE", "100"))
INITIAL_BALANCE = int(os.environ.get("AGENT_BATTLE_INITIAL_BALANCE", "1000"))
INITIAL_HP = int(os.environ.get("AGENT_BATTLE_INITIAL_HP", "100"))
INITIAL_MP = int(os.environ.get("AGENT_BATTLE_INITIAL_MP", "50"))
MAX_HP = int(os.environ.get("AGENT_BATTLE_MAX_HP", "100"))
MAX_MP = int(os.environ.get("AGENT_BATTLE_MAX_MP", "100"))
DB_PATH = os.environ.get("AGENT_BATTLE_DB_PATH", "")
RATE_LIMIT_PER_MINUTE = int(os.environ.get("AGENT_BATTLE_RATE_LIMIT", "300"))

# Skill pool: pick 3 when registering an agent.
SKILL_POOL = {
    "vampire":    "win bid → heal 30% of damage dealt",
    "berserker":  "HP < 33% → winning bids deal +50% damage",
    "focused":    "first bid of the battle costs 0 MP",
    "thornmail":  "lose bid → opponent takes 3 recoil damage",
    "meditate":   "tie → you gain +15 MP instead of +10",
    "poison":     "win bid → apply 3-round poison (4 dmg/round)",
    "guard":      "start battle with one shield that blocks first lost bid's damage",
    "overcharge": "can bid up to MP+5; if you lose the bid, take 5 self-damage",
}
