"""
FIRE Capital Tools - Market data service (RentCast + Google Places).

Looks up an address and pulls:
  - RentCast (real public REST API, sourced from public records/listings --
    not scraping): rent estimate, rental comparables, and basic property
    details.
  - Google Places API (official): rating, review count, a few review
    snippets.

Built standalone -- no dependency on Deal Dive's own blueprint code, same
principle as tools/scorecard_history.py and tools/deal_dive_db.py -- so it
can also become the foundation of the currently-placeholder "Rent Comps"
tool later without redoing this work. Deal Dive (tools/deal_dive.py) is a
*caller* of this module, not the other way around.

RentCast's free tier is 50 calls/month, so every lookup goes through
tools/market_data_cache.py first; a real API call only happens on a cache
miss or a stale (>30 days by default) entry.

Both providers degrade gracefully rather than raising: a missing API key,
a failed request, or an address neither provider recognizes all come back
as {"available": False, "message": ...} instead of an exception, the same
way FIRE Metrics' own market-context lookup in tools/deal_dive.py handles
an unindexed city.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests

from tools import market_data_cache as cache

BASE_DIR = Path(__file__).resolve().parent.parent

RENTCAST_BASE_URL = "https://api.rentcast.io/v1"
GOOGLE_PLACES_BASE_URL = "https://maps.googleapis.com/maps/api/place"
REQUEST_TIMEOUT = 15


def get_secret(name: str, fallback_file: str | None = None) -> str | None:
    """Env var first, then an optional gitignored local file at the repo
    root. Mirrors fire_metrics/fire_metrics_updater/config.py's get_secret()
    -- same pattern, kept local here rather than imported so this module
    has zero dependency on the fire_metrics package. Never logs the value."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    if fallback_file:
        path = Path(fallback_file)
        if not path.is_absolute():
            path = BASE_DIR / path
        if path.exists():
            file_value = path.read_text(encoding="utf-8").strip()
            if file_value:
                return file_value
    return None


def _rentcast_error_detail(resp: requests.Response) -> str:
    """RentCast returns a JSON body like {"status":403,"error":"billing/
    subscription-inactive","message":"..."} on failure -- surface that
    directly (e.g. "the key exists but isn't on an active subscription")
    instead of just the bare HTTP status code."""
    try:
        payload = resp.json()
        message = payload.get("message") or payload.get("error")
        if message:
            return f"{message} (HTTP {resp.status_code})"
    except ValueError:
        pass
    return f"HTTP {resp.status_code}"


def _rentcast_api_key() -> str | None:
    return get_secret("RENTCAST_API_KEY", "rentcast_api_key.txt")


def _google_places_api_key() -> str | None:
    return get_secret("GOOGLE_PLACES_API_KEY", "google_places_api_key.txt")


# ── RentCast ─────────────────────────────────────────────────────────────

def _next_month_label() -> str:
    import calendar
    import datetime

    now = datetime.datetime.utcnow()
    year, month = (now.year, now.month + 1) if now.month < 12 else (now.year + 1, 1)
    return f"{calendar.month_name[month]} {year}"


def _rentcast_usage_gate() -> dict[str, Any] | None:
    """Hard stop, not a warning: if this month's real-call count is at or
    above the safety threshold, refuse to make another RentCast request at
    all. Returns a ready-to-return {"available": False, ...} dict if the
    lookup should be blocked, or None if it's fine to proceed."""
    with cache.get_connection() as conn:
        usage = cache.get_rentcast_usage(conn)
    if usage >= cache.RENTCAST_MONTHLY_SAFETY_THRESHOLD:
        return {
            "available": False,
            "message": (
                f"Monthly RentCast lookup limit reached ({usage}/{cache.RENTCAST_MONTHLY_FREE_LIMIT} "
                f"used this month, safety threshold {cache.RENTCAST_MONTHLY_SAFETY_THRESHOLD}) — "
                f"resets {_next_month_label()}."
            ),
        }
    return None


