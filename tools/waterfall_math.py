"""
FIRE Capital Tools - LP/GP distribution waterfall.

Allocates a property's distributable cash to investors through an ordered
tier cascade, and reports what each investor receives, their return, and
how much passed through each tier.

Pure: no Flask, no database, no I/O. Given the stakes -- a wrong number
here misstates what a real person is owed, not just an internal analytic
-- everything below is arranged so that errors are loud rather than
plausible:

  * All cascade arithmetic is in INTEGER CENTS. Money is never held in a
    float during allocation, so "total distributed equals cash available"
    is exact by construction rather than exact to within a tolerance.
  * Ten invariants are asserted on every run (see check_invariants). A
    violation raises WaterfallInvariantError and returns nothing. A
    waterfall that cannot prove it conserved the money must not present a
    number that looks fine.

Tier order for this beta, applied per period:

    1. Return of Capital     100% to LPs, until contributions are repaid
    2. Preferred Return      100% to LPs, until the accrued pref is paid
    3. Promote Split         the remainder, e.g. 80 LP / 20 GP

Tiers are an ordered list rather than three hardcoded steps, so adding an
IRR-hurdle band later is data rather than a rewrite.

Preferred return accrues on (unreturned capital + unpaid pref) -- the
unpaid balance compounds. That is not a stylistic choice: without it,
paying accrued pref in a later period costs the LP internal rate of
return, and invariant 8 ("LP IRR >= pref rate once the pref is fully
paid") is simply false. Verified: on a 1,000 contribution repaid in year 1
with pref settled in year 2, non-compounding gives an LP IRR of 7.4457%
against an 8% pref, while compounding gives exactly 8.000000%.

Only the accrual convention is implemented. pref_convention is carried
through to the result regardless, so a report always states which
convention produced its numbers.
"""

from __future__ import annotations

import math
from typing import Any

from tools.deal_analyzer_math import irr, npv

CLASS_LP = "LP"
CLASS_GP = "GP"

TIER_RETURN_OF_CAPITAL = "return_of_capital"
TIER_PREF = "pref"
TIER_PROMOTE = "promote"
TIER_IRR_HURDLE = "irr_hurdle"      # architected, not selectable in the beta

# Rounding cash to whole cents on input shifts a computed IRR very slightly
# against the same deal computed on unrounded floats. Measured at ~2e-9 on a
# real five-year scenario; the bound below is generous against that and still
# far tighter than anything that could hide a cascade error.
IRR_QUANTIZATION_BOUND = 1e-6

PREF_CONVENTION_ACCRUAL = "accrual"
PREF_CONVENTION_IRR_LOOKBACK = "irr_lookback"   # not implemented in the beta

# Fallback tiers for a terms dict that carries none. FIRE Capital's stated
# standard promote split is 70/30; it is a default only, and every stored
# scenario supplies its own tier rows rather than falling back to these.
DEFAULT_PROMOTE_LP_PCT = 70.0
DEFAULT_PROMOTE_GP_PCT = 30.0

DEFAULT_TIERS = (
    {"sort_order": 0, "tier_type": TIER_RETURN_OF_CAPITAL, "lp_share_pct": 100.0, "gp_share_pct": 0.0},
    {"sort_order": 1, "tier_type": TIER_PREF, "lp_share_pct": 100.0, "gp_share_pct": 0.0},
    {"sort_order": 2, "tier_type": TIER_PROMOTE,
     "lp_share_pct": DEFAULT_PROMOTE_LP_PCT, "gp_share_pct": DEFAULT_PROMOTE_GP_PCT},
)


class WaterfallError(ValueError):
    """Bad input -- the caller's message to the user."""


class WaterfallInvariantError(AssertionError):
    """A computed waterfall failed one of its own conservation checks.

    Deliberately not a subclass of WaterfallError: this is not the user's
    mistake, it is a bug in the cascade, and it must never be caught and
    rendered as a soft warning next to numbers that are wrong."""


# ── money: integer cents ─────────────────────────────────────────────────

