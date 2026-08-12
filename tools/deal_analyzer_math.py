"""
FIRE Capital Tools - Deal Analyzer calculations.

Every number Deal Analyzer shows is produced here. Deliberately pure: no
Flask, no request context, no I/O, no globals -- inputs in, a result dict
out -- so the formulas can be unit-tested directly (tests/
test_deal_analyzer_math.py) rather than only through an HTTP round-trip.
Same standalone principle as tools/market_data_service.py.

Scope is a levered-returns read on *one* set of assumptions: a single
blended NOI growth rate, a single amortizing loan held to sale, and a cap-
rate exit. No unit-level rent roll, no itemized expenses, no interest-only
period, no refinance, no sensitivity tables -- those belong in the
Underwriting tool.

Two conventions worth stating because reasonable models differ:

  * Exit value uses the *forward* NOI (year H+1), which is what a buyer at
    the end of year H would be capitalizing. Using year H's NOI instead
    understates the exit by one year of growth.
  * Cash flows are annual and end-of-year. Debt service is computed from a
    monthly amortization schedule and then annualized, because a monthly
    payment on a 30-year note is not simply the annual-rate equivalent.

Nothing here returns NaN or infinity. Anything that cannot be computed --
DSCR with no debt, an IRR with no sign change -- comes back as None with a
companion reason string, so the caller renders an em dash and an
explanation rather than "nan".
"""

from __future__ import annotations

from typing import Any

# Bisection bounds for the IRR search. The lower bound sits just above
# -100% (a total loss asymptote, where the discount factor blows up) and
# the upper bound at +1000%, comfortably past any plausible real return.
IRR_LOW = -0.9999
IRR_HIGH = 10.0
# Convergence is measured on the *rate* interval, not on the NPV residual
# in dollars. An NPV threshold would be scale-dependent: the same rate
# precision leaves a residual of cents on a $1M deal and fractions of a
# cent on a $10k one, so a fixed dollar tolerance silently gives large
# deals a looser answer. Halving 11.0 down to 1e-12 takes ~44 iterations,
# well inside the cap below.
IRR_TOLERANCE = 1e-12
IRR_MAX_ITERATIONS = 200

MAX_HOLD_YEARS = 30


class ValidationError(ValueError):
    """Raised for input combinations that cannot produce a meaningful
    result at all (as opposed to a single metric that happens to be
    undefined). The caller turns this into a form error, so the message is
    written to be shown to the user directly."""


# ── Loan mechanics ───────────────────────────────────────────────────────

def monthly_payment(principal: float, annual_rate: float, amort_years: int) -> float:
    """Level monthly payment fully amortizing `principal` over
    `amort_years`. Handles a 0% loan as straight-line principal repayment
    rather than dividing by a zero rate."""
    if principal <= 0:
        return 0.0
    n = amort_years * 12
    if n <= 0:
        raise ValidationError("Amortization period must be at least 1 year.")
    r = annual_rate / 12.0
    if r == 0:
        return principal / n
    return principal * r / (1 - (1 + r) ** -n)


def remaining_balance(principal: float, annual_rate: float, amort_years: int, months_paid: int) -> float:
    """Outstanding principal after `months_paid` payments -- the balloon
    that gets retired out of sale proceeds. Never returns a negative
    balance: if the loan fully amortizes within the hold, the balance is
    zero, not an overpayment."""
    if principal <= 0:
        return 0.0
    pmt = monthly_payment(principal, annual_rate, amort_years)
    r = annual_rate / 12.0
    if r == 0:
        return max(0.0, principal - pmt * months_paid)
    grown = (1 + r) ** months_paid
    balance = principal * grown - pmt * ((grown - 1) / r)
    return max(0.0, balance)


# ── IRR ──────────────────────────────────────────────────────────────────

def npv(rate: float, cashflows: list[float]) -> float:
    """NPV of end-of-period cash flows, with cashflows[0] at time zero."""
    return sum(cf / (1 + rate) ** t for t, cf in enumerate(cashflows))


