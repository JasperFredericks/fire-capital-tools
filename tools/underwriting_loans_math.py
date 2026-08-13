"""
Multi-loan debt stack: independent loans, summed.

A scenario's financing is either

  single-loan (the default, and what every existing scenario uses)
      one loan sized by LTV, at one rate, over one amortization -- the
      engine derives it from the scenario's own ltv_pct/interest_rate_pct/
      amort_years fields exactly as it always has

  multi-loan (opt-in, this module)
      a list of loans, each with its own amount, rate and amortization

Multi-loan does not restructure the income model. NOI is built the same
way, the exit is capitalized the same way, and the returns engine is the
same engine. Only three figures change, and each is a plain sum:

    loan amount      = Σ amount
    debt service     = Σ each loan's own annual payment
    balance at exit  = Σ each loan's own remaining balance

Each loan amortizes independently on its own terms. That is the whole
model -- there is no cross-collateralization, no cash sweep, no
sequencing of principal between loans, because none of those were asked
for and each would be a modelling assumption rather than arithmetic.

── The LTV inversion ────────────────────────────────────────────────────

In single-loan mode LTV is an *input*: the user sets it and the loan
follows. In multi-loan mode that direction reverses -- the loans are the
input and LTV becomes an *output*, Σ amount / purchase price. The same
field therefore means something different depending on mode, which is the
one genuine integration risk here, so implied_ltv_pct() exists to make
the computed value explicit rather than leaving a stale typed-in LTV on
screen next to loans that contradict it.

Payment and balance arithmetic is deliberately not reimplemented: both
delegate to deal_analyzer_math, so a single loan entered here and the
same loan entered as an LTV produce identical numbers by construction.

── BACKLOG: interest-only periods ───────────────────────────────────────

Not built, and asked for. Michelle's Quick Deal Analyzer notes included
"needs an IO period"; that request belongs here rather than there, since
a single-point cap-rate valuation has no debt in it at all. Interest-only
exists nowhere in this codebase today -- deal_analyzer_math's header
lists it as explicitly out of scope.

What it would take: a per-loan io_years, a payment of principal * rate /
12 during the IO months, and a balance that stays at the original
principal until amortization begins. annual_payment() and balance_after()
are the two functions that would change, and both are shared with the
single-loan path through deal_analyzer_math -- so this is a shared-engine
change and needs the equivalence discipline every other one has had, not
a quick edit. Scoped separately and deliberately not started here.
"""

from __future__ import annotations

from typing import Any

from tools import deal_analyzer_math as dam

# Guard rails on a single loan row. Wide on purpose -- these reject input
# that is certainly a typo, not input that is merely unusual.
MAX_LOANS = 10
MAX_AMORT_YEARS = 40
MAX_RATE_PCT = 100.0


class LoanValidationError(ValueError):
    """A loan row cannot be modelled as entered."""


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def validate(loans: list[dict[str, Any]]) -> None:
    """Reject a stack that cannot be modelled, before any arithmetic.

    Raised rather than silently coerced: a loan with a blank amount is a
    half-finished entry, and treating it as $0 would quietly understate
    the debt stack on a page whose entire purpose is to total it.
    """
    if len(loans) > MAX_LOANS:
        raise LoanValidationError(f"A scenario can carry at most {MAX_LOANS} loans.")
    for idx, loan in enumerate(loans, start=1):
        name = (loan.get("name") or f"Loan {idx}").strip()
        amount = loan.get("amount")
        if amount is None or _f(amount, -1) < 0:
            raise LoanValidationError(f"{name}: amount must be zero or greater.")
        rate = loan.get("rate_pct")
        if rate is None or _f(rate, -1) < 0:
            raise LoanValidationError(f"{name}: interest rate must be zero or greater.")
        if _f(rate) > MAX_RATE_PCT:
            raise LoanValidationError(f"{name}: interest rate of {rate}% is not plausible.")
        amort = loan.get("amort_years")
        if amort is None or int(_f(amort)) < 1:
            raise LoanValidationError(f"{name}: amortization must be at least 1 year.")
        if int(_f(amort)) > MAX_AMORT_YEARS:
            raise LoanValidationError(
                f"{name}: amortization must be {MAX_AMORT_YEARS} years or less.")


