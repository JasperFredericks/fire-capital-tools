"""
FIRE Capital Tools - Deal Dive persistence.

Stores deals (one row per property being evaluated as a potential
acquisition, or a deeper look at an existing one) plus each deal's comps
and condition assessment. Files (offering memoranda, T12s, inspection
reports, photos) are tracked here as metadata only -- the bytes live on
disk under uploads/deal-dive/, the same way every other upload in this
app works.

Reuses the connection/schema-init *pattern* already established by
tools/scorecard_history.py and fire_metrics/fire_metrics_updater/db.py --
a fresh connection per call, idempotent CREATE TABLE IF NOT EXISTS on
every connect, env-var-overridable path with a local fallback. The
schema itself is new (deals/comps/condition/files), not shared with
either of those.

The database path is controlled by DEAL_DIVE_DB_PATH (falls back to a
local file at the repo root for development). In production this should
point at a persistent volume, the same way SCORECARD_PRO_DB_PATH and
FIRE_METRICS_DB_PATH already do.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent

STATUSES = ("active_review", "under_contract", "closed", "passed", "archived")
DEFAULT_STATUS = "active_review"

SCHEMA = """
CREATE TABLE IF NOT EXISTS deals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT NOT NULL,
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    zip TEXT,
    property_type TEXT NOT NULL DEFAULT 'Multifamily',
    unit_count INTEGER,
    status TEXT NOT NULL DEFAULT 'active_review',

    asking_price REAL,
    purchase_price REAL,
    current_noi REAL,
    projected_noi REAL,
    cap_rate REAL,
    financial_notes TEXT,

    condition_rating TEXT,
    condition_notes TEXT,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deal_comps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id INTEGER NOT NULL,
    comp_type TEXT NOT NULL DEFAULT 'sale',
    address TEXT,
    price REAL,
    unit_count INTEGER,
    comp_date TEXT,
    source_notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deal_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    uploaded_at TEXT NOT NULL
);
"""


def get_db_path() -> Path:
    configured = os.environ.get("DEAL_DIVE_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    return BASE_DIR / "deal_dive.db"


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


# ── Deals ────────────────────────────────────────────────────────────────

def create_deal(conn: sqlite3.Connection, fields: dict[str, Any]) -> int:
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO deals (address, city, state, zip, property_type, unit_count, status,
                           created_at, updated_at)
        VALUES (:address, :city, :state, :zip, :property_type, :unit_count, :status,
                :created_at, :updated_at)
        """,
        {
            "address": fields["address"],
            "city": fields["city"],
            "state": fields["state"],
            "zip": fields.get("zip"),
            "property_type": fields.get("property_type") or "Multifamily",
            "unit_count": fields.get("unit_count"),
            "status": fields.get("status") or DEFAULT_STATUS,
            "created_at": now,
            "updated_at": now,
        },
    )
    conn.commit()
    return cur.lastrowid


def list_deals(conn: sqlite3.Connection, status: str | None = None) -> list[dict[str, Any]]:
    if status:
        rows = conn.execute(
            "SELECT * FROM deals WHERE status = ? ORDER BY updated_at DESC", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM deals ORDER BY updated_at DESC").fetchall()
    return [dict(row) for row in rows]


def get_deal(conn: sqlite3.Connection, deal_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)).fetchone()
    return dict(row) if row else None


def update_deal_status(conn: sqlite3.Connection, deal_id: int, status: str) -> None:
    conn.execute(
        "UPDATE deals SET status = ?, updated_at = ? WHERE id = ?",
        (status, _now(), deal_id),
    )
    conn.commit()


def update_deal(conn: sqlite3.Connection, deal_id: int, fields: dict[str, Any]) -> None:
    conn.execute(
        """
        UPDATE deals SET
            address = :address,
            city = :city,
            state = :state,
            zip = :zip,
            property_type = :property_type,
            unit_count = :unit_count,
            updated_at = :updated_at
        WHERE id = :deal_id
        """,
        {
            "address": fields["address"],
            "city": fields["city"],
            "state": fields["state"],
            "zip": fields.get("zip"),
            "property_type": fields.get("property_type") or "Multifamily",
            "unit_count": fields.get("unit_count"),
            "updated_at": _now(),
            "deal_id": deal_id,
        },
    )
    conn.commit()


