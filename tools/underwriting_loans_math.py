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

── Interest-only periods ────────────────────────────────────────────────

Built, per loan. `io_years` on a loan row means that many years of
interest-only payments before amortization begins; absent or 0 is an
ordinary loan and takes the arithmetic that was here before.

Two things about it are worth stating rather than inferring.

The convention. When the IO period ends the loan amortizes over its
REMAINING term -- the original amortization minus the IO period -- so it
still matures on its original schedule. The alternative, re-amortizing
over the full original term, lowers the payment and pushes a larger
balloon past the original maturity. Both are real conventions and they
are not interchangeable: on a $4.5M loan with two years of IO they differ
by roughly $27,000 of balloon. Every result carries balloon_convention so
a page never has to assert which one produced its number.

Debt service is a series, not a scalar. That is the part that reached
further than the backlog note predicted: annual_payment() and
balance_after() were named as the two functions to change, but the engine
computed one debt-service figure and subtracted it from every year, so
the per-year loop had to change too. A stack where only the senior loan
is interest-only steps up partway through the hold while the rest stays
level, which no single number describes.
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
        io = loan.get("io_years")
        if io not in (None, ""):
            io_years = int(_f(io))
            if io_years < 0:
                raise LoanValidationError(
                    f"{name}: interest-only period cannot be negative.")
            # An IO period at least as long as the amortization means the
            # loan never retires a dollar of principal. That is not an
            # unusual structure, it is an impossible one as described.
            if io_years >= int(_f(amort)):
                raise LoanValidationError(
                    f"{name}: an interest-only period of {io_years} years with "
                    f"a {int(_f(amort))}-year amortization means the loan never "
                    f"amortizes — the interest-only period must be shorter.")


def io_months_of(loan: dict[str, Any]) -> int:
    """This loan's interest-only period, in months.

    Per loan, not per scenario. The schema carries every other economic
    term on the row already, and the lending matches: an IO period is
    common on senior acquisition debt while a mezzanine piece may be
    interest-only for its whole term or amortizing from day one. Forcing
    one IO period across a stack would model a structure nobody wrote.
    """
    return int(_f(loan.get("io_years"))) * 12


def annual_payment(loan: dict[str, Any]) -> float:
    """This loan's own level annual debt service, ignoring any IO period.

    Unchanged on purpose: this is the fully-amortizing payment, which is
    still a well-defined figure and still what a loan with no IO period
    pays every year. Callers that need the time-varying figure ask
    debt_service_series() for it.
    """
    amount = _f(loan.get("amount"))
    if amount <= 0:
        return 0.0
    return dam.monthly_payment(amount, _f(loan.get("rate_pct")) / 100.0,
                               int(_f(loan.get("amort_years")))) * 12


def debt_service_series(loan: dict[str, Any], hold_years: int) -> list[float]:
    """This loan's annual debt service for each year of the hold.

    With no IO period this is annual_payment() repeated, by the same
    expression, so a stack of ordinary loans totals to exactly what it
    did before interest-only existed.
    """
    amount = _f(loan.get("amount"))
    hold = int(hold_years)
    if amount <= 0:
        return [0.0] * hold
    return dam.annual_debt_service_series(
        amount, _f(loan.get("rate_pct")) / 100.0,
        int(_f(loan.get("amort_years"))), hold,
        io_months=io_months_of(loan))


def balance_after(loan: dict[str, Any], months: int) -> float:
    """This loan's outstanding principal after `months` payments."""
    amount = _f(loan.get("amount"))
    if amount <= 0:
        return 0.0
    return dam.remaining_balance(amount, _f(loan.get("rate_pct")) / 100.0,
                                 int(_f(loan.get("amort_years"))), months,
                                 io_months=io_months_of(loan))


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

    hold = int(hold_years)
    per_loan = []
    running_ds = 0.0
    for idx, loan in enumerate(loans, start=1):
        series = debt_service_series(loan, hold)
        # Year 1 is what the per-loan DSCR has always divided by, and with
        # no IO period it is still the level payment exactly.
        payment = series[0] if series else annual_payment(loan)
        io_years = int(_f(loan.get("io_years")))
        running_ds += payment
        per_loan.append({
            "sort_order": loan.get("sort_order", idx),
            "name": (loan.get("name") or f"Loan {idx}").strip() or f"Loan {idx}",
            "amount": _f(loan.get("amount")),
            "rate_pct": _f(loan.get("rate_pct")),
            "amort_years": int(_f(loan.get("amort_years"))),
            "io_years": io_years,
            "annual_debt_service": payment,
            "monthly_debt_service": payment / 12 if payment else 0.0,
            "debt_service_series": series,
            # What this loan pays once amortization starts. None when it
            # never changes, so the page can say "and nothing changes".
            "post_io_debt_service": (series[io_years]
                                     if io_years and io_years < hold else None),
            "io_covers_whole_hold": bool(io_years and io_years >= hold),
            "balance_at_exit": balance_after(loan, months),
            "dscr": (noi_year1 / payment) if (noi_year1 is not None and payment > 0) else None,
            "cumulative_dscr": (noi_year1 / running_ds)
                               if (noi_year1 is not None and running_ds > 0) else None,
        })

    debt_service = sum(l["annual_debt_service"] for l in per_loan)
    # The stack's debt service year by year: each loan amortizes on its own
    # terms, so a stack where only the senior loan is interest-only steps
    # up partway through while the rest stays level.
    combined_series = [sum(l["debt_service_series"][t] for l in per_loan)
                       for t in range(hold)] if per_loan else [0.0] * hold
    any_io = any(l["io_years"] for l in per_loan)
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
        "debt_service_series": combined_series,
        "has_io": any_io,
        # The stack's own step-up year: the first year its combined debt
        # service differs from year 1. None when nothing steps up.
        "post_io_debt_service": next(
            (v for v in combined_series[1:] if v != combined_series[0]), None)
        if any_io else None,
        "io_covers_whole_hold": bool(per_loan) and all(
            l["io_covers_whole_hold"] for l in per_loan),
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
        # Only sent when a loan in the stack actually has an IO period.
        # Absent, the engine spreads the scalar across the hold exactly as
        # it did before this key existed, so a stack of ordinary loans
        # takes the same path it always took.
        **({"debt_service_series": summary["debt_service_series"]}
           if summary.get("has_io") else {}),
    }
