"""
FIRE Capital Tools - Scorecard Pro save-progress / trend history.

Persists each upload's monthly KPIs (income, expenses, NOI, occupancy,
expense ratio) per property, so a new upload can be compared against
everything accumulated so far instead of being treated as a one-off
snapshot.

This reuses the connection/schema-init *pattern* from FIRE Metrics' own
SQLite module (fire_metrics/fire_metrics_updater/db.py) -- a fresh
connection per call, idempotent CREATE TABLE IF NOT EXISTS on every
connect, env-var-overridable path with a local fallback -- since that
part is generic and has nothing FIRE-Metrics-specific in it. The schema
itself is unrelated: FIRE Metrics stores one current snapshot per city
with no history table at all (its "city comparison" is a client-side,
localStorage-only feature comparing different cities' current values,
not one entity's values over time), which is a fundamentally different
shape from a per-property, per-month history. So only the storage
*pattern* is shared here, not any FIRE Metrics code or schema.

The database path is controlled by SCORECARD_PRO_DB_PATH (falls back to
a local file at the repo root for development). In production this
should point at a persistent volume, the same way FIRE_METRICS_DB_PATH
and FBI_CRIME_WORKBOOK_PATH already do for this app's other durable
storage -- set via the environment, never hardcoded.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS scorecard_history (
    property_key TEXT NOT NULL,
    property_name TEXT NOT NULL,
    month TEXT NOT NULL,
    month_start TEXT NOT NULL,
    income REAL,
    expenses REAL,
    noi REAL,
    occupancy REAL,
    expense_ratio REAL,
    uploaded_at TEXT NOT NULL,
    PRIMARY KEY (property_key, month)
);
"""


def get_db_path() -> Path:
    configured = os.environ.get("SCORECARD_PRO_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    return BASE_DIR / "scorecard_pro_history.db"


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


def normalize_property_key(property_name: str) -> str:
    """Case/whitespace-insensitive key so the same property always lands
    in the same history row group even if a later upload's parser output
    capitalizes or spaces the name slightly differently."""
    return " ".join(str(property_name or "").strip().lower().split())


def get_history(conn: sqlite3.Connection, property_key: str) -> list[dict[str, Any]]:
    """Full accumulated history for a property, oldest to newest."""
    rows = conn.execute(
        "SELECT * FROM scorecard_history WHERE property_key = ? ORDER BY month_start ASC",
        (property_key,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_latest(conn: sqlite3.Connection, property_key: str) -> dict[str, Any] | None:
    """Most recent month on record for a property, or None if it has no history yet."""
    row = conn.execute(
        "SELECT * FROM scorecard_history WHERE property_key = ? ORDER BY month_start DESC LIMIT 1",
        (property_key,),
    ).fetchone()
    return dict(row) if row else None


def upsert_months(
    conn: sqlite3.Connection,
    property_name: str,
    months: Iterable[dict[str, Any]],
    uploaded_at: str,
) -> str:
    """Insert-or-update one row per month for this property.

    `months` items need: month (display label, e.g. "Jun 2025"),
    month_start (ISO date string, e.g. "2025-06-01", for correct
    chronological ordering -- month labels alone don't sort correctly),
    and income/expenses/noi/occupancy/expense_ratio.

    An overlapping month (already on file from a prior upload's trailing
    T12 window) is updated in place, not duplicated -- the newer upload's
    values win, per (property_key, month) being the primary key.

    Returns the property_key the rows were stored under.
    """
    property_key = normalize_property_key(property_name)
    for m in months:
        conn.execute(
            """
            INSERT INTO scorecard_history
                (property_key, property_name, month, month_start, income, expenses,
                 noi, occupancy, expense_ratio, uploaded_at)
            VALUES (:property_key, :property_name, :month, :month_start, :income,
                    :expenses, :noi, :occupancy, :expense_ratio, :uploaded_at)
            ON CONFLICT(property_key, month) DO UPDATE SET
                property_name = excluded.property_name,
                month_start = excluded.month_start,
                income = excluded.income,
                expenses = excluded.expenses,
                noi = excluded.noi,
                occupancy = excluded.occupancy,
                expense_ratio = excluded.expense_ratio,
                uploaded_at = excluded.uploaded_at
            """,
            {
                "property_key": property_key,
                "property_name": property_name,
                "month": m["month"],
                "month_start": m["month_start"],
                "income": m.get("income"),
                "expenses": m.get("expenses"),
                "noi": m.get("noi"),
                "occupancy": m.get("occupancy"),
                "expense_ratio": m.get("expense_ratio"),
                "uploaded_at": uploaded_at,
            },
        )
    conn.commit()
    return property_key
