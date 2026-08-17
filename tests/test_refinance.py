"""
Cash-out refinance: a capital event in the middle of a hold.

Everything before this was operating cash flow and a terminal sale. A
refinance is the first mid-hold event that hands money to investors, and
it does three things at once: it replaces the loan, it returns capital,
and it changes what the preferred return accrues on for every period
afterwards.

MICHELLE'S ORDER, WHICH IS THE PART THAT MATTERS

    payoff of the old loan -> fees -> return of capital

Preferred return is deliberately absent. It is not paid at the event; it
keeps accruing afterwards on whatever capital is still unreturned. That
is the whole point of returning capital early, and it is why the tests
below check the accrual in the year AFTER the refinance rather than only
the dollars moved at it.

THE FEE BASE IS AN ASSUMPTION AND IS FLAGGED AS ONE

refi_fee_pct is charged on the excess proceeds pool, not on the gross new
loan. The stated order forces that reading -- the fee sits between the
payoff and the return of capital, so it can only be a share of what
survives the payoff. The sale-side capital transaction fee uses the gross
sale price instead, so the two bases genuinely differ. If that is wrong,
one line in refinance() changes and these tests say which.
"""

import unittest

from tools import deal_analyzer_math as m
from tools import waterfall_math as wm

# Eagle Rock, scenario 4, as it really stands.
LOAN = 4_543_500.0
RATE = 0.065
AMORT = 30
HOLD = 5
EQUITY = 2_688_848.65

# The refinance fixture's own numbers, named so that assertions derive from
# them rather than restating them. refi_costs_pct is deliberately NOT 1.0:
# at 1.0 the costs came to $52,000, which is also 1% of the $5.2M gross new
# loan -- so 'costs' and 'a gross-loan fee' were the same number, and the
# expression `5_200_000.0 * 0.01` appeared in two tests meaning two
# different things. Any test that compared them could pass by coincidence.
REFI_LOAN = 5_200_000.0
REFI_COSTS_PCT = 1.37
REFI_FEE_PCT = 1.0


def refi(**over):
    kw = dict(refi_year=3, refi_loan=REFI_LOAN, refi_rate=0.06,
              refi_amort_years=30, refi_costs_pct=REFI_COSTS_PCT,
              refi_fee_pct=REFI_FEE_PCT)
    kw.update(over)
    return m.refinance(LOAN, RATE, AMORT, HOLD, **kw)


class ExcessProceedsTests(unittest.TestCase):
    def test_the_payoff_is_the_io_aware_balance_at_the_refinance(self):
        ev = refi()
        self.assertEqual(ev["payoff_balance"],
                         m.remaining_balance(LOAN, RATE, AMORT, 36))

    def test_an_interest_only_period_raises_the_payoff(self):
        """Nothing amortised, so more is owed at the refinance."""
        plain = refi()["payoff_balance"]
        with_io = refi(io_months=24)["payoff_balance"]
        self.assertGreater(with_io, plain)
        self.assertEqual(with_io,
                         m.remaining_balance(LOAN, RATE, AMORT, 36, io_months=24))

    def test_costs_are_a_share_of_the_new_loan(self):
        self.assertEqual(refi()["refi_costs"],
                         REFI_LOAN * (REFI_COSTS_PCT / 100.0))

    def test_excess_is_the_new_loan_less_payoff_and_costs(self):
        ev = refi()
        self.assertAlmostEqual(
            ev["excess_proceeds"],
            REFI_LOAN - ev["payoff_balance"] - ev["refi_costs"], places=6)

    def test_the_gp_fee_comes_off_the_excess_pool(self):
        """The assumption this file's docstring flags."""
        ev = refi()
        self.assertAlmostEqual(ev["gp_fee"],
                               ev["excess_proceeds"] * (REFI_FEE_PCT / 100.0),
                               places=6)

    def test_and_is_not_a_share_of_the_gross_new_loan(self):
        """Pinned so the alternative reading cannot creep in unnoticed."""
        ev = refi()
        self.assertNotAlmostEqual(ev["gp_fee"],
                                  REFI_LOAN * (REFI_FEE_PCT / 100.0), places=2)

    def test_investors_receive_the_excess_less_the_fee(self):
        ev = refi()
        self.assertAlmostEqual(ev["proceeds_to_investors"],
                               ev["excess_proceeds"] - ev["gp_fee"], places=6)

    def test_a_zero_fee_sends_everything_to_investors(self):
        ev = refi(refi_fee_pct=0.0)
        self.assertEqual(ev["gp_fee"], 0.0)
        self.assertEqual(ev["proceeds_to_investors"], ev["excess_proceeds"])


