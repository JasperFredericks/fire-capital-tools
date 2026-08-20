"""
FIRE Capital Tools - OpenAI usage counter.

Counts real OpenAI calls, per calendar month, tagged by which feature made
them. Same shape and the same rules as rentcast_usage and
google_places_usage in tools/market_data_cache.py: env-var-overridable
path with a local fallback, fresh connection per call, idempotent
CREATE TABLE IF NOT EXISTS on connect.

WHY THIS EXISTS, AND WHY IT IS TAGGED BY FEATURE

There is a real $60/month spend cap on the OpenAI account. It is an
account-level backstop set outside this application, which means that
when it bites, it bites everything at once and says nothing about which
feature spent the money. RentCast and Google Places have had local
counters from the start; OpenAI has had none, and
tools/service_costs.py has been carrying a note saying so.

One combined number would not answer the question that matters. FIRE
Metrics' CRE research runs a hosted web-search tool and is far more
expensive per call than a summary; an OM extraction reads a whole
document. "Something used $60" is not actionable. "CRE research made 340
calls and 2.1M prompt tokens" is.

TOKENS AS WELL AS CALLS

The two existing counters count calls, because RentCast and Places bill
per request and a call IS the unit of spend. OpenAI bills per token, so a
call count alone cannot tell you where the budget went -- one CRE research
call can cost more than fifty summaries. Tokens are recorded alongside
the call count for that reason, and they are nullable: a response that
does not report usage still counts as a call rather than being dropped.

WHAT COUNTS

Only a real outbound request. A cache hit never reaches the functions
that record here, which is the same guarantee the RentCast and Places
counters make and the reason recording happens at the call site rather
than in the route.

THIS COUNTS, IT DOES NOT GATE

Nothing here refuses a call. There is deliberately no threshold and no
at_cap flag, because this application does not enforce an OpenAI limit --
the $60 cap lives at the account. Inventing a local threshold would mean
inventing a number nobody has agreed to, and the existing counters' caps
are real figures from real vendor limits. Gating, if it is wanted,
belongs with each feature that spends, next to its own confirm-before-
spend step.
"""

from __future__ import annotations

import datetime
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent

# The features that spend today, and the two that are designed but not
# built. Listed here so the admin page can show a known feature at zero
# rather than silently omitting it -- "OM extraction: 0 calls" and "OM
# extraction is missing from this table" look identical otherwise.
FEATURE_FIRE_METRICS_SUMMARY = "fire_metrics_summary"
FEATURE_FIRE_METRICS_CRE = "fire_metrics_cre"
FEATURE_OM_EXTRACTION = "om_extraction"
FEATURE_INVESTOR_NOTETAKER = "investor_notetaker"

FEATURE_LABELS: dict[str, str] = {
    FEATURE_FIRE_METRICS_SUMMARY: "FIRE Metrics — market summary",
    FEATURE_FIRE_METRICS_CRE: "FIRE Metrics — CRE research (web search)",
    FEATURE_OM_EXTRACTION: "OM extraction",
    FEATURE_INVESTOR_NOTETAKER: "Investor notetaker",
}

# Order for display. Not alphabetical: the two that actually spend today
# come first.
KNOWN_FEATURES: tuple[str, ...] = (
    FEATURE_FIRE_METRICS_SUMMARY,
    FEATURE_FIRE_METRICS_CRE,
    FEATURE_OM_EXTRACTION,
    FEATURE_INVESTOR_NOTETAKER,
)

MAX_FEATURE_LEN = 64

SCHEMA = """
-- One row per (month, feature). The composite primary key is what makes
-- the upsert below safe to call concurrently and what keeps a feature's
-- history from being collapsed into a single total.
--
-- A feature key is NOT constrained to the list in this module. A new
-- feature can start recording before anyone updates FEATURE_LABELS, and
-- it will show on the admin page under its raw key rather than being
-- rejected or silently dropped. Losing a spend record to keep a
-- vocabulary tidy would be the wrong trade.
CREATE TABLE IF NOT EXISTS openai_usage (
    year_month TEXT NOT NULL,
    feature TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (year_month, feature)
);
"""


def get_db_path() -> Path:
    configured = os.environ.get("OPENAI_USAGE_DB_PATH", "").strip()
    if configured:
        return Path(configured)
    return BASE_DIR / "openai_usage.db"


