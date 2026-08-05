"""
FIRE Capital Tools - Site DD persistence.

Three tables: an assessment (one site visit), its item responses, and its
photos. Scores are never stored -- they are computed on read by
tools/site_dd_checklist.score_assessment(), so a stored figure can never
drift out of step with the items behind it.

Same connection/schema-init pattern as every other SQLite module here:
env-var-overridable path with a repo-relative fallback, fresh connection
per call, idempotent CREATE TABLE IF NOT EXISTS on every connect.

The database path is controlled by SITE_DD_DB_PATH. In production this
MUST point at the persistent volume (/data/site_dd.db) -- the container
filesystem is ephemeral and the fallback below exists for local
development only.

deal_id is a soft reference to deal_dive.db's deals table -- a plain
nullable integer, since SQLite cannot enforce a foreign key across
database files. Two things keep that safe, exactly as for rent_comps:

  * deals uses AUTOINCREMENT, so a deleted deal's id is never reissued and
    an orphan can never re-attach itself to an unrelated future deal.
  * delete_assessments_for_deal() below is called from Deal Dive's
    delete_deal(), so orphans don't accumulate in the first place.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent

STATUS_DRAFT = "draft"
STATUS_COMPLETE = "complete"
STATUSES = (STATUS_DRAFT, STATUS_COMPLETE)

MAX_LABEL_LEN = 255
MAX_NOTE_LEN = 4000

SCHEMA = """
CREATE TABLE IF NOT EXISTS site_dd_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id INTEGER,
    property_label TEXT NOT NULL,
    assessed_on TEXT,
    inspector TEXT,
    checklist_version INTEGER NOT NULL,
    overall_notes TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS site_dd_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id INTEGER NOT NULL,
    category_key TEXT NOT NULL,
    item_key TEXT NOT NULL,
    score INTEGER,
    note TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (assessment_id, item_key)
);

CREATE TABLE IF NOT EXISTS site_dd_photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id INTEGER NOT NULL,
    item_key TEXT,
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    caption TEXT,
    uploaded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sitedd_deal ON site_dd_assessments (deal_id);
CREATE INDEX IF NOT EXISTS idx_sitedd_items ON site_dd_items (assessment_id);
CREATE INDEX IF NOT EXISTS idx_sitedd_photos ON site_dd_photos (assessment_id);
"""


def get_db_path() -> Path:
    configured = os.environ.get("SITE_DD_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    return BASE_DIR / "site_dd.db"


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


# ── Assessments ──────────────────────────────────────────────────────────

def create_assessment(conn: sqlite3.Connection, fields: dict[str, Any]) -> int:
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO site_dd_assessments
            (deal_id, property_label, assessed_on, inspector, checklist_version,
             overall_notes, status, created_at, updated_at)
        VALUES (:deal_id, :property_label, :assessed_on, :inspector, :checklist_version,
                :overall_notes, :status, :created_at, :updated_at)
        """,
        {
            "deal_id": fields.get("deal_id"),
            "property_label": (fields.get("property_label") or "Untitled")[:MAX_LABEL_LEN],
            "assessed_on": fields.get("assessed_on"),
            "inspector": fields.get("inspector"),
            "checklist_version": fields["checklist_version"],
            "overall_notes": fields.get("overall_notes"),
            "status": fields.get("status") or STATUS_DRAFT,
            "created_at": now,
            "updated_at": now,
        },
    )
    conn.commit()
    return cur.lastrowid


def get_assessment(conn: sqlite3.Connection, assessment_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM site_dd_assessments WHERE id = ?", (assessment_id,)
    ).fetchone()
    return dict(row) if row else None