class RefusalTests(unittest.TestCase):
    def test_a_cash_in_refinance_is_refused(self):
        with self.assertRaises(m.ValidationError) as caught:
            refi(refi_loan=1_000_000.0)
        self.assertIn("cash IN", str(caught.exception))

    def test_the_message_says_how_much_would_be_needed(self):
        small = 1_000_000.0
        with self.assertRaises(m.ValidationError) as caught:
            refi(refi_loan=small)
        payoff = m.remaining_balance(LOAN, RATE, AMORT, 36)
        needed = payoff + small * (REFI_COSTS_PCT / 100.0) - small
        self.assertIn(f"{needed:,.0f}", str(caught.exception))

    def test_a_refinance_in_the_exit_year_is_refused(self):
        with self.assertRaises(m.ValidationError) as caught:
            refi(refi_year=HOLD)
        self.assertIn("between 1 and 4", str(caught.exception))

    def test_a_refinance_after_the_hold_is_refused(self):
        with self.assertRaises(m.ValidationError):
            refi(refi_year=HOLD + 4)

    def test_a_zero_new_loan_is_refused(self):
        with self.assertRaises(m.ValidationError):
            refi(refi_loan=0.0)

    def test_break_even_is_allowed(self):
        """Zero excess is a real structure, not an error."""
        payoff = m.remaining_balance(LOAN, RATE, AMORT, 36)
        ev = refi(refi_loan=payoff, refi_costs_pct=0.0)
        self.assertAlmostEqual(ev["excess_proceeds"], 0.0, places=6)
        self.assertAlmostEqual(ev["proceeds_to_investors"], 0.0, places=6)


class DebtServiceSplicingTests(unittest.TestCase):
    def test_the_series_has_one_entry_per_year_of_the_hold(self):
        self.assertEqual(len(refi()["debt_service_series"]), HOLD)

    def test_years_before_the_refinance_are_the_original_loan(self):
        ev = refi()
        original = m.annual_debt_service_series(LOAN, RATE, AMORT, HOLD)
        self.assertEqual(ev["debt_service_series"][:3], original[:3])

    def test_years_after_are_the_new_loan(self):
        ev = refi()
        after = m.annual_debt_service_series(5_200_000.0, 0.06, 30, 2)
        self.assertEqual(ev["debt_service_series"][3:], after)

    def test_the_new_loan_can_carry_its_own_interest_only_period(self):
        ev = refi(refi_io_months=12)
        self.assertLess(ev["debt_service_series"][3],
                        refi()["debt_service_series"][3])

    def test_the_balloon_is_the_new_loans_not_the_originals(self):
        ev = refi()
        self.assertEqual(ev["balance_at_exit"],
                         m.remaining_balance(5_200_000.0, 0.06, 30, 24))

    def test_the_balloon_is_not_the_original_loans(self):
        ev = refi()
        self.assertNotAlmostEqual(
            ev["balance_at_exit"],
            m.remaining_balance(LOAN, RATE, AMORT, HOLD * 12), places=2)


