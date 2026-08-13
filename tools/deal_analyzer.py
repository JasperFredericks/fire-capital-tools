"""
FIRE Capital Tools - Quick Deal Analyzer (beta).

One question: given a NOI and a cap rate, what should this property be
worth?

    Gross Potential Income - Vacancy & Credit Loss + Other Income
      = EGI - Operating Expenses = NOI / Cap Rate = Implied Price

Three ways to arrive at the NOI, each labelled on screen so the reader
knows what the price is built on:

  * Build-up      the income and expense lines above      "Estimated"
  * Direct entry  a NOI figure typed straight in          "Entered"
  * T12 upload    twelve months of actuals, parsed        "Actuals — from T12"

Two modes, one page, mirroring tools/rent_comps.py:
  * Standalone -- screening a lead that isn't in Deal Dive yet.
  * Deal-linked -- arrived at with ?deal_id=N, prefilled from that deal.

Deal-linked inputs are not locked. The whole point is "what if the NOI
were lower / the cap rate higher", so every prefilled value stays
editable, and nothing entered here writes back to the deal.

WHAT THIS TOOL USED TO BE

Until this rewrite this page was a levered-returns model: purchase price
in, cap rate / cash-on-cash / DSCR / IRR / equity multiple / annual cash
flows out. That model is not deleted -- it lives in
tools/deal_analyzer_math.py, unchanged and still fully tested, and is
exercised through Underwriting, which does the same job far more
thoroughly from a real rent roll and itemized expenses. What changed is
the direction of this page: price in / returns out became NOI in / price
out. The two tools are now separated by the question they answer rather
than by how much typing they need.

Stateless by design: no database, no saved scenarios, and an uploaded
T12 is parsed and discarded rather than stored. Results are a pure
function of the submitted inputs (tools/quick_analyzer_math.py), so
there is nothing to persist that could not be recomputed.
"""

from __future__ import annotations

import secrets
import shutil
import tempfile
from pathlib import Path

from flask import Blueprint, current_app, flash, render_template, request
from flask_login import login_required
from werkzeug.utils import secure_filename

from tools import deal_dive_db
from tools import quick_analyzer_math as calc
from tools import quick_analyzer_t12 as t12
from tools.form_utils import to_float, to_int

deal_analyzer_bp = Blueprint("deal_analyzer", __name__)

FEEDBACK_TOOL_NAME = "Quick Deal Analyzer"

ALLOWED_UPLOAD_EXT = {".xlsx", ".xlsm"}

# Starting assumptions for a blank form. Plausible rather than neutral --
# a form of all zeros cannot be submitted, and blank fields make it
# unclear which inputs are optional. Every one is editable.
#
# Note what is absent: no NOI growth rate. A single-point valuation has
# no second year to grow into. The field was removed from this page in
# the rewrite, not renamed; it remains in deal_analyzer_math.analyze()
# where it still means something.
DEFAULTS = {
    "gross_potential_income": "",
    "vacancy_pct": "7",
    "other_income": "",
    "expenses_mode": "pct",
    "operating_expenses": "45",
    "noi_direct": "",
    "cap_rate_pct": "",
    "asking_price": "",
    "unit_count": "",
    "range_pct": str(calc.DEFAULT_RANGE_PCT),
    "noi_provenance": calc.PROVENANCE_BUILDUP,
    # A snapshot of what the last T12 import produced. Carried through the
    # form so an "Actuals — from T12" claim can be checked against the
    # figures actually on screen rather than merely asserted. Editing any
    # of them drops the claim; see calc.resolve_provenance().
    "imported_gross_potential_income": "",
    "imported_vacancy_pct": "",
    "imported_other_income": "",
    "imported_operating_expenses": "",
    "imported_noi_direct": "",
}

TEXT_FIELDS = tuple(DEFAULTS)


def _deal_context():
    """Resolve deal-linked vs standalone mode. A deal_id that no longer
    exists degrades to standalone with a flash rather than 404ing,
    matching how Deal Dive and Rent Comps handle a deal deleted in
    another tab."""
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
    """Seed the form from a deal's stored figures.

    The asking price seeds the *comparison*, not the valuation: this tool
    computes what the property is worth and then grades what is being
    asked against it. Feeding the asking price into the valuation itself
    would make the grade circular.
    """
    form = dict(DEFAULTS)
    ask = deal.get("asking_price") or deal.get("purchase_price")
    if ask is not None:
        form["asking_price"] = f"{ask:.0f}"
    if deal.get("current_noi") is not None:
        form["noi_direct"] = f"{deal['current_noi']:.0f}"
        form["noi_provenance"] = calc.PROVENANCE_ENTERED
    if deal.get("cap_rate") is not None:
        form["cap_rate_pct"] = f"{deal['cap_rate']:g}"
    if deal.get("unit_count"):
        form["unit_count"] = str(deal["unit_count"])
    return form


def _collect_inputs(form):
    """Coerce the submitted form into the dict analyze() expects.

    Coercion only -- validation of the *combination* is the math module's
    job (calc.ValidationError), so there is exactly one place where the
    rules live.
    """
    return {
        "gross_potential_income": to_float(form.get("gross_potential_income")),
        "vacancy_pct": to_float(form.get("vacancy_pct")),
        "other_income": to_float(form.get("other_income")),
        "expenses_mode": (form.get("expenses_mode") or "pct").strip(),
        "operating_expenses": to_float(form.get("operating_expenses")),
        "noi_direct": to_float(form.get("noi_direct")),
        "cap_rate_pct": to_float(form.get("cap_rate_pct")),
        "asking_price": to_float(form.get("asking_price")),
        "unit_count": to_int(form.get("unit_count")),
        "range_pct": to_float(form.get("range_pct")),
        "noi_provenance": (form.get("noi_provenance") or calc.PROVENANCE_BUILDUP).strip(),
        "imported": {
            key: to_float(form.get("imported_" + key))
            for key in ("gross_potential_income", "vacancy_pct", "other_income",
                        "operating_expenses", "noi_direct")
        },
    }


