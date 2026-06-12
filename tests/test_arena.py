import json
import unittest

from agent_battle.arena import Arena, ArenaError
from agent_battle.server import create_app


class ArenaRulesTest(unittest.TestCase):
    def setUp(self):
        self.arena = Arena()
        self.a = self.arena.create_agent()
        self.b = self.arena.create_agent()

    def test_agents_start_with_initial_balance(self):
        self.assertEqual(self.a["balance"], 1000)
        self.assertEqual(self.b["balance"], 1000)
        self.assertNotEqual(self.a["agent_id"], self.b["agent_id"])
        self.assertNotEqual(self.a["api_key"], self.b["api_key"])

    def test_joining_battle_locks_stake_and_starts_battle(self):
        battle = self.arena.create_battle(self.a["api_key"], 100)
        self.assertEqual(self.arena.get_agent(self.a["api_key"])["balance"], 900)

        joined = self.arena.join_battle(self.b["api_key"], battle["battle_id"])

        self.assertEqual(joined["status"], "active")
        self.assertEqual(self.arena.get_agent(self.b["api_key"])["balance"], 900)

    def test_round_advances_after_both_agents_submit_actions(self):
        battle = self._active_battle()

        self.arena.submit_action(self.a["api_key"], battle["battle_id"], "attack")
        current = self.arena.submit_action(self.b["api_key"], battle["battle_id"], "charge")

        self.assertEqual(current["round"], 2)
        state = self.arena.get_battle(self.a["api_key"], battle["battle_id"])
        self.assertEqual(state["self"]["energy"], 40)
        self.assertEqual(state["opponent"]["hp"], 85)
        self.assertEqual(len(state["battle_log"]), 1)

    def test_insufficient_energy_defaults_to_defend(self):
        battle = self._active_battle()
        battle_id = battle["battle_id"]

        for _ in range(5):
            self.arena.submit_action(self.a["api_key"], battle_id, "attack")
            self.arena.submit_action(self.b["api_key"], battle_id, "defend")

        self.arena.submit_action(self.a["api_key"], battle_id, "attack")
        self.arena.submit_action(self.b["api_key"], battle_id, "defend")

        log_entry = self.arena.get_battle(self.a["api_key"], battle_id)["battle_log"][-1]
        self.assertEqual(log_entry["actions"][self.a["agent_id"]]["requested"], "attack")
        self.assertEqual(log_entry["actions"][self.a["agent_id"]]["resolved"], "defend")

    def test_special_cooldown_defaults_to_attack(self):
        battle = self._active_battle()
        battle_id = battle["battle_id"]

        self.arena.submit_action(self.a["api_key"], battle_id, "special")
        self.arena.submit_action(self.b["api_key"], battle_id, "defend")
        self.arena.submit_action(self.a["api_key"], battle_id, "special")
        self.arena.submit_action(self.b["api_key"], battle_id, "defend")

        log_entry = self.arena.get_battle(self.a["api_key"], battle_id)["battle_log"][-1]
        self.assertEqual(log_entry["actions"][self.a["agent_id"]]["requested"], "special")
        self.assertEqual(log_entry["actions"][self.a["agent_id"]]["resolved"], "attack")

    def test_winner_receives_pot_when_opponent_hp_reaches_zero(self):
        battle = self._active_battle()
        battle_id = battle["battle_id"]

        result = None
        for action in ["special", "attack", "attack", "charge", "charge", "special"]:
            self.arena.submit_action(self.a["api_key"], battle_id, action)
            result = self.arena.submit_action(self.b["api_key"], battle_id, "charge")
            if result["status"] == "resolved":
                break

        self.assertEqual(result["winner_id"], self.a["agent_id"])
        self.assertEqual(self.arena.get_agent(self.a["api_key"])["balance"], 1100)
        self.assertEqual(self.arena.get_agent(self.b["api_key"])["balance"], 900)
        self.assertEqual(self.arena.get_agent(self.a["api_key"])["wins"], 1)
        self.assertEqual(self.arena.get_agent(self.b["api_key"])["losses"], 1)

    def test_draw_refunds_both_stakes_after_round_limit(self):
        battle = self._active_battle()
        battle_id = battle["battle_id"]

        result = None
        for _ in range(20):
            self.arena.submit_action(self.a["api_key"], battle_id, "defend")
            result = self.arena.submit_action(self.b["api_key"], battle_id, "defend")
            if result["status"] == "resolved":
                break

        self.assertIsNone(result["winner_id"])
        self.assertEqual(self.arena.get_agent(self.a["api_key"])["balance"], 1000)
        self.assertEqual(self.arena.get_agent(self.b["api_key"])["balance"], 1000)

    def test_non_participant_cannot_read_or_act_in_battle(self):
        battle = self._active_battle()
        outsider = self.arena.create_agent()

        with self.assertRaises(ArenaError):
            self.arena.get_battle(outsider["api_key"], battle["battle_id"])

        with self.assertRaises(ArenaError):
            self.arena.submit_action(outsider["api_key"], battle["battle_id"], "attack")

    def test_resolved_battle_rejects_more_actions(self):
        battle = self._active_battle()
        battle_id = battle["battle_id"]

        self.arena.submit_action(self.b["api_key"], battle_id, "forfeit")

        with self.assertRaises(ArenaError):
            self.arena.submit_action(self.a["api_key"], battle_id, "attack")

    def _active_battle(self):
        battle = self.arena.create_battle(self.a["api_key"], 100)
        return self.arena.join_battle(self.b["api_key"], battle["battle_id"])


class HttpApiTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app(Arena())

    def test_root_route_describes_service(self):
        response = self._get_json("/", None)

        self.assertEqual(response["status"], 200)
        self.assertEqual(response["body"]["service"], "agent-battle-arena")
        self.assertIn("POST /agents", response["body"]["endpoints"])

    def test_http_flow_creates_agents_battle_action_and_result(self):
        agent_a = self._post_json("/agents", None, {})["body"]
        agent_b = self._post_json("/agents", None, {})["body"]

        battle = self._post_json("/battles", agent_a["api_key"], {"stake": 100})["body"]
        joined = self._post_json(
            f"/battles/{battle['battle_id']}/join",
            agent_b["api_key"],
            {},
        )["body"]

        self.assertEqual(joined["status"], "active")

        self._post_json(
            f"/battles/{battle['battle_id']}/actions",
            agent_a["api_key"],
            {"action": "attack"},
        )
        state_response = self._post_json(
            f"/battles/{battle['battle_id']}/actions",
            agent_b["api_key"],
            {"action": "forfeit"},
        )

        self.assertEqual(state_response["status"], 200)
        result = self._get_json(f"/battles/{battle['battle_id']}/result", agent_a["api_key"])
        self.assertEqual(result["body"]["winner_id"], agent_a["agent_id"])

    def test_http_requires_auth_for_agent_state(self):
        response = self._get_json("/agents/me", None)
        self.assertEqual(response["status"], 401)

    def _post_json(self, path, api_key, payload):
        request = self._request("POST", path, api_key, payload)
        status, headers, body = self.app.handle(request)
        return {"status": status, "headers": headers, "body": json.loads(body)}

    def _get_json(self, path, api_key):
        request = self._request("GET", path, api_key, None)
        status, headers, body = self.app.handle(request)
        return {"status": status, "headers": headers, "body": json.loads(body)}

    def _request(self, method, path, api_key, payload):
        headers = {}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        return {
            "method": method,
            "path": path,
            "headers": headers,
            "body": json.dumps(payload or {}),
        }


if __name__ == "__main__":
    unittest.main()
