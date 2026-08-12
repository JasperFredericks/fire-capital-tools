"""
FIRE Capital Tools - Underwriting persistence.

Three tables: a scenario (one set of assumptions), its itemized expense
lines, and its rent roll unit lines.

Nothing derived is stored. EGI, NOI, IRR, the sensitivity grid -- all
computed on read by tools/underwriting_math.py, the same discipline Site DD
applies to its scores. A stored IRR that disagrees with the inputs beneath
it is the worst failure this tool could have, and the only way to guarantee
it cannot happen is to never write one down.

Same connection/schema-init pattern as every other SQLite module here:
env-var-overridable path with a repo-relative fallback, fresh connection per
call, idempotent CREATE TABLE IF NOT EXISTS on every connect.

The database path is controlled by UNDERWRITING_DB_PATH and MUST point at
the persistent volume in production (/data/underwriting.db); the fallback
below is for local development only.

deal_id is a soft reference across database files, as in rent_comps and
site_dd -- safe because deal_dive_db.deals uses AUTOINCREMENT (a deleted
id is never reissued) and because delete_scenarios_for_deal() below is
called from Deal Dive's delete_deal().
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent

MAX_LABEL_LEN = 255

SCHEMA = """
CREATE TABLE IF NOT EXISTS underwriting_scenarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id INTEGER,
    name TEXT NOT NULL DEFAULT 'Base case',
    property_label TEXT NOT NULL,
    purchase_price REAL, closing_costs_pct REAL, acquisition_fee_pct REAL,
    capital_transaction_fee_pct REAL, management_fee_pct REAL,
    ltv_pct REAL,
    interest_rate_pct REAL, amort_years INTEGER,
    hold_years INTEGER, exit_cap_pct REAL, selling_costs_pct REAL,
    vacancy_pct REAL, concessions_pct REAL, bad_debt_pct REAL,
    other_income_annual REAL,
    rent_growth_pct REAL, expense_growth_pct REAL,
    rentroll_source TEXT, t12_source TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS underwriting_expense_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id INTEGER NOT NULL,
    category_key TEXT, category_name TEXT,
    gl_code TEXT, label TEXT NOT NULL,
    annual_amount REAL,
    growth_pct REAL,
    is_included INTEGER NOT NULL DEFAULT 1,
    line_kind TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    -- JSON list of per-year growth rates overriding growth_pct. NULL for
    -- the overwhelming majority of lines, which is why this is a column
    -- and not a second table.
    growth_schedule TEXT
);

-- Per-year overrides for the scenario-level income assumptions. A
-- scenario with no rows here runs on its flat rates, which is what makes
-- the per-year feature opt-in and every existing scenario unchanged.
CREATE TABLE IF NOT EXISTS underwriting_assumption_years (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id INTEGER NOT NULL,
    year INTEGER NOT NULL,
    vacancy_pct REAL, concessions_pct REAL,
    bad_debt_pct REAL, rent_growth_pct REAL
);

CREATE TABLE IF NOT EXISTS underwriting_unit_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id INTEGER NOT NULL,
    unit TEXT, unit_type TEXT, sqft REAL, status TEXT,
    in_place_rent REAL, market_rent REAL,
    lease_start TEXT, lease_end TEXT
);

-- Loans are a list, not columns on the scenario: a debt stack has an
-- arbitrary number of tranches, and widening the scenario row for a
-- second mortgage would need widening again for a third.
--
-- A scenario with no rows here is in single-loan mode and is financed by
-- its own ltv_pct/interest_rate_pct/amort_years exactly as before, which
-- is what makes multi-loan opt-in rather than a migration.
CREATE TABLE IF NOT EXISTS underwriting_loans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id INTEGER NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL DEFAULT 'Mortgage',
    amount REAL,
    rate_pct REAL,
    amort_years INTEGER
);

