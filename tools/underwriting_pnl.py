"""
Pro-forma P&L view over an already-computed Underwriting scenario.

This module computes nothing that underwriting_math has not already
computed. It is a *presentation* of `analyze_scenario()`'s output: the
same income build-up, the same expense lines, the same NOI, re-shaped
into revenue-detail / expense-detail-by-category / NOI rows across the
projection years.

Why a separate module rather than more keys on analyze_scenario():
underwriting_math is the engine Deal Analyzer shares, and a formatting
concern has no business there. Nothing in this file is imported by the
engine, so a change here cannot move a return.

── The one derivation, and why it is safe ───────────────────────────────

project_noi_series() returns per-year *totals* (income, expenses, NOI)
but not the per-category breakdown a P&L needs. Splitting a total into
its parts means re-applying the same growth expression per line, which
is duplicated arithmetic and therefore a drift risk -- exactly the kind
of "formatting layer that quietly became a second calculation" this
view must not be.

That risk is closed by reconciliation rather than by trust: every P&L
built here is checked against the authoritative projection totals, to
the cent, for every year (see reconcile()). If a split ever fails to sum
back to the engine's own total, build_pnl() raises instead of rendering.
A P&L that disagrees with the model it claims to describe is worse than
no P&L, so it is not shown at all.

Sign convention: revenue deductions (loss to lease, vacancy, concessions,
bad debt) carry negative amounts so a column sums straight down to EGI.
Expenses are positive magnitudes, subtracted from EGI to reach NOI.
"""

from __future__ import annotations

from typing import Any

from tools import underwriting_math as um

# The engine's own numeric coercion, aliased rather than reimplemented: a
# value the engine reads as 0.0 must never read as anything else here, and
# a second copy of the rule is a second thing to drift.
_num = um._num

# Half a cent. The split re-sums the same dollars in a different order
# than project_noi_series does, so the two totals can differ by float
# rounding in the region of 1e-10 -- but never by a cent, which would be
# real money. See the module docstring.
RECONCILE_TOLERANCE = 0.005

UNCATEGORIZED = "Uncategorized"


class PnLReconciliationError(AssertionError):
    """A P&L column did not sum back to the engine's own total for that
    year. Raised rather than returned: the caller must not be able to
    render a P&L that disagrees with the scenario."""


# ── Revenue ──────────────────────────────────────────────────────────────

# (key in build_egi's output, label, is_deduction)
REVENUE_ROWS = (
    ("gross_potential_rent", "Gross Potential Rent", False),
    ("loss_to_lease",        "Loss to Lease",        True),
    ("vacancy",              "Vacancy",              True),
    ("concessions",          "Concessions",          True),
    ("bad_debt",             "Bad Debt",             True),
    ("other_income",         "Other Income",         False),
)


def _rent_factor(rent_growth_pct: Any, year: int) -> float:
    """Income growth factor for year `year` (1-based).

    Mirrors project_noi_series exactly: income = egi_year1 * (1+rg)^(t-1).
    Because that scales the whole of EGI by one rate, scaling each of its
    components by the same factor is an identity, not an approximation --
    every revenue column still sums to the engine's income for that year.
    """
    rg = (float(rent_growth_pct or 0.0)) / 100.0
    return (1.0 + rg) ** (year - 1)


def _line_growth(line: dict[str, Any], default_expense_growth_pct: Any) -> float:
    """Per-line expense growth, falling back to the scenario default.

    Same fallback rule as project_noi_series: a line's own rate wins, and
    only a genuinely absent rate (None) inherits the default. A line
    explicitly set to 0% must stay at 0%, not silently inherit.
    """
    g = line.get("growth_pct")
    if g is None:
        return (float(default_expense_growth_pct or 0.0)) / 100.0
    return float(g) / 100.0


def _category_of(line: dict[str, Any]) -> str:
    for key in ("category_name", "category_key"):
        val = (line.get(key) or "").strip()
        if val:
            return val
    return UNCATEGORIZED


def build_revenue(egi: dict[str, Any], rent_growth_pct: Any,
                  years: list[int]) -> list[dict[str, Any]]:
    """Revenue detail rows, one row per component, one amount per year."""
    rows = []
    for key, label, is_deduction in REVENUE_ROWS:
        base = float(egi.get(key) or 0.0)
        # Deductions are stored as positive magnitudes by build_egi and
        # subtracted there; negating here lets a column sum down to EGI.
        signed = -base if is_deduction else base
        rows.append({
            "key": key,
            "label": label,
            "is_deduction": is_deduction,
            "amounts": [signed * _rent_factor(rent_growth_pct, y) for y in years],
        })
    return rows


# ── Expenses ─────────────────────────────────────────────────────────────

