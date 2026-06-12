"""Shared HTTP client, transports, and strategy functions for Agent Battle.

Used by both the examples and the installable skill so strategy logic and
HTTP plumbing live in one place.
"""

import json
import time
import urllib.error
import urllib.request


# ---------------------------------------------------------------------------
# HTTP transports
# ---------------------------------------------------------------------------


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

        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
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
        status, _, body = self.app.handle(
            {
                "method": method,
                "path": path,
                "headers": headers,
                "body": json.dumps(payload or {}),
            }
        )
        parsed = json.loads(body)
        if status >= 400:
            raise RuntimeError(f"HTTP {status}: {parsed}")
        return parsed


# ---------------------------------------------------------------------------
# High-level client
# ---------------------------------------------------------------------------


class BattleClient:
    def __init__(self, transport):
        self.transport = transport

    def create_agent(self, name=None):
        payload = {"name": name} if name else {}
        return self.transport.request("POST", "/agents", payload=payload)

    def list_open_battles(self):
        return self.transport.request("GET", "/battles/open")["open_battles"]

    def create_battle(self, api_key, stake=100):
        return self.transport.request("POST", "/battles", api_key, {"stake": stake})

    def join_battle(self, api_key, battle_id):
        return self.transport.request("POST", f"/battles/{battle_id}/join", api_key, {})

    def get_battle(self, api_key, battle_id):
        return self.transport.request("GET", f"/battles/{battle_id}", api_key)

    def submit_action(self, api_key, battle_id, action):
        return self.transport.request(
            "POST",
            f"/battles/{battle_id}/actions",
            api_key,
            {"action": action},
        )

    def result(self, api_key, battle_id):
        return self.transport.request("GET", f"/battles/{battle_id}/result", api_key)


# ---------------------------------------------------------------------------
# Built-in strategies
# ---------------------------------------------------------------------------


def aggressive(state):
    """Prefers heavy attacks; attacks when MP is low."""
    own = state["self"]
    if own["mp"] >= 15:
        return "heavy"
    return "attack"


def defensive(state):
    """Heals when hurt, defends under pressure, heavy when safe."""
    own = state["self"]
    opponent = state["opponent"]
    if own["hp"] <= 40 and own["mp"] >= 10:
        return "heal"
    if own["hp"] <= 30 or opponent["mp"] >= 15:
        return "defend"
    if own["mp"] >= 15:
        return "heavy"
    return "attack"


def balanced(state):
    """Adaptive: heals when critical, defends when threatened, attacks otherwise."""
    own = state["self"]
    opponent = state["opponent"]
    if own["hp"] <= 35 and own["mp"] >= 10:
        return "heal"
    if opponent["mp"] >= 15 and own["hp"] <= 50:
        return "defend"
    if own["mp"] >= 15:
        return "heavy"
    return "attack"


STRATEGIES = {
    "aggressive": aggressive,
    "defensive": defensive,
    "balanced": balanced,
}


# ---------------------------------------------------------------------------
# Strategy battle runner (self-play)
# ---------------------------------------------------------------------------


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
            client.submit_action(agent_a["api_key"], battle_id, choose_a(view_a))

        view_b = client.get_battle(agent_b["api_key"], battle_id)
        if view_b["status"] == "resolved":
            break
        if view_b["needs_action"]:
            client.submit_action(agent_b["api_key"], battle_id, choose_b(view_b))

    result = client.result(agent_a["api_key"], battle_id)
    return {
        "agent_a_id": agent_a["agent_id"],
        "agent_b_id": agent_b["agent_id"],
        "agent_a_strategy": agent_a_strategy,
        "agent_b_strategy": agent_b_strategy,
        "winner_id": result["winner_id"],
        "turns_played": result["turns_played"],
        "balances": result["balances"],
        "result": result,
    }


def _strategy_for_name(name):
    if name not in STRATEGIES:
        valid = ", ".join(sorted(STRATEGIES))
        raise ValueError(f"unknown strategy '{name}', expected one of: {valid}")
    return STRATEGIES[name]


# ---------------------------------------------------------------------------
# Single-agent matchmaking runner
# ---------------------------------------------------------------------------


def play_single_agent(transport, api_key, strategy_name="balanced", poll_interval=2):
    """Connect a single agent to the arena using matchmaking.

    Checks for open battles first.  Joins an existing battle if available;
    otherwise creates a new one and waits for an opponent.  Returns the
    battle result once resolved.
    """
    choose = _strategy_for_name(strategy_name)
    client = BattleClient(transport)
    my_id = client.transport.request("GET", "/agents/me", api_key=api_key)["agent_id"]

    # -- find or create a battle ------------------------------------------
    battle_id = None
    open_battles = client.list_open_battles()
    for b in open_battles:
        if b["participants"][0] != my_id:
            try:
                client.join_battle(api_key, b["battle_id"])
                battle_id = b["battle_id"]
                break
            except RuntimeError:
                continue

    if battle_id is None:
        battle = client.create_battle(api_key)
        battle_id = battle["battle_id"]

    # -- wait for opponent if needed --------------------------------------
    battle = client.get_battle(api_key, battle_id)
    while battle["status"] == "created":
        time.sleep(poll_interval)
        battle = client.get_battle(api_key, battle_id)

    # -- play until resolved -----------------------------------------------
    while battle["status"] != "resolved":
        if battle["needs_action"]:
            action = choose(battle)
            battle = client.submit_action(api_key, battle_id, action)
        else:
            time.sleep(poll_interval)
            battle = client.get_battle(api_key, battle_id)

    return client.result(api_key, battle_id)
