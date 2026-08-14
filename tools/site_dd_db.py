"""
FIRE Capital Tools - Site DD persistence.

The rebuilt model: an assessment (one site visit), the areas within it,
the rooms within those, the findings recorded against them, and the media
attached to those findings. Summaries are never stored -- they are
computed on read by tools/site_dd_conditions.summarize(), so a stored
figure can never drift out of step with the findings behind it.

Two tables from the first version, site_dd_items and site_dd_photos, are
superseded and left in place rather than dropped. See the comment above
them for why; nothing reads or writes them.

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

_FINDINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS site_dd_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id INTEGER NOT NULL,
    area_id INTEGER,
    room_id INTEGER,
    scope TEXT NOT NULL DEFAULT 'property',
    category_key TEXT,
    item_key TEXT NOT NULL,
    -- Which one of this item. A unit can have two smoke alarms and a
    -- bathroom two sinks, each with its own condition, note and photos.
    -- Numbered from 1 and assigned automatically; instance_label is the
    -- optional free text that replaces the number on screen ("hallway"
    -- reads better than "#2" six weeks later).
    instance_no INTEGER NOT NULL DEFAULT 1,
    instance_label TEXT,
    condition TEXT,
    -- A categorical fact about the item that is NOT a condition: the
    -- flooring is vinyl, the dishwasher is a hookup with no machine in
    -- it, the smoke alarm is missing. Branch 1 assumed the condition
    -- column would carry the room checklists unchanged, and for genuine
    -- conditions it does -- but "hookup only" and "missing" are presence
    -- facts, and forcing them onto a wear scale would mean recording
    -- "Replace" for an appliance that was never there.
    detail TEXT,
    note TEXT,
    quantity REAL,
    measure TEXT,                              -- 'ea' | 'sqft' | 'lf' ...
    created_at TEXT NOT NULL
);

"""

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

-- SUPERSEDED 2026-08-13 by site_dd_findings. Left in place, not dropped:
-- an idempotent init_schema() runs on every connection, so a DROP here
-- would fire every time forever -- including against a restored backup or
-- a future branch that reintroduces writes. Nothing reads or writes these
-- two tables any more; they hold 32 rows of scripted verification data
-- and no real inspection.
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
-- ── The rebuilt model ────────────────────────────────────────────────
--
-- property -> area -> room -> finding, with media hanging off findings.
-- Branch 1 populates the property scope only: one implicit "whole
-- property" context with findings whose room_id is NULL. Areas and rooms
-- are created here rather than in Branch 2 so the schema does not need
-- revisiting when unit-by-unit inspection lands on top of it.

CREATE TABLE IF NOT EXISTS site_dd_areas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'common',       -- 'unit' | 'common'
    label TEXT NOT NULL,
    status TEXT,                               -- occupied | vacant | down
    sort_order INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TEXT NOT NULL
);

-- sort_order is the whole answer to "click kitchen and it comes first".
-- The order rooms are added IS the order they are walked, stored per
-- area because a corner unit and a studio do not flow the same way. No
-- template, no configuration screen, no versioning problem -- a column.
CREATE TABLE IF NOT EXISTS site_dd_rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    area_id INTEGER NOT NULL,
    room_type TEXT NOT NULL,
    label TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- One row per inspected item. room_id is NULL for property-scope and
-- area-scope findings, which is what makes the property checklist and a
-- future bathroom checklist the same kind of record.
--
-- `condition` is a string on the five-state scale, NOT the old 1-5
-- integer. The two are never mixed: site_dd_conditions.is_valid()
-- rejects integers outright rather than translating them, because a
-- stored 2 meant "Poor" on a scale that no longer exists and reading it
-- as "Repair" would be inventing an inspector's opinion.
""" + _FINDINGS_SCHEMA + """