def build_expenses(expense_lines: list[dict[str, Any]],
                   default_expense_growth_pct: Any,
                   years: list[int]) -> list[dict[str, Any]]:
    """Expense detail grouped by category, each category carrying its lines.

    Line selection is delegated to um.operating_expense_lines() rather than
    re-filtered here, so "which lines count" has exactly one definition in
    the codebase. Excluded and acquisition lines are therefore absent by
    construction -- see excluded_lines() for what is shown separately.
    """
    included = um.operating_expense_lines(expense_lines)

    groups: dict[str, dict[str, Any]] = {}
    for line in included:
        cat = _category_of(line)
        group = groups.setdefault(cat, {"category": cat, "lines": []})
        amt = float(_num(line.get("annual_amount")) or 0.0)
        g = _line_growth(line, default_expense_growth_pct)
        group["lines"].append({
            "label": line.get("label") or cat,
            "gl_code": line.get("gl_code") or "",
            "growth_pct": line.get("growth_pct"),
            "effective_growth_pct": g * 100.0,
            "amounts": [amt * (1.0 + g) ** (y - 1) for y in years],
        })

    out = []
    for cat in sorted(groups):
        group = groups[cat]
        group["lines"].sort(key=lambda l: (l["label"] or "").lower())
        group["subtotals"] = [
            sum(l["amounts"][i] for l in group["lines"]) for i in range(len(years))
        ]
        group["line_count"] = len(group["lines"])
        out.append(group)
    return out


def excluded_lines(expense_lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Operating lines the scenario deliberately excludes from NOI.

    Listed on the P&L, with zero effect on any total, for the same reason
    the Underwriting screen keeps them visible: a line that vanished would
    read as a line that was never entered.
    """
    return [
        {
            "label": l.get("label") or _category_of(l),
            "category": _category_of(l),
            "gl_code": l.get("gl_code") or "",
            "annual_amount": float(_num(l.get("annual_amount")) or 0.0),
        }
        for l in (expense_lines or [])
        if not l.get("is_included") and not um.is_acquisition_line(l)
    ]


# ── Assembly ─────────────────────────────────────────────────────────────

def build_pnl(scenario: dict[str, Any], unit_lines: list[dict[str, Any]],
              expense_lines: list[dict[str, Any]],
              result: dict[str, Any]) -> dict[str, Any]:
    """Assemble the pro-forma P&L from an existing analyze_scenario result.

    `result` is passed in rather than recomputed, so the P&L cannot
    disagree with the page that linked to it -- the same reason
    site_dd_report takes its scores as an argument.

    Raises PnLReconciliationError if any year's detail fails to sum back
    to the engine's total for that year.
    """
    projection = result["projection"]
    proj_years = projection["years"]
    years = [y["year"] for y in proj_years]

    revenue = build_revenue(result["egi"], scenario.get("rent_growth_pct"), years)
    expenses = build_expenses(expense_lines, scenario.get("expense_growth_pct"), years)

    revenue_totals = [sum(r["amounts"][i] for r in revenue) for i in range(len(years))]
    expense_totals = [sum(g["subtotals"][i] for g in expenses) for i in range(len(years))]
    noi = [revenue_totals[i] - expense_totals[i] for i in range(len(years))]

    pnl = {
        "property_label": scenario.get("property_label") or "Untitled",
        "scenario_name": scenario.get("name") or "Base case",
        "scenario_id": scenario.get("id"),
        "years": years,
        "hold_years": len(years),
        "unit_count": result["egi"].get("unit_count") or len(unit_lines or []),
        "rent_growth_pct": scenario.get("rent_growth_pct"),
        "expense_growth_pct": scenario.get("expense_growth_pct"),
        "revenue": revenue,
        "revenue_totals": revenue_totals,
        "expenses": expenses,
        "expense_totals": expense_totals,
        "noi": noi,
        "excluded": excluded_lines(expense_lines),
        # Year-1 figures restated from the engine untouched, so the header
        # tiles are provably the same numbers the detail page showed.
        "noi_year1_source": proj_years[0]["noi"],
        "opex_year1_source": result["operating_expenses_year1"],
        "egi_year1_source": result["egi"]["effective_gross_income"],
        "margin": [
            (noi[i] / revenue_totals[i]) if revenue_totals[i] else None
            for i in range(len(years))
        ],
    }

    pnl["reconciliation"] = reconcile(pnl, result)
    return pnl


def reconcile(pnl: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    """Check every year's detail against the engine's own totals.

    Raises on the first mismatch. This is the check that makes the module
    docstring's claim -- "a formatting layer, not a calculation" -- an
    asserted property rather than an assurance.
    """
    proj_years = result["projection"]["years"]
    checks: list[dict[str, Any]] = []

    def ok(name: str, year: int, got: float, want: float) -> None:
        diff = abs(got - want)
        passed = diff <= RECONCILE_TOLERANCE
        checks.append({"name": name, "year": year, "got": got,
                       "want": want, "diff": diff, "passed": passed})
        if not passed:
            raise PnLReconciliationError(
                f"P&L {name} for year {year} is {got!r} but the scenario's "
                f"projection says {want!r} (off by {diff}, tolerance "
                f"{RECONCILE_TOLERANCE})")

    for i, py in enumerate(proj_years):
        ok("revenue", py["year"], pnl["revenue_totals"][i], py["income"])
        ok("expenses", py["year"], pnl["expense_totals"][i], py["expenses"])
        ok("NOI", py["year"], pnl["noi"][i], py["noi"])

    # Year 1 is additionally pinned to the two headline figures the rest of
    # the app quotes, which reach here by a different route than the
    # projection does.
    ok("year-1 operating expenses", 1,
       pnl["expense_totals"][0], result["operating_expenses_year1"])
    ok("year-1 EGI", 1, pnl["revenue_totals"][0],
       result["egi"]["effective_gross_income"])
    return checks
