"""
Server data layer — supports SQLite (local dev) and OCI MySQL (production).

Backend selection:
  - DATABASE_URL starts with "mysql" -> MySQL via pymysql
  - DATABASE_URL unset               -> SQLite at ~/.forgemem/server.db
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse

DATABASE_URL: str = os.environ.get("DATABASE_URL", "")
DB_PATH = Path(os.environ.get("FORGEMEM_SERVER_DB", str(Path.home() / ".forgemem" / "server.db")))

_SCHEMA_SQLITE = [
    """CREATE TABLE IF NOT EXISTS users (
        id          TEXT PRIMARY KEY,
        email       TEXT UNIQUE NOT NULL,
        balance_usd REAL NOT NULL DEFAULT 5.0,
        created_at  INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS magic_link_tokens (
        token       TEXT PRIMARY KEY,
        email       TEXT NOT NULL,
        callback    TEXT NOT NULL,
        state       TEXT NOT NULL,
        created_at  INTEGER NOT NULL,
        expires_at  INTEGER NOT NULL,
        used        INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS usage_runs (
        run_id      TEXT PRIMARY KEY,
        user_id     TEXT NOT NULL,
        cost_usd    REAL NOT NULL,
        model       TEXT NOT NULL,
        balance_usd REAL NOT NULL,
        ts          INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS stripe_events (
        event_id     TEXT PRIMARY KEY,
        processed_at INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS devices (
        id          TEXT PRIMARY KEY,
        user_id     TEXT NOT NULL,
        name        TEXT NOT NULL DEFAULT '',
        last_sync   INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sync_traces (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     TEXT NOT NULL,
        device_id   TEXT NOT NULL,
        local_id    TEXT NOT NULL,
        ts          INTEGER,
        session_id  TEXT,
        project_tag TEXT,
        type        TEXT NOT NULL DEFAULT 'note',
        content     TEXT NOT NULL,
        distilled   INTEGER NOT NULL DEFAULT 0,
        synced_at   INTEGER NOT NULL,
        UNIQUE(user_id, device_id, local_id)
    )""",
    """CREATE TABLE IF NOT EXISTS sync_principles (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         TEXT NOT NULL,
        device_id       TEXT NOT NULL,
        local_id        TEXT NOT NULL,
        source_local_id TEXT,
        project_tag     TEXT,
        type            TEXT,
        principle       TEXT NOT NULL,
        impact_score    INTEGER NOT NULL DEFAULT 5,
        tags            TEXT,
        synced_at       INTEGER NOT NULL,
        UNIQUE(user_id, device_id, local_id)
    )""",
    """CREATE TABLE IF NOT EXISTS sessions (
        token       TEXT PRIMARY KEY,
        user_id     TEXT NOT NULL,
        created_at  INTEGER NOT NULL,
        expires_at  INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_usage_runs_user_ts ON usage_runs (user_id, ts)",
    "CREATE INDEX IF NOT EXISTS idx_sync_traces_user ON sync_traces (user_id)",
]

_SCHEMA_MYSQL = [
    """CREATE TABLE IF NOT EXISTS users (
        id          VARCHAR(64) PRIMARY KEY,
        email       VARCHAR(255) UNIQUE NOT NULL,
        balance_usd DOUBLE NOT NULL DEFAULT 5.0,
        created_at  BIGINT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS magic_link_tokens (
        token       VARCHAR(128) PRIMARY KEY,
        email       VARCHAR(255) NOT NULL,
        callback    TEXT NOT NULL,
        state       VARCHAR(128) NOT NULL,
        created_at  BIGINT NOT NULL,
        expires_at  BIGINT NOT NULL,
        used        TINYINT NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS usage_runs (
        run_id      VARCHAR(64) PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        cost_usd    DOUBLE NOT NULL,
        model       VARCHAR(128) NOT NULL,
        balance_usd DOUBLE NOT NULL,
        ts          BIGINT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS stripe_events (
        event_id     VARCHAR(128) PRIMARY KEY,
        processed_at BIGINT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS devices (
        id          VARCHAR(128) PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        name        VARCHAR(255) NOT NULL DEFAULT '',
        last_sync   BIGINT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sync_traces (
        id          BIGINT AUTO_INCREMENT PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        device_id   VARCHAR(128) NOT NULL,
        local_id    VARCHAR(128) NOT NULL,
        ts          BIGINT,
        session_id  VARCHAR(128),
        project_tag VARCHAR(255),
        type        VARCHAR(32) NOT NULL DEFAULT 'note',
        content     MEDIUMTEXT NOT NULL,
        distilled   TINYINT NOT NULL DEFAULT 0,
        synced_at   BIGINT NOT NULL,
        UNIQUE KEY uq_trace (user_id, device_id, local_id)
    )""",
    """CREATE TABLE IF NOT EXISTS sync_principles (
        id              BIGINT AUTO_INCREMENT PRIMARY KEY,
        user_id         VARCHAR(64) NOT NULL,
        device_id       VARCHAR(128) NOT NULL,
        local_id        VARCHAR(128) NOT NULL,
        source_local_id VARCHAR(128),
        project_tag     VARCHAR(255),
        type            VARCHAR(64),
        principle       MEDIUMTEXT NOT NULL,
        impact_score    INT NOT NULL DEFAULT 5,
        tags            TEXT,
        synced_at       BIGINT NOT NULL,
        UNIQUE KEY uq_principle (user_id, device_id, local_id)
    )""",
    """CREATE TABLE IF NOT EXISTS sessions (
        token       VARCHAR(512) PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        created_at  BIGINT NOT NULL,
        expires_at  BIGINT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_usage_runs_user_ts ON usage_runs (user_id, ts)",
    "CREATE INDEX IF NOT EXISTS idx_sync_traces_user ON sync_traces (user_id)",
]


class Database:
    def __init__(self, path: Path = DB_PATH, url: str = DATABASE_URL):
        self.path = path
        self.url = url
        self._mysql = url.startswith("mysql")

    def _conn(self):
        if self._mysql:
            import pymysql
            import pymysql.cursors
            parsed = urlparse(self.url)
            return pymysql.connect(
                host=parsed.hostname or "127.0.0.1",
                port=parsed.port or 3306,
                user=parsed.username or "forgemem",
                password=parsed.password or "",
                database=(parsed.path or "/forgemem").lstrip("/"),
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=False,
                connect_timeout=10,
            )
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _exec(self, conn, sql: str, params: tuple = ()):
        if self._mysql:
            cur = conn.cursor()
            cur.execute(sql, params)
            return cur
        return conn.execute(sql, params)

    def _fetchone(self, conn, sql: str, params: tuple = ()) -> dict | None:
        cur = self._exec(conn, sql, params)
        row = cur.fetchone()
        return dict(row) if row else None

    def _fetchall(self, conn, sql: str, params: tuple = ()) -> list[dict]:
        cur = self._exec(conn, sql, params)
        return [dict(r) for r in (cur.fetchall() or [])]

    def _q(self, sql: str) -> str:
        return sql.replace("?", "%s") if self._mysql else sql

    def init(self) -> None:
        schema = _SCHEMA_MYSQL if self._mysql else _SCHEMA_SQLITE
        if not self._mysql:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            for stmt in schema:
                if self._mysql and stmt.lstrip().upper().startswith("CREATE INDEX"):
                    try:
                        self._exec(conn, stmt)
                    except Exception as e:
                        if "Duplicate key name" in str(e):
                            pass
                        else:
                            raise
                else:
                    self._exec(conn, stmt)
            conn.commit()

    # ---- Users ----

    def create_user(self, email: str, initial_balance: float = 5.0) -> str:
        uid = secrets.token_hex(16)
        now = int(time.time())
        with self._conn() as conn:
            self._exec(
                conn,
                self._q("INSERT INTO users (id, email, balance_usd, created_at) VALUES (?,?,?,?)"),
                (uid, email, initial_balance, now),
            )
            conn.commit()
        return uid

    def get_user_by_email(self, email: str) -> dict | None:
        with self._conn() as conn:
            return self._fetchone(conn, self._q("SELECT * FROM users WHERE email=?"), (email,))

    def get_user_by_id(self, user_id: str) -> dict | None:
        with self._conn() as conn:
            return self._fetchone(conn, self._q("SELECT * FROM users WHERE id=?"), (user_id,))

    def deduct_credits(self, user_id: str, amount: float) -> float:
        with self._conn() as conn:
            row = self._fetchone(conn, self._q("SELECT balance_usd FROM users WHERE id=?"), (user_id,))
            if not row:
                raise ValueError(f"User {user_id} not found")
            new_balance = round(row["balance_usd"] - amount, 6)
            if new_balance < 0:
                raise ValueError(f"Insufficient credits (balance: ${row['balance_usd']:.4f})")
            self._exec(conn, self._q("UPDATE users SET balance_usd=? WHERE id=?"), (new_balance, user_id))
            conn.commit()
        return new_balance

    def top_up_credits(self, user_id: str, amount: float) -> float:
        with self._conn() as conn:
            self._exec(
                conn,
                self._q("UPDATE users SET balance_usd = balance_usd + ? WHERE id=?"),
                (amount, user_id),
            )
            row = self._fetchone(conn, self._q("SELECT balance_usd FROM users WHERE id=?"), (user_id,))
            conn.commit()
        if not row:
            raise ValueError(f"User {user_id} not found")
        return round(row["balance_usd"], 6)

    # ---- Usage ----

    def log_run(self, user_id: str, run_id: str, cost_usd: float, model: str, balance_usd: float) -> None:
        sql = self._q(
            "INSERT INTO usage_runs (run_id, user_id, cost_usd, model, balance_usd, ts) "
            "VALUES (?,?,?,?,?,?)"
        )
        with self._conn() as conn:
            self._exec(conn, sql, (run_id, user_id, cost_usd, model, balance_usd, int(time.time())))
            conn.commit()

    def run_count_in_window(self, user_id: str, window_seconds: int = 3600) -> int:
        cutoff = int(time.time()) - window_seconds
        with self._conn() as conn:
            cur = self._exec(
                conn,
                self._q("SELECT COUNT(*) as cnt FROM usage_runs WHERE user_id=? AND ts > ?"),
                (user_id, cutoff),
            )
            row = cur.fetchone()
        return (dict(row).get("cnt") or 0) if row else 0

    # ---- Magic link tokens ----

    def create_magic_link_token(self, token: str, email: str, callback: str, state: str, ttl: int = 600) -> None:
        now = int(time.time())
        sql = self._q(
            "INSERT INTO magic_link_tokens "
            "(token, email, callback, state, created_at, expires_at) VALUES (?,?,?,?,?,?)"
        )
        with self._conn() as conn:
            self._exec(conn, sql, (token, email, callback, state, now, now + ttl))
            conn.commit()

    def consume_magic_link_token(self, token: str) -> dict | None:
        now = int(time.time())
        with self._conn() as conn:
            row = self._fetchone(
                conn,
                self._q("SELECT * FROM magic_link_tokens WHERE token=? AND expires_at>? AND used=0"),
                (token, now),
            )
            if row:
                self._exec(conn, self._q("UPDATE magic_link_tokens SET used=1 WHERE token=?"), (token,))
                conn.commit()
        return row

    # ---- Sessions ----

    def create_session(self, token: str, user_id: str, ttl_seconds: int = 30 * 86400) -> None:
        now = int(time.time())
        sql = self._q("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)")
        with self._conn() as conn:
            self._exec(conn, sql, (token, user_id, now, now + ttl_seconds))
            conn.commit()

    def get_user_by_session(self, token: str) -> dict | None:
        now = int(time.time())
        with self._conn() as conn:
            row = self._fetchone(
                conn,
                self._q("SELECT user_id FROM sessions WHERE token=? AND expires_at>?"),
                (token, now),
            )
            if not row:
                return None
            return self._fetchone(conn, self._q("SELECT * FROM users WHERE id=?"), (row["user_id"],))


    # ---- Stripe ----

    def stripe_event_seen(self, event_id: str) -> bool:
        with self._conn() as conn:
            row = self._fetchone(
                conn,
                self._q("SELECT 1 as found FROM stripe_events WHERE event_id=?"),
                (event_id,),
            )
            if row:
                return True
            self._exec(
                conn,
                self._q("INSERT INTO stripe_events (event_id, processed_at) VALUES (?,?)"),
                (event_id, int(time.time())),
            )
            conn.commit()
        return False

    # ---- Sync ----

    def upsert_device(self, user_id: str, device_id: str, name: str) -> None:
        now = int(time.time())
        if self._mysql:
            sql = (
                "INSERT INTO devices (id, user_id, name, last_sync) VALUES (%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE name=VALUES(name), last_sync=VALUES(last_sync)"
            )
        else:
            sql = "INSERT OR REPLACE INTO devices (id, user_id, name, last_sync) VALUES (?,?,?,?)"
        with self._conn() as conn:
            self._exec(conn, sql, (device_id, user_id, name, now))
            conn.commit()

    def upsert_trace(self, user_id: str, device_id: str, t: dict) -> None:
        now = int(time.time())
        if self._mysql:
            sql = (
                "INSERT INTO sync_traces "
                "(user_id,device_id,local_id,ts,session_id,project_tag,type,content,distilled,synced_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE "
                "ts=VALUES(ts),content=VALUES(content),distilled=VALUES(distilled),synced_at=VALUES(synced_at)"
            )
        else:
            sql = (
                "INSERT OR REPLACE INTO sync_traces "
                "(user_id,device_id,local_id,ts,session_id,project_tag,type,content,distilled,synced_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)"
            )
        with self._conn() as conn:
            self._exec(conn, sql, (
                user_id, device_id, t["local_id"],
                t.get("ts"), t.get("session_id"), t.get("project_tag"),
                t.get("type", "note"), t["content"],
                int(bool(t.get("distilled", False))), now,
            ))
            conn.commit()

    def upsert_principle(self, user_id: str, device_id: str, p: dict) -> None:
        now = int(time.time())
        if self._mysql:
            sql = (
                "INSERT INTO sync_principles "
                "(user_id,device_id,local_id,source_local_id,project_tag,type,principle,impact_score,tags,synced_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE "
                "principle=VALUES(principle),impact_score=VALUES(impact_score),"
                "tags=VALUES(tags),synced_at=VALUES(synced_at)"
            )
        else:
            sql = (
                "INSERT OR REPLACE INTO sync_principles "
                "(user_id,device_id,local_id,source_local_id,project_tag,type,principle,impact_score,tags,synced_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)"
            )
        with self._conn() as conn:
            self._exec(conn, sql, (
                user_id, device_id, p["local_id"],
                p.get("source_local_id"), p.get("project_tag"), p.get("type"),
                p["principle"], int(p.get("impact_score", 5)), p.get("tags"), now,
            ))
            conn.commit()

    def pull_traces(self, user_id: str, since: int = 0, exclude_device: str = "") -> list[dict]:
        sql = self._q("SELECT * FROM sync_traces WHERE user_id=? AND synced_at>?")
        params: tuple = (user_id, since)
        if exclude_device:
            sql += self._q(" AND device_id!=?")
            params += (exclude_device,)
        with self._conn() as conn:
            return self._fetchall(conn, sql, params)

    def pull_principles(self, user_id: str, since: int = 0, exclude_device: str = "") -> list[dict]:
        sql = self._q("SELECT * FROM sync_principles WHERE user_id=? AND synced_at>?")
        params: tuple = (user_id, since)
        if exclude_device:
            sql += self._q(" AND device_id!=?")
            params += (exclude_device,)
        with self._conn() as conn:
            return self._fetchall(conn, sql, params)

    # ---- Stats / Activity ----

    def count_runs(self, user_id: str) -> int:
        """Total usage_runs count for user."""
        with self._conn() as conn:
            cur = self._exec(
                conn,
                self._q("SELECT COUNT(*) as cnt FROM usage_runs WHERE user_id=?"),
                (user_id,),
            )
            row = cur.fetchone()
        return (dict(row).get("cnt") or 0) if row else 0

    def get_recent_runs(self, user_id: str, limit: int = 20) -> list[dict]:
        """Recent usage_runs rows: model, cost_usd, ts columns."""
        with self._conn() as conn:
            return self._fetchall(
                conn,
                self._q(
                    "SELECT model, cost_usd, ts FROM usage_runs "
                    "WHERE user_id=? ORDER BY ts DESC LIMIT ?"
                ),
                (user_id, limit),
            )

    def count_synced_traces(self, user_id: str) -> int:
        """Count of sync_traces rows for user."""
        with self._conn() as conn:
            cur = self._exec(
                conn,
                self._q("SELECT COUNT(*) as cnt FROM sync_traces WHERE user_id=?"),
                (user_id,),
            )
            row = cur.fetchone()
        return (dict(row).get("cnt") or 0) if row else 0

    def count_synced_principles(self, user_id: str) -> int:
        """Count of sync_principles rows for user."""
        with self._conn() as conn:
            cur = self._exec(
                conn,
                self._q("SELECT COUNT(*) as cnt FROM sync_principles WHERE user_id=?"),
                (user_id,),
            )
            row = cur.fetchone()
        return (dict(row).get("cnt") or 0) if row else 0

    def get_synced_projects(self, user_id: str) -> list[str]:
        """Distinct project names from sync_traces for user."""
        with self._conn() as conn:
            rows = self._fetchall(
                conn,
                self._q(
                    "SELECT DISTINCT project_tag FROM sync_traces "
                    "WHERE user_id=? AND project_tag IS NOT NULL AND project_tag != ''"
                ),
                (user_id,),
            )
        return [r["project_tag"] for r in rows]
