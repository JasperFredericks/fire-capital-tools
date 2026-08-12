"""
FIRE Capital Tools - Underwriting calculations.

Builds a NOI series from a rent roll and itemized expenses, then hands that
series to the same returns engine Deal Analyzer uses
(deal_analyzer_math.analyze_noi_series). Pure: no Flask, no database, no
I/O, so every figure here is unit-testable directly.

The division of labour, and why it matters:

    Deal Analyzer   one NOI number in  -> returns out
    Underwriting    rent roll + expense lines -> NOI series -> same engine

Both call the same engine deliberately. Two implementations of IRR would
eventually disagree about the same deal, and the disagreement would be
silent -- the sensitivity grid is computed by that engine too, cell by
cell, for exactly this reason.

Sign convention, stated once because getting it wrong is invisible: every
element of the EGI build-up below GPR is a DEDUCTION and is subtracted.
Other income is the only addition. A T12 may carry deductions as negative
numbers already; this module takes magnitudes and subtracts them, so
callers must pass positive values for losses.
"""

from __future__ import annotations

from typing import Any

from tools import underwriting_loans_math as ulm
from tools.deal_analyzer_math import (  # noqa: F401  (ValidationError re-exported)
    ValidationError,
    analyze_noi_series,
)

# Standard categories for the manual fallback, used when no T12 is
# uploaded. Same shape the T12 import produces, so the form is one form
# either way -- hand-filled or pre-filled.
DEFAULT_EXPENSE_CATEGORIES = (
    ("payroll", "Payroll"),
    ("repairs_maintenance", "Repairs & Maintenance"),
    ("utilities", "Utilities"),
    ("insurance", "Insurance"),
    ("property_tax", "Property Tax"),
    ("management_fee", "Management Fee"),
    ("reserves", "Reserves"),
    ("general_admin", "General & Administrative"),
)

# ── Acquisition costs ────────────────────────────────────────────────────
#
# One-time costs paid at close, itemized instead of estimated as a flat
# percentage of price. Stored in the same expense-lines table with this
# line_kind, which is exactly why the operating-expense functions below
# filter on it: an acquisition cost is a capital outlay at t=0, not an
# annual operating expense, and summing it into NOI would both understate
# NOI and then capitalize the error into the exit value via the cap rate.
# is_included alone is NOT sufficient to separate them -- these lines are
# genuinely "included", just in a different total.
ACQUISITION_COST_KIND = "acquisition_cost"

DEFAULT_ACQUISITION_COST_CATEGORIES = (
    ("legal", "Legal"),
    ("property_inspection", "Property Inspection"),
    ("lead_paint", "Lead Paint"),
    ("environmental", "Environmental"),
    ("appraisal", "Appraisal"),
    ("structural_inspection", "Structural Inspection"),
    ("lender_legal", "Lender Legal"),
    ("doc_prep", "Doc Prep"),
    ("origination_fee", "Origination Fee"),
)

# When an itemized total falls this far below the flat-percentage estimate
# it is more likely half-finished than genuinely cheap, so the result is
# flagged. Deliberately a warning and not a correction: the entered data
# still wins, exactly as the Scorecard Pro override mismatch behaves.
ACQUISITION_SHORTFALL_WARN_PCT = 50.0

# Sensitivity grid geometry.
EXIT_CAP_STEPS = 11      # +/- 1.25% in 0.25 increments
EXIT_CAP_STEP_PCT = 0.25
RENT_GROWTH_MIN_PCT = 1.0
RENT_GROWTH_MAX_PCT = 6.0
RENT_GROWTH_STEPS = 11   # 1.0 .. 6.0 in 0.5 increments
PRICE_STEPS = 11
PRICE_STEP_PCT = 2.5     # +/- 12.5% in 2.5% increments


# ── Rent roll -> Effective Gross Income ──────────────────────────────────

