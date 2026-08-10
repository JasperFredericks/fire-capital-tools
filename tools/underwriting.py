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
    Blueprint, abort, current_app, flash, redirect, render_template, request, url_for,
)
from flask_login import login_required
from werkzeug.utils import secure_filename

from tools import deal_dive_db
from tools import underwriting_db as db
from tools import underwriting_math as um
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
                                  db.list_expense_lines(conn, scenario["id"]))
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

    result = error = grid = None
    try:
        result = um.analyze_scenario(scenario, units, expense_lines)
        grid = um.sensitivity_grid(scenario, units, expense_lines,
                                   metric=grid_metric, variable=grid_variable)
    except um.ValidationError as exc:
        error = str(exc)

    return render_template(
        "tools/underwriting_detail.html",
        scenario=scenario, deal=_deal_for(scenario["deal_id"]),
        units=units, expense_lines=expense_lines, result=result, error=error,
        grid=grid, grid_metric=grid_metric, grid_variable=grid_variable,
        unit_mix=um.unit_mix(units),
        default_categories=um.DEFAULT_EXPENSE_CATEGORIES,
        feedback_tool=FEEDBACK_TOOL_NAME,
    )


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
            lines.append({
                "category_key": l["category_key"], "category_name": l["category_name"],
                "gl_code": l["gl_code"], "label": l["label"], "line_kind": l["line_kind"],
                "annual_amount": to_float(request.form.get(f"amount_{lid}")),
                "growth_pct": to_float(request.form.get(f"growth_{lid}")),
                "is_included": request.form.get(f"included_{lid}") == "1",
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
