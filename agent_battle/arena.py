"""Agent Battle arena — turn-based combat with deterministic RNG."""

import copy
import random as _random
import secrets
import threading
import uuid

from agent_battle import config
from agent_battle.persistence import Persistence

VALID_ACTIONS = {"attack", "heavy", "defend", "heal", "forfeit"}


class ArenaError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Arena:
    def __init__(self):
        self._lock = threading.RLock()
        self._persistence = Persistence(config.DB_PATH)
        self._agents = self._persistence.load_agents()
        self._api_keys = {}
        self._names = {}
        self._rooms = {}
        self._battles = self._persistence.load_battles()
        for battle in self._battles.values():
            if battle.get("room") and battle["status"] in ("created", "active"):
                self._rooms[battle["room"]] = battle["battle_id"]
        for agent in self._agents.values():
            self._api_keys[agent["api_key"]] = agent["agent_id"]
            if agent.get("name"):
                self._names[agent["name"]] = agent["agent_id"]

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def create_agent(self, name=None):
        with self._lock:
            name = (name or "").strip() or None
            if name and name in self._names:
                return self._public_agent(self._agents[self._names[name]])

            agent_id = "agent_" + uuid.uuid4().hex
            api_key = "ab_" + secrets.token_urlsafe(24)
            agent = {
                "agent_id": agent_id,
                "api_key": api_key,
                "name": name,
                "balance": config.INITIAL_BALANCE,
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "active_battle_id": None,
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

            # Generate or validate room code
            room = (room or "").strip() or None
            if not room:
                room = secrets.token_hex(3)  # 6-char code like "a1b2c3"
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
                "seed": _random.randint(0, 2**31 - 1),
                "turn": 0,
                "participants": [agent["agent_id"]],
                "order": [agent["agent_id"]],
                "states": {
                    agent["agent_id"]: self._new_player_state(),
                },
                "battle_log": [],
                "winner_id": None,
                "result": None,
            }
            self._battles[battle_id] = battle
            self._persistence.save_agent(agent)
            self._persistence.save_battle(battle)
            return self._battle_summary(battle)

    def find_battle_by_room(self, room_code):
        """Return battle summary for a room code, or None."""
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
            return [self._public_battle_snapshot(battle) for battle in self._battles.values()]

    def list_open_battles(self):
        with self._lock:
            return [
                self._battle_summary(battle)
                for battle in self._battles.values()
                if battle["status"] == "created"
            ]

    def get_public_battle(self, battle_id):
        with self._lock:
            return self._public_battle_snapshot(self._battle_for_id(battle_id))

    def submit_action(self, api_key, battle_id, action):
        with self._lock:
            agent = self._agent_for_key(api_key)
            battle = self._battle_for_id(battle_id)
            self._ensure_participant(agent, battle)
            if battle["status"] != "active":
                raise ArenaError(409, "battle is not active")
            if action not in VALID_ACTIONS:
                raise ArenaError(400, "invalid action")

            agent_id = agent["agent_id"]

            # forfeit is always allowed, even out of turn
            if action == "forfeit":
                opponent_id = self._opponent_id(battle, agent_id)
                return self._resolve_battle(battle, opponent_id, "forfeit")

            # enforce turn order
            current_actor = battle["order"][battle["turn"] % 2]
            if agent_id != current_actor:
                raise ArenaError(409, "not your turn")

            # validate resource requirements
            state = battle["states"][agent_id]
            err = self._validate_action(state, action)
            if err:
                raise ArenaError(409, err)

            self._apply_action(battle, agent_id, action)
            return self._battle_view(agent, battle)

    # ------------------------------------------------------------------
    # action application
    # ------------------------------------------------------------------

    def _validate_action(self, state, action):
        if action == "heavy" and state["mp"] < 15:
            return "not enough MP for heavy (need 15)"
        if action == "heal" and state["mp"] < 10:
            return "not enough MP to heal (need 10)"
        return None

    def _apply_action(self, battle, actor, action):
        opponent = self._opponent_id(battle, actor)
        me = battle["states"][actor]
        them = battle["states"][opponent]

        # deterministic RNG for this turn
        rng = _random.Random(battle["seed"] + battle["turn"])

        # clear my defending at the start of my turn
        me["defending"] = False

        note = ""
        if action == "attack":
            base = 10 + int(rng.random() * 8)  # 10..17
            dmg = base // 2 if them["defending"] else base
            them["hp"] -= dmg
            them["defending"] = False
            note = f"{actor} attacks for {dmg}"

        elif action == "heavy":
            me["mp"] -= 15
            if rng.random() > 0.25:  # 75% hit
                base = 22 + int(rng.random() * 10)  # 22..31
                dmg = base // 2 if them["defending"] else base
                them["hp"] -= dmg
                them["defending"] = False
                note = f"{actor} heavy attack for {dmg}"
            else:
                note = f"{actor} heavy attack MISSED"

        elif action == "defend":
            me["defending"] = True
            me["mp"] = min(config.MAX_MP, me["mp"] + 5)
            note = f"{actor} defends (+5 MP)"

        elif action == "heal":
            me["mp"] -= 10
            amount = 15 + int(rng.random() * 10)  # 15..24
            healed = min(config.MAX_HP - me["hp"], amount)
            me["hp"] += healed
            note = f"{actor} heals {healed} HP"

        battle["battle_log"].append(
            {
                "turn": battle["turn"],
                "actor": actor,
                "action": action,
                "note": note,
                "after": copy.deepcopy(battle["states"]),
            }
        )
        battle["turn"] += 1

        # check terminal after each turn
        if self._is_terminal(battle):
            winner_id = self._determine_winner(battle)
            reason = "hp_depleted"
            if winner_id is None and battle["turn"] >= config.MAX_TURNS:
                reason = "turn_limit"
            elif winner_id is None:
                reason = "hp_depleted"
            return self._resolve_battle(battle, winner_id, reason)

        if battle["turn"] >= config.MAX_TURNS:
            winner_id = self._determine_winner(battle)
            return self._resolve_battle(battle, winner_id, "turn_limit")

        return None

    def _is_terminal(self, battle):
        return any(s["hp"] <= 0 for s in battle["states"].values())

    def _determine_winner(self, battle):
        a_id, b_id = battle["participants"]
        a_hp = battle["states"][a_id]["hp"]
        b_hp = battle["states"][b_id]["hp"]
        if a_hp <= 0 and b_hp <= 0:
            return None  # draw
        if a_hp <= 0:
            return b_id
        if b_hp <= 0:
            return a_id
        if a_hp > b_hp:
            return a_id
        if b_hp > a_hp:
            return b_id
        return None  # draw

    def _resolve_battle(self, battle, winner_id, reason):
        if battle["status"] == "resolved":
            return self._battle_summary(battle)

        battle["status"] = "resolved"
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

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _new_player_state(self):
        return {
            "hp": config.INITIAL_HP,
            "mp": config.INITIAL_MP,
            "defending": False,
        }

    def _validate_stake(self, agent, stake):
        if stake != config.FIXED_STAKE:
            raise ArenaError(400, f"stake must be {config.FIXED_STAKE}")
        if agent["balance"] < stake:
            raise ArenaError(409, "insufficient balance")

    def _ensure_available(self, agent):
        if agent["active_battle_id"] is not None:
            raise ArenaError(409, "agent is already in a battle")

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
        for participant_id in battle["participants"]:
            if participant_id != agent_id:
                return participant_id
        return None

    # ------------------------------------------------------------------
    # serialisation helpers
    # ------------------------------------------------------------------

    def _battle_view(self, agent, battle):
        agent_id = agent["agent_id"]
        opponent_id = self._opponent_id(battle, agent_id)
        view = self._battle_summary(battle)
        view["self"] = copy.deepcopy(battle["states"][agent_id])
        view["opponent"] = (
            copy.deepcopy(battle["states"][opponent_id]) if opponent_id else None
        )
        view["needs_action"] = (
            battle["status"] == "active"
            and agent_id == battle["order"][battle["turn"] % 2]
        )
        view["battle_log"] = copy.deepcopy(battle["battle_log"])
        return view

    def _battle_summary(self, battle):
        return {
            "battle_id": battle["battle_id"],
            "status": battle["status"],
            "stake": battle["stake"],
            "room": battle.get("room"),
            "turn": battle["turn"],
            "participants": list(battle["participants"]),
            "winner_id": battle["winner_id"],
        }

    def _public_battle_snapshot(self, battle):
        return {
            "battle_id": battle["battle_id"],
            "status": battle["status"],
            "stake": battle["stake"],
            "pot": battle["stake"] * len(battle["participants"]),
            "turn": battle["turn"],
            "participants": list(battle["participants"]),
            "winner_id": battle["winner_id"],
            "states": copy.deepcopy(battle["states"]),
            "battle_log": copy.deepcopy(battle["battle_log"]),
            "result": copy.deepcopy(battle["result"]),
        }

    def _public_agent(self, agent):
        return {
            "agent_id": agent["agent_id"],
            "api_key": agent["api_key"],
            "balance": agent["balance"],
            "wins": agent["wins"],
            "losses": agent["losses"],
            "draws": agent["draws"],
            "active_battle_id": agent["active_battle_id"],
        }
