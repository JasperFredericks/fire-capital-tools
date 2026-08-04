"""
FIRE Capital Tools - Deal Analyzer (beta).

A quick levered-returns read on one set of assumptions: enter a price,
financing terms, NOI and an exit, get cap rate, cash-on-cash, DSCR, IRR
and equity multiple plus the annual cash flows behind them.

Two modes, one page, mirroring tools/rent_comps.py:
  * Standalone -- screening a lead that isn't in Deal Dive yet.
  * Deal-linked -- arrived at with ?deal_id=N, inputs prefilled from that
    deal's stored figures.

Unlike Rent Comps, deal-linked inputs are *not* locked. Rent Comps locks
the address because a comp set has to belong to a real property; here the
entire point is "what if we paid less / borrowed less / held longer", so
every prefilled value stays editable. Nothing entered here writes back to
the deal.

Stateless by design: no database, no saved scenarios. Results are a pure
function of the submitted inputs (tools/deal_analyzer_math.py), so there
is nothing to persist that couldn't be recomputed. Scenario saving is a
deliberate later iteration, once beta feedback has settled what the input
set should actually be -- persisting a schema now would mean migrating it
the moment those inputs change.

Scope boundary: this is not a pro forma. Unit-level rent rolls, itemized
expenses, capex schedules and sensitivity tables belong in the Underwriting
tool. Deal Dive's Financials tab records what a deal's figures *are*; this
projects what one set of assumptions *returns*.
"""

from __future__ import annotations

from flask import Blueprint, flash, render_template, request
from flask_login import login_required

from tools import deal_analyzer_math as calc
from tools import deal_dive_db
from tools.form_utils import to_float, to_int

deal_analyzer_bp = Blueprint("deal_analyzer", __name__)

FEEDBACK_TOOL_NAME = "Deal Analyzer"

# Starting assumptions for a blank form. Chosen to be plausible rather than
# neutral -- a form of all zeros can't be submitted, and blank fields make
# it unclear which inputs are optional. Every one is editable.
DEFAULTS = {
    "purchase_price": "",
    "closing_costs_pct": "2",
    "ltv_pct": "70",
    "interest_rate_pct": "",
    "amort_years": "30",
    "noi_year1": "",
    "noi_growth_pct": "3",
    "hold_years": "5",
    "exit_cap_pct": "",
    "selling_costs_pct": "2",
}

NUMERIC_FIELDS = (
    "purchase_price", "closing_costs_pct", "ltv_pct", "interest_rate_pct",
    "noi_year1", "noi_growth_pct", "exit_cap_pct", "selling_costs_pct",
)
INTEGER_FIELDS = ("amort_years", "hold_years")


def _deal_context():
    """Resolve deal-linked vs standalone mode. A deal_id that no longer
    exists degrades to standalone with a flash rather than 404ing, matching
    how Deal Dive and Rent Comps handle a deal deleted in another tab."""
    deal_id = to_int(request.args.get("deal_id") or request.form.get("deal_id"))
    if deal_id is None:
        return None, None
    with deal_dive_db.get_connection() as conn:
        deal = deal_dive_db.get_deal(conn, deal_id)
    if not deal:
        flash("That deal could not be found — showing a blank analysis instead.", "warning")
        return None, None
    return deal_id, deal


def _prefill_from_deal(deal):
    """Seed the form from a deal's stored figures. Purchase price falls
    back to the asking price when no purchase price has been agreed yet,
    which is the usual state for something still being analyzed. Cap rate
    seeds the *exit* assumption only as a starting guess -- the going-in
    cap is computed from the numbers, never taken from the record."""
    form = dict(DEFAULTS)
    price = deal.get("purchase_price") or deal.get("asking_price")
    if price is not None:
        form["purchase_price"] = f"{price:.0f}"
    if deal.get("current_noi") is not None:
        form["noi_year1"] = f"{deal['current_noi']:.0f}"
    if deal.get("cap_rate") is not None:
        form["exit_cap_pct"] = f"{deal['cap_rate']:g}"
    return form


def _collect_inputs(form):
    """Coerce the submitted form into the numeric dict analyze() expects.
    Coercion only -- validation of the *combination* is the math module's
    job (calc.ValidationError), so there is exactly one place where the
    rules live."""
    values = {f: to_float(form.get(f)) for f in NUMERIC_FIELDS}
    values.update({f: to_int(form.get(f)) for f in INTEGER_FIELDS})
    return values


@deal_analyzer_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    """GET renders the form (prefilled from ?deal_id=N when present).
    POST computes and re-renders the same page with results below, inputs
    retained so an assumption can be nudged and resubmitted without
    retyping the rest."""
    deal_id, deal = _deal_context()

    if request.method == "POST":
        form = {f: (request.form.get(f) or "").strip() for f in DEFAULTS}
        try:
            result = calc.analyze(_collect_inputs(request.form))
            error = None
        except calc.ValidationError as exc:
            result, error = None, str(exc)
    else:
        form = _prefill_from_deal(deal) if deal else dict(DEFAULTS)
        result, error = None, None

    return render_template(
        "tools/deal_analyzer.html",
        form=form,
        result=result,
        error=error,
        deal=deal,
        deal_id=deal_id,
        feedback_tool=FEEDBACK_TOOL_NAME,
    )