def delete_deal(conn: sqlite3.Connection, deal_id: int) -> None:
    conn.execute("DELETE FROM deal_comps WHERE deal_id = ?", (deal_id,))
    conn.execute("DELETE FROM deal_files WHERE deal_id = ?", (deal_id,))
    conn.execute("DELETE FROM deals WHERE id = ?", (deal_id,))
    conn.commit()


def update_financials(conn: sqlite3.Connection, deal_id: int, fields: dict[str, Any]) -> None:
    conn.execute(
        """
        UPDATE deals SET
            asking_price = :asking_price,
            purchase_price = :purchase_price,
            current_noi = :current_noi,
            projected_noi = :projected_noi,
            cap_rate = :cap_rate,
            financial_notes = :financial_notes,
            updated_at = :updated_at
        WHERE id = :deal_id
        """,
        {
            "asking_price": fields.get("asking_price"),
            "purchase_price": fields.get("purchase_price"),
            "current_noi": fields.get("current_noi"),
            "projected_noi": fields.get("projected_noi"),
            "cap_rate": fields.get("cap_rate"),
            "financial_notes": fields.get("financial_notes"),
            "updated_at": _now(),
            "deal_id": deal_id,
        },
    )
    conn.commit()


def update_condition(conn: sqlite3.Connection, deal_id: int, fields: dict[str, Any]) -> None:
    conn.execute(
        """
        UPDATE deals SET
            condition_rating = :condition_rating,
            condition_notes = :condition_notes,
            updated_at = :updated_at
        WHERE id = :deal_id
        """,
        {
            "condition_rating": fields.get("condition_rating"),
            "condition_notes": fields.get("condition_notes"),
            "updated_at": _now(),
            "deal_id": deal_id,
        },
    )
    conn.commit()


# ── Comps ────────────────────────────────────────────────────────────────

def add_comp(conn: sqlite3.Connection, deal_id: int, fields: dict[str, Any]) -> int:
    cur = conn.execute(
        """
        INSERT INTO deal_comps (deal_id, comp_type, address, price, unit_count, comp_date,
                                source_notes, created_at)
        VALUES (:deal_id, :comp_type, :address, :price, :unit_count, :comp_date,
                :source_notes, :created_at)
        """,
        {
            "deal_id": deal_id,
            "comp_type": fields.get("comp_type") or "sale",
            "address": fields.get("address"),
            "price": fields.get("price"),
            "unit_count": fields.get("unit_count"),
            "comp_date": fields.get("comp_date"),
            "source_notes": fields.get("source_notes"),
            "created_at": _now(),
        },
    )
    conn.commit()
    return cur.lastrowid


def list_comps(conn: sqlite3.Connection, deal_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM deal_comps WHERE deal_id = ? ORDER BY comp_date DESC, id DESC",
        (deal_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def delete_comp(conn: sqlite3.Connection, deal_id: int, comp_id: int) -> None:
    conn.execute("DELETE FROM deal_comps WHERE id = ? AND deal_id = ?", (comp_id, deal_id))
    conn.commit()


# ── Files ────────────────────────────────────────────────────────────────

def add_file(conn: sqlite3.Connection, deal_id: int, category: str, original_name: str, stored_name: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO deal_files (deal_id, category, original_name, stored_name, uploaded_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (deal_id, category, original_name, stored_name, _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_files(conn: sqlite3.Connection, deal_id: int, category: str | None = None) -> list[dict[str, Any]]:
    if category:
        rows = conn.execute(
            "SELECT * FROM deal_files WHERE deal_id = ? AND category = ? ORDER BY uploaded_at DESC",
            (deal_id, category),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM deal_files WHERE deal_id = ? ORDER BY uploaded_at DESC", (deal_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def get_file(conn: sqlite3.Connection, deal_id: int, file_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM deal_files WHERE id = ? AND deal_id = ?", (file_id, deal_id)
    ).fetchone()
    return dict(row) if row else None