def analyze(**over):
    inputs = {
        "purchase_price": 6_990_000.0, "closing_costs_pct": 2.0,
        "ltv_pct": 65.0, "interest_rate_pct": 6.5, "amort_years": AMORT,
        "hold_years": HOLD, "exit_cap_pct": 6.0, "selling_costs_pct": 2.0,
        "noi_year1": 482_120.76, "noi_growth_pct": 3.0,
    }
    inputs.update(over)
    noi = [inputs["noi_year1"] * 1.03 ** t for t in range(HOLD)]
    return m.analyze_noi_series(inputs, noi, inputs["noi_year1"] * 1.03 ** HOLD)


class EngineTests(unittest.TestCase):
    REFI = dict(refi_year=3, refi_loan_amount=REFI_LOAN, refi_rate_pct=6.0,
                refi_amort_years=30, refi_costs_pct=REFI_COSTS_PCT,
                refi_fee_pct=REFI_FEE_PCT)

    def test_absent_refinance_changes_nothing(self):
        plain = analyze()
        self.assertIsNone(plain["refinance"])
        self.assertTrue(all(y["refi_proceeds"] == 0.0 for y in plain["years"]))

    def test_the_proceeds_land_in_exactly_one_year(self):
        result = analyze(**self.REFI)
        got = [y["refi_proceeds"] > 0 for y in result["years"]]
        self.assertEqual(got, [False, False, True, False, False])

    def test_that_year_is_the_refinance_year(self):
        result = analyze(**self.REFI)
        self.assertEqual(result["years"][2]["refi_proceeds"],
                         result["refinance"]["proceeds_to_investors"])

    def test_the_proceeds_are_added_to_that_years_cash_flow(self):
        plain, withrefi = analyze(), analyze(**self.REFI)
        gain = withrefi["years"][2]["cash_flow"] - plain["years"][2]["cash_flow"]
        self.assertAlmostEqual(gain,
                               withrefi["refinance"]["proceeds_to_investors"],
                               places=6)

    def test_returning_capital_early_raises_the_irr(self):
        self.assertGreater(analyze(**self.REFI)["levered_irr"],
                           analyze()["levered_irr"])

    def test_the_rate_defaults_to_the_existing_loans(self):
        left_blank = analyze(**{**self.REFI, "refi_rate_pct": None})
        self.assertEqual(left_blank["refinance"]["debt_service_series"][3],
                         m.annual_debt_service_series(
                             5_200_000.0, 0.065, 30, 2)[0])

    def test_the_amortization_defaults_to_the_existing_loans(self):
        left_blank = analyze(**{**self.REFI, "refi_amort_years": None})
        self.assertEqual(
            left_blank["refinance"]["debt_service_series"][3],
            m.annual_debt_service_series(5_200_000.0, 0.06, AMORT, 2)[0])

    def test_a_refinance_on_a_multi_loan_stack_is_refused(self):
        """Which loan it replaces is not defined, so it is not guessed."""
        debt = {"loan_amount": 4_000_000.0, "annual_debt_service": 300_000.0,
                "balance_at_exit": 3_800_000.0}
        with self.assertRaises(m.ValidationError) as caught:
            inputs = {
                "purchase_price": 6_990_000.0, "closing_costs_pct": 2.0,
                "ltv_pct": 65.0, "interest_rate_pct": 6.5,
                "amort_years": AMORT, "hold_years": HOLD, "exit_cap_pct": 6.0,
                "selling_costs_pct": 2.0, "noi_year1": 482_120.76,
                "noi_growth_pct": 3.0, **self.REFI,
            }
            noi = [482_120.76 * 1.03 ** t for t in range(HOLD)]
            m.analyze_noi_series(inputs, noi, 482_120.76 * 1.03 ** HOLD,
                                 debt=debt)
        self.assertIn("multi-loan", str(caught.exception))


