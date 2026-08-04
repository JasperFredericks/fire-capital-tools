"""
FIRE Capital Tools - Beta feedback storage.

One table, shared by every tool. Each row records which tool the note came
from and what page it was written on, so "what did Michelle say about Deal
Analyzer?" is answerable without hunting through email.

Same connection/schema-init pattern as every other SQLite module here
(tools/deal_dive_db.py, tools/rent_comps_db.py, tools/market_data_cache.py,
tools/scorecard_history.py): env-var-overridable path with a repo-relative
fallback, fresh connection per call, idempotent CREATE TABLE IF NOT EXISTS
on every connect.

The database path is controlled by FEEDBACK_DB_PATH. In production this
MUST point at the persistent volume (/data/feedback.db). The container
filesystem is ephemeral, so a repo-relative path is destroyed on every
deploy -- this project has already had to find and fix that exact bug in
four separate modules, and the fallback below exists for local development
only, never for production.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent

MAX_MESSAGE_LEN = 5000

SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool TEXT NOT NULL,
    message TEXT NOT NULL,
    page_url TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_tool ON feedback (tool);
"""


def get_db_path() -> Path:
    configured = os.environ.get("FEEDBACK_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    return BASE_DIR / "feedback.db"


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def get_connection(db_path: Path | None = None):
    path = Path(db_path) if db_path is not None else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        init_schema(conn)
        yield conn
    finally:
        conn.close()


def _now() -> str:
    import datetime

    return datetime.datetime.utcnow().isoformat()


def add_feedback(conn: sqlite3.Connection, tool: str, message: str, page_url: str | None) -> int:
    cur = conn.execute(
        "INSERT INTO feedback (tool, message, page_url, created_at) VALUES (?, ?, ?, ?)",
        (tool, message[:MAX_MESSAGE_LEN], page_url, _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_feedback(conn: sqlite3.Connection, tool: str | None = None) -> list[dict[str, Any]]:
    if tool:
        rows = conn.execute(
            "SELECT * FROM feedback WHERE tool = ? ORDER BY id DESC", (tool,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM feedback ORDER BY id DESC").fetchall()
    return [dict(row) for row in rows]
