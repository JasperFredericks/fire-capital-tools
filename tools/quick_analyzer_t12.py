"""
FIRE Capital Tools - Quick Deal Analyzer T12 extraction.

Turns a parsed T12 into the five totals the valuation form needs:
gross potential income, vacancy loss, other income, operating expenses
and NOI. Nothing else.

WHY THIS IS NOT UNDERWRITING'S T12 IMPORT

Underwriting's upload_t12() runs KPICalculator.category_breakdown() to
produce an editable, itemized expense set: depth-aware leaf detection so
a tree-format P&L is not double-counted, GL codes, per-line growth rates,
and capex/debt-service exclusion defaults. All of that exists to support
a line-by-line model.

This tool has no line items. It needs five numbers. So it uses
KPICalculator.calculate() -- which already computes monthly income,
expenses and NOI -- and sums twelve months. No category breakdown, no
leaf detection, no exclusion defaults, nothing editable. Underwriting's
import is untouched and keeps its own behaviour.

WHY THE DEDUCTION LINE IS "VACANCY & CREDIT LOSS", NOT "VACANCY"

The obvious implementation reads code 4220 (Vacancy Loss) and calls it
vacancy. On Eagle Rock's real T12 that is wrong by $115,439: the rent
deductions between Gross Potential Rent (4110) and Net Rental Income
(4000) are vacancy AND loss-to-lease, bad debt, concessions, discounts
and move-in credits. Reading only 4220 produced a build-up whose NOI was
$112,546 higher than the NOI the same file reports -- a statement that
does not tie, which is the one thing a statement must do.

So the deduction is taken as the whole distance from 4110 to 4000, and
the field is labelled for what it actually contains. Likewise "other
income" is the distance from Net Rental Income to total income, which
picks up the income fallback codes KPICalculator counts (Eagle Rock has
$2,893.67 of them) rather than code 4300 alone.

Every figure is therefore derived from the two totals KPICalculator
itself computes -- income and expenses -- so this module cannot disagree
with Scorecard Pro about what the file says. reconcile() asserts that
before returning: the build-up must reproduce the reported NOI, or
nothing is returned at all.

Pure apart from the parser it is handed: no Flask, no request context,
no filesystem beyond the path the caller already validated.
"""

from __future__ import annotations

from typing import Any

from tools.scorecard_pro.kpis import KPICalculator
from tools.scorecard_pro.parsing import PnLParser

# The two codes read directly. Everything else is derived from
# KPICalculator's own income/expense totals so the arithmetic ties.
CODE_GPR = "4110"                 # Gross Potential Rent
CODE_NET_RENTAL_INCOME = "4000"   # after all rent deductions
CODE_VACANCY_ONLY = "4220"        # reported for context, never used as the deduction

# The build-up must reproduce the reported NOI to within a cent. Wider
# than float noise, far tighter than any real accounting difference.
RECONCILE_TOLERANCE = 0.01


class T12ReconciliationError(AssertionError):
    """The derived build-up does not reproduce the T12's own NOI.

    Deliberately not a T12Unreadable: that means "this file cannot be
    read", which the user fixes by typing figures in. This means "the
    figures were read but do not add up", which is a defect in this
    module and must not be presented to anyone as a valuation.
    """


class T12Unreadable(Exception):
    """The file could not be turned into a usable set of totals.

    Carries a message written for the user, because the caller's whole
    job on failure is to show it and leave the form usable. Every failure
    mode here is recoverable by typing the numbers in by hand -- there is
    no dead end, so the message says what to do next rather than only
    what went wrong.
    """


