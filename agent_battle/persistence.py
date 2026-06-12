"""Optional SQLite persistence layer for Agent Battle.

When AGENT_BATTLE_DB_PATH is set, agent and battle state is written through to
SQLite so data survives server restarts.  When unset, the arena runs fully
in-memory (current behaviour).
"""

import json
import sqlite3
import threading


class Persistence:
    def __init__(self, db_path):
        self._db_path = db_path
        self._enabled = bool(db_path)
        self._conn = None
        self._lock = threading.Lock()
        if self._enabled:
            self._connect()
            self._migrate()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def load_agents(self):
        if not self._enabled:
            return {}
        with self._lock:
            rows = self._conn.execute("SELECT agent_id, data FROM agents").fetchall()
            return {row[0]: json.loads(row[1]) for row in rows}

    def save_agent(self, agent):
        if not self._enabled:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO agents(agent_id, data) VALUES(?, ?)",
                (agent["agent_id"], json.dumps(agent, sort_keys=True)),
            )
            self._conn.commit()

    def load_battles(self):
        if not self._enabled:
            return {}
        with self._lock:
            rows = self._conn.execute("SELECT battle_id, data FROM battles").fetchall()
            return {row[0]: json.loads(row[1]) for row in rows}

    def save_battle(self, battle):
        if not self._enabled:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO battles(battle_id, data) VALUES(?, ?)",
                (battle["battle_id"], json.dumps(battle, sort_keys=True)),
            )
            self._conn.commit()

    def delete_battle(self, battle_id):
        if not self._enabled:
            return
        with self._lock:
            self._conn.execute("DELETE FROM battles WHERE battle_id = ?", (battle_id,))
            self._conn.commit()

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _connect(self):
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")

    def _migrate(self):
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS agents (agent_id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS battles (battle_id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        self._conn.commit()