-- Built now, written in Branch 3. bytes and duration_s exist so the
-- storage question has numbers to answer it: video is the reason the
-- volume math changes, and a table that cannot report its own size
-- cannot be managed.
CREATE TABLE IF NOT EXISTS site_dd_media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id INTEGER NOT NULL,
    finding_id INTEGER,
    kind TEXT NOT NULL DEFAULT 'photo',        -- 'photo' | 'video'
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    caption TEXT,
    bytes INTEGER,
    duration_s REAL,
    -- Which item, and in which scope, this was taken for. Branch 1 built
    -- this table ahead of its use and add_media() accepted an item_key it
    -- then dropped on the floor, so every photo bucketed under "no item".
    -- Added as columns rather than inferred from finding_id because a
    -- capture is often taken before the finding row exists -- you
    -- photograph the crack, then decide it is a Replace.
    item_key TEXT,
    area_id INTEGER,
    room_id INTEGER,
    uploaded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sitedd_areas ON site_dd_areas (assessment_id);
CREATE INDEX IF NOT EXISTS idx_sitedd_rooms ON site_dd_rooms (area_id);
CREATE INDEX IF NOT EXISTS idx_sitedd_find ON site_dd_findings (assessment_id);
CREATE INDEX IF NOT EXISTS idx_sitedd_media ON site_dd_media (assessment_id);
"""



def get_db_path() -> Path:
    configured = os.environ.get("SITE_DD_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    return BASE_DIR / "site_dd.db"


# Columns added after a table first shipped. CREATE TABLE IF NOT EXISTS
# does nothing to a table that already exists, so a new column needs an
# explicit ALTER on every existing database -- without this, an assessment
# saved before the upgrade raises "no such column" on read.
_FINDING_ADDED_COLUMNS = (
    ("detail", "TEXT"),
)

# The unique key widened from (assessment, area, room, item) to include
# instance_no. SQLite cannot alter a table's constraints, and the old one
# is inline in CREATE TABLE, so an existing database has to be rebuilt --
# an ALTER adding the column alone would leave the OLD unique key in
# place and a second instance would still be refused.
#
# Guarded by inspecting the real index rather than a version flag: the
# rebuild runs once, on a database that still has the four-column key,
# and is a no-op forever after.
_FINDINGS_IDENTITY_INDEX = """
-- Identity is enforced by an expression index, NOT an inline UNIQUE.
--
-- SQLite treats NULLs as DISTINCT in a unique constraint, so the previous
-- UNIQUE(assessment_id, area_id, room_id, item_key) never fired for
-- property-scope rows, where area_id and room_id are both NULL. Every
-- save of the property checklist therefore INSERTED another 32 rows
-- instead of updating them -- measured on master: 32, then 64, then 96.
-- It went unseen because the old {item_key: row} read collapsed the
-- duplicates on the way out.
--
-- COALESCE gives the nullable columns a real value to compare, so the
-- property scope gets the same identity guarantee every other scope had.
CREATE UNIQUE INDEX IF NOT EXISTS ux_sitedd_finding_identity
    ON site_dd_findings (assessment_id, COALESCE(area_id, -1),
                         COALESCE(room_id, -1), item_key, instance_no);