def extract_totals(path: str) -> dict[str, Any]:
    """Parse a T12 and return the twelve-month totals.

    Raises T12Unreadable, with a message safe to show the user, for every
    failure mode -- an unrecognized workbook, a file with no parseable
    accounts, or one whose months carry no NOI. The caller catches the one
    exception type and falls back to manual entry.
    """
    try:
        parser = PnLParser(str(path))
        parser.parse()
        data = parser.get_data()
    except Exception as exc:
        # PnLParser raises whatever openpyxl and its own sheet lookup
        # raise -- "'T12' sheet not found" among them. The specific text
        # is included because it is often the only clue about which sheet
        # the file actually uses.
        raise T12Unreadable(
            f"That file could not be read as a T12 ({exc}). "
            f"Enter the figures below by hand instead."
        ) from exc

    if not data.get("accounts"):
        raise T12Unreadable(
            "No recognizable accounts were found in that T12. "
            "Enter the figures below by hand instead."
        )

    calc = KPICalculator(data)
    kpis = calc.calculate()

    months = list(calc.available_months)
    if not months:
        raise T12Unreadable(
            "That T12 has no monthly columns to total. "
            "Enter the figures below by hand instead."
        )

    def total(code: str) -> float:
        return sum(calc.get_val(code, m) for m in months)

    def total_kpi(key: str) -> float:
        return sum(float(kpis[key].get(m) or 0.0) for m in months)

    gpr = total(CODE_GPR)
    net_rental_income = total(CODE_NET_RENTAL_INCOME)
    egi = total_kpi("income")
    expenses = total_kpi("expenses")
    noi = total_kpi("noi")

    if gpr <= 0 and egi == 0:
        raise T12Unreadable(
            "That T12 parsed, but carries no rent or income to work from. "
            "Enter the figures below by hand instead."
        )

    # Some files carry no 4110 at all and book everything as other income.
    # That still ties, and still values correctly -- it just looks odd on
    # screen, so the caller is told rather than left to wonder.
    warnings = []
    if gpr <= 0:
        warnings.append(
            "This T12 has no Gross Potential Rent line, so all income is shown "
            "as other income. The NOI is still the file's own figure."
        )
        net_rental_income = 0.0

    # The whole distance from gross potential rent to net rental income:
    # vacancy plus loss-to-lease, bad debt, concessions and discounts.
    deductions = max(0.0, gpr - net_rental_income)
    deduction_pct = (deductions / gpr * 100.0) if gpr else 0.0
    # Everything counted as income that is not net rental income.
    other_income = egi - net_rental_income

    vacancy_only = abs(total(CODE_VACANCY_ONLY))
    if gpr > 0 and deductions > 0:
        warnings.append(
            f"Rent deductions total ${deductions:,.0f} "
            f"({deduction_pct:.1f}% of gross potential rent), of which "
            f"${vacancy_only:,.0f} is vacancy — the rest is loss-to-lease, "
            f"bad debt, concessions and discounts."
        )

    totals = {
        "months": len(months),
        "gross_potential_income": gpr,
        "net_rental_income": net_rental_income,
        "vacancy_loss_only": vacancy_only,
        "deductions": deductions,
        "vacancy_pct": deduction_pct,
        "other_income": other_income,
        "effective_gross_income": egi,
        "operating_expenses": expenses,
        "noi": noi,
        "warnings": warnings,
    }
    reconcile(totals)
    return totals


def reconcile(totals: dict[str, Any]) -> None:
    """Assert the build-up reproduces the file's own NOI.

    Same discipline as underwriting_pnl.reconcile(): the figures shown on
    screen must add up to the figure the tool acts on. If they ever
    diverge, that is a defect in the derivation above, and the right
    outcome is a loud failure rather than a plausible-looking valuation
    built on numbers that do not tie.
    """
    built_egi = (totals["gross_potential_income"] - totals["deductions"]
                 + totals["other_income"])
    built_noi = built_egi - totals["operating_expenses"]

    for name, got, want in (
        ("EGI", built_egi, totals["effective_gross_income"]),
        ("NOI", built_noi, totals["noi"]),
    ):
        if abs(got - want) > RECONCILE_TOLERANCE:
            raise T12ReconciliationError(
                f"T12 build-up does not tie: {name} builds to {got:,.2f} but "
                f"the file reports {want:,.2f} (off by {got - want:,.2f})."
            )
