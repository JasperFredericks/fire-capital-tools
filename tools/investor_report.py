"""
FIRE Capital Tools - Investor Report (beta).

LP/GP distribution waterfalls built on top of a saved Underwriting
scenario: capital contributions in, per-investor distributions, returns and
tier flows out.

Deal-linked only, and requiring an Underwriting scenario. Unlike every
other tool here this one genuinely cannot stand alone -- a waterfall with
no cash flow is nothing -- so a deal without an Underwriting scenario is
told so and pointed at the tool that makes one, rather than being offered
an empty form that could never produce a number.

The distributable cash comes from the source scenario's separated
components (per-year operating cash flow and net sale proceeds), never
re-derived here. Deal Analyzer, Underwriting and this tool therefore all
describe the same deal with the same arithmetic.

Every figure is computed on read. Nothing derived is stored, and
waterfall_math asserts ten conservation invariants on each run -- a result
that cannot prove it conserved the money raises rather than rendering.
WaterfallInvariantError is deliberately NOT caught below: it means the
cascade is wrong, and showing plausible numbers next to a soft warning
would be worse than showing an error.
"""

from __future__ import annotations

from pathlib import Path

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request, url_for,
)
from flask_login import login_required

from tools import deal_dive_db
from tools import investor_report_db as db
from tools import underwriting_db as uw_db
from tools import underwriting_math as um
from tools import gp_split_math as gps
from tools import waterfall_math as wm
from tools.form_utils import to_float, to_int

investor_report_bp = Blueprint("investor_report", __name__)

FEEDBACK_TOOL_NAME = "Investor Report"


def _deal_for(deal_id):
    if deal_id is None:
        return None
    with deal_dive_db.get_connection() as conn:
        return deal_dive_db.get_deal(conn, deal_id)


def _underwriting_result(underwriting_scenario_id: int):
    """Run the source Underwriting scenario to get its cash flows. Returns
    (scenario, returns) or (None, None) if it no longer exists or cannot be
    computed -- a waterfall must not silently fall back to invented cash."""
    with uw_db.get_connection() as conn:
        scenario = uw_db.get_scenario(conn, underwriting_scenario_id)
        if not scenario:
            return None, None
        units = uw_db.list_unit_lines(conn, underwriting_scenario_id)
        lines = uw_db.list_expense_lines(conn, underwriting_scenario_id)
    try:
        return scenario, um.analyze_scenario(scenario, units, lines)
    except um.ValidationError:
        return scenario, None


# ── Index ────────────────────────────────────────────────────────────────

@investor_report_bp.route("/")
@login_required
def index():
    """Deal picker plus the investor register. Without a deal_id there is
    nothing to compute, so this lists what exists rather than offering a
    form that cannot work."""
    deal_id = to_int(request.args.get("deal_id"))
    deal = _deal_for(deal_id)
    if deal_id is not None and not deal:
        flash("That deal could not be found.", "warning")
        deal_id, deal = None, None

    with deal_dive_db.get_connection() as conn:
        deals = deal_dive_db.list_deals(conn)
    with uw_db.get_connection() as conn:
        uw_scenarios = uw_db.list_scenarios(conn, deal_id=deal_id) if deal_id else []
    with db.get_connection() as conn:
        investors = db.list_investors(conn)
        scenarios = db.list_scenarios(conn, deal_id=deal_id)
        contributions = db.list_contributions(conn, deal_id) if deal_id else []

    return render_template(
        "tools/investor_report.html",
        deal=deal, deal_id=deal_id, deals=deals, investors=investors,
        scenarios=scenarios, contributions=contributions,
        uw_scenarios=uw_scenarios,
        total_contributed=sum(c["amount"] or 0.0 for c in contributions),
        feedback_tool=FEEDBACK_TOOL_NAME,
    )


# ── Investors and contributions ──────────────────────────────────────────

