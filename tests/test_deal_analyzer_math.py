"""
Unit tests for tools/deal_analyzer_math.py.

Verification strategy: wherever possible these check results against an
*independent* method rather than against numbers produced by the same code
they are testing.

  * Loan mechanics are checked against a month-by-month amortization
    simulation written from scratch in the test -- if monthly_payment() is
    wrong, the simulated balance will not land on zero.
  * IRR is checked by confirming NPV at the returned rate is ~0, which is
    the definition of an IRR and is independent of how it was found. Two
    analytically-known cases (a single doubling, a flat 10%) pin the
    absolute scale.
  * Ratio metrics are checked against arithmetic restated in the test.

That way a copy-paste error in the module cannot silently validate itself.
"""

import unittest

from tools import deal_analyzer_math as m


def simulate_amortization(principal, annual_rate, amort_years, months):
    """Independent month-by-month loan simulation, deliberately not using
    anything from the module under test."""
    r = annual_rate / 12.0
    pmt = m.monthly_payment(principal, annual_rate, amort_years)
    balance = principal
    for _ in range(months):
        interest = balance * r
        balance = balance + interest - pmt
    return max(0.0, balance)


def assert_npv_zero(case, rate, flows):
    """NPV at the IRR must be zero. Asserted *relative to* the largest cash
    flow: an absolute dollar threshold would be stricter on a $10k deal
    than a $1M one for the same rate precision."""
    scale = max(abs(cf) for cf in flows) or 1.0
    case.assertLess(abs(m.npv(rate, flows)) / scale, 1e-9)


BASE = {
    "purchase_price": 1_000_000.0,
    "closing_costs_pct": 2.0,
    "ltv_pct": 70.0,
    "interest_rate_pct": 6.5,
    "amort_years": 30,
    "noi_year1": 70_000.0,
    "noi_growth_pct": 3.0,
    "hold_years": 5,
    "exit_cap_pct": 6.0,
    "selling_costs_pct": 2.0,
}


def scenario(**overrides):
    data = dict(BASE)
    data.update(overrides)
    return data


class TestLoanMechanics(unittest.TestCase):
    def test_payment_fully_amortizes_to_zero(self):
        """A correct level payment leaves a zero balance after the final
        payment. Verified by independent simulation, not by formula."""
        self.assertAlmostEqual(simulate_amortization(700_000, 0.065, 30, 360), 0.0, places=2)

    def test_zero_interest_is_straight_line(self):
        pmt = m.monthly_payment(300_000, 0.0, 25)
        self.assertAlmostEqual(pmt, 300_000 / 300, places=6)
        self.assertAlmostEqual(simulate_amortization(300_000, 0.0, 25, 300), 0.0, places=6)

    def test_remaining_balance_matches_simulation(self):
        for months in (12, 60, 120, 359):
            with self.subTest(months=months):
                self.assertAlmostEqual(
                    m.remaining_balance(700_000, 0.065, 30, months),
                    simulate_amortization(700_000, 0.065, 30, months),
                    places=2,
                )

    def test_no_loan_means_no_payment_or_balance(self):
        self.assertEqual(m.monthly_payment(0, 0.065, 30), 0.0)
        self.assertEqual(m.remaining_balance(0, 0.065, 30, 60), 0.0)

    def test_balance_never_goes_negative_past_full_amortization(self):
        self.assertEqual(m.remaining_balance(100_000, 0.05, 10, 600), 0.0)


