import argparse
import json
import urllib.error
import urllib.request


class HttpTransport:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")

    def request(self, method, path, api_key=None, payload=None):
        data = None
        headers = {"content-type": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"

        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
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


class BattleClient:
    def __init__(self, transport):
        self.transport = transport

    def create_agent(self):
        return self.transport.request("POST", "/agents", payload={})

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


def play_demo_battle(transport):
    client = BattleClient(transport)
    agent_a = client.create_agent()
    agent_b = client.create_agent()
    battle = client.create_battle(agent_a["api_key"])
    battle_id = battle["battle_id"]
    client.join_battle(agent_b["api_key"], battle_id)

    agent_a_plan = ["special", "attack", "attack", "charge", "charge", "special"]
    agent_b_plan = ["charge"] * len(agent_a_plan)

    state = None
    for action_a, action_b in zip(agent_a_plan, agent_b_plan):
        state = client.submit_action(agent_a["api_key"], battle_id, action_a)
        if state["status"] == "resolved":
            break
        state = client.submit_action(agent_b["api_key"], battle_id, action_b)
        if state["status"] == "resolved":
            break

    if state is None or state["status"] != "resolved":
        client.submit_action(agent_b["api_key"], battle_id, "forfeit")

    return client.result(agent_a["api_key"], battle_id)


def main():
    parser = argparse.ArgumentParser(description="Run a two-agent Agent Battle demo.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()

    result = play_demo_battle(HttpTransport(args.base_url))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