def build_egi(unit_lines: list[dict[str, Any]], assumptions: dict[str, Any]) -> dict[str, Any]:
    """Year-1 income build-up from the rent roll.

        Gross Potential Rent      every unit at market rent, annualized
      - Loss to lease             market - in-place, occupied units only
      - Vacancy                   vacancy_pct of GPR
      - Concessions               concessions_pct of GPR
      - Bad debt                  bad_debt_pct of GPR
      + Other income              annual, typically from the T12
      = Effective Gross Income

    Loss to lease is occupied-only on purpose: a vacant unit's shortfall is
    vacancy, and charging it as loss-to-lease as well would deduct the same
    dollar twice. Negative loss-to-lease (in-place above market) is kept
    rather than floored at zero -- that is real upside and hiding it would
    understate income.
    """
    gpr = 0.0
    ltl = 0.0
    occupied = 0
    total_units = 0

    for u in unit_lines or []:
        total_units += 1
        market = _num(u.get("market_rent"))
        in_place = _num(u.get("in_place_rent"))
        # A unit with no market rent on file falls back to its in-place rent
        # so it still contributes to GPR instead of silently reading as zero.
        effective_market = market if market is not None else in_place
        if effective_market is not None:
            gpr += effective_market * 12
        if _is_occupied(u) and market is not None and in_place is not None:
            occupied += 1
            ltl += (market - in_place) * 12

    vacancy_pct = _pct(assumptions.get("vacancy_pct"))
    concessions_pct = _pct(assumptions.get("concessions_pct"))
    bad_debt_pct = _pct(assumptions.get("bad_debt_pct"))
    other_income = _num(assumptions.get("other_income_annual")) or 0.0

    vacancy = gpr * vacancy_pct
    concessions = gpr * concessions_pct
    bad_debt = gpr * bad_debt_pct

    egi = gpr - ltl - vacancy - concessions - bad_debt + other_income

    return {
        "unit_count": total_units,
        "occupied_units": occupied,
        "gross_potential_rent": gpr,
        "loss_to_lease": ltl,
        "vacancy": vacancy,
        "concessions": concessions,
        "bad_debt": bad_debt,
        "other_income": other_income,
        "effective_gross_income": egi,
    }