@investor_report_bp.route("/investors", methods=["POST"])
@login_required
def add_investor():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("An investor name is required.", "danger")
    else:
        with db.get_connection() as conn:
            db.create_investor(conn, name,
                               (request.form.get("entity_type") or "").strip() or None,
                               (request.form.get("notes") or "").strip() or None)
        flash(f"Investor “{name}” added.", "success")
    return redirect(url_for("investor_report.index", deal_id=to_int(request.form.get("deal_id"))))


@investor_report_bp.route("/contributions", methods=["POST"])
@login_required
def add_contribution():
    deal_id = to_int(request.form.get("deal_id"))
    investor_id = to_int(request.form.get("investor_id"))
    amount = to_float(request.form.get("amount"))
    if not deal_id or not investor_id or amount is None or amount <= 0:
        flash("An investor and a contribution amount greater than zero are required.", "danger")
        return redirect(url_for("investor_report.index", deal_id=deal_id))
    cls = request.form.get("investor_class") or wm.CLASS_LP
    with db.get_connection() as conn:
        db.add_contribution(conn, investor_id, deal_id, amount,
                            (request.form.get("contribution_date") or "").strip() or None,
                            cls if cls in (wm.CLASS_LP, wm.CLASS_GP) else wm.CLASS_LP)
    flash("Capital contribution recorded.", "success")
    return redirect(url_for("investor_report.index", deal_id=deal_id) + "#capital")


@investor_report_bp.route("/contributions/<int:contribution_id>/delete", methods=["POST"])
@login_required
def delete_contribution(contribution_id):
    deal_id = to_int(request.form.get("deal_id"))
    with db.get_connection() as conn:
        db.delete_contribution(conn, contribution_id, deal_id)
    flash("Contribution removed.", "success")
    return redirect(url_for("investor_report.index", deal_id=deal_id) + "#capital")


# ── Waterfall scenarios ──────────────────────────────────────────────────

@investor_report_bp.route("/new", methods=["POST"])
@login_required
def new_scenario():
    deal_id = to_int(request.form.get("deal_id"))
    uw_id = to_int(request.form.get("underwriting_scenario_id"))
    deal = _deal_for(deal_id)
    if not deal:
        flash("A deal is required to build a waterfall.", "danger")
        return redirect(url_for("investor_report.index"))
    if not uw_id:
        flash("An Underwriting scenario is required — it supplies the cash available "
              "to distribute.", "danger")
        return redirect(url_for("investor_report.index", deal_id=deal_id))

    with db.get_connection() as conn:
        sid = db.create_scenario(conn, {
            "deal_id": deal_id, "underwriting_scenario_id": uw_id,
            "name": (request.form.get("name") or "Base waterfall").strip(),
            "property_label": f"{deal['address']}, {deal['city']} {deal['state']}",
            "pref_rate_pct": to_float(request.form.get("pref_rate_pct")) or 8.0,
            "pref_convention": wm.PREF_CONVENTION_ACCRUAL,
            "promote_lp_pct": to_float(request.form.get("promote_lp_pct")) or db.DEFAULT_PROMOTE_LP_PCT,
            "promote_gp_pct": to_float(request.form.get("promote_gp_pct")) or db.DEFAULT_PROMOTE_GP_PCT,
        })
    flash("Waterfall scenario created.", "success")
    return redirect(url_for("investor_report.detail", scenario_id=sid))


