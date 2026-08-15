"""
FIRE Capital Tools - user-configured settings.

A small namespaced key/value store for the handful of things that are
genuinely a firm's choice rather than a property's data: grading bands
today, Deal Readiness thresholds later.

WHY A NEW STORE RATHER THAN AN EXISTING TABLE

Quick Deal Analyzer is stateless -- it has no database at all, because
every figure it renders is a pure function of the submitted form. It has
nowhere to put a setting.

The obvious alternative was underwriting.db, which is where Deal
Readiness lives. That would mean Quick Deal Analyzer's configuration
sitting inside another tool's database, and a delete-cascade for an
underwriting scenario running past a setting that has nothing to do with
scenarios. A shared setting belongs to the app, not to whichever tool
happened to need one first.

WHY NAMESPACED KEY/VALUE

Deal Readiness has the identical problem -- invented defaults under a
"not confirmed" disclaimer -- and will want the same mechanism. A
namespace column means that lands as a new namespace rather than a
migration, and the two cannot collide. This task deliberately wires only
Quick Deal Analyzer; nothing here touches Deal Readiness.

WHY ONE GLOBAL SETTING AND NOT PER-USER OR PER-DEAL

Per-user is not possible: this app has a single shared admin login, and
there is no user model to hang a setting off -- no created_by anywhere in
the schema.

Per-deal would be worse than impossible, it would be wrong. A grading
band is a statement about what the firm will pay, not a property of the
building being graded. Thresholds that varied per deal would make the
colour mean something different on every screen, which removes the only
thing a grade is for: comparing one deal against another on a fixed
standard.
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (namespace, key)
);
"""


def get_db_path() -> Path:
    configured = os.environ.get("APP_SETTINGS_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    return BASE_DIR / "app_settings.db"


def storage_status() -> dict[str, Any]:
    configured = os.environ.get("APP_SETTINGS_DB_PATH", "").strip()
    return {"path": str(get_db_path()), "persistent": bool(configured),
            "env_var": "APP_SETTINGS_DB_PATH"}


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
    return datetime.datetime.utcnow().isoformat()


def get(conn: sqlite3.Connection, namespace: str, key: str,
        default: Any = None) -> Any:
    row = conn.execute(
        "SELECT value_json FROM app_settings WHERE namespace = ? AND key = ?",
        (namespace, key)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value_json"])
    except (TypeError, ValueError):
        # A corrupt value reads as absent rather than raising. The caller
        # falls back to its own default, which is the same outcome as
        # never having configured it -- and far better than a settings
        # row taking a tool down.
        return default


def set_value(conn: sqlite3.Connection, namespace: str, key: str,
              value: Any) -> None:
    conn.execute(
        """INSERT INTO app_settings (namespace, key, value_json, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(namespace, key) DO UPDATE SET
               value_json = excluded.value_json,
               updated_at = excluded.updated_at""",
        (namespace, key, json.dumps(value), _now()))
    conn.commit()


def updated_at(conn: sqlite3.Connection, namespace: str, key: str) -> str | None:
    row = conn.execute(
        "SELECT updated_at FROM app_settings WHERE namespace = ? AND key = ?",
        (namespace, key)).fetchone()
    return row["updated_at"] if row else None


def clear(conn: sqlite3.Connection, namespace: str, key: str | None = None) -> int:
    """Remove a setting, or a whole namespace.

    Clearing is a first-class operation, not an afterthought: a user who
    configured thresholds and then wants the placeholders back must be
    able to get exactly the original behaviour, not an approximation of
    it.
    """
    if key is None:
        cur = conn.execute("DELETE FROM app_settings WHERE namespace = ?",
                           (namespace,))
    else:
        cur = conn.execute(
            "DELETE FROM app_settings WHERE namespace = ? AND key = ?",
            (namespace, key))
    conn.commit()
    return cur.rowcount


def has(conn: sqlite3.Connection, namespace: str, key: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM app_settings WHERE namespace = ? AND key = ?",
        (namespace, key)).fetchone() is not None
