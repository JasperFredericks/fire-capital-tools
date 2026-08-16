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

import os
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
from tools import underwriting_capex as ucx
from tools import underwriting_crosscheck as uxc
from tools import underwriting_db as db
from tools import underwriting_market as umkt
from tools import quick_analyzer_t12 as qa_t12
from tools import underwriting_property as uprop
from tools import om_db
from tools import om_extract
from tools import upload_limits as ul
from tools import deal_readiness_defaults as readiness
from tools import underwriting_loans_math as ulm
from tools import underwriting_math as um
from tools import underwriting_pnl as pnl_view
from tools import underwriting_pnl_export as pnl_export
from tools import underwriting_scenario_export as sc_export
from tools import underwriting_schedule as us
from tools.form_utils import to_float, to_int
from tools.scorecard_pro.kpis import KPICalculator
from tools.scorecard_pro.parsing import PnLParser
from tools.underwriting_rentroll import UnrecognizedRentRoll, parse_rent_roll_workbook

underwriting_bp = Blueprint("underwriting", __name__)

FEEDBACK_TOOL_NAME = "Underwriting"
ALLOWED_UPLOAD_EXT = {".xlsx", ".xlsm", ".csv"}

# Scoped to the OM route alone. Deliberately NOT added to the constant
# above, which is shared with the rent roll and T12 importers.
OM_UPLOAD_EXT = {".pdf"}

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
        value = (to_int(raw) if key in ("amort_years", "hold_years", "io_years")
                 else to_float(raw))
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


def _crosscheck(scenario, egi, unit_count):
    """Compare the model against the T12 that was imported for it.

    The T12 is re-parsed from the stored upload rather than cached: this
    tool stores no derived figures anywhere (see underwriting_db's header),
    and a cached T12 total is a derived figure that could drift from the
    file it claims to describe. Parsing costs well under a second.

    Every failure degrades to "unavailable" with a reason. A cross-check
    is a reference panel -- no underwriting number depends on it, so a
    missing or unreadable T12 must never take the page down with it.
    """
    has_rentroll = bool(scenario.get("rentroll_source"))
    source = scenario.get("t12_source")
    if not (has_rentroll and source and egi):
        return uxc.build(egi, None, has_rentroll=has_rentroll,
                         has_t12=bool(source), unit_count=unit_count)

    path = _find_upload(scenario["id"], source)
    if path is None:
        return {"available": False, "checks": [], "firing": [],
                "reason": "The imported T12 file is no longer on disk, so it "
                          "cannot be compared against the rent roll."}
    try:
        totals = qa_t12.extract_totals(str(path))
    except Exception as exc:                      # noqa: BLE001 - reference panel
        current_app.logger.info("Cross-check could not read the T12: %s", exc)
        return {"available": False, "checks": [], "firing": [],
                "reason": "The imported T12 could not be re-read for comparison."}
    return uxc.build(egi, totals, has_rentroll=True, has_t12=True,
                     unit_count=unit_count)


def _find_upload(scenario_id, original_name):
    """Locate a stored upload from the original filename it was saved under.

    Uploads are stored as "<token>_<original name>", so the original name
    is a suffix match rather than the filename itself.
    """
    directory = _upload_dir(scenario_id)
    if not directory.exists():
        return None
    for candidate in sorted(directory.iterdir()):
        if candidate.is_file() and candidate.name.endswith(original_name):
            return candidate
    return None


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
        capex_lines = db.list_capex_lines(conn, scenario_id)

    result = error = grid = None
    try:
        result = um.analyze_scenario(scenario, units, expense_lines,
                                     loans=loans,
                                     assumption_years=assumption_years,
                                     capex_lines=capex_lines)
        grid = um.sensitivity_grid(scenario, units, expense_lines,
                                   metric=grid_metric, variable=grid_variable,
                                   loans=loans,
                                   assumption_years=assumption_years,
                                   capex_lines=capex_lines)
    except um.ValidationError as exc:
        error = str(exc)

    readiness_rows = readiness.evaluate(result)
    property_info = uprop.resolve(scenario, (result or {}).get("egi"))
    market = umkt.lookup(property_info["city"], property_info["state"])
    crosscheck = _crosscheck(scenario, (result or {}).get("egi"),
                             property_info["unit_count"]["value"])

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
        capex_lines=capex_lines,
        capex_scopes=ucx.SCOPES,
        capex_scope_labels=ucx.SCOPE_LABELS,
        default_contingency_pct=ucx.DEFAULT_CONTINGENCY_PCT,
        property_info=property_info,
        market=market,
        crosscheck=crosscheck,
        om_documents=_om_documents(scenario_id),
        om_page_cap=om_extract.PAGE_CAP,
        om_pitch_absent_note=om_extract.PITCH_ABSENT_NOTE,
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


