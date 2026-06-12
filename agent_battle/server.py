import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from agent_battle.arena import Arena, ArenaError


class App:
    def __init__(self, arena):
        self.arena = arena

    def handle(self, request):
        try:
            return self._handle(request)
        except ArenaError as error:
            return self._json(error.status, {"error": error.message})
        except json.JSONDecodeError:
            return self._json(400, {"error": "invalid json body"})

    def _handle(self, request):
        method = request["method"]
        path = request["path"].strip("/")
        parts = [] if path == "" else path.split("/")
        body = self._json_body(request)
        api_key = self._api_key(request)

        if method == "GET" and parts == []:
            return self._json(
                200,
                {
                    "service": "agent-battle-arena",
                    "status": "ok",
                    "endpoints": [
                        "POST /agents",
                        "GET /agents/me",
                        "POST /battles",
                        "POST /battles/{battle_id}/join",
                        "GET /battles/{battle_id}",
                        "POST /battles/{battle_id}/actions",
                        "GET /battles/{battle_id}/result",
                    ],
                },
            )
        if method == "POST" and parts == ["agents"]:
            return self._json(201, self.arena.create_agent())
        if method == "GET" and parts == ["agents", "me"]:
            return self._json(200, self.arena.get_agent(api_key))
        if method == "POST" and parts == ["battles"]:
            return self._json(201, self.arena.create_battle(api_key, body.get("stake")))
        if method == "POST" and len(parts) == 3 and parts[0] == "battles" and parts[2] == "join":
            return self._json(200, self.arena.join_battle(api_key, parts[1]))
        if method == "GET" and len(parts) == 2 and parts[0] == "battles":
            return self._json(200, self.arena.get_battle(api_key, parts[1]))
        if method == "POST" and len(parts) == 3 and parts[0] == "battles" and parts[2] == "actions":
            return self._json(200, self.arena.submit_action(api_key, parts[1], body.get("action")))
        if method == "GET" and len(parts) == 3 and parts[0] == "battles" and parts[2] == "result":
            return self._json(200, self.arena.get_result(api_key, parts[1]))

        return self._json(404, {"error": "route not found"})

    def _json_body(self, request):
        raw_body = request.get("body") or "{}"
        return json.loads(raw_body)

    def _api_key(self, request):
        auth = request.get("headers", {}).get("authorization", "")
        prefix = "Bearer "
        if auth.startswith(prefix):
            return auth[len(prefix) :]
        return None

    def _json(self, status, payload):
        return status, {"content-type": "application/json"}, json.dumps(payload, sort_keys=True)


def create_app(arena=None):
    return App(arena or Arena())


def run_server(host="127.0.0.1", port=8080):
    app = create_app()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._handle()

        def do_POST(self):
            self._handle()

        def _handle(self):
            length = int(self.headers.get("content-length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            headers = {key.lower(): value for key, value in self.headers.items()}
            request = {
                "method": self.command,
                "path": urlparse(self.path).path,
                "headers": headers,
                "body": body,
            }
            status, response_headers, response_body = app.handle(request)
            encoded = response_body.encode("utf-8")
            self.send_response(status)
            for key, value in response_headers.items():
                self.send_header(key, value)
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Agent Battle arena listening on http://{host}:{port}")
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Run the Agent Battle MVP arena.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8080, type=int)
    args = parser.parse_args()
    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
