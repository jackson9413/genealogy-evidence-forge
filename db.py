"""SQLite session log for genealogy-evidence-forge."""

import json
import os
import sqlite3
import time

DB_PATH = os.environ.get(
    "GENEALOGY_FORGE_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "genealogy_forge.db"))


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            mode TEXT NOT NULL,
            title TEXT,
            notes_text TEXT NOT NULL,
            overall REAL,
            verdict TEXT,
            rating INTEGER,
            notes TEXT,
            payload TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def save_session(mode, text, result, title=None):
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO sessions (created_at, mode, title, notes_text, overall, verdict, payload) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (int(time.time()), mode, title or (text.strip().splitlines() or ["Untitled"])[0][:80],
         text, result.get("overall"), result.get("verdict"),
         json.dumps(result)))
    conn.commit()
    sid = cur.lastrowid
    conn.close()
    return sid


def list_sessions(limit=50):
    conn = _connect()
    rows = conn.execute(
        "SELECT id, created_at, mode, title, overall, verdict, rating, notes "
        "FROM sessions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_session(sid):
    conn = _connect()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    conn.close()
    if not row:
        return None
    data = dict(row)
    data["result"] = json.loads(data.pop("payload"))
    return data


def update_session(sid, rating=None, notes=None):
    conn = _connect()
    row = conn.execute("SELECT id FROM sessions WHERE id = ?", (sid,)).fetchone()
    if not row:
        conn.close()
        return None
    if rating is not None:
        conn.execute("UPDATE sessions SET rating = ? WHERE id = ?", (int(rating), sid))
    if notes is not None:
        conn.execute("UPDATE sessions SET notes = ? WHERE id = ?", (notes, sid))
    conn.commit()
    conn.close()
    return get_session(sid)