def irr(cashflows: list[float]) -> tuple[float | None, str | None]:
    """IRR by bisection. Returns (rate, None) on success or
    (None, reason) when the rate is not defined.

    Bisection rather than Newton, and pure Python rather than
    numpy-financial: the cash-flow vector here is a handful of annual
    entries, bisection cannot diverge or oscillate on it, and
    numpy_financial.irr() signals failure by silently returning nan --
    which is precisely the outcome this tool must never show. It also
    keeps the dependency list unchanged.

    An IRR only exists where the NPV curve crosses zero. If every dollar
    goes out and none comes back (or vice versa) there is no crossing, and
    that is reported as a reason rather than guessed at."""
    if not cashflows or len(cashflows) < 2:
        return None, "Not enough cash flows to compute a return."
    if all(cf >= 0 for cf in cashflows) or all(cf <= 0 for cf in cashflows):
        return None, "Cash flows never change sign, so there is no rate of return to solve for."

    low, high = IRR_LOW, IRR_HIGH
    npv_low, npv_high = npv(low, cashflows), npv(high, cashflows)
    if npv_low * npv_high > 0:
        return None, (
            "No internal rate of return exists between -100% and +1000% for these cash flows "
            "— the deal never returns the capital invested."
        )

    for _ in range(IRR_MAX_ITERATIONS):
        mid = (low + high) / 2
        if (high - low) / 2 < IRR_TOLERANCE:
            return mid, None
        value = npv(mid, cashflows)
        if value * npv_low > 0:
            low, npv_low = mid, value
        else:
            high = mid
    return (low + high) / 2, None


# ── Validation ───────────────────────────────────────────────────────────

def _validate(i: dict[str, Any]) -> None:
    """Reject input combinations that make the whole calculation
    meaningless, before any arithmetic runs. A single undefined *metric*
    (DSCR on an all-cash deal) is not an error and is handled downstream."""
    if i["purchase_price"] is None or i["purchase_price"] <= 0:
        raise ValidationError("Purchase price must be greater than zero.")
    if i["noi_year1"] is None:
        raise ValidationError("Year-1 NOI is required.")
    if i["ltv_pct"] is None or i["ltv_pct"] < 0:
        raise ValidationError("LTV must be zero or greater.")
    if i["ltv_pct"] > 100:
        raise ValidationError("LTV cannot exceed 100% — the loan would be larger than the purchase price.")
    if i["exit_cap_pct"] is None or i["exit_cap_pct"] <= 0:
        raise ValidationError("Exit cap rate must be greater than zero — a zero cap rate implies an infinite sale price.")
    if i["hold_years"] is None or i["hold_years"] < 1:
        raise ValidationError("Hold period must be at least 1 year.")
    if i["hold_years"] > MAX_HOLD_YEARS:
        raise ValidationError(f"Hold period must be {MAX_HOLD_YEARS} years or less.")
    if i["amort_years"] is None or i["amort_years"] < 1:
        raise ValidationError("Amortization period must be at least 1 year.")
    if i["interest_rate_pct"] is None or i["interest_rate_pct"] < 0:
        raise ValidationError("Interest rate must be zero or greater.")
    if i["closing_costs_pct"] is None or i["closing_costs_pct"] < 0:
        raise ValidationError("Closing costs must be zero or greater.")
    if i["selling_costs_pct"] is None or i["selling_costs_pct"] < 0:
        raise ValidationError("Selling costs must be zero or greater.")
    if i["noi_growth_pct"] is None:
        raise ValidationError("NOI growth rate is required (enter 0 for flat NOI).")


# ── Main entry point ─────────────────────────────────────────────────────

def analyze(inputs: dict[str, Any]) -> dict[str, Any]:
    """Single-growth-rate projection: the Deal Analyzer entry point.

    A thin wrapper over analyze_noi_series() -- it derives the NOI series
    from one growth rate and hands off. Underwriting builds its series line
    by line from a rent roll and itemized expenses and calls the core
    directly, so both tools compute returns with the same engine rather
    than two implementations that can drift apart on the same deal."""
    _validate(inputs)
    noi1 = float(inputs["noi_year1"])
    g = float(inputs["noi_growth_pct"]) / 100.0
    H = int(inputs["hold_years"])
    return analyze_noi_series(
        inputs,
        noi_series=[noi1 * (1 + g) ** (t - 1) for t in range(1, H + 1)],
        noi_exit=noi1 * (1 + g) ** H,
    )


