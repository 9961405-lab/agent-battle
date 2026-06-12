"""Shared HTTP client, transports, and bid-based strategy functions."""

import json
import time
import urllib.error
import urllib.request


class HttpTransport:
    def __init__(self, base_url, timeout=10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method, path, api_key=None, payload=None):
        data = None
        headers = {"content-type": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8")
            raise RuntimeError(f"HTTP {error.code}: {body}") from error


class AppTransport:
    def __init__(self, app):
        self.app = app

    def request(self, method, path, api_key=None, payload=None):
        headers = {}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        status, _, body = self.app.handle({
            "method": method, "path": path, "headers": headers,
            "body": json.dumps(payload or {}),
        })
        parsed = json.loads(body)
        if status >= 400:
            raise RuntimeError(f"HTTP {status}: {parsed}")
        return parsed


class BattleClient:
    def __init__(self, transport):
        self.transport = transport

    def create_agent(self, name=None, skills=None, owner=None):
        payload = {}
        if name:
            payload["name"] = name
        if skills:
            payload["skills"] = skills
        if owner:
            payload["owner"] = owner
        return self.transport.request("POST", "/agents", payload=payload)

    def list_open_battles(self, api_key=None):
        return self.transport.request("GET", "/battles/open", api_key)["open_battles"]

    def create_battle(self, api_key, stake=100, room=None):
        payload = {"stake": stake}
        if room:
            payload["room"] = room
        return self.transport.request("POST", "/battles", api_key, payload)

    def find_room(self, room_code):
        return self.transport.request("GET", f"/battles/room/{room_code}")

    def join_battle(self, api_key, battle_id):
        return self.transport.request("POST", f"/battles/{battle_id}/join", api_key, {})

    def get_battle(self, api_key, battle_id):
        return self.transport.request("GET", f"/battles/{battle_id}", api_key)

    def submit_bid(self, api_key, battle_id, bid):
        return self.transport.request("POST", f"/battles/{battle_id}/bid", api_key, {"bid": bid})

    def result(self, api_key, battle_id):
        return self.transport.request("GET", f"/battles/{battle_id}/result", api_key)


# ---- Bid strategies ----

def aggressive_bid(state):
    """Bid high to dominate."""
    mp = state["self"]["mp"]
    return mp  # all-in

def defensive_bid(state):
    """Conservative bidding."""
    mp = state["self"]["mp"]
    hp = state["self"]["hp"]
    if hp <= 40:
        return mp // 2
    return min(mp, 5)

def balanced_bid(state):
    """Adaptive bidding."""
    mp = state["self"]["mp"]
    hp = state["self"]["hp"]
    if hp <= 30:
        return mp // 2
    return min(mp, max(1, mp // 3))

STRATEGIES = {
    "aggressive": aggressive_bid,
    "defensive": defensive_bid,
    "balanced": balanced_bid,
}


def _strategy_for_name(name):
    if name not in STRATEGIES:
        raise ValueError(f"unknown strategy '{name}', expected one of: {sorted(STRATEGIES)}")
    return STRATEGIES[name]


# ---- Self-play runner ----

def play_strategy_battle(transport, agent_a_strategy="balanced", agent_b_strategy="aggressive"):
    choose_a = _strategy_for_name(agent_a_strategy)
    choose_b = _strategy_for_name(agent_b_strategy)
    client = BattleClient(transport)
    agent_a = client.create_agent()
    agent_b = client.create_agent()
    battle = client.create_battle(agent_a["api_key"])
    battle_id = battle["battle_id"]
    client.join_battle(agent_b["api_key"], battle_id)

    while True:
        view_a = client.get_battle(agent_a["api_key"], battle_id)
        if view_a["status"] == "resolved":
            break
        if view_a["needs_action"]:
            client.submit_bid(agent_a["api_key"], battle_id, choose_a(view_a))

        view_b = client.get_battle(agent_b["api_key"], battle_id)
        if view_b["status"] == "resolved":
            break
        if view_b["needs_action"]:
            client.submit_bid(agent_b["api_key"], battle_id, choose_b(view_b))

    result = client.result(agent_a["api_key"], battle_id)
    return {
        "agent_a_id": agent_a["agent_id"], "agent_b_id": agent_b["agent_id"],
        "agent_a_strategy": agent_a_strategy, "agent_b_strategy": agent_b_strategy,
        "winner_id": result["winner_id"], "turns_played": result["turns_played"],
        "balances": result["balances"], "result": result,
    }


# ---- Single-agent matchmaking ----

def play_single_agent(transport, api_key, strategy_name="balanced", poll_interval=2):
    choose = _strategy_for_name(strategy_name)
    client = BattleClient(transport)
    my_id = client.transport.request("GET", "/agents/me", api_key=api_key)["agent_id"]

    battle_id = None
    for b in client.list_open_battles(api_key):
        if b["participants"][0] != my_id:
            try:
                client.join_battle(api_key, b["battle_id"])
                battle_id = b["battle_id"]
                break
            except RuntimeError:
                continue
    if battle_id is None:
        battle_id = client.create_battle(api_key)["battle_id"]

    battle = client.get_battle(api_key, battle_id)
    while battle["status"] == "created":
        time.sleep(poll_interval)
        battle = client.get_battle(api_key, battle_id)

    while battle["status"] != "resolved":
        if battle["needs_action"]:
            battle = client.submit_bid(api_key, battle_id, choose(battle))
        else:
            time.sleep(poll_interval)
            battle = client.get_battle(api_key, battle_id)

    return client.result(api_key, battle_id)
