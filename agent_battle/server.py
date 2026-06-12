import argparse
import html
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
                    "dashboard": "/dashboard",
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
        if method == "GET" and parts == ["dashboard"]:
            return self._html(200, self._dashboard_html())
        if method == "GET" and parts == ["dashboard", "data"]:
            return self._json(200, {"battles": self.arena.list_public_battles()})
        if method == "GET" and len(parts) == 3 and parts[:2] == ["dashboard", "battles"]:
            return self._html(200, self._battle_html(parts[2]))
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

    def _html(self, status, body):
        return status, {"content-type": "text/html; charset=utf-8"}, body

    def _dashboard_html(self):
        battles = sorted(
            self.arena.list_public_battles(),
            key=lambda battle: battle["battle_id"],
            reverse=True,
        )
        rows = "\n".join(self._battle_row(battle) for battle in battles)
        if not rows:
            rows = (
                "<tr><td colspan=\"7\" class=\"empty\">No battles yet. "
                "Run <code>./battle.sh</code> or connect an agent to create one.</td></tr>"
            )
        return self._page(
            "Agent Battle Dashboard",
            f"""
            <header class="topbar">
              <div>
                <h1>Agent Battle Dashboard</h1>
                <p>Public read-only view of battles running in this arena.</p>
              </div>
              <a class="button" href="/dashboard">Refresh</a>
            </header>
            <main>
              <section class="stats">
                <div><strong>{len(battles)}</strong><span>Total battles</span></div>
                <div><strong>{self._count_status(battles, "active")}</strong><span>Active</span></div>
                <div><strong>{self._count_status(battles, "created")}</strong><span>Waiting</span></div>
                <div><strong>{self._count_status(battles, "resolved")}</strong><span>Resolved</span></div>
              </section>
              <section class="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Battle</th>
                      <th>Agent A</th>
                      <th>Agent B</th>
                      <th>Status</th>
                      <th>Round</th>
                      <th>Stake</th>
                      <th>Winner</th>
                    </tr>
                  </thead>
                  <tbody>{rows}</tbody>
                </table>
              </section>
            </main>
            """,
        )

    def _battle_html(self, battle_id):
        battle = self.arena.get_public_battle(battle_id)
        participants = battle["participants"]
        state_cards = "\n".join(self._state_card(battle, agent_id) for agent_id in participants)
        if len(participants) == 1:
            state_cards += "<article class=\"agent-card empty-card\">Waiting for opponent</article>"
        log_rows = "\n".join(self._log_row(entry, participants) for entry in battle["battle_log"])
        if not log_rows:
            log_rows = "<tr><td colspan=\"5\" class=\"empty\">No resolved rounds yet.</td></tr>"
        return self._page(
            f"Battle {battle_id}",
            f"""
            <header class="topbar">
              <div>
                <a class="back" href="/dashboard">Back to dashboard</a>
                <h1>{self._short_id(battle_id)}</h1>
                <p>{self._participant_label(participants, 0)} vs {self._participant_label(participants, 1)}</p>
              </div>
              <a class="button" href="/dashboard/battles/{self._escape_attr(battle_id)}">Refresh</a>
            </header>
            <main>
              <section class="meta">
                <div><span>Status</span><strong>{self._status_badge(battle['status'])}</strong></div>
                <div><span>Round</span><strong>{battle['round']}</strong></div>
                <div><span>Stake</span><strong>{battle['stake']}</strong></div>
                <div><span>Winner</span><strong>{self._winner_text(battle)}</strong></div>
              </section>
              <section class="agents">{state_cards}</section>
              <section class="table-wrap">
                <h2>Battle Log</h2>
                <table>
                  <thead>
                    <tr>
                      <th>Round</th>
                      <th>Agent A Action</th>
                      <th>Agent B Action</th>
                      <th>Agent A After</th>
                      <th>Agent B After</th>
                    </tr>
                  </thead>
                  <tbody>{log_rows}</tbody>
                </table>
              </section>
            </main>
            """,
        )

    def _battle_row(self, battle):
        participants = battle["participants"]
        battle_id = self._escape_attr(battle["battle_id"])
        return f"""
        <tr>
          <td><a href="/dashboard/battles/{battle_id}">{self._short_id(battle['battle_id'])}</a></td>
          <td>{self._participant_label(participants, 0)}</td>
          <td>{self._participant_label(participants, 1)}</td>
          <td>{self._status_badge(battle['status'])}</td>
          <td>{battle['round']}</td>
          <td>{battle['stake']}</td>
          <td>{self._winner_text(battle)}</td>
        </tr>
        """

    def _state_card(self, battle, agent_id):
        state = battle["states"][agent_id]
        winner = " winner" if battle["winner_id"] == agent_id else ""
        return f"""
        <article class="agent-card{winner}">
          <div class="agent-title">{self._short_id(agent_id)}</div>
          <div class="agent-id">{self._escape(agent_id)}</div>
          <dl>
            <div><dt>HP</dt><dd>{state['hp']}</dd></div>
            <div><dt>Energy</dt><dd>{state['energy']}</dd></div>
            <div><dt>Special CD</dt><dd>{state['cooldowns']['special']}</dd></div>
          </dl>
        </article>
        """

    def _log_row(self, entry, participants):
        a_id = participants[0]
        b_id = participants[1]
        return f"""
        <tr>
          <td>{entry['round']}</td>
          <td>{self._action_text(entry, a_id)}</td>
          <td>{self._action_text(entry, b_id)}</td>
          <td>{self._state_text(entry['after'][a_id])}</td>
          <td>{self._state_text(entry['after'][b_id])}</td>
        </tr>
        """

    def _action_text(self, entry, agent_id):
        action = entry["actions"][agent_id]
        requested = self._escape(action["requested"])
        resolved = self._escape(action["resolved"])
        if requested == resolved:
            return requested
        return f"{requested} -> {resolved}"

    def _state_text(self, state):
        return (
            f"HP {state['hp']} / Energy {state['energy']} / "
            f"CD {state['cooldowns']['special']}"
        )

    def _count_status(self, battles, status):
        return sum(1 for battle in battles if battle["status"] == status)

    def _winner_text(self, battle):
        if battle["status"] != "resolved":
            return "-"
        if battle["winner_id"] is None:
            return "Draw"
        return self._short_id(battle["winner_id"])

    def _participant_label(self, participants, index):
        if index >= len(participants):
            return "Waiting"
        return self._short_id(participants[index])

    def _short_id(self, value):
        safe = self._escape(value)
        if "_" in value:
            prefix, suffix = value.split("_", 1)
            return f"{self._escape(prefix)}_{self._escape(suffix[:8])}"
        return safe[:14]

    def _status_badge(self, status):
        safe_status = self._escape(status)
        return f"<span class=\"badge {self._escape_attr(status)}\">{safe_status}</span>"

    def _escape(self, value):
        return html.escape(str(value), quote=False)

    def _escape_attr(self, value):
        return html.escape(str(value), quote=True)

    def _page(self, title, body):
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="5">
  <title>{self._escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #647184;
      --line: #d9dee7;
      --active: #0f766e;
      --waiting: #a16207;
      --resolved: #475569;
      --link: #0b5cad;
      --danger: #b42318;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-width: 320px;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    a {{ color: var(--link); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .topbar {{
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 16px;
      padding: 28px 32px 18px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }}
    h1, h2, p {{ margin: 0; }}
    h1 {{ font-size: 24px; letter-spacing: 0; }}
    h2 {{ font-size: 16px; margin-bottom: 12px; }}
    p, .back {{ color: var(--muted); }}
    main {{ padding: 24px 32px 40px; }}
    .button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 36px;
      padding: 0 14px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      font-weight: 600;
      white-space: nowrap;
    }}
    .stats, .meta {{
      display: grid;
      grid-template-columns: repeat(4, minmax(120px, 1fr));
      gap: 12px;
      margin-bottom: 20px;
    }}
    .stats div, .meta div, .agent-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }}
    .stats strong, .meta strong {{
      display: block;
      font-size: 22px;
      line-height: 1.1;
    }}
    .stats span, .meta span, dt, .agent-id {{
      color: var(--muted);
      font-size: 12px;
    }}
    .table-wrap {{
      overflow-x: auto;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }}
    table {{
      width: 100%;
      min-width: 760px;
      border-collapse: collapse;
    }}
    th, td {{
      padding: 10px 8px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: middle;
      white-space: nowrap;
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }}
    tr:last-child td {{ border-bottom: 0; }}
    .badge {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 0 8px;
      border-radius: 999px;
      color: #fff;
      font-size: 12px;
      font-weight: 700;
    }}
    .badge.active {{ background: var(--active); }}
    .badge.created {{ background: var(--waiting); }}
    .badge.resolved {{ background: var(--resolved); }}
    .agents {{
      display: grid;
      grid-template-columns: repeat(2, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 20px;
    }}
    .agent-card.winner {{ border-color: var(--active); }}
    .agent-title {{ font-size: 18px; font-weight: 800; }}
    .agent-id {{
      margin-top: 4px;
      overflow-wrap: anywhere;
    }}
    dl {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 10px;
      margin: 14px 0 0;
    }}
    dt, dd {{ margin: 0; }}
    dd {{ font-size: 22px; font-weight: 800; }}
    .empty, .empty-card {{
      color: var(--muted);
      text-align: center;
    }}
    code {{
      padding: 2px 5px;
      border-radius: 4px;
      background: #eef1f5;
    }}
    @media (max-width: 720px) {{
      .topbar {{
        align-items: flex-start;
        flex-direction: column;
        padding: 20px 16px 14px;
      }}
      main {{ padding: 16px; }}
      .stats, .meta, .agents {{
        grid-template-columns: 1fr;
      }}
      table {{ min-width: 680px; }}
    }}
  </style>
</head>
<body>
{body}
</body>
</html>"""


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
