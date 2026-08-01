"""SQLite — durable job queue, event log, and read model.

Deliberately disposable: B2 holds the system of record; this file is a
local accelerator. `bside.restore` rebuilds it from the bucket.
WAL mode + short busy timeout make it safe for the API and the worker
sharing one process.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any

from bside.config import settings
from bside.models import EventRecord, utcnow

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS shows (
  id TEXT PRIMARY KEY,
  doc TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS episodes (
  id TEXT PRIMARY KEY,
  show_id TEXT NOT NULL,
  status TEXT NOT NULL,
  doc TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'queued',       -- queued|running|done|failed|cancelled
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  next_attempt_at TEXT NOT NULL,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(status, next_attempt_at);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_id TEXT NOT NULL,
  type TEXT NOT NULL,
  data TEXT NOT NULL,
  ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_ep ON events(episode_id, seq);
CREATE TABLE IF NOT EXISTS counters (
  day TEXT PRIMARY KEY,
  episodes_started INTEGER NOT NULL DEFAULT 0
);
"""


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(settings().sqlite_path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init_db() -> None:
    connect().executescript(SCHEMA)
    connect().commit()


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---------- read model ----------


def upsert_show(show_id: str, doc: dict[str, Any]) -> None:
    with tx() as c:
        c.execute(
            "INSERT INTO shows(id, doc, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET doc=excluded.doc, updated_at=excluded.updated_at",
            (show_id, json.dumps(doc), utcnow()),
        )


def get_show(show_id: str) -> dict[str, Any] | None:
    row = connect().execute("SELECT doc FROM shows WHERE id=?", (show_id,)).fetchone()
    return json.loads(row["doc"]) if row else None


def list_shows() -> list[dict[str, Any]]:
    rows = connect().execute("SELECT doc FROM shows ORDER BY updated_at DESC").fetchall()
    return [json.loads(r["doc"]) for r in rows]


def upsert_episode(ep_id: str, show_id: str, status: str, doc: dict[str, Any]) -> None:
    with tx() as c:
        c.execute(
            "INSERT INTO episodes(id, show_id, status, doc, updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, doc=excluded.doc, "
            "updated_at=excluded.updated_at",
            (ep_id, show_id, status, json.dumps(doc), utcnow()),
        )


def get_episode(ep_id: str) -> dict[str, Any] | None:
    row = connect().execute("SELECT doc FROM episodes WHERE id=?", (ep_id,)).fetchone()
    return json.loads(row["doc"]) if row else None


def list_episodes(show_id: str | None = None) -> list[dict[str, Any]]:
    if show_id:
        rows = connect().execute(
            "SELECT doc FROM episodes WHERE show_id=? ORDER BY updated_at DESC", (show_id,)
        ).fetchall()
    else:
        rows = connect().execute("SELECT doc FROM episodes ORDER BY updated_at DESC").fetchall()
    return [json.loads(r["doc"]) for r in rows]


# ---------- durable job queue ----------


def enqueue(episode_id: str, kind: str, payload: dict[str, Any] | None = None, max_attempts: int = 3) -> int:
    with tx() as c:
        cur = c.execute(
            "INSERT INTO jobs(episode_id, kind, payload, next_attempt_at, max_attempts, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            (episode_id, kind, json.dumps(payload or {}), utcnow(), max_attempts, utcnow(), utcnow()),
        )
        return int(cur.lastrowid)


def claim_ready_job() -> dict[str, Any] | None:
    """Atomically claim one ready job (queued and due)."""
    with tx() as c:
        row = c.execute(
            "SELECT * FROM jobs WHERE status='queued' AND next_attempt_at<=? "
            "ORDER BY id LIMIT 1",
            (utcnow(),),
        ).fetchone()
        if row is None:
            return None
        c.execute(
            "UPDATE jobs SET status='running', attempts=attempts+1, updated_at=? WHERE id=?",
            (utcnow(), row["id"]),
        )
        job = dict(row)
        job["attempts"] = row["attempts"] + 1
        job["payload"] = json.loads(row["payload"])
        return job


def finish_job(
    job_id: int, status: str, error: str | None = None, next_attempt_at: str | None = None
) -> None:
    with tx() as c:
        c.execute(
            "UPDATE jobs SET status=?, error=?, next_attempt_at=COALESCE(?, next_attempt_at), "
            "updated_at=? WHERE id=?",
            (status, error, next_attempt_at, utcnow(), job_id),
        )


def requeue_job(job_id: int, error: str, next_attempt_at: str) -> None:
    with tx() as c:
        c.execute(
            "UPDATE jobs SET status='queued', error=?, next_attempt_at=?, updated_at=? WHERE id=?",
            (error, next_attempt_at, utcnow(), job_id),
        )


def recover_orphans() -> int:
    """On boot: any job left 'running' by a crash goes back to 'queued'."""
    with tx() as c:
        cur = c.execute(
            "UPDATE jobs SET status='queued', updated_at=?, "
            "error=COALESCE(error,'') || ' [recovered after restart]' WHERE status='running'",
            (utcnow(),),
        )
        return cur.rowcount


def job_for_episode(episode_id: str) -> dict[str, Any] | None:
    row = connect().execute(
        "SELECT * FROM jobs WHERE episode_id=? ORDER BY id DESC LIMIT 1", (episode_id,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["payload"] = json.loads(row["payload"])
    return d


# ---------- events ----------


def append_event(episode_id: str, type_: str, data: dict[str, Any]) -> EventRecord:
    with tx() as c:
        cur = c.execute(
            "INSERT INTO events(episode_id, type, data, ts) VALUES(?,?,?,?)",
            (episode_id, type_, json.dumps(data), utcnow()),
        )
        seq = int(cur.lastrowid)
    return EventRecord(seq=seq, episode_id=episode_id, type=type_, data=data)


def events_after(episode_id: str, after_seq: int, limit: int = 500) -> list[EventRecord]:
    rows = connect().execute(
        "SELECT * FROM events WHERE episode_id=? AND seq>? ORDER BY seq LIMIT ?",
        (episode_id, after_seq, limit),
    ).fetchall()
    return [
        EventRecord(seq=r["seq"], episode_id=r["episode_id"], type=r["type"],
                    data=json.loads(r["data"]), ts=r["ts"])
        for r in rows
    ]


# ---------- judge-mode budget ----------


def try_consume_daily_budget(limit: int) -> bool:
    day = utcnow()[:10]
    with tx() as c:
        c.execute(
            "INSERT INTO counters(day, episodes_started) VALUES(?,0) ON CONFLICT(day) DO NOTHING",
            (day,),
        )
        row = c.execute("SELECT episodes_started FROM counters WHERE day=?", (day,)).fetchone()
        if row["episodes_started"] >= limit:
            return False
        c.execute("UPDATE counters SET episodes_started=episodes_started+1 WHERE day=?", (day,))
        return True