def _record_rentcast_call() -> None:
    with cache.get_connection() as conn:
        cache.increment_rentcast_usage(conn)


def rentcast_quota() -> dict[str, Any]:
    """This month's RentCast usage, for callers that need to *show* the
    quota and pre-emptively disable a paid action (Deal Dive's and Rent
    Comps' Force Refresh buttons both do).

    Lives here rather than in each blueprint so there is exactly one
    definition of "at cap": at_cap mirrors _rentcast_usage_gate()'s own
    >= threshold condition, so a button can never stay enabled past the
    point a real request would be refused, and the number shown to the
    user is the number that actually blocks the call. Purely a read --
    never increments anything."""
    with cache.get_connection() as conn:
        used = cache.get_rentcast_usage(conn)
    threshold = cache.RENTCAST_MONTHLY_SAFETY_THRESHOLD
    return {
        "used": used,
        "threshold": threshold,
        "at_cap": used >= threshold,
        "limit": cache.RENTCAST_MONTHLY_FREE_LIMIT,
    }


def get_rentcast_data(address: str, city: str, state: str, zip_code: str | None = None) -> dict[str, Any]:
    """Rent estimate + rental comparables + basic property details for one
    address. Returns {"available": False, "message": ...} rather than
    raising if the key is missing, the monthly safety cap is hit, or either
    call fails.

    Costs exactly one RentCast request per lookup: the rent-estimate call
    carries subject-property attributes in its own subjectProperty field,
    so no second /properties request is needed (see the comment at that
    point below).

    Hard usage cap: RentCast's free plan is 50 requests/month with a per-
    request overage fee beyond that -- refuses to make a real call at all
    once this month's count is at/above the safety threshold (see
    market_data_cache.RENTCAST_MONTHLY_SAFETY_THRESHOLD), checked *before*
    any request goes out, not after. Cache hits (in get_market_data) never
    reach this function at all, so they never count against the quota."""
    api_key = _rentcast_api_key()
    if not api_key:
        return {"available": False, "message": "RentCast API key not configured."}

    blocked = _rentcast_usage_gate()
    if blocked:
        return blocked

    full_address = ", ".join(part for part in [address, city, state, zip_code] if part)
    headers = {"X-Api-Key": api_key, "Accept": "application/json"}

    try:
        rent_resp = requests.get(
            f"{RENTCAST_BASE_URL}/avm/rent/long-term",
            headers=headers,
            params={"address": full_address},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        return {"available": False, "message": f"RentCast rent-estimate lookup failed: {exc}"}
    _record_rentcast_call()  # a request reached RentCast's server either way -- counts against quota

    if rent_resp.status_code == 404:
        return {"available": False, "message": f"RentCast has no rent estimate on file for {full_address}."}
    if not rent_resp.ok:
        return {
            "available": False,
            "message": f"RentCast rent-estimate lookup failed: {_rentcast_error_detail(rent_resp)}",
        }

    rent_payload = rent_resp.json()
    # correlation/days_old/listing_status were previously dropped on the
    # floor even though RentCast sends them with every comparable at no
    # extra cost. correlation in particular is what RentCast itself sorts
    # the list by, so without it a caller inherits the ordering but can't
    # show *why* one comp outranks another. Kept now that Rent Comps
    # surfaces them as Match % / Last Seen / Status.
    #
    # Cache entries written before these keys existed simply won't have
    # them; callers read via .get()/Jinja's undefined-is-falsy and render
    # a dash, so an old cached row degrades to the previous display rather
    # than erroring.
    comparables = [
        {
            "address": comp.get("formattedAddress"),
            "price": comp.get("price"),
            "bedrooms": comp.get("bedrooms"),
            "bathrooms": comp.get("bathrooms"),
            "square_footage": comp.get("squareFootage"),
            "distance_miles": comp.get("distance"),
            "correlation": comp.get("correlation"),
            "days_old": comp.get("daysOld"),
            "listing_status": comp.get("status"),
        }
        for comp in (rent_payload.get("comparables") or [])
    ]

    # Subject-property details come out of the rent-estimate response above
    # rather than a second /properties request: the rent endpoint's
    # lookupSubjectAttributes parameter defaults to true, so the same
    # attributes ride along in subjectProperty for free. This used to be a
    # separate /properties call, which doubled the cost of every lookup
    # (2 quota units instead of 1) against a hard 50/month limit.
    #
    # Verified against live responses for both a data-rich address (all
    # five attributes the UI renders come back identically from either
    # endpoint) and a data-poor one (both endpoints return nothing, so the
    # second call bought nothing there either).
    #
    # One deliberate gap: subjectProperty omits lastSalePrice, which
    # /properties did return. The key is kept below so the cached dict
    # shape is unchanged, but it is now always None. Nothing reads it --
    # no template or route references last_sale_price -- so this drops a
    # value that was already never displayed. lastSaleDate *is* in
    # subjectProperty and is preserved.
    subject = rent_payload.get("subjectProperty") or {}
    property_details = None
    if subject:
        property_details = {
            "property_type": subject.get("propertyType"),
            "bedrooms": subject.get("bedrooms"),
            "bathrooms": subject.get("bathrooms"),
            "square_footage": subject.get("squareFootage"),
            "year_built": subject.get("yearBuilt"),
            "last_sale_price": None,  # not provided by subjectProperty; see above
            "last_sale_date": subject.get("lastSaleDate"),
        }

    return {
        "available": True,
        "rent_estimate": rent_payload.get("rent"),
        "rent_range_low": rent_payload.get("rentRangeLow"),
        "rent_range_high": rent_payload.get("rentRangeHigh"),
        "comparables": comparables,
        "property": property_details,
    }


# ── Google Places ────────────────────────────────────────────────────────

def _google_places_usage_gate() -> dict[str, Any] | None:
    """Same hard-stop pattern as RentCast's gate: refuse to make another
    real Google Places request at all once this month's count is at or
    above the safety threshold. Returns a ready {"available": False, ...}
    dict if the lookup should be blocked, or None if it's fine to proceed."""
    with cache.get_connection() as conn:
        usage = cache.get_google_places_usage(conn)
    if usage >= cache.GOOGLE_PLACES_MONTHLY_SAFETY_THRESHOLD:
        return {
            "available": False,
            "message": (
                f"Monthly Google Places lookup limit reached ({usage} used this month, "
                f"safety threshold {cache.GOOGLE_PLACES_MONTHLY_SAFETY_THRESHOLD}) — "
                f"resets {_next_month_label()}."
            ),
        }
    return None


def _record_google_places_call() -> None:
    with cache.get_connection() as conn:
        cache.increment_google_places_usage(conn)


def google_places_quota() -> dict[str, Any]:
    """This month's Google Places usage, the counterpart to
    rentcast_quota() above and for the same reason: at_cap mirrors
    _google_places_usage_gate()'s own >= threshold condition, so anything
    that displays this number is displaying the number that actually
    blocks a call.

    Unlike RentCast there is no "limit" key, and that absence is
    deliberate. RentCast publishes a hard 50/month, so a used/limit
    fraction is a true statement. Google's free allowance is a
    researched estimate (see market_data_cache), so presenting one here
    would dress a guess up as a denominator. Callers get the threshold
    this app enforces and nothing it cannot stand behind. Purely a read --
    never increments anything."""
    with cache.get_connection() as conn:
        used = cache.get_google_places_usage(conn)
    threshold = cache.GOOGLE_PLACES_MONTHLY_SAFETY_THRESHOLD
    return {
        "used": used,
        "threshold": threshold,
        "at_cap": used >= threshold,
    }


def quota_reset_label() -> str:
    """When both monthly counters roll over. Wraps the module-private
    _next_month_label() so callers outside this module (the service-costs
    page) can show the reset date without reaching past the underscore or
    recomputing the date themselves."""
    return _next_month_label()


def get_google_place_rating(address: str, city: str, state: str) -> dict[str, Any]:
    """Rating, review count, and a few review snippets for the place at this
    address. Returns {"available": False, "message": ...} rather than
    raising if the key is missing, the monthly safety cap is hit, the place
    can't be found, or either call fails.

    Hard usage cap: see market_data_cache.GOOGLE_PLACES_MONTHLY_SAFETY_THRESHOLD
    for the reasoning -- checked *before* any request goes out, same as
    RentCast's cap. Cache hits (in get_market_data) never reach this
    function at all, so they never count against it."""
    api_key = _google_places_api_key()
    if not api_key:
        return {"available": False, "message": "Google Places API key not configured."}

    blocked = _google_places_usage_gate()
    if blocked:
        return blocked

    full_address = ", ".join(part for part in [address, city, state] if part)

    try:
        find_resp = requests.get(
            f"{GOOGLE_PLACES_BASE_URL}/findplacefromtext/json",
            params={
                "input": full_address,
                "inputtype": "textquery",
                "fields": "place_id,name",
                "key": api_key,
            },
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        return {"available": False, "message": f"Google Places lookup failed: {exc}"}
    _record_google_places_call()  # a request reached Google's servers either way -- counts against quota

    if not find_resp.ok:
        return {"available": False, "message": f"Google Places lookup failed (HTTP {find_resp.status_code})."}

    find_payload = find_resp.json()
    status = find_payload.get("status")
    if status == "ZERO_RESULTS":
        return {"available": False, "message": f"Google Places has no listing for {full_address}."}
    if status != "OK" or not find_payload.get("candidates"):
        detail = find_payload.get("error_message")
        message = f"Google Places lookup failed (status: {status})"
        if detail:
            message += f" -- {detail}"
        return {"available": False, "message": message}

    candidate = find_payload["candidates"][0]
    place_id = candidate.get("place_id")

    blocked = _google_places_usage_gate()  # re-check -- the find-place call above may have just hit the cap
    if blocked:
        return blocked

    try:
        details_resp = requests.get(
            f"{GOOGLE_PLACES_BASE_URL}/details/json",
            params={
                "place_id": place_id,
                "fields": "name,rating,user_ratings_total,reviews",
                "key": api_key,
            },
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        return {"available": False, "message": f"Google Places details lookup failed: {exc}"}
    _record_google_places_call()

    if not details_resp.ok or details_resp.json().get("status") != "OK":
        return {"available": False, "message": "Google Places details lookup failed."}

    result = details_resp.json().get("result", {})
    reviews = [
        {
            "author": r.get("author_name"),
            "rating": r.get("rating"),
            "text": (r.get("text") or "")[:280],
        }
        for r in (result.get("reviews") or [])[:3]
    ]

    return {
        "available": True,
        "place_name": result.get("name") or candidate.get("name"),
        "rating": result.get("rating"),
        "review_count": result.get("user_ratings_total"),
        "reviews": reviews,
    }


# ── Combined, cached lookup ──────────────────────────────────────────────

def get_market_data(
    address: str,
    city: str,
    state: str,
    zip_code: str | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """The function callers (Deal Dive, and later Rent Comps) should
    actually use. Checks the cache first; only calls RentCast/Google Places
    for real on a miss or a stale (>30 day) entry, or when force_refresh is
    explicitly requested."""
    address_key = cache.normalize_address_key(address, city, state, zip_code)

    with cache.get_connection() as conn:
        if not force_refresh:
            cached = cache.get_cached(conn, address_key)
            if cached:
                return {
                    "from_cache": True,
                    "fetched_at": cached["fetched_at"],
                    "rentcast": cached["rentcast"],
                    "google_places": cached["google_places"],
                }

        rentcast_data = get_rentcast_data(address, city, state, zip_code)
        google_data = get_google_place_rating(address, city, state)
        cache.save_cache(conn, address_key, address, city, state, zip_code, rentcast_data, google_data)

    return {"from_cache": False, "fetched_at": None, "rentcast": rentcast_data, "google_places": google_data}