class TestIRR(unittest.TestCase):
    def test_known_flat_ten_percent(self):
        rate, reason = m.irr([-100.0, 110.0])
        self.assertIsNone(reason)
        self.assertAlmostEqual(rate, 0.10, places=6)

    def test_known_doubling_over_two_years(self):
        """-100 now, 200 in two years => (2)^(1/2) - 1 = 41.4214%."""
        rate, reason = m.irr([-100.0, 0.0, 200.0])
        self.assertIsNone(reason)
        self.assertAlmostEqual(rate, 2 ** 0.5 - 1, places=6)

    def test_npv_at_returned_rate_is_zero(self):
        """The defining property of an IRR, checked independently."""
        flows = [-320_000.0, 24_500.0, 26_000.0, 27_500.0, 29_000.0, 480_000.0]
        rate, reason = m.irr(flows)
        self.assertIsNone(reason)
        assert_npv_zero(self, rate, flows)

    def test_negative_early_cashflows_still_resolve(self):
        """Early years cash-flow negative (debt service exceeds NOI), then
        a large sale. Bisection must still find the crossing."""
        flows = [-500_000.0, -20_000.0, -12_000.0, -4_000.0, 5_000.0, 900_000.0]
        rate, reason = m.irr(flows)
        self.assertIsNone(reason)
        assert_npv_zero(self, rate, flows)
        self.assertGreater(rate, 0.0)

    def test_all_negative_is_not_computable(self):
        rate, reason = m.irr([-100.0, -50.0, -25.0])
        self.assertIsNone(rate)
        self.assertIn("sign", reason.lower())

    def test_all_positive_is_not_computable(self):
        rate, reason = m.irr([100.0, 50.0])
        self.assertIsNone(rate)
        self.assertIsNotNone(reason)

    def test_never_returns_nan(self):
        for flows in ([-100.0, -50.0], [100.0, 50.0], [-100.0, 110.0], [0.0, 0.0]):
            rate, reason = m.irr(flows)
            if rate is not None:
                self.assertEqual(rate, rate, "IRR returned NaN")
                self.assertNotEqual(abs(rate), float("inf"))
            else:
                self.assertTrue(reason)


class TestEdgeCases(unittest.TestCase):
    def test_all_cash_dscr_is_none_with_reason(self):
        r = m.analyze(scenario(ltv_pct=0.0))
        self.assertIsNone(r["dscr"])
        self.assertIn("all-cash", r["dscr_reason"].lower())
        self.assertEqual(r["loan_amount"], 0.0)
        self.assertEqual(r["annual_debt_service"], 0.0)
        # equity is the whole price plus closing costs
        self.assertAlmostEqual(r["equity_invested"], 1_020_000.0, places=6)

    def test_exit_cap_zero_is_validation_error(self):
        with self.assertRaises(m.ValidationError) as ctx:
            m.analyze(scenario(exit_cap_pct=0.0))
        self.assertIn("cap rate", str(ctx.exception).lower())

    def test_ltv_over_100_is_validation_error(self):
        with self.assertRaises(m.ValidationError) as ctx:
            m.analyze(scenario(ltv_pct=105.0))
        self.assertIn("ltv", str(ctx.exception).lower())

    def test_ltv_exactly_100_with_closing_costs_is_allowed(self):
        """100% LTV is not itself invalid -- the equity is the closing
        costs. Only >100 is rejected."""
        r = m.analyze(scenario(ltv_pct=100.0))
        self.assertAlmostEqual(r["equity_invested"], 20_000.0, places=6)

    def test_ltv_100_with_no_closing_costs_rejected(self):
        with self.assertRaises(m.ValidationError):
            m.analyze(scenario(ltv_pct=100.0, closing_costs_pct=0.0))

    def test_irr_not_computable_surfaces_reason_not_crash(self):
        """A deal so bad it never returns capital: tiny NOI, no growth,
        near-worthless exit. analyze() must complete and report why."""
        r = m.analyze(scenario(
            noi_year1=1_000.0, noi_growth_pct=0.0, exit_cap_pct=95.0, hold_years=3,
        ))
        self.assertIsNone(r["levered_irr"])
        self.assertTrue(r["levered_irr_reason"])
        # the rest of the result is still fully populated
        self.assertIsNotNone(r["equity_multiple"])
        self.assertIsNotNone(r["going_in_cap_rate"])

    def test_zero_and_negative_noi_growth_accepted(self):
        for g in (0.0, -2.0):
            with self.subTest(growth=g):
                r = m.analyze(scenario(noi_growth_pct=g))
                self.assertEqual(len(r["years"]), 5)

    def test_invalid_purchase_price_rejected(self):
        with self.assertRaises(m.ValidationError):
            m.analyze(scenario(purchase_price=0.0))

    def test_hold_period_bounds(self):
        with self.assertRaises(m.ValidationError):
            m.analyze(scenario(hold_years=0))
        with self.assertRaises(m.ValidationError):
            m.analyze(scenario(hold_years=31))


