"""
FIRE Capital Tools - Investor Report notetaker storage.

The transcript pool, the property aliases that make matching work, and
the generated investor updates. Same connection/schema pattern as every
other SQLite module here: env-var-overridable path with a local fallback,
fresh connection per call, idempotent CREATE TABLE IF NOT EXISTS.

THE POOL IS A POOL, NOT AN UPLOAD TABLE

Transcripts arrive today by Michelle exporting them from Fathom or Otter
and uploading the file. A later version may pull them from a service API
instead. Both are ways of FILLING THE SAME POOL, so the table records
where a transcript came from (`source`) and an optional foreign id
(`external_id`) rather than assuming a file was uploaded:

    source        'fathom' | 'otter' | 'unspecified'   (upload today)
                  a future API mode adds its own value
    external_id   the service's own id, when there is one; NULL for an
                  upload
    stored_name   the file on disk, NULL for a transcript that arrived
                  as text with no file behind it

Nothing downstream -- matching, the date filter, synthesis -- reads any
of those three. They exist so that adding an API source is a new writer
against this table rather than a second table and a second pipeline.

WHY THE TEXT IS STORED AS WELL AS THE FILE

The file is kept because it is what Michelle actually uploaded and the
thing to go back to. The extracted text is stored because matching and
synthesis both need it, and re-reading and re-decoding a file on every
match would make a property search O(disk) for no benefit. An hour of
conversation is around 50 KB; the pool is small and this is cheap.

PROPERTY KEYS, AND WHY THEY ARE NOT DEAL IDS

A transcript is matched to a property_key, not a deal_id. Deal Dive has
two deals, both address-only, and the one property with a real building
name -- Eagle Rock Apartments -- exists only as free text on an
Underwriting scenario. Keying on deal_id would make that property
unmatchable.

So the key is a string with a documented shape:

    deal:1                        a real Deal Dive record
    label:eagle rock apartments   a property known only by a label

If Eagle Rock later becomes a Deal Dive record, its transcripts keep
working under the old key until someone repoints them, and
`properties.merge_key()` is where that would happen. A missing deal
record is a data gap, not a reason to refuse to match.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent

SOURCE_FATHOM = "fathom"
SOURCE_OTTER = "otter"
SOURCE_UNSPECIFIED = "unspecified"
SOURCES = (SOURCE_FATHOM, SOURCE_OTTER, SOURCE_UNSPECIFIED)

SOURCE_LABELS = {
    SOURCE_FATHOM: "Fathom",
    SOURCE_OTTER: "Otter",
    SOURCE_UNSPECIFIED: "Not stated",
}

# How a transcript came to be attached to a property. Recorded because
# "the tool guessed this" and "a person said so" are different claims and
# the page shows them differently.
MATCH_AUTO = "auto"
MATCH_MANUAL = "manual"
MATCH_UNASSIGNED = "unassigned"
MATCH_AMBIGUOUS = "ambiguous"
MATCH_METHODS = (MATCH_AUTO, MATCH_MANUAL, MATCH_UNASSIGNED, MATCH_AMBIGUOUS)

MAX_TITLE_LEN = 255
MAX_ALIAS_LEN = 120

SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Where it came from. 'fathom'/'otter'/'unspecified' today; an API
    -- mode would add its own value without changing anything that reads
    -- this table.
    source TEXT NOT NULL DEFAULT 'unspecified',
    external_id TEXT,
    title TEXT,
    original_name TEXT,
    stored_name TEXT,
    bytes INTEGER,
    -- The date of the MEETING, not of the upload. Entered by hand: see
    -- the module note in investor_notes.py on why it is not parsed.
    transcript_date TEXT NOT NULL,
    body TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    -- The match result. property_key is NULL until something matches.
    property_key TEXT,
    property_label TEXT,
    match_method TEXT NOT NULL DEFAULT 'unassigned',
    match_evidence TEXT,                       -- JSON, shown on screen
    uploaded_at TEXT NOT NULL
);

-- What Michelle actually calls a property out loud. This does the heavy
-- lifting in matching: nobody says "one thousand one hundred and twenty
-- Jackson Street" on a call, they say "Jackson".
CREATE TABLE IF NOT EXISTS property_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_key TEXT NOT NULL,
    alias TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (property_key, alias)
);

-- A generated investor update. Investor Report had no narrative document
-- concept at all before this -- it was investors, contributions and
-- waterfalls -- so this is a new artifact rather than a column on an
-- existing one.
CREATE TABLE IF NOT EXISTS investor_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_key TEXT NOT NULL,
    property_label TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    -- property + range + the exact set of transcript ids + prompt
    -- version. Re-running the same query is free; adding a transcript to
    -- the range changes the key and correctly re-spends.
    cache_key TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT,
    sections_json TEXT NOT NULL,
    transcript_ids_json TEXT NOT NULL,
    generated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_property ON transcripts (property_key);
CREATE INDEX IF NOT EXISTS idx_notes_date ON transcripts (transcript_date);
CREATE INDEX IF NOT EXISTS idx_notes_alias ON property_aliases (property_key);
CREATE UNIQUE INDEX IF NOT EXISTS ux_update_cache ON investor_updates (cache_key);
"""


