#!/usr/bin/env python3
import argparse
import json
import urllib.error
import urllib.request


def request(base_url, method, path, api_key=None, payload=None):
    headers = {"content-type": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        raise RuntimeError(f"HTTP {error.code}: {body}") from error


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


def play(base_url, agent_a_strategy, agent_b_strategy):
    choose_a = STRATEGIES[agent_a_strategy]
    choose_b = STRATEGIES[agent_b_strategy]

    agent_a = request(base_url, "POST", "/agents", payload={})
    agent_b = request(base_url, "POST", "/agents", payload={})
    battle = request(base_url, "POST", "/battles", agent_a["api_key"], {"stake": 100})
    battle_id = battle["battle_id"]
    request(base_url, "POST", f"/battles/{battle_id}/join", agent_b["api_key"], {})

    status = "active"
    while status != "resolved":
        state_a = request(base_url, "GET", f"/battles/{battle_id}", agent_a["api_key"])
        if state_a["status"] == "resolved":
            break
        if state_a["needs_action"]:
            response = request(
                base_url,
                "POST",
                f"/battles/{battle_id}/actions",
                agent_a["api_key"],
                {"action": choose_a(state_a)},
            )
            status = response["status"]
            if status == "resolved":
                break

        state_b = request(base_url, "GET", f"/battles/{battle_id}", agent_b["api_key"])
        if state_b["status"] == "resolved":
            break
        if state_b["needs_action"]:
            response = request(
                base_url,
                "POST",
                f"/battles/{battle_id}/actions",
                agent_b["api_key"],
                {"action": choose_b(state_b)},
            )
            status = response["status"]

    result = request(base_url, "GET", f"/battles/{battle_id}/result", agent_a["api_key"])
    return {
        "agent_a_id": agent_a["agent_id"],
        "agent_a_strategy": agent_a_strategy,
        "agent_b_id": agent_b["agent_id"],
        "agent_b_strategy": agent_b_strategy,
        "winner_id": result["winner_id"],
        "rounds_played": result["rounds_played"],
        "balances": result["balances"],
        "result": result,
    }


def main():
    parser = argparse.ArgumentParser(description="Connect to an Agent Battle arena.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--agent-a", default="balanced", choices=sorted(STRATEGIES))
    parser.add_argument("--agent-b", default="aggressive", choices=sorted(STRATEGIES))
    args = parser.parse_args()

    print(json.dumps(play(args.base_url, args.agent_a, args.agent_b), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
