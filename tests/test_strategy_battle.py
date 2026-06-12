import unittest

from agent_battle.arena import Arena, VALID_ACTIONS
from agent_battle.server import create_app
from examples.demo_battle import AppTransport
from examples.strategy_battle import (
    aggressive,
    balanced,
    defensive,
    play_strategy_battle,
)


class StrategyTest(unittest.TestCase):
    def test_aggressive_uses_special_when_ready(self):
        state = self._state(hp=100, energy=50, special_cooldown=0)

        self.assertEqual(aggressive(state), "special")

    def test_aggressive_charges_when_energy_is_too_low_to_attack(self):
        state = self._state(hp=100, energy=5, special_cooldown=0)

        self.assertEqual(aggressive(state), "charge")

    def test_defensive_defends_when_low_hp(self):
        state = self._state(hp=25, energy=50, opponent_energy=20)

        self.assertEqual(defensive(state), "defend")

    def test_defensive_defends_when_opponent_has_high_energy(self):
        state = self._state(hp=80, energy=50, opponent_energy=80)

        self.assertEqual(defensive(state), "defend")

    def test_balanced_always_returns_legal_action_for_common_states(self):
        states = [
            self._state(hp=100, energy=50, special_cooldown=0, opponent_energy=30),
            self._state(hp=30, energy=20, special_cooldown=0, opponent_energy=90),
            self._state(hp=80, energy=5, special_cooldown=2, opponent_energy=10),
            self._state(hp=60, energy=30, special_cooldown=1, opponent_energy=40),
        ]

        for state in states:
            self.assertIn(balanced(state), VALID_ACTIONS)

    def _state(self, hp, energy, special_cooldown=0, opponent_energy=50):
        return {
            "self": {
                "hp": hp,
                "energy": energy,
                "cooldowns": {"special": special_cooldown},
            },
            "opponent": {
                "hp": 100,
                "energy": opponent_energy,
                "cooldowns": {"special": 0},
            },
        }


class StrategyRunnerTest(unittest.TestCase):
    def test_runner_completes_battle_and_returns_summary_with_result(self):
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
        self.assertIn("rounds_played", summary)
        self.assertIn("balances", summary)
        self.assertGreater(len(summary["result"]["battle_log"]), 0)


if __name__ == "__main__":
    unittest.main()