class InvariantAssertions:
    """Strict invariant checking, mixed into the waterfall test cases.

    WHY assertIsNot(passed, False) WAS NOT ENOUGH

    check_invariants() and verify_against_source() report each invariant as
    a dict with "passed" set to True, False, or None. None means the
    invariant did not apply to this run -- invariant 10 returns it when the
    property failed to cover a shortfall, because the LP and property
    vectors cannot match by design in that case.

    `assertIsNot(passed, False)` therefore accepts None as success. An
    invariant that silently stopped evaluating -- because a key was renamed,
    a guard inverted, or a branch stopped being reached -- would report None
    and the assertion would still go green. The suite would claim invariant
    coverage it did not have.

    These helpers require passed to be exactly True, and separately require
    that the invariants expected to run actually ran. A not-applicable
    invariant is legitimate but must be named at the call site, never
    absorbed silently.
    """

    def assertInvariantsHold(self, checks, *, expect=None):
        seen = set()
        for check in checks:
            seen.add(check["n"])
            with self.subTest(invariant=check["n"], name=check["name"]):
                self.assertIs(
                    check["passed"], True,
                    f"invariant {check['n']} ({check['name']}) reported "
                    f"passed={check['passed']!r}, not True. "
                    f"None means it never evaluated. {check.get('detail', '')}")
        self.assertTrue(seen, "no invariants were evaluated at all")
        if expect is not None:
            self.assertEqual(
                seen & set(expect), set(expect),
                f"expected invariants {sorted(expect)} to be evaluated; "
                f"saw {sorted(seen)}")
        return seen

