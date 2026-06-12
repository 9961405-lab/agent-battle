import argparse
import html
import json
import logging
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from agent_battle import config
from agent_battle.arena import Arena, ArenaError

logger = logging.getLogger("agent-battle")


class RateLimiter:
    """Simple in-memory token bucket per client IP."""

    def __init__(self, max_per_minute):
        self._max = max_per_minute
        self._buckets = {}
        self._cleanup_at = 0

    def allow(self, client_ip):
        now = time.monotonic()
        # periodic cleanup: drop buckets older than 10 minutes
        if now - self._cleanup_at > 300:
            stale = [ip for ip, b in self._buckets.items() if now - b["last"] > 600]
            for ip in stale:
                del self._buckets[ip]
            self._cleanup_at = now

        bucket = self._buckets.get(client_ip)
        if bucket is None:
            bucket = self._buckets[client_ip] = {"tokens": self._max, "last": now}
        elapsed = now - bucket["last"]
        bucket["tokens"] = min(self._max, bucket["tokens"] + elapsed * (self._max / 60.0))
        bucket["last"] = now
        if bucket["tokens"] >= 1:
            bucket["tokens"] -= 1
            return True
        return False


class App:
    def __init__(self, arena):
        self.arena = arena
        self._rate_limiter = RateLimiter(config.RATE_LIMIT_PER_MINUTE)

    def handle(self, request):
        try:
            client_ip = request.get("client_ip", "127.0.0.1")
            path = request["path"].strip("/")
            # dashboard routes are not rate-limited
            if not path.startswith("dashboard") and not self._rate_limiter.allow(client_ip):
                return self._json(429, {"error": "rate limit exceeded"})
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
                        "GET /battles/open",
                        "GET /battles/room/{room_code}",
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
            agent = self.arena.create_agent(name=body.get("name"))
            logger.info("agent created id=%s", agent["agent_id"])
            return self._json(201, agent)
        if method == "GET" and parts == ["agents", "me"]:
            return self._json(200, self.arena.get_agent(api_key))
        if method == "POST" and parts == ["battles"]:
            result = self.arena.create_battle(api_key, body.get("stake"), room=body.get("room"))
            logger.info("battle created id=%s room=%s", result["battle_id"], result.get("room"))
            return self._json(201, result)
        if method == "GET" and parts == ["battles", "open"]:
            return self._json(200, {"open_battles": self.arena.list_open_battles()})
        if method == "GET" and len(parts) == 3 and parts[:2] == ["battles", "room"]:
            battle = self.arena.find_battle_by_room(parts[2])
            if not battle:
                return self._json(404, {"error": "room not found"})
            return self._json(200, battle)
        if method == "POST" and len(parts) == 3 and parts[0] == "battles" and parts[2] == "join":
            result = self.arena.join_battle(api_key, parts[1])
            logger.info("battle joined id=%s", parts[1])
            return self._json(200, result)
        if method == "GET" and len(parts) == 2 and parts[0] == "battles":
            return self._json(200, self.arena.get_battle(api_key, parts[1]))
        if method == "POST" and len(parts) == 3 and parts[0] == "battles" and parts[2] == "actions":
            result = self.arena.submit_action(api_key, parts[1], body.get("action"))
            if result["status"] == "resolved":
                logger.info("battle resolved id=%s winner=%s", parts[1], result.get("winner_id"))
            return self._json(200, result)
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
            return auth[len(prefix):]
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
        return self._page(
            "Agent Battle Dashboard",
            f"""
            <header class="topbar">
              <div>
                <h1>Agent Battle Dashboard</h1>
                <p>Public read-only view &mdash; auto-updates every 5 s</p>
              </div>
              <button class="button" onclick="refresh()">Refresh</button>
            </header>
            <main>
              <section class="stats" id="stats"></section>
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
                  <tbody id="battle-rows"></tbody>
                </table>
              </section>
            </main>
            """,
            head_extra="",
            body_extra=f"""<script>
            var initialBattles = {json.dumps(battles)};
            </script>
            <script>
            {_DASHBOARD_JS}
            </script>""",
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
              <button class="button" onclick="location.reload()">Refresh</button>
            </header>
            <main>
              <section class="meta" id="meta">{self._meta_html(battle)}</section>
              <section class="agents" id="agent-cards">{state_cards}</section>
              <section class="table-wrap">
                <h2>Battle Log</h2>
                <table>
                  <thead>
                    <tr>
                      <th>Turn</th>
                      <th>Actor</th>
                      <th>Action</th>
                      <th>Result</th>
                    </tr>
                  </thead>
                  <tbody id="log-rows">{log_rows}</tbody>
                </table>
              </section>
            </main>
            """,
            head_extra="",
            body_extra=f"""<script>
            var battleId = {json.dumps(battle_id)};
            </script>
            <script>
            {_BATTLE_JS}
            </script>""",
        )

    # ------------------------------------------------------------------
    # HTML snippet helpers
    # ------------------------------------------------------------------

    def _state_card(self, battle, agent_id):
        state = battle["states"][agent_id]
        winner = " winner" if battle["winner_id"] == agent_id else ""
        defending = " (defending)" if state.get("defending") else ""
        return f"""
        <article class="agent-card{winner}">
          <div class="agent-title">{self._short_id(agent_id)}</div>
          <div class="agent-id">{self._escape(agent_id)}</div>
          <dl>
            <div><dt>HP</dt><dd>{state['hp']}</dd></div>
            <div><dt>MP</dt><dd>{state['mp']}</dd></div>
            <div><dt>Status</dt><dd>{self._escape(defending.strip() or '-')}</dd></div>
          </dl>
        </article>
        """

    def _meta_html(self, battle):
        winner = "-"
        if battle["status"] == "resolved":
            winner = "Draw" if battle["winner_id"] is None else self._short_id(battle["winner_id"])
        return f"""
        <div><span>Status</span><strong>{self._status_badge(battle['status'])}</strong></div>
        <div><span>Turn</span><strong>{battle['turn']}</strong></div>
        <div><span>Stake</span><strong>{battle['stake']}</strong></div>
        <div><span>Winner</span><strong>{self._escape(winner)}</strong></div>
        """

    def _log_row(self, entry, participants):
        actor = entry["actor"]
        note = self._escape(entry["note"])
        a_after = entry["after"].get(participants[0], {})
        b_after = entry["after"].get(participants[1], {})
        return f"""
        <tr>
          <td>{entry['turn']}</td>
          <td>{self._short_id(actor)}</td>
          <td>{self._escape(entry['action'])}</td>
          <td>{note}</td>
          <td>{self._state_text(a_after)}</td>
          <td>{self._state_text(b_after)}</td>
        </tr>
        """

    def _state_text(self, state):
        if not state:
            return "-"
        defending = " D" if state.get("defending") else ""
        return f"HP {state['hp']} / MP {state['mp']}{defending}"

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

    def _page(self, title, body, head_extra="", body_extra=""):
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
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
      font-size: 14px;
      white-space: nowrap;
      cursor: pointer;
    }}
    .button:hover {{ background: #eef1f5; }}
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
  {head_extra}
</head>
<body>
{body}
{body_extra}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Inline JavaScript for AJAX dashboard
# ---------------------------------------------------------------------------

_DASHBOARD_JS = r"""
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function shortId(v) {
  var s = esc(v);
  var idx = s.indexOf('_');
  return idx === -1 ? s.slice(0,14) : s.slice(0, idx+1) + s.slice(idx+1, idx+9);
}

function badge(status) {
  return '<span class="badge ' + esc(status) + '">' + esc(status) + '</span>';
}

function winnerText(b) {
  if (b.status !== 'resolved') return '-';
  if (b.winner_id == null) return 'Draw';
  return shortId(b.winner_id);
}

function participant(parts, idx) {
  return idx < parts.length ? shortId(parts[idx]) : 'Waiting';
}

function renderList(battles) {
  var stats = document.getElementById('stats');
  var total = battles.length;
  var active = battles.filter(function(b){return b.status==='active'}).length;
  var created = battles.filter(function(b){return b.status==='created'}).length;
  var resolved = battles.filter(function(b){return b.status==='resolved'}).length;
  stats.innerHTML =
    '<div><strong>' + total + '</strong><span>Total battles</span></div>' +
    '<div><strong>' + active + '</strong><span>Active</span></div>' +
    '<div><strong>' + created + '</strong><span>Waiting</span></div>' +
    '<div><strong>' + resolved + '</strong><span>Resolved</span></div>';

  var tbody = document.getElementById('battle-rows');
  if (battles.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">No battles yet. Run <code>./battle.sh</code> or connect an agent to create one.</td></tr>';
    return;
  }
  tbody.innerHTML = battles.map(function(b) {
    var bid = esc(b.battle_id);
    return '<tr>' +
      '<td><a href="/dashboard/battles/' + bid + '">' + shortId(b.battle_id) + '</a></td>' +
      '<td>' + participant(b.participants, 0) + '</td>' +
      '<td>' + participant(b.participants, 1) + '</td>' +
      '<td>' + badge(b.status) + '</td>' +
      '<td>' + b.turn + '</td>' +
      '<td>' + b.stake + '</td>' +
      '<td>' + winnerText(b) + '</td>' +
      '</tr>';
  }).join('');
}

function refresh() {
  fetch('/dashboard/data')
    .then(function(r) { return r.json(); })
    .then(function(data) { renderList(data.battles); });
}

renderList(typeof initialBattles !== 'undefined' ? initialBattles : []);
setInterval(refresh, 5000);
"""

_BATTLE_JS = r"""
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function shortId(v) {
  var s = esc(v);
  var idx = s.indexOf('_');
  return idx === -1 ? s.slice(0,14) : s.slice(0, idx+1) + s.slice(idx+1, idx+9);
}

function badge(status) {
  return '<span class="badge ' + esc(status) + '">' + esc(status) + '</span>';
}

function actionText(entry) {
  return esc(entry.action);
}

function stateText(st) {
  if (!st) return '-';
  var def = st.defending ? ' D' : '';
  return 'HP ' + st.hp + ' / MP ' + st.mp + def;
}

function renderMeta(b) {
  var w = b.winner_id;
  var winLabel = '-';
  if (b.status === 'resolved') winLabel = w == null ? 'Draw' : shortId(w);
  document.getElementById('meta').innerHTML =
    '<div><span>Status</span><strong>' + badge(b.status) + '</strong></div>' +
    '<div><span>Turn</span><strong>' + b.turn + '</strong></div>' +
    '<div><span>Stake</span><strong>' + b.stake + '</strong></div>' +
    '<div><span>Winner</span><strong>' + esc(winLabel) + '</strong></div>';
}

function renderCards(b) {
  var parts = b.participants;
  var html = parts.map(function(aid) {
    var st = b.states[aid];
    var w = b.winner_id === aid ? ' winner' : '';
    var defending = st.defending ? ' (defending)' : '';
    return '<article class="agent-card' + w + '">' +
      '<div class="agent-title">' + shortId(aid) + '</div>' +
      '<div class="agent-id">' + esc(aid) + '</div>' +
      '<dl>' +
        '<div><dt>HP</dt><dd>' + st.hp + '</dd></div>' +
        '<div><dt>MP</dt><dd>' + st.mp + '</dd></div>' +
        '<div><dt>Status</dt><dd>' + esc(defending || '-') + '</dd></div>' +
      '</dl>' +
      '</article>';
  }).join('');
  if (parts.length === 1) html += '<article class="agent-card empty-card">Waiting for opponent</article>';
  document.getElementById('agent-cards').innerHTML = html;
}

function renderLog(b) {
  var parts = b.participants;
  var entries = b.battle_log;
  var tbody = document.getElementById('log-rows');
  if (entries.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">No turns yet.</td></tr>';
    return;
  }
  tbody.innerHTML = entries.map(function(e) {
    return '<tr>' +
      '<td>' + e.turn + '</td>' +
      '<td>' + shortId(e.actor) + '</td>' +
      '<td>' + esc(e.action) + '</td>' +
      '<td>' + esc(e.note) + '</td>' +
      '<td>' + stateText(e.after[parts[0]]) + '</td>' +
      '<td>' + stateText(e.after[parts[1]]) + '</td>' +
      '</tr>';
  }).join('');
}

function refreshBattle() {
  fetch('/dashboard/data')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var b = null;
      for (var i = 0; i < data.battles.length; i++) {
        if (data.battles[i].battle_id === battleId) { b = data.battles[i]; break; }
      }
      if (b) {
        renderMeta(b);
        renderCards(b);
        renderLog(b);
      }
    });
}

setInterval(refreshBattle, 5000);
"""


# ---------------------------------------------------------------------------
# Server entry points
# ---------------------------------------------------------------------------


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
                "client_ip": self.client_address[0],
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
            logger.debug(
                "%s %s %s",
                self.client_address[0],
                format % args if args else format,
                self.headers.get("User-Agent", "-"),
            )

    server = ThreadingHTTPServer((host, port), Handler)
    logger.info("Agent Battle arena listening on http://%s:%s", host, port)
    print(f"Agent Battle arena listening on http://{host}:{port}")
    server.serve_forever()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )
    parser = argparse.ArgumentParser(description="Run the Agent Battle MVP arena.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8080, type=int)
    args = parser.parse_args()
    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
