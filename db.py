from __future__ import annotations

import sqlite3
import os
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "bot.db")

_db_initialized = False


def get_db() -> sqlite3.Connection:
    global _db_initialized
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if not _db_initialized:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                first_seen TEXT,
                last_active TEXT,
                downloads INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        _db_initialized = True
    return conn


def track_user(user_id: int, username: str | None, first_name: str | None, last_name: str | None):
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO users (user_id, username, first_name, last_name, first_seen, last_active, downloads)
        VALUES (?, ?, ?, ?, ?, ?, 0)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name,
            last_name = excluded.last_name,
            last_active = excluded.last_active
    """, (user_id, username, first_name, last_name, now, now))
    conn.commit()
    conn.close()


def increment_downloads(user_id: int):
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE users SET downloads = downloads + 1, last_active = ? WHERE user_id = ?", (now, user_id))
    conn.commit()
    conn.close()


def get_stats() -> dict:
    conn = get_db()
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_downloads = conn.execute("SELECT COALESCE(SUM(downloads), 0) FROM users").fetchone()[0]
    conn.close()
    return {"total_users": total_users, "total_downloads": total_downloads}


def get_all_users() -> list[dict]:
    conn = get_db()
    rows = conn.execute("SELECT * FROM users ORDER BY last_active DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]
