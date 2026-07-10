"""SQLite persistence layer for the FIRE Metrics city search dashboard.

Replaces the JSON-index approach (city_metrics_index.json etc.) with a
single SQLite database, so the running app can query and update individual
metrics without rewriting a whole-file index every time. Critically, each
metric *family* (population, income, home value, employment, climate,
crime) has its own last-updated timestamp column -- a city's population can
be refreshed today while its crime index is still from a manual upload
three months ago, and callers need to be able to show that distinction.

The database path is controlled by FIRE_METRICS_DB_PATH (falls back to a
local path under fire_metrics/output/ for development). In production this
should point at the Railway persistent volume, e.g. /data/fire_metrics.db
-- that mount point is set via the environment variable, never hardcoded
here, since it doesn't exist on a local dev machine.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parent.parent


def get_db_path() -> Path:
    configured = os.getenv("FIRE_METRICS_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    return BASE_DIR / "output" / "fire_metrics.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS cities (
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    display_name TEXT NOT NULL,
    normalized_city TEXT NOT NULL,
    normalized_display_name TEXT NOT NULL,
    search_key TEXT NOT NULL,
    include_flag INTEGER NOT NULL DEFAULT 1,
    threshold_reason TEXT,

    population_rank REAL,
    population_current REAL,
    population_growth_2020_2025 REAL,
    population_growth_recent REAL,
    landlord_friendliness_score REAL,
    landlord_friendliness_label TEXT,
    population_updated_at TEXT,

    median_income_current REAL,
    median_income_growth_2021_2024 REAL,
    median_income_growth_recent REAL,
    income_updated_at TEXT,

    median_home_value_current REAL,
    median_home_value_growth_2021_2024 REAL,
    median_home_value_growth_recent REAL,
    home_value_updated_at TEXT,

    employment_current REAL,
    employment_growth_2021_2025 REAL,
    employment_growth_recent REAL,
    employment_updated_at TEXT,

    climate_risk_score REAL,
    climate_risk_rating TEXT,
    climate_updated_at TEXT,

    crime_index_score REAL,
    crime_rating TEXT,
    density_adjusted_crime_score REAL,
    density_adjusted_crime_rating TEXT,
    crime_manual_review TEXT,
    crime_updated_at TEXT,

    PRIMARY KEY (city, state)
);

CREATE TABLE IF NOT EXISTS search_aliases (
    search_key TEXT NOT NULL,
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY (search_key, city, state)
);

CREATE TABLE IF NOT EXISTS excluded_cities (
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    normalized_city TEXT,
    normalized_key TEXT,
    latest_population REAL,
    threshold_reason TEXT,
    PRIMARY KEY (city, state)
);

CREATE TABLE IF NOT EXISTS refresh_metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


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


def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def upsert_city_identity(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    """Ensure a (city, state) row exists with its identity/search fields.

    Safe to call before any metric-family upsert -- metric upserts below
    assume the row already exists (they UPDATE, not INSERT).
    """
    count = 0
    for row in rows:
        conn.execute(
            """
            INSERT INTO cities (city, state, display_name, normalized_city, normalized_display_name, search_key)
            VALUES (:city, :state, :display_name, :normalized_city, :normalized_display_name, :search_key)
            ON CONFLICT(city, state) DO UPDATE SET
                display_name=excluded.display_name,
                normalized_city=excluded.normalized_city,
                normalized_display_name=excluded.normalized_display_name,
                search_key=excluded.search_key
            """,
            row,
        )
        conn.execute("DELETE FROM search_aliases WHERE city = :city AND state = :state", row)
        for alias in row.get("search_keys", []):
            conn.execute(
                "INSERT OR IGNORE INTO search_aliases (search_key, city, state) VALUES (?, ?, ?)",
                (alias, row["city"], row["state"]),
            )
        count += 1
    conn.commit()
    return count


# Column groups for each metric family, keyed by the family name used in
# ingest calls -- also determines which *_updated_at column gets stamped.
METRIC_FAMILY_COLUMNS = {
    "population": [
        "population_rank", "population_current", "population_growth_2020_2025",
        "population_growth_recent", "landlord_friendliness_score", "landlord_friendliness_label",
        "include_flag", "threshold_reason",
    ],
    "income": ["median_income_current", "median_income_growth_2021_2024", "median_income_growth_recent"],
    "home_value": ["median_home_value_current", "median_home_value_growth_2021_2024", "median_home_value_growth_recent"],
    "employment": ["employment_current", "employment_growth_2021_2025", "employment_growth_recent"],
    "climate": ["climate_risk_score", "climate_risk_rating"],
    "crime": [
        "crime_index_score", "crime_rating", "density_adjusted_crime_score",
        "density_adjusted_crime_rating", "crime_manual_review",
    ],
}
METRIC_FAMILY_TIMESTAMP_COLUMN = {
    "population": "population_updated_at",
    "income": "income_updated_at",
    "home_value": "home_value_updated_at",
    "employment": "employment_updated_at",
    "climate": "climate_updated_at",
    "crime": "crime_updated_at",
}


def upsert_metric_family(conn: sqlite3.Connection, family: str, rows: Iterable[dict[str, Any]], updated_at: str) -> int:
    """Update one metric family's columns + its timestamp for existing city rows.

    Rows not already present (via upsert_city_identity) are skipped -- a
    metric family should never be the first thing that creates a city row.
    """
    if family not in METRIC_FAMILY_COLUMNS:
        raise ValueError(f"Unknown metric family: {family}")

    columns = METRIC_FAMILY_COLUMNS[family]
    timestamp_col = METRIC_FAMILY_TIMESTAMP_COLUMN[family]
    set_clause = ", ".join(f"{c} = :{c}" for c in columns)

    count = 0
    for row in rows:
        params = {c: row.get(c) for c in columns}
        params["city"] = row["city"]
        params["state"] = row["state"]
        params["updated_at"] = updated_at
        cur = conn.execute(
            f"UPDATE cities SET {set_clause}, {timestamp_col} = :updated_at WHERE city = :city AND state = :state",
            params,
        )
        count += cur.rowcount
    conn.commit()
    return count


def replace_excluded_cities(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    conn.execute("DELETE FROM excluded_cities")
    count = 0
    for row in rows:
        conn.execute(
            """
            INSERT OR REPLACE INTO excluded_cities (city, state, normalized_city, normalized_key, latest_population, threshold_reason)
            VALUES (:city, :state, :normalized_city, :normalized_key, :latest_population, :threshold_reason)
            """,
            row,
        )
        count += 1
    conn.commit()
    return count


def get_metadata(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM refresh_metadata").fetchall()
    return {row["key"]: row["value"] for row in rows}


def set_metadata(conn: sqlite3.Connection, **kwargs: Any) -> None:
    for key, value in kwargs.items():
        conn.execute(
            "INSERT INTO refresh_metadata (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value) if value is not None else None),
        )
    conn.commit()


def city_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a `cities` table row into the flat dict shape city_search.py
    (find_city_match) expects -- same field names Beckett's index_builder.py
    used to produce from the JSON index, so city_search.py needs zero
    changes.
    """
    keys = row.keys()
    data = {k: row[k] for k in keys}
    data["include_flag"] = bool(data.get("include_flag"))
    return data


def fetch_all_cities(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM cities WHERE include_flag = 1").fetchall()
    result = []
    for row in rows:
        city_dict = city_row_to_dict(row)
        aliases = conn.execute(
            "SELECT search_key FROM search_aliases WHERE city = ? AND state = ?",
            (city_dict["city"], city_dict["state"]),
        ).fetchall()
        city_dict["search_keys"] = [a["search_key"] for a in aliases]
        result.append(city_dict)
    return result


def fetch_excluded_cities(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM excluded_cities").fetchall()
    return [dict(row) for row in rows]


def build_city_index_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    """Build the {"cities": [...]} dict shape city_search.find_city_match expects."""
    return {"cities": fetch_all_cities(conn)}


def build_excluded_index_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    return {"excluded": fetch_excluded_cities(conn)}
