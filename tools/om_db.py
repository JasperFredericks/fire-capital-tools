"""
FIRE Capital Tools - OM document and extraction storage.

Two tables, deliberately separate.

    om_documents     a PDF somebody uploaded against a scenario
    om_extractions   what one prompt version read out of one file

They are split because the cache belongs to the FILE, not to the
scenario. The same OM uploaded to a second scenario -- comparing two
structures on one property, which is the whole reason scenarios exist --
must not spend a second time. So om_extractions is keyed on
(file_sha256, prompt_version) with no scenario in it at all, and any
document with matching bytes reads the same stored summary.

Storage path follows the existing per-endpoint pattern: the PDF itself
lives under UPLOAD_FOLDER/underwriting/<scenario_id>/, on the /data
volume, and inherits the delete-cascade that already removes that
directory when a scenario is deleted.

Same persistence discipline as every other store here: the path comes
from an env var pointing at /data, with a local fallback for development.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ENV_VAR = "UNDERWRITING_DB_PATH"

SCHEMA = """
CREATE TABLE IF NOT EXISTS om_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id INTEGER NOT NULL,
    file_sha256 TEXT NOT NULL,
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    bytes INTEGER,
    page_count INTEGER,
    -- JSON lists, so the page arithmetic done at upload time is not
    -- recomputed differently later by a caller that guessed at the cap.
    pages_used TEXT,
    pages_skipped TEXT,
    unreadable_pages TEXT,
    uploaded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_om_doc_scenario
    ON om_documents (scenario_id);

CREATE TABLE IF NOT EXISTS om_extractions (
    -- No scenario_id on purpose. See the module note: the cache is the
    -- file's, so re-uploading the same OM anywhere costs nothing.
    file_sha256 TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    model TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    pages_used TEXT,
    skipped_note TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (file_sha256, prompt_version)
);
"""


def get_db_path() -> Path:
    configured = os.environ.get(ENV_VAR)
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent.parent / "data" / "underwriting.db"


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
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def add_document(conn, scenario_id: int, *, file_sha256: str,
                 original_name: str, stored_name: str, bytes_: int,
                 page_count: int, pages_used: list[int],
                 pages_skipped: list[int],
                 unreadable_pages: list[int]) -> int:
    cur = conn.execute(
        """INSERT INTO om_documents
           (scenario_id, file_sha256, original_name, stored_name, bytes,
            page_count, pages_used, pages_skipped, unreadable_pages,
            uploaded_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (scenario_id, file_sha256, original_name, stored_name, bytes_,
         page_count, json.dumps(pages_used), json.dumps(pages_skipped),
         json.dumps(unreadable_pages), _now()))
    conn.commit()
    return int(cur.lastrowid)


def _document_row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for key in ("pages_used", "pages_skipped", "unreadable_pages"):
        try:
            out[key] = json.loads(out.get(key) or "[]")
        except (TypeError, ValueError):
            out[key] = []
    return out


def list_documents(conn, scenario_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM om_documents WHERE scenario_id = ? "
        "ORDER BY uploaded_at DESC, id DESC", (scenario_id,)).fetchall()
    return [_document_row(r) for r in rows]


def get_document(conn, doc_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM om_documents WHERE id = ?",
                       (doc_id,)).fetchone()
    return _document_row(row) if row else None


def delete_document(conn, doc_id: int) -> None:
    """Removes the document row only.

    The extraction is deliberately left behind: it is keyed on the file's
    bytes, so deleting one scenario's copy of an OM must not make another
    scenario's copy re-spend to read the same document again.
    """
    conn.execute("DELETE FROM om_documents WHERE id = ?", (doc_id,))
    conn.commit()


def save_extraction(conn, *, file_sha256: str, prompt_version: str,
                    summary: dict[str, Any], model: str,
                    prompt_tokens: int, completion_tokens: int,
                    pages_used: list[int], skipped_note: str) -> None:
    conn.execute(
        """INSERT INTO om_extractions
           (file_sha256, prompt_version, summary_json, model, prompt_tokens,
            completion_tokens, pages_used, skipped_note, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT (file_sha256, prompt_version) DO UPDATE SET
             summary_json = excluded.summary_json,
             model = excluded.model,
             prompt_tokens = excluded.prompt_tokens,
             completion_tokens = excluded.completion_tokens,
             pages_used = excluded.pages_used,
             skipped_note = excluded.skipped_note,
             created_at = excluded.created_at""",
        (file_sha256, prompt_version, json.dumps(summary), model,
         prompt_tokens, completion_tokens, json.dumps(pages_used),
         skipped_note, _now()))
    conn.commit()


def get_extraction(conn, file_sha256: str,
                   prompt_version: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM om_extractions WHERE file_sha256 = ? "
        "AND prompt_version = ?", (file_sha256, prompt_version)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["summary"] = json.loads(out.pop("summary_json"))
    try:
        out["pages_used"] = json.loads(out.get("pages_used") or "[]")
    except (TypeError, ValueError):
        out["pages_used"] = []
    return out


def storage_status() -> dict[str, Any]:
    configured = bool(os.environ.get(ENV_VAR))
    return {
        "path": str(get_db_path()),
        "configured": configured,
        "persistent": configured and str(get_db_path()).startswith("/data"),
        "env_var": ENV_VAR,
    }
