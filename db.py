"""Shared data-access layer backed by PostgreSQL and Redis.

* **PostgreSQL** holds all durable state (registered bots, failed attempts,
  verified users, the single allowed group per bot). Access goes through a
  thread-safe connection pool, so it is safe to call from pyTelegramBotAPI's
  worker threads and from background monitor threads alike.
* **Redis** holds ephemeral, self-expiring state (pending captcha challenges and
  the manager's per-user conversation state). TTLs replace the old manual
  cleanup thread.

Both the manager (``antispambot.py``) and every child bot (``child_bot.py``)
import this module and therefore share the same stores, which is what lets the
manager read a group id that a child wrote (`/mybots`).
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from typing import Iterator, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
import redis

import config

log = config.setup_logging("antispam.db")

_pg_pool: Optional[ThreadedConnectionPool] = None
_pg_semaphore: Optional[threading.BoundedSemaphore] = None
_pg_lock = threading.Lock()
_redis: Optional["redis.Redis"] = None


# --------------------------------------------------------------------------- #
# Connection management
# --------------------------------------------------------------------------- #
def _pool() -> ThreadedConnectionPool:
    global _pg_pool, _pg_semaphore
    if _pg_pool is None:
        with _pg_lock:
            if _pg_pool is None:
                maxconn = int(os.environ.get("PG_POOL_MAX", "10"))
                _pg_semaphore = threading.BoundedSemaphore(maxconn)
                _pg_pool = ThreadedConnectionPool(
                    minconn=1, maxconn=maxconn, dsn=config.DATABASE_URL
                )
    return _pg_pool


@contextmanager
def get_cursor(commit: bool = False) -> Iterator[RealDictCursor]:
    """Borrow a pooled connection and yield a dict cursor.

    A semaphore bounds concurrent borrowers to the pool size, so callers *wait*
    for a free connection instead of hitting ``PoolError`` under load (telebot
    dispatches handlers on many worker threads). The connection is always
    returned to the pool; it is rolled back on error, committed on success.
    """
    pool = _pool()
    assert _pg_semaphore is not None
    _pg_semaphore.acquire()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)
        _pg_semaphore.release()


def get_redis() -> "redis.Redis":
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)
    return _redis


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
def init_schema() -> None:
    """Create every table if it does not already exist (idempotent)."""
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_bots (
                user_id      BIGINT PRIMARY KEY,
                bot_token    TEXT NOT NULL,
                bot_username TEXT,
                group_id     BIGINT,
                created_at   DOUBLE PRECISION,
                process_id   INTEGER
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS failed_attempts (
                user_id   BIGINT,
                group_id  BIGINT,
                fails     INTEGER DEFAULT 0,
                ban_until DOUBLE PRECISION,
                PRIMARY KEY (user_id, group_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS verified_users (
                user_id     BIGINT,
                group_id    BIGINT,
                verified_at DOUBLE PRECISION,
                PRIMARY KEY (user_id, group_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS allowed_group (
                owner_id BIGINT PRIMARY KEY,
                group_id BIGINT NOT NULL
            )
            """
        )
    log.info("Database schema is ready")


# --------------------------------------------------------------------------- #
# Manager: user_bots
# --------------------------------------------------------------------------- #
def get_user_bot(user_id: int) -> Optional[dict]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM user_bots WHERE user_id = %s", (user_id,))
        return cur.fetchone()


def get_user_bot_by_token(token: str) -> Optional[dict]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM user_bots WHERE bot_token = %s", (token,))
        return cur.fetchone()


def upsert_user_bot(user_id: int, token: str, username: str, created_at: float) -> None:
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO user_bots (user_id, bot_token, bot_username, created_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE
                SET bot_token = EXCLUDED.bot_token,
                    bot_username = EXCLUDED.bot_username,
                    created_at = EXCLUDED.created_at
            """,
            (user_id, token, username, created_at),
        )


def set_process_id(user_id: int, pid: Optional[int]) -> None:
    with get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE user_bots SET process_id = %s WHERE user_id = %s",
            (pid, user_id),
        )


def delete_user_bot(user_id: int) -> None:
    with get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM user_bots WHERE user_id = %s", (user_id,))


def all_user_bots() -> list[dict]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM user_bots")
        return cur.fetchall()


def running_bots() -> list[dict]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT user_id, process_id FROM user_bots WHERE process_id IS NOT NULL"
        )
        return cur.fetchall()


# --------------------------------------------------------------------------- #
# Child: allowed group (one group per bot owner)
# --------------------------------------------------------------------------- #
def get_allowed_group(owner_id: int) -> Optional[int]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT group_id FROM allowed_group WHERE owner_id = %s", (owner_id,)
        )
        row = cur.fetchone()
        return row["group_id"] if row else None


def is_allowed_group(owner_id: int, group_id: int) -> bool:
    return get_allowed_group(owner_id) == group_id


def try_claim_group(owner_id: int, group_id: int) -> bool:
    """Atomically claim ``group_id`` as the single allowed group for ``owner_id``.

    Returns ``True`` if this call is the one that set it (or it was already the
    same group), ``False`` if a different group was already claimed. This closes
    the race where a bot is added to two groups almost simultaneously.
    """
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO allowed_group (owner_id, group_id)
            VALUES (%s, %s)
            ON CONFLICT (owner_id) DO NOTHING
            """,
            (owner_id, group_id),
        )
        cur.execute(
            "SELECT group_id FROM allowed_group WHERE owner_id = %s", (owner_id,)
        )
        row = cur.fetchone()
    return bool(row) and row["group_id"] == group_id


