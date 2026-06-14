"""Agent Battle config — overridable via environment variables."""

import os

MAX_TURNS = int(os.environ.get("AGENT_BATTLE_MAX_TURNS", "30"))
FIXED_STAKE = int(os.environ.get("AGENT_BATTLE_STAKE", "100"))
INITIAL_BALANCE = int(os.environ.get("AGENT_BATTLE_INITIAL_BALANCE", "1000"))
INITIAL_HP = int(os.environ.get("AGENT_BATTLE_INITIAL_HP", "100"))
INITIAL_MP = int(os.environ.get("AGENT_BATTLE_INITIAL_MP", "50"))
MAX_HP = int(os.environ.get("AGENT_BATTLE_MAX_HP", "100"))
MAX_MP = int(os.environ.get("AGENT_BATTLE_MAX_MP", "100"))
DB_PATH = os.environ.get("AGENT_BATTLE_DB_PATH", "")
RATE_LIMIT_PER_MINUTE = int(os.environ.get("AGENT_BATTLE_RATE_LIMIT", "300"))

# Arena Storm (生死圈): from STORM_START onward, every turn deals escalating
# damage to BOTH players — turn T deals (T - STORM_START + 1) HP. This forces
# battles to resolve in a watchable number of turns instead of dragging to the
# cap, and creates rising tension a spectator can follow.
STORM_START = int(os.environ.get("AGENT_BATTLE_STORM_START", "10"))

# Disconnect handling: if an active battle sees no bid activity for this many
# seconds, it's considered abandoned. The player who is still responding wins by
# timeout; if neither has acted, the higher-HP player wins (draw if tied). This
# frees a stuck player whose opponent went offline instead of locking the battle
# forever (turns only advance when BOTH sides bid, so a silent opponent would
# otherwise freeze the match — storm included). Generous by default to tolerate
# slow LLM turns and rate limiting.
BID_TIMEOUT = int(os.environ.get("AGENT_BATTLE_BID_TIMEOUT", "120"))

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
