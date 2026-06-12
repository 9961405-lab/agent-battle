import unittest
from agent_battle.arena import Arena
from agent_battle.client import AppTransport, aggressive_bid, defensive_bid, balanced_bid, play_strategy_battle
from agent_battle.server import create_app


class BidStrategyTest(unittest.TestCase):
    def test_aggressive_bids_high(self):
        self.assertGreaterEqual(aggressive_bid(self._s(mp=30)), 15)

    def test_defensive_bids_low(self):
        self.assertLessEqual(defensive_bid(self._s(mp=30)), 10)

    def test_all_strategies_return_valid_bids(self):
        for strat in [aggressive_bid, defensive_bid, balanced_bid]:
            for mp in [5, 20, 50]:
                bid = strat(self._s(mp=mp))
                self.assertGreaterEqual(bid, 0)
                self.assertLessEqual(bid, mp)

    def _s(self, mp, hp=100):
        return {"self": {"hp": hp, "mp": mp}, "opponent": {"hp": "high", "mp": "high", "skills": []}}


class StrategyRunnerTest(unittest.TestCase):
    def test_runner_completes(self):
        app = create_app(Arena(), rate_limit=10000)
        s = play_strategy_battle(AppTransport(app), "balanced", "aggressive")
        self.assertEqual(s["result"]["status"], "resolved")
        self.assertIn("turns_played", s)


if __name__ == "__main__":
    unittest.main()
