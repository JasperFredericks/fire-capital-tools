"""
FIRE Capital Tools - Underwriting (beta).

A full pro forma: a rent roll builds effective gross income, an itemized
expense set builds operating expenses, and the resulting NOI series runs
through the same returns engine Deal Analyzer uses, with a two-variable
sensitivity grid over the top.

Where this sits between the existing tools:

    Deal Dive -> Financials   what a deal's figures ARE (a record)
    Deal Analyzer             what ONE NOI number returns (a screen)
    Underwriting              where that NOI comes from (a model)

The rule of thumb offered to the user is "do you already know the NOI, or
do you need to build it?".

Reuse rather than reimplementation, deliberately:
  * returns          deal_analyzer_math.analyze_noi_series -- the same
                     engine, so the two tools can never disagree about the
                     same deal, and every sensitivity cell is computed by it
  * expenses         scorecard_pro PnLParser + KPICalculator.category_breakdown
  * rent roll        tools/underwriting_rentroll (new: nothing existing
                     returns per-unit data)
  * uploads          the shared UPLOAD_FOLDER, already volume-safe

Deal-linked is the primary mode: underwriting something means having its
T12 and rent roll, which means it is real enough to be in Deal Dive.
Standalone is supported for a broker package that arrives first, and then
requires a property_label.
"""

from __future__ import annotations

import secrets
import shutil
from pathlib import Path

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request,
    send_file, url_for,
)
from flask_login import login_required
from werkzeug.utils import secure_filename

from tools import branding
from tools import deal_dive_db
from tools import underwriting_db as db
from tools import deal_readiness_defaults as readiness
from tools import underwriting_loans_math as ulm
from tools import underwriting_math as um
from tools import underwriting_pnl as pnl_view
from tools import underwriting_pnl_export as pnl_export
from tools import underwriting_schedule as us
from tools.form_utils import to_float, to_int
from tools.scorecard_pro.kpis import KPICalculator
from tools.scorecard_pro.parsing import PnLParser
from tools.underwriting_rentroll import UnrecognizedRentRoll, parse_rent_roll_workbook

underwriting_bp = Blueprint("underwriting", __name__)

FEEDBACK_TOOL_NAME = "Underwriting"
ALLOWED_UPLOAD_EXT = {".xlsx", ".xlsm", ".csv"}

DEFAULTS = {
    "closing_costs_pct": 2.0, "ltv_pct": 65.0, "amort_years": 30,
    "hold_years": 5, "selling_costs_pct": 2.0, "vacancy_pct": 5.0,
    "concessions_pct": 1.0, "bad_debt_pct": 0.5,
    "rent_growth_pct": 3.0, "expense_growth_pct": 2.5,
}

# Rows of the side-by-side comparison, in the order an underwriter reads
# them: what the deal costs, what it earns, whether it can service its
# debt, then what it returns. (key, label, format) where the format names
# a filter the template applies -- kept here rather than in the template
# so the row set is defined once.
COMPARE_METRICS = (
    ("going_in_cap_rate", "Going-in Cap Rate", "pct"),
    ("cash_on_cash", "Cash-on-Cash (Yr 1)", "pct"),
    ("dscr", "DSCR (Yr 1)", "ratio"),
    ("levered_irr", "Levered IRR", "pct"),
    ("equity_multiple", "Equity Multiple", "multiple"),
)