def unit_mix(unit_lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per unit-type rollup: count, average sqft, average in-place, average
    market. Averages skip missing values rather than treating them as zero,
    which would drag an average down for no reason."""
    buckets: dict[str, dict[str, Any]] = {}
    for u in unit_lines or []:
        key = (u.get("unit_type") or "Unspecified").strip() or "Unspecified"
        b = buckets.setdefault(key, {"unit_type": key, "count": 0,
                                     "_sqft": [], "_in_place": [], "_market": []})
        b["count"] += 1
        for src, dst in (("sqft", "_sqft"), ("in_place_rent", "_in_place"),
                         ("market_rent", "_market")):
            v = _num(u.get(src))
            if v is not None:
                b[dst].append(v)
    out = []
    for b in buckets.values():
        out.append({
            "unit_type": b["unit_type"],
            "count": b["count"],
            "avg_sqft": _avg(b["_sqft"]),
            "avg_in_place_rent": _avg(b["_in_place"]),
            "avg_market_rent": _avg(b["_market"]),
        })
    return sorted(out, key=lambda r: r["unit_type"])


# ── Expenses ─────────────────────────────────────────────────────────────

def is_acquisition_line(line: dict[str, Any]) -> bool:
    """True for a one-time acquisition cost rather than an annual operating
    expense. Checked by line_kind, never by label -- a category named
    "Legal" is an operating expense on one deal and a closing cost on
    another, so the kind is the only reliable signal."""
    return (line or {}).get("line_kind") == ACQUISITION_COST_KIND


def operating_expense_lines(expense_lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Included lines that are genuinely annual operating expenses.

    Excluded lines (debt service, capex) stay in the list so they remain
    visible and re-includable, but contribute nothing -- debt service is
    modeled by the loan, and counting it here as well would charge it
    twice. Acquisition costs are dropped for a different reason: they are
    a t=0 capital outlay, and letting one into an annual total would
    depress every year's NOI and then be capitalized into the exit price.
    """
    return [l for l in (expense_lines or [])
            if l.get("is_included") and not is_acquisition_line(l)]


def total_operating_expenses(expense_lines: list[dict[str, Any]]) -> float:
    """Year-1 operating expenses. Acquisition costs are not operating
    expenses and are excluded -- see operating_expense_lines()."""
    return sum(_num(l.get("annual_amount")) or 0.0
               for l in operating_expense_lines(expense_lines))


def acquisition_costs(expense_lines: list[dict[str, Any]],
                      purchase_price: Any,
                      closing_costs_pct: Any,
                      acquisition_fee_pct: Any = None) -> dict[str, Any]:
    """Reconcile the itemized acquisition lines against the flat percentage.

    Itemizing OVERRIDES the percentage rather than adding to it: the two
    describe the same money, and the flat percentage exists as an estimate
    for scenarios that have not been itemized yet. Adding them would
    double-count every cost the percentage was already standing in for.

    The override is never silent. Both totals are returned so the caller
    can show the substitution, and a shortfall far below the estimate is
    flagged as probably-incomplete itemization -- the entered data still
    wins, but the reader is told.

    The acquisition fee is different in kind and therefore ALWAYS ADDS,
    whichever of the two above is in use. It is the GP's fee for sourcing
    and closing the deal, not a third-party cost of closing -- none of the
    nine itemized categories covers it, and the flat percentage does not
    stand in for it either. Overriding it away when costs are itemized
    would silently drop a real six-figure use of funds; the override rule
    applies only between the two descriptions of the *same* money.
    """
    lines = [l for l in (expense_lines or [])
             if is_acquisition_line(l) and l.get("is_included")]
    itemized_total = sum(_num(l.get("annual_amount")) or 0.0 for l in lines)

    price = _f(purchase_price)
    flat_total = price * _pct(closing_costs_pct)
    fee_pct = _f(acquisition_fee_pct)
    fee_total = price * (fee_pct / 100.0)

    is_itemized = bool(lines)
    effective = (itemized_total if is_itemized else flat_total) + fee_total

    # Compares itemized against flat only. The fee is excluded deliberately:
    # it is present in neither, so folding it in would make a complete
    # itemization look like a shortfall.
    shortfall_pct = None
    if is_itemized and flat_total > 0:
        shortfall_pct = (flat_total - itemized_total) / flat_total * 100.0

    return {
        "lines": lines,
        "line_count": len(lines),
        "itemized_total": itemized_total,
        "flat_total": flat_total,
        "flat_pct": _f(closing_costs_pct),
        "acquisition_fee_pct": fee_pct,
        "acquisition_fee_total": fee_total,
        "has_acquisition_fee": fee_total > 0,
        # Closing costs alone, before the fee -- so the page can show the
        # substitution and the addition as two separate statements.
        "costs_before_fee": itemized_total if is_itemized else flat_total,
        "is_itemized": is_itemized,
        "effective_total": effective,
        # Percentage the shared engine is actually given, so the displayed
        # dollars and the engine's arithmetic can never disagree.
        "effective_pct": (effective / price * 100.0) if price > 0 else 0.0,
        "shortfall_pct": shortfall_pct,
        "shortfall_warning": bool(
            shortfall_pct is not None and shortfall_pct >= ACQUISITION_SHORTFALL_WARN_PCT
        ),
    }


def project_noi_series(egi_year1: float, expense_lines: list[dict[str, Any]],
                       hold_years: int, rent_growth_pct: float,
                       default_expense_growth_pct: float, *,
                       management_fee_pct: float | None = None) -> dict[str, Any]:
    """NOI for years 1..H plus the forward NOI (year H+1) used at exit.

    Income grows at one rate. Expenses grow per line, each falling back to
    the scenario default when it has no rate of its own -- that is the point
    of an itemized model: property tax and insurance rarely move at the same
    rate as payroll, and a single blended assumption hides that.

    `management_fee_pct` is charged annually as a percentage of that year's
    effective gross income, and is deducted with the other operating
    expenses -- so it reduces NOI, and therefore also the exit value, since
    the exit capitalizes NOI. It needs no growth rate of its own: being a
    percentage of income, it already grows with income.

    None or 0 adds nothing at all, not a zero-valued term, so a scenario
    without a fee is arithmetically untouched.
    """
    if hold_years < 1:
        raise ValidationError("Hold period must be at least 1 year.")

    rg = (rent_growth_pct or 0.0) / 100.0
    default_eg = (default_expense_growth_pct or 0.0) / 100.0
    mgmt = (management_fee_pct or 0.0) / 100.0
    included = operating_expense_lines(expense_lines)

    years = []
    for t in range(1, hold_years + 2):        # one extra year for the exit
        income = egi_year1 * (1 + rg) ** (t - 1)
        expenses = 0.0
        for l in included:
            amt = _num(l.get("annual_amount")) or 0.0
            g = l.get("growth_pct")
            g = default_eg if g is None else (float(g) / 100.0)
            expenses += amt * (1 + g) ** (t - 1)
        management_fee = income * mgmt
        if mgmt:
            expenses += management_fee
        years.append({"year": t, "income": income, "expenses": expenses,
                      "management_fee": management_fee,
                      "noi": income - expenses})

    return {
        "years": years[:hold_years],
        "noi_series": [y["noi"] for y in years[:hold_years]],
        "noi_exit": years[hold_years]["noi"],
        "exit_year_detail": years[hold_years],
    }


# ── Full scenario ────────────────────────────────────────────────────────

def analyze_scenario(scenario: dict[str, Any], unit_lines: list[dict[str, Any]],
                     expense_lines: list[dict[str, Any]], *,
                     loans: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Rent roll + expenses + assumptions -> the full underwriting result.

    Everything after the NOI series is delegated to the shared engine, so
    the returns here and Deal Analyzer's are computed by identical code.

    `loans` is the optional multi-loan stack. Empty or absent -- which is
    every scenario that predates the Loans tab -- keeps single-loan mode:
    the engine sizes one loan from ltv_pct exactly as it always has, and
    the result dict is unchanged in both shape and value.

    With loans present the financing inverts: the stack becomes the input
    and LTV becomes a computed output (see underwriting_loans_math). Only
    the three debt figures change. Income, NOI and the exit are built by
    the same code either way -- a debt stack is not a different property.
    """
    egi = build_egi(unit_lines, scenario)
    hold = int(scenario.get("hold_years") or 0)
    proj = project_noi_series(
        egi["effective_gross_income"], expense_lines, hold,
        _f(scenario.get("rent_growth_pct")), _f(scenario.get("expense_growth_pct")),
        management_fee_pct=scenario.get("management_fee_pct"),
    )
    acq = acquisition_costs(expense_lines, scenario.get("purchase_price"),
                            scenario.get("closing_costs_pct"),
                            scenario.get("acquisition_fee_pct"))
    # The shared engine takes a percentage, not a dollar amount, and is
    # deliberately not modified: itemized costs are handed over as the
    # equivalent percentage instead. Deal Analyzer therefore keeps
    # computing returns with byte-identical code.
    engine_inputs = _engine_inputs(scenario, acq["effective_pct"])

    debt_stack = None
    if loans:
        # Sized against the post-fee NOI series: the management fee is an
        # operating expense, so a DSCR quoted here must be measured on the
        # same NOI the returns are.
        debt_stack = ulm.summarize(loans, hold,
                                   noi_year1=proj["noi_series"][0] if proj["noi_series"] else None,
                                   purchase_price=scenario.get("purchase_price"))
        # LTV is an output now, so the engine is told the implied figure
        # rather than the stale one typed into the scenario -- otherwise
        # its own LTV validation would police a number nothing uses.
        implied = debt_stack["implied_ltv_pct"]
        if implied is not None:
            engine_inputs["ltv_pct"] = implied

    returns = analyze_noi_series(
        engine_inputs, proj["noi_series"], proj["noi_exit"],
        debt=ulm.engine_debt(debt_stack) if debt_stack else None)

    # The management fee is an operating expense of this model, so the
    # year-1 headline has to include it -- otherwise the figure quoted as
    # "operating expenses" would not be the figure actually subtracted to
    # reach the NOI shown beside it.
    management_fee_year1 = proj["years"][0]["management_fee"] if proj["years"] else 0.0

    return {
        "egi": egi,
        "unit_mix": unit_mix(unit_lines),
        "projection": proj,
        "operating_expenses_year1": (total_operating_expenses(expense_lines)
                                     + management_fee_year1),
        "expense_lines_year1": total_operating_expenses(expense_lines),
        "management_fee_year1": management_fee_year1,
        "acquisition_costs": acq,
        "returns": returns,
        # None in single-loan mode, so a template can branch on presence
        # rather than on an empty-list sentinel that reads as "no debt".
        "debt_stack": debt_stack,
    }


def _engine_inputs(scenario: dict[str, Any],
                   closing_costs_pct: float | None = None) -> dict[str, Any]:
    """Map a scenario onto the engine's input contract. noi_year1 and
    noi_growth_pct are required by the shared validator but unused on the
    explicit-series path; they are filled with harmless placeholders rather
    than loosening validation that Deal Analyzer depends on."""
    return {
        "purchase_price": _f(scenario.get("purchase_price")),
        "closing_costs_pct": (_f(scenario.get("closing_costs_pct"))
                              if closing_costs_pct is None else _f(closing_costs_pct)),
        "ltv_pct": _f(scenario.get("ltv_pct")),
        "interest_rate_pct": _f(scenario.get("interest_rate_pct")),
        "amort_years": int(scenario.get("amort_years") or 0),
        "hold_years": int(scenario.get("hold_years") or 0),
        "exit_cap_pct": _f(scenario.get("exit_cap_pct")),
        "selling_costs_pct": _f(scenario.get("selling_costs_pct")),
        # Charged on the gross sale price at exit, alongside selling costs.
        # Deal Analyzer has no such field and never sets this key, so its
        # path through the engine is untouched.
        "capital_transaction_fee_pct": _f(scenario.get("capital_transaction_fee_pct")),
        "noi_year1": 1.0,
        "noi_growth_pct": 0.0,
    }


# ── Sensitivity ──────────────────────────────────────────────────────────

def sensitivity_grid(scenario, unit_lines, expense_lines, metric="levered_irr",
                     variable="rent_growth", *,
                     loans: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Two-variable grid: exit cap rate against either rent growth or
    purchase price.

    Every cell is a full analyze_scenario() run through the same engine as
    the headline figures -- not an approximation, and not a second
    implementation. The base-case cell is flagged so it can be checked
    against the headline; a grid computed with different math than the
    number above it is a bug that is very hard to see and easy to trust.

    121 cells cost well under a hundredth of a second, so this is recomputed
    on render and never cached or stored.

    In multi-loan mode the stack is held fixed across the grid: the loans
    are contracted amounts, so they do not move when the exit cap or the
    rent growth assumption does. Under the price variable this means LTV
    varies across the columns, which is the correct reading -- the same
    debt against a different price is a different LTV.
    """
    base_cap = _f(scenario.get("exit_cap_pct"))
    half = (EXIT_CAP_STEPS - 1) // 2
    caps = [round(base_cap + (i - half) * EXIT_CAP_STEP_PCT, 4) for i in range(EXIT_CAP_STEPS)]

    if variable == "price":
        base_price = _f(scenario.get("purchase_price"))
        ph = (PRICE_STEPS - 1) // 2
        col_values = [round(base_price * (1 + (j - ph) * PRICE_STEP_PCT / 100.0), 2)
                      for j in range(PRICE_STEPS)]
        col_label = "Purchase Price"
        base_col = base_price
    else:
        step = (RENT_GROWTH_MAX_PCT - RENT_GROWTH_MIN_PCT) / (RENT_GROWTH_STEPS - 1)
        col_values = [round(RENT_GROWTH_MIN_PCT + j * step, 4) for j in range(RENT_GROWTH_STEPS)]
        col_label = "Rent Growth %"
        base_col = _f(scenario.get("rent_growth_pct"))

    rows = []
    for cap in caps:
        cells = []
        for col in col_values:
            s = dict(scenario)
            s["exit_cap_pct"] = cap
            s["purchase_price" if variable == "price" else "rent_growth_pct"] = col
            try:
                res = analyze_scenario(s, unit_lines, expense_lines, loans=loans)["returns"]
                value = res.get(metric)
                reason = res.get(f"{metric}_reason")
            except ValidationError as exc:
                value, reason = None, str(exc)
            cells.append({
                "col": col, "value": value, "reason": reason,
                "is_base": _close(cap, base_cap) and _close(col, base_col),
            })
        rows.append({"exit_cap_pct": cap, "cells": cells,
                     "is_base_row": _close(cap, base_cap)})

    return {"metric": metric, "variable": variable, "row_label": "Exit Cap %",
            "col_label": col_label, "col_values": col_values, "rows": rows,
            "base_exit_cap_pct": base_cap, "base_col_value": base_col}


# ── helpers ──────────────────────────────────────────────────────────────

def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _f(v):
    n = _num(v)
    return 0.0 if n is None else n


def _pct(v):
    return (_num(v) or 0.0) / 100.0


def _avg(vals):
    return (sum(vals) / len(vals)) if vals else None


def _close(a, b, tol=1e-9):
    return abs(_f(a) - _f(b)) < tol


_VACANT_MARKERS = ("vacant", "down", "model", "notice")


def _is_occupied(unit) -> bool:
    status = " ".join(str(unit.get("status") or "").strip().lower().split())
    if not status:
        return _num(unit.get("in_place_rent")) not in (None, 0.0)
    if status.startswith("occupied") or status.startswith("current"):
        return True
    return not any(m in status for m in _VACANT_MARKERS)
