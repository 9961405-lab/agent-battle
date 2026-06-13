"""Smoke tests for blind-bid arena."""

import json
import unittest

from agent_battle.arena import Arena, ArenaError
from agent_battle.server import create_app


class ArenaBidTest(unittest.TestCase):
    def setUp(self):
        self.arena = Arena()
        self.a = self.arena.create_agent()
        self.b = self.arena.create_agent()

    def test_agents_start_with_initial_balance(self):
        self.assertEqual(self.a["balance"], 1000)
        self.assertEqual(self.b["balance"], 1000)

    def test_bid_round_resolves_when_both_submit(self):
        battle = self._active()
        bid = battle["battle_id"]

        self.arena.submit_bid(self.a["api_key"], bid, 10)
        view = self.arena.submit_bid(self.b["api_key"], bid, 5)

        # A bid higher, should have dealt 5 damage
        self.assertEqual(view["battle_log"][-1]["bids"][self.a["agent_id"]], 10)
        self.assertEqual(view["battle_log"][-1]["bids"][self.b["agent_id"]], 5)
        self.assertEqual(view["battle_log"][-1]["damage"], 5)

    def test_tie_refunds_mp(self):
        battle = self._active()
        bid = battle["battle_id"]

        self.arena.submit_bid(self.a["api_key"], bid, 8)
        self.arena.submit_bid(self.b["api_key"], bid, 8)

        view = self.arena.get_battle(self.a["api_key"], bid)
        # Both spent 8, refunded 10 → net +2
        self.assertEqual(view["self"]["mp"], 50 - 8 + 10)

    def test_bid_must_be_within_mp(self):
        battle = self._active()
        bid = battle["battle_id"]

        with self.assertRaises(ArenaError):
            self.arena.submit_bid(self.a["api_key"], bid, 999)

    def test_skills_registered(self):
        agent = self.arena.create_agent(name="skilled", skills=["vampire", "berserker", "focused"])
        self.assertEqual(agent["skills"], ["vampire", "berserker", "focused"])

    def test_skill_vampire_heals_on_win(self):
        arena = Arena()
        a = arena.create_agent(skills=["vampire"])
        b = arena.create_agent()
        battle = arena.create_battle(a["api_key"], 100)
        arena.join_battle(b["api_key"], battle["battle_id"])
        bid = battle["battle_id"]

        # First wound A so vampire can actually heal
        arena.submit_bid(b["api_key"], bid, 15)
        arena.submit_bid(a["api_key"], bid, 0)
        view_a = arena.get_battle(a["api_key"], bid)
        hp_before = view_a["self"]["hp"]

        # Now A wins a bid with vampire
        arena.submit_bid(a["api_key"], bid, 10)
        arena.submit_bid(b["api_key"], bid, 0)
        view_a = arena.get_battle(a["api_key"], bid)
        self.assertGreater(view_a["self"]["hp"], hp_before)

    def test_skill_guard_blocks_first_loss_damage(self):
        arena = Arena()
        a = arena.create_agent()
        b = arena.create_agent(skills=["guard"])
        battle = arena.create_battle(a["api_key"], 100)
        arena.join_battle(b["api_key"], battle["battle_id"])
        bid = battle["battle_id"]

        # A wins first round
        arena.submit_bid(a["api_key"], bid, 15)
        arena.submit_bid(b["api_key"], bid, 5)

        view = arena.get_battle(b["api_key"], bid)
        # B's guard should have blocked damage
        self.assertEqual(view["self"]["hp"], 100)

    def test_skill_poison_applies_dot(self):
        arena = Arena()
        a = arena.create_agent(skills=["poison"])
        b = arena.create_agent()
        battle = arena.create_battle(a["api_key"], 100)
        arena.join_battle(b["api_key"], battle["battle_id"])
        bid = battle["battle_id"]

        # A wins with poison
        arena.submit_bid(a["api_key"], bid, 10)
        arena.submit_bid(b["api_key"], bid, 0)

        # Next round: A submits low, B submits low → poison ticks
        arena.submit_bid(a["api_key"], bid, 0)
        view = arena.submit_bid(b["api_key"], bid, 0)

        # B should have taken poison damage (4 dmg)
        self.assertLess(view["self"]["hp"] if view["self"] else 100, 100)

    def test_fog_of_war_hides_exact_opponent_stats(self):
        battle = self._active()
        bid = battle["battle_id"]

        view = self.arena.get_battle(self.a["api_key"], bid)
        self.assertIsInstance(view["self"]["hp"], int)
        self.assertIsInstance(view["self"]["mp"], int)
        # Opponent should be low/mid/high
        self.assertIn(view["opponent"]["hp"], ("low", "mid", "high"))
        self.assertIn(view["opponent"]["mp"], ("low", "mid", "high"))

    def test_resolved_battle_rejects_bids(self):
        arena2 = Arena()
        a = arena2.create_agent()
        b = arena2.create_agent()
        bt = arena2.create_battle(a["api_key"], 100)
        arena2.join_battle(b["api_key"], bt["battle_id"])
        bid2 = bt["battle_id"]

        for _ in range(30):
            v = arena2.get_battle(a["api_key"], bid2)
            if v["status"] == "resolved":
                break
            if v["needs_action"]:
                arena2.submit_bid(a["api_key"], bid2, min(15, v["self"]["mp"]))

            v = arena2.get_battle(b["api_key"], bid2)
            if v["status"] == "resolved":
                break
            if v["needs_action"]:
                arena2.submit_bid(b["api_key"], bid2, 0)

        with self.assertRaises(ArenaError):
            arena2.submit_bid(a["api_key"], bid2, 5)

    def _active(self):
        battle = self.arena.create_battle(self.a["api_key"], 100)
        return self.arena.join_battle(self.b["api_key"], battle["battle_id"])


class HttpApiTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app(Arena(), rate_limit=10000)

    def test_root_route(self):
        r = self._get_json("/", None)
        self.assertEqual(r["body"]["service"], "agent-battle-arena")

    def test_create_agent_with_skills(self):
        r = self._post_json("/agents", None, {"name": "test", "skills": ["vampire", "berserker"]})
        self.assertEqual(r["body"]["skills"], ["vampire", "berserker"])

    def test_bid_flow(self):
        a = self._post_json("/agents", None, {})["body"]
        b = self._post_json("/agents", None, {})["body"]
        battle = self._post_json("/battles", a["api_key"], {"stake": 100})["body"]
        self._post_json(f"/battles/{battle['battle_id']}/join", b["api_key"], {})

        r = self._post_json(f"/battles/{battle['battle_id']}/bid", a["api_key"], {"bid": 10})
        self.assertEqual(r["status"], 200)
        r = self._post_json(f"/battles/{battle['battle_id']}/bid", b["api_key"], {"bid": 3})
        self.assertEqual(r["status"], 200)

    def test_dashboard_shows_battles(self):
        a = self._post_json("/agents", None, {})["body"]
        b = self._post_json("/agents", None, {})["body"]
        battle = self._post_json("/battles", a["api_key"], {"stake": 100})["body"]
        self._post_json(f"/battles/{battle['battle_id']}/join", b["api_key"], {})
        r = self._get_text("/dashboard", None)
        self.assertIn("AGENT BATTLE", r["body"])

    def _post_json(self, path, api_key, payload):
        r = self._request("POST", path, api_key, payload)
        s, h, b = self.app.handle(r)
        return {"status": s, "headers": h, "body": json.loads(b)}

    def _get_json(self, path, api_key):
        r = self._request("GET", path, api_key, None)
        s, h, b = self.app.handle(r)
        return {"status": s, "headers": h, "body": json.loads(b)}

    def _get_text(self, path, api_key):
        r = self._request("GET", path, api_key, None)
        s, h, b = self.app.handle(r)
        return {"status": s, "headers": h, "body": b}

    def _request(self, method, path, api_key, payload):
        headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
        return {"method": method, "path": path, "headers": headers, "body": json.dumps(payload or {}), "client_ip": "127.0.0.1"}


if __name__ == "__main__":
    unittest.main()