def _summary_payload(scenario_id):
    """Everything the scenario export writes, gathered exactly the way the
    detail page gathers it -- so the document and the screen cannot show
    different numbers for the same scenario."""
    with db.get_connection() as conn:
        scenario = db.get_scenario(conn, scenario_id)
        if not scenario:
            return None
        units = db.list_unit_lines(conn, scenario_id)
        expense_lines = db.list_expense_lines(conn, scenario_id)
        loans = db.list_loans(conn, scenario_id)
        assumption_years = db.list_assumption_years(conn, scenario_id)
        capex_lines = db.list_capex_lines(conn, scenario_id)

    try:
        result = um.analyze_scenario(scenario, units, expense_lines, loans=loans,
                                     assumption_years=assumption_years,
                                     capex_lines=capex_lines)
    except um.ValidationError:
        result = None

    property_info = uprop.resolve(scenario, (result or {}).get("egi"))
    return {
        "scenario": scenario,
        "result": result,
        "loans": loans,
        "property_info": property_info,
        "market": umkt.lookup(property_info["city"], property_info["state"]),
        "crosscheck": _crosscheck(scenario, (result or {}).get("egi"),
                                  property_info["unit_count"]["value"]),
    }


@underwriting_bp.route("/scenario/<int:scenario_id>/summary.pdf")
@login_required
def summary_pdf(scenario_id):
    data = _summary_payload(scenario_id)
    if data is None:
        return _not_found()
    name = sc_export.export_filename(data["scenario"], "pdf")
    out_path = _upload_dir(scenario_id) / name
    sc_export.build_pdf(out_path, data, logo_path=branding.logo_png_path(Path(current_app.root_path) / "static"))
    return send_file(str(out_path), as_attachment=True,
                     download_name=name, mimetype="application/pdf")


@underwriting_bp.route("/scenario/<int:scenario_id>/summary.xlsx")
@login_required
def summary_xlsx(scenario_id):
    data = _summary_payload(scenario_id)
    if data is None:
        return _not_found()
    name = sc_export.export_filename(data["scenario"], "xlsx")
    out_path = _upload_dir(scenario_id) / name
    sc_export.build_xlsx(out_path, data)
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
        io_years = request.form.getlist("loan_io_years")

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
                # Blank means no interest-only period, which is None rather
                # than 0 so the column reads as unset. The math treats the
                # two identically.
                "io_years": to_int(io_years[i]) if i < len(io_years) else None,
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


@underwriting_bp.route("/scenario/<int:scenario_id>/property", methods=["POST"])
@login_required
def save_property(scenario_id):
    """Property info: unit count, occupancy, parking, city and state.

    Its own form and its own partial update. Routing it through the
    assumptions save would blank every field this form does not post --
    the purchase price included -- because that path rewrites the whole
    numeric payload and defaults what is missing.

    Blank means blank. An empty unit-count box clears the override and
    returns the page to the rent roll's own figure; it does not mean zero
    and it does not silently keep the previous override.
    """
    with db.get_connection() as conn:
        if not db.get_scenario(conn, scenario_id):
            return _not_found()
        db.update_scenario_partial(conn, scenario_id, {
            "unit_count_override": to_int(request.form.get("unit_count_override")),
            "occupancy_pct_override": to_float(request.form.get("occupancy_pct_override")),
            "parking_spaces": to_int(request.form.get("parking_spaces")),
            "parking_notes": (request.form.get("parking_notes") or "").strip() or None,
            "city": (request.form.get("city") or "").strip() or None,
            "state": (request.form.get("state") or "").strip().upper()[:2] or None,
        }, db.PROPERTY_FIELDS)
    flash("Property info saved.", "success")
    return redirect(url_for("underwriting.detail", scenario_id=scenario_id) + "#property")