class WaterfallTests(InvariantAssertions, unittest.TestCase):
    """The Investor Report half, and the coordination between them."""

    TERMS = {"pref_rate_pct": 8.0, "tiers": [
        {"tier_type": "return_of_capital"}, {"tier_type": "pref"},
        {"tier_type": "promote", "gp_share_pct": 20.0}]}

    def _run(self, returns):
        periods = wm.periods_from_underwriting(returns)
        lps = [{"investor_id": 1, "name": "LP",
                "amount": returns["equity_invested"], "investor_class": "LP"}]
        return periods, wm.run_waterfall(lps, periods, self.TERMS)

    def test_the_refinance_arrives_as_its_own_component(self):
        returns = analyze(**EngineTests.REFI)
        periods, _ = self._run(returns)
        self.assertAlmostEqual(periods[2]["refi_proceeds"],
                               returns["refinance"]["proceeds_to_investors"],
                               places=6)

    def test_operating_cash_excludes_it_so_nothing_double_counts(self):
        """Asserted in cents, which is the unit that has to balance.

        The split is done in cents on purpose: the cascade converts each
        component separately, so the two halves must reconstitute the
        year's total exactly or invariant 10 -- which compares vectors
        cent for cent with no tolerance -- fails. Asserting to six
        decimals here would be asserting the opposite of the fix.
        """
        returns = analyze(**EngineTests.REFI)
        periods, _ = self._run(returns)
        year = returns["years"][2]
        self.assertEqual(
            wm.to_cents(periods[2]["operating_cash"])
            + wm.to_cents(periods[2]["refi_proceeds"]),
            wm.to_cents(year["cash_flow"]))
        self.assertLess(
            abs(periods[2]["operating_cash"]
                - (year["cash_flow"] - periods[2]["refi_proceeds"])), 0.01)

    def test_it_pays_return_of_capital(self):
        _, wf = self._run(analyze(**EngineTests.REFI))
        self.assertGreater(wf["periods"][2]["refi_return_of_capital"], 0)

    def test_pref_accrual_falls_in_the_year_after(self):
        """The mechanism the whole feature exists for."""
        _, wf = self._run(analyze(**EngineTests.REFI))
        self.assertLess(wf["periods"][3]["accrued_pref"],
                        wf["periods"][2]["accrued_pref"])

    def test_no_pref_is_paid_from_the_refinance_pool(self):
        _, wf = self._run(analyze(**EngineTests.REFI))
        from_refi = [t for t in wf["periods"][2]["tiers"]
                     if t.get("from_refinance")]
        self.assertTrue(from_refi)
        self.assertTrue(all(t["tier_type"] == "return_of_capital"
                            for t in from_refi))

    def test_return_of_capital_is_capped_at_what_is_outstanding(self):
        returns = analyze(refi_year=1, refi_loan_amount=7_400_000.0,
                          refi_rate_pct=6.0, refi_costs_pct=0.5,
                          refi_fee_pct=1.0)
        _, wf = self._run(returns)
        self.assertLessEqual(wf["periods"][0]["refi_return_of_capital"],
                             returns["equity_invested"] + 0.01)

    def test_a_surplus_beyond_that_is_still_distributed(self):
        """Invariant 1 requires every cent received to go somewhere."""
        returns = analyze(refi_year=1, refi_loan_amount=7_400_000.0,
                          refi_rate_pct=6.0, refi_costs_pct=0.5,
                          refi_fee_pct=1.0)
        _, wf = self._run(returns)
        self.assertInvariantsHold(wm.check_invariants(wf),
                                  expect=range(1, 9))

    def test_every_invariant_holds_with_a_refinance_present(self):
        _, wf = self._run(analyze(**EngineTests.REFI))
        self.assertInvariantsHold(wm.check_invariants(wf),
                                  expect=range(1, 9))

    def test_invariant_10_still_reproduces_the_property(self):
        """The coordination enforcer, unchanged and still load-bearing.

        With a 100/0 promote the LP's flows ARE the property's -- refinance
        included. If either side of the change had been made without the
        other, this is where it would fail.
        """
        returns = analyze(**EngineTests.REFI)
        periods = wm.periods_from_underwriting(returns)
        lps = [{"investor_id": 1, "name": "LP",
                "amount": returns["equity_invested"], "investor_class": "LP"}]
        wf = wm.run_waterfall(lps, periods,
                              {"pref_rate_pct": 0.0,
                               "tiers": [{"tier_type": "promote",
                                          "gp_share_pct": 0.0}]})
        source_total = sum(p["operating_cash"] + p["sale_proceeds"]
                           + p["refi_proceeds"] for p in periods)
        checks = wm.verify_against_source(
            wf, source_total,
            source_levered_cashflows=returns["levered_cashflows"],
            source_levered_irr=returns["levered_irr"])
        # 9 and 10 both, and 10 must have actually evaluated -- with a
        # refinance present it is the check that proves the split did not
        # reorder a cent.
        self.assertInvariantsHold(checks, expect=(9, 10))

    def test_invariant_9_conservation_holds(self):
        returns = analyze(**EngineTests.REFI)
        periods, wf = self._run(returns)
        source_total = sum(p["operating_cash"] + p["sale_proceeds"]
                           + p["refi_proceeds"] for p in periods)
        nine = next(c for c in wm.verify_against_source(wf, source_total)
                    if c["n"] == 9)
        self.assertInvariantsHold([nine], expect=(9,))

    def test_a_thin_refinance_does_not_crash_the_cascade(self):
        returns = analyze(refi_year=3, refi_loan_amount=4_390_000.0,
                          refi_rate_pct=6.0, refi_costs_pct=0.2,
                          refi_fee_pct=1.0)
        self.assertGreaterEqual(
            returns["refinance"]["proceeds_to_investors"], 0.0)
        _, wf = self._run(returns)
        self.assertInvariantsHold(wm.check_invariants(wf),
                                  expect=range(1, 9))

    def test_no_refinance_leaves_the_period_shape_as_it_was(self):
        returns = analyze()
        periods, _ = self._run(returns)
        self.assertTrue(all(p["refi_proceeds"] == 0.0 for p in periods))
        for idx, p in enumerate(periods):
            with self.subTest(year=idx + 1):
                self.assertEqual(p["operating_cash"],
                                 returns["years"][idx]["cash_flow"])


if __name__ == "__main__":
    unittest.main()
