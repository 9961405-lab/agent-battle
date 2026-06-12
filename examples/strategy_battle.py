"""Strategy battle runner — thin wrapper over agent_battle.client."""

import argparse
import json

from agent_battle.client import (
    STRATEGIES,
    HttpTransport,
    play_strategy_battle,
)


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