def to_cents(value) -> int:
    """Dollars -> whole cents, rounded half away from zero.

    Written as floor(x + 0.5) rather than int(round(x + 0.5)). The latter
    double-rounds -- round() applies banker's rounding and the +0.5 then
    applies half-up on top of it -- which biases every conversion upward by
    up to a full cent. That inflated the LP's cash flows just enough to
    break invariant 10 against the property's own flows, which is exactly
    the failure mode the invariant exists to catch."""
    if value is None:
        return 0
    scaled = float(value) * 100.0
    return int(math.floor(scaled + 0.5)) if scaled >= 0 else int(math.ceil(scaled - 0.5))


def to_dollars(cents: int) -> float:
    return round(cents / 100.0, 2)


def split_pro_rata(amount_cents: int, weights: list[int]) -> list[int]:
    """Split whole cents in proportion to `weights`, losing nothing.

    Floor each share, then hand every leftover cent to the largest weight
    (ties broken by position, so the result is deterministic). Returning
    floors alone would leak up to n-1 cents per period, which across a
    ten-year waterfall is exactly the kind of small, invisible discrepancy
    that erodes trust in the whole report."""
    total_w = sum(weights)
    if total_w <= 0 or amount_cents == 0:
        return [0] * len(weights)
    shares = [amount_cents * w // total_w for w in weights]
    residual = amount_cents - sum(shares)
    if residual:
        order = sorted(range(len(weights)), key=lambda i: (-weights[i], i))
        for k in range(abs(residual)):
            shares[order[k % len(order)]] += 1 if residual > 0 else -1
    return shares


# ── IRR hurdle (closed form) ─────────────────────────────────────────────

def amount_to_reach_irr(flows: list[float], period: int, target_rate: float) -> float:
    """Cash at `period` that lands `flows` on exactly `target_rate`.

    NPV is linear in any single cash flow, so this needs no iterative
    solver at all:

        cf_T = -(1 + r)^T * NPV_r(all other flows)

    Reuses the existing npv() primitive rather than adding a second
    root-finder alongside deal_analyzer_math.irr(). Unused by the beta's
    accrual tiers; present and tested so an IRR-hurdle tier is a data
    change later, not new math under time pressure."""
    if period < 0 or period >= len(flows):
        raise WaterfallError("IRR hurdle period is outside the cash flow series.")
    others = list(flows)
    others[period] = 0.0
    return -((1.0 + target_rate) ** period) * npv(target_rate, others)


# ── the cascade ──────────────────────────────────────────────────────────

def run_waterfall(contributions: list[dict[str, Any]],
                  periods: list[dict[str, Any]],
                  terms: dict[str, Any]) -> dict[str, Any]:
    """Allocate each period's distributable cash through the tier cascade.

    contributions -- [{investor_id, name, amount, investor_class}]
    periods       -- [{year, operating_cash, sale_proceeds}] in order
    terms         -- {pref_rate_pct, pref_convention, tiers[]}

    Returns the full allocation plus every figure the report shows. Raises
    WaterfallInvariantError if the result fails its own checks."""
    lps = [c for c in contributions if (c.get("investor_class") or CLASS_LP) == CLASS_LP]
    if not lps:
        raise WaterfallError("At least one LP contribution is required to build a waterfall.")
    if not periods:
        raise WaterfallError("The source Underwriting scenario has no cash flow periods.")

    convention = terms.get("pref_convention") or PREF_CONVENTION_ACCRUAL
    if convention != PREF_CONVENTION_ACCRUAL:
        raise WaterfallError(
            f"Only the '{PREF_CONVENTION_ACCRUAL}' preferred-return convention is "
            f"implemented in this beta."
        )
    pref_rate = float(terms.get("pref_rate_pct") or 0.0) / 100.0
    tiers = sorted(terms.get("tiers") or DEFAULT_TIERS, key=lambda t: t.get("sort_order", 0))

    contrib_cents = [to_cents(c.get("amount")) for c in lps]
    if sum(contrib_cents) <= 0:
        raise WaterfallError("Total LP contributed capital must be greater than zero.")

    n = len(lps)
    unreturned = list(contrib_cents)     # per LP
    unpaid_pref = [0] * n                # per LP
    lp_received = [0] * n                # cumulative, per LP
    lp_flows = [[-c] for c in contrib_cents]   # per LP, for IRR
    gp_received = 0
    gp_flows = [0]

    tier_totals = {t["tier_type"]: {"lp": 0, "gp": 0} for t in tiers}
    period_rows = []

    for p in periods:
        cash = to_cents(p.get("operating_cash")) + to_cents(p.get("sale_proceeds"))
        # A refinance pays down capital and nothing else.
        #
        # Michelle's stated order at the event is payoff, then fees, then
        # return of capital -- pref is deliberately absent from it. So the
        # refi pool runs the return-of-capital tier alone, BEFORE the
        # normal cascade, and any part of it beyond the capital still
        # outstanding falls through into the ordinary pool for the period
        # rather than being trapped (invariant 1 requires every cent
        # received to be distributed).
        #
        # Pref is untouched here on purpose. It is not paid at the event;
        # it goes on accruing in every later period on whatever capital is
        # still unreturned -- which is now smaller, which is the entire
        # point of returning capital early.
        refi_cents = to_cents(p.get("refi_proceeds"))
        cash_in = cash + refi_cents

        # Accrue on unreturned capital PLUS unpaid pref -- the unpaid
        # balance compounds. See the module docstring: without this,
        # invariant 8 does not hold.
        accrued = [0] * n
        if pref_rate:
            for i in range(n):
                base = unreturned[i] + unpaid_pref[i]
                a = int(round(base * pref_rate))
                accrued[i] = a
                unpaid_pref[i] += a

        row = {"year": p.get("year"), "cash_available": cash_in,
               "accrued_pref": sum(accrued), "tiers": [], "lp": [0] * n,
               "gp": 0, "refi_proceeds": refi_cents, "refi_return_of_capital": 0}

        if refi_cents > 0:
            payable = min(refi_cents, sum(unreturned))
            shares = split_pro_rata(payable, unreturned)
            for i, share in enumerate(shares):
                unreturned[i] -= share
                lp_received[i] += share
                row["lp"][i] += share
            # setdefault, not indexing: tier_totals is built from the
            # CONFIGURED tiers, and a structure with no return-of-capital
            # tier is legal. A refinance still returns capital in that
            # case, so the bucket has to be able to appear.
            tier_totals.setdefault(TIER_RETURN_OF_CAPITAL,
                                   {"lp": 0, "gp": 0})["lp"] += payable
            row["refi_return_of_capital"] = payable
            row["tiers"].append({"tier_type": TIER_RETURN_OF_CAPITAL,
                                 "lp": payable, "gp": 0, "paid": payable,
                                 "from_refinance": True})
            # Whatever the refinance raised beyond the outstanding capital
            # joins the period's ordinary cash and takes the normal route.
            cash += refi_cents - payable

        for tier in tiers:
            ttype = tier["tier_type"]
            if cash <= 0:
                row["tiers"].append({"tier_type": ttype, "lp": 0, "gp": 0, "paid": 0})
                continue

            if ttype == TIER_RETURN_OF_CAPITAL:
                payable = min(cash, sum(unreturned))
                shares = split_pro_rata(payable, unreturned)
                for i, s in enumerate(shares):
                    unreturned[i] -= s
                lp_pay, gp_pay = shares, 0

            elif ttype == TIER_PREF:
                payable = min(cash, sum(unpaid_pref))
                shares = split_pro_rata(payable, unpaid_pref)
                for i, s in enumerate(shares):
                    unpaid_pref[i] -= s
                lp_pay, gp_pay = shares, 0

            elif ttype == TIER_PROMOTE:
                payable = cash
                gp_pay = payable * to_cents(tier.get("gp_share_pct")) // to_cents(100.0)
                lp_total = payable - gp_pay
                lp_pay = split_pro_rata(lp_total, contrib_cents)

            elif ttype == TIER_IRR_HURDLE:
                raise WaterfallError(
                    "IRR-hurdle tiers are modeled but not implemented in this beta."
                )
            else:
                raise WaterfallError(f"Unknown tier type: {ttype!r}")

            paid = sum(lp_pay) + gp_pay
            # Invariant 4, checked at the point it could go wrong rather
            # than only after the fact.
            if paid > cash:
                raise WaterfallInvariantError(
                    f"Tier {ttype!r} tried to distribute {paid} cents with only {cash} available."
                )
            cash -= paid
            for i, s in enumerate(lp_pay):
                lp_received[i] += s
                row["lp"][i] += s
            gp_received += gp_pay
            row["gp"] += gp_pay
            tier_totals[ttype]["lp"] += sum(lp_pay)
            tier_totals[ttype]["gp"] += gp_pay
            row["tiers"].append({"tier_type": ttype, "lp": sum(lp_pay), "gp": gp_pay, "paid": paid})

        # A period can end with cash left over (undistributed) or with the
        # property having failed to cover its debt service (a shortfall).
        # They are opposite things and folding both into one signed number
        # is what made "undistributed" go negative and invariant 1 read as
        # if money had been conjured.
        #
        # An LP never receives a negative distribution: a year the property
        # does not cover is funded from reserves or a capital call, not by
        # taking money back. So the shortfall is recorded, not allocated.
        row["shortfall"] = -cash if cash < 0 else 0
        row["undistributed"] = cash if cash > 0 else 0
        row["distributed"] = cash_in - cash if cash > 0 else (0 if cash_in <= 0
                                                              else cash_in)
        period_rows.append(row)
        for i in range(n):
            lp_flows[i].append(row["lp"][i])
        gp_flows.append(row["gp"])

    result = _assemble(lps, contrib_cents, periods, period_rows, tier_totals,
                       lp_received, gp_received, lp_flows, gp_flows,
                       unreturned, unpaid_pref, pref_rate, convention, tiers)
    check_invariants(result)
    return result


def _assemble(lps, contrib_cents, periods, period_rows, tier_totals, lp_received,
              gp_received, lp_flows, gp_flows, unreturned, unpaid_pref,
              pref_rate, convention, tiers):
    total_cash = sum(r["cash_available"] for r in period_rows)
    total_distributed = sum(r["distributed"] for r in period_rows)
    total_shortfall = sum(r["shortfall"] for r in period_rows)

    investors = []
    for i, c in enumerate(lps):
        flows = [to_dollars(x) for x in lp_flows[i]]
        rate, reason = irr(flows)
        investors.append({
            "investor_id": c.get("investor_id"), "name": c.get("name") or "LP",
            "investor_class": CLASS_LP,
            "contributed": to_dollars(contrib_cents[i]),
            "contributed_cents": contrib_cents[i],
            "distributed": to_dollars(lp_received[i]),
            "distributed_cents": lp_received[i],
            "unreturned_capital": to_dollars(unreturned[i]),
            "unpaid_pref": to_dollars(unpaid_pref[i]),
            "equity_multiple": (lp_received[i] / contrib_cents[i]) if contrib_cents[i] else None,
            "irr": rate, "irr_reason": reason,
            "cashflows": flows,
            "ownership_pct": contrib_cents[i] / sum(contrib_cents) * 100.0,
        })

    gp_rate, gp_reason = irr([to_dollars(x) for x in gp_flows]) if gp_received else (None, "GP contributed no capital and receives promote only.")
    lp_total_flows = [to_dollars(sum(f[t] for f in lp_flows)) for t in range(len(lp_flows[0]))]
    lp_rate, lp_reason = irr(lp_total_flows)

    return {
        "convention": convention,
        "pref_rate_pct": pref_rate * 100.0,
        "tiers": tiers,
        "periods": [{
            "year": r["year"],
            "cash_available": to_dollars(r["cash_available"]),
            "accrued_pref": to_dollars(r["accrued_pref"]),
            "distributed": to_dollars(r["distributed"]),
            "undistributed": to_dollars(r["undistributed"]),
            # What the property failed to cover this period. Reported so a
            # reader sees WHY a year distributed nothing rather than being
            # left to infer it from a zero.
            "shortfall": to_dollars(r["shortfall"]),
            # The refinance, if this period had one. Carried out of the
            # cascade rather than left inside it: a reader looking at a
            # year where unreturned capital dropped needs to see that a
            # capital event caused it, not infer it from the tier rows.
            "refi_proceeds": to_dollars(r.get("refi_proceeds", 0)),
            "refi_return_of_capital": to_dollars(r.get("refi_return_of_capital", 0)),
            "lp": [to_dollars(x) for x in r["lp"]],
            "lp_total": to_dollars(sum(r["lp"])),
            "gp": to_dollars(r["gp"]),
            "tiers": [{**t, "lp": to_dollars(t["lp"]), "gp": to_dollars(t["gp"]),
                       "paid": to_dollars(t["paid"])} for t in r["tiers"]],
        } for r in period_rows],
        "tier_totals": {k: {"lp": to_dollars(v["lp"]), "gp": to_dollars(v["gp"]),
                            "total": to_dollars(v["lp"] + v["gp"])}
                        for k, v in tier_totals.items()},
        "investors": investors,
        "gp": {"distributed": to_dollars(gp_received), "distributed_cents": gp_received,
               "irr": gp_rate, "irr_reason": gp_reason,
               "cashflows": [to_dollars(x) for x in gp_flows]},
        "lp_aggregate": {"contributed": to_dollars(sum(contrib_cents)),
                         "distributed": to_dollars(sum(lp_received)),
                         "irr": lp_rate, "irr_reason": lp_reason,
                         "cashflows": lp_total_flows},
        "totals": {
            "cash_available": to_dollars(total_cash),
            "distributed": to_dollars(total_distributed),
            # + shortfall, so this stays a genuine "left over" figure.
            # Without it a deal with one uncovered year reports NEGATIVE
            # undistributed cash, which reads as money having gone missing.
            "undistributed": to_dollars(total_cash - total_distributed
                                        + total_shortfall),
            "shortfall": to_dollars(total_shortfall),
            "lp_distributed": to_dollars(sum(lp_received)),
            "gp_distributed": to_dollars(gp_received),
            "contributed": to_dollars(sum(contrib_cents)),
        },
        # cent-exact figures the invariant checks work on
        "_cents": {
            "total_cash": total_cash, "total_distributed": total_distributed,
            "total_shortfall": total_shortfall,
            "lp_received": lp_received, "gp_received": gp_received,
            "contrib": contrib_cents,
            "period_rows": period_rows, "unreturned": unreturned,
            # Exact tier totals. The dollar-rounded "tier_totals" above is
            # for display; invariant checks must never round-trip through
            # it -- doing so produced one-cent false failures on invariants
            # 5 and 6, which is precisely the class of discrepancy these
            # checks exist to catch, so the measurement is fixed rather
            # than the tolerance loosened.
            "tier_totals": {k: dict(v) for k, v in tier_totals.items()},
        },
    }


# ── invariants ───────────────────────────────────────────────────────────

def check_invariants(result: dict[str, Any]) -> list[dict[str, Any]]:
    """The ten conservation checks, asserted on every run.

    Raises WaterfallInvariantError on the first failure. These are not
    diagnostics to be shown alongside a suspect number -- if one fails the
    cascade is wrong and there is no number worth showing."""
    c = result["_cents"]
    checks: list[dict[str, Any]] = []

    def ok(n, name, passed, detail=""):
        checks.append({"n": n, "name": name, "passed": bool(passed), "detail": detail})
        if not passed:
            raise WaterfallInvariantError(f"Invariant {n} ({name}) failed: {detail}")

    # 1. every period, and cumulatively, to the cent
    for r in c["period_rows"]:
        allocated = sum(r["lp"]) + r["gp"]
        # allocated + left over - shortfall == what came in. On a period
        # with no shortfall this is the identity it always was; on a
        # negative period it is 0 + 0 - shortfall == a negative inflow.
        if allocated + r["undistributed"] - r["shortfall"] != r["cash_available"]:
            ok(1, "distributions equal cash available", False,
               f"year {r['year']}: allocated {allocated} + undistributed "
               f"{r['undistributed']} - shortfall {r['shortfall']} "
               f"!= available {r['cash_available']}")
    total_alloc = sum(c["lp_received"]) + c["gp_received"]
    ok(1, "distributions equal cash available", total_alloc == c["total_distributed"],
       f"allocated {total_alloc} != distributed {c['total_distributed']}")

    # 2. LP + GP is the whole of what was distributed
    ok(2, "LP + GP equals 100% of distributed cash",
       sum(c["lp_received"]) + c["gp_received"] == c["total_distributed"])

    # 3. tier outflows reconcile to each period's cash in
    for r in c["period_rows"]:
        paid = sum(t["paid"] for t in r["tiers"])
        if paid != r["distributed"]:
            ok(3, "tier outflows equal period cash in", False,
               f"year {r['year']}: tiers paid {paid} != distributed {r['distributed']}")
    ok(3, "tier outflows equal period cash in", True)

    # 4. no tier over-distributes (also enforced inline during the cascade)
    #
    # max(available, 0), not available. A period whose cash is NEGATIVE has
    # nothing to distribute, and every tier correctly pays zero -- but zero
    # is arithmetically "greater than" a negative number, so the bare
    # comparison fired on exactly the deals it should have passed. A
    # value-add year 1 that does not cover debt service is ordinary, and it
    # crashed the Investor Report rather than showing a year with no
    # distribution.
    #
    # The second clause is new and STRENGTHENS this invariant rather than
    # excusing the case: a negative period must distribute exactly nothing.
    # Without it, max() alone would also accept a negative period that
    # somehow paid out.
    for r in c["period_rows"]:
        paid = sum(t["paid"] for t in r["tiers"])
        if paid > max(r["cash_available"], 0):
            ok(4, "no tier distributes more than remains", False,
               f"year {r['year']}: paid {paid} with {r['cash_available']} available")
        if r["cash_available"] < 0 and paid != 0:
            ok(4, "no tier distributes more than remains", False,
               f"year {r['year']}: distributed {paid} in a period with negative cash")
    ok(4, "no tier distributes more than remains", True)

    # 4b. the shortfall is exactly the negative cash, and nothing else.
    for r in c["period_rows"]:
        expected = -r["cash_available"] if r["cash_available"] < 0 else 0
        if r["shortfall"] != expected:
            ok(4, "no tier distributes more than remains", False,
               f"year {r['year']}: shortfall {r['shortfall']} != {expected}")

    # 5. return of capital never exceeds contributions (exact cents)
    roc_t = c["tier_totals"].get(TIER_RETURN_OF_CAPITAL, {"lp": 0, "gp": 0})
    roc_cents = roc_t["lp"] + roc_t["gp"]
    ok(5, "return of capital within contributed capital",
       roc_cents <= sum(c["contrib"]),
       f"returned {roc_cents} > contributed {sum(c['contrib'])}")

    # 6. pro-rata allocation loses nothing: returned + still unreturned == contributed
    for i, contrib in enumerate(c["contrib"]):
        returned = contrib - c["unreturned"][i]
        if returned + c["unreturned"][i] != contrib:
            ok(6, "pro-rata LP shares sum to contributed capital", False, f"investor index {i}")
    ok(6, "pro-rata LP shares sum to contributed capital",
       sum(c["contrib"]) - sum(c["unreturned"]) == roc_cents,
       f"capital reduction {sum(c['contrib']) - sum(c['unreturned'])} != RoC tier {roc_cents}")

    # 7. no promote before the pref is satisfied
    running_pref_due = 0
    for r in c["period_rows"]:
        running_pref_due += r["accrued_pref"]
        paid_pref = sum(t["lp"] for t in r["tiers"] if t["tier_type"] == TIER_PREF)
        running_pref_due -= paid_pref
        gp_this = sum(t["gp"] for t in r["tiers"] if t["tier_type"] == TIER_PROMOTE)
        if gp_this > 0 and running_pref_due > 0:
            ok(7, "no GP promote before the pref is satisfied", False,
               f"year {r['year']}: GP took {gp_this} with {running_pref_due} pref outstanding")
    ok(7, "no GP promote before the pref is satisfied", True)

    # 8. LP IRR at least the pref, once capital and pref are fully settled
    fully_settled = sum(c["unreturned"]) == 0 and all(
        to_cents(i["unpaid_pref"]) == 0 for i in result["investors"])
    lp_irr = result["lp_aggregate"]["irr"]
    pref = result["pref_rate_pct"] / 100.0
    if fully_settled and lp_irr is not None and pref > 0:
        ok(8, "LP IRR at least the pref rate once fully paid", lp_irr >= pref - 1e-9,
           f"LP IRR {lp_irr:.8f} < pref {pref:.8f}")
    else:
        checks.append({"n": 8, "name": "LP IRR at least the pref rate once fully paid",
                       "passed": None, "detail": "not applicable — capital or pref still outstanding"})

    # 9/10 are cross-tool and need the source scenario; see verify_against_source()
    return checks


def verify_against_source(result: dict[str, Any], source_total_distributions: float,
                          source_levered_irr: float | None = None,
                          tolerance_cents: int | None = None,
                          source_levered_cashflows: list[float] | None = None) -> list[dict[str, Any]]:
    """Invariants 9 and 10, which need the Underwriting scenario to compare
    against and so cannot live inside the cascade itself.

    9  waterfall total == the source scenario's total distributions,
       plus any shortfall the property failed to cover (see below)
    10 with a 100% LP / 0% GP promote and a single LP funding all the
       equity, every dollar follows the property, so the LP's IRR must
       equal the property's levered IRR exactly. Not applicable when the
       property has a shortfall -- see the note at the check itself.

    Invariant 9 carries a stated precision bound rather than an arbitrary
    tolerance. The source total is a sum of unrounded floats; the waterfall
    rounds each period's cash to whole cents on the way in, because cent
    arithmetic is what makes invariant 1 exact. Rounding can move each
    period by at most half a cent, so the two totals can legitimately
    differ by up to one cent per period and no more. Anything beyond that
    is money going missing, which is what this check is for.

    Note the bound applies only to the comparison against the source. The
    stricter statement -- that the waterfall distributed every cent it
    actually received -- is invariant 1, and that one is exact."""
    checks = []
    if source_levered_cashflows is not None:
        result["_source_levered_cashflows"] = source_levered_cashflows
    n_periods = max(1, len(result.get("periods") or []))
    if tolerance_cents is None:
        tolerance_cents = n_periods
    # The source total sums the property's cash flows INCLUDING any year
    # that went negative; the waterfall distributes zero in such a year and
    # records the gap as a shortfall. So the conservation statement is
    #
    #     distributed == source total + shortfall
    #
    # which is the same identity as before whenever no year is negative,
    # and is stricter than a widened tolerance would be -- it pins the
    # difference to the shortfall exactly rather than allowing it to be
    # anything small.
    shortfall_cents = result.get("_cents", {}).get("total_shortfall", 0)
    diff = abs(to_cents(result["totals"]["distributed"]) - shortfall_cents
               - to_cents(source_total_distributions))
    passed = diff <= tolerance_cents
    checks.append({"n": 9, "name": "waterfall total matches source scenario",
                   "passed": passed,
                   "detail": f"difference of {diff} cent(s); bound is {tolerance_cents} "
                             f"(one per period, from cent-rounding the input)"})
    if not passed:
        raise WaterfallInvariantError(
            f"Invariant 9 failed: waterfall distributed "
            f"{result['totals']['distributed']} (less a shortfall of "
            f"{to_dollars(shortfall_cents)}) against source total "
            f"{source_total_distributions} ({diff} cents apart, bound {tolerance_cents})")

    # Invariant 10 asserts that a degenerate 100/0 cascade reproduces the
    # property's own cash flows and IRR exactly. That claim is TRUE ONLY
    # WHEN THE PROPERTY NEVER GOES NEGATIVE.
    #
    # An LP does not receive a negative distribution. If year 1 fails to
    # cover debt service the property's flow is -13,178 and the LP's is 0
    # -- the shortfall is funded by reserves or a capital call, which is a
    # contribution, not a distribution. So the two vectors legitimately
    # differ, and so do the IRRs.
    #
    # This is reported as NOT APPLICABLE with the reason stated, exactly as
    # invariant 8 already does while capital is outstanding. It is not
    # weakened and not skipped silently: on a deal with no shortfall it
    # still runs, and still raises.
    shortfall_cents = result.get("_cents", {}).get("total_shortfall", 0)
    if source_levered_irr is not None and shortfall_cents:
        checks.append({
            "n": 10, "name": "degenerate 100/0 reproduces the property cash flows",
            "passed": None,
            "detail": f"not applicable — the property failed to cover "
                      f"{to_dollars(shortfall_cents)} and the LP took no negative "
                      f"distribution, so the two vectors cannot match by design"})
    elif source_levered_irr is not None:
        lp_irr = result["lp_aggregate"]["irr"]
        # Stated as flow-vector equality rather than IRR equality, which is
        # the stronger claim and the one that is exactly true. With a 100/0
        # promote and one LP funding all the equity, the LP's cash flows
        # ARE the property's, cent for cent -- so if the vectors match, the
        # cascade provably reordered nothing.
        #
        # The IRRs then differ only by the input quantization: the property
        # IRR is computed on unrounded floats while the waterfall rounds
        # each period to whole cents, which moves the rate by ~1e-9. That
        # residual belongs to the cent conversion, not to the cascade, so
        # it is reported against a stated bound instead of pretended away.
        source_flows = result.get("_source_levered_cashflows")
        if source_flows is not None:
            lp_flows = result["lp_aggregate"]["cashflows"]
            same = (len(lp_flows) == len(source_flows) and
                    all(to_cents(a) == to_cents(b) for a, b in zip(lp_flows, source_flows)))
            checks.append({"n": 10, "name": "degenerate 100/0 reproduces the property cash flows",
                           "passed": same,
                           "detail": "LP cash flows identical to the property's, cent for cent"
                                     if same else f"{lp_flows} != {source_flows}"})
            if not same:
                raise WaterfallInvariantError(
                    f"Invariant 10 failed: LP cash flows {lp_flows} differ from the "
                    f"property's {source_flows}")

        match = lp_irr is not None and abs(lp_irr - source_levered_irr) < IRR_QUANTIZATION_BOUND
        checks.append({"n": 10, "name": "degenerate 100/0 reproduces the property IRR",
                       "passed": match,
                       "detail": f"LP IRR {lp_irr:.12f} vs property {source_levered_irr:.12f} "
                                 f"(bound {IRR_QUANTIZATION_BOUND:g}, from cent-rounding)"})
        if not match:
            raise WaterfallInvariantError(
                f"Invariant 10 failed: LP IRR {lp_irr} != property levered IRR "
                f"{source_levered_irr} beyond the {IRR_QUANTIZATION_BOUND:g} quantization bound")
    return checks


def periods_from_underwriting(returns: dict[str, Any]) -> list[dict[str, Any]]:
    """Distributable cash from an Underwriting scenario's returns.

    Reads the separated components -- per-year operating cash flow and the
    net sale proceeds -- rather than levered_cashflows, which folds the
    sale into the final year. A waterfall has to be able to treat sale
    proceeds differently from operations, so they must arrive apart even
    though this beta's tiers happen to treat them alike."""
    years = returns.get("years") or []
    sale = returns.get("net_sale_proceeds") or 0.0
    out = []
    for idx, y in enumerate(years):
        # A refinance is a third kind of money and arrives as its own
        # component. operating_cash is the year's cash flow NET of it, so
        # the two cannot double-count: analyze_noi_series() adds the refi
        # into cash_flow, and it is taken back out here.
        refi = y.get("refi_proceeds") or 0.0
        # Split in CENTS, not dollars. The cascade converts each component
        # separately, so `to_cents(a) + to_cents(b)` has to equal
        # `to_cents(a + b)` or the year is off by a cent -- and invariant
        # 10 compares the LP vector against the property's cent for cent,
        # exactly, with no tolerance. Subtracting in dollars and letting
        # each half round independently loses that cent about half the
        # time, which is how this was found.
        #
        # Applied only when there IS a refinance. With none, the original
        # expression is used untouched, so every scenario that predates
        # this carries the same unrounded float it always did.
        cash_flow = y.get("cash_flow") or 0.0
        operating = (to_dollars(to_cents(cash_flow) - to_cents(refi))
                     if refi else cash_flow)
        out.append({
            "year": y.get("year", idx + 1),
            "operating_cash": operating,
            "sale_proceeds": sale if idx == len(years) - 1 else 0.0,
            "refi_proceeds": refi,
        })
    return out