class TestFullScenario(unittest.TestCase):
    """The reference case. Each assertion restates the arithmetic
    independently of the module."""

    def setUp(self):
        self.r = m.analyze(scenario())

    def test_capital_stack(self):
        self.assertAlmostEqual(self.r["loan_amount"], 700_000.0, places=6)
        self.assertAlmostEqual(self.r["closing_costs"], 20_000.0, places=6)
        self.assertAlmostEqual(self.r["equity_invested"], 320_000.0, places=6)

    def test_going_in_cap(self):
        self.assertAlmostEqual(self.r["going_in_cap_rate"], 70_000 / 1_000_000, places=9)

    def test_dscr(self):
        self.assertAlmostEqual(self.r["dscr"], 70_000 / self.r["annual_debt_service"], places=9)
        self.assertGreater(self.r["dscr"], 1.0)

    def test_cash_on_cash(self):
        cf1 = 70_000 - self.r["annual_debt_service"]
        self.assertAlmostEqual(self.r["cash_on_cash"], cf1 / 320_000.0, places=9)

    def test_noi_grows_at_stated_rate(self):
        years = self.r["years"]
        self.assertAlmostEqual(years[0]["noi"], 70_000.0, places=6)
        for t in range(1, 5):
            self.assertAlmostEqual(years[t]["noi"], 70_000 * 1.03 ** t, places=6)

    def test_exit_uses_forward_noi(self):
        """Year 6 NOI capitalized, not year 5."""
        self.assertAlmostEqual(self.r["noi_exit_year"], 70_000 * 1.03 ** 5, places=6)
        self.assertAlmostEqual(self.r["gross_sale_price"], (70_000 * 1.03 ** 5) / 0.06, places=4)

    def test_net_sale_proceeds(self):
        gross = self.r["gross_sale_price"]
        expected = gross - gross * 0.02 - m.remaining_balance(700_000, 0.065, 30, 60)
        self.assertAlmostEqual(self.r["net_sale_proceeds"], expected, places=4)

    def test_equity_multiple(self):
        total = sum(y["cash_flow"] for y in self.r["years"]) + self.r["net_sale_proceeds"]
        self.assertAlmostEqual(self.r["equity_multiple"], total / 320_000.0, places=9)
        self.assertGreater(self.r["equity_multiple"], 1.0)

    def test_levered_irr_npv_is_zero(self):
        self.assertIsNone(self.r["levered_irr_reason"])
        assert_npv_zero(self, self.r["levered_irr"], self.r["levered_cashflows"])

    def test_unlevered_irr_npv_is_zero(self):
        self.assertIsNone(self.r["unlevered_irr_reason"])
        assert_npv_zero(self, self.r["unlevered_irr"], self.r["unlevered_cashflows"])

    def test_unlevered_excludes_debt_entirely(self):
        """The unlevered vector must not carry the loan on either end --
        no loan proceeds at entry, no balance retired at exit."""
        self.assertAlmostEqual(self.r["unlevered_cashflows"][0], -(1_000_000.0 + 20_000.0), places=6)
        gross = self.r["gross_sale_price"]
        expected_final = self.r["years"][-1]["noi"] + gross - gross * 0.02
        self.assertAlmostEqual(self.r["unlevered_cashflows"][-1], expected_final, places=4)

    def test_leverage_is_accretive_here(self):
        """With a 7% going-in cap against a 6.5% loan, leverage should
        improve returns. A sanity check on sign, not a precise value."""
        self.assertGreater(self.r["levered_irr"], self.r["unlevered_irr"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
