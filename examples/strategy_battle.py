import argparse
import json

from examples.demo_battle import BattleClient, HttpTransport


def aggressive(state):
    own = state["self"]
    if own["energy"] >= 30 and own["cooldowns"]["special"] == 0:
        return "special"
    if own["energy"] >= 10:
        return "attack"
    return "charge"


def defensive(state):
    own = state["self"]
    opponent = state["opponent"]
    if own["hp"] <= 30 or opponent["energy"] >= 70:
        return "defend"
    if own["energy"] >= 30 and own["cooldowns"]["special"] == 0:
        return "special"
    if own["energy"] >= 10:
        return "attack"
    return "charge"


def balanced(state):
    own = state["self"]
    opponent = state["opponent"]
    if own["hp"] <= 35 and opponent["energy"] >= 30:
        return "defend"
    if opponent["hp"] <= 35 and own["energy"] >= 30 and own["cooldowns"]["special"] == 0:
        return "special"
    if own["energy"] < 10:
        return "charge"
    if own["energy"] >= 30 and own["cooldowns"]["special"] == 0 and opponent["energy"] < 70:
        return "special"
    if opponent["energy"] >= 80:
        return "defend"
    return "attack"


STRATEGIES = {
    "aggressive": aggressive,
    "defensive": defensive,
    "balanced": balanced,
}


def play_strategy_battle(transport, agent_a_strategy="balanced", agent_b_strategy="aggressive"):
    choose_a = _strategy_for_name(agent_a_strategy)
    choose_b = _strategy_for_name(agent_b_strategy)
    client = BattleClient(transport)

    agent_a = client.create_agent()
    agent_b = client.create_agent()
    battle = client.create_battle(agent_a["api_key"])
    battle_id = battle["battle_id"]
    client.join_battle(agent_b["api_key"], battle_id)

    state = {"status": "active"}
    while state["status"] != "resolved":
        state_a = client.get_battle(agent_a["api_key"], battle_id)
        if state_a["status"] == "resolved":
            break
        if state_a["needs_action"]:
            state = client.submit_action(agent_a["api_key"], battle_id, choose_a(state_a))
            if state["status"] == "resolved":
                break

        state_b = client.get_battle(agent_b["api_key"], battle_id)
        if state_b["status"] == "resolved":
            break
        if state_b["needs_action"]:
            state = client.submit_action(agent_b["api_key"], battle_id, choose_b(state_b))

    result = client.result(agent_a["api_key"], battle_id)
    return {
        "agent_a_id": agent_a["agent_id"],
        "agent_b_id": agent_b["agent_id"],
        "agent_a_strategy": agent_a_strategy,
        "agent_b_strategy": agent_b_strategy,
        "winner_id": result["winner_id"],
        "rounds_played": result["rounds_played"],
        "balances": result["balances"],
        "result": result,
    }


def _strategy_for_name(name):
    if name not in STRATEGIES:
        valid = ", ".join(sorted(STRATEGIES))
        raise ValueError(f"unknown strategy '{name}', expected one of: {valid}")
    return STRATEGIES[name]


def main():
    parser = argparse.ArgumentParser(description="Run a strategy-vs-strategy Agent Battle.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--agent-a", default="balanced", choices=sorted(STRATEGIES))
    parser.add_argument("--agent-b", default="aggressive", choices=sorted(STRATEGIES))
    args = parser.parse_args()

    summary = play_strategy_battle(
        HttpTransport(args.base_url),
        agent_a_strategy=args.agent_a,
        agent_b_strategy=args.agent_b,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
