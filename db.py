from __future__ import annotations

import os
import asyncio
import sqlite3
from datetime import datetime, timezone

# Путь к БД настраивается через env — на Railway можно указать примонтированный
# volume, чтобы статистика переживала редеплой.
DB_PATH = os.environ.get("DB_PATH") or os.path.join(os.path.dirname(__file__), "bot.db")


def _connect() -> sqlite3.Connection:
    # timeout — на случай параллельных записей из разных потоков (to_thread)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    """Создать таблицу при старте (идемпотентно, без гонок на глобальном флаге)."""
    conn = _connect()
    try:
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
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _track_user(user_id: int, username: str | None, first_name: str | None, last_name: str | None) -> None:
    conn = _connect()
    try:
        now = _now()
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
    finally:
        conn.close()


def _increment_downloads(user_id: int) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE users SET downloads = downloads + 1, last_active = ? WHERE user_id = ?",
            (_now(), user_id),
        )
        conn.commit()
    finally:
        conn.close()


def _get_stats() -> dict:
    conn = _connect()
    try:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_downloads = conn.execute("SELECT COALESCE(SUM(downloads), 0) FROM users").fetchone()[0]
    finally:
        conn.close()
    return {"total_users": total_users, "total_downloads": total_downloads}


def _get_all_users() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM users ORDER BY last_active DESC").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# Публичный API — async-обёртки, чтобы блокирующие вызовы SQLite не вешали event loop.

async def track_user(user_id: int, username: str | None, first_name: str | None, last_name: str | None) -> None:
    await asyncio.to_thread(_track_user, user_id, username, first_name, last_name)


async def increment_downloads(user_id: int) -> None:
    await asyncio.to_thread(_increment_downloads, user_id)


async def get_stats() -> dict:
    return await asyncio.to_thread(_get_stats)


async def get_all_users() -> list[dict]:
    return await asyncio.to_thread(_get_all_users)


# Инициализация схемы при импорте (до старта event loop).
_init_db()
