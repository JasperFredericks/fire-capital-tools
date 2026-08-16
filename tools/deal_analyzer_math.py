"""
FIRE Capital Tools - Deal Analyzer calculations.

Every number Deal Analyzer shows is produced here. Deliberately pure: no
Flask, no request context, no I/O, no globals -- inputs in, a result dict
out -- so the formulas can be unit-tested directly (tests/
test_deal_analyzer_math.py) rather than only through an HTTP round-trip.
Same standalone principle as tools/market_data_service.py.

Scope is a levered-returns read on *one* set of assumptions: a single
blended NOI growth rate, a single amortizing loan held to sale, and a cap-
rate exit. No unit-level rent roll, no itemized expenses, no refinance, no
sensitivity tables -- those belong in the Underwriting tool.

Interest-only periods live here now, because the amortization arithmetic
they change has always lived here and Underwriting borrows it. They are
not exposed by Deal Analyzer: that tool sets no io_years, the parameter
defaults to absent, and the debt-service series collapses to the level
payment it always was. A test asserts Deal Analyzer's route never sets
the field, and another asserts the no-IO result is unchanged key by key.

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

# How the balloon was arrived at, carried in every result so a page never
# has to assert a convention it did not compute. See _amortizing_months().
BALLOON_CONVENTION_LEVEL = (
    "Level payment amortizing over the full term from day one.")
BALLOON_CONVENTION_IO = (
    "Interest-only first, then amortizing over the REMAINING term "
    "(amortization period minus the interest-only period), so the loan "
    "still matures on its original schedule.")
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

def _payment_over_months(principal: float, annual_rate: float, n: int) -> float:
    """Level payment amortizing `principal` over exactly `n` months.

    The month-denominated form of monthly_payment(), extracted rather
    than duplicated so there is one amortization formula in this codebase
    and not two. An interest-only period leaves a remaining term measured
    in months, and rounding it back to whole years would move the
    payment. The operations and their order are exactly what
    monthly_payment() has always performed, so the year-denominated
    result is unchanged to the bit.
    """
    r = annual_rate / 12.0
    if r == 0:
        return principal / n
    return principal * r / (1 - (1 + r) ** -n)


def monthly_payment(principal: float, annual_rate: float, amort_years: int) -> float:
    """Level monthly payment fully amortizing `principal` over
    `amort_years`. Handles a 0% loan as straight-line principal repayment
    rather than dividing by a zero rate."""
    if principal <= 0:
        return 0.0
    n = amort_years * 12
    if n <= 0:
        raise ValidationError("Amortization period must be at least 1 year.")
    return _payment_over_months(principal, annual_rate, n)


def io_monthly_payment(principal: float, annual_rate: float) -> float:
    """One month's interest on `principal`, and nothing more.

    An interest-only payment retires no principal, which is the whole
    point: the balance is still the original principal on the day the IO
    period ends. Uses the same monthly rate the amortization math uses,
    so the two agree on what a month of interest costs.
    """
    if principal <= 0:
        return 0.0
    return principal * (annual_rate / 12.0)


def _amortizing_months(amort_years: int, io_months: int) -> int:
    """Months of amortization left once the IO period ends.

    Convention B, and the reason it is stated everywhere a balloon is
    shown: after interest-only ends the loan amortizes over its REMAINING
    term -- the original amortization minus the IO period -- so it still
    matures on its original schedule. The alternative convention
    re-amortizes over the full original term, which lowers the payment
    and pushes a larger balloon out past the original maturity date. Both
    are real; they are not interchangeable, and on a $4.5M loan with two
    years of IO they differ by about $27,000 of balloon.
    """
    n = amort_years * 12 - io_months
    if n <= 0:
        raise ValidationError(
            "An interest-only period as long as the amortization period "
            "would mean the loan never amortizes at all.")
    return n


def remaining_balance(principal: float, annual_rate: float, amort_years: int,
                      months_paid: int, *, io_months: int = 0) -> float:
    """Outstanding principal after `months_paid` payments -- the balloon
    that gets retired out of sale proceeds. Never returns a negative
    balance: if the loan fully amortizes within the hold, the balance is
    zero, not an overpayment.

    `io_months` is keyword-only and defaults to 0, which takes the
    original code path below unchanged -- every existing caller, and
    every Deal Analyzer call, computes exactly what it always did.
    """
    if principal <= 0:
        return 0.0
    if io_months <= 0:
        pmt = monthly_payment(principal, annual_rate, amort_years)
        r = annual_rate / 12.0
        if r == 0:
            return max(0.0, principal - pmt * months_paid)
        grown = (1 + r) ** months_paid
        balance = principal * grown - pmt * ((grown - 1) / r)
        return max(0.0, balance)

    # Nothing amortizes while only interest is being paid, so the balance
    # on the last day of the IO period is still the original principal.
    n_remaining = _amortizing_months(amort_years, io_months)
    if months_paid <= io_months:
        return principal
    pmt = _payment_over_months(principal, annual_rate, n_remaining)
    r = annual_rate / 12.0
    m = months_paid - io_months
    if r == 0:
        return max(0.0, principal - pmt * m)
    grown = (1 + r) ** m
    balance = principal * grown - pmt * ((grown - 1) / r)
    return max(0.0, balance)


def refinance(loan: float, annual_rate: float, amort_years: int,
              hold_years: int, *, io_months: int = 0,
              refi_year: int, refi_loan: float, refi_rate: float,
              refi_amort_years: int, refi_io_months: int = 0,
              refi_costs_pct: float = 0.0,
              refi_fee_pct: float = 0.0) -> dict[str, Any]:
    """One loan replaced by another, part-way through the hold.

    A cash-out refinance is the first mid-hold capital EVENT this model
    has had. Everything before it was operating cash flow and a terminal
    sale, so the shape is new even though every piece of arithmetic it
    uses already existed.

    THE ORDER, WHICH IS MICHELLE'S AND NOT AN INVENTION

        new loan
          - payoff of the old loan       (IO-aware, at refi_year * 12)
          - refinance costs              (points and closing)
          = excess proceeds
          - GP capital transaction fee
          = what reaches the investors

    Preferred return is deliberately absent from that list. It is not
    paid at the event; it keeps accruing afterwards on whatever capital
    is still unreturned, which is the whole point of returning capital
    early.

    THE FEE BASE IS AN ASSUMPTION, FLAGGED

    refi_fee_pct is charged on the EXCESS PROCEEDS POOL, not on the gross
    new loan. That is the reading the stated order forces -- the fee sits
    between the payoff and the return of capital, so it can only be a
    share of what is left after the payoff. The sale-side fee is charged
    on the gross sale price instead, so the two bases genuinely differ,
    and that difference is deliberate rather than an oversight. If
    Michelle means the gross new loan, this is the one line to change.

    A cash-in refinance -- new loan smaller than the payoff -- is refused
    rather than modelled as a negative distribution.
    """
    h = int(hold_years)
    year = int(refi_year)
    if not 1 <= year <= h - 1:
        raise ValidationError(
            f"The refinance year must be between 1 and {h - 1} for a "
            f"{h}-year hold — a refinance in the exit year is a sale.")
    if refi_loan <= 0:
        raise ValidationError("The new loan amount must be greater than zero.")

    payoff = remaining_balance(loan, annual_rate, amort_years, year * 12,
                               io_months=io_months)
    costs = refi_loan * (refi_costs_pct / 100.0)
    excess = refi_loan - payoff - costs
    if excess < 0:
        raise ValidationError(
            f"This refinance raises ${refi_loan:,.0f} against a payoff of "
            f"${payoff:,.0f} plus ${costs:,.0f} of costs, so it needs "
            f"${-excess:,.0f} of cash IN rather than returning any. A "
            f"cash-in refinance is not modelled here.")

    fee = excess * (refi_fee_pct / 100.0)
    to_investors = excess - fee

    # Two segments, spliced. The first is simply the original loan's own
    # series truncated at the refinance; the second starts a new loan on
    # its own terms, which may include a fresh interest-only period.
    before = (annual_debt_service_series(loan, annual_rate, amort_years, year,
                                         io_months=io_months)
              if loan > 0 else [0.0] * year)
    after = annual_debt_service_series(refi_loan, refi_rate, refi_amort_years,
                                       h - year, io_months=refi_io_months)
    return {
        "refi_year": year,
        "payoff_balance": payoff,
        "refi_costs": costs,
        "excess_proceeds": excess,
        "gp_fee": fee,
        "proceeds_to_investors": to_investors,
        "debt_service_series": before + after,
        # The balloon is the NEW loan's, at the months it has actually
        # been paying -- not the original's, which no longer exists.
        "balance_at_exit": remaining_balance(refi_loan, refi_rate,
                                             refi_amort_years,
                                             (h - year) * 12,
                                             io_months=refi_io_months),
        "loan_amount": refi_loan,
    }


def annual_debt_service_series(principal: float, annual_rate: float,
                               amort_years: int, hold_years: int, *,
                               io_months: int = 0) -> list[float]:
    """Annual debt service for operating years 1..hold_years.

    A list rather than a scalar because an interest-only period makes
    debt service a function of time. With `io_months` at 0 -- every Deal
    Analyzer call and every scenario with no IO period -- this returns
    the single level payment repeated, computed by the same
    `monthly_payment(...) * 12` expression the engine used before this
    function existed, so the default path is identical elementwise rather
    than merely close.
    """
    h = int(hold_years)
    if h < 1:
        raise ValidationError("Hold period must be at least 1 year.")
    if principal <= 0:
        return [0.0] * h
    if io_months <= 0:
        return [monthly_payment(principal, annual_rate, amort_years) * 12] * h

    n_remaining = _amortizing_months(amort_years, io_months)
    io_pmt = io_monthly_payment(principal, annual_rate)
    am_pmt = _payment_over_months(principal, annual_rate, n_remaining)

    series = []
    for year in range(1, h + 1):
        first, last = (year - 1) * 12 + 1, year * 12
        # Months of this year that fall inside the IO period. A whole-year
        # io_years never splits a year, but the arithmetic is written for
        # the general case rather than assuming the caller's units.
        io_count = min(12, max(0, min(last, io_months) - first + 1))
        series.append(io_pmt * io_count + am_pmt * (12 - io_count))
    return series


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
                       noi_exit: float, *,
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

    # Interest-only period, single-loan mode. Absent on every Deal
    # Analyzer call -- that tool has no such field -- so this is 0 and the
    # series below collapses to the level payment it has always been.
    io_years = int(float(inputs.get("io_years") or 0))
    if io_years < 0:
        raise ValidationError("Interest-only period cannot be negative.")
    if io_years and io_years >= amort_years:
        raise ValidationError(
            f"An interest-only period of {io_years} years with a "
            f"{amort_years}-year amortization means the loan never "
            f"amortizes — the IO period must be shorter.")
    io_months = io_years * 12

    # Cash-out refinance. Absent -- every Deal Analyzer call and every
    # scenario that predates the columns -- leaves everything below
    # exactly as it was. Single-loan mode only: see the note where
    # refi_event is consumed.
    refi_event = None
    refi_year = int(float(inputs.get("refi_year") or 0))
    if refi_year and debt is None:
        refi_event = refinance(
            loan, rate, amort_years, H, io_months=io_months,
            refi_year=refi_year,
            refi_loan=float(inputs.get("refi_loan_amount") or 0.0),
            refi_rate=(float(inputs["refi_rate_pct"])
                       if inputs.get("refi_rate_pct") not in (None, "")
                       else float(inputs["interest_rate_pct"])) / 100.0,
            refi_amort_years=int(float(inputs.get("refi_amort_years")
                                       or amort_years)),
            refi_io_months=int(float(inputs.get("refi_io_years") or 0)) * 12,
            refi_costs_pct=float(inputs.get("refi_costs_pct") or 0.0),
            refi_fee_pct=float(inputs.get("refi_fee_pct") or 0.0))
    elif refi_year and debt is not None:
        # Multi-loan mode does not naturally apply: a stack has no single
        # loan to replace, and which loan the refinance retires is a term
        # nobody has stated. Refused rather than guessed at.
        raise ValidationError(
            "A refinance cannot be modelled on a multi-loan stack yet — "
            "which loan it replaces is not defined. Remove the loan stack "
            "or the refinance year.")

    if debt is None:
        debt_service_series = (
            refi_event["debt_service_series"] if refi_event
            else (annual_debt_service_series(
                loan, rate, amort_years, H, io_months=io_months)
                if loan > 0 else [0.0] * H))
    else:
        # A multi-loan stack whose loans have their own IO periods hands
        # over a series; one without them hands over the scalar it always
        # did, which is spread across the hold unchanged.
        supplied = debt.get("debt_service_series")
        debt_service_series = ([float(x) for x in supplied] if supplied
                               else [float(debt["annual_debt_service"])] * H)
    if len(debt_service_series) != H:
        raise ValidationError(
            "Debt service series must have one entry per year of the hold.")

    # The headline scalar stays year 1, which is what it has always been.
    debt_service = debt_service_series[0]

    # Year-by-year operations, from the supplied NOI series.
    years = []
    cumulative = 0.0
    for t in range(1, H + 1):
        noi_t = float(noi_series[t - 1])
        ds_t = debt_service_series[t - 1]
        # The refinance lands in exactly one year. It is carried as its own
        # component rather than folded into cash_flow alone, because the
        # waterfall has to be able to treat a capital event differently
        # from operations -- the same reason sale proceeds travel apart.
        refi_t = (refi_event["proceeds_to_investors"]
                  if refi_event and t == refi_event["refi_year"] else 0.0)
        cf_t = noi_t - ds_t + refi_t
        cumulative += cf_t
        years.append({
            "year": t,
            "noi": noi_t,
            "debt_service": ds_t,
            "refi_proceeds": refi_t,
            "cash_flow": cf_t,
            "cumulative_cash_flow": cumulative,
        })

    operating_cf = [y["cash_flow"] for y in years]

    # Exit. Capitalize the *forward* NOI (year H+1) -- what a buyer at the
    # end of the hold would underwrite -- not the final year's own NOI.
    noi_exit = float(noi_exit)
    gross_sale = noi_exit / exit_cap
    selling_costs = gross_sale * sc_pct
    # A capital transaction fee is charged on the gross sale price, like
    # the selling costs beside it, and is absent (0.0) for every Deal
    # Analyzer call -- that tool has no such field, so .get() keeps its
    # arithmetic literally unchanged rather than merely equivalent.
    ctf_pct = float(inputs.get("capital_transaction_fee_pct") or 0.0) / 100.0
    capital_transaction_fee = gross_sale * ctf_pct
    # The multi-loan override supplies the summed payoff of the stack; with
    # no override this is the single LTV-sized loan, exactly as before.
    if refi_event:
        balance_at_exit = refi_event["balance_at_exit"]
    elif debt is None:
        balance_at_exit = remaining_balance(loan, rate, amort_years, H * 12,
                                            io_months=io_months)
    else:
        balance_at_exit = float(debt["balance_at_exit"])
    net_sale_levered = (gross_sale - selling_costs - capital_transaction_fee
                        - balance_at_exit)
    net_sale_unlevered = gross_sale - selling_costs - capital_transaction_fee

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

    # DSCR through the hold, not just at the front of it.
    #
    # With an interest-only period the year-1 ratio is the most flattering
    # number in the deal: debt service is lower precisely because no
    # principal is being retired. Quoting it alone would let a scenario
    # clear a covenant on screen and breach it the year IO ends. So the
    # per-year series is reported, along with the minimum across the hold
    # -- the binding constraint, and the figure any pass/fail grading is
    # meant to read.
    dscr_by_year = [
        (float(noi_series[t]) / debt_service_series[t])
        if debt_service_series[t] > 0 else None
        for t in range(H)
    ]
    measured = [d for d in dscr_by_year if d is not None]
    dscr_min = min(measured) if measured else None
    dscr_min_year = (dscr_by_year.index(dscr_min) + 1) if measured else None

    # The two phases, named, so the page never has to infer them.
    io_year_count = min(io_years, H) if io_years else 0
    dscr_io = dscr_by_year[0] if io_year_count else None
    dscr_post_io = (dscr_by_year[io_year_count]
                    if io_year_count and io_year_count < H else None)

    cash_on_cash = operating_cf[0] / equity

    return {
        "inputs": dict(inputs),
        "loan_amount": loan,
        "closing_costs": closing_costs,
        "equity_invested": equity,
        "annual_debt_service": debt_service,
        "monthly_debt_service": debt_service / 12 if debt_service else 0.0,
        "debt_service_series": debt_service_series,
        "io_years": io_years,
        # The refinance, or None. Everything a page or a waterfall needs
        # to describe the event travels together: what was paid off, what
        # it cost, what the GP took and what reached the investors.
        "refinance": refi_event,
        "refi_year": (refi_event or {}).get("refi_year"),
        "refi_proceeds": (refi_event or {}).get("proceeds_to_investors", 0.0),
        "refi_gp_fee": (refi_event or {}).get("gp_fee", 0.0),
        # Which convention produced the balloon, carried with the result
        # rather than written into a template. A balloon is only
        # interpretable alongside the amortization convention behind it,
        # so the two travel together.
        "balloon_convention": (BALLOON_CONVENTION_IO if io_years
                               else BALLOON_CONVENTION_LEVEL),
        # True when the IO period outlasts the hold: the loan never
        # amortizes a dollar before sale, so the balloon is the original
        # principal. Legal and sometimes intended, never silent.
        "io_covers_whole_hold": bool(io_years and io_years >= H),
        "years": years,
        "noi_exit_year": noi_exit,
        "gross_sale_price": gross_sale,
        "selling_costs": selling_costs,
        "capital_transaction_fee": capital_transaction_fee,
        "loan_balance_at_exit": balance_at_exit,
        "net_sale_proceeds": net_sale_levered,
        "total_distributions": total_distributions,
        # headline metrics
        "going_in_cap_rate": going_in_cap,
        "cash_on_cash": cash_on_cash,
        "dscr": dscr,
        "dscr_reason": dscr_reason,
        "dscr_by_year": dscr_by_year,
        "dscr_min": dscr_min,
        "dscr_min_year": dscr_min_year,
        "dscr_io": dscr_io,
        "dscr_post_io": dscr_post_io,
        "levered_irr": levered_irr,
        "levered_irr_reason": levered_irr_reason,
        "unlevered_irr": unlevered_irr,
        "unlevered_irr_reason": unlevered_irr_reason,
        "equity_multiple": equity_multiple,
        "levered_cashflows": levered_flows,
        "unlevered_cashflows": unlevered_flows,
    }
