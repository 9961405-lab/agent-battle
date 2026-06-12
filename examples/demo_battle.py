import argparse, json
from agent_battle.client import BattleClient, HttpTransport


def play_demo_battle(transport):
    client = BattleClient(transport)
    a = client.create_agent()
    b = client.create_agent()
    battle = client.create_battle(a["api_key"])
    bid = battle["battle_id"]
    client.join_battle(b["api_key"], bid)

    while True:
        va = client.get_battle(a["api_key"], bid)
        if va["status"] == "resolved": break
        if va["needs_action"]: client.submit_bid(a["api_key"], bid, min(10, va["self"]["mp"]))

        vb = client.get_battle(b["api_key"], bid)
        if vb["status"] == "resolved": break
        if vb["needs_action"]: client.submit_bid(b["api_key"], bid, min(5, vb["self"]["mp"]))

    return client.result(a["api_key"], bid)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = p.parse_args()
    print(json.dumps(play_demo_battle(HttpTransport(args.base_url)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