def get_db_path() -> Path:
    configured = os.environ.get("INVESTOR_NOTES_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    return BASE_DIR / "investor_notes.db"


def storage_status() -> dict[str, Any]:
    """Whether this database survives a deploy.

    Same reporting as the OpenAI counter, and for the same reason: an
    uploaded transcript that quietly disappears on the next deploy is
    worse than one that was refused.
    """
    configured = os.environ.get("INVESTOR_NOTES_DB_PATH", "").strip()
    return {"path": str(get_db_path()), "persistent": bool(configured),
            "env_var": "INVESTOR_NOTES_DB_PATH"}


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


def text_digest(body: str) -> str:
    return hashlib.sha256((body or "").encode("utf-8", "replace")).hexdigest()


# ── Transcripts ──────────────────────────────────────────────────────────

def add_transcript(conn: sqlite3.Connection, *, body: str, transcript_date: str,
                   source: str = SOURCE_UNSPECIFIED, title: str | None = None,
                   original_name: str | None = None, stored_name: str | None = None,
                   bytes_len: int | None = None,
                   external_id: str | None = None) -> int:
    cur = conn.execute(
        """INSERT INTO transcripts
           (source, external_id, title, original_name, stored_name, bytes,
            transcript_date, body, text_sha256, match_method, uploaded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (source if source in SOURCES else SOURCE_UNSPECIFIED, external_id,
         (title or "")[:MAX_TITLE_LEN] or None, original_name, stored_name,
         bytes_len, transcript_date, body, text_digest(body),
         MATCH_UNASSIGNED, _now()))
    conn.commit()
    return cur.lastrowid


def get_transcript(conn: sqlite3.Connection, tid: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM transcripts WHERE id = ?", (tid,)).fetchone()
    return dict(row) if row else None


def list_transcripts(conn: sqlite3.Connection, *, property_key: str | None = None,
                     start: str | None = None, end: str | None = None,
                     include_unassigned: bool = False) -> list[dict[str, Any]]:
    """The pool, filtered.

    Dates are compared as ISO strings, which sorts correctly because they
    are zero-padded. `include_unassigned` exists so the review screen can
    show what has NOT been matched alongside what has -- an unmatched
    transcript that is silently omitted looks like it was never uploaded.
    """
    sql = ["SELECT * FROM transcripts WHERE 1=1"]
    args: list[Any] = []
    if property_key is not None:
        if include_unassigned:
            sql.append("AND (property_key = ? OR property_key IS NULL)")
        else:
            sql.append("AND property_key = ?")
        args.append(property_key)
    if start:
        sql.append("AND transcript_date >= ?")
        args.append(start)
    if end:
        sql.append("AND transcript_date <= ?")
        args.append(end)
    sql.append("ORDER BY transcript_date, id")
    return [dict(r) for r in conn.execute(" ".join(sql), args)]


def set_match(conn: sqlite3.Connection, tid: int, *, property_key: str | None,
              property_label: str | None, method: str,
              evidence: Any = None) -> None:
    conn.execute(
        """UPDATE transcripts SET property_key = ?, property_label = ?,
           match_method = ?, match_evidence = ? WHERE id = ?""",
        (property_key, property_label,
         method if method in MATCH_METHODS else MATCH_UNASSIGNED,
         json.dumps(evidence) if evidence is not None else None, tid))
    conn.commit()


def delete_transcript(conn: sqlite3.Connection, tid: int) -> dict[str, Any] | None:
    row = get_transcript(conn, tid)
    if row:
        conn.execute("DELETE FROM transcripts WHERE id = ?", (tid,))
        conn.commit()
    return row


def evidence_of(row: dict[str, Any]) -> Any:
    raw = row.get("match_evidence")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


# ── Aliases ──────────────────────────────────────────────────────────────

class UnknownProperty(ValueError):
    """An alias was aimed at a property that does not exist."""


def add_alias(conn: sqlite3.Connection, property_key: str, alias: str,
              valid_keys: set[str] | None = None) -> bool:
    """Attach a name to a property.

    `valid_keys` is the set of keys that correspond to a real property,
    supplied by the caller because this module cannot see Deal Dive,
    Underwriting or Site DD and should not learn how to.

    It exists because an alias is only ever read by looking it up from a
    property entry: investor_notes_properties.build() attaches aliases by
    key, so a row whose key matches no entry is stored and then never
    read by anything, ever. Nothing errored, nothing appeared, and the
    alias silently did not exist. Passing the real keys turns that into a
    refusal at the point of writing.

    Omitting `valid_keys` keeps the old unchecked behaviour, which is
    what the tests that predate this use; the route passes them.
    """
    alias = " ".join((alias or "").split())[:MAX_ALIAS_LEN]
    if not alias or not property_key:
        return False
    if valid_keys is not None and property_key not in valid_keys:
        raise UnknownProperty(
            f"No property is known by the key {property_key!r}, so an alias "
            "for it would never be read. Add the property first.")
    try:
        conn.execute(
            "INSERT INTO property_aliases (property_key, alias, created_at) "
            "VALUES (?, ?, ?)", (property_key, alias, _now()))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False        # already there; not an error worth raising


def delete_alias(conn: sqlite3.Connection, alias_id: int) -> None:
    conn.execute("DELETE FROM property_aliases WHERE id = ?", (alias_id,))
    conn.commit()


def list_aliases(conn: sqlite3.Connection,
                 property_key: str | None = None) -> list[dict[str, Any]]:
    if property_key:
        rows = conn.execute(
            "SELECT * FROM property_aliases WHERE property_key = ? ORDER BY alias",
            (property_key,))
    else:
        rows = conn.execute(
            "SELECT * FROM property_aliases ORDER BY property_key, alias")
    return [dict(r) for r in rows]


def aliases_by_key(conn: sqlite3.Connection) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in list_aliases(conn):
        out.setdefault(row["property_key"], []).append(row["alias"])
    return out


# ── Updates ──────────────────────────────────────────────────────────────

def find_update(conn: sqlite3.Connection, cache_key: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM investor_updates WHERE cache_key = ?",
                       (cache_key,)).fetchone()
    return dict(row) if row else None


def save_update(conn: sqlite3.Connection, *, property_key: str,
                property_label: str, period_start: str, period_end: str,
                cache_key: str, prompt_version: str, model: str | None,
                sections: Any, transcript_ids: list[int]) -> int:
    """Store a generated update, replacing any earlier one for the same key.

    ON CONFLICT rather than an error: the same query re-run after a
    Force Refresh is a legitimate overwrite, and the cache key already
    encodes everything that would make it a different document.
    """
    cur = conn.execute(
        """INSERT INTO investor_updates
           (property_key, property_label, period_start, period_end, cache_key,
            prompt_version, model, sections_json, transcript_ids_json,
            generated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(cache_key) DO UPDATE SET
               sections_json = excluded.sections_json,
               transcript_ids_json = excluded.transcript_ids_json,
               model = excluded.model,
               generated_at = excluded.generated_at""",
        (property_key, property_label, period_start, period_end, cache_key,
         prompt_version, model, json.dumps(sections),
         json.dumps(sorted(transcript_ids)), _now()))
    conn.commit()
    return cur.lastrowid or (find_update(conn, cache_key) or {}).get("id")


def get_update(conn: sqlite3.Connection, uid: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM investor_updates WHERE id = ?",
                       (uid,)).fetchone()
    return dict(row) if row else None


def list_updates(conn: sqlite3.Connection,
                 property_key: str | None = None) -> list[dict[str, Any]]:
    if property_key:
        rows = conn.execute(
            "SELECT * FROM investor_updates WHERE property_key = ? "
            "ORDER BY generated_at DESC", (property_key,))
    else:
        rows = conn.execute(
            "SELECT * FROM investor_updates ORDER BY generated_at DESC")
    return [dict(r) for r in rows]


def delete_update(conn: sqlite3.Connection, uid: int) -> None:
    conn.execute("DELETE FROM investor_updates WHERE id = ?", (uid,))
    conn.commit()


def sections_of(row: dict[str, Any]) -> Any:
    try:
        return json.loads(row.get("sections_json") or "[]")
    except (TypeError, ValueError):
        return []


def transcript_ids_of(row: dict[str, Any]) -> list[int]:
    try:
        return list(json.loads(row.get("transcript_ids_json") or "[]"))
    except (TypeError, ValueError):
        return []