@underwriting_bp.route("/scenario/<int:scenario_id>/capex", methods=["POST"])
@login_required
def save_capex(scenario_id):
    """Rewrite the capex budget from the form.

    Whole-list replacement, like the loans form: the page posts the entire
    budget, so a row the user cleared has to disappear rather than linger.
    A row with no label and no cost is treated as an empty slot and
    dropped rather than saved as a zero-dollar line.

    `source` is carried through from a hidden field rather than forced to
    'manual', so that when Site DD one day writes rows here, editing the
    budget around them does not relabel them as hand-entered.
    """
    with db.get_connection() as conn:
        if not db.get_scenario(conn, scenario_id):
            return _not_found()

        lines = []
        for idx in _posted_indexes(request.form, "capex_label_"):
            label = (request.form.get(f"capex_label_{idx}") or "").strip()
            total = to_float(request.form.get(f"capex_total_{idx}"))
            qty = to_float(request.form.get(f"capex_qty_{idx}"))
            unit = to_float(request.form.get(f"capex_unit_{idx}"))
            if not label and total is None and qty is None and unit is None:
                continue
            lines.append({
                "sort_order": len(lines),
                "scope": request.form.get(f"capex_scope_{idx}"),
                "category": request.form.get(f"capex_category_{idx}"),
                "label": label or "Untitled item",
                "quantity": qty, "unit_cost": unit, "total_cost": total,
                "is_contingency": request.form.get(f"capex_contingency_{idx}") == "1",
                "source": request.form.get(f"capex_source_{idx}") or ucx.SOURCE_MANUAL,
                "source_ref": request.form.get(f"capex_source_ref_{idx}") or None,
            })
        db.replace_capex_lines(conn, scenario_id, lines)

        # The contingency percentage lives on the scenario, not on a line.
        # Blank falls back to the default; an explicit 0 is honoured, which
        # is why this is not `or DEFAULT`.
        db.update_scenario_partial(
            conn, scenario_id,
            {"capex_contingency_pct": to_float(request.form.get("capex_contingency_pct"))},
            ("capex_contingency_pct",))

    flash(f"Capex budget saved — {len(lines)} line{'' if len(lines) == 1 else 's'}.", "success")
    return redirect(url_for("underwriting.detail", scenario_id=scenario_id) + "#capex")


def _posted_indexes(form, prefix):
    """Row indexes present in a posted repeating form, in order.

    Read from the keys rather than assuming a contiguous range, so a form
    that skips an index (a row removed in the browser) still saves the
    rows around it.
    """
    out = []
    for key in form:
        if key.startswith(prefix):
            suffix = key[len(prefix):]
            if suffix.isdigit():
                out.append(int(suffix))
    return sorted(set(out))


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

def _save_upload(scenario_id, file_storage, allowed=None):
    """Save an upload under this scenario's directory on the volume.

    `allowed` is per-route rather than global. The OM route accepts PDFs
    and the rent-roll and T12 routes must not: widening the shared
    ALLOWED_UPLOAD_EXT would have let a PDF be posted to the rent-roll
    importer, which reads it as a spreadsheet and fails somewhere less
    legible than here.
    """
    allowed = ALLOWED_UPLOAD_EXT if allowed is None else allowed
    name = secure_filename(file_storage.filename)
    ext = Path(name).suffix.lower()
    if ext not in allowed:
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
        ul.check(request.content_length, ul.SPREADSHEET_BYTES, "rent roll")
    except ul.UploadTooLarge as exc:
        flash(str(exc), "danger")
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
        ul.check(request.content_length, ul.SPREADSHEET_BYTES, "T12")
    except ul.UploadTooLarge as exc:
        flash(str(exc), "danger")
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


