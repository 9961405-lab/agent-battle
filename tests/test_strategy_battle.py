import unittest

from agent_battle.arena import Arena, VALID_ACTIONS
from agent_battle.client import (
    AppTransport,
    aggressive,
    balanced,
    defensive,
    play_strategy_battle,
)
from agent_battle.server import create_app


class StrategyTest(unittest.TestCase):
    def test_aggressive_uses_heavy_when_mp_available(self):
        state = self._state(hp=100, mp=30)
        self.assertEqual(aggressive(state), "heavy")

    def test_aggressive_attacks_when_mp_low(self):
        state = self._state(hp=100, mp=5)
        self.assertEqual(aggressive(state), "attack")

    def test_defensive_heals_when_hurt(self):
        state = self._state(hp=25, mp=20)
        self.assertEqual(defensive(state), "heal")

    def test_defensive_defends_when_pressured(self):
        state = self._state(hp=25, mp=5)  # can't heal, should defend
        self.assertEqual(defensive(state), "defend")

    def test_balanced_always_returns_legal_action(self):
        states = [
            self._state(hp=100, mp=50),
            self._state(hp=30, mp=15),
            self._state(hp=80, mp=5),
            self._state(hp=60, mp=30),
        ]
        for state in states:
            self.assertIn(balanced(state), VALID_ACTIONS)

    def test_all_strategies_return_valid_actions(self):
        for strat in [aggressive, defensive, balanced]:
            for hp in [100, 50, 20]:
                for mp in [50, 20, 5]:
                    state = self._state(hp=hp, mp=mp)
                    action = strat(state)
                    self.assertIn(action, VALID_ACTIONS,
                                  f"{strat.__name__} at hp={hp} mp={mp} returned {action}")

    def _state(self, hp, mp):
        return {
            "self": {"hp": hp, "mp": mp, "defending": False},
            "opponent": {"hp": 100, "mp": 50, "defending": False},
        }


class StrategyRunnerTest(unittest.TestCase):
    def test_runner_completes_battle_and_returns_summary(self):
        app = create_app(Arena())

        summary = play_strategy_battle(
            AppTransport(app),
            agent_a_strategy="balanced",
            agent_b_strategy="aggressive",
        )

        self.assertEqual(summary["agent_a_strategy"], "balanced")
        self.assertEqual(summary["agent_b_strategy"], "aggressive")
        self.assertEqual(summary["result"]["status"], "resolved")
        self.assertIn("winner_id", summary)
        self.assertIn("turns_played", summary)
        self.assertIn("balances", summary)
        self.assertGreater(len(summary["result"]["battle_log"]), 0)


if __name__ == "__main__":
    unittest.main()