def set_bot_group(owner_id: int, group_id: int) -> None:
    """Record the active group on the owner's ``user_bots`` row so `/mybots` works."""
    with get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE user_bots SET group_id = %s WHERE user_id = %s",
            (group_id, owner_id),
        )


# --------------------------------------------------------------------------- #
# Child: failed attempts & verified users
# --------------------------------------------------------------------------- #
def get_failed(user_id: int, group_id: int) -> Optional[dict]:
    with get_cursor() as cur:
        cur.execute(
            "SELECT fails, ban_until FROM failed_attempts "
            "WHERE user_id = %s AND group_id = %s",
            (user_id, group_id),
        )
        return cur.fetchone()


def ensure_failed_row(user_id: int, group_id: int) -> None:
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO failed_attempts (user_id, group_id, fails, ban_until)
            VALUES (%s, %s, 0, NULL)
            ON CONFLICT (user_id, group_id) DO NOTHING
            """,
            (user_id, group_id),
        )


def increment_fails(user_id: int, group_id: int) -> int:
    """Increment the fail counter (creating the row if needed) and return the new value."""
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO failed_attempts (user_id, group_id, fails)
            VALUES (%s, %s, 1)
            ON CONFLICT (user_id, group_id) DO UPDATE
                SET fails = failed_attempts.fails + 1
            RETURNING fails
            """,
            (user_id, group_id),
        )
        return cur.fetchone()["fails"]


def set_ban_until(user_id: int, group_id: int, ban_until: float) -> None:
    with get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE failed_attempts SET ban_until = %s "
            "WHERE user_id = %s AND group_id = %s",
            (ban_until, user_id, group_id),
        )


def reset_failed(user_id: int, group_id: int) -> None:
    with get_cursor(commit=True) as cur:
        cur.execute(
            "DELETE FROM failed_attempts WHERE user_id = %s AND group_id = %s",
            (user_id, group_id),
        )


def is_verified(user_id: int, group_id: int) -> bool:
    with get_cursor() as cur:
        cur.execute(
            "SELECT 1 FROM verified_users WHERE user_id = %s AND group_id = %s",
            (user_id, group_id),
        )
        return cur.fetchone() is not None


def mark_verified(user_id: int, group_id: int, verified_at: float) -> None:
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO verified_users (user_id, group_id, verified_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, group_id) DO UPDATE
                SET verified_at = EXCLUDED.verified_at
            """,
            (user_id, group_id, verified_at),
        )


def forget_member(user_id: int, group_id: int) -> None:
    """Drop verification + pending state when a member leaves the group."""
    with get_cursor(commit=True) as cur:
        cur.execute(
            "DELETE FROM verified_users WHERE user_id = %s AND group_id = %s",
            (user_id, group_id),
        )


# --------------------------------------------------------------------------- #
# Redis: pending captcha challenges (auto-expiring)
# --------------------------------------------------------------------------- #
def _pending_key(owner_id: int, user_id: int) -> str:
    return f"pending:{owner_id}:{user_id}"


def set_pending(owner_id: int, user_id: int, group_id: int, answer: str) -> None:
    get_redis().set(
        _pending_key(owner_id, user_id),
        json.dumps({"group_id": group_id, "answer": answer}),
        ex=config.PENDING_TTL_SECONDS,
    )


def get_pending(owner_id: int, user_id: int) -> Optional[tuple[int, str]]:
    raw = get_redis().get(_pending_key(owner_id, user_id))
    if not raw:
        return None
    data = json.loads(raw)
    return int(data["group_id"]), data["answer"]


def clear_pending(owner_id: int, user_id: int) -> None:
    get_redis().delete(_pending_key(owner_id, user_id))


# --------------------------------------------------------------------------- #
# Redis: manager conversation state
# --------------------------------------------------------------------------- #
def _state_key(user_id: int) -> str:
    return f"state:{user_id}"


def set_state(user_id: int, state: str) -> None:
    get_redis().set(_state_key(user_id), state, ex=config.STATE_TTL_SECONDS)


def get_state(user_id: int) -> Optional[str]:
    return get_redis().get(_state_key(user_id))


def clear_state(user_id: int) -> None:
    get_redis().delete(_state_key(user_id))
