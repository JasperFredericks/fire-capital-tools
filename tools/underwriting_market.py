"""
FIRE Capital Tools - Underwriting market context.

Reads the market metrics FIRE Metrics already holds for a scenario's city.
Adds no external integration, no new API key and no new cost: every figure
here was already gathered, from documented government sources, by a tool
that ships in this app.

WHY NOT city-data.com

It was the site originally suggested. It publishes no developer API, and
its terms of use exclude "any use of data mining, robots, spiders, or
similar data gathering and extraction tools" for commercial collection or
derivative use without prior written permission. So scraping it would be
both fragile and a term we would be breaking. FIRE Metrics already covers
the same ground -- population, income, home values, employment, crime,
climate risk -- so there is nothing to gain by trying.

WHY THE JOIN GOES THROUGH search_aliases

FIRE Metrics stores Census place names. San Francisco is recorded as
"San Francisco city", not "San Francisco". A naive

    WHERE lower(city) = lower(?)

silently returns nothing for it, and for most cities -- which is exactly
what happened the first time this lookup was written during the design
investigation. The search_aliases table exists to resolve a typed name to
a stored one, and it is the only correct way in. A miss here must mean
"not covered", never "joined wrong".

WHAT IS HONESTLY NOT COVERED

FIRE Metrics includes US cities above roughly 100,000 population -- 343 of
them, the smallest being Longmont, CO at 100,109. Mill Valley, CA, a real
deal in this app, has about 14,000 people and will never be in that set.
Rather than render an empty card, a scenario in an uncovered city is told
plainly what the coverage is and why its city is not in it. A blank panel
reads as a bug; a stated limit reads as a limit.

Pure of Flask, but not of I/O: it reads the FIRE Metrics database. Kept
read-only, with its own connection, and every failure degrades to
"unavailable" rather than raising -- market context is a reference panel,
and no underwriting figure depends on it.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

# Mirrors fire_metrics/fire_metrics_updater/db.py. Read directly rather
# than imported because that package is a standalone updater with its own
# dependencies; this only needs the path and a SELECT.
BASE_DIR = Path(__file__).resolve().parent.parent

# Stated on screen, so the reader knows what "not covered" means.
POPULATION_FLOOR = 100_000

# Which metrics to surface, in display order. Deliberately a fixed list:
# the full FIRE Metrics page is one click away, and a market panel inside
# an underwriting model should be a summary, not a second copy of it.
METRICS = (
    ("population_current", "Population", "count", None),
    ("population_growth_recent", "Population growth", "pct", "recent"),
    ("median_income_current", "Median income", "money", None),
    ("median_income_growth_recent", "Income growth", "pct", "recent"),
    ("median_home_value_current", "Median home value", "money", None),
    ("median_home_value_growth_recent", "Home value growth", "pct", "recent"),
    ("employment_growth_recent", "Employment growth", "pct", "recent"),
    ("crime_index_score", "Crime index", "score", "crime_rating"),
    ("climate_risk_score", "Climate risk", "score", "climate_risk_rating"),
    ("landlord_friendliness_score", "Landlord friendliness", "score",
     "landlord_friendliness_label"),
)


def get_db_path() -> Path:
    configured = os.environ.get("FIRE_METRICS_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    return BASE_DIR / "fire_metrics.db"


def _normalize(text: str) -> str:
    """A minimal restatement of city_search.normalize_city_tokens.

    Restated rather than imported for the reason in the module docstring:
    the updater package is standalone. Kept deliberately small -- it only
    has to produce the same key for ordinary input, and any miss degrades
    to "not covered", which is a safe wrong answer rather than a wrong
    number.
    """
    import re
    value = (text or "").lower().strip()
    value = value.replace("&", " and ")
    value = re.sub(r"[._,]", " ", value)
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\bsaint\b", "st", value)
    value = re.sub(r"\bft\b", "fort", value)
    value = re.sub(r"\bmt\b", "mount", value)
    return re.sub(r"\s+", " ", value).strip()


def _search_key(city: str, state: str) -> str:
    return f"{_normalize(city)} {_normalize(state)}".strip()


def lookup(city: str | None, state: str | None,
           db_path: Path | None = None) -> dict[str, Any]:
    """Market context for one city. Never raises.

    Returns a dict that always carries `available` and, when False, a
    `reason` written to be shown to the reader.
    """
    base = {
        "available": False,
        "city": (city or "").strip() or None,
        "state": (state or "").strip().upper() or None,
        "population_floor": POPULATION_FLOOR,
        "metrics": [],
        "display_name": None,
        "reason": None,
    }

    if not base["city"] or not base["state"]:
        base["reason"] = (
            "Add the property's city and state above to see market context "
            "from FIRE Metrics.")
        return base

    path = Path(db_path) if db_path is not None else get_db_path()
    if not path.exists():
        base["reason"] = "FIRE Metrics data is not available on this server."
        return base

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        base["reason"] = "FIRE Metrics data could not be opened."
        return base

    try:
        row = _find_city(conn, base["city"], base["state"])
        if row is None:
            base["reason"] = (
                f"FIRE Metrics covers US cities over about "
                f"{POPULATION_FLOOR:,} people — {base['city']}, {base['state']} "
                f"isn't in that set, so there is no market data to show here."
            )
            return base

        base["available"] = True
        base["display_name"] = row["display_name"]
        base["metrics"] = _metrics_from(row)
        return base
    except sqlite3.Error:
        base["reason"] = "FIRE Metrics data could not be read."
        return base
    finally:
        conn.close()


def _find_city(conn, city: str, state: str):
    """Resolve a typed city to a stored row.

    Three attempts, widest-correct first:
      1. the alias table, which is what it exists for
      2. the stored normalized display name ("san francisco ca")
      3. the stored normalized city ("san francisco city" -> its own key)

    A plain `city = ?` match is deliberately NOT among them. It is the one
    that looks right and silently fails.
    """
    key = _search_key(city, state)

    row = conn.execute(
        "SELECT c.* FROM search_aliases a JOIN cities c "
        "ON c.city = a.city AND c.state = a.state WHERE a.search_key = ?",
        (key,)).fetchone()
    if row:
        return row

    row = conn.execute(
        "SELECT * FROM cities WHERE normalized_display_name = ?",
        (f"{_normalize(city)} {_normalize(state)}".strip(),)).fetchone()
    if row:
        return row

    return conn.execute(
        "SELECT * FROM cities WHERE normalized_city = ? AND upper(state) = ?",
        (_normalize(city), state.strip().upper())).fetchone()


def _metrics_from(row) -> list[dict[str, Any]]:
    keys = row.keys()
    out = []
    for column, label, kind, extra in METRICS:
        if column not in keys:
            continue
        value = row[column]
        rating = None
        if extra and extra != "recent" and extra in keys:
            rating = row[extra]
        out.append({
            "key": column,
            "label": label,
            "value": value,
            "kind": kind,
            "rating": rating,
            "is_recent": extra == "recent",
            "available": value is not None,
        })
    return out