def _upload_dir(scenario_id: int) -> Path:
    path = Path(current_app.config["UPLOAD_FOLDER"]) / "underwriting" / str(scenario_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _deal_for(deal_id):
    if deal_id is None:
        return None
    with deal_dive_db.get_connection() as conn:
        return deal_dive_db.get_deal(conn, deal_id)


def _not_found():
    flash("That scenario could not be found — it may have been deleted.", "danger")
    return redirect(url_for("underwriting.index"))


def _scenario_form(form) -> dict:
    """Coerce the assumptions form. Blank numeric fields fall back to the
    default rather than to zero: a blank hold period meaning 'zero years'
    would fail validation confusingly, whereas the default is what the user
    almost certainly meant."""
    out = {}
    for key in db.SCENARIO_NUMERIC:
        raw = form.get(key)
        value = to_int(raw) if key in ("amort_years", "hold_years") else to_float(raw)
        out[key] = DEFAULTS.get(key) if value is None else value
    out["name"] = (form.get("name") or "Base case").strip()
    out["notes"] = (form.get("notes") or "").strip() or None
    return out


def _schedule_rows(scenario, assumption_years):
    """One row per projection year for the per-year assumptions form.

    Every cell is prefilled with the rate actually in force for that year
    -- an explicit override where one exists, otherwise the flat rate
    resolved by carry-forward. So the form opens showing the model as it
    stands, and editing one box overrides exactly one year rather than
    starting from blanks the user has to re-derive.

    `is_override` marks cells the scenario genuinely stores, so the
    template can distinguish "you set this" from "this is the default
    shown for reference".
    """
    schedule = us.normalize(assumption_years)
    hold = int(scenario.get("hold_years") or 0) or 1
    rows = []
    for year in range(1, min(hold, us.MAX_SCHEDULE_YEARS) + 1):
        cells = {}
        for field in us.SCHEDULE_FIELDS:
            cells[field] = {
                "value": us.resolve(schedule, field, scenario.get(field), year),
                "is_override": bool(schedule.get(year, {}).get(field) is not None),
            }
        rows.append({"year": year, "cells": cells})
    return rows


def _investor_summary(deal_id):
    """Investor Report's figures for this deal, or None.

    Imported inside the function rather than at module scope: Deal Dive
    already imports both tools, and a top-level import here would close a
    cycle (investor_report imports underwriting_db, and underwriting
    would import investor_report).

    Never raises. This card is a courtesy on someone else's page -- a
    waterfall that cannot be computed must not take the Underwriting
    scenario down with it, and summary_for_deal already reports that state
    rather than throwing.
    """
    if deal_id is None:
        return None
    try:
        from tools import investor_report
        return investor_report.summary_for_deal(deal_id)
    except Exception:
        return None


# ── Index ────────────────────────────────────────────────────────────────

@underwriting_bp.route("/")
@login_required
def index():
    deal_id = to_int(request.args.get("deal_id"))
    deal = _deal_for(deal_id)
    if deal_id is not None and not deal:
        flash("That deal could not be found — showing all scenarios instead.", "warning")
        deal_id = None

    with db.get_connection() as conn:
        scenarios = db.list_scenarios(conn, deal_id=deal_id, all_scopes=deal_id is None)
        for s in scenarios:
            s["summary"] = _safe_summary(conn, s)

    return render_template("tools/underwriting.html", scenarios=scenarios, deal=deal,
                           deal_id=deal_id, defaults=DEFAULTS,
                           feedback_tool=FEEDBACK_TOOL_NAME)


def _safe_summary(conn, scenario):
    """Headline figures for the list view. Never raises -- a scenario with
    incomplete assumptions should still be listed and openable, not break
    the whole page."""
    try:
        res = um.analyze_scenario(scenario,
                                  db.list_unit_lines(conn, scenario["id"]),
                                  db.list_expense_lines(conn, scenario["id"]),
                                  loans=db.list_loans(conn, scenario["id"]),
                                  assumption_years=db.list_assumption_years(conn, scenario["id"]))
        return {"noi": res["projection"]["noi_series"][0],
                "irr": res["returns"]["levered_irr"],
                "units": res["egi"]["unit_count"]}
    except Exception:
        return None


@underwriting_bp.route("/new", methods=["POST"])
@login_required
def new_scenario():
    deal_id = to_int(request.form.get("deal_id"))
    deal = _deal_for(deal_id)
    if deal_id is not None and not deal:
        flash("That deal could not be found.", "danger")
        return redirect(url_for("underwriting.index"))

    if deal:
        label = f"{deal['address']}, {deal['city']} {deal['state']}"
    else:
        label = (request.form.get("property_label") or "").strip()
        if not label:
            flash("A property name or address is required for a standalone scenario.", "danger")
            return redirect(url_for("underwriting.index"))

    fields = dict(DEFAULTS)
    fields.update({
        "deal_id": deal_id, "property_label": label,
        "name": (request.form.get("name") or "Base case").strip(),
        "purchase_price": to_float(request.form.get("purchase_price"))
                          or (deal or {}).get("purchase_price")
                          or (deal or {}).get("asking_price"),
        "exit_cap_pct": (deal or {}).get("cap_rate") or 6.0,
        "interest_rate_pct": 6.5,
    })
    with db.get_connection() as conn:
        sid = db.create_scenario(conn, fields)
    flash("Scenario created — upload a rent roll and T12, or enter assumptions manually.", "success")
    return redirect(url_for("underwriting.detail", scenario_id=sid))


# ── Detail ───────────────────────────────────────────────────────────────

@underwriting_bp.route("/compare")
@login_required
def compare():
    """Saved scenarios for one deal, side by side.

    Display only. Nothing is created, stored or mutated here -- every
    column is analyze_scenario() run against rows that already exist, the
    same call the detail page makes, so a figure here can never disagree
    with the figure on the scenario's own page.

    A scenario whose assumptions are incomplete raises ValidationError
    rather than producing a number; that column reports the reason instead
    of being silently dropped, since a missing column would read as "no
    scenario" rather than "this one needs attention".
    """
    deal_id = to_int(request.args.get("deal_id"))
    deal = _deal_for(deal_id)
    if deal_id is not None and not deal:
        flash("That deal could not be found.", "warning")
        deal_id, deal = None, None

    with deal_dive_db.get_connection() as conn:
        deals = deal_dive_db.list_deals(conn)

    columns = []
    if deal_id is not None:
        with db.get_connection() as conn:
            scenarios = db.list_scenarios(conn, deal_id=deal_id)
            for sc in scenarios:
                units = db.list_unit_lines(conn, sc["id"])
                lines = db.list_expense_lines(conn, sc["id"])
                try:
                    res = um.analyze_scenario(
                        sc, units, lines,
                        loans=db.list_loans(conn, sc["id"]),
                        assumption_years=db.list_assumption_years(conn, sc["id"]))
                    columns.append({"scenario": sc, "result": res, "error": None})
                except um.ValidationError as exc:
                    columns.append({"scenario": sc, "result": None, "error": str(exc)})

    return render_template(
        "tools/underwriting_compare.html",
        deal=deal, deal_id=deal_id, deals=deals, columns=columns,
        metrics=COMPARE_METRICS,
        feedback_tool=FEEDBACK_TOOL_NAME,
    )


@underwriting_bp.route("/scenario/<int:scenario_id>")
@login_required
def detail(scenario_id):
    grid_metric = request.args.get("metric") or "levered_irr"
    grid_variable = request.args.get("variable") or "rent_growth"
    if grid_metric not in ("levered_irr", "equity_multiple"):
        grid_metric = "levered_irr"
    if grid_variable not in ("rent_growth", "price"):
        grid_variable = "rent_growth"

    with db.get_connection() as conn:
        scenario = db.get_scenario(conn, scenario_id)
        if not scenario:
            abort(404)
        units = db.list_unit_lines(conn, scenario_id)
        expense_lines = db.list_expense_lines(conn, scenario_id)
        loans = db.list_loans(conn, scenario_id)
        assumption_years = db.list_assumption_years(conn, scenario_id)

    result = error = grid = None
    try:
        result = um.analyze_scenario(scenario, units, expense_lines,
                                     loans=loans,
                                     assumption_years=assumption_years)
        grid = um.sensitivity_grid(scenario, units, expense_lines,
                                   metric=grid_metric, variable=grid_variable,
                                   loans=loans,
                                   assumption_years=assumption_years)
    except um.ValidationError as exc:
        error = str(exc)

    readiness_rows = readiness.evaluate(result)

    return render_template(
        "tools/underwriting_detail.html",
        scenario=scenario, deal=_deal_for(scenario["deal_id"]),
        units=units, expense_lines=expense_lines,
        loans=loans, default_amort_years=DEFAULTS["amort_years"],
        assumption_years=assumption_years,
        schedule_rows=_schedule_rows(scenario, assumption_years),
        schedule_fields=us.SCHEDULE_FIELDS,
        max_schedule_years=us.MAX_SCHEDULE_YEARS,
        # The operating-expenses table shows excluded lines deliberately
        # ("shown, not dropped"), so this filters only on kind -- not on
        # is_included -- to keep that behaviour intact.
        operating_lines=[l for l in expense_lines if not um.is_acquisition_line(l)],
        result=result, error=error,
        grid=grid, grid_metric=grid_metric, grid_variable=grid_variable,
        unit_mix=um.unit_mix(units),
        default_categories=um.DEFAULT_EXPENSE_CATEGORIES,
        acquisition_categories=um.DEFAULT_ACQUISITION_COST_CATEGORIES,
        readiness_rows=readiness_rows,
        investor_summary=_investor_summary(scenario["deal_id"]),
        readiness_counts=readiness.counts(readiness_rows),
        acquisition_saved={l["category_key"]: l["annual_amount"]
                           for l in expense_lines if um.is_acquisition_line(l)},
        feedback_tool=FEEDBACK_TOOL_NAME,
    )


# ── Pro-forma P&L ────────────────────────────────────────────────────────
#
# A view over the scenario, not a second model of it: every figure comes
# from the same analyze_scenario() call the detail page uses, and
# build_pnl() refuses to return a statement that does not reconcile to it.

def _load_pnl(scenario_id: int):
    """Scenario -> (scenario, pnl). Shared by the three P&L endpoints so
    the page and both downloads are built from one code path.

    Returns (None, None) when the scenario cannot be computed at all --
    an incomplete scenario has no P&L, and the caller redirects back to
    the detail page where the actual validation error is shown.

    The per-year schedule is loaded and passed for the same reason the
    detail page passes it: a scheduled scenario's income differs year by
    year, and a P&L built without the schedule would quietly show the
    flat-rate model instead -- disagreeing with the page that linked to
    it. Its own reconciliation would not catch that, because it would tie
    perfectly against the wrong result.

    Loans are passed too. They cannot move a single figure on this
    statement -- financing sits below NOI -- but reading the scenario the
    same way everywhere else does means there is no second definition of
    "this scenario" to drift.
    """
    with db.get_connection() as conn:
        scenario = db.get_scenario(conn, scenario_id)
        if not scenario:
            abort(404)
        units = db.list_unit_lines(conn, scenario_id)
        expense_lines = db.list_expense_lines(conn, scenario_id)
        loans = db.list_loans(conn, scenario_id)
        assumption_years = db.list_assumption_years(conn, scenario_id)

    try:
        result = um.analyze_scenario(scenario, units, expense_lines,
                                     loans=loans,
                                     assumption_years=assumption_years)
    except um.ValidationError:
        return scenario, None
    return scenario, pnl_view.build_pnl(scenario, units, expense_lines, result)


def _pnl_unavailable(scenario_id: int):
    flash("This scenario's assumptions are incomplete, so it has no P&L yet.",
          "warning")
    return redirect(url_for("underwriting.detail", scenario_id=scenario_id))


@underwriting_bp.route("/scenario/<int:scenario_id>/pnl")
@login_required
def pnl(scenario_id):
    scenario, statement = _load_pnl(scenario_id)
    if statement is None:
        return _pnl_unavailable(scenario_id)
    return render_template(
        "tools/underwriting_pnl.html",
        scenario=scenario, deal=_deal_for(scenario["deal_id"]),
        pnl=statement, rows=pnl_export.flatten_rows(statement),
        feedback_tool=FEEDBACK_TOOL_NAME,
    )


@underwriting_bp.route("/scenario/<int:scenario_id>/pnl.pdf")
@login_required
def pnl_pdf(scenario_id):
    """Built on demand rather than stored, for the same reason Site DD's
    report is: it is derived entirely from the scenario, so a stored copy
    could only go stale."""
    _scenario, statement = _load_pnl(scenario_id)
    if statement is None:
        return _pnl_unavailable(scenario_id)

    name = pnl_export.export_filename(statement, "pdf")
    out_path = _upload_dir(scenario_id) / name
    pnl_export.build_pdf(
        out_path, statement,
        logo_path=branding.logo_png_path(Path(current_app.root_path) / "static"),
    )
    return send_file(str(out_path), as_attachment=True,
                     download_name=name, mimetype="application/pdf")


@underwriting_bp.route("/scenario/<int:scenario_id>/pnl.xlsx")
@login_required
def pnl_xlsx(scenario_id):
    _scenario, statement = _load_pnl(scenario_id)
    if statement is None:
        return _pnl_unavailable(scenario_id)

    name = pnl_export.export_filename(statement, "xlsx")
    out_path = _upload_dir(scenario_id) / name
    pnl_export.build_xlsx(out_path, statement)
    return send_file(
        str(out_path), as_attachment=True, download_name=name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@underwriting_bp.route("/scenario/<int:scenario_id>/save", methods=["POST"])
@login_required
def save(scenario_id):
    with db.get_connection() as conn:
        if not db.get_scenario(conn, scenario_id):
            return _not_found()
        fields = _scenario_form(request.form)
        fields["property_label"] = (request.form.get("property_label") or "").strip() or "Untitled"
        db.update_scenario(conn, scenario_id, fields)
    flash("Assumptions saved.", "success")
    return redirect(url_for("underwriting.detail", scenario_id=scenario_id))


@underwriting_bp.route("/scenario/<int:scenario_id>/expenses", methods=["POST"])
@login_required
def save_expenses(scenario_id):
    """Rewrite the expense set from the form. Excluded lines are kept with
    is_included=0 rather than dropped, so a line the model chose to exclude
    stays visible and re-includable."""
    with db.get_connection() as conn:
        if not db.get_scenario(conn, scenario_id):
            return _not_found()
        existing = db.list_expense_lines(conn, scenario_id)
        lines = []
        for l in existing:
            lid = str(l["id"])
            # Acquisition costs are edited by their own form and are not
            # rendered as inputs here. Reading them from this request would
            # find nothing and silently blank every amount, so they are
            # carried through untouched instead.
            if um.is_acquisition_line(l):
                lines.append({
                    "category_key": l["category_key"], "category_name": l["category_name"],
                    "gl_code": l["gl_code"], "label": l["label"], "line_kind": l["line_kind"],
                    "annual_amount": l["annual_amount"], "growth_pct": l["growth_pct"],
                    "is_included": l["is_included"],
                    "growth_schedule": l.get("growth_schedule"),
                })
                continue
            lines.append({
                "category_key": l["category_key"], "category_name": l["category_name"],
                "gl_code": l["gl_code"], "label": l["label"], "line_kind": l["line_kind"],
                "annual_amount": to_float(request.form.get(f"amount_{lid}")),
                "growth_pct": to_float(request.form.get(f"growth_{lid}")),
                "is_included": request.form.get(f"included_{lid}") == "1",
                # Per-line schedules are edited by their own form; reading
                # them from this request would find nothing and silently
                # clear every override.
                "growth_schedule": us.dump_line_schedule(
                    us.parse_line_schedule(
                        request.form.get(f"schedule_{lid}")
                        if f"schedule_{lid}" in request.form
                        else l.get("growth_schedule"))),
            })
        # optional manual additions (the no-T12 fallback path)
        for key, label in um.DEFAULT_EXPENSE_CATEGORIES:
            amt = to_float(request.form.get(f"new_amount_{key}"))
            if amt is None:
                continue
            lines.append({"category_key": key, "category_name": label, "gl_code": None,
                          "label": label, "line_kind": "operating", "annual_amount": amt,
                          "growth_pct": to_float(request.form.get(f"new_growth_{key}")),
                          "is_included": True})
        db.replace_expense_lines(conn, scenario_id, lines)
    flash("Expense lines saved.", "success")
    return redirect(url_for("underwriting.detail", scenario_id=scenario_id) + "#expenses")


@underwriting_bp.route("/scenario/<int:scenario_id>/loans", methods=["POST"])
@login_required
def save_loans(scenario_id):
    """Rewrite this scenario's debt stack from the Loans form.

    Posting an empty stack is meaningful, not a no-op: it returns the
    scenario to single-loan mode, where the engine sizes one loan from
    ltv_pct again. That is the only way back, so it must be reachable.

    Rows are read positionally from parallel field arrays. A row whose
    amount is blank is dropped rather than saved as zero -- the "Add
    Mortgage" button appends an empty row, and submitting without filling
    it in should not book a $0 loan.
    """
    with db.get_connection() as conn:
        if not db.get_scenario(conn, scenario_id):
            return _not_found()

        names = request.form.getlist("loan_name")
        amounts = request.form.getlist("loan_amount")
        rates = request.form.getlist("loan_rate_pct")
        amorts = request.form.getlist("loan_amort_years")

        loans = []
        for i in range(len(amounts)):
            amount = to_float(amounts[i])
            if amount is None:
                continue
            loans.append({
                "sort_order": i,
                "name": (names[i] if i < len(names) else "") or f"Loan {i + 1}",
                "amount": amount,
                "rate_pct": to_float(rates[i]) if i < len(rates) else None,
                "amort_years": to_int(amorts[i]) if i < len(amorts) else None,
            })

        # Validated before it is stored: an unmodellable stack saved now is
        # a scenario that cannot be opened later.
        try:
            ulm.validate(loans)
        except ulm.LoanValidationError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("underwriting.detail", scenario_id=scenario_id) + "#loans")

        db.replace_loans(conn, scenario_id, loans)

    if loans:
        flash(f"Saved {len(loans)} loan{'s' if len(loans) != 1 else ''}. "
              "LTV is now computed from the stack.", "success")
    else:
        flash("Debt stack cleared — this scenario is back to single-loan (LTV) mode.",
              "success")
    return redirect(url_for("underwriting.detail", scenario_id=scenario_id) + "#loans")
@underwriting_bp.route("/scenario/<int:scenario_id>/assumption-years", methods=["POST"])
@login_required
def save_assumption_years(scenario_id):
    """Rewrite the per-year assumption schedule.

    A cell equal to the scenario's flat rate is stored as no override at
    all. The form prefills every cell with the rate in force, so without
    this an untouched form would convert the whole scenario to a fully
    scheduled one -- freezing today's flat rates into rows that would then
    stop following a later change to the flat assumption.
    """
    with db.get_connection() as conn:
        scenario = db.get_scenario(conn, scenario_id)
        if not scenario:
            return _not_found()

        rows = []
        hold = int(scenario.get("hold_years") or 0) or 1
        for year in range(1, min(hold, us.MAX_SCHEDULE_YEARS) + 1):
            row = {"year": year}
            for field in us.SCHEDULE_FIELDS:
                value = to_float(request.form.get(f"{field}_y{year}"))
                # to_float() parses form strings; the scenario's own value
                # is already a float off the row, so it is coerced by the
                # schedule module's parser instead of being run back
                # through the form parser.
                flat = us._f(scenario.get(field))
                row[field] = None if (value is None or value == flat) else value
            rows.append(row)

        db.replace_assumption_years(conn, scenario_id, rows)
        stored = db.list_assumption_years(conn, scenario_id)

    if stored:
        flash(f"Per-year assumptions saved — {len(stored)} year"
              f"{'s' if len(stored) != 1 else ''} override the flat rates.", "success")
    else:
        flash("No per-year overrides — this scenario runs on its flat rates.", "success")
    return redirect(url_for("underwriting.detail", scenario_id=scenario_id) + "#peryear")


@underwriting_bp.route("/scenario/<int:scenario_id>/acquisition-costs", methods=["POST"])
@login_required
def save_acquisition_costs(scenario_id):
    """Replace this scenario's itemized acquisition costs.

    Kept separate from the expenses form for the same reason the two are
    separated in the math: these are a one-time capital outlay, not an
    annual operating expense, and mixing them into one form is how one
    ends up in the other's total. Operating lines are carried through
    untouched here, mirroring how that form carries these through.
    """
    with db.get_connection() as conn:
        if not db.get_scenario(conn, scenario_id):
            return _not_found()

        lines = [dict(l) for l in db.list_expense_lines(conn, scenario_id)
                 if not um.is_acquisition_line(l)]

        for key, label in um.DEFAULT_ACQUISITION_COST_CATEGORIES:
            amt = to_float(request.form.get(f"acq_{key}"))
            # A blank field removes the line rather than storing a zero, so
            # "not itemized" and "itemized as nothing" stay distinguishable
            # -- the override only applies when at least one line exists.
            if amt is None:
                continue
            lines.append({
                "category_key": key, "category_name": label, "gl_code": None,
                "label": label, "line_kind": um.ACQUISITION_COST_KIND,
                "annual_amount": amt, "growth_pct": None, "is_included": True,
            })

        db.replace_expense_lines(conn, scenario_id, lines)
    flash("Acquisition costs saved.", "success")
    return redirect(url_for("underwriting.detail", scenario_id=scenario_id) + "#acquisition")


@underwriting_bp.route("/scenario/<int:scenario_id>/delete", methods=["POST"])
@login_required
def delete(scenario_id):
    with db.get_connection() as conn:
        if not db.get_scenario(conn, scenario_id):
            return _not_found()
        db.delete_scenario(conn, scenario_id)
    shutil.rmtree(_upload_dir(scenario_id), ignore_errors=True)
    flash("Scenario deleted.", "success")
    return redirect(url_for("underwriting.index"))


# ── Uploads ──────────────────────────────────────────────────────────────

def _save_upload(scenario_id, file_storage):
    name = secure_filename(file_storage.filename)
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        raise ValueError(f"Unsupported file type: {ext or 'unknown'}.")
    stored = f"{secrets.token_urlsafe(8)}_{name}"
    path = _upload_dir(scenario_id) / stored
    file_storage.save(str(path))
    return name, path


@underwriting_bp.route("/scenario/<int:scenario_id>/rentroll", methods=["POST"])
@login_required
def upload_rentroll(scenario_id):
    with db.get_connection() as conn:
        scenario = db.get_scenario(conn, scenario_id)
    if not scenario:
        return _not_found()

    upload = request.files.get("rentroll")
    if not upload or not upload.filename:
        flash("No rent roll file selected.", "danger")
        return redirect(url_for("underwriting.detail", scenario_id=scenario_id))

    try:
        original, path = _save_upload(scenario_id, upload)
        parsed = parse_rent_roll_workbook(path)
    except UnrecognizedRentRoll as exc:
        flash(str(exc), "danger")
        return redirect(url_for("underwriting.detail", scenario_id=scenario_id))
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("underwriting.detail", scenario_id=scenario_id))

    with db.get_connection() as conn:
        db.replace_unit_lines(conn, scenario_id, parsed["units"])
        db.update_scenario(conn, scenario_id, dict(scenario, rentroll_source=original))
        conn.execute("UPDATE underwriting_scenarios SET rentroll_source = ? WHERE id = ?",
                     (original, scenario_id))
        conn.commit()
    for w in parsed["warnings"]:
        flash(w, "warning")
    flash(f"Rent roll imported — {parsed['unit_count']} units.", "success")
    return redirect(url_for("underwriting.detail", scenario_id=scenario_id) + "#rentroll")