def analyze_noi_series(inputs: dict[str, Any], noi_series: list[float],
                       noi_exit: float,
                       debt: dict[str, Any] | None = None) -> dict[str, Any]:
    """The engine. Capital stack, debt service, cash flows, returns.

    `noi_series` is NOI for operating years 1..H, one entry per year, in
    order. `noi_exit` is the *forward* NOI (year H+1) that a buyer at the
    end of the hold would capitalize -- passed explicitly rather than
    inferred, because a caller that builds NOI from itemized line items has
    no single growth rate to extrapolate from, and guessing one here would
    silently disagree with the model the user actually entered.

    `debt` is an optional override for the three financing figures this
    function would otherwise derive from a single LTV-sized loan:
    loan_amount, annual_debt_service and balance_at_exit. It exists for
    Underwriting's multi-loan mode, where a stack of independently
    amortizing loans cannot be described by one rate and one term.

    When `debt` is None -- which is every Deal Analyzer call, and every
    single-loan Underwriting scenario -- the code below is exactly what it
    was before the override existed, so the default path cannot have
    moved. A test asserts that equivalence directly.

    Everything downstream of the NOI series -- exit, IRR, multiples -- is
    identical for both callers, and for both financing modes, by
    construction."""
    _validate(inputs)

    H = int(inputs["hold_years"])
    if len(noi_series) != H:
        raise ValidationError(
            f"Internal error: {len(noi_series)} NOI years supplied for a "
            f"{H}-year hold."
        )

    P = float(inputs["purchase_price"])
    cc_pct = float(inputs["closing_costs_pct"]) / 100.0
    ltv = float(inputs["ltv_pct"]) / 100.0
    rate = float(inputs["interest_rate_pct"]) / 100.0
    amort_years = int(inputs["amort_years"])
    exit_cap = float(inputs["exit_cap_pct"]) / 100.0
    sc_pct = float(inputs["selling_costs_pct"]) / 100.0

    closing_costs = P * cc_pct
    loan = P * ltv if debt is None else float(debt["loan_amount"])
    equity = P - loan + closing_costs

    if equity <= 0:
        raise ValidationError(
            "Total cash invested works out to zero or less — reduce LTV or add closing costs."
        )

    if debt is None:
        debt_service = monthly_payment(loan, rate, amort_years) * 12 if loan > 0 else 0.0
    else:
        debt_service = float(debt["annual_debt_service"])

    # Year-by-year operations, from the supplied NOI series.
    years = []
    cumulative = 0.0
    for t in range(1, H + 1):
        noi_t = float(noi_series[t - 1])
        cf_t = noi_t - debt_service
        cumulative += cf_t
        years.append({
            "year": t,
            "noi": noi_t,
            "debt_service": debt_service,
            "cash_flow": cf_t,
            "cumulative_cash_flow": cumulative,
        })

    operating_cf = [y["cash_flow"] for y in years]

    # Exit. Capitalize the *forward* NOI (year H+1) -- what a buyer at the
    # end of the hold would underwrite -- not the final year's own NOI.
    noi_exit = float(noi_exit)
    gross_sale = noi_exit / exit_cap
    selling_costs = gross_sale * sc_pct
    balance_at_exit = (remaining_balance(loan, rate, amort_years, H * 12)
                       if debt is None else float(debt["balance_at_exit"]))
    net_sale_levered = gross_sale - selling_costs - balance_at_exit
    net_sale_unlevered = gross_sale - selling_costs

    # Levered: equity out at t0, operating cash flow, plus sale net of debt.
    levered_flows = [-equity] + operating_cf[:]
    levered_flows[-1] += net_sale_levered
    levered_irr, levered_irr_reason = irr(levered_flows)

    # Unlevered: the asset on its own. Debt is absent from both the outlay
    # (full price + closing, no loan) and the exit (no balance to retire),
    # which is the point of the metric -- mixing the loan payoff back in
    # here would quietly reintroduce leverage and understate the result.
    unlevered_flows = [-(P + closing_costs)] + [y["noi"] for y in years]
    unlevered_flows[-1] += net_sale_unlevered
    unlevered_irr, unlevered_irr_reason = irr(unlevered_flows)

    total_distributions = sum(operating_cf) + net_sale_levered
    equity_multiple = total_distributions / equity

    noi1 = float(noi_series[0])
    going_in_cap = noi1 / P

    # DSCR and cash-on-cash are ratios against things that can legitimately
    # be absent. An all-cash deal has no debt service, so DSCR is not
    # "infinite" -- it simply does not apply.
    if debt_service > 0:
        dscr = noi1 / debt_service
        dscr_reason = None
    else:
        dscr = None
        dscr_reason = "No debt — DSCR does not apply to an all-cash purchase."

    cash_on_cash = operating_cf[0] / equity

    return {
        "inputs": dict(inputs),
        "loan_amount": loan,
        "closing_costs": closing_costs,
        "equity_invested": equity,
        "annual_debt_service": debt_service,
        "monthly_debt_service": debt_service / 12 if debt_service else 0.0,
        "years": years,
        "noi_exit_year": noi_exit,
        "gross_sale_price": gross_sale,
        "selling_costs": selling_costs,
        "loan_balance_at_exit": balance_at_exit,
        "net_sale_proceeds": net_sale_levered,
        "total_distributions": total_distributions,
        # headline metrics
        "going_in_cap_rate": going_in_cap,
        "cash_on_cash": cash_on_cash,
        "dscr": dscr,
        "dscr_reason": dscr_reason,
        "levered_irr": levered_irr,
        "levered_irr_reason": levered_irr_reason,
        "unlevered_irr": unlevered_irr,
        "unlevered_irr_reason": unlevered_irr_reason,
        "equity_multiple": equity_multiple,
        "levered_cashflows": levered_flows,
        "unlevered_cashflows": unlevered_flows,
    }
