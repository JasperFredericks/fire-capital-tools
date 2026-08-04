"""
FIRE Capital Tools - Rent Comps.

A standalone rental-comparables lookup: enter any address, pull RentCast's
rent estimate and comparable rentals, and save the ones worth keeping.

Two modes, one page:
  * Standalone -- type an address, saved comps have a NULL deal_id and
    belong to no deal. This is the Markets-section use case: checking a
    submarket without a deal existing yet.
  * Deal-scoped -- arrived at with ?deal_id=N from Deal Dive. The address
    is taken from the deal (read-only, since editing it here would silently
    diverge from the deal record), and saved comps carry that deal_id so
    Deal Dive can show a count and link back.

Reuses tools/market_data_service for every RentCast interaction -- that
module was built standalone for exactly this, and none of its
API-calling or caching logic is duplicated here. Quota safety is the same
pattern Deal Dive uses, calling the same market_data_service.rentcast_quota()
so there is one definition of "at cap" across both tools:
  * Reload from Cache -- always free, never touches the network.
  * Force Refresh -- spends a call, confirmed in the UI, refused both
    client-side (disabled button) and server-side once at cap.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_required

from tools import deal_dive_db
from tools.form_utils import to_float as _to_float, to_int as _to_int
from tools import market_data_cache
from tools import market_data_service
from tools import rent_comps_db as db

rent_comps_bp = Blueprint("rent_comps", __name__)

MAX_COMP_ADDRESS_LEN = 255

# RentCast returns 15 comparables by default, already sorted by correlation
# descending. Showing all 15 up front is a wall of rows, so both tables
# collapse to the strongest few with an expand control -- a *display*
# limit, never a data limit. Saved comps in particular are never truncated
# away: the user chose to save them, and silently hiding that work is the
# opposite of what a cap is for.
CANDIDATE_PREVIEW_COUNT = 5
SAVED_PREVIEW_COUNT = 5


def _estimate_confidence(rent, low, high):
    """Range width as a share of the estimate, turned into a plain label.

    RentCast gives a low/high band but no confidence score, and the band's
    width is the only signal available about how sure the estimate is: a
    $1,900 estimate spanning $1,880-$1,920 means something very different
    from one spanning $1,400-$2,400. Returns None when any input is missing
    or the estimate is zero, so the caller renders a dash rather than a
    fabricated confidence."""
    if rent is None or low is None or high is None:
        return None
    try:
        rent = float(rent)
        spread = float(high) - float(low)
    except (TypeError, ValueError):
        return None
    if rent <= 0 or spread < 0:
        return None

    pct = (spread / rent) * 100
    if pct <= 15:
        label = "High"
    elif pct <= 30:
        label = "Moderate"
    else:
        label = "Low"
    return {"label": label, "spread_pct": pct}


def _context_from_request():
    """Resolve which of the two modes this request is in, and what address
    it applies to.

    deal_id wins over any address in the query string -- if the page was
    opened for a specific deal, the deal record is the source of truth for
    the address, and a hand-edited query param must not be able to point a
    deal's comps at a different property. A deal_id that no longer exists
    degrades to standalone mode with a flash rather than 404ing, matching
    Deal Dive's own _deal_not_found() reasoning: the deal may have been
    deleted in another tab."""
    deal_id = _to_int(request.args.get("deal_id") or request.form.get("deal_id"))

    if deal_id is not None:
        with deal_dive_db.get_connection() as conn:
            deal = deal_dive_db.get_deal(conn, deal_id)
        if deal:
            return {
                "deal_id": deal_id,
                "deal": deal,
                "address": deal["address"],
                "city": deal["city"],
                "state": deal["state"],
                "zip": deal.get("zip"),
            }
        flash("That deal could not be found — showing a standalone search instead.", "warning")

    return {
        "deal_id": None,
        "deal": None,
        "address": (request.args.get("address") or request.form.get("address") or "").strip()[:MAX_COMP_ADDRESS_LEN],
        "city": (request.args.get("city") or request.form.get("city") or "").strip(),
        "state": (request.args.get("state") or request.form.get("state") or "").strip().upper(),
        "zip": (request.args.get("zip") or request.form.get("zip") or "").strip() or None,
    }


def _redirect_to_view(ctx):
    """Back to the search page in whichever mode we're in, preserving the
    address so a standalone search survives the POST-redirect-GET."""
    if ctx["deal_id"] is not None:
        return redirect(url_for("rent_comps.index", deal_id=ctx["deal_id"]))
    return redirect(
        url_for(
            "rent_comps.index",
            address=ctx["address"] or None,
            city=ctx["city"] or None,
            state=ctx["state"] or None,
            zip=ctx["zip"] or None,
        )
    )


def _has_address(ctx) -> bool:
    return bool(ctx["address"] and ctx["city"] and ctx["state"])


@rent_comps_bp.route("/")
@login_required
def index():
    """Read-only render. Like Deal Dive's detail(), a plain page view never
    triggers a RentCast call -- it shows whatever is already cached for
    this address, and pulling fresh data is always an explicit POST."""
    ctx = _context_from_request()

    cached = None
    if _has_address(ctx):
        address_key = market_data_cache.normalize_address_key(
            ctx["address"], ctx["city"], ctx["state"], ctx["zip"]
        )
        with market_data_cache.get_connection() as conn:
            cached = market_data_cache.get_cached(conn, address_key)

    with db.get_connection() as conn:
        saved = db.list_comps(conn, ctx["deal_id"])
        already_saved = db.saved_addresses(conn, ctx["deal_id"])

    rentcast = (cached or {}).get("rentcast") or None
    candidates = []
    if rentcast and rentcast.get("available"):
        candidates = rentcast.get("comparables") or []

    confidence = None
    if rentcast and rentcast.get("available"):
        confidence = _estimate_confidence(
            rentcast.get("rent_estimate"),
            rentcast.get("rent_range_low"),
            rentcast.get("rent_range_high"),
        )

    return render_template(
        "tools/rent_comps.html",
        ctx=ctx,
        has_address=_has_address(ctx),
        cached=cached,
        rentcast=rentcast,
        candidates=candidates,
        confidence=confidence,
        saved=saved,
        already_saved=already_saved,
        candidate_preview=CANDIDATE_PREVIEW_COUNT,
        saved_preview=SAVED_PREVIEW_COUNT,
        rentcast_quota=market_data_service.rentcast_quota(),
    )


@rent_comps_bp.route("/search", methods=["POST"])
@login_required
def search():
    """Standalone address entry. Only navigates -- the address becomes
    query params and index() renders whatever is cached for it. Pulling
    fresh data is a separate, explicit action, so typing an address can
    never spend a call by itself."""
    ctx = _context_from_request()
    if ctx["deal_id"] is None and not _has_address(ctx):
        flash("Enter an address, city, and state to search.", "danger")
        return redirect(url_for("rent_comps.index"))
    return _redirect_to_view(ctx)


@rent_comps_bp.route("/reload", methods=["POST"])
@login_required
def reload_from_cache():
    """Re-display cached data, guaranteed free. Deliberately does not go
    through market_data_service at all -- even an unforced get_market_data()
    spends real calls on a cache miss or a stale entry, and this action's
    whole point is that it can never cost anything."""
    ctx = _context_from_request()
    if not _has_address(ctx):
        flash("No address to reload.", "danger")
        return _redirect_to_view(ctx)

    address_key = market_data_cache.normalize_address_key(
        ctx["address"], ctx["city"], ctx["state"], ctx["zip"]
    )
    with market_data_cache.get_connection() as conn:
        cached = market_data_cache.get_cached(conn, address_key)

    if cached:
        flash("Reloaded cached rent data — no API calls used.", "success")
    else:
        flash(
            "Nothing cached for this address yet (or the cache entry has expired) — "
            "use Force Refresh to pull fresh data.",
            "info",
        )
    return _redirect_to_view(ctx)


@rent_comps_bp.route("/pull", methods=["POST"])
@login_required
def pull():
    """Spend a RentCast call for fresh data. Same two-sided cap enforcement
    Deal Dive uses: the template disables the button at cap, and this
    refuses the request anyway, since a disabled button is a UI convenience
    rather than a guarantee (stale page, double submit, direct POST)."""
    ctx = _context_from_request()
    if not _has_address(ctx):
        flash("Enter an address, city, and state before pulling data.", "danger")
        return _redirect_to_view(ctx)

    if market_data_service.rentcast_quota()["at_cap"]:
        flash(
            "Monthly RentCast lookup limit reached — showing cached data instead. "
            "Force Refresh is unavailable until the counter resets.",
            "warning",
        )
        return _redirect_to_view(ctx)

    result = market_data_service.get_market_data(
        ctx["address"], ctx["city"], ctx["state"], ctx["zip"], force_refresh=True
    )
    rentcast = result.get("rentcast") or {}
    if rentcast.get("available"):
        count = len(rentcast.get("comparables") or [])
        flash(f"Pulled fresh rent data — {count} comparable{'' if count == 1 else 's'} found.", "success")
    else:
        flash(rentcast.get("message") or "RentCast returned no data for this address.", "warning")
    return _redirect_to_view(ctx)


@rent_comps_bp.route("/save", methods=["POST"])
@login_required
def save_comp():
    """Copy one auto-pulled candidate into saved comps. Auto-pulled data
    supplements the saved list -- it is never merged in on its own, only
    via this explicit action, the same principle Deal Dive applies to its
    own promoted comps. Costs no API calls; it reads from the form the
    candidates table already rendered."""
    ctx = _context_from_request()
    # "comp_address", not "address" -- in standalone mode the form also
    # carries the *subject* address (scope_fields() in the template), and a
    # single "address" name would collide between the two.
    address = (request.form.get("comp_address") or "").strip()[:MAX_COMP_ADDRESS_LEN]

    with db.get_connection() as conn:
        if address and address.lower() in db.saved_addresses(conn, ctx["deal_id"]):
            flash("That comp is already saved.", "info")
            return _redirect_to_view(ctx)

        db.add_comp(
            conn,
            ctx["deal_id"],
            {
                "address": address or None,
                "bedrooms": _to_float(request.form.get("bedrooms")),
                "bathrooms": _to_float(request.form.get("bathrooms")),
                "square_footage": _to_float(request.form.get("square_footage")),
                "distance_miles": _to_float(request.form.get("distance_miles")),
                "correlation": _to_float(request.form.get("correlation")),
                "days_old": _to_int(request.form.get("days_old")),
                "listing_status": (request.form.get("listing_status") or "").strip() or None,
                "rent": _to_float(request.form.get("rent")),
                "comp_date": (request.form.get("comp_date") or "").strip() or None,
                "source": db.SOURCE_RENTCAST,
            },
        )
    flash("Comp saved.", "success")
    return _redirect_to_view(ctx)


@rent_comps_bp.route("/comp/<int:comp_id>/delete", methods=["POST"])
@login_required
def delete_comp(comp_id):
    ctx = _context_from_request()
    with db.get_connection() as conn:
        db.delete_comp(conn, comp_id, ctx["deal_id"])
    flash("Comp removed.", "success")
    return _redirect_to_view(ctx)


# ── Cross-tool query ─────────────────────────────────────────────────────

def count_for_deal(deal_id: int) -> int:
    """How many rent comps are saved against one deal. Deal Dive's summary
    card calls this directly rather than over HTTP -- both run in the same
    process, and an internal request would add a round-trip and an auth
    hop for a single integer."""
    with db.get_connection() as conn:
        return db.count_comps(conn, deal_id)