@investor_report_bp.route("/scenario/<int:scenario_id>")
@login_required
def detail(scenario_id):
    with db.get_connection() as conn:
        scenario = db.get_scenario(conn, scenario_id)
        if not scenario:
            abort(404)
        tiers = db.list_tiers(conn, scenario_id)
        partners = db.list_gp_partners(conn, scenario_id)
        contributions = db.list_contributions(conn, scenario["deal_id"])

    uw_scenario, uw_result = _underwriting_result(scenario["underwriting_scenario_id"])

    result = error = None
    source_checks: list = []
    if uw_scenario is None:
        error = ("The Underwriting scenario this waterfall was built from no longer "
                 "exists. Rebuild it, or create a new waterfall from a current scenario.")
    elif uw_result is None:
        error = ("The source Underwriting scenario cannot currently be computed — its "
                 "assumptions are incomplete. Fix them there and this report will follow.")
    elif not contributions:
        error = ("No capital contributions recorded for this deal yet. A waterfall needs "
                 "to know who put in what before it can allocate anything.")
    else:
        returns = uw_result["returns"]
        # WaterfallInvariantError is intentionally not caught: it means the
        # cascade is wrong, and an error page is the correct outcome.
        result = wm.run_waterfall(
            contributions,
            wm.periods_from_underwriting(returns),
            {"pref_rate_pct": scenario["pref_rate_pct"],
             "pref_convention": scenario["pref_convention"],
             "tiers": tiers},
        )
        source_checks = wm.verify_against_source(
            result, returns["total_distributions"],
            source_levered_irr=None,
            source_levered_cashflows=returns.get("levered_cashflows"))
        result["invariant_checks"] = wm.check_invariants(result) + source_checks
        # Downstream of the cascade: dividing a number the waterfall has
        # already fixed. GPSplitInvariantError is intentionally not caught,
        # for the same reason WaterfallInvariantError is not.
        gp_split = gps.allocate(result, partners)
        result["gp_split"] = gp_split
        result["invariant_checks"] += gp_split["invariant_checks"]

    return render_template(
        "tools/investor_report_detail.html",
        scenario=scenario, tiers=tiers, contributions=contributions,
        partners=partners,
        deal=_deal_for(scenario["deal_id"]),
        uw_scenario=uw_scenario, uw_result=uw_result,
        result=result, error=error,
        feedback_tool=FEEDBACK_TOOL_NAME,
    )


@investor_report_bp.route("/scenario/<int:scenario_id>/save", methods=["POST"])
@login_required
def save(scenario_id):
    with db.get_connection() as conn:
        if not db.get_scenario(conn, scenario_id):
            flash("That waterfall scenario could not be found.", "danger")
            return redirect(url_for("investor_report.index"))
        gp = to_float(request.form.get("promote_gp_pct"))
        gp = db.DEFAULT_PROMOTE_GP_PCT if gp is None else gp
        db.update_scenario(conn, scenario_id, {
            "name": (request.form.get("name") or "Base waterfall").strip(),
            "pref_rate_pct": to_float(request.form.get("pref_rate_pct")) or 0.0,
            "pref_convention": wm.PREF_CONVENTION_ACCRUAL,
            "promote_lp_pct": 100.0 - gp, "promote_gp_pct": gp,
            "notes": (request.form.get("notes") or "").strip() or None,
        })
    flash("Waterfall terms saved.", "success")
    return redirect(url_for("investor_report.detail", scenario_id=scenario_id))


@investor_report_bp.route("/scenario/<int:scenario_id>/partners", methods=["POST"])
@login_required
def save_gp_partners(scenario_id):
    """Rewrite this scenario's GP partner split.

    Posting an empty set is meaningful: it returns the scenario to a
    single implicit 100% GP bucket, which is the only way back and so
    must be reachable.

    Validated before storing. A set that does not total 100% cannot be
    allocated, and storing it would leave a scenario whose detail page
    raises on open.
    """
    with db.get_connection() as conn:
        if not db.get_scenario(conn, scenario_id):
            flash("That waterfall scenario could not be found.", "danger")
            return redirect(url_for("investor_report.index"))

        names = request.form.getlist("partner_name")
        shares = request.form.getlist("partner_share_pct")
        notes = request.form.getlist("partner_notes")
        investor_ids = request.form.getlist("partner_investor_id")

        partners = []
        for i in range(len(shares)):
            share = to_float(shares[i])
            if share is None:
                continue          # a blank row is an unfilled row, not a 0% partner
            partners.append({
                "sort_order": i,
                "name": (names[i] if i < len(names) else "") or f"Partner {i + 1}",
                "share_pct": share,
                "notes": (notes[i] if i < len(notes) else "") or None,
                "investor_id": to_int(investor_ids[i]) if i < len(investor_ids) else None,
            })

        if partners:
            try:
                gps.validate(gps.normalize(partners))
            except gps.GPSplitError as exc:
                flash(str(exc), "danger")
                return redirect(
                    url_for("investor_report.detail", scenario_id=scenario_id) + "#gp-split")

        db.replace_gp_partners(conn, scenario_id, partners)

    if partners:
        flash(f"GP split saved across {len(partners)} partner"
              f"{'s' if len(partners) != 1 else ''}.", "success")
    else:
        flash("GP split cleared — the promote reports as a single GP bucket.", "success")
    return redirect(url_for("investor_report.detail", scenario_id=scenario_id) + "#gp-split")


