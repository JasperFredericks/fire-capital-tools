"""
FIRE Capital Tools - Investor Report persistence.

Four tables: investors (entity-level, reused across deals), their capital
contributions, a waterfall scenario carrying the terms, and its ordered
tiers.

Nothing derived is stored. Distributions, per-investor IRRs, tier flows and
the reconciliation checks are all computed on read by
tools/waterfall_math.py -- the same discipline as Site DD's scores and
Underwriting's NOI, and it matters most here: a stored distribution figure
that disagrees with the contributions and terms beneath it would be a
number telling someone they are owed the wrong amount.

investors is entity-level rather than per-deal because Michelle's LPs
recur across deals; keying them to a deal would fragment the same person
into several records and make any cross-deal view impossible.

Same connection/schema-init pattern as every other SQLite module here.
INVESTOR_REPORT_DB_PATH must point at the persistent volume in production;
the repo-relative fallback is for local development only.

deal_id is a soft cross-database reference, as in rent_comps, site_dd and
underwriting -- safe because deals uses AUTOINCREMENT and because
delete_scenarios_for_deal() is called from Deal Dive's delete_deal().
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tools import waterfall_math as wm

BASE_DIR = Path(__file__).resolve().parent.parent
MAX_NAME_LEN = 255

# Default promote split for a NEW scenario. FIRE Capital's stated
# standard is 70/30, replacing the 80/20 this shipped with.
#
# This is a default, not a rule: every scenario stores its own split and
# the form stays fully editable. Changing it cannot reach an existing
# waterfall -- stored rows carry their own promote_lp_pct/promote_gp_pct
# and their own tier rows, and nothing here rewrites them. A test asserts
# that directly.
DEFAULT_PROMOTE_LP_PCT = 70.0
DEFAULT_PROMOTE_GP_PCT = 30.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS investors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    entity_type TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS capital_contributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    investor_id INTEGER NOT NULL,
    deal_id INTEGER,
    scenario_id INTEGER,
    amount REAL NOT NULL,
    contribution_date TEXT,
    investor_class TEXT NOT NULL DEFAULT 'LP',
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS waterfall_scenarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id INTEGER NOT NULL,
    underwriting_scenario_id INTEGER NOT NULL,
    name TEXT NOT NULL DEFAULT 'Base waterfall',
    property_label TEXT,
    pref_rate_pct REAL NOT NULL DEFAULT 8.0,
    pref_convention TEXT NOT NULL DEFAULT 'accrual',
    promote_lp_pct REAL NOT NULL DEFAULT 70.0,
    promote_gp_pct REAL NOT NULL DEFAULT 30.0,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS waterfall_tiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    waterfall_scenario_id INTEGER NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    tier_type TEXT NOT NULL,
    hurdle_rate_pct REAL,
    lp_share_pct REAL NOT NULL DEFAULT 100.0,
    gp_share_pct REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_ir_contrib_deal ON capital_contributions (deal_id);
CREATE INDEX IF NOT EXISTS idx_ir_contrib_inv ON capital_contributions (investor_id);
-- Named partners making up the GP. Rows here divide the promote the
-- cascade already computed; no rows means one implicit 100% bucket, which
-- is what every scenario predating this feature reports.
CREATE TABLE IF NOT EXISTS gp_partners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    waterfall_scenario_id INTEGER NOT NULL,
    investor_id INTEGER,
    name TEXT NOT NULL DEFAULT 'Partner',
    share_pct REAL,
    notes TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_ir_gp_partners ON gp_partners (waterfall_scenario_id);
CREATE INDEX IF NOT EXISTS idx_ir_scen_deal ON waterfall_scenarios (deal_id);
CREATE INDEX IF NOT EXISTS idx_ir_tiers ON waterfall_tiers (waterfall_scenario_id);
"""


