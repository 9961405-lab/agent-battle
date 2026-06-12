import argparse, json
from agent_battle.client import STRATEGIES, HttpTransport, play_strategy_battle


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://127.0.0.1:8080")
    p.add_argument("--agent-a", default="balanced", choices=sorted(STRATEGIES))
    p.add_argument("--agent-b", default="aggressive", choices=sorted(STRATEGIES))
    args = p.parse_args()
    print(json.dumps(play_strategy_battle(HttpTransport(args.base_url), args.agent_a, args.agent_b), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
