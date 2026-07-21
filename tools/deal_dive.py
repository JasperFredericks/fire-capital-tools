"""
FIRE Capital Tools - Deal Dive.

A deep-dive report on one specific property (financials, comps, and
condition) to support evaluating it as a potential acquisition, or a
deeper look at an existing one. Deliberately scoped to summary-level
figures and manual entry -- see tools/deal_dive_db.py's module docstring
and the section comments below for what's explicitly out of scope here
(full underwriting models, automated comps, document auto-parsing).
"""

from __future__ import annotations

import secrets
import shutil
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import login_required
from werkzeug.utils import secure_filename

from tools import deal_dive_db as db
from tools import market_data_cache
from tools import market_data_service

deal_dive_bp = Blueprint("deal_dive", __name__)

ALLOWED_UPLOAD_EXT = {
    ".pdf", ".xlsx", ".xls", ".csv", ".doc", ".docx",
    ".jpg", ".jpeg", ".png", ".heic",
}
MAX_COMP_ADDRESS_LEN = 255


def _upload_dir(deal_id: int) -> Path:
    path = Path(current_app.config["UPLOAD_FOLDER"]) / "deal-dive" / str(deal_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _to_float(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value.replace(",", "").replace("$", "").replace("%", ""))
    except ValueError:
        return None


def _to_int(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(float(value.replace(",", "")))
    except ValueError:
        return None


_MARKET_COMP_SOURCE_PREFIX = "Auto-pulled from RentCast"


def _deal_not_found():
    """A deal can vanish out from under an open browser tab -- deleted in
    another tab/session, or reached via a stale back-navigation -- and a
    raw 404 error page for that is a confusing dead end for what was just
    a normal form submission. Flash a plain explanation and send the user
    back to the deal list instead. (Direct navigation to a deal URL that
    never existed at all -- detail()/download_file() -- still 404s
    normally, since that's an ordinary "page not found," not a deal that
    existed a moment ago on the same page.)"""
    flash("That deal could not be found — it may have been deleted.", "danger")
    return redirect(url_for("deal_dive.index"))


def _promoted_market_addresses(comps) -> set[str]:
    """Normalized addresses already promoted from auto-pulled market data
    into this deal's own comps table -- identifies a promoted RentCast
    comparable by the same source_notes prefix promote_market_comp() writes,
    since that's the only marker distinguishing an auto-pulled comp from a
    manually-entered one once it's in deal_comps."""
    return {
        (c.get("address") or "").strip().lower()
        for c in comps
        if (c.get("source_notes") or "").startswith(_MARKET_COMP_SOURCE_PREFIX) and c.get("address")
    }


def get_market_context(city, state):
    """Read-only lookup against FIRE Metrics' own city index, reusing its
    existing fuzzy city_search matching rather than a fragile exact-string
    match. Never writes to FIRE Metrics' database or touches its comparison
    feature. Degrades to {"available": False, ...} for any reason (city not
    indexed, FIRE Metrics DB not reachable) instead of raising -- market
    context is a nice-to-have alongside the manually-entered comps, not a
    dependency Deal Dive should break over."""
    if not city or not state:
        return {"available": False, "message": "No city/state on file for this deal yet."}
    try:
        from fire_metrics.fire_metrics_updater import city_search, db as fm_db

        with fm_db.get_connection() as conn:
            city_index = fm_db.build_city_index_payload(conn)
            excluded_index = fm_db.build_excluded_index_payload(conn)
        result = city_search.find_city_match(f"{city}, {state}", city_index, excluded_index)
        if result.get("status") == "found":
            return {"available": True, "city": result["city"]}
        return {
            "available": False,
            "message": f"No FIRE Metric market data available for {city}, {state}.",
        }
    except Exception as exc:
        current_app.logger.warning("Deal Dive market context lookup failed: %s", exc)
        return {"available": False, "message": "Market data lookup is temporarily unavailable."}


@deal_dive_bp.route("/")
@login_required
def index():
    status_filter = request.args.get("status") or None
    if status_filter and status_filter not in db.STATUSES:
        status_filter = None
    with db.get_connection() as conn:
        deals = db.list_deals(conn, status=status_filter)
    return render_template(
        "tools/deal_dive.html",
        deals=deals,
        statuses=db.STATUSES,
        status_filter=status_filter,
    )


@deal_dive_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_deal():
    if request.method == "POST":
        address = (request.form.get("address") or "").strip()
        city = (request.form.get("city") or "").strip()
        state = (request.form.get("state") or "").strip().upper()
        if not address or not city or not state:
            flash("Address, city, and state are required.", "danger")
            return render_template("tools/deal_dive_new.html", form=request.form)

        with db.get_connection() as conn:
            deal_id = db.create_deal(
                conn,
                {
                    "address": address,
                    "city": city,
                    "state": state,
                    "zip": (request.form.get("zip") or "").strip() or None,
                    "property_type": (request.form.get("property_type") or "").strip() or "Multifamily",
                    "unit_count": _to_int(request.form.get("unit_count")),
                    "status": db.DEFAULT_STATUS,
                },
            )
        flash("Deal created.", "success")
        return redirect(url_for("deal_dive.detail", deal_id=deal_id))

    return render_template("tools/deal_dive_new.html", form={})


@deal_dive_bp.route("/deal/<int:deal_id>")
@login_required
def detail(deal_id):
    with db.get_connection() as conn:
        deal = db.get_deal(conn, deal_id)
        if not deal:
            abort(404)
        comps = db.list_comps(conn, deal_id)
        financial_files = db.list_files(conn, deal_id, category="financial")
        condition_files = db.list_files(conn, deal_id, category="condition")
    market = get_market_context(deal["city"], deal["state"])

    # Read-only: shows whatever's already cached from a prior "Auto-pull
    # market data" action, without ever triggering a fresh RentCast/Google
    # Places API call on a plain page view. Pulling fresh data (or a cache
    # hit) only happens via the explicit POST below.
    address_key = market_data_cache.normalize_address_key(deal["address"], deal["city"], deal["state"], deal.get("zip"))
    with market_data_cache.get_connection() as mconn:
        auto_market_data = market_data_cache.get_cached(mconn, address_key)

    return render_template(
        "tools/deal_dive_detail.html",
        deal=deal,
        comps=comps,
        financial_files=financial_files,
        condition_files=condition_files,
        market=market,
        statuses=db.STATUSES,
        auto_market_data=auto_market_data,
        promoted_market_addresses=_promoted_market_addresses(comps),
    )


@deal_dive_bp.route("/deal/<int:deal_id>/edit", methods=["GET", "POST"])
@login_required
def edit_deal(deal_id):
    with db.get_connection() as conn:
        deal = db.get_deal(conn, deal_id)
        if not deal:
            return _deal_not_found()

        if request.method == "POST":
            address = (request.form.get("address") or "").strip()
            city = (request.form.get("city") or "").strip()
            state = (request.form.get("state") or "").strip().upper()
            if not address or not city or not state:
                flash("Address, city, and state are required.", "danger")
                return render_template("tools/deal_dive_edit.html", deal=deal, form=request.form)

            db.update_deal(
                conn,
                deal_id,
                {
                    "address": address,
                    "city": city,
                    "state": state,
                    "zip": (request.form.get("zip") or "").strip() or None,
                    "property_type": (request.form.get("property_type") or "").strip() or "Multifamily",
                    "unit_count": _to_int(request.form.get("unit_count")),
                },
            )
            flash("Deal updated.", "success")
            return redirect(url_for("deal_dive.detail", deal_id=deal_id))

    return render_template("tools/deal_dive_edit.html", deal=deal, form=deal)


@deal_dive_bp.route("/deal/<int:deal_id>/delete", methods=["POST"])
@login_required
def delete_deal(deal_id):
    with db.get_connection() as conn:
        if not db.get_deal(conn, deal_id):
            return _deal_not_found()
        db.delete_deal(conn, deal_id)

    upload_dir = _upload_dir(deal_id)
    shutil.rmtree(upload_dir, ignore_errors=True)

    flash("Deal deleted.", "success")
    return redirect(url_for("deal_dive.index"))


@deal_dive_bp.route("/deal/<int:deal_id>/status", methods=["POST"])
@login_required
def update_status(deal_id):
    status = (request.form.get("status") or "").strip()
    if status not in db.STATUSES:
        flash("Unrecognized status.", "danger")
        return redirect(url_for("deal_dive.detail", deal_id=deal_id))
    with db.get_connection() as conn:
        if not db.get_deal(conn, deal_id):
            return _deal_not_found()
        db.update_deal_status(conn, deal_id, status)
    flash("Status updated.", "success")
    return redirect(request.referrer or url_for("deal_dive.detail", deal_id=deal_id))


@deal_dive_bp.route("/deal/<int:deal_id>/financials", methods=["POST"])
@login_required
def update_financials(deal_id):
    with db.get_connection() as conn:
        if not db.get_deal(conn, deal_id):
            return _deal_not_found()
        db.update_financials(
            conn,
            deal_id,
            {
                "asking_price": _to_float(request.form.get("asking_price")),
                "purchase_price": _to_float(request.form.get("purchase_price")),
                "current_noi": _to_float(request.form.get("current_noi")),
                "projected_noi": _to_float(request.form.get("projected_noi")),
                "cap_rate": _to_float(request.form.get("cap_rate")),
                "financial_notes": (request.form.get("financial_notes") or "").strip() or None,
            },
        )
    flash("Financials updated.", "success")
    return redirect(url_for("deal_dive.detail", deal_id=deal_id) + "#financials")


@deal_dive_bp.route("/deal/<int:deal_id>/condition", methods=["POST"])
@login_required
def update_condition(deal_id):
    with db.get_connection() as conn:
        if not db.get_deal(conn, deal_id):
            return _deal_not_found()
        db.update_condition(
            conn,
            deal_id,
            {
                "condition_rating": (request.form.get("condition_rating") or "").strip() or None,
                "condition_notes": (request.form.get("condition_notes") or "").strip() or None,
            },
        )
    flash("Condition assessment updated.", "success")
    return redirect(url_for("deal_dive.detail", deal_id=deal_id) + "#condition")


@deal_dive_bp.route("/deal/<int:deal_id>/comps", methods=["POST"])
@login_required
def add_comp(deal_id):
    with db.get_connection() as conn:
        if not db.get_deal(conn, deal_id):
            return _deal_not_found()
        address = (request.form.get("address") or "").strip()[:MAX_COMP_ADDRESS_LEN]
        db.add_comp(
            conn,
            deal_id,
            {
                "comp_type": request.form.get("comp_type") if request.form.get("comp_type") in ("sale", "rental") else "sale",
                "address": address or None,
                "price": _to_float(request.form.get("price")),
                "unit_count": _to_int(request.form.get("unit_count")),
                "comp_date": (request.form.get("comp_date") or "").strip() or None,
                "source_notes": (request.form.get("source_notes") or "").strip() or None,
            },
        )
    flash("Comp added.", "success")
    return redirect(url_for("deal_dive.detail", deal_id=deal_id) + "#comps")


@deal_dive_bp.route("/deal/<int:deal_id>/comps/<int:comp_id>/delete", methods=["POST"])
@login_required
def delete_comp(deal_id, comp_id):
    with db.get_connection() as conn:
        if not db.get_deal(conn, deal_id):
            return _deal_not_found()
        db.delete_comp(conn, deal_id, comp_id)
    flash("Comp removed.", "success")
    return redirect(url_for("deal_dive.detail", deal_id=deal_id) + "#comps")


@deal_dive_bp.route("/deal/<int:deal_id>/market-data", methods=["POST"])
@login_required
def pull_market_data(deal_id):
    """Auto-pull RentCast + Google Places data for this deal's address.
    Uses tools/market_data_service's own cache -- a repeat pull for the
    same address within the staleness window doesn't spend one of
    RentCast's 50 free monthly calls."""
    with db.get_connection() as conn:
        deal = db.get_deal(conn, deal_id)
        if not deal:
            return _deal_not_found()

    force_refresh = request.form.get("force_refresh") == "1"
    result = market_data_service.get_market_data(
        deal["address"], deal["city"], deal["state"], deal.get("zip"), force_refresh=force_refresh
    )

    if result["from_cache"]:
        flash("Loaded market data from cache (already pulled recently).", "success")
    else:
        rentcast_ok = (result.get("rentcast") or {}).get("available")
        google_ok = (result.get("google_places") or {}).get("available")
        if rentcast_ok or google_ok:
            flash("Pulled fresh market data.", "success")
        else:
            flash("Market data pull completed, but neither source returned data for this address.", "warning")

    return redirect(url_for("deal_dive.detail", deal_id=deal_id) + "#comps")


@deal_dive_bp.route("/deal/<int:deal_id>/market-data/promote", methods=["POST"])
@login_required
def promote_market_comp(deal_id):
    """Copy one auto-pulled RentCast comparable into the deal's own manual
    deal_comps table. Auto-pulled data supplements manual entry -- it never
    gets silently merged in on its own, only via this explicit action."""
    address = (request.form.get("address") or "").strip()
    with db.get_connection() as conn:
        if not db.get_deal(conn, deal_id):
            return _deal_not_found()

        if address and address.lower() in _promoted_market_addresses(db.list_comps(conn, deal_id)):
            flash("That comp has already been added.", "info")
            return redirect(url_for("deal_dive.detail", deal_id=deal_id) + "#comps")

        note_parts = []
        for label, key in (("bd", "bedrooms"), ("ba", "bathrooms"), ("sqft", "square_footage")):
            value = request.form.get(key)
            if value:
                note_parts.append(f"{value}{label}")
        distance = request.form.get("distance_miles")
        if distance:
            note_parts.append(f"{distance} mi away")
        source_notes = _MARKET_COMP_SOURCE_PREFIX + (f" ({', '.join(note_parts)})" if note_parts else "")

        db.add_comp(
            conn,
            deal_id,
            {
                "comp_type": "rental",
                "address": address or None,
                "price": _to_float(request.form.get("price")),
                "unit_count": None,
                "comp_date": None,
                "source_notes": source_notes,
            },
        )
    flash("Added to comps.", "success")
    return redirect(url_for("deal_dive.detail", deal_id=deal_id) + "#comps")


@deal_dive_bp.route("/deal/<int:deal_id>/files", methods=["POST"])
@login_required
def upload_file(deal_id):
    category = request.form.get("category")
    if category not in ("financial", "condition"):
        flash("Unrecognized upload category.", "danger")
        return redirect(url_for("deal_dive.detail", deal_id=deal_id))

    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash("No file selected.", "danger")
        return redirect(url_for("deal_dive.detail", deal_id=deal_id) + f"#{category}")

    original_name = secure_filename(upload.filename)
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        flash(f"Unsupported file type: {ext or 'unknown'}.", "danger")
        return redirect(url_for("deal_dive.detail", deal_id=deal_id) + f"#{category}")

    with db.get_connection() as conn:
        if not db.get_deal(conn, deal_id):
            return _deal_not_found()
        stored_name = f"{secrets.token_urlsafe(8)}_{original_name}"
        upload.save(str(_upload_dir(deal_id) / stored_name))
        db.add_file(conn, deal_id, category, original_name, stored_name)

    flash("File uploaded.", "success")
    return redirect(url_for("deal_dive.detail", deal_id=deal_id) + f"#{category}")


@deal_dive_bp.route("/deal/<int:deal_id>/files/<int:file_id>/download")
@login_required
def download_file(deal_id, file_id):
    with db.get_connection() as conn:
        record = db.get_file(conn, deal_id, file_id)
    if not record:
        abort(404)
    file_path = _upload_dir(deal_id) / record["stored_name"]
    if not file_path.exists():
        abort(404)
    return send_file(str(file_path), as_attachment=True, download_name=record["original_name"])
