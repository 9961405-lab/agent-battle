"""Demo battle client — thin wrapper over agent_battle.client."""

import argparse
import json

from agent_battle.client import BattleClient, HttpTransport


def play_demo_battle(transport):
    client = BattleClient(transport)
    agent_a = client.create_agent()
    agent_b = client.create_agent()
    battle = client.create_battle(agent_a["api_key"])
    battle_id = battle["battle_id"]
    client.join_battle(agent_b["api_key"], battle_id)

    # A attacks, B forfeits — minimal demo
    while True:
        view_a = client.get_battle(agent_a["api_key"], battle_id)
        if view_a["status"] == "resolved":
            break
        if view_a["needs_action"]:
            client.submit_action(agent_a["api_key"], battle_id, "attack")

        view_b = client.get_battle(agent_b["api_key"], battle_id)
        if view_b["status"] == "resolved":
            break
        if view_b["needs_action"]:
            client.submit_action(agent_b["api_key"], battle_id, "forfeit")

    return client.result(agent_a["api_key"], battle_id)


def main():
    parser = argparse.ArgumentParser(description="Run a two-agent Agent Battle demo.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()

    result = play_demo_battle(HttpTransport(args.base_url))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
