#!/usr/bin/env python3
"""Continue an existing agent-battle bid-fight."""

import json
import urllib.request
import sys
import time

API_KEY = "ab_54lDKwLz1041UHFMAjWIQKFgAAuONYTe"
BATTLE_ID = "battle_d8ea6b79b197479ba7943962c5de4d24"
BASE_URL = "http://101.43.87.232:8080"

# Bypass local proxy
import os
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


def req(method, path, body=None):
    url = BASE_URL + path
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}")
        return None


def decide_bid(state):
    """Simple aggressive strategy."""
    self = state["self"]
    opp = state["opponent"]
    hp = self["hp"]
    mp = self["mp"]
    turn = state["turn"]

    # berserker: 1.5x damage when HP <= 50
    berserker_active = hp <= 50

    # opponent MP heuristic
    opp_mp = {"high": 40, "mid": 25, "low": 10}.get(opp["mp"], 15)

    if mp <= 5:
        return 0  # conserve

    # aggressive: bid ~70% of estimated opponent bid
    bid = min(mp - 1, max(5, int(opp_mp * 0.7 + 5)))
    bid = min(bid, mp)

    # if HP low, bid higher to finish
    if hp <= 30:
        bid = min(mp, bid + 15)

    return max(1, bid)


def main():
    print(f"Monitoring battle {BATTLE_ID}...")
    last_turn = -1

    while True:
        state = req("GET", f"/battles/{BATTLE_ID}")
        if not state:
            time.sleep(3)
            continue

        status = state["status"]
        if status == "resolved":
            result = req("GET", f"/battles/{BATTLE_ID}/result")
            print("\n=== BATTLE OVER ===")
            print(json.dumps(result, indent=2, ensure_ascii=False))
            break

        if status != "active":
            print(f"Status: {status}, waiting...")
            time.sleep(3)
            continue

        turn = state["turn"]
        needs = state["needs_action"]

        if needs and turn != last_turn:
            last_turn = turn
            bid = decide_bid(state)
            print(f"\n[Turn {turn}] HP={state['self']['hp']} MP={state['self']['mp']} | "
                  f"Opp HP={state['opponent']['hp']} MP={state['opponent']['mp']}")
            print(f"  -> Bidding {bid} MP")
            resp = req("POST", f"/battles/{BATTLE_ID}/bid", {"bid": bid})
            if resp:
                # Show latest log entry
                log = resp.get("battle_log", [])
                if log:
                    last = log[-1]
                    print(f"  Result: {last['notes']}")
        else:
            if turn != last_turn:
                print(f"[Turn {turn} waiting for opponent...]")
                last_turn = turn
            time.sleep(2)


if __name__ == "__main__":
    main()