def get_db_path() -> Path:
    configured = os.environ.get("INVESTOR_REPORT_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    return BASE_DIR / "investor_report.db"


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


# ── Investors ────────────────────────────────────────────────────────────

def create_investor(conn, name: str, entity_type: str | None = None,
                    notes: str | None = None) -> int:
    now = _now()
    cur = conn.execute(
        "INSERT INTO investors (name, entity_type, notes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ((name or "Investor")[:MAX_NAME_LEN], entity_type, notes, now, now))
    conn.commit()
    return cur.lastrowid


def list_investors(conn) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM investors ORDER BY name")]


def get_investor(conn, investor_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM investors WHERE id = ?", (investor_id,)).fetchone()
    return dict(row) if row else None


def delete_investor(conn, investor_id: int) -> None:
    conn.execute("DELETE FROM capital_contributions WHERE investor_id = ?", (investor_id,))
    conn.execute("DELETE FROM investors WHERE id = ?", (investor_id,))
    conn.commit()


# ── Contributions ────────────────────────────────────────────────────────

def add_contribution(conn, investor_id: int, deal_id: int, amount: float,
                     contribution_date: str | None = None,
                     investor_class: str = wm.CLASS_LP, notes: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO capital_contributions "
        "(investor_id, deal_id, scenario_id, amount, contribution_date, investor_class, notes, created_at) "
        "VALUES (?, ?, NULL, ?, ?, ?, ?, ?)",
        (investor_id, deal_id, amount, contribution_date, investor_class, notes, _now()))
    conn.commit()
    return cur.lastrowid


def list_contributions(conn, deal_id: int) -> list[dict[str, Any]]:
    """Contributions for a deal, joined to investor names so the waterfall
    can be run without a second lookup."""
    rows = conn.execute(
        "SELECT c.*, i.name AS name, i.entity_type AS entity_type "
        "FROM capital_contributions c JOIN investors i ON i.id = c.investor_id "
        "WHERE c.deal_id = ? ORDER BY c.id", (deal_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_contribution(conn, contribution_id: int, deal_id: int) -> None:
    conn.execute("DELETE FROM capital_contributions WHERE id = ? AND deal_id = ?",
                 (contribution_id, deal_id))
    conn.commit()


# ── Waterfall scenarios + tiers ──────────────────────────────────────────

def create_scenario(conn, fields: dict[str, Any]) -> int:
    now = _now()
    cur = conn.execute(
        """INSERT INTO waterfall_scenarios
           (deal_id, underwriting_scenario_id, name, property_label, pref_rate_pct,
            pref_convention, promote_lp_pct, promote_gp_pct, notes, created_at, updated_at)
           VALUES (:deal_id,:underwriting_scenario_id,:name,:property_label,:pref_rate_pct,
                   :pref_convention,:promote_lp_pct,:promote_gp_pct,:notes,:created_at,:updated_at)""",
        {"deal_id": fields["deal_id"],
         "underwriting_scenario_id": fields["underwriting_scenario_id"],
         "name": (fields.get("name") or "Base waterfall")[:MAX_NAME_LEN],
         "property_label": fields.get("property_label"),
         "pref_rate_pct": fields.get("pref_rate_pct", 8.0),
         "pref_convention": fields.get("pref_convention") or wm.PREF_CONVENTION_ACCRUAL,
         "promote_lp_pct": fields.get("promote_lp_pct", DEFAULT_PROMOTE_LP_PCT),
         "promote_gp_pct": fields.get("promote_gp_pct", DEFAULT_PROMOTE_GP_PCT),
         "notes": fields.get("notes"), "created_at": now, "updated_at": now})
    conn.commit()
    sid = cur.lastrowid
    replace_tiers(conn, sid, default_tiers(fields.get("promote_lp_pct", DEFAULT_PROMOTE_LP_PCT),
                                           fields.get("promote_gp_pct", DEFAULT_PROMOTE_GP_PCT)))
    return sid


def default_tiers(lp_pct: float = DEFAULT_PROMOTE_LP_PCT, gp_pct: float = DEFAULT_PROMOTE_GP_PCT) -> list[dict[str, Any]]:
    """The beta's three tiers. Stored as rows even though there is exactly
    one configuration, so adding an IRR-hurdle band later is data."""
    return [
        {"sort_order": 0, "tier_type": wm.TIER_RETURN_OF_CAPITAL,
         "hurdle_rate_pct": None, "lp_share_pct": 100.0, "gp_share_pct": 0.0},
        {"sort_order": 1, "tier_type": wm.TIER_PREF,
         "hurdle_rate_pct": None, "lp_share_pct": 100.0, "gp_share_pct": 0.0},
        {"sort_order": 2, "tier_type": wm.TIER_PROMOTE,
         "hurdle_rate_pct": None, "lp_share_pct": lp_pct, "gp_share_pct": gp_pct},
    ]


def replace_tiers(conn, scenario_id: int, tiers: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM waterfall_tiers WHERE waterfall_scenario_id = ?", (scenario_id,))
    conn.executemany(
        """INSERT INTO waterfall_tiers
           (waterfall_scenario_id, sort_order, tier_type, hurdle_rate_pct, lp_share_pct, gp_share_pct)
           VALUES (?,?,?,?,?,?)""",
        [(scenario_id, t.get("sort_order", i), t["tier_type"], t.get("hurdle_rate_pct"),
          t.get("lp_share_pct", 100.0), t.get("gp_share_pct", 0.0))
         for i, t in enumerate(tiers)])
    conn.commit()


def list_tiers(conn, scenario_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM waterfall_tiers WHERE waterfall_scenario_id = ? ORDER BY sort_order, id",
        (scenario_id,)).fetchall()
    return [dict(r) for r in rows]


# ── GP partners ──────────────────────────────────────────────────────────
#
# No rows means one implicit 100% bucket. Nothing writes a default row,
# because doing so would convert every existing scenario into a
# "configured" one and make "has a split" unanswerable.

def list_gp_partners(conn, scenario_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM gp_partners WHERE waterfall_scenario_id = ? "
        "ORDER BY sort_order, id", (scenario_id,)).fetchall()
    return [dict(r) for r in rows]


def replace_gp_partners(conn, scenario_id: int, partners: list[dict[str, Any]]) -> None:
    """Rewrite the partner set. Whole-list replacement, the same shape as
    replace_tiers: the form posts every row, and a partner the user
    removed has to disappear rather than linger."""
    conn.execute("DELETE FROM gp_partners WHERE waterfall_scenario_id = ?", (scenario_id,))
    conn.executemany(
        """INSERT INTO gp_partners
           (waterfall_scenario_id, investor_id, name, share_pct, notes, sort_order)
           VALUES (:waterfall_scenario_id,:investor_id,:name,:share_pct,:notes,:sort_order)""",
        [{"waterfall_scenario_id": scenario_id,
          "investor_id": p.get("investor_id"),
          "name": (str(p.get("name") or f"Partner {i + 1}")[:MAX_NAME_LEN]).strip()
                  or f"Partner {i + 1}",
          "share_pct": p.get("share_pct"),
          "notes": (p.get("notes") or None),
          "sort_order": p.get("sort_order", i)}
         for i, p in enumerate(partners)])
    conn.commit()


def get_scenario(conn, scenario_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM waterfall_scenarios WHERE id = ?", (scenario_id,)).fetchone()
    return dict(row) if row else None


def list_scenarios(conn, deal_id: int | None = None) -> list[dict[str, Any]]:
    if deal_id is None:
        rows = conn.execute("SELECT * FROM waterfall_scenarios ORDER BY id DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM waterfall_scenarios WHERE deal_id = ? ORDER BY id DESC",
            (deal_id,)).fetchall()
    return [dict(r) for r in rows]


def count_for_deal(conn, deal_id: int) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM waterfall_scenarios WHERE deal_id = ?",
                       (deal_id,)).fetchone()
    return row["n"] if row else 0


def update_scenario(conn, scenario_id: int, fields: dict[str, Any]) -> None:
    conn.execute(
        """UPDATE waterfall_scenarios SET name=:name, pref_rate_pct=:pref_rate_pct,
           pref_convention=:pref_convention, promote_lp_pct=:promote_lp_pct,
           promote_gp_pct=:promote_gp_pct, notes=:notes, updated_at=:updated_at
           WHERE id=:scenario_id""",
        {"name": (fields.get("name") or "Base waterfall")[:MAX_NAME_LEN],
         "pref_rate_pct": fields.get("pref_rate_pct", 8.0),
         "pref_convention": fields.get("pref_convention") or wm.PREF_CONVENTION_ACCRUAL,
         "promote_lp_pct": fields.get("promote_lp_pct", DEFAULT_PROMOTE_LP_PCT),
         "promote_gp_pct": fields.get("promote_gp_pct", DEFAULT_PROMOTE_GP_PCT),
         "notes": fields.get("notes"), "updated_at": _now(), "scenario_id": scenario_id})
    replace_tiers(conn, scenario_id,
                  default_tiers(fields.get("promote_lp_pct", DEFAULT_PROMOTE_LP_PCT),
                                fields.get("promote_gp_pct", DEFAULT_PROMOTE_GP_PCT)))
    conn.commit()


def delete_scenario(conn, scenario_id: int) -> None:
    conn.execute("DELETE FROM waterfall_tiers WHERE waterfall_scenario_id = ?", (scenario_id,))
    conn.execute("DELETE FROM gp_partners WHERE waterfall_scenario_id = ?", (scenario_id,))
    conn.execute("DELETE FROM waterfall_scenarios WHERE id = ?", (scenario_id,))
    conn.commit()


def delete_scenarios_for_deal(conn, deal_id: int) -> list[int]:
    """Called from Deal Dive's delete_deal. Removes the deal's waterfall
    scenarios and its capital contributions. Investors themselves survive --
    they are entity-level and belong to other deals too."""
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM waterfall_scenarios WHERE deal_id = ?", (deal_id,)).fetchall()]
    for sid in ids:
        delete_scenario(conn, sid)
    conn.execute("DELETE FROM capital_contributions WHERE deal_id = ?", (deal_id,))
    conn.commit()
    return ids
