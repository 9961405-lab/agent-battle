import unittest

from agent_battle.arena import Arena
from agent_battle.client import AppTransport
from agent_battle.server import create_app
from examples.demo_battle import play_demo_battle


class DemoClientTest(unittest.TestCase):
    def test_demo_client_completes_a_battle_over_http_api(self):
        app = create_app(Arena())
        result = play_demo_battle(AppTransport(app))

        self.assertEqual(result["status"], "resolved")
        self.assertIsNotNone(result["winner_id"])
        self.assertEqual(result["pot"], 200)
        self.assertGreater(len(result["battle_log"]), 0)


if __name__ == "__main__":
    unittest.main()
