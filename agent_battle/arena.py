import copy
import secrets
import uuid


INITIAL_BALANCE = 1000
INITIAL_HP = 100
INITIAL_ENERGY = 50
MAX_ENERGY = 100
FIXED_STAKE = 100
MAX_ROUNDS = 20
VALID_ACTIONS = {"attack", "defend", "charge", "special", "forfeit"}


class ArenaError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Arena:
    def __init__(self):
        self._agents = {}
        self._api_keys = {}
        self._battles = {}

    def create_agent(self):
        agent_id = "agent_" + uuid.uuid4().hex
        api_key = "ab_" + secrets.token_urlsafe(24)
        agent = {
            "agent_id": agent_id,
            "api_key": api_key,
            "balance": INITIAL_BALANCE,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "active_battle_id": None,
        }
        self._agents[agent_id] = agent
        self._api_keys[api_key] = agent_id
        return self._public_agent(agent)

    def get_agent(self, api_key):
        return self._public_agent(self._agent_for_key(api_key))

    def create_battle(self, api_key, stake):
        agent = self._agent_for_key(api_key)
        self._ensure_available(agent)
        self._validate_stake(agent, stake)

        battle_id = "battle_" + uuid.uuid4().hex
        agent["balance"] -= stake
        agent["active_battle_id"] = battle_id
        battle = {
            "battle_id": battle_id,
            "status": "created",
            "stake": stake,
            "round": 1,
            "participants": [agent["agent_id"]],
            "states": {
                agent["agent_id"]: self._new_player_state(),
            },
            "pending_actions": {},
            "battle_log": [],
            "winner_id": None,
            "result": None,
        }
        self._battles[battle_id] = battle
        return self._battle_summary(battle)

    def join_battle(self, api_key, battle_id):
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
        battle["states"][agent["agent_id"]] = self._new_player_state()
        battle["status"] = "active"
        return self._battle_summary(battle)

    def get_battle(self, api_key, battle_id):
        agent = self._agent_for_key(api_key)
        battle = self._battle_for_id(battle_id)
        self._ensure_participant(agent, battle)
        return self._battle_view(agent, battle)

    def get_result(self, api_key, battle_id):
        agent = self._agent_for_key(api_key)
        battle = self._battle_for_id(battle_id)
        self._ensure_participant(agent, battle)
        if battle["status"] != "resolved":
            raise ArenaError(409, "battle is not resolved")
        return copy.deepcopy(battle["result"])

    def submit_action(self, api_key, battle_id, action):
        agent = self._agent_for_key(api_key)
        battle = self._battle_for_id(battle_id)
        self._ensure_participant(agent, battle)
        if battle["status"] != "active":
            raise ArenaError(409, "battle is not active")
        if action not in VALID_ACTIONS:
            raise ArenaError(400, "invalid action")
        agent_id = agent["agent_id"]
        if agent_id in battle["pending_actions"]:
            raise ArenaError(409, "agent already submitted an action this round")

        battle["pending_actions"][agent_id] = action
        if action == "forfeit":
            opponent_id = self._opponent_id(battle, agent_id)
            return self._resolve_battle(battle, opponent_id, "forfeit")
        if len(battle["pending_actions"]) == 2:
            self._resolve_round(battle)
        return self._battle_view(agent, battle)

    def _resolve_round(self, battle):
        before = copy.deepcopy(battle["states"])
        participants = battle["participants"]
        resolved = {}
        damage = {}

        for agent_id in participants:
            requested = battle["pending_actions"][agent_id]
            action = self._resolve_action(battle["states"][agent_id], requested)
            resolved[agent_id] = {"requested": requested, "resolved": action}
            damage[agent_id] = self._base_damage(action)

        for agent_id in participants:
            state = battle["states"][agent_id]
            action = resolved[agent_id]["resolved"]
            if action == "attack":
                state["energy"] -= 10
            elif action == "defend":
                state["energy"] = min(MAX_ENERGY, state["energy"] + 5)
            elif action == "charge":
                state["energy"] = min(MAX_ENERGY, state["energy"] + 20)
            elif action == "special":
                state["energy"] -= 30

        for agent_id in participants:
            opponent_id = self._opponent_id(battle, agent_id)
            incoming = damage[opponent_id]
            if resolved[agent_id]["resolved"] == "defend":
                incoming = incoming // 2
            battle["states"][agent_id]["hp"] -= incoming

        for agent_id in participants:
            cooldown = battle["states"][agent_id]["cooldowns"]["special"]
            if cooldown > 0:
                battle["states"][agent_id]["cooldowns"]["special"] = cooldown - 1
        for agent_id in participants:
            if resolved[agent_id]["resolved"] == "special":
                battle["states"][agent_id]["cooldowns"]["special"] = 3

        battle["battle_log"].append(
            {
                "round": battle["round"],
                "before": before,
                "actions": resolved,
                "after": copy.deepcopy(battle["states"]),
            }
        )
        battle["pending_actions"] = {}

        winner_id = self._winner_after_round(battle)
        if winner_id is not None or self._is_draw_after_round(battle):
            return self._resolve_battle(battle, winner_id, "hp_depleted")

        if battle["round"] >= MAX_ROUNDS:
            return self._resolve_by_round_limit(battle)

        battle["round"] += 1
        return None

    def _resolve_action(self, state, requested):
        if requested == "special":
            if state["cooldowns"]["special"] > 0:
                requested = "attack"
            elif state["energy"] < 30:
                return "defend"
        if requested == "attack" and state["energy"] < 10:
            return "defend"
        return requested

    def _base_damage(self, action):
        if action == "attack":
            return 15
        if action == "special":
            return 35
        return 0

    def _winner_after_round(self, battle):
        a_id, b_id = battle["participants"]
        a_hp = battle["states"][a_id]["hp"]
        b_hp = battle["states"][b_id]["hp"]
        if a_hp <= 0 and b_hp <= 0:
            if a_hp == b_hp:
                return None
            return a_id if a_hp > b_hp else b_id
        if a_hp <= 0:
            return b_id
        if b_hp <= 0:
            return a_id
        return None

    def _is_draw_after_round(self, battle):
        return all(state["hp"] <= 0 for state in battle["states"].values()) and self._winner_after_round(battle) is None

    def _resolve_by_round_limit(self, battle):
        a_id, b_id = battle["participants"]
        a_hp = battle["states"][a_id]["hp"]
        b_hp = battle["states"][b_id]["hp"]
        if a_hp == b_hp:
            winner_id = None
        else:
            winner_id = a_id if a_hp > b_hp else b_id
        return self._resolve_battle(battle, winner_id, "round_limit")

    def _resolve_battle(self, battle, winner_id, reason):
        if battle["status"] == "resolved":
            return self._battle_summary(battle)

        battle["status"] = "resolved"
        battle["winner_id"] = winner_id
        pot = battle["stake"] * 2
        if winner_id is None:
            for agent_id in battle["participants"]:
                self._agents[agent_id]["balance"] += battle["stake"]
                self._agents[agent_id]["draws"] += 1
        else:
            self._agents[winner_id]["balance"] += pot
            self._agents[winner_id]["wins"] += 1
            loser_id = self._opponent_id(battle, winner_id)
            self._agents[loser_id]["losses"] += 1

        for agent_id in battle["participants"]:
            self._agents[agent_id]["active_battle_id"] = None

        battle["result"] = {
            "battle_id": battle["battle_id"],
            "status": "resolved",
            "reason": reason,
            "winner_id": winner_id,
            "stake": battle["stake"],
            "pot": pot,
            "rounds_played": len(battle["battle_log"]),
            "balances": {
                agent_id: self._agents[agent_id]["balance"]
                for agent_id in battle["participants"]
            },
            "final_states": copy.deepcopy(battle["states"]),
            "battle_log": copy.deepcopy(battle["battle_log"]),
        }
        return self._battle_summary(battle)

    def _new_player_state(self):
        return {
            "hp": INITIAL_HP,
            "energy": INITIAL_ENERGY,
            "cooldowns": {"special": 0},
        }

    def _validate_stake(self, agent, stake):
        if stake != FIXED_STAKE:
            raise ArenaError(400, "stake must be 100")
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

    def _battle_view(self, agent, battle):
        agent_id = agent["agent_id"]
        opponent_id = self._opponent_id(battle, agent_id)
        view = self._battle_summary(battle)
        view["self"] = copy.deepcopy(battle["states"][agent_id])
        view["opponent"] = copy.deepcopy(battle["states"][opponent_id]) if opponent_id else None
        view["needs_action"] = (
            battle["status"] == "active" and agent_id not in battle["pending_actions"]
        )
        view["battle_log"] = copy.deepcopy(battle["battle_log"])
        return view

    def _battle_summary(self, battle):
        return {
            "battle_id": battle["battle_id"],
            "status": battle["status"],
            "stake": battle["stake"],
            "round": battle["round"],
            "participants": list(battle["participants"]),
            "winner_id": battle["winner_id"],
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