# ── Offering memorandum ──────────────────────────────────────────────────
#
# Reference material, never an input. The summary is displayed beside the
# scenario and no route below writes a single scenario column -- there is
# a source-level test asserting that, because the T12 importer a few
# hundred lines up DOES write scenario fields from an upload, and this
# must not become the same shape of thing by drift.

def _om_model_name() -> str:
    return (os.environ.get("OM_EXTRACTION_MODEL")
            or os.environ.get("FIRE_METRICS_SUMMARY_MODEL")
            or "gpt-4.1-mini")


def _om_api_key() -> str:
    return current_app.config.get("OPENAI_API_KEY") or ""


def _om_documents(scenario_id: int) -> list[dict]:
    """Each uploaded OM with its extraction, if one has been paid for."""
    with om_db.get_connection() as conn:
        docs = om_db.list_documents(conn, scenario_id)
        for doc in docs:
            doc["extraction"] = om_db.get_extraction(
                conn, doc["file_sha256"], om_extract.PROMPT_VERSION)
    return docs


def _om_redirect(scenario_id):
    return redirect(url_for("underwriting.detail",
                            scenario_id=scenario_id) + "#om")


@underwriting_bp.route("/scenario/<int:scenario_id>/om", methods=["POST"])
@login_required
def upload_om(scenario_id):
    """Step one of two: read the PDF locally and spend nothing.

    Page count, readability and the cost estimate are all derived from
    the file itself. A scanned OM is refused here, before any call could
    be made, which is the entire reason the gate has two steps.
    """
    with db.get_connection() as conn:
        scenario = db.get_scenario(conn, scenario_id)
    if not scenario:
        return _not_found()

    upload = request.files.get("om")
    if not upload or not upload.filename:
        flash("No OM file selected.", "danger")
        return _om_redirect(scenario_id)
    try:
        ul.check(request.content_length, ul.DOCUMENT_BYTES, "OM")
    except ul.UploadTooLarge as exc:
        flash(str(exc), "danger")
        return _om_redirect(scenario_id)

    try:
        original, path = _save_upload(scenario_id, upload, OM_UPLOAD_EXT)
    except ValueError:
        flash("An offering memorandum must be a PDF.", "danger")
        return _om_redirect(scenario_id)

    data = Path(path).read_bytes()
    try:
        inspection = om_extract.inspect(data)
    except om_extract.OMUnreadable as exc:
        # The file is removed: keeping a PDF whose text cannot be read
        # would leave a document on the volume that no page can use.
        Path(path).unlink(missing_ok=True)
        flash(str(exc), "danger")
        return _om_redirect(scenario_id)

    with om_db.get_connection() as conn:
        om_db.add_document(
            conn, scenario_id,
            file_sha256=om_extract.file_sha256(data),
            original_name=original, stored_name=Path(path).name,
            bytes_=len(data), page_count=inspection["page_count"],
            pages_used=inspection["pages_used"],
            pages_skipped=inspection["pages_skipped"],
            unreadable_pages=inspection["unreadable_pages"])

    note = om_extract.skipped_note(inspection)
    flash(f"{original} read: {inspection['page_count']} pages, "
          f"{len(inspection['readable_pages'])} with readable text. "
          f"Nothing has been sent to OpenAI yet."
          + (f" {note}" if note else ""), "success")
    return _om_redirect(scenario_id)


@underwriting_bp.route("/scenario/<int:scenario_id>/om/<int:doc_id>/extract",
                       methods=["POST"])