def list_assessments(conn: sqlite3.Connection, deal_id: int | None = None,
                     all_scopes: bool = False) -> list[dict[str, Any]]:
    """Newest first. Three scopes, kept distinct on purpose: all_scopes for
    the index page, a specific deal for the deal-linked view, and NULL for
    the standalone list -- mixing a deal's assessments into the standalone
    list would misattribute them."""
    if all_scopes:
        rows = conn.execute(
            "SELECT * FROM site_dd_assessments ORDER BY COALESCE(assessed_on, created_at) DESC, id DESC"
        ).fetchall()
    elif deal_id is None:
        rows = conn.execute(
            "SELECT * FROM site_dd_assessments WHERE deal_id IS NULL "
            "ORDER BY COALESCE(assessed_on, created_at) DESC, id DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM site_dd_assessments WHERE deal_id = ? "
            "ORDER BY COALESCE(assessed_on, created_at) DESC, id DESC",
            (deal_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def latest_for_deal(conn: sqlite3.Connection, deal_id: int) -> dict[str, Any] | None:
    """Backs Deal Dive's summary card. Multiple assessments per deal are
    allowed (re-inspections are real), so the card shows the most recent."""
    rows = list_assessments(conn, deal_id=deal_id)
    return rows[0] if rows else None


def count_for_deal(conn: sqlite3.Connection, deal_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM site_dd_assessments WHERE deal_id = ?", (deal_id,)
    ).fetchone()
    return row["n"] if row else 0


def update_assessment(conn: sqlite3.Connection, assessment_id: int, fields: dict[str, Any]) -> None:
    conn.execute(
        """
        UPDATE site_dd_assessments SET
            property_label = :property_label,
            assessed_on = :assessed_on,
            inspector = :inspector,
            overall_notes = :overall_notes,
            status = :status,
            updated_at = :updated_at
        WHERE id = :assessment_id
        """,
        {
            "property_label": (fields.get("property_label") or "Untitled")[:MAX_LABEL_LEN],
            "assessed_on": fields.get("assessed_on"),
            "inspector": fields.get("inspector"),
            "overall_notes": fields.get("overall_notes"),
            "status": fields.get("status") or STATUS_DRAFT,
            "updated_at": _now(),
            "assessment_id": assessment_id,
        },
    )
    conn.commit()


def delete_assessment(conn: sqlite3.Connection, assessment_id: int) -> None:
    conn.execute("DELETE FROM site_dd_items WHERE assessment_id = ?", (assessment_id,))
    conn.execute("DELETE FROM site_dd_photos WHERE assessment_id = ?", (assessment_id,))
    conn.execute("DELETE FROM site_dd_assessments WHERE id = ?", (assessment_id,))
    conn.commit()


def delete_assessments_for_deal(conn: sqlite3.Connection, deal_id: int) -> list[int]:
    """Called from Deal Dive's delete_deal so a deleted deal leaves no
    assessments behind. Returns the deleted assessment ids so the caller
    can also remove their upload directories -- the rows and the files on
    disk are separate concerns and both have to go.

    Standalone assessments (deal_id NULL) are never touched."""
    ids = [
        r["id"] for r in conn.execute(
            "SELECT id FROM site_dd_assessments WHERE deal_id = ?", (deal_id,)
        ).fetchall()
    ]
    for aid in ids:
        delete_assessment(conn, aid)
    return ids


# ── Items ────────────────────────────────────────────────────────────────

def upsert_items(conn: sqlite3.Connection, assessment_id: int,
                 responses: list[dict[str, Any]]) -> None:
    """Write the whole checklist in one transaction. ON CONFLICT keyed on
    (assessment_id, item_key) so saving the form repeatedly updates rather
    than duplicating -- item_key is the stable identity, never position."""
    now = _now()
    conn.executemany(
        """
        INSERT INTO site_dd_items (assessment_id, category_key, item_key, score, note, created_at)
        VALUES (:assessment_id, :category_key, :item_key, :score, :note, :created_at)
        ON CONFLICT(assessment_id, item_key) DO UPDATE SET
            score = excluded.score,
            note = excluded.note
        """,
        [
            {
                "assessment_id": assessment_id,
                "category_key": r["category_key"],
                "item_key": r["item_key"],
                "score": r.get("score"),
                "note": (r.get("note") or None) and r["note"][:MAX_NOTE_LEN],
                "created_at": now,
            }
            for r in responses
        ],
    )
    conn.commit()


def get_items(conn: sqlite3.Connection, assessment_id: int) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM site_dd_items WHERE assessment_id = ?", (assessment_id,)
    ).fetchall()
    return {r["item_key"]: dict(r) for r in rows}


def get_scores_map(conn: sqlite3.Connection, assessment_id: int) -> dict[str, Any]:
    """item_key -> score, the exact shape score_assessment() expects."""
    return {k: v["score"] for k, v in get_items(conn, assessment_id).items()}


# ── Photos ───────────────────────────────────────────────────────────────

def add_photo(conn: sqlite3.Connection, assessment_id: int, item_key: str | None,
              original_name: str, stored_name: str, caption: str | None) -> int:
    cur = conn.execute(
        """
        INSERT INTO site_dd_photos (assessment_id, item_key, original_name, stored_name, caption, uploaded_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (assessment_id, item_key, original_name, stored_name, caption, _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_photos(conn: sqlite3.Connection, assessment_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM site_dd_photos WHERE assessment_id = ? ORDER BY id", (assessment_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_photo(conn: sqlite3.Connection, assessment_id: int, photo_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM site_dd_photos WHERE id = ? AND assessment_id = ?", (photo_id, assessment_id)
    ).fetchone()
    return dict(row) if row else None


def delete_photo(conn: sqlite3.Connection, assessment_id: int, photo_id: int) -> None:
    conn.execute(
        "DELETE FROM site_dd_photos WHERE id = ? AND assessment_id = ?", (photo_id, assessment_id)
    )
    conn.commit()
