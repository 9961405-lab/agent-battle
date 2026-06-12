#!/usr/bin/env python3
"""Single-agent matchmaking client for Agent Battle.

Registers one agent (or reuses an existing key), joins the first open
battle, or creates a new one and waits for an opponent.  Never creates
two agents — it always looks for a real opponent first.
"""

import argparse
import json
import os
import sys

# Make the repo-root agent_battle package importable regardless of CWD.
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from agent_battle.client import STRATEGIES, HttpTransport, play_single_agent

DEFAULT_ARENA_URL = "http://101.43.87.232:8080"


def main():
    parser = argparse.ArgumentParser(
        description="Single-agent matchmaking client for Agent Battle."
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("AGENT_BATTLE_URL", DEFAULT_ARENA_URL),
    )
    parser.add_argument(
        "--strategy",
        default="balanced",
        choices=sorted(STRATEGIES),
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("AGENT_BATTLE_API_KEY", ""),
        help="Reuse an existing agent key. If not set, a new agent is registered.",
    )
    args = parser.parse_args()

    transport = HttpTransport(args.base_url)

    if args.api_key:
        api_key = args.api_key
        agent = transport.request("GET", "/agents/me", api_key=api_key)
        print(f"Reusing agent {agent['agent_id']}  balance={agent['balance']}", file=sys.stderr)
    else:
        agent = transport.request("POST", "/agents", payload={})
        api_key = agent["api_key"]
        print(f"Registered new agent {agent['agent_id']}", file=sys.stderr)
        print(f"Export this key for reuse: export AGENT_BATTLE_API_KEY={api_key}", file=sys.stderr)

    result = play_single_agent(transport, api_key, strategy_name=args.strategy)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
