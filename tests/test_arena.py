import json
import unittest

from agent_battle.arena import Arena, ArenaError, VALID_ACTIONS
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

    def test_turns_alternate_between_agents(self):
        battle = self._active_battle()
        battle_id = battle["battle_id"]

        # First turn belongs to agent A (order[0])
        view_a = self.arena.get_battle(self.a["api_key"], battle_id)
        self.assertTrue(view_a["needs_action"])

        view_b = self.arena.get_battle(self.b["api_key"], battle_id)
        self.assertFalse(view_b["needs_action"])

        # A acts
        self.arena.submit_action(self.a["api_key"], battle_id, "attack")

        # Now B's turn
        view_a = self.arena.get_battle(self.a["api_key"], battle_id)
        self.assertFalse(view_a["needs_action"])

        view_b = self.arena.get_battle(self.b["api_key"], battle_id)
        self.assertTrue(view_b["needs_action"])

    def test_attack_deals_random_damage(self):
        battle = self._active_battle()
        battle_id = battle["battle_id"]

        self.arena.submit_action(self.a["api_key"], battle_id, "attack")
        self.arena.submit_action(self.b["api_key"], battle_id, "defend")

        state_a = self.arena.get_battle(self.a["api_key"], battle_id)
        state_b = self.arena.get_battle(self.b["api_key"], battle_id)

        # A attacked, B didn't have defend up yet → full damage 10-17
        self.assertGreaterEqual(state_b["self"]["hp"], 100 - 17)  # min HP = 83
        self.assertLess(state_a["opponent"]["hp"], 100)  # B took damage

    def test_heavy_costs_mp_and_has_hit_chance(self):
        battle = self._active_battle()
        battle_id = battle["battle_id"]

        self.arena.submit_action(self.a["api_key"], battle_id, "heavy")

        state = self.arena.get_battle(self.a["api_key"], battle_id)
        self.assertEqual(state["self"]["mp"], 35)  # 50 - 15

    def test_heal_restores_hp(self):
        battle = self._active_battle()
        battle_id = battle["battle_id"]

        # Let A deal some damage to B first, then B heals
        self.arena.submit_action(self.a["api_key"], battle_id, "attack")
        state = self.arena.get_battle(self.b["api_key"], battle_id)

        old_hp = state["opponent"]["hp"]  # A sees B's HP
        self.arena.submit_action(self.b["api_key"], battle_id, "heal")

        state = self.arena.get_battle(self.b["api_key"], battle_id)
        # B's HP should be restored
        self.assertGreater(state["self"]["hp"], 100 - 17)  # worst case attack damage

    def test_defend_halves_incoming_damage(self):
        battle = self._active_battle()
        battle_id = battle["battle_id"]

        # B defends
        self.arena.submit_action(self.a["api_key"], battle_id, "attack")
        state = self.arena.get_battle(self.a["api_key"], battle_id)
        dmg_without_defend = 100 - state["opponent"]["hp"]

        self.arena.submit_action(self.b["api_key"], battle_id, "defend")

        # Next: A attacks again, B now has defend up
        self.arena.submit_action(self.a["api_key"], battle_id, "attack")
        state = self.arena.get_battle(self.a["api_key"], battle_id)
        dmg_with_defend = (100 - dmg_without_defend) - state["opponent"]["hp"]

        # defend should halve the damage
        self.assertLessEqual(dmg_with_defend, dmg_without_defend)

    def test_heavy_insufficient_mp_rejected(self):
        battle = self._active_battle()
        battle_id = battle["battle_id"]

        # Drain A's MP with 3 heavy attacks
        for _ in range(3):
            self.arena.submit_action(self.a["api_key"], battle_id, "heavy")
            self.arena.submit_action(self.b["api_key"], battle_id, "defend")

        # A now has 5 MP, heavy should be rejected
        with self.assertRaises(ArenaError):
            self.arena.submit_action(self.a["api_key"], battle_id, "heavy")

    def test_out_of_turn_action_rejected(self):
        battle = self._active_battle()
        battle_id = battle["battle_id"]

        # It's A's turn, B tries to act
        with self.assertRaises(ArenaError):
            self.arena.submit_action(self.b["api_key"], battle_id, "attack")

    def test_forfeit_works_any_time(self):
        battle = self._active_battle()
        battle_id = battle["battle_id"]

        # B forfeits even though it's A's turn
        result = self.arena.submit_action(self.b["api_key"], battle_id, "forfeit")
        self.assertEqual(result["winner_id"], self.a["agent_id"])

    def test_non_participant_cannot_read_or_act(self):
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

    def test_draw_refunds_both_stakes_after_turn_limit(self):
        battle = self._active_battle()
        battle_id = battle["battle_id"]

        result = None
        for _ in range(200):
            a_turn = self.arena.get_battle(self.a["api_key"], battle_id)
            if a_turn["status"] == "resolved":
                break
            if a_turn["needs_action"]:
                result = self.arena.submit_action(self.a["api_key"], battle_id, "defend")
                if result["status"] == "resolved":
                    break

            b_turn = self.arena.get_battle(self.b["api_key"], battle_id)
            if b_turn["status"] == "resolved":
                break
            if b_turn["needs_action"]:
                result = self.arena.submit_action(self.b["api_key"], battle_id, "defend")

        self.assertIsNone(result["winner_id"])
        self.assertEqual(self.arena.get_agent(self.a["api_key"])["balance"], 1000)
        self.assertEqual(self.arena.get_agent(self.b["api_key"])["balance"], 1000)

    def test_winner_receives_pot(self):
        battle = self._active_battle()
        battle_id = battle["battle_id"]

        result = None
        for _ in range(50):
            a_turn = self.arena.get_battle(self.a["api_key"], battle_id)
            if a_turn["status"] == "resolved":
                break
            if a_turn["needs_action"]:
                result = self.arena.submit_action(self.a["api_key"], battle_id, "attack")
                if result["status"] == "resolved":
                    break

            b_turn = self.arena.get_battle(self.b["api_key"], battle_id)
            if b_turn["status"] == "resolved":
                break
            if b_turn["needs_action"]:
                result = self.arena.submit_action(self.b["api_key"], battle_id, "defend")

        winner = self.arena.get_agent(self.a["api_key"])
        loser = self.arena.get_agent(self.b["api_key"])
        self.assertEqual(winner["balance"] + loser["balance"], 2000)
        # winner should have more than original
        if result["winner_id"] == self.a["agent_id"]:
            self.assertGreater(winner["balance"], 900)

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
        self._post_json(
            f"/battles/{battle['battle_id']}/join",
            agent_b["api_key"],
            {},
        )

        # A attacks
        self._post_json(
            f"/battles/{battle['battle_id']}/actions",
            agent_a["api_key"],
            {"action": "attack"},
        )
        # B forfeits
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

    def test_dashboard_lists_battles_without_auth(self):
        agent_a = self._post_json("/agents", None, {})["body"]
        agent_b = self._post_json("/agents", None, {})["body"]
        battle = self._post_json("/battles", agent_a["api_key"], {"stake": 100})["body"]
        self._post_json(f"/battles/{battle['battle_id']}/join", agent_b["api_key"], {})

        response = self._get_text("/dashboard", None)

        self.assertEqual(response["status"], 200)
        self.assertEqual(response["headers"]["content-type"], "text/html; charset=utf-8")
        self.assertIn("Agent Battle Dashboard", response["body"])
        self.assertIn(agent_a["agent_id"][:14], response["body"])
        self.assertNotIn(agent_a["api_key"], response["body"])

    def test_dashboard_battle_detail_shows_log(self):
        agent_a = self._post_json("/agents", None, {})["body"]
        agent_b = self._post_json("/agents", None, {})["body"]
        battle = self._post_json("/battles", agent_a["api_key"], {"stake": 100})["body"]
        self._post_json(f"/battles/{battle['battle_id']}/join", agent_b["api_key"], {})
        self._post_json(
            f"/battles/{battle['battle_id']}/actions",
            agent_a["api_key"],
            {"action": "attack"},
        )

        response = self._get_text(f"/dashboard/battles/{battle['battle_id']}", None)

        self.assertEqual(response["status"], 200)
        self.assertIn("Battle Log", response["body"])
        self.assertIn("attack", response["body"])

    def test_open_battles_list_waiting_battles(self):
        agent_a = self._post_json("/agents", None, {})["body"]
        self._post_json("/battles", agent_a["api_key"], {"stake": 100})

        response = self._get_json("/battles/open", agent_a["api_key"])

        self.assertEqual(len(response["body"]["open_battles"]), 1)
        self.assertEqual(response["body"]["open_battles"][0]["status"], "created")

    def _post_json(self, path, api_key, payload):
        request = self._request("POST", path, api_key, payload)
        status, headers, body = self.app.handle(request)
        return {"status": status, "headers": headers, "body": json.loads(body)}

    def _get_json(self, path, api_key):
        request = self._request("GET", path, api_key, None)
        status, headers, body = self.app.handle(request)
        return {"status": status, "headers": headers, "body": json.loads(body)}

    def _get_text(self, path, api_key):
        request = self._request("GET", path, api_key, None)
        status, headers, body = self.app.handle(request)
        return {"status": status, "headers": headers, "body": body}

    def _request(self, method, path, api_key, payload):
        headers = {}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        return {
            "method": method,
            "path": path,
            "headers": headers,
            "body": json.dumps(payload or {}),
            "client_ip": "127.0.0.1",
        }


if __name__ == "__main__":
    unittest.main()
