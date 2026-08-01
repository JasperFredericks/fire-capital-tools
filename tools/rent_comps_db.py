"""
FIRE Capital Tools - Rent Comps persistence.

Stores rental comparables the user has explicitly saved, either as a
standalone lookup (deal_id NULL) or attached to a specific Deal Dive deal
(deal_id set). Auto-pulled RentCast candidates are *not* stored here --
those live in the market-data cache and are transient until promoted.

Same connection/schema-init pattern as every other SQLite module in this
app (tools/deal_dive_db.py, tools/market_data_cache.py,
tools/scorecard_history.py): env-var-overridable path with a local
fallback, fresh connection per call, idempotent CREATE TABLE IF NOT EXISTS
on every connect.

Deliberately its own database file rather than a table inside
deal_dive.db, matching where the tool sits in the product: Rent Comps is a
standalone tool under Markets, alongside FIRE Metric, not a part of Deal
Dive under Acquisitions. It has to work with no deal at all.

The consequence is that deal_id is a *soft* reference -- a plain nullable
integer, not an enforced foreign key, since SQLite cannot enforce a FK
across database files. Two things keep that safe:

  * deal_dive_db.deals uses AUTOINCREMENT, so a deleted deal's id is never
    handed out again. An orphaned row can therefore never re-attach itself
    to an unrelated future deal.
  * delete_deal() still calls delete_comps_for_deal() below, so orphans
    don't accumulate in the first place. That cascade is *additional* to
    Deal Dive's existing one (deal_comps/deal_files), which is untouched.

The database path is controlled by RENT_COMPS_DB_PATH (falls back to a
local file at the repo root for development). In production this should
point at a persistent volume, the same way the other tool databases do.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent

SOURCE_RENTCAST = "rentcast"
SOURCE_MANUAL = "manual"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rent_comps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id INTEGER,
    address TEXT,
    bedrooms REAL,
    bathrooms REAL,
    square_footage REAL,
    distance_miles REAL,
    correlation REAL,
    days_old INTEGER,
    listing_status TEXT,
    rent REAL,
    comp_date TEXT,
    source TEXT NOT NULL DEFAULT 'rentcast',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rent_comps_deal ON rent_comps (deal_id);
"""


def get_db_path() -> Path:
    configured = os.environ.get("RENT_COMPS_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    return BASE_DIR / "rent_comps.db"


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


# ── Comps ────────────────────────────────────────────────────────────────

def add_comp(conn: sqlite3.Connection, deal_id: int | None, fields: dict[str, Any]) -> int:
    cur = conn.execute(
        """
        INSERT INTO rent_comps (deal_id, address, bedrooms, bathrooms, square_footage,
                                distance_miles, correlation, days_old, listing_status,
                                rent, comp_date, source, created_at)
        VALUES (:deal_id, :address, :bedrooms, :bathrooms, :square_footage,
                :distance_miles, :correlation, :days_old, :listing_status,
                :rent, :comp_date, :source, :created_at)
        """,
        {
            "deal_id": deal_id,
            "address": fields.get("address"),
            "bedrooms": fields.get("bedrooms"),
            "bathrooms": fields.get("bathrooms"),
            "square_footage": fields.get("square_footage"),
            "distance_miles": fields.get("distance_miles"),
            "correlation": fields.get("correlation"),
            "days_old": fields.get("days_old"),
            "listing_status": fields.get("listing_status"),
            "rent": fields.get("rent"),
            "comp_date": fields.get("comp_date"),
            "source": fields.get("source") or SOURCE_RENTCAST,
            "created_at": _now(),
        },
    )
    conn.commit()
    return cur.lastrowid


def list_comps(conn: sqlite3.Connection, deal_id: int | None) -> list[dict[str, Any]]:
    """Saved comps for one scope. deal_id=None means the standalone scope
    (rows with a NULL deal_id) -- not "all comps regardless of deal", since
    the two scopes are shown in separate contexts and mixing them would
    leak one deal's comps into another's view.

    Ordered by correlation desc so the closest matches lead, with id desc
    as the fallback for rows that have no correlation (manual entries, or
    rows promoted before correlation was captured). NULLs sort last rather
    than first, which is not SQLite's default for DESC."""
    if deal_id is None:
        rows = conn.execute(
            """
            SELECT * FROM rent_comps WHERE deal_id IS NULL
            ORDER BY (correlation IS NULL), correlation DESC, id DESC
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM rent_comps WHERE deal_id = ?
            ORDER BY (correlation IS NULL), correlation DESC, id DESC
            """,
            (deal_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def count_comps(conn: sqlite3.Connection, deal_id: int) -> int:
    """Backs Deal Dive's summary card. Scalar count only -- Deal Dive never
    needs the rows themselves, so it doesn't pay to load them."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM rent_comps WHERE deal_id = ?", (deal_id,)
    ).fetchone()
    return row["n"] if row else 0


def saved_addresses(conn: sqlite3.Connection, deal_id: int | None) -> set[str]:
    """Normalized addresses already saved in this scope, for the "Added"
    state on the candidates table and the duplicate guard on save. Matches
    on address text because that is the only stable identifier RentCast
    gives a comparable -- there is no per-listing id in the projection the
    market-data service caches."""
    return {
        (row["address"] or "").strip().lower()
        for row in (
            conn.execute("SELECT address FROM rent_comps WHERE deal_id IS NULL").fetchall()
            if deal_id is None
            else conn.execute(
                "SELECT address FROM rent_comps WHERE deal_id = ?", (deal_id,)
            ).fetchall()
        )
        if row["address"]
    }


def delete_comp(conn: sqlite3.Connection, comp_id: int, deal_id: int | None) -> None:
    """Scoped delete -- the deal_id must match the scope the user is
    viewing, so a comp id from one deal can't be removed from another
    deal's page (or from the standalone list)."""
    if deal_id is None:
        conn.execute("DELETE FROM rent_comps WHERE id = ? AND deal_id IS NULL", (comp_id,))
    else:
        conn.execute("DELETE FROM rent_comps WHERE id = ? AND deal_id = ?", (comp_id, deal_id))
    conn.commit()


def delete_comps_for_deal(conn: sqlite3.Connection, deal_id: int) -> None:
    """Called from Deal Dive's delete_deal so a deleted deal doesn't leave
    rent comps behind. Standalone rows (deal_id NULL) are never touched."""
    conn.execute("DELETE FROM rent_comps WHERE deal_id = ?", (deal_id,))
    conn.commit()