def annual_payment(loan: dict[str, Any]) -> float:
    """This loan's own level annual debt service."""
    amount = _f(loan.get("amount"))
    if amount <= 0:
        return 0.0
    return dam.monthly_payment(amount, _f(loan.get("rate_pct")) / 100.0,
                               int(_f(loan.get("amort_years")))) * 12


def balance_after(loan: dict[str, Any], months: int) -> float:
    """This loan's outstanding principal after `months` payments."""
    amount = _f(loan.get("amount"))
    if amount <= 0:
        return 0.0
    return dam.remaining_balance(amount, _f(loan.get("rate_pct")) / 100.0,
                                 int(_f(loan.get("amort_years"))), months)


def implied_ltv_pct(loans: list[dict[str, Any]], purchase_price: Any) -> float | None:
    """Σ loan amounts ÷ purchase price, as a percentage.

    None when there is no price to divide by -- an unpriced scenario has
    no LTV, and reporting 0% would read as "unlevered".
    """
    price = _f(purchase_price)
    if price <= 0:
        return None
    return total_amount(loans) / price * 100.0


def total_amount(loans: list[dict[str, Any]]) -> float:
    return sum(_f(l.get("amount")) for l in loans or [])


def summarize(loans: list[dict[str, Any]], hold_years: int,
              noi_year1: float | None = None,
              purchase_price: Any = None) -> dict[str, Any]:
    """The debt stack as the engine needs it, plus the per-loan detail the
    screen shows.

    `combined_dscr` is the headline because it is the binding constraint:
    every per-loan DSCR is necessarily at least as large, since each
    divides the same NOI by a smaller slice of the debt service. The
    per-loan figures are reported for per-lender covenants, which differ
    loan by loan, and `cumulative_dscr` walks the stack in seniority order
    so it is visible *where* coverage runs out rather than only that it
    did.
    """
    loans = list(loans or [])
    validate(loans)
    months = int(hold_years) * 12

    per_loan = []
    running_ds = 0.0
    for idx, loan in enumerate(loans, start=1):
        payment = annual_payment(loan)
        running_ds += payment
        per_loan.append({
            "sort_order": loan.get("sort_order", idx),
            "name": (loan.get("name") or f"Loan {idx}").strip() or f"Loan {idx}",
            "amount": _f(loan.get("amount")),
            "rate_pct": _f(loan.get("rate_pct")),
            "amort_years": int(_f(loan.get("amort_years"))),
            "annual_debt_service": payment,
            "monthly_debt_service": payment / 12 if payment else 0.0,
            "balance_at_exit": balance_after(loan, months),
            "dscr": (noi_year1 / payment) if (noi_year1 is not None and payment > 0) else None,
            "cumulative_dscr": (noi_year1 / running_ds)
                               if (noi_year1 is not None and running_ds > 0) else None,
        })

    debt_service = sum(l["annual_debt_service"] for l in per_loan)
    return {
        "loans": per_loan,
        "loan_count": len(per_loan),
        "loan_amount": total_amount(loans),
        "annual_debt_service": debt_service,
        "monthly_debt_service": debt_service / 12 if debt_service else 0.0,
        "balance_at_exit": sum(l["balance_at_exit"] for l in per_loan),
        "implied_ltv_pct": implied_ltv_pct(loans, purchase_price),
        "combined_dscr": (noi_year1 / debt_service)
                         if (noi_year1 is not None and debt_service > 0) else None,
    }


def engine_debt(summary: dict[str, Any]) -> dict[str, Any]:
    """The three figures analyze_noi_series accepts as a debt override.

    Deliberately narrow: the engine is handed only what it would otherwise
    have derived from LTV, so multi-loan mode changes the financing and
    nothing else about how returns are computed.
    """
    return {
        "loan_amount": summary["loan_amount"],
        "annual_debt_service": summary["annual_debt_service"],
        "balance_at_exit": summary["balance_at_exit"],
    }