CREATE INDEX IF NOT EXISTS idx_uw_deal ON underwriting_scenarios (deal_id);
CREATE INDEX IF NOT EXISTS idx_uw_exp ON underwriting_expense_lines (scenario_id);
CREATE INDEX IF NOT EXISTS idx_uw_unit ON underwriting_unit_lines (scenario_id);
CREATE INDEX IF NOT EXISTS idx_uw_loan ON underwriting_loans (scenario_id);
CREATE INDEX IF NOT EXISTS idx_uw_years ON underwriting_assumption_years (scenario_id);
"""

SCENARIO_NUMERIC = (
    "purchase_price", "closing_costs_pct", "acquisition_fee_pct",
    "capital_transaction_fee_pct", "management_fee_pct",
    "ltv_pct", "interest_rate_pct",
    "hold_years", "exit_cap_pct", "selling_costs_pct", "vacancy_pct",
    "concessions_pct", "bad_debt_pct", "other_income_annual",
    "rent_growth_pct", "expense_growth_pct", "amort_years",
)


def get_db_path() -> Path:
    configured = os.environ.get("UNDERWRITING_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    return BASE_DIR / "underwriting.db"


# Columns added after the first release. CREATE TABLE IF NOT EXISTS does
# nothing to a table that already exists, so a new column needs an explicit
# ALTER on every existing database -- without this, a scenario saved before
# the upgrade would raise "no such column" on read.
_SCENARIO_ADDED_COLUMNS = (
    ("acquisition_fee_pct", "REAL"),
    ("capital_transaction_fee_pct", "REAL"),
    ("management_fee_pct", "REAL"),
)


_EXPENSE_ADDED_COLUMNS = (
    ("growth_schedule", "TEXT"),
)


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(underwriting_scenarios)")}
    for name, coltype in _SCENARIO_ADDED_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE underwriting_scenarios ADD COLUMN {name} {coltype}")
    existing_exp = {row[1] for row in conn.execute(
        "PRAGMA table_info(underwriting_expense_lines)")}
    for name, coltype in _EXPENSE_ADDED_COLUMNS:
        if name not in existing_exp:
            conn.execute(f"ALTER TABLE underwriting_expense_lines ADD COLUMN {name} {coltype}")
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


# ── Scenarios ────────────────────────────────────────────────────────────

def create_scenario(conn, fields: dict[str, Any]) -> int:
    now = _now()
    payload = {k: fields.get(k) for k in SCENARIO_NUMERIC}
    payload.update({
        "deal_id": fields.get("deal_id"),
        "name": (fields.get("name") or "Base case")[:MAX_LABEL_LEN],
        "property_label": (fields.get("property_label") or "Untitled")[:MAX_LABEL_LEN],
        "rentroll_source": fields.get("rentroll_source"),
        "t12_source": fields.get("t12_source"),
        "notes": fields.get("notes"),
        "created_at": now, "updated_at": now,
    })
    cols = ", ".join(payload)
    binds = ", ".join(f":{k}" for k in payload)
    cur = conn.execute(f"INSERT INTO underwriting_scenarios ({cols}) VALUES ({binds})", payload)
    conn.commit()
    return cur.lastrowid


def get_scenario(conn, scenario_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM underwriting_scenarios WHERE id = ?", (scenario_id,)).fetchone()
    return dict(row) if row else None


def list_scenarios(conn, deal_id: int | None = None, all_scopes: bool = False) -> list[dict[str, Any]]:
    if all_scopes:
        rows = conn.execute("SELECT * FROM underwriting_scenarios ORDER BY id DESC").fetchall()
    elif deal_id is None:
        rows = conn.execute(
            "SELECT * FROM underwriting_scenarios WHERE deal_id IS NULL ORDER BY id DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM underwriting_scenarios WHERE deal_id = ? ORDER BY id DESC",
            (deal_id,)).fetchall()
    return [dict(r) for r in rows]


def count_for_deal(conn, deal_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM underwriting_scenarios WHERE deal_id = ?", (deal_id,)).fetchone()
    return row["n"] if row else 0


def update_scenario(conn, scenario_id: int, fields: dict[str, Any]) -> None:
    payload = {k: fields.get(k) for k in SCENARIO_NUMERIC}
    payload.update({
        "name": (fields.get("name") or "Base case")[:MAX_LABEL_LEN],
        "property_label": (fields.get("property_label") or "Untitled")[:MAX_LABEL_LEN],
        "notes": fields.get("notes"),
        "updated_at": _now(), "scenario_id": scenario_id,
    })
    sets = ", ".join(f"{k} = :{k}" for k in payload if k != "scenario_id")
    conn.execute(f"UPDATE underwriting_scenarios SET {sets} WHERE id = :scenario_id", payload)
    conn.commit()


def delete_scenario(conn, scenario_id: int) -> None:
    conn.execute("DELETE FROM underwriting_expense_lines WHERE scenario_id = ?", (scenario_id,))
    conn.execute("DELETE FROM underwriting_unit_lines WHERE scenario_id = ?", (scenario_id,))
    conn.execute("DELETE FROM underwriting_loans WHERE scenario_id = ?", (scenario_id,))
    conn.execute("DELETE FROM underwriting_assumption_years WHERE scenario_id = ?", (scenario_id,))
    conn.execute("DELETE FROM underwriting_scenarios WHERE id = ?", (scenario_id,))
    conn.commit()


def delete_scenarios_for_deal(conn, deal_id: int) -> list[int]:
    """Called from Deal Dive's delete_deal so a removed deal leaves no
    scenarios behind. Standalone scenarios (deal_id NULL) are untouched."""
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM underwriting_scenarios WHERE deal_id = ?", (deal_id,)).fetchall()]
    for sid in ids:
        delete_scenario(conn, sid)
    return ids


# ── Expense lines ────────────────────────────────────────────────────────

def replace_expense_lines(conn, scenario_id: int, lines: list[dict[str, Any]]) -> None:
    """Wholesale replace. An expense set is edited as a unit -- a T12 re-import
    should not leave orphans from the previous chart of accounts."""
    conn.execute("DELETE FROM underwriting_expense_lines WHERE scenario_id = ?", (scenario_id,))
    conn.executemany(
        """INSERT INTO underwriting_expense_lines
           (scenario_id, category_key, category_name, gl_code, label,
            annual_amount, growth_pct, is_included, line_kind, sort_order,
            growth_schedule)
           VALUES (:scenario_id,:category_key,:category_name,:gl_code,:label,
                   :annual_amount,:growth_pct,:is_included,:line_kind,:sort_order,
                   :growth_schedule)""",
        [{"scenario_id": scenario_id,
          "category_key": l.get("category_key"), "category_name": l.get("category_name"),
          "gl_code": l.get("gl_code"), "label": l.get("label") or "Expense",
          "annual_amount": l.get("annual_amount"), "growth_pct": l.get("growth_pct"),
          "is_included": 1 if l.get("is_included") else 0,
          "line_kind": l.get("line_kind"), "sort_order": idx,
          "growth_schedule": l.get("growth_schedule")}
         for idx, l in enumerate(lines)])
    conn.commit()


def list_expense_lines(conn, scenario_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM underwriting_expense_lines WHERE scenario_id = ? ORDER BY sort_order, id",
        (scenario_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["is_included"] = bool(d["is_included"])
        out.append(d)
    return out


# ── Unit lines ───────────────────────────────────────────────────────────

def replace_unit_lines(conn, scenario_id: int, units: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM underwriting_unit_lines WHERE scenario_id = ?", (scenario_id,))
    conn.executemany(
        """INSERT INTO underwriting_unit_lines
           (scenario_id, unit, unit_type, sqft, status, in_place_rent, market_rent,
            lease_start, lease_end)
           VALUES (:scenario_id,:unit,:unit_type,:sqft,:status,:in_place_rent,:market_rent,
                   :lease_start,:lease_end)""",
        [{"scenario_id": scenario_id, "unit": u.get("unit"), "unit_type": u.get("unit_type"),
          "sqft": u.get("sqft"), "status": u.get("status"),
          "in_place_rent": u.get("in_place_rent"), "market_rent": u.get("market_rent"),
          "lease_start": u.get("lease_start"), "lease_end": u.get("lease_end")}
         for u in units])
    conn.commit()


def list_unit_lines(conn, scenario_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM underwriting_unit_lines WHERE scenario_id = ? ORDER BY id",
        (scenario_id,)).fetchall()
    return [dict(r) for r in rows]


# ── Loans ────────────────────────────────────────────────────────────────
#
# An empty list means single-loan mode -- see the schema comment. Nothing
# here writes a default row, because creating one would silently convert
# every existing scenario to multi-loan mode.

def list_loans(conn, scenario_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM underwriting_loans WHERE scenario_id = ? ORDER BY sort_order, id",
        (scenario_id,)).fetchall()
    return [dict(r) for r in rows]


# ── Per-year assumptions ─────────────────────────────────────────────────
#
# No rows means flat rates. Nothing writes a default row, because seeding
# one per year would convert every scenario to a scheduled one and make
# "has an override" unanswerable.

def list_assumption_years(conn, scenario_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM underwriting_assumption_years WHERE scenario_id = ? ORDER BY year",
        (scenario_id,)).fetchall()
    return [dict(r) for r in rows]


def replace_loans(conn, scenario_id: int, loans: list[dict[str, Any]]) -> None:
    """Rewrite the whole stack for a scenario.

    Whole-list replacement rather than per-row updates, the same shape as
    replace_expense_lines: the Loans form posts the entire stack, and a
    row the user deleted has to disappear rather than linger.
    """
    conn.execute("DELETE FROM underwriting_loans WHERE scenario_id = ?", (scenario_id,))
    conn.executemany(
        """INSERT INTO underwriting_loans
           (scenario_id, sort_order, name, amount, rate_pct, amort_years)
           VALUES (:scenario_id,:sort_order,:name,:amount,:rate_pct,:amort_years)""",
        [{"scenario_id": scenario_id,
          "sort_order": l.get("sort_order", i),
          "name": (str(l.get("name") or f"Loan {i + 1}")[:MAX_LABEL_LEN]).strip()
                  or f"Loan {i + 1}",
          "amount": l.get("amount"),
          "rate_pct": l.get("rate_pct"),
          "amort_years": l.get("amort_years")}
         for i, l in enumerate(loans)])
    conn.commit()


def delete_loans_for_scenario(conn, scenario_id: int) -> None:
    conn.execute("DELETE FROM underwriting_loans WHERE scenario_id = ?", (scenario_id,))
    conn.commit()


def replace_assumption_years(conn, scenario_id: int, rows: list[dict[str, Any]]) -> None:
    """Rewrite the whole schedule. A row with every field blank is dropped
    rather than stored, so clearing the form clears the schedule."""
    conn.execute("DELETE FROM underwriting_assumption_years WHERE scenario_id = ?",
                 (scenario_id,))
    keep = [r for r in rows
            if any(r.get(f) is not None for f in
                   ("vacancy_pct", "concessions_pct", "bad_debt_pct", "rent_growth_pct"))]
    conn.executemany(
        """INSERT INTO underwriting_assumption_years
           (scenario_id, year, vacancy_pct, concessions_pct, bad_debt_pct, rent_growth_pct)
           VALUES (:scenario_id,:year,:vacancy_pct,:concessions_pct,:bad_debt_pct,:rent_growth_pct)""",
        [{"scenario_id": scenario_id, "year": int(r["year"]),
          "vacancy_pct": r.get("vacancy_pct"), "concessions_pct": r.get("concessions_pct"),
          "bad_debt_pct": r.get("bad_debt_pct"), "rent_growth_pct": r.get("rent_growth_pct")}
         for r in keep])
    conn.commit()
