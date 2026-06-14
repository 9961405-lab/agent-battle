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
            payload = {"error": error.message}
            if error.details:
                payload.update(error.details)
            return self._json(error.status, payload)
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
        return self._page("Agent Battle Arena", f"""
        <header class="topbar">
          <div class="brand"><div class="logo">⚔️</div><div><h1>AGENT BATTLE</h1><p>Live arena &middot; auto-updating</p></div></div>
          <button class="button" onclick="refresh()">↻ Refresh</button>
        </header>
        <main>
          <section class="stats" id="stats"></section>
          <h2>Matches</h2>
          <section class="matches" id="matches"></section>
        </main>
        """, body_extra=f"""<script>var initialBattles={json.dumps(battles)};</script><script>{_DASHBOARD_JS}</script>""")

    def _battle_html(self, battle_id):
        battle = self.arena.get_public_battle(battle_id)
        participants = battle["participants"]
        return self._page(f"Battle {battle_id}", f"""
        <header class="topbar">
          <div class="brand"><div class="logo">⚔️</div><div><a class="back" href="/dashboard">&larr; All matches</a><h1>{self._short_id(participants[0] if participants else '')} VS {self._participant_label(participants, 1)}</h1></div></div>
          <button class="button" onclick="location.reload()">↻ Refresh</button>
        </header>
        <main>
          <section class="meta" id="meta"></section>
          <div class="victory" id="victory"></div>
          <div class="storm-banner" id="storm" style="display:none"></div>
          <div class="latest" id="latest"></div>
          <section class="arena" id="arena"></section>
          <section class="table-wrap"><h2>Play-by-play</h2>
            <div class="feed" id="feed"></div>
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
:root{{color-scheme:dark;--bg:#0a0e16;--panel:#141c2c;--panel2:#1a2335;--line:#26314a;--text:#eef2fb;--muted:#8593b0;--p1:#22d3ee;--p2:#f472b6;--gold:#fbbf24;--storm:#a855f7;--danger:#ef4444}}
*{{box-sizing:border-box}}
body{{margin:0;min-width:340px;background:radial-gradient(1200px 600px at 50% -10%,rgba(168,85,247,.10),transparent 60%),radial-gradient(900px 500px at 0% 0%,rgba(34,211,238,.07),transparent 55%),var(--bg);color:var(--text);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}}
a{{color:var(--p1);text-decoration:none}}a:hover{{opacity:.85}}
.topbar{{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:16px 28px;border-bottom:1px solid var(--line);background:linear-gradient(180deg,rgba(20,28,44,.92),rgba(10,14,22,.5));position:sticky;top:0;z-index:20;backdrop-filter:blur(8px)}}
.topbar h1{{margin:0;font-size:19px;font-weight:900;letter-spacing:1px}}
.brand{{display:flex;align-items:center;gap:12px}}
.logo{{width:34px;height:34px;border-radius:9px;background:linear-gradient(135deg,var(--p1),var(--storm));display:grid;place-items:center;font-size:18px;box-shadow:0 0 18px -4px var(--storm)}}
.topbar p,.back{{margin:0;color:var(--muted);font-size:12px}}
.back{{display:inline-block;margin-bottom:5px;font-weight:700}}
h2{{margin:0 0 14px;font-size:12px;font-weight:900;letter-spacing:2px;text-transform:uppercase;color:var(--muted)}}
main{{padding:26px 28px 60px;max-width:1080px;margin:0 auto}}
.button{{display:inline-flex;align-items:center;gap:6px;min-height:38px;padding:0 16px;border:1px solid var(--line);border-radius:10px;background:var(--panel2);color:var(--text);font-weight:800;font-size:13px;cursor:pointer;transition:.15s}}
.button:hover{{border-color:var(--p1);box-shadow:0 0 0 1px var(--p1)}}
.badge{{display:inline-flex;align-items:center;height:22px;padding:0 11px;border-radius:999px;font-size:11px;font-weight:900;letter-spacing:.5px;text-transform:uppercase}}
.badge.active{{background:rgba(52,211,153,.16);color:#34d399}}.badge.created{{background:rgba(251,191,36,.16);color:var(--gold)}}.badge.resolved{{background:rgba(133,147,176,.16);color:var(--muted)}}
.empty{{color:var(--muted);text-align:center;padding:34px}}
.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:26px}}
.stats div{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}}
.stats strong{{display:block;font-size:30px;font-weight:900;line-height:1}}
.stats span{{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:1px}}
.stats .s-active strong{{color:#34d399}}.stats .s-wait strong{{color:var(--gold)}}
.matches{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}}
.match{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;transition:.15s}}
.match:hover{{transform:translateY(-2px);border-color:var(--p1)}}
.vs-row{{display:flex;align-items:center;gap:10px}}
.who{{flex:1;min-width:0}}.who .nm{{font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.who.a .nm{{color:var(--p1)}}.who.b{{text-align:right}}.who.b .nm{{color:var(--p2)}}
.vs{{font-size:11px;font-weight:900;color:var(--muted)}}
.foot{{display:flex;justify-content:space-between;align-items:center;margin-top:14px;font-size:12px;color:var(--muted)}}
.avatar{{width:34px;height:34px;border-radius:9px;display:grid;place-items:center;font-size:18px;flex:0 0 auto}}
.avatar.a{{background:rgba(34,211,238,.14);border:1px solid var(--p1)}}.avatar.b{{background:rgba(244,114,182,.14);border:1px solid var(--p2)}}
.meta{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}}
.meta div{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px}}
.meta span{{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:1px;margin-bottom:5px}}
.meta strong{{font-size:18px;font-weight:900}}
.arena{{display:grid;grid-template-columns:1fr 64px 1fr;gap:14px;margin-bottom:18px}}
.fighter{{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:18px;transition:.3s}}
.fighter.a{{border-top:3px solid var(--p1)}}.fighter.b{{border-top:3px solid var(--p2)}}
.fighter.a.lead{{box-shadow:0 0 0 1px var(--p1),0 0 26px -8px var(--p1)}}.fighter.b.lead{{box-shadow:0 0 0 1px var(--p2),0 0 26px -8px var(--p2)}}
.fighter.dead{{opacity:.4;filter:grayscale(.6)}}
.fighter .top{{display:flex;align-items:center;gap:12px;margin-bottom:14px}}
.big-av{{width:48px;height:48px;border-radius:12px;display:grid;place-items:center;font-size:26px;flex:0 0 auto}}
.fighter.a .big-av{{background:rgba(34,211,238,.14);border:1px solid var(--p1)}}.fighter.b .big-av{{background:rgba(244,114,182,.14);border:1px solid var(--p2)}}
.fighter .nm{{font-size:17px;font-weight:900;overflow-wrap:anywhere}}
.crown{{font-size:12px;font-weight:900;color:var(--gold);letter-spacing:1px;margin-top:2px}}
.gauge{{margin-top:12px}}
.gauge .lab{{display:flex;justify-content:space-between;font-size:11px;font-weight:800;color:var(--muted);margin-bottom:5px;letter-spacing:.5px}}
.gauge .lab b{{color:var(--text);font-variant-numeric:tabular-nums}}
.track{{position:relative;height:18px;border-radius:9px;background:#080c14;border:1px solid var(--line);overflow:hidden}}
.track>.ghost{{position:absolute;inset:0;width:100%;background:rgba(255,255,255,.4);transition:width .7s ease .15s}}
.track>.fill{{position:absolute;inset:0;width:100%;transition:width .25s ease;background-image:repeating-linear-gradient(90deg,rgba(0,0,0,.18) 0 2px,transparent 2px 15px)}}
.track.hp>.fill{{background-color:#22c55e}}.track.hp.mid>.fill{{background-color:#f59e0b}}.track.hp.low>.fill{{background-color:var(--danger);animation:pulse 1s infinite}}
.track.mp>.fill{{background-color:#38bdf8;box-shadow:0 0 12px rgba(56,189,248,.5)}}
@keyframes pulse{{50%{{opacity:.5}}}}
.loadout{{display:flex;flex-wrap:wrap;gap:6px;margin-top:14px}}
.skill{{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:800;padding:4px 9px;border-radius:8px;background:var(--panel2);border:1px solid var(--line)}}
.center{{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px}}
.vs-badge{{font-size:22px;font-weight:900;color:var(--muted);text-shadow:0 0 18px rgba(168,85,247,.6)}}
.storm-col{{writing-mode:vertical-rl;font-weight:900;font-size:10px;letter-spacing:3px;color:var(--storm);opacity:.55}}
.storm-banner{{display:flex;align-items:center;gap:12px;background:linear-gradient(90deg,rgba(168,85,247,.16),rgba(239,68,68,.10));border:1px solid rgba(168,85,247,.4);border-radius:12px;padding:12px 16px;margin-bottom:18px;font-weight:800}}
.storm-banner.hot{{animation:stormpulse 1.2s infinite}}
@keyframes stormpulse{{50%{{border-color:var(--danger);box-shadow:0 0 24px -4px rgba(239,68,68,.55)}}}}
.storm-banner .lvl{{margin-left:auto;color:var(--danger);font-weight:900;font-size:16px}}
.dots{{display:flex;gap:5px}}.dots i{{width:9px;height:9px;border-radius:50%;background:#26314a;display:block}}
.dots i.on{{background:var(--storm);box-shadow:0 0 8px var(--storm)}}
.latest{{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--p1);border-radius:12px;padding:14px 16px;margin-bottom:18px;font-size:15px;font-weight:700;min-height:22px}}
.latest.storm{{border-left-color:var(--storm)}}
.latest.win{{border-left-color:var(--gold);background:linear-gradient(90deg,rgba(251,191,36,.12),transparent)}}
.victory{{display:none;align-items:center;gap:18px;background:linear-gradient(90deg,rgba(251,191,36,.16),rgba(20,28,44,.3));border:1px solid var(--gold);border-radius:16px;padding:18px 24px;margin-bottom:18px}}
.victory.show{{display:flex;animation:flashin .5s ease}}
.victory .ko{{font-size:34px;font-weight:900;letter-spacing:3px;color:var(--gold);text-shadow:0 0 26px rgba(251,191,36,.5)}}
.victory .sub{{font-weight:800}}
.table-wrap{{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:18px}}
.feed{{display:flex;flex-direction:column;gap:8px;max-height:540px;overflow-y:auto}}
.evt{{display:flex;gap:12px;align-items:baseline;padding:10px 14px;border:1px solid var(--line);border-left:3px solid var(--line);border-radius:10px;background:var(--panel2)}}
.evt .t{{flex:0 0 34px;color:var(--muted);font-size:12px;font-weight:900;font-variant-numeric:tabular-nums}}
.evt .c{{flex:1;font-size:13.5px}}
.evt.a{{border-left-color:var(--p1)}}.evt.b{{border-left-color:var(--p2)}}.evt.tie{{opacity:.65}}.evt.storm{{border-left-color:var(--storm)}}
.evt.new{{animation:flashin .5s ease}}
@keyframes flashin{{from{{transform:translateY(-5px);opacity:0}}}}
.fx{{font-size:12px;color:var(--muted);margin-left:8px}}
@media(max-width:760px){{main{{padding:18px 14px 50px}}.stats,.meta{{grid-template-columns:repeat(2,1fr)}}.arena{{grid-template-columns:1fr;gap:10px}}.center{{flex-direction:row}}.storm-col{{writing-mode:horizontal-tb}}}}
</style>{head_extra}</head><body>{body}{body_extra}</body></html>"""


_DASHBOARD_JS = r"""
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function shortId(v){var s=esc(v);var i=s.indexOf('_');return i===-1?s.slice(0,14):s.slice(0,i+1)+s.slice(i+1,i+9)}
function badge(s){return '<span class="badge '+esc(s)+'">'+esc(s)+'</span>'}
function winnerText(b){if(b.status!=='resolved')return'-';if(b.winner_id==null)return'Draw';return shortId(b.winner_id)}
var SKILL_ICON={vampire:'🧛',berserker:'🔥',focused:'🎯',thornmail:'🌵',meditate:'🧘',poison:'☠️',guard:'🛡️',overcharge:'⚡'};
function avFor(b,aid){var sk=(b.skills&&b.skills[aid])||[];return sk.length?(SKILL_ICON[sk[0]]||'⚔️'):'⚔️'}
function miniHp(b,aid){if(!b.states||!b.states[aid])return '';var s=b.states[aid],mx=b.max_hp||100;var p=Math.max(0,Math.min(100,s.hp/mx*100));var c=p<=25?'low':(p<=50?'mid':'');return '<div class="track hp '+c+'" style="height:8px;margin-top:8px"><div class="fill" style="width:'+p+'%"></div></div>'}
function card(b){
  var p=b.participants,bid=esc(b.battle_id);
  var aN=p[0]?shortId(p[0]):'—',bN=p[1]?shortId(p[1]):'Waiting…';
  var avA='<span class="avatar a">'+(p[0]?avFor(b,p[0]):'⚔️')+'</span>';
  var avB=p[1]?'<span class="avatar b">'+avFor(b,p[1])+'</span>':'<span class="avatar b">⏳</span>';
  var foot=b.status==='resolved'?('🏆 '+winnerText(b)):('Turn '+b.turn+(b.turn>=(b.storm_start||10)?' ⛈️':''));
  return '<a class="match" href="/dashboard/battles/'+bid+'">'+
    '<div class="vs-row">'+avA+'<div class="who a"><div class="nm">'+aN+'</div></div>'+
    '<div class="vs">VS</div>'+
    '<div class="who b"><div class="nm">'+bN+'</div></div>'+avB+'</div>'+
    miniHp(b,p[0])+(p[1]?miniHp(b,p[1]):'')+
    '<div class="foot">'+badge(b.status)+'<span>'+foot+'</span></div></a>';
}
function renderList(battles){
  battles=battles.slice().sort(function(x,y){var o={active:0,created:1,resolved:2};return (o[x.status]-o[y.status])||(y.turn-x.turn)});
  var a=battles.filter(function(b){return b.status==='active'}).length,
      c=battles.filter(function(b){return b.status==='created'}).length,
      r=battles.filter(function(b){return b.status==='resolved'}).length;
  document.getElementById('stats').innerHTML=
    '<div><strong>'+battles.length+'</strong><span>Total</span></div>'+
    '<div class="s-active"><strong>'+a+'</strong><span>⚔️ Fighting</span></div>'+
    '<div class="s-wait"><strong>'+c+'</strong><span>⏳ Waiting</span></div>'+
    '<div><strong>'+r+'</strong><span>✓ Finished</span></div>';
  var box=document.getElementById('matches');
  box.innerHTML=battles.length===0?'<div class="empty">No battles yet — point an agent at the arena to start one.</div>':battles.map(card).join('');
}
function refresh(){fetch('/dashboard/data').then(function(r){return r.json()}).then(function(d){renderList(d.battles)})}
renderList(typeof initialBattles!=='undefined'?initialBattles:[]);setInterval(refresh,4000)
"""

_BATTLE_JS = r"""
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function shortId(v){var s=esc(v);var i=s.indexOf('_');return i===-1?s.slice(0,14):s.slice(0,i+1)+s.slice(i+1,i+9)}
function badge(s){return '<span class="badge '+esc(s)+'">'+esc(s)+'</span>'}
function pct(v,max){return Math.max(0,Math.min(100,v/(max||100)*100))}
function hpCls(p){return p<=25?'low':(p<=50?'mid':'')}
var SKILL_ICON={vampire:'🧛',berserker:'🔥',focused:'🎯',thornmail:'🌵',meditate:'🧘',poison:'☠️',guard:'🛡️',overcharge:'⚡'};
function avEmoji(b,aid){var sk=(b.skills&&b.skills[aid])||[];return sk.length?(SKILL_ICON[sk[0]]||'⚔️'):'⚔️'}
function stormLevel(b){return Math.max(0,(b.turn||0)-(b.storm_start||10)+1)}

var built=false,builtKey='',prevHp={},prevFeed=0;

function fighterHtml(b,aid,i,cls){
  var sk=(b.skills&&b.skills[aid])||[];
  var load=sk.map(function(k){return '<span class="skill">'+(SKILL_ICON[k]||'')+' '+esc(k)+'</span>'}).join('');
  return '<div class="fighter '+cls+'" id="ftr'+i+'">'+
    '<div class="top"><div class="big-av">'+avEmoji(b,aid)+'</div><div><div class="nm">'+shortId(aid)+'</div><div class="crown" id="crown'+i+'"></div></div></div>'+
    '<div class="gauge"><div class="lab"><span>HP</span><b id="hptxt'+i+'">–</b></div><div class="track hp" id="hptrk'+i+'"><div class="ghost" id="hpgh'+i+'"></div><div class="fill" id="hpfl'+i+'"></div></div></div>'+
    '<div class="gauge"><div class="lab"><span>MP</span><b id="mptxt'+i+'">–</b></div><div class="track mp"><div class="fill" id="mpfl'+i+'"></div></div></div>'+
    '<div class="loadout">'+load+'</div></div>';
}
function buildArena(b){
  var p=b.participants;
  var a=fighterHtml(b,p[0],0,'a');
  var center='<div class="center"><div class="vs-badge">VS</div><div class="storm-col">STORM</div></div>';
  var bb=p.length>1?fighterHtml(b,p[1],1,'b'):'<div class="fighter b"><div class="top"><div class="big-av">⏳</div><div><div class="nm">Waiting…</div></div></div></div>';
  document.getElementById('arena').innerHTML=a+center+bb;
}
function setHp(i,aid,b){
  var mx=b.max_hp||100,s=b.states[aid],np=pct(s.hp,mx);
  var fl=document.getElementById('hpfl'+i),gh=document.getElementById('hpgh'+i),trk=document.getElementById('hptrk'+i),tx=document.getElementById('hptxt'+i);
  if(!fl)return;
  trk.className='track hp '+hpCls(np);fl.style.width=np+'%';tx.textContent=s.hp+' / '+mx;
  var prev=prevHp[aid];
  if(prev==null||s.hp>=prev){gh.style.transition='none';gh.style.width=np+'%';}
  else{var op=pct(prev,mx);gh.style.transition='none';gh.style.width=op+'%';requestAnimationFrame(function(){gh.style.transition='width .7s ease .15s';gh.style.width=np+'%';});}
  prevHp[aid]=s.hp;
}
function setMp(i,aid,b){var s=b.states[aid],fl=document.getElementById('mpfl'+i),tx=document.getElementById('mptxt'+i);if(!fl)return;fl.style.width=pct(s.mp,b.max_mp||100)+'%';tx.textContent=s.mp+' / '+(b.max_mp||100);}

function commentary(x,p){
  var bidA=x.bids[p[0]],bidB=x.bids[p[1]],fx=[],main,ev=x.events||[];
  if(x.winner==null){main='🤝 Both commit '+bidA+' vs '+bidB+' — stalemate, MP restored';}
  else{var wn=shortId(x.winner),wbid=x.bids[x.winner],lbid=(x.winner===p[0]?bidB:bidA);main=(wbid>=25?'🚀 ':'💥 ')+wn+' bids '+wbid+' over '+lbid+' → '+x.damage+' dmg';}
  ev.forEach(function(e){
    if(e==='guard')fx.push('🛡️ shield absorbs it');
    else if(e==='vampire')fx.push('🧛 lifesteal');
    else if(e==='poison_applied')fx.push('☠️ poisoned');
    else if(e==='poison')fx.push('☠️ poison ticks 4');
    else if(e==='thornmail')fx.push('🌵 3 recoil');
    else if(e==='berserker')fx.push('🔥 berserker +50%');
    else if(e==='overcharge')fx.push('⚡ overcharge burn 5');
    else if(e.indexOf('storm:')===0)fx.push('⛈️ STORM −'+e.split(':')[1]);
  });
  return {main:main,fx:fx};
}
function isStormy(x){return (x.events||[]).some(function(s){return s.indexOf('storm:')===0})}

function renderMeta(b){
  var win=b.status==='resolved'?(b.winner_id==null?'Draw':shortId(b.winner_id)):'—';
  document.getElementById('meta').innerHTML=
    '<div><span>Status</span><strong>'+badge(b.status)+'</strong></div>'+
    '<div><span>Turn</span><strong>'+b.turn+' / '+(b.max_turns||30)+'</strong></div>'+
    '<div><span>Pot</span><strong>'+(b.pot||b.stake*2)+'</strong></div>'+
    '<div><span>Winner</span><strong>'+esc(win)+'</strong></div>';
}
function renderStorm(b){
  var sb=document.getElementById('storm'),lvl=stormLevel(b);
  if(b.status==='resolved'){sb.style.display='none';return;}
  sb.style.display='flex';
  if(lvl>0){
    var dots='';for(var i=0;i<8;i++)dots+='<i class="'+(i<lvl?'on':'')+'"></i>';
    sb.className='storm-banner'+(lvl>=4?' hot':'');
    sb.innerHTML='<span>⛈️ ARENA STORM</span><div class="dots">'+dots+'</div><span class="lvl">−'+lvl+' HP / turn</span>';
  }else{
    sb.className='storm-banner';
    sb.innerHTML='<span>⛈️ Storm closes in at turn '+(b.storm_start||10)+'</span><span class="lvl" style="color:var(--muted)">turn '+b.turn+'</span>';
  }
}
function renderVictory(b){
  var v=document.getElementById('victory');
  if(b.status!=='resolved'){v.className='victory';return;}
  v.className='victory show';
  v.innerHTML=b.winner_id==null?'<div class="ko">DRAW</div><div class="sub">Both fighters fell.</div>':'<div class="ko">K.O.</div><div class="sub">👑 '+shortId(b.winner_id)+' takes the pot of '+(b.pot||b.stake*2)+'</div>';
}
function renderLatest(b){
  var e=b.battle_log||[],lat=document.getElementById('latest');
  if(!e.length){lat.className='latest';lat.textContent='⚔️ Battle is about to begin…';return;}
  var last=e[e.length-1],lc=commentary(last,b.participants);
  if(b.status==='resolved'){lat.className='latest win';lat.innerHTML=(b.winner_id==null?'🤝 Draw!':'👑 '+shortId(b.winner_id)+' wins!')+' <span class="fx">'+esc(lc.main)+'</span>';}
  else{lat.className='latest'+(isStormy(last)?' storm':'');lat.innerHTML='📣 '+esc(lc.main)+(lc.fx.length?' <span class="fx">'+lc.fx.map(esc).join(' · ')+'</span>':'');}
}
function renderFeed(b){
  var p=b.participants,e=b.battle_log||[],box=document.getElementById('feed');
  if(!e.length){box.innerHTML='<div class="empty">Waiting for the first bid…</div>';prevFeed=0;return;}
  box.innerHTML=e.map(function(x,idx){
    var c=commentary(x,p),who=x.winner==null?'tie':(x.winner===p[0]?'a':'b');
    var cls='evt '+who+(isStormy(x)?' storm':'')+(idx===e.length-1&&e.length>prevFeed?' new':'');
    var fx=c.fx.length?'<span class="fx">'+c.fx.map(esc).join(' · ')+'</span>':'';
    return '<div class="'+cls+'"><div class="t">'+x.turn+'</div><div class="c">'+esc(c.main)+fx+'</div></div>';
  }).join('');
  box.scrollTop=box.scrollHeight;prevFeed=e.length;
}

function render(b){
  var key=(b.participants||[]).join(',');
  if(!built||key!==builtKey){buildArena(b);built=true;builtKey=key;prevHp={};}
  var p=b.participants;
  p.forEach(function(aid,i){setHp(i,aid,b);setMp(i,aid,b);});
  if(p.length>1){
    var hA=b.states[p[0]].hp,hB=b.states[p[1]].hp;
    var fA=document.getElementById('ftr0'),fB=document.getElementById('ftr1');
    if(fA)fA.className='fighter a'+(hA>hB?' lead':'')+(hA<=0?' dead':'');
    if(fB)fB.className='fighter b'+(hB>hA?' lead':'')+(hB<=0?' dead':'');
    var c0=document.getElementById('crown0'),c1=document.getElementById('crown1');
    if(c0)c0.textContent=b.winner_id===p[0]?'👑 WINNER':'';
    if(c1)c1.textContent=b.winner_id===p[1]?'👑 WINNER':'';
  }
  renderMeta(b);renderStorm(b);renderVictory(b);renderLatest(b);renderFeed(b);
}
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
            ua = self.headers.get("User-Agent", "-") if hasattr(self, "headers") and self.headers else "-"
            logger.debug("%s %s %s", self.client_address[0], format % args if args else format, ua)

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
