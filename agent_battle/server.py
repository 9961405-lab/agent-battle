"""Agent Battle HTTP server — blind-bid arena with fog of war."""

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
    def __init__(self, max_per_minute):
        self._max = max_per_minute
        self._buckets = {}
        self._cleanup_at = 0

    def allow(self, client_ip):
        now = time.monotonic()
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
    def __init__(self, arena, rate_limit=None):
        self.arena = arena
        self._rate_limiter = RateLimiter(rate_limit if rate_limit is not None else config.RATE_LIMIT_PER_MINUTE)

    def handle(self, request):
        try:
            client_ip = request.get("client_ip", "127.0.0.1")
            path = request["path"].strip("/")
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
            return self._json(200, {
                "service": "agent-battle-arena",
                "status": "ok",
                "dashboard": "/dashboard",
                "endpoints": [
                    "POST /agents",
                    "GET /agents/me",
                    "POST /battles",
                    "GET /battles/open",
                    "GET /battles/room/{code}",
                    "POST /battles/{id}/join",
                    "GET /battles/{id}",
                    "POST /battles/{id}/bid",
                    "GET /battles/{id}/result",
                ],
            })
        if method == "GET" and parts == ["dashboard"]:
            return self._html(200, self._dashboard_html())
        if method == "GET" and parts == ["dashboard", "data"]:
            return self._json(200, {"battles": self.arena.list_public_battles()})
        if method == "GET" and len(parts) == 3 and parts[:2] == ["dashboard", "battles"]:
            return self._html(200, self._battle_html(parts[2]))
        if method == "POST" and parts == ["agents"]:
            agent = self.arena.create_agent(name=body.get("name"), skills=body.get("skills"), owner=body.get("owner"))
            logger.info("agent created id=%s skills=%s", agent["agent_id"], agent.get("skills"))
            return self._json(201, agent)
        if method == "GET" and parts == ["agents", "me"]:
            return self._json(200, self.arena.get_agent(api_key))
        if method == "POST" and parts == ["battles"]:
            result = self.arena.create_battle(api_key, body.get("stake"), room=body.get("room"))
            logger.info("battle created id=%s room=%s", result["battle_id"], result.get("room"))
            return self._json(201, result)
        if method == "GET" and parts == ["battles", "open"]:
            return self._json(200, {"open_battles": self.arena.list_open_battles(api_key)})
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
        if method == "POST" and len(parts) == 3 and parts[0] == "battles" and parts[2] == "bid":
            result = self.arena.submit_bid(api_key, parts[1], body.get("bid", 0))
            if result["status"] == "resolved":
                logger.info("battle resolved id=%s winner=%s", parts[1], result.get("winner_id"))
            return self._json(200, result)
        if method == "GET" and len(parts) == 3 and parts[0] == "battles" and parts[2] == "result":
            return self._json(200, self.arena.get_result(api_key, parts[1]))
        return self._json(404, {"error": "route not found"})

    # ---- helpers ----
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

    # ---- dashboard ----
    def _dashboard_html(self):
        battles = sorted(self.arena.list_public_battles(), key=lambda b: b["battle_id"], reverse=True)
        return self._page("Agent Battle Dashboard", f"""
        <header class="topbar">
          <div><h1>Agent Battle Dashboard</h1><p>Public view &mdash; auto-updates every 5 s</p></div>
          <button class="button" onclick="refresh()">Refresh</button>
        </header>
        <main>
          <section class="stats" id="stats"></section>
          <section class="table-wrap">
            <table>
              <thead><tr><th>Battle</th><th>Agent A</th><th>Agent B</th><th>Status</th><th>Turn</th><th>Stake</th><th>Winner</th></tr></thead>
              <tbody id="battle-rows"></tbody>
            </table>
          </section>
        </main>
        """, body_extra=f"""<script>var initialBattles={json.dumps(battles)};</script><script>{_DASHBOARD_JS}</script>""")

    def _battle_html(self, battle_id):
        battle = self.arena.get_public_battle(battle_id)
        participants = battle["participants"]
        return self._page(f"Battle {battle_id}", f"""
        <header class="topbar">
          <div><a class="back" href="/dashboard">Back</a><h1>{self._short_id(battle_id)}</h1><p>{self._participant_label(participants, 0)} vs {self._participant_label(participants, 1)}</p></div>
          <button class="button" onclick="location.reload()">Refresh</button>
        </header>
        <main>
          <section class="meta" id="meta"></section>
          <div class="latest" id="latest"></div>
          <section class="agents" id="agent-cards"></section>
          <section class="table-wrap"><h2>Play-by-play</h2>
            <div class="feed" id="log-feed"></div>
          </section>
        </main>
        """, body_extra=f"""<script>var battleId={json.dumps(battle_id)};var initialBattle={json.dumps(battle)};</script><script>{_BATTLE_JS}</script>""")

    def _participant_label(self, p, i):
        return "Waiting" if i >= len(p) else self._short_id(p[i])

    def _short_id(self, v):
        s = self._escape(v)
        if "_" in v:
            prefix, suffix = v.split("_", 1)
            return f"{self._escape(prefix)}_{self._escape(suffix[:8])}"
        return s[:14]

    def _status_badge(self, s):
        return f"<span class=\"badge {self._escape_attr(s)}\">{self._escape(s)}</span>"

    def _escape(self, v):
        return html.escape(str(v), quote=False)

    def _escape_attr(self, v):
        return html.escape(str(v), quote=True)

    def _page(self, title, body, head_extra="", body_extra=""):
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{self._escape(title)}</title><style>
:root{{color-scheme:light;--bg:#f6f7f9;--panel:#fff;--text:#17202a;--muted:#647184;--line:#d9dee7;--active:#0f766e;--waiting:#a16207;--resolved:#475569;--link:#0b5cad}}
*{{box-sizing:border-box}}body{{margin:0;min-width:320px;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
a{{color:var(--link);text-decoration:none}}a:hover{{text-decoration:underline}}
.topbar{{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;padding:28px 32px 18px;border-bottom:1px solid var(--line);background:var(--panel)}}
h1,h2,p{{margin:0}}h1{{font-size:24px}}h2{{font-size:16px;margin-bottom:12px}}p,.back{{color:var(--muted)}}
main{{padding:24px 32px 40px}}
.button{{display:inline-flex;align-items:center;justify-content:center;min-height:36px;padding:0 14px;border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--text);font-weight:600;font-size:14px;white-space:nowrap;cursor:pointer}}
.button:hover{{background:#eef1f5}}
.stats,.meta{{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:12px;margin-bottom:20px}}
.stats div,.meta div,.agent-card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px}}
.stats strong,.meta strong{{display:block;font-size:22px;line-height:1.1}}
.stats span,.meta span,dt,.agent-id{{color:var(--muted);font-size:12px}}
.table-wrap{{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px}}
table{{width:100%;min-width:760px;border-collapse:collapse}}
th,td{{padding:10px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle;white-space:nowrap}}
th{{color:var(--muted);font-size:12px;font-weight:700;text-transform:uppercase}}
tr:last-child td{{border-bottom:0}}
.badge{{display:inline-flex;align-items:center;min-height:24px;padding:0 8px;border-radius:999px;color:#fff;font-size:12px;font-weight:700}}
.badge.active{{background:var(--active)}}.badge.created{{background:var(--waiting)}}.badge.resolved{{background:var(--resolved)}}
.agents{{display:grid;grid-template-columns:repeat(2,minmax(220px,1fr));gap:16px;margin-bottom:20px}}
.agent-card.winner{{border-color:var(--active)}}.agent-title{{font-size:18px;font-weight:800}}.agent-id{{margin-top:4px;overflow-wrap:anywhere}}
dl{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin:14px 0 0}}dt,dd{{margin:0}}dd{{font-size:22px;font-weight:800}}
.empty,.empty-card{{color:var(--muted);text-align:center}}code{{padding:2px 5px;border-radius:4px;background:#eef1f5}}
.bar{{height:14px;border-radius:7px;background:#e8ebf0;overflow:hidden;margin-top:4px}}
.bar>i{{display:block;height:100%;border-radius:7px;transition:width .5s ease}}
.bar.hp>i{{background:#10b981}}.bar.hp.mid>i{{background:#f59e0b}}.bar.hp.low>i{{background:#ef4444}}
.bar.mp>i{{background:#3b82f6}}
.statline{{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin-top:10px}}
.statline b{{color:var(--text);font-size:13px}}
.chips{{display:flex;flex-wrap:wrap;gap:5px;margin-top:10px}}
.chip{{font-size:11px;padding:2px 8px;border-radius:999px;background:#eef1f5;color:#475569;font-weight:600}}
.latest{{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--active);border-radius:8px;padding:14px 16px;margin-bottom:20px;font-size:15px;font-weight:600;min-height:20px}}
.latest.storm{{border-left-color:#7c3aed;background:#faf5ff}}
.storm-meter{{display:flex;align-items:center;gap:8px}}
.storm-meter .dot{{width:9px;height:9px;border-radius:50%;background:#e8ebf0}}
.storm-meter .dot.on{{background:#7c3aed}}
.feed{{display:flex;flex-direction:column;gap:8px;max-height:520px;overflow-y:auto}}
.evt{{display:flex;gap:10px;align-items:baseline;padding:9px 12px;border:1px solid var(--line);border-radius:8px;background:#fbfcfd}}
.evt .t{{flex:0 0 38px;color:var(--muted);font-size:12px;font-weight:700}}
.evt .c{{flex:1;font-size:13px}}
.evt.kill{{border-color:#ef4444;background:#fef2f2}}.evt.tie{{opacity:.75}}.evt.storm{{border-color:#c4b5fd;background:#faf5ff}}
.fx{{font-size:12px;color:var(--muted);margin-left:6px}}
@media(max-width:720px){{.topbar{{align-items:flex-start;flex-direction:column;padding:20px 16px 14px}}main{{padding:16px}}.stats,.meta,.agents{{grid-template-columns:1fr}}table{{min-width:680px}}}}
</style>{head_extra}</head><body>{body}{body_extra}</body></html>"""


_DASHBOARD_JS = r"""
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function shortId(v){var s=esc(v);var i=s.indexOf('_');return i===-1?s.slice(0,14):s.slice(0,i+1)+s.slice(i+1,i+9)}
function badge(s){return '<span class="badge '+esc(s)+'">'+esc(s)+'</span>'}
function winnerText(b){if(b.status!=='resolved')return'-';if(b.winner_id==null)return'Draw';return shortId(b.winner_id)}
function participant(p,i){return i<p.length?shortId(p[i]):'Waiting'}
function renderList(battles){
  var s=document.getElementById('stats');
  var t=battles.length,a=battles.filter(function(b){return b.status==='active'}).length,
    c=battles.filter(function(b){return b.status==='created'}).length,
    r=battles.filter(function(b){return b.status==='resolved'}).length;
  s.innerHTML='<div><strong>'+t+'</strong><span>Total</span></div><div><strong>'+a+'</strong><span>Active</span></div><div><strong>'+c+'</strong><span>Waiting</span></div><div><strong>'+r+'</strong><span>Resolved</span></div>';
  var tb=document.getElementById('battle-rows');
  if(battles.length===0){tb.innerHTML='<tr><td colspan="7" class="empty">No battles yet.</td></tr>';return}
  tb.innerHTML=battles.map(function(b){var bid=esc(b.battle_id);return'<tr><td><a href="/dashboard/battles/'+bid+'">'+shortId(b.battle_id)+'</a></td><td>'+participant(b.participants,0)+'</td><td>'+participant(b.participants,1)+'</td><td>'+badge(b.status)+'</td><td>'+b.turn+'</td><td>'+b.stake+'</td><td>'+winnerText(b)+'</td></tr>'}).join('')
}
function refresh(){fetch('/dashboard/data').then(function(r){return r.json()}).then(function(d){renderList(d.battles)})}
renderList(typeof initialBattles!=='undefined'?initialBattles:[]);setInterval(refresh,5000)
"""

_BATTLE_JS = r"""
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function shortId(v){var s=esc(v);var i=s.indexOf('_');return i===-1?s.slice(0,14):s.slice(0,i+1)+s.slice(i+1,i+9)}
function badge(s){return '<span class="badge '+esc(s)+'">'+esc(s)+'</span>'}
function clampPct(v,max){var p=Math.max(0,Math.min(100,(v/max)*100));return p}
function hpClass(v,max){var r=v/max;return r<=0.25?'low':(r<=0.5?'mid':'')}
var SKILL_ICON={vampire:'🧛',berserker:'🔥',focused:'🎯',thornmail:'🌵',meditate:'🧘',poison:'☠️',guard:'🛡️',overcharge:'⚡'};

function stormLevel(b){return Math.max(0,(b.turn||0)-(b.storm_start||10)+1)}

function renderMeta(b){
  var w=b.winner_id,win='-';if(b.status==='resolved')win=w==null?'Draw':shortId(w);
  var lvl=stormLevel(b),dots='';for(var i=0;i<6;i++)dots+='<span class="dot'+(i<lvl?' on':'')+'"></span>';
  var stormHtml=lvl>0?('<div class="storm-meter">'+dots+'<b style="color:#7c3aed">-'+lvl+' HP/turn</b></div>'):('<span>turn '+(b.storm_start||10)+'</span>');
  document.getElementById('meta').innerHTML=
    '<div><span>Status</span><strong>'+badge(b.status)+'</strong></div>'+
    '<div><span>Turn</span><strong>'+b.turn+' / '+(b.max_turns||30)+'</strong></div>'+
    '<div><span>⛈️ Storm</span><strong>'+stormHtml+'</strong></div>'+
    '<div><span>Winner</span><strong>'+esc(win)+'</strong></div>';
}

function renderCards(b){
  var p=b.participants,maxhp=b.max_hp||100,maxmp=b.max_mp||100;
  var h=p.map(function(aid){
    var s=b.states[aid],w=b.winner_id===aid?' winner':'';
    var sk=(b.skills&&b.skills[aid])||[];
    var chips=sk.map(function(k){return '<span class="chip">'+(SKILL_ICON[k]||'')+' '+esc(k)+'</span>'}).join('');
    return '<article class="agent-card'+w+'">'+
      '<div class="agent-title">'+(w?'👑 ':'')+shortId(aid)+'</div>'+
      '<div class="statline"><span>HP</span><b>'+s.hp+' / '+maxhp+'</b></div>'+
      '<div class="bar hp '+hpClass(s.hp,maxhp)+'"><i style="width:'+clampPct(s.hp,maxhp)+'%"></i></div>'+
      '<div class="statline"><span>MP</span><b>'+s.mp+' / '+maxmp+'</b></div>'+
      '<div class="bar mp"><i style="width:'+clampPct(s.mp,maxmp)+'%"></i></div>'+
      '<div class="chips">'+chips+'</div></article>';
  }).join('');
  if(p.length===1)h+='<article class="agent-card empty-card">Waiting for opponent…</article>';
  document.getElementById('agent-cards').innerHTML=h;
}

// Build a spectator commentary line from a log entry.
function commentary(x,p){
  var bidA=x.bids[p[0]],bidB=x.bids[p[1]],fx=[],main;
  var ev=x.events||[];
  if(x.winner==null){
    main='🤝 Both commit '+bidA+' vs '+bidB+' — stalemate, MP restored';
  }else{
    var wn=shortId(x.winner),wbid=x.bids[x.winner],lbid=(x.winner===p[0]?bidB:bidA);
    var allin=wbid>=25?'🚀 ':'💥 ';
    main=allin+wn+' bids '+wbid+' over '+lbid+' → '+x.damage+' dmg';
  }
  ev.forEach(function(e){
    if(e==='guard')fx.push('🛡️ shield absorbs it');
    else if(e==='vampire')fx.push('🧛 lifesteal');
    else if(e==='poison_applied')fx.push('☠️ poison applied');
    else if(e==='poison')fx.push('☠️ poison ticks 4');
    else if(e==='thornmail')fx.push('🌵 3 recoil');
    else if(e==='berserker')fx.push('🔥 berserker +50%');
    else if(e==='overcharge')fx.push('⚡ overcharge burn 5');
    else if(e.indexOf('storm:')===0)fx.push('⛈️ STORM -'+e.split(':')[1]+' both');
  });
  return {main:main,fx:fx,kill:false};
}

function renderFeed(b){
  var p=b.participants,e=b.battle_log,box=document.getElementById('log-feed');
  if(!e||e.length===0){box.innerHTML='<div class="empty">Waiting for the first bid…</div>';document.getElementById('latest').innerHTML='Battle is about to begin…';return}
  var rows=e.map(function(x){
    var c=commentary(x,p);
    var cls='evt'+(x.winner==null?' tie':'')+((x.events||[]).some(function(s){return s.indexOf('storm:')===0})?' storm':'');
    var fxHtml=c.fx.length?'<span class="fx">'+c.fx.map(esc).join(' · ')+'</span>':'';
    return '<div class="'+cls+'"><div class="t">#'+x.turn+'</div><div class="c">'+esc(c.main)+fxHtml+'</div></div>';
  });
  box.innerHTML=rows.join('');
  // latest moment banner
  var last=e[e.length-1],lc=commentary(last,p);
  var lcls='latest'+((last.events||[]).some(function(s){return s.indexOf('storm:')===0})?' storm':'');
  var lat=document.getElementById('latest');
  lat.className=lcls;
  if(b.status==='resolved'){
    var win=b.winner_id==null?'🤝 Draw!':('👑 '+shortId(b.winner_id)+' wins!');
    lat.innerHTML=win+' <span class="fx">'+esc(lc.main)+'</span>';
  }else{
    lat.innerHTML=esc(lc.main)+(lc.fx.length?' <span class="fx">'+lc.fx.map(esc).join(' · ')+'</span>':'');
  }
}

function render(b){renderMeta(b);renderCards(b);renderFeed(b)}
function refreshBattle(){fetch('/dashboard/data').then(function(r){return r.json()}).then(function(d){for(var i=0;i<d.battles.length;i++){if(d.battles[i].battle_id===battleId){render(d.battles[i]);return}}})}
if(typeof initialBattle!=='undefined'&&initialBattle)render(initialBattle);
setInterval(refreshBattle,3000)
"""


def create_app(arena=None, rate_limit=None):
    return App(arena or Arena(), rate_limit=rate_limit)


def run_server(host="127.0.0.1", port=8080):
    app = create_app()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self): self._handle()
        def do_POST(self): self._handle()

        def _handle(self):
            length = int(self.headers.get("content-length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            headers = {key.lower(): value for key, value in self.headers.items()}
            request = {"method": self.command, "path": urlparse(self.path).path, "headers": headers, "body": body, "client_ip": self.client_address[0]}
            status, response_headers, response_body = app.handle(request)
            encoded = response_body.encode("utf-8")
            self.send_response(status)
            for key, value in response_headers.items():
                self.send_header(key, value)
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format, *args):
            logger.debug("%s %s %s", self.client_address[0], format % args if args else format, self.headers.get("User-Agent", "-"))

    server = ThreadingHTTPServer((host, port), Handler)
    logger.info("Agent Battle arena listening on http://%s:%s", host, port)
    print(f"Agent Battle arena listening on http://{host}:{port}")
    server.serve_forever()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S", stream=sys.stdout)
    parser = argparse.ArgumentParser(description="Run the Agent Battle arena.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8080, type=int)
    args = parser.parse_args()
    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