@login_required
def extract_om(scenario_id, doc_id):
    """Step two: the only place this feature spends anything.

    A cached extraction for these exact bytes and this prompt version is
    served without a call. Force Refresh is the one way past that, and it
    is an explicit button rather than a default.
    """
    with db.get_connection() as conn:
        scenario = db.get_scenario(conn, scenario_id)
    if not scenario:
        return _not_found()

    with om_db.get_connection() as conn:
        doc = om_db.get_document(conn, doc_id)
    if not doc or doc["scenario_id"] != scenario_id:
        flash("That OM is not on this scenario.", "danger")
        return _om_redirect(scenario_id)

    force = request.form.get("force_refresh") == "1"
    if not force:
        with om_db.get_connection() as conn:
            cached = om_db.get_extraction(conn, doc["file_sha256"],
                                          om_extract.PROMPT_VERSION)
        if cached:
            flash("Already read — showing the stored summary. Nothing was "
                  "sent and nothing was charged.", "success")
            return _om_redirect(scenario_id)

    if not _om_api_key():
        flash("OPENAI_API_KEY is not configured, so no OM can be read.",
              "danger")
        return _om_redirect(scenario_id)

    path = _upload_dir(scenario_id) / doc["stored_name"]
    if not path.exists():
        flash("The stored PDF for this OM is missing.", "danger")
        return _om_redirect(scenario_id)

    data = path.read_bytes()
    try:
        inspection = om_extract.inspect(data)
        extracted = om_extract.extract(data, api_key=_om_api_key(),
                                       model_name=_om_model_name(),
                                       inspection=inspection)
    except om_extract.OMUnreadable as exc:
        flash(str(exc), "danger")
        return _om_redirect(scenario_id)
    except om_extract.OMRejected as exc:
        # The call was made and billed, and the counter already recorded
        # it. What is refused is SHOWING the result, because it failed a
        # check against the document it claims to be quoting.
        flash("The summary that came back did not match the document and "
              "was discarded rather than shown: "
              + "; ".join(exc.reasons[:3]), "danger")
        return _om_redirect(scenario_id)
    except Exception as exc:                          # noqa: BLE001
        flash(f"The OM could not be read: {exc}", "danger")
        return _om_redirect(scenario_id)

    with om_db.get_connection() as conn:
        om_db.save_extraction(
            conn, file_sha256=doc["file_sha256"],
            prompt_version=om_extract.PROMPT_VERSION,
            summary=extracted["summary"], model=extracted["model"],
            prompt_tokens=extracted["prompt_tokens"],
            completion_tokens=extracted["completion_tokens"],
            pages_used=extracted["pages_used"],
            skipped_note=extracted["skipped_note"])

    flash(f"OM read: {extracted['prompt_tokens']:,} prompt and "
          f"{extracted['completion_tokens']:,} completion tokens. "
          "Nothing on this scenario was changed.", "success")
    return _om_redirect(scenario_id)


@underwriting_bp.route("/scenario/<int:scenario_id>/om/<int:doc_id>/file")
@login_required
def om_file(scenario_id, doc_id):
    """The original PDF, kept so a page reference can be checked.

    A summary that cites page 14 is only useful beside the document that
    has a page 14.
    """
    with om_db.get_connection() as conn:
        doc = om_db.get_document(conn, doc_id)
    if not doc or doc["scenario_id"] != scenario_id:
        return _not_found()
    path = _upload_dir(scenario_id) / doc["stored_name"]
    if not path.exists():
        return _not_found()
    return send_file(str(path), as_attachment=False,
                     download_name=doc["original_name"])


@underwriting_bp.route("/scenario/<int:scenario_id>/om/<int:doc_id>/delete",
                       methods=["POST"])
@login_required
def delete_om(scenario_id, doc_id):
    with om_db.get_connection() as conn:
        doc = om_db.get_document(conn, doc_id)
        if not doc or doc["scenario_id"] != scenario_id:
            flash("That OM is not on this scenario.", "danger")
            return _om_redirect(scenario_id)
        om_db.delete_document(conn, doc_id)
    (_upload_dir(scenario_id) / doc["stored_name"]).unlink(missing_ok=True)
    flash(f"Removed {doc['original_name']}.", "success")
    return _om_redirect(scenario_id)
