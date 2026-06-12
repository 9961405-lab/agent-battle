#!/usr/bin/env python3
"""Single-agent matchmaking client for Agent Battle (blind-bid mode)."""
import argparse, json, os, sys

_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from agent_battle.client import STRATEGIES, HttpTransport, play_single_agent

DEFAULT_ARENA_URL = "http://101.43.87.232:8080"

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default=os.environ.get("AGENT_BATTLE_URL", DEFAULT_ARENA_URL))
    p.add_argument("--strategy", default="balanced", choices=sorted(STRATEGIES))
    p.add_argument("--api-key", default=os.environ.get("AGENT_BATTLE_API_KEY", ""))
    args = p.parse_args()

    t = HttpTransport(args.base_url)
    if args.api_key:
        key = args.api_key
        agent = t.request("GET", "/agents/me", api_key=key)
        print(f"Reusing agent {agent['agent_id']}  balance={agent['balance']}", file=sys.stderr)
    else:
        agent = t.request("POST", "/agents", payload={})
        key = agent["api_key"]
        print(f"Registered {agent['agent_id']}", file=sys.stderr)
        print(f"export AGENT_BATTLE_API_KEY={key}", file=sys.stderr)

    result = play_single_agent(t, key, strategy_name=args.strategy)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