@investor_report_bp.route("/scenario/<int:scenario_id>/delete", methods=["POST"])
@login_required
def delete(scenario_id):
    with db.get_connection() as conn:
        scenario = db.get_scenario(conn, scenario_id)
        if scenario:
            db.delete_scenario(conn, scenario_id)
    flash("Waterfall scenario deleted.", "success")
    return redirect(url_for("investor_report.index",
                            deal_id=(scenario or {}).get("deal_id")))


# ── Cross-tool ───────────────────────────────────────────────────────────

def summary_for_deal(deal_id: int) -> dict | None:
    """The deal's latest waterfall, with its actual results.

    Previously this returned only the stored scenario row and a
    contributed total -- everything a caller would want to *show* (LP IRR,
    GP promote, who gets what) had to be recomputed by the caller, which
    is why nothing used it for more than a link.

    It now runs the waterfall and returns the figures themselves. Running
    rather than storing is the same discipline the rest of the report
    follows: a stored IRR that disagrees with the assumptions beneath it
    is the worst failure this tool could have.

    Never raises. A summary card is decoration on someone else's page, so
    a scenario that cannot currently be computed comes back with
    `computed: False` and a reason rather than taking that page down with
    it.
    """
    with db.get_connection() as conn:
        rows = db.list_scenarios(conn, deal_id=deal_id)
        if not rows:
            return None
        latest = rows[0]
        latest["total_count"] = db.count_for_deal(conn, deal_id)
        contributions = db.list_contributions(conn, deal_id)
        latest["contributed"] = sum(c["amount"] or 0.0 for c in contributions)
        tiers = db.list_tiers(conn, latest["id"])
        partners = db.list_gp_partners(conn, latest["id"])

    latest.update({"computed": False, "reason": None, "lp_irr": None,
                   "lp_distributed": None, "gp_promote": None,
                   "gp_split": None, "partners": []})

    uw_scenario, uw_result = _underwriting_result(latest["underwriting_scenario_id"])
    if uw_scenario is None:
        latest["reason"] = "The source Underwriting scenario no longer exists."
        return latest
    if uw_result is None:
        latest["reason"] = "The source Underwriting scenario cannot currently be computed."
        return latest
    if not contributions:
        latest["reason"] = "No capital contributions recorded for this deal yet."
        return latest

    try:
        returns = uw_result["returns"]
        result = wm.run_waterfall(
            contributions, wm.periods_from_underwriting(returns),
            {"pref_rate_pct": latest["pref_rate_pct"],
             "pref_convention": latest["pref_convention"], "tiers": tiers})
        split = gps.allocate(result, partners)
    except (wm.WaterfallError, wm.WaterfallInvariantError,
            gps.GPSplitError, gps.GPSplitInvariantError) as exc:
        latest["reason"] = str(exc)
        return latest

    latest.update({
        "computed": True,
        "lp_contributed": result["lp_aggregate"]["contributed"],
        "lp_distributed": result["lp_aggregate"]["distributed"],
        "lp_irr": result["lp_aggregate"]["irr"],
        "lp_irr_reason": result["lp_aggregate"]["irr_reason"],
        "gp_promote": result["totals"]["gp_distributed"],
        "gp_split": split,
        "partners": split["partners"],
        "total_distributed": result["totals"]["distributed"],
    })
    return latest


def purge_for_deal(deal_id: int, upload_root: Path | None = None) -> list[int]:
    """Called from Deal Dive's delete_deal. Investors survive -- they are
    entity-level and belong to other deals."""
    with db.get_connection() as conn:
        return db.delete_scenarios_for_deal(conn, deal_id)