"""

_FINDINGS_REBUILD_COLUMNS = (
    "assessment_id", "area_id", "room_id", "scope", "category_key",
    "item_key", "condition", "detail", "note", "quantity", "measure",
    "created_at",
)


def _needs_findings_rebuild(conn: sqlite3.Connection) -> bool:
    """True while the table still carries the old inline UNIQUE.

    Detected by looking for an auto-created unique index over item_key --
    sqlite_autoindex_* exists only for an inline constraint, and the
    replacement is a named expression index, so the two cannot be
    confused.
    """
    for idx in conn.execute("PRAGMA index_list('site_dd_findings')"):
        name, unique = idx[1], idx[2]
        if not unique or not name.startswith("sqlite_autoindex"):
            continue
        cols = [r[2] for r in conn.execute(f"PRAGMA index_info('{name}')")]
        if "item_key" in cols:
            return True
    return False


def _rebuild_findings(conn: sqlite3.Connection) -> None:
    """Recreate site_dd_findings with the wider unique key, carrying every
    existing row across as instance 1."""
    have = {row[1] for row in conn.execute("PRAGMA table_info(site_dd_findings)")}
    carried = [c for c in _FINDINGS_REBUILD_COLUMNS if c in have]
    cols = ", ".join(carried)
    # The rename carries the old auto-index with it, so it is dropped
    # along with the old table below.
    conn.execute("ALTER TABLE site_dd_findings RENAME TO site_dd_findings_old")
    conn.executescript(_FINDINGS_SCHEMA)
    # Any duplicates the NULL-scope bug already wrote are collapsed to the
    # newest row per identity -- keeping the most recent save is what the
    # upsert would have done had the constraint worked.
    conn.execute(
        f"INSERT INTO site_dd_findings ({cols}, instance_no) "
        f"SELECT {cols}, 1 FROM site_dd_findings_old WHERE id IN ("
        f"  SELECT MAX(id) FROM site_dd_findings_old "
        f"  GROUP BY assessment_id, COALESCE(area_id, -1), COALESCE(room_id, -1), item_key)")
    conn.execute("DROP TABLE site_dd_findings_old")


_MEDIA_ADDED_COLUMNS = (
    ("item_key", "TEXT"),
    ("area_id", "INTEGER"),
    ("room_id", "INTEGER"),
)


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # Order matters: the identity index names instance_no, which a legacy
    # table does not have until it has been rebuilt.
    if _needs_findings_rebuild(conn):
        _rebuild_findings(conn)
    conn.executescript(_FINDINGS_IDENTITY_INDEX)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(site_dd_findings)")}
    for name, coltype in _FINDING_ADDED_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE site_dd_findings ADD COLUMN {name} {coltype}")
    existing_media = {row[1] for row in conn.execute("PRAGMA table_info(site_dd_media)")}
    for name, coltype in _MEDIA_ADDED_COLUMNS:
        if name not in existing_media:
            conn.execute(f"ALTER TABLE site_dd_media ADD COLUMN {name} {coltype}")
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
    # Rooms hang off areas, not off the assessment, so they are cleared by
    # the area ids rather than by assessment_id -- a room whose area is gone
    # is unreachable but would still be a row.
    area_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM site_dd_areas WHERE assessment_id = ?", (assessment_id,)).fetchall()]
    for aid in area_ids:
        conn.execute("DELETE FROM site_dd_rooms WHERE area_id = ?", (aid,))
    conn.execute("DELETE FROM site_dd_areas WHERE assessment_id = ?", (assessment_id,))
    conn.execute("DELETE FROM site_dd_findings WHERE assessment_id = ?", (assessment_id,))
    conn.execute("DELETE FROM site_dd_media WHERE assessment_id = ?", (assessment_id,))
    # Superseded tables: still cleared, so deleting an assessment cannot
    # leave rows behind in them either.
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

# ── Findings ─────────────────────────────────────────────────────────────
#
# Replaces the old item responses. Same upsert discipline, keyed on the
# identity that actually distinguishes a finding: which assessment, which
# area, which room, which item. For Branch 1 area_id and room_id are always
# NULL, so the key degenerates to (assessment, item) -- exactly what the
# old table used -- and widens for free when Branch 2 adds units.

def upsert_findings(conn: sqlite3.Connection, assessment_id: int,
                    responses: list[dict[str, Any]]) -> None:
    """Write a scope's findings in one transaction.

    Repeated saves update rather than duplicate. item_key is the stable
    identity, never position, so reordering or inserting checklist items
    cannot silently reassign an existing response to a different question.
    """
    now = _now()
    conn.executemany(
        """
        INSERT INTO site_dd_findings
            (assessment_id, area_id, room_id, scope, category_key, item_key,
             instance_no, instance_label, condition, detail, note, quantity,
             measure, created_at)
        VALUES (:assessment_id, :area_id, :room_id, :scope, :category_key,
                :item_key, :instance_no, :instance_label, :condition, :detail,
                :note, :quantity, :measure, :created_at)
        ON CONFLICT(assessment_id, COALESCE(area_id, -1), COALESCE(room_id, -1),
                    item_key, instance_no) DO UPDATE SET
            instance_label = excluded.instance_label,
            condition = excluded.condition,
            detail = excluded.detail,
            note = excluded.note,
            quantity = excluded.quantity,
            measure = excluded.measure
        """,
        [
            {
                "assessment_id": assessment_id,
                "area_id": r.get("area_id"),
                "room_id": r.get("room_id"),
                "scope": r.get("scope") or "property",
                "category_key": r.get("category_key"),
                "item_key": r["item_key"],
                "instance_no": int(r.get("instance_no") or 1),
                "instance_label": (r.get("instance_label") or None),
                "condition": r.get("condition"),
                "detail": r.get("detail"),
                "note": (r.get("note") or None) and r["note"][:MAX_NOTE_LEN],
                "quantity": r.get("quantity"),
                "measure": r.get("measure"),
                "created_at": now,
            }
            for r in responses
        ],
    )
    conn.commit()


def get_findings(conn: sqlite3.Connection, assessment_id: int,
                 area_id: int | None = None,
                 room_id: int | None = None) -> dict[str, list[dict[str, Any]]]:
    """item_key -> LIST of instances, in instance order, for one scope.

    A list rather than a single row because an item can occur more than
    once: two smoke alarms, two sinks. This shape changed when instances
    landed -- the previous {item_key: row} dict silently discarded every
    instance after the first, which is a data-loss bug rather than a
    display one.

    area_id/room_id are matched with IS rather than = so that NULL (the
    property scope) selects the property rows instead of matching nothing,
    which is what `= NULL` would do.
    """
    rows = conn.execute(
        "SELECT * FROM site_dd_findings WHERE assessment_id = ? "
        "AND area_id IS ? AND room_id IS ? ORDER BY item_key, instance_no",
        (assessment_id, area_id, room_id)).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["item_key"], []).append(dict(r))
    return out


def get_conditions_map(conn: sqlite3.Connection, assessment_id: int,
                       area_id: int | None = None,
                       room_id: int | None = None) -> dict[str, list[Any]]:
    """item_key -> LIST of conditions, the shape summarize() expects.

    One entry per instance, so two sinks needing replacement count twice
    rather than collapsing into one.
    """
    return {k: [row["condition"] for row in rows]
            for k, rows in get_findings(conn, assessment_id, area_id, room_id).items()}


def list_all_findings(conn: sqlite3.Connection, assessment_id: int) -> list[dict[str, Any]]:
    """Every finding on the assessment, all scopes. Used by the export and,
    from Branch 4, by the capex hand-off."""
    rows = conn.execute(
        "SELECT * FROM site_dd_findings WHERE assessment_id = ? ORDER BY id",
        (assessment_id,)).fetchall()
    return [dict(r) for r in rows]


def next_instance_no(conn: sqlite3.Connection, assessment_id: int, item_key: str,
                     area_id: int | None, room_id: int | None) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(instance_no), 0) + 1 AS n FROM site_dd_findings "
        "WHERE assessment_id = ? AND area_id IS ? AND room_id IS ? AND item_key = ?",
        (assessment_id, area_id, room_id, item_key)).fetchone()
    return int(row["n"] or 1)


def add_instance(conn: sqlite3.Connection, assessment_id: int, item_key: str,
                 area_id: int | None, room_id: int | None,
                 scope: str = "room", category_key: str | None = None,
                 instance_label: str | None = None) -> int:
    """Append another instance of an item, with nothing recorded on it yet.

    Instance 1 is backfilled if it does not exist. The checklist always
    renders a first instance whether or not a row has been saved for it,
    so without this "Add another" on an untouched item would create
    instance 1 -- and the inspector would tap the button and watch
    nothing happen, because the row they just made is the one already on
    screen.
    """
    n = next_instance_no(conn, assessment_id, item_key, area_id, room_id)
    if n == 1:
        conn.execute(
            """INSERT INTO site_dd_findings
               (assessment_id, area_id, room_id, scope, category_key, item_key,
                instance_no, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
            (assessment_id, area_id, room_id, scope, category_key, item_key, _now()))
        n = 2
    cur = conn.execute(
        """INSERT INTO site_dd_findings
           (assessment_id, area_id, room_id, scope, category_key, item_key,
            instance_no, instance_label, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (assessment_id, area_id, room_id, scope, category_key, item_key,
         n, instance_label, _now()))
    conn.commit()
    return cur.lastrowid


def add_first_instance(conn: sqlite3.Connection, assessment_id: int, item_key: str,
                       area_id: int | None, room_id: int | None,
                       scope: str = "room",
                       category_key: str | None = None) -> int:
    """Create the empty instance 1 for an item that has none yet.

    Used when a photo arrives before any condition has been recorded, so
    the media has a real finding to attach to.
    """
    cur = conn.execute(
        """INSERT INTO site_dd_findings
           (assessment_id, area_id, room_id, scope, category_key, item_key,
            instance_no, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
        (assessment_id, area_id, room_id, scope, category_key, item_key, _now()))
    conn.commit()
    return cur.lastrowid


def delete_instance(conn: sqlite3.Connection, finding_id: int) -> None:
    """Remove one instance and detach any media pointing at it.

    Media is detached rather than deleted: a photo is evidence somebody
    took, and silently destroying it because a row was removed is a
    bigger loss than an orphaned thumbnail. It stays on the assessment
    with its finding_id cleared.
    """
    conn.execute("UPDATE site_dd_media SET finding_id = NULL WHERE finding_id = ?",
                 (finding_id,))
    conn.execute("DELETE FROM site_dd_findings WHERE id = ?", (finding_id,))
    conn.commit()


def get_finding(conn: sqlite3.Connection, finding_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM site_dd_findings WHERE id = ?",
                       (finding_id,)).fetchone()
    return dict(row) if row else None


# ── Areas and rooms (written from Branch 2; readable now) ────────────────

def list_areas(conn: sqlite3.Connection, assessment_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM site_dd_areas WHERE assessment_id = ? ORDER BY sort_order, id",
        (assessment_id,)).fetchall()
    return [dict(r) for r in rows]


def list_rooms(conn: sqlite3.Connection, area_id: int) -> list[dict[str, Any]]:
    """Rooms in walk order. sort_order is the inspector's own click order,
    which is the point -- see the schema comment."""
    rows = conn.execute(
        "SELECT * FROM site_dd_rooms WHERE area_id = ? ORDER BY sort_order, id",
        (area_id,)).fetchall()
    return [dict(r) for r in rows]


AREA_UNIT = "unit"
AREA_COMMON = "common"
AREA_KINDS = (AREA_UNIT, AREA_COMMON)

# Occupancy status. Drives Site DD Lite in a later branch, which inspects
# vacant units and common areas only, so the vocabulary is fixed here
# rather than being invented then.
AREA_OCCUPIED = "occupied"
AREA_VACANT = "vacant"
AREA_DOWN = "down"
AREA_STATUSES = (AREA_OCCUPIED, AREA_VACANT, AREA_DOWN)


def create_area(conn: sqlite3.Connection, assessment_id: int,
                fields: dict[str, Any]) -> int:
    """Add a unit or common area.

    sort_order defaults to the end of the list, so areas appear in the
    order they were added unless something reorders them explicitly.
    """
    kind = fields.get("kind")
    if kind not in AREA_KINDS:
        kind = AREA_UNIT
    status = fields.get("status")
    if status not in AREA_STATUSES:
        status = None
    row = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM site_dd_areas "
        "WHERE assessment_id = ?", (assessment_id,)).fetchone()
    cur = conn.execute(
        """INSERT INTO site_dd_areas
           (assessment_id, kind, label, status, sort_order, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (assessment_id, kind,
         (str(fields.get("label") or "Untitled")[:MAX_LABEL_LEN]).strip() or "Untitled",
         status, row["n"], (fields.get("notes") or None), _now()))
    conn.commit()
    return cur.lastrowid


def get_area(conn: sqlite3.Connection, area_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM site_dd_areas WHERE id = ?", (area_id,)).fetchone()
    return dict(row) if row else None


def update_area(conn: sqlite3.Connection, area_id: int, fields: dict[str, Any]) -> None:
    status = fields.get("status")
    conn.execute(
        "UPDATE site_dd_areas SET label = ?, status = ?, notes = ? WHERE id = ?",
        ((str(fields.get("label") or "Untitled")[:MAX_LABEL_LEN]).strip() or "Untitled",
         status if status in AREA_STATUSES else None,
         (fields.get("notes") or None), area_id))
    conn.commit()


def delete_area(conn: sqlite3.Connection, area_id: int) -> None:
    """Remove an area, its rooms, and every finding recorded in them.

    Findings are cleared by area_id rather than by room, so a finding
    recorded at unit scope (room_id NULL) goes too -- otherwise deleting a
    unit would leave its smoke-alarm answers behind with nothing to
    attach them to.
    """
    conn.execute("DELETE FROM site_dd_rooms WHERE area_id = ?", (area_id,))
    conn.execute("DELETE FROM site_dd_findings WHERE area_id = ?", (area_id,))
    conn.execute("DELETE FROM site_dd_areas WHERE id = ?", (area_id,))
    conn.commit()


def create_room(conn: sqlite3.Connection, area_id: int, room_type: str,
                label: str | None = None) -> int:
    """Append a room to an area.

    THE ORDER ROOMS ARE ADDED IS THE ORDER THEY ARE WALKED. sort_order is
    assigned from the current maximum, so tapping Kitchen first puts the
    kitchen first -- which is the entire feature. Nothing sorts rooms
    alphabetically or by type anywhere, deliberately.
    """
    row = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM site_dd_rooms "
        "WHERE area_id = ?", (area_id,)).fetchone()
    cur = conn.execute(
        """INSERT INTO site_dd_rooms (area_id, room_type, label, sort_order, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (area_id, room_type,
         (str(label)[:MAX_LABEL_LEN].strip() if label else None),
         row["n"], _now()))
    conn.commit()
    return cur.lastrowid


def get_room(conn: sqlite3.Connection, room_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM site_dd_rooms WHERE id = ?", (room_id,)).fetchone()
    return dict(row) if row else None


def delete_room(conn: sqlite3.Connection, room_id: int) -> None:
    conn.execute("DELETE FROM site_dd_findings WHERE room_id = ?", (room_id,))
    conn.execute("DELETE FROM site_dd_rooms WHERE id = ?", (room_id,))
    conn.commit()


def copy_layout(conn: sqlite3.Connection, from_area_id: int, to_area_id: int) -> int:
    """Copy one unit's room sequence onto another. Returns rooms copied.

    THE LAYOUT COPIES; THE FINDINGS DO NOT.

    That distinction is the whole point. Two units may have the same three
    rooms in the same order and be in completely different condition, and
    copying an inspection from one to the other would be fabricating an
    observation nobody made. Only room_type, label and sort_order move.

    The target's existing rooms are replaced rather than appended to, so
    copying twice does not produce six rooms. Any findings already recorded
    against the replaced rooms go with them -- which is why the UI only
    offers this on a unit with no findings yet.
    """
    existing = conn.execute(
        "SELECT id FROM site_dd_rooms WHERE area_id = ?", (to_area_id,)).fetchall()
    for r in existing:
        conn.execute("DELETE FROM site_dd_findings WHERE room_id = ?", (r["id"],))
    conn.execute("DELETE FROM site_dd_rooms WHERE area_id = ?", (to_area_id,))

    source = conn.execute(
        "SELECT room_type, label, sort_order FROM site_dd_rooms "
        "WHERE area_id = ? ORDER BY sort_order, id", (from_area_id,)).fetchall()
    now = _now()
    conn.executemany(
        """INSERT INTO site_dd_rooms (area_id, room_type, label, sort_order, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        [(to_area_id, r["room_type"], r["label"], i, now)
         for i, r in enumerate(source)])
    conn.commit()
    return len(source)


def area_finding_count(conn: sqlite3.Connection, area_id: int) -> int:
    """How many findings have been recorded anywhere in this area, at any
    scope. Used to decide whether copy-layout is still safe to offer."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM site_dd_findings WHERE area_id = ? "
        "AND condition IS NOT NULL", (area_id,)).fetchone()
    return int(row["n"] or 0)


# ── Media ────────────────────────────────────────────────────────────────
#
# Photos moved onto site_dd_media with the rest of the rebuild rather than
# being left on the superseded site_dd_photos table. Leaving them straddling
# the old schema while findings moved would mean two sources of truth for
# "what is attached to this assessment", and Branch 3 would have had to
# migrate them anyway -- at which point real photos would exist to lose.
#
# kind is 'photo' for everything written today. Video arrives in Branch 3
# and needs no schema change: bytes and duration_s are already here.

MEDIA_PHOTO = "photo"
MEDIA_VIDEO = "video"


def add_media(conn: sqlite3.Connection, assessment_id: int, item_key: str | None,
              original_name: str, stored_name: str, caption: str | None,
              kind: str = MEDIA_PHOTO, finding_id: int | None = None,
              size_bytes: int | None = None, duration_s: float | None = None,
              area_id: int | None = None, room_id: int | None = None) -> int:
    cur = conn.execute(
        """
        INSERT INTO site_dd_media
            (assessment_id, finding_id, kind, original_name, stored_name,
             caption, bytes, duration_s, item_key, area_id, room_id, uploaded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (assessment_id, finding_id, kind, original_name, stored_name,
         caption, size_bytes, duration_s, item_key, area_id, room_id, _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_media(conn: sqlite3.Connection, assessment_id: int,
               kind: str | None = None) -> list[dict[str, Any]]:
    if kind is None:
        rows = conn.execute(
            "SELECT * FROM site_dd_media WHERE assessment_id = ? ORDER BY id",
            (assessment_id,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM site_dd_media WHERE assessment_id = ? AND kind = ? ORDER BY id",
            (assessment_id, kind)).fetchall()
    return [dict(r) for r in rows]


def list_media_for_scope(conn: sqlite3.Connection, assessment_id: int,
                         area_id: int | None = None,
                         room_id: int | None = None) -> list[dict[str, Any]]:
    """Media captured in one scope. IS rather than = so NULL (the property
    scope) selects the property rows instead of matching nothing."""
    rows = conn.execute(
        "SELECT * FROM site_dd_media WHERE assessment_id = ? "
        "AND area_id IS ? AND room_id IS ? ORDER BY id",
        (assessment_id, area_id, room_id)).fetchall()
    return [dict(r) for r in rows]


def media_totals(conn: sqlite3.Connection) -> dict[str, Any]:
    """Storage used by Site DD media across every assessment.

    Exists because video changes the storage math in a way photos never
    did: at 40 MB a clip, the production volume holds about 115 of them.
    A footprint nobody can see is one nobody checks until it is full.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(bytes), 0) AS b FROM site_dd_media"
    ).fetchone()
    photos = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(bytes), 0) AS b "
        "FROM site_dd_media WHERE kind = ?", (MEDIA_PHOTO,)).fetchone()
    videos = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(bytes), 0) AS b "
        "FROM site_dd_media WHERE kind = ?", (MEDIA_VIDEO,)).fetchone()
    return {
        "count": int(row["n"] or 0), "bytes": int(row["b"] or 0),
        "photo_count": int(photos["n"] or 0), "photo_bytes": int(photos["b"] or 0),
        "video_count": int(videos["n"] or 0), "video_bytes": int(videos["b"] or 0),
    }


def get_media(conn: sqlite3.Connection, assessment_id: int,
              media_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM site_dd_media WHERE id = ? AND assessment_id = ?",
        (media_id, assessment_id)).fetchone()
    return dict(row) if row else None


def delete_media(conn: sqlite3.Connection, assessment_id: int, media_id: int) -> None:
    conn.execute("DELETE FROM site_dd_media WHERE id = ? AND assessment_id = ?",
                 (media_id, assessment_id))
    conn.commit()


def media_bytes_for_assessment(conn: sqlite3.Connection, assessment_id: int) -> int:
    """Total stored bytes. Exists from Branch 1 because the storage
    question is the one that decides whether video is viable at all, and a
    figure nobody can query is a figure nobody will check."""
    row = conn.execute(
        "SELECT COALESCE(SUM(bytes), 0) AS n FROM site_dd_media WHERE assessment_id = ?",
        (assessment_id,)).fetchone()
    return int(row["n"] or 0)
