"""Agent Battle arena — blind-bid combat with fog of war and skill decks."""

import copy
import hashlib
import random as _random
import secrets
import threading
import time
import uuid

from agent_battle import config
from agent_battle.persistence import Persistence


class ArenaError(Exception):
    def __init__(self, status, message, details=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details or {}


def _fog(value, max_val):
    ratio = value / max_val if max_val else 0
    if ratio <= 0.33:
        return "low"
    elif ratio <= 0.66:
        return "mid"
    return "high"


def _make_seed(battle_id, server_secret):
    """Deterministic seed from battle_id + server secret — not guessable by clients."""
    raw = hashlib.sha256(f"{battle_id}:{server_secret}".encode()).digest()
    return int.from_bytes(raw[:4], "big")


def _validate_skills(skills):
    if not skills:
        return []
    picked = []
    for s in skills:
        s = (s or "").strip().lower()
        if s and s in config.SKILL_POOL and s not in picked:
            picked.append(s)
    return picked[:3]


class Arena:
    RESOLVED_TTL = 3600  # seconds before resolved battles are purged from memory

    def __init__(self):
        self._lock = threading.RLock()
        self._persistence = Persistence(config.DB_PATH)
        self._agents = self._persistence.load_agents()
        self._api_keys = {}
        self._names = {}
        self._rooms = {}
        self._battles = self._persistence.load_battles()
        self._server_secret = secrets.token_hex(32)  # anti seed-prediction
        self._last_cleanup = time.monotonic()
        for battle in self._battles.values():
            battle.setdefault("resolved_at", None)
            if battle.get("room") and battle["status"] in ("created", "active"):
                self._rooms[battle["room"]] = battle["battle_id"]
        for agent in self._agents.values():
            self._api_keys[agent["api_key"]] = agent["agent_id"]
            if agent.get("name"):
                self._names[agent["name"]] = agent["agent_id"]

    def _maybe_cleanup(self):
        now = time.monotonic()
        if now - self._last_cleanup < 300:
            return
        self._last_cleanup = now
        stale = [
            bid for bid, b in self._battles.items()
            if b["status"] == "resolved"
            and b.get("resolved_at")
            and now - b["resolved_at"] > self.RESOLVED_TTL
        ]
        for bid in stale:
            del self._battles[bid]

    # ==================================================================
    # public API
    # ==================================================================

    def create_agent(self, name=None, skills=None, owner=None):
        with self._lock:
            name = (name or "").strip() or None
            owner = (owner or "").strip() or None
            if name and name in self._names:
                return self._public_agent(self._agents[self._names[name]])

            agent_id = "agent_" + uuid.uuid4().hex
            api_key = "ab_" + secrets.token_urlsafe(24)
            picked = _validate_skills(skills or [])
            agent = {
                "agent_id": agent_id,
                "api_key": api_key,
                "name": name,
                "owner": owner,
                "skills": picked,
                "balance": config.INITIAL_BALANCE,
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "active_battle_id": None,
                "created_at": time.time(),
            }
            self._agents[agent_id] = agent
            self._api_keys[api_key] = agent_id
            if name:
                self._names[name] = agent_id
            self._persistence.save_agent(agent)
            return self._public_agent(agent)

    def get_agent(self, api_key):
        with self._lock:
            return self._public_agent(self._agent_for_key(api_key))

    def create_battle(self, api_key, stake, room=None):
        with self._lock:
            agent = self._agent_for_key(api_key)
            self._ensure_available(agent)
            self._validate_stake(agent, stake)

            room = (room or "").strip() or None
            if not room:
                room = secrets.token_hex(3)
            elif room in self._rooms:
                raise ArenaError(409, "room code already in use")

            battle_id = "battle_" + uuid.uuid4().hex
            agent["balance"] -= stake
            agent["active_battle_id"] = battle_id
            self._rooms[room] = battle_id
            battle = {
                "battle_id": battle_id,
                "status": "created",
                "stake": stake,
                "room": room,
                "seed": _make_seed(battle_id, self._server_secret),
                "turn": 0,
                "participants": [agent["agent_id"]],
                "order": [agent["agent_id"]],
                "states": {agent["agent_id"]: self._new_player_state()},
                "pending_bids": {},
                "battle_log": [],
                "winner_id": None,
                "result": None,
                "created_at": time.time(),
                # per-battle skill state
                "skill_state": {},
            }
            self._battles[battle_id] = battle
            self._persistence.save_agent(agent)
            self._persistence.save_battle(battle)
            return self._battle_summary(battle)

    def find_battle_by_room(self, room_code):
        with self._lock:
            battle_id = self._rooms.get(room_code)
            if not battle_id:
                return None
            battle = self._battles[battle_id]
            if battle["status"] not in ("created", "active"):
                return None
            return self._public_battle_snapshot(battle)

    def join_battle(self, api_key, battle_id):
        with self._lock:
            agent = self._agent_for_key(api_key)
            battle = self._battle_for_id(battle_id)
            self._ensure_available(agent)
            if battle["status"] != "created":
                raise ArenaError(409, "battle is not joinable")
            if agent["agent_id"] in battle["participants"]:
                raise ArenaError(409, "agent cannot join its own battle")
            self._validate_stake(agent, battle["stake"])

            agent["balance"] -= battle["stake"]
            agent["active_battle_id"] = battle_id
            battle["participants"].append(agent["agent_id"])
            battle["order"].append(agent["agent_id"])
            battle["states"][agent["agent_id"]] = self._new_player_state()
            battle["status"] = "active"
            # Init skill state for both agents
            for pid in battle["participants"]:
                battle["skill_state"].setdefault(pid, {"focused_used": False, "guard_active": True, "poison_rounds": 0, "combo_win": 0})
            self._persistence.save_agent(agent)
            self._persistence.save_battle(battle)
            return self._battle_summary(battle)

    def get_battle(self, api_key, battle_id):
        with self._lock:
            agent = self._agent_for_key(api_key)
            battle = self._battle_for_id(battle_id)
            self._ensure_participant(agent, battle)
            return self._battle_view(agent, battle)

    def get_result(self, api_key, battle_id):
        with self._lock:
            agent = self._agent_for_key(api_key)
            battle = self._battle_for_id(battle_id)
            self._ensure_participant(agent, battle)
            if battle["status"] != "resolved":
                raise ArenaError(409, "battle is not resolved")
            return copy.deepcopy(battle["result"])

    def list_public_battles(self):
        with self._lock:
            return [self._public_battle_snapshot(b) for b in self._battles.values()]

    def list_open_battles(self, api_key=None):
        """Return joinable battles, filtering out same-owner battles."""
        with self._lock:
            self._maybe_cleanup()
            agent = None
            if api_key:
                try:
                    agent = self._agent_for_key(api_key)
                except ArenaError:
                    pass
            result = []
            for battle in self._battles.values():
                if battle["status"] != "created":
                    continue
                if agent and agent.get("owner"):
                    creator_id = battle["participants"][0]
                    creator = self._agents.get(creator_id)
                    if creator and creator.get("owner") == agent["owner"]:
                        continue  # anti-self-play
                result.append(self._battle_summary(battle))
            return result

    def get_public_battle(self, battle_id):
        with self._lock:
            return self._public_battle_snapshot(self._battle_for_id(battle_id))

    def submit_bid(self, api_key, battle_id, bid):
        with self._lock:
            agent = self._agent_for_key(api_key)
            battle = self._battle_for_id(battle_id)
            self._ensure_participant(agent, battle)
            if battle["status"] != "active":
                raise ArenaError(409, "battle is not active")

            agent_id = agent["agent_id"]
            state = battle["states"][agent_id]
            max_bid = state["mp"]
            if "overcharge" in self._agents[agent_id].get("skills", []):
                max_bid += 5

            bid = int(bid)
            if bid < 0 or bid > max_bid:
                raise ArenaError(409, f"bid must be 0..{max_bid}")
            if agent_id in battle["pending_bids"]:
                raise ArenaError(409, "already submitted a bid this round")

            battle["pending_bids"][agent_id] = bid

            if len(battle["pending_bids"]) == 2:
                self._resolve_bids(battle)

            return self._battle_view(agent, battle)

    # ==================================================================
    # bid resolution
    # ==================================================================

    def _resolve_bids(self, battle):
        a_id, b_id = battle["participants"]
        bid_a = battle["pending_bids"][a_id]
        bid_b = battle["pending_bids"][b_id]
        a_state = battle["states"][a_id]
        b_state = battle["states"][b_id]

        a_skills = self._agents[a_id].get("skills", [])
        b_skills = self._agents[b_id].get("skills", [])

        # Deduct MP (focused refunds first bid)
        a_ss = battle["skill_state"][a_id]
        b_ss = battle["skill_state"][b_id]

        a_mp_cost = 0 if ("focused" in a_skills and not a_ss["focused_used"]) else bid_a
        b_mp_cost = 0 if ("focused" in b_skills and not b_ss["focused_used"]) else bid_b
        a_state["mp"] -= a_mp_cost
        b_state["mp"] -= b_mp_cost
        a_ss["focused_used"] = True
        b_ss["focused_used"] = True

        rng = _random.Random(battle["seed"] + battle["turn"])
        note_a = ""
        note_b = ""
        events = []  # spectator commentary tags for this round

        if bid_a > bid_b:
            winner, loser = a_id, b_id
            w_bid, l_bid = bid_a, bid_b
            w_skills, l_skills = a_skills, b_skills
            w_ss, l_ss = a_ss, b_ss
            w_state, l_state = a_state, b_state
            w_mp_cost, l_mp_cost = a_mp_cost, b_mp_cost
        elif bid_b > bid_a:
            winner, loser = b_id, a_id
            w_bid, l_bid = bid_b, bid_a
            w_skills, l_skills = b_skills, a_skills
            w_ss, l_ss = b_ss, a_ss
            w_state, l_state = b_state, a_state
            w_mp_cost, l_mp_cost = b_mp_cost, a_mp_cost
        else:
            # tie
            events.append("tie")
            a_state["mp"] = min(config.MAX_MP, a_state["mp"] + 10)
            b_state["mp"] = min(config.MAX_MP, b_state["mp"] + 10)
            if "meditate" in a_skills:
                a_state["mp"] = min(config.MAX_MP, a_state["mp"] + 5)
            if "meditate" in b_skills:
                b_state["mp"] = min(config.MAX_MP, b_state["mp"] + 5)
            # Tick poison on tie rounds
            for pid, ss, st in [(a_id, a_ss, a_state), (b_id, b_ss, b_state)]:
                if ss["poison_rounds"] > 0:
                    st["hp"] -= 4
                    ss["poison_rounds"] -= 1
                    events.append("poison")

            self._apply_storm(battle, a_state, b_state, events)

            mp_note = "+15 MP" if "meditate" in a_skills else "+10 MP"
            note_a = f"tie ({bid_a} vs {bid_b}), {mp_note}"
            note_b = f"tie ({bid_b} vs {bid_a}), {'+15 MP' if 'meditate' in b_skills else '+10 MP'}"
            self._log_turn(battle, a_id, b_id, bid_a, bid_b, note_a, note_b, None, 0, events)

            if self._is_terminal(battle):
                wid = self._determine_winner(battle)
                return self._resolve_battle(battle, wid, "hp_depleted")
            if battle["turn"] >= config.MAX_TURNS:
                wid = self._determine_winner(battle)
                return self._resolve_battle(battle, wid, "turn_limit")
            return

        damage = w_bid - l_bid

        # ---- winner skills ----
        if "berserker" in w_skills and w_state["hp"] < config.MAX_HP * 0.33:
            damage = int(damage * 1.5)
            events.append("berserker")

        if "poison" in w_skills:
            l_ss["poison_rounds"] = 3
            events.append("poison_applied")

        # ---- loser skills ----
        if "guard" in l_skills and l_ss["guard_active"]:
            l_ss["guard_active"] = False
            damage = 0
            events.append("guard")

        if "thornmail" in l_skills and damage > 0:
            w_state["hp"] -= 3  # recoil
            events.append("thornmail")

        # Apply damage
        l_state["hp"] -= damage

        # Vampire heal
        if "vampire" in w_skills and damage > 0:
            heal = max(1, int(damage * 0.3))
            w_state["hp"] = min(config.MAX_HP, w_state["hp"] + heal)
            events.append("vampire")

        # Overcharge: if you used MP beyond your natural pool (bid > pre-deduction MP),
        # you lose 5 HP. Only the loser takes overcharge self-damage.
        if "overcharge" in l_skills:
            pre_mp = l_state["mp"] + l_mp_cost  # MP before this bid was deducted
            if l_bid > pre_mp:
                l_state["hp"] -= 5
                events.append("overcharge")

        # Tick poison on loser
        if l_ss["poison_rounds"] > 0:
            l_state["hp"] -= 4
            l_ss["poison_rounds"] -= 1
            events.append("poison")

        self._apply_storm(battle, a_state, b_state, events)

        note_w = f"won bid ({w_bid} vs {l_bid}), dealt {damage} dmg"
        note_l = f"lost bid ({l_bid} vs {w_bid}), took {damage} dmg"

        self._log_turn(battle, a_id, b_id, bid_a, bid_b, note_a if a_id == winner else note_w, note_b if b_id == winner else note_l, winner, damage, events)

        # check terminal
        if self._is_terminal(battle):
            wid = self._determine_winner(battle)
            reason = "hp_depleted"
            return self._resolve_battle(battle, wid, reason)

        if battle["turn"] >= config.MAX_TURNS:
            wid = self._determine_winner(battle)
            return self._resolve_battle(battle, wid, "turn_limit")

        return None

    def _apply_storm(self, battle, a_state, b_state, events):
        """Escalating arena damage to both players, forcing a timely finish."""
        over = battle["turn"] - config.STORM_START
        if over < 0:
            return
        dmg = over + 1
        a_state["hp"] -= dmg
        b_state["hp"] -= dmg
        events.append(f"storm:{dmg}")

    def _log_turn(self, battle, a_id, b_id, bid_a, bid_b, note_a, note_b, winner_id, damage, events=None):
        battle["battle_log"].append({
            "turn": battle["turn"],
            "bids": {a_id: bid_a, b_id: bid_b},
            "notes": {a_id: note_a, b_id: note_b},
            "winner": winner_id,
            "damage": damage,
            "events": list(events or []),
            "after": copy.deepcopy(battle["states"]),
        })
        battle["pending_bids"] = {}
        battle["turn"] += 1

    def _is_terminal(self, battle):
        return any(s["hp"] <= 0 for s in battle["states"].values())

    def _determine_winner(self, battle):
        a_id, b_id = battle["participants"]
        a_hp = battle["states"][a_id]["hp"]
        b_hp = battle["states"][b_id]["hp"]
        if a_hp <= 0 and b_hp <= 0:
            return None
        if a_hp <= 0:
            return b_id
        if b_hp <= 0:
            return a_id
        if a_hp > b_hp:
            return a_id
        if b_hp > a_hp:
            return b_id
        return None

    def _resolve_battle(self, battle, winner_id, reason):
        if battle["status"] == "resolved":
            return self._battle_summary(battle)

        battle["status"] = "resolved"
        battle["resolved_at"] = time.monotonic()
        battle["winner_id"] = winner_id
        if battle.get("room"):
            self._rooms.pop(battle["room"], None)
        pot = battle["stake"] * 2
        if winner_id is None:
            for agent_id in battle["participants"]:
                self._agents[agent_id]["balance"] += battle["stake"]
                self._agents[agent_id]["draws"] += 1
                self._agents[agent_id]["active_battle_id"] = None
                self._persistence.save_agent(self._agents[agent_id])
        else:
            self._agents[winner_id]["balance"] += pot
            self._agents[winner_id]["wins"] += 1
            self._agents[winner_id]["active_battle_id"] = None
            self._persistence.save_agent(self._agents[winner_id])
            loser_id = self._opponent_id(battle, winner_id)
            self._agents[loser_id]["losses"] += 1
            self._agents[loser_id]["active_battle_id"] = None
            self._persistence.save_agent(self._agents[loser_id])

        battle["result"] = {
            "battle_id": battle["battle_id"],
            "status": "resolved",
            "reason": reason,
            "winner_id": winner_id,
            "stake": battle["stake"],
            "pot": pot,
            "turns_played": battle["turn"],
            "balances": {
                agent_id: self._agents[agent_id]["balance"]
                for agent_id in battle["participants"]
            },
            "final_states": copy.deepcopy(battle["states"]),
            "battle_log": copy.deepcopy(battle["battle_log"]),
        }
        self._persistence.save_battle(battle)
        return self._battle_summary(battle)

    # ==================================================================
    # helpers
    # ==================================================================

    def _new_player_state(self):
        return {"hp": config.INITIAL_HP, "mp": config.INITIAL_MP}

    def _validate_stake(self, agent, stake):
        if stake != config.FIXED_STAKE:
            raise ArenaError(400, f"stake must be {config.FIXED_STAKE}")
        if agent["balance"] < stake:
            raise ArenaError(409, "insufficient balance")

    def _ensure_available(self, agent):
        active_id = agent["active_battle_id"]
        if active_id is None:
            return
        battle = self._battles.get(active_id)
        # Self-heal ghost references: if the battle was cleaned up or already
        # resolved, the agent is actually free — clear the stale pointer and
        # let it proceed instead of being locked out forever.
        if battle is None or battle["status"] == "resolved":
            agent["active_battle_id"] = None
            self._persistence.save_agent(agent)
            return
        # Genuinely busy: surface the active battle so the client can route the
        # user back to it (resume / spectate) instead of a dead-end 409.
        raise ArenaError(
            409,
            "you already have an ongoing battle — finish or leave it before starting another",
            details={"active_battle_id": active_id, "active_battle_status": battle["status"]},
        )

    def _ensure_participant(self, agent, battle):
        if agent["agent_id"] not in battle["participants"]:
            raise ArenaError(403, "agent is not a participant in this battle")

    def _agent_for_key(self, api_key):
        if not api_key or api_key not in self._api_keys:
            raise ArenaError(401, "invalid or missing api key")
        return self._agents[self._api_keys[api_key]]

    def _battle_for_id(self, battle_id):
        if battle_id not in self._battles:
            raise ArenaError(404, "battle not found")
        return self._battles[battle_id]

    def _opponent_id(self, battle, agent_id):
        for pid in battle["participants"]:
            if pid != agent_id:
                return pid
        return None

    # ==================================================================
    # serialisation
    # ==================================================================

    def _battle_view(self, agent, battle):
        agent_id = agent["agent_id"]
        opponent_id = self._opponent_id(battle, agent_id)
        view = self._battle_summary(battle)
        view["self"] = copy.deepcopy(battle["states"][agent_id])
        view["self"]["skills"] = self._agents[agent_id].get("skills", [])

        if opponent_id and battle["status"] in ("active", "resolved"):
            opp = battle["states"][opponent_id]
            opp_skills = self._agents[opponent_id].get("skills", [])
            view["opponent"] = {
                "hp": _fog(opp["hp"], config.MAX_HP),
                "mp": _fog(opp["mp"], config.MAX_MP),
                "skills": opp_skills,
            }
        else:
            view["opponent"] = None

        view["needs_action"] = (
            battle["status"] == "active"
            and agent_id not in battle["pending_bids"]
        )
        # Storm telegraph: damage both players take THIS turn if it resolves now.
        next_storm = max(0, battle["turn"] - config.STORM_START + 1)
        view["storm"] = {
            "active": next_storm > 0,
            "damage": next_storm,
            "starts_turn": config.STORM_START,
            "max_turns": config.MAX_TURNS,
        }
        view["battle_log"] = self._fog_battle_log(battle["battle_log"], agent_id, opponent_id)
        return view

    def _fog_battle_log(self, log, agent_id, opponent_id):
        """Return battle log with opponent's exact stats replaced by fog values."""
        fogged = []
        for entry in log:
            e = copy.deepcopy(entry)
            if opponent_id and opponent_id in e.get("after", {}):
                opp = e["after"][opponent_id]
                opp["hp"] = _fog(opp["hp"], config.MAX_HP)
                opp["mp"] = _fog(opp["mp"], config.MAX_MP)
            fogged.append(e)
        return fogged

    def _battle_summary(self, battle):
        return {
            "battle_id": battle["battle_id"],
            "status": battle["status"],
            "stake": battle["stake"],
            "room": battle.get("room"),
            "turn": battle.get("turn", 0),
            "participants": list(battle.get("participants", [])),
            "winner_id": battle.get("winner_id"),
            "created_at": battle.get("created_at"),
        }

    def _public_battle_snapshot(self, battle):
        participants = list(battle.get("participants", []))
        return {
            "battle_id": battle["battle_id"],
            "status": battle["status"],
            "stake": battle["stake"],
            "pot": battle["stake"] * len(participants),
            "turn": battle.get("turn", 0),
            "participants": participants,
            "winner_id": battle.get("winner_id"),
            "states": copy.deepcopy(battle.get("states", {})),
            "skills": {pid: self._agents[pid].get("skills", []) for pid in participants if pid in self._agents},
            "battle_log": copy.deepcopy(battle.get("battle_log", [])),
            "result": copy.deepcopy(battle.get("result")),
            "created_at": battle.get("created_at"),
            "storm_start": config.STORM_START,
            "max_turns": config.MAX_TURNS,
            "max_hp": config.MAX_HP,
            "max_mp": config.MAX_MP,
        }

    def _public_agent(self, agent):
        return {
            "agent_id": agent["agent_id"],
            "api_key": agent["api_key"],
            "name": agent.get("name"),
            "owner": agent.get("owner"),
            "skills": agent.get("skills", []),
            "balance": agent["balance"],
            "wins": agent["wins"],
            "losses": agent["losses"],
            "draws": agent["draws"],
            "active_battle_id": agent["active_battle_id"],
            "created_at": agent.get("created_at"),
        }
