"""
FIRE Capital Tools - Market data cache.

Backs tools/market_data_service.py. Kept in its own module (rather than
folded into the service file) for the same reason scorecard_history.py and
deal_dive_db.py are separate from their blueprints -- a persistence layer
with no dependency on any tool's own route/blueprint code, so it stays
independently reusable (this cache and the service in front of it are
built standalone specifically so a future "Rent Comps" tool can reuse them
without redoing this work).

Same connection/schema-init pattern as every other SQLite module in this
app: env-var-overridable path with a local fallback, fresh connection per
call, idempotent CREATE TABLE IF NOT EXISTS on every connect.

The database path is controlled by MARKET_DATA_DB_PATH (falls back to a
local file at the repo root for development).
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent

DEFAULT_STALENESS_DAYS = 30

# RentCast's free plan is a hard 50 requests/month with a per-request
# overage charge beyond that -- there is no "soft" version of this limit.
# The safety threshold is deliberately below the real limit (not 49) so a
# single lookup that makes more than one real RentCast call (rent estimate
# + property details, two separate requests against the same quota) can
# never push us past 50 even if the check only runs once at the start of
# that lookup.
RENTCAST_MONTHLY_FREE_LIMIT = 50
RENTCAST_MONTHLY_SAFETY_THRESHOLD = 45

# Google Places' free allowance doesn't work like RentCast's -- there's no
# single fixed number to undercut. Google retired the old pooled $200/month
# credit in March 2025; the current model is a separate free-events-per-
# month allowance *per SKU tier* (roughly 10k/month for the cheapest
# "Essentials" tier down to roughly 1k/month for "Enterprise"). The Place
# Details call this service makes requests rating, user_ratings_total, and
# reviews -- all three are officially in Google's "Atmosphere" field
# category, which Google's own Legacy API docs confirm is billed on top of
# the base request, and third-party pricing trackers place at the
# Enterprise tier (~1,000 free events/month, the smallest allowance of the
# three tiers) since Google's own pages don't publish the exact number in
# one place. Given that real uncertainty -- and given the point of this
# cap is to never repeat the surprise-charge experience that already
# happened once -- the threshold below is set to roughly 10% of that
# researched (not confirmed) ~1,000/month figure, a much larger safety
# margin proportionally than RentCast's 45/50, specifically because the
# underlying number itself is an estimate rather than a documented fact.
GOOGLE_PLACES_MONTHLY_SAFETY_THRESHOLD = 100

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_data_cache (
    address_key TEXT PRIMARY KEY,
    address TEXT NOT NULL,
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    zip TEXT,
    rentcast_json TEXT,
    google_places_json TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rentcast_usage (
    year_month TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS google_places_usage (
    year_month TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0
);
"""


def get_db_path() -> Path:
    configured = os.environ.get("MARKET_DATA_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    return BASE_DIR / "market_data_cache.db"


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


def normalize_address_key(address: str, city: str, state: str, zip_code: str | None = None) -> str:
    """Case/whitespace-insensitive key so the same address always hits the
    same cache row regardless of minor formatting differences."""
    parts = [address or "", city or "", state or "", zip_code or ""]
    return " ".join(" ".join(parts).strip().lower().split())


def get_cached(
    conn: sqlite3.Connection,
    address_key: str,
    staleness_days: int = DEFAULT_STALENESS_DAYS,
) -> dict[str, Any] | None:
    """Return the cached entry if present and not older than staleness_days,
    else None (caller should treat None as "needs a fresh API call")."""
    row = conn.execute(
        "SELECT * FROM market_data_cache WHERE address_key = ?", (address_key,)
    ).fetchone()
    if not row:
        return None

    fetched_at = datetime.datetime.fromisoformat(row["fetched_at"])
    age = datetime.datetime.utcnow() - fetched_at
    if age > datetime.timedelta(days=staleness_days):
        return None

    return {
        "address": row["address"],
        "city": row["city"],
        "state": row["state"],
        "zip": row["zip"],
        "rentcast": json.loads(row["rentcast_json"]) if row["rentcast_json"] else None,
        "google_places": json.loads(row["google_places_json"]) if row["google_places_json"] else None,
        "fetched_at": row["fetched_at"],
    }


def save_cache(
    conn: sqlite3.Connection,
    address_key: str,
    address: str,
    city: str,
    state: str,
    zip_code: str | None,
    rentcast_data: dict[str, Any] | None,
    google_data: dict[str, Any] | None,
) -> None:
    conn.execute(
        """
        INSERT INTO market_data_cache
            (address_key, address, city, state, zip, rentcast_json, google_places_json, fetched_at)
        VALUES (:address_key, :address, :city, :state, :zip, :rentcast_json, :google_places_json, :fetched_at)
        ON CONFLICT(address_key) DO UPDATE SET
            address = excluded.address,
            city = excluded.city,
            state = excluded.state,
            zip = excluded.zip,
            rentcast_json = excluded.rentcast_json,
            google_places_json = excluded.google_places_json,
            fetched_at = excluded.fetched_at
        """,
        {
            "address_key": address_key,
            "address": address,
            "city": city,
            "state": state,
            "zip": zip_code,
            "rentcast_json": json.dumps(rentcast_data) if rentcast_data is not None else None,
            "google_places_json": json.dumps(google_data) if google_data is not None else None,
            "fetched_at": datetime.datetime.utcnow().isoformat(),
        },
    )
    conn.commit()


# ── RentCast monthly usage (hard cap, no overage) ─────────────────────────

def current_year_month() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m")


def get_rentcast_usage(conn: sqlite3.Connection, year_month: str | None = None) -> int:
    """Real RentCast API calls made this month -- cache hits never
    increment this, only an actual outbound request to RentCast does."""
    year_month = year_month or current_year_month()
    row = conn.execute(
        "SELECT count FROM rentcast_usage WHERE year_month = ?", (year_month,)
    ).fetchone()
    return row["count"] if row else 0


def increment_rentcast_usage(conn: sqlite3.Connection, year_month: str | None = None) -> int:
    year_month = year_month or current_year_month()
    conn.execute(
        """
        INSERT INTO rentcast_usage (year_month, count) VALUES (?, 1)
        ON CONFLICT(year_month) DO UPDATE SET count = count + 1
        """,
        (year_month,),
    )
    conn.commit()
    return get_rentcast_usage(conn, year_month)


# ── Google Places monthly usage (hard cap, no overage) ────────────────────

def get_google_places_usage(conn: sqlite3.Connection, year_month: str | None = None) -> int:
    """Real Google Places API calls made this month -- cache hits never
    increment this, only an actual outbound request to Google does."""
    year_month = year_month or current_year_month()
    row = conn.execute(
        "SELECT count FROM google_places_usage WHERE year_month = ?", (year_month,)
    ).fetchone()
    return row["count"] if row else 0


def increment_google_places_usage(conn: sqlite3.Connection, year_month: str | None = None) -> int:
    year_month = year_month or current_year_month()
    conn.execute(
        """
        INSERT INTO google_places_usage (year_month, count) VALUES (?, 1)
        ON CONFLICT(year_month) DO UPDATE SET count = count + 1
        """,
        (year_month,),
    )
    conn.commit()
    return get_google_places_usage(conn, year_month)