def storage_status() -> dict[str, Any]:
    """Whether this counter's database will survive a deploy.

    Every other persistent store in this app is pointed at the Railway
    volume by an explicit *_DB_PATH environment variable. If this one is
    unset the counter still works perfectly -- it just writes to the
    container filesystem and starts again from zero on the next deploy.

    That is the worst possible failure for a MONTHLY counter: it would
    look correct on every page load while quietly under-reporting spend
    several times a month. So the state is reported rather than assumed,
    and the admin page says so out loud.
    """
    configured = os.environ.get("OPENAI_USAGE_DB_PATH", "").strip()
    return {
        "path": str(get_db_path()),
        "configured": bool(configured),
        "persistent": bool(configured),
        "env_var": "OPENAI_USAGE_DB_PATH",
    }


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


def current_year_month() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m")


def clean_feature(feature: Any) -> str:
    name = str(feature or "").strip()[:MAX_FEATURE_LEN]
    return name or "unattributed"


def tokens_from_response(response: Any) -> tuple[int, int]:
    """Prompt and completion tokens off an OpenAI response object.

    Tolerant on purpose. The Responses API, the Chat Completions API and
    the various SDK versions do not agree on the attribute names, and a
    counter that raised because a field moved would take a working
    feature down with it. Anything unreadable is zero, and the call is
    still counted.
    """
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return (0, 0)

    def pick(*names: str) -> int:
        for n in names:
            value = getattr(usage, n, None)
            if value is None and isinstance(usage, dict):
                value = usage.get(n)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return 0
        return 0

    return (pick("input_tokens", "prompt_tokens"),
            pick("output_tokens", "completion_tokens"))


def record(feature: str, response: Any = None, *,
           prompt_tokens: int | None = None,
           completion_tokens: int | None = None,
           year_month: str | None = None,
           db_path: Path | None = None) -> None:
    """Count one real OpenAI call.

    NEVER RAISES. A counter is bookkeeping; a failure to write it must not
    turn a working market summary into an error page. A lost count is a
    gap in a report, an exception here would be a broken feature.

    Pass the SDK response and the tokens are read off it; pass the token
    counts directly if the caller already has them.
    """
    try:
        if response is not None and prompt_tokens is None and completion_tokens is None:
            prompt_tokens, completion_tokens = tokens_from_response(response)
        with get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO openai_usage
                    (year_month, feature, calls, prompt_tokens, completion_tokens)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(year_month, feature) DO UPDATE SET
                    calls = calls + 1,
                    prompt_tokens = prompt_tokens + excluded.prompt_tokens,
                    completion_tokens = completion_tokens + excluded.completion_tokens
                """,
                (year_month or current_year_month(), clean_feature(feature),
                 int(prompt_tokens or 0), int(completion_tokens or 0)),
            )
            conn.commit()
    except Exception:  # noqa: BLE001 -- see the docstring
        pass


def usage_for_month(conn: sqlite3.Connection,
                    year_month: str | None = None) -> dict[str, Any]:
    """The per-feature breakdown for one month, plus its totals.

    Every known feature appears, at zero if it has not spent. A feature
    that recorded without being in KNOWN_FEATURES appears after them,
    under its raw key -- so a spend can never be invisible just because
    this module has not been updated to name it.
    """
    ym = year_month or current_year_month()
    stored = {r["feature"]: dict(r) for r in conn.execute(
        "SELECT * FROM openai_usage WHERE year_month = ?", (ym,))}

    ordered = list(KNOWN_FEATURES) + sorted(
        k for k in stored if k not in KNOWN_FEATURES)

    rows = []
    for key in ordered:
        row = stored.get(key) or {}
        calls = int(row.get("calls") or 0)
        prompt = int(row.get("prompt_tokens") or 0)
        completion = int(row.get("completion_tokens") or 0)
        rows.append({
            "feature": key,
            "label": FEATURE_LABELS.get(key, key),
            "known": key in FEATURE_LABELS,
            "calls": calls,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        })

    return {
        "year_month": ym,
        "rows": rows,
        "total_calls": sum(r["calls"] for r in rows),
        "total_tokens": sum(r["total_tokens"] for r in rows),
        "any_usage": any(r["calls"] for r in rows),
    }