def _form_from_request():
    return {f: (request.form.get(f) or "").strip() for f in TEXT_FIELDS}


def _parse_uploaded_t12(file_storage):
    """Save the upload to a temporary directory, parse it, delete it.

    Nothing is kept: this tool stores no scenarios, so a stored file would
    be an orphan the moment the response was rendered. The temp directory
    is removed in a finally block so a parse failure cannot leave the
    upload behind either.
    """
    name = secure_filename(file_storage.filename or "")
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        raise t12.T12Unreadable(
            f"{ext or 'That file type'} is not a spreadsheet this tool can read — "
            f"upload a .xlsx or .xlsm T12, or enter the figures by hand."
        )
    tmpdir = tempfile.mkdtemp(prefix="qa_t12_")
    try:
        path = Path(tmpdir) / f"{secrets.token_urlsafe(8)}_{name}"
        file_storage.save(str(path))
        return t12.extract_totals(str(path))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _form_from_t12(form, totals):
    """Overwrite the income and expense lines with the parsed actuals.

    The expense mode is forced to dollars: a T12 reports what was spent,
    and rounding that into a percentage of EGI just to re-multiply it
    would introduce error for no benefit. NOI is written to the direct
    field as well, so the price is built on the file's own NOI rather
    than on a re-derivation of it.
    """
    updated = dict(form)
    updated.update({
        "gross_potential_income": f"{totals['gross_potential_income']:.2f}",
        # Six decimals, not four. The deduction is a percentage of a figure
        # in the millions, so four decimals leaves the rebuilt NOI about a
        # dollar away from the file's own -- visible if the reader clears
        # the NOI field to watch the build-up recompute. Six keeps it
        # under a cent on a $1.3M rent roll.
        "vacancy_pct": f"{totals['vacancy_pct']:.6f}",
        "other_income": f"{totals['other_income']:.2f}",
        "expenses_mode": "amount",
        "operating_expenses": f"{totals['operating_expenses']:.2f}",
        "noi_direct": f"{totals['noi']:.2f}",
        "noi_provenance": calc.PROVENANCE_T12,
    })
    # The snapshot the provenance claim is checked against.
    for key in ("gross_potential_income", "vacancy_pct", "other_income",
                "operating_expenses", "noi_direct"):
        updated["imported_" + key] = updated[key]
    return updated


@deal_analyzer_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    """GET renders the form (prefilled from ?deal_id=N when present).

    POST does one of two things depending on whether a file came with it:
    a T12 upload prefills the form and then values it if a cap rate is
    already present; anything else values the submitted figures. Both
    re-render the same page with inputs retained, so an assumption can be
    nudged and resubmitted without retyping the rest.
    """
    deal_id, deal = _deal_context()
    result = error = None
    t12_totals = None
    t12_error = None

    if request.method == "POST":
        form = _form_from_request()
        upload = request.files.get("t12")
        # One form, two buttons. Branching on which was pressed rather than
        # on whether a file happens to be attached means pressing Calculate
        # with a file still selected values what is on screen instead of
        # silently re-importing and overwriting it.
        importing = request.form.get("action") == "import" and upload and upload.filename

        if importing:
            # The degraded path. A T12 that cannot be read must never be a
            # dead end: the message says what happened and the form below
            # is left exactly as it was, ready to be filled in by hand.
            try:
                t12_totals = _parse_uploaded_t12(upload)
                form = _form_from_t12(form, t12_totals)
                for w in t12_totals.get("warnings", []):
                    flash(w, "warning")
                flash(f"T12 imported — {t12_totals['months']} months of actuals.", "success")
            except t12.T12Unreadable as exc:
                t12_error = str(exc)
            except t12.T12ReconciliationError as exc:
                # Not a user error. The figures parsed but do not add up,
                # which means this tool would be showing a build-up that
                # disagrees with its own NOI. Refuse rather than render.
                current_app.logger.error("Quick Analyzer T12 reconciliation failed: %s", exc)
                t12_error = (
                    "That T12 parsed, but its totals do not add up to the NOI it "
                    "reports, so no valuation was produced. Enter the figures by hand."
                )

        # Value it when there is something to value. After a T12 upload
        # that is only true once a cap rate is present -- the upload gives
        # a NOI, not a target yield.
        if not t12_error and (form.get("cap_rate_pct") or "").strip():
            try:
                result = calc.analyze(_collect_inputs(form))
            except calc.ValidationError as exc:
                error = str(exc)
        elif not t12_error and not importing:
            error = "Target cap rate is required."
    else:
        form = _prefill_from_deal(deal) if deal else dict(DEFAULTS)

    return render_template(
        "tools/deal_analyzer.html",
        form=form,
        result=result,
        error=error,
        t12_totals=t12_totals,
        t12_error=t12_error,
        deal=deal,
        deal_id=deal_id,
        range_choices=calc.RANGE_CHOICES,
        grade_bands=calc.GRADE_BANDS,
        feedback_tool=FEEDBACK_TOOL_NAME,
    )