@underwriting_bp.route("/scenario/<int:scenario_id>/t12", methods=["POST"])
@login_required
def upload_t12(scenario_id):
    """Import a T12 and seed the itemized expense lines from it.

    Aggregation is delegated to KPICalculator.category_breakdown(), which is
    depth-aware: a tree-format P&L carries both rollup parents and their
    children, and summing all of them double-counts by roughly 4x on a real
    file. Only leaves become editable lines."""
    with db.get_connection() as conn:
        scenario = db.get_scenario(conn, scenario_id)
    if not scenario:
        return _not_found()

    upload = request.files.get("t12")
    if not upload or not upload.filename:
        flash("No T12 file selected.", "danger")
        return redirect(url_for("underwriting.detail", scenario_id=scenario_id))

    try:
        original, path = _save_upload(scenario_id, upload)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("underwriting.detail", scenario_id=scenario_id))

    parser = PnLParser(str(path))
    parser.parse()
    data = parser.get_data()
    if not data["accounts"]:
        flash("No recognizable accounts were parsed from that T12.", "danger")
        return redirect(url_for("underwriting.detail", scenario_id=scenario_id))

    calc = KPICalculator(data)
    breakdown = calc.category_breakdown()
    lines = [{
        "category_key": l["category_code"], "category_name": l["category_name"],
        "gl_code": l["code"], "label": l["name"], "line_kind": l["line_kind"],
        "annual_amount": l["annual_total"], "growth_pct": None,
        "is_included": l["is_included_default"],
    } for l in breakdown["lines"]]

    other_income = sum(
        sum(v or 0.0 for v in a["data"].values())
        for c, a in data["accounts"].items() if str(c) == "4300")

    with db.get_connection() as conn:
        db.replace_expense_lines(conn, scenario_id, lines)
        conn.execute(
            "UPDATE underwriting_scenarios SET t12_source = ?, other_income_annual = ? WHERE id = ?",
            (original, other_income or scenario.get("other_income_annual"), scenario_id))
        conn.commit()

    excluded = sum(1 for l in lines if not l["is_included"])
    flash(f"T12 imported — {len(lines)} expense lines "
          f"({excluded} excluded by default as debt service or capital items).", "success")
    for d in breakdown["discrepancies"]:
        flash(f"{d['category_name']}: the file's own rollup total "
              f"({d['parent_total']:,.2f}) differs from the sum of its detail lines "
              f"({d['leaf_total']:,.2f}) by {d['difference']:,.2f}. The detail lines are used.",
              "warning")
    return redirect(url_for("underwriting.detail", scenario_id=scenario_id) + "#expenses")


# ── Cross-tool ───────────────────────────────────────────────────────────

def summary_for_deal(deal_id: int) -> dict | None:
    """Backs Deal Dive's Financials link-out."""
    with db.get_connection() as conn:
        rows = db.list_scenarios(conn, deal_id=deal_id)
        if not rows:
            return None
        latest = rows[0]
        latest["summary"] = _safe_summary(conn, latest)
        latest["total_count"] = db.count_for_deal(conn, deal_id)
        return latest


def purge_for_deal(deal_id: int, upload_root: Path) -> list[int]:
    with db.get_connection() as conn:
        ids = db.delete_scenarios_for_deal(conn, deal_id)
    for sid in ids:
        shutil.rmtree(Path(upload_root) / "underwriting" / str(sid), ignore_errors=True)
    return ids
