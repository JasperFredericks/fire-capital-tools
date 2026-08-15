"""
Interest-only periods, and the guarantee that they changed nothing else.

An IO period is the first thing in this engine that makes debt service a
function of time. Everything before it -- one loan or a stack of them --
paid the same amount every year, and the engine subtracted a single
scalar from each year's NOI. That assumption is now gone, which makes
this a shared-engine change: Deal Analyzer computes its returns with the
same code, and Deal Analyzer has no interest-only period at all.

So the tests here are in two halves.

The first half is the feature: the payment during IO, the payment after
it, the balloon, and the DSCR through the hold.

The second half is the guarantee, and is the more important of the two.
With no IO period every figure must be what it was, computed by the same
expression rather than one that merely agrees to the cent. That is
asserted three ways -- an equivalence grid across the parameter absent,
None and 0; an elementwise identity between the debt-service series and
the old scalar repeated; and a source-level assertion that Deal
Analyzer's route never sets the field in the first place.
"""

import ast
import unittest
from pathlib import Path

from tools import deal_analyzer_math as m
from tools import underwriting_loans_math as ulm

ROOT = Path(__file__).resolve().parent.parent

# Eagle Rock's real financing, scenario 4 on production.
LOAN = 4_543_500.0
RATE = 0.065
AMORT = 30
HOLD = 5


class IoPaymentTests(unittest.TestCase):
    def test_an_io_payment_is_one_month_of_interest(self):
        self.assertEqual(m.io_monthly_payment(LOAN, RATE), LOAN * (RATE / 12.0))

    def test_it_retires_no_principal(self):
        """The defining property, stated as a property rather than a number."""
        self.assertEqual(
            m.remaining_balance(LOAN, RATE, AMORT, 24, io_months=24), LOAN)

    def test_the_balance_is_untouched_at_every_point_inside_the_io_period(self):
        for month in (1, 6, 12, 23, 24):
            with self.subTest(month=month):
                self.assertEqual(
                    m.remaining_balance(LOAN, RATE, AMORT, month, io_months=24),
                    LOAN)

    def test_an_io_payment_is_smaller_than_an_amortizing_one(self):
        self.assertLess(m.io_monthly_payment(LOAN, RATE),
                        m.monthly_payment(LOAN, RATE, AMORT))

    def test_a_zero_principal_loan_pays_nothing(self):
        self.assertEqual(m.io_monthly_payment(0.0, RATE), 0.0)


class ConventionBTests(unittest.TestCase):
    """After IO ends the loan amortizes over its REMAINING term.

    Asserted against the alternative convention rather than only against
    a number, because the two differ by a few thousand dollars and a test
    that pinned one figure would not say which model produced it.
    """

    def test_the_payment_amortizes_the_remaining_term(self):
        series = m.annual_debt_service_series(LOAN, RATE, AMORT, HOLD,
                                              io_months=24)
        expected = m.monthly_payment(LOAN, RATE, AMORT - 2) * 12
        self.assertEqual(series[2], expected)

    def test_it_is_not_the_full_term_convention(self):
        series = m.annual_debt_service_series(LOAN, RATE, AMORT, HOLD,
                                              io_months=24)
        full_term = m.monthly_payment(LOAN, RATE, AMORT) * 12
        self.assertGreater(
            series[2], full_term,
            "convention B amortizes over a shorter remaining term, so the "
            "post-IO payment must be LARGER than the full-term payment")

    def test_the_loan_still_matures_on_its_original_schedule(self):
        """The reason for choosing B: nothing is left at original maturity."""
        balance = m.remaining_balance(LOAN, RATE, AMORT, AMORT * 12,
                                      io_months=24)
        self.assertAlmostEqual(balance, 0.0, places=6)

    def test_the_full_term_convention_would_leave_a_balloon_at_maturity(self):
        """Pinning the difference, so the choice stays visible."""
        pmt = m.monthly_payment(LOAN, RATE, AMORT)
        r = RATE / 12.0
        months = AMORT * 12 - 24
        grown = (1 + r) ** months
        left = LOAN * grown - pmt * ((grown - 1) / r)
        self.assertGreater(left, 100_000.0)

    def test_the_convention_is_named_on_the_result(self):
        result = _analyze(io_years=2)
        self.assertIn("REMAINING term", result["balloon_convention"])
        self.assertIn("Level payment", _analyze()["balloon_convention"])


def _analyze(**over):
    inputs = {
        "purchase_price": 6_990_000.0, "closing_costs_pct": 2.0,
        "ltv_pct": 65.0, "interest_rate_pct": 6.5, "amort_years": AMORT,
        "hold_years": HOLD, "exit_cap_pct": 5.75, "selling_costs_pct": 2.0,
        "noi_year1": 384_455.38, "noi_growth_pct": 3.0,
    }
    inputs.update(over)
    noi = [inputs["noi_year1"] * 1.03 ** t for t in range(HOLD)]
    return m.analyze_noi_series(inputs, noi, inputs["noi_year1"] * 1.03 ** HOLD)


class DscrThroughTheHoldTests(unittest.TestCase):
    """DSCR is never one number when an IO period exists."""

    def test_the_io_dscr_and_the_post_io_dscr_are_both_reported(self):
        result = _analyze(io_years=2)
        self.assertIsNotNone(result["dscr_io"])
        self.assertIsNotNone(result["dscr_post_io"])
        self.assertGreater(result["dscr_io"], result["dscr_post_io"],
                           "the IO-period ratio is the flattering one")

    def test_dscr_min_is_the_minimum_across_the_hold(self):
        result = _analyze(io_years=2)
        self.assertEqual(result["dscr_min"], min(result["dscr_by_year"]))

    def test_dscr_min_is_not_the_headline_year_one_figure(self):
        """The whole point: year 1 would pass a covenant year 3 breaches."""
        result = _analyze(io_years=2)
        self.assertLess(result["dscr_min"], result["dscr"])

    def test_dscr_min_names_the_year_it_occurs(self):
        result = _analyze(io_years=2)
        self.assertEqual(
            result["dscr_by_year"][result["dscr_min_year"] - 1],
            result["dscr_min"])

    def test_each_year_uses_its_own_noi(self):
        result = _analyze(io_years=2)
        for idx, (year, ratio) in enumerate(
                zip(result["years"], result["dscr_by_year"])):
            with self.subTest(year=idx + 1):
                self.assertEqual(ratio, year["noi"] / year["debt_service"])

    def test_with_no_io_the_extra_fields_are_empty(self):
        result = _analyze()
        self.assertIsNone(result["dscr_io"])
        self.assertIsNone(result["dscr_post_io"])
        self.assertEqual(result["dscr_min"], result["dscr"])


class IoLongerThanTheHoldTests(unittest.TestCase):
    def test_the_balloon_is_the_whole_original_principal(self):
        result = _analyze(io_years=6)
        self.assertEqual(result["loan_balance_at_exit"], result["loan_amount"])

    def test_and_it_is_flagged_rather_than_silent(self):
        self.assertTrue(_analyze(io_years=6)["io_covers_whole_hold"])
        self.assertFalse(_analyze(io_years=2)["io_covers_whole_hold"])

    def test_debt_service_never_steps_up(self):
        series = _analyze(io_years=6)["debt_service_series"]
        self.assertEqual(len(set(series)), 1)


class ValidationTests(unittest.TestCase):
    def test_io_as_long_as_the_amortization_is_refused(self):
        with self.assertRaises(m.ValidationError) as caught:
            _analyze(io_years=30)
        self.assertIn("never", str(caught.exception).lower())

    def test_io_longer_than_the_amortization_is_refused(self):
        with self.assertRaises(m.ValidationError):
            _analyze(io_years=31)

    def test_a_negative_io_period_is_refused(self):
        with self.assertRaises(m.ValidationError):
            _analyze(io_years=-1)

    def test_a_loan_row_is_validated_the_same_way(self):
        with self.assertRaises(ulm.LoanValidationError):
            ulm.validate([{"amount": 1e6, "rate_pct": 6.0,
                           "amort_years": 30, "io_years": 30}])

    def test_a_loan_row_with_no_io_period_still_validates(self):
        for value in (None, "", 0):
            with self.subTest(value=value):
                ulm.validate([{"amount": 1e6, "rate_pct": 6.0,
                               "amort_years": 30, "io_years": value}])


class PerLoanIndependenceTests(unittest.TestCase):
    STACK = [
        {"name": "Senior", "amount": 4_000_000.0, "rate_pct": 6.5,
         "amort_years": 30, "io_years": 2},
        {"name": "Mezz", "amount": 1_000_000.0, "rate_pct": 11.0,
         "amort_years": 30, "io_years": None},
    ]

    def test_one_loan_can_be_io_while_another_amortizes(self):
        summary = ulm.summarize(self.STACK, HOLD, noi_year1=384_455.38)
        senior, mezz = summary["loans"]
        self.assertLess(senior["debt_service_series"][0],
                        senior["debt_service_series"][2])
        self.assertEqual(len(set(mezz["debt_service_series"])), 1)

    def test_the_combined_series_steps_up_once(self):
        summary = ulm.summarize(self.STACK, HOLD, noi_year1=384_455.38)
        self.assertEqual(len(set(summary["debt_service_series"])), 2)

    def test_the_combined_series_is_the_sum_of_the_loans(self):
        summary = ulm.summarize(self.STACK, HOLD, noi_year1=384_455.38)
        for t in range(HOLD):
            with self.subTest(year=t + 1):
                self.assertEqual(
                    summary["debt_service_series"][t],
                    sum(l["debt_service_series"][t] for l in summary["loans"]))

    def test_a_stack_with_no_io_sends_no_series_to_the_engine(self):
        plain = [dict(self.STACK[0], io_years=None), self.STACK[1]]
        summary = ulm.summarize(plain, HOLD, noi_year1=384_455.38)
        self.assertFalse(summary["has_io"])
        self.assertNotIn("debt_service_series", ulm.engine_debt(summary))

    def test_a_stack_with_io_does_send_one(self):
        summary = ulm.summarize(self.STACK, HOLD, noi_year1=384_455.38)
        self.assertIn("debt_service_series", ulm.engine_debt(summary))

    def test_a_plain_stack_totals_exactly_what_it_always_did(self):
        plain = [dict(self.STACK[0], io_years=None), self.STACK[1]]
        summary = ulm.summarize(plain, HOLD, noi_year1=384_455.38)
        self.assertEqual(summary["annual_debt_service"],
                         sum(ulm.annual_payment(l) for l in plain))


class DealAnalyzerIsUntouchedTests(unittest.TestCase):
    """The guarantee half. Absent must mean today, not nearly today."""

    GRID = [
        {"purchase_price": p, "ltv_pct": ltv, "interest_rate_pct": rate,
         "amort_years": amort, "hold_years": hold}
        for p in (1_000_000.0, 6_990_000.0)
        for ltv in (0.0, 65.0, 80.0)
        for rate in (0.0, 6.5, 9.25)
        for amort in (15, 30)
        for hold in (1, 5, 10)
    ]

    def _base(self, over):
        inputs = {
            "purchase_price": 6_990_000.0, "closing_costs_pct": 2.0,
            "ltv_pct": 65.0, "interest_rate_pct": 6.5, "amort_years": 30,
            "hold_years": 5, "exit_cap_pct": 5.75, "selling_costs_pct": 2.0,
            "noi_year1": 384_455.38, "noi_growth_pct": 3.0,
        }
        inputs.update(over)
        return inputs

    def _run(self, inputs):
        h = inputs["hold_years"]
        noi = [inputs["noi_year1"] * 1.03 ** t for t in range(h)]
        return m.analyze_noi_series(inputs, noi,
                                    inputs["noi_year1"] * 1.03 ** h)

    def test_absent_none_and_zero_all_agree_across_the_grid(self):
        added = {"debt_service_series", "io_years", "balloon_convention",
                 "io_covers_whole_hold", "dscr_by_year", "dscr_min",
                 "dscr_min_year", "dscr_io", "dscr_post_io"}
        for over in self.GRID:
            inputs = self._base(over)
            try:
                absent = self._run(dict(inputs))
            except m.ValidationError:
                continue        # combinations this engine already refused
            for spelling in (None, 0):
                with self.subTest(over=over, io_years=spelling):
                    got = self._run(dict(inputs, io_years=spelling))
                    for key in set(absent) - added:
                        if key == "inputs":
                            continue
                        self.assertEqual(got[key], absent[key],
                                         f"{key} moved when io_years={spelling!r}")

    def test_the_series_is_elementwise_the_old_scalar_repeated(self):
        for over in self.GRID:
            inputs = self._base(over)
            try:
                result = self._run(dict(inputs))
            except m.ValidationError:
                continue
            with self.subTest(over=over):
                loan = result["loan_amount"]
                scalar = (m.monthly_payment(loan,
                                            inputs["interest_rate_pct"] / 100.0,
                                            inputs["amort_years"]) * 12
                          if loan > 0 else 0.0)
                self.assertEqual(result["debt_service_series"],
                                 [scalar] * inputs["hold_years"])

    def test_the_series_helper_itself_repeats_the_scalar(self):
        for rate in (0.0, 0.065):
            for amort in (15, 30):
                with self.subTest(rate=rate, amort=amort):
                    self.assertEqual(
                        m.annual_debt_service_series(LOAN, rate, amort, HOLD),
                        [m.monthly_payment(LOAN, rate, amort) * 12] * HOLD)

    def test_remaining_balance_default_matches_the_original_formula(self):
        for months in (0, 12, 60, 360):
            for rate in (0.0, 0.065):
                with self.subTest(months=months, rate=rate):
                    pmt = m.monthly_payment(LOAN, rate, AMORT)
                    r = rate / 12.0
                    if r == 0:
                        expected = max(0.0, LOAN - pmt * months)
                    else:
                        grown = (1 + r) ** months
                        expected = max(0.0, LOAN * grown - pmt * ((grown - 1) / r))
                    self.assertEqual(
                        m.remaining_balance(LOAN, rate, AMORT, months), expected)

    def test_monthly_payment_is_unchanged_by_the_extraction(self):
        """_payment_over_months was factored out of monthly_payment."""
        for rate in (0.0, 0.045, 0.065, 0.115):
            for amort in (1, 15, 30, 40):
                with self.subTest(rate=rate, amort=amort):
                    r = rate / 12.0
                    n = amort * 12
                    expected = (LOAN / n if r == 0
                                else LOAN * r / (1 - (1 + r) ** -n))
                    self.assertEqual(m.monthly_payment(LOAN, rate, amort),
                                     expected)

    def test_deal_analyzer_never_sets_an_io_period(self):
        """Source-level, so the guarantee does not rest on a fixture.

        Deal Analyzer's route builds the engine input dict; if the string
        never appears there, no Deal Analyzer request can carry one.
        """
        for name in ("tools/deal_analyzer.py", "tools/quick_analyzer_math.py"):
            with self.subTest(module=name):
                source = (ROOT / name).read_text(encoding="utf-8")
                tree = ast.parse(source)
                literals = {node.value for node in ast.walk(tree)
                            if isinstance(node, ast.Constant)
                            and isinstance(node.value, str)}
                self.assertNotIn("io_years", literals,
                                 f"{name} references io_years")


class NoInteractionWithIncomeOrFeesTests(unittest.TestCase):
    """An IO period is a debt-service timing change and nothing else.

    Everything above the debt line -- income, NOI, every fee, the exit --
    must be bit-identical with and without one. Asserted directly rather
    than argued from the code's shape.
    """

    def setUp(self):
        self.plain = _analyze()
        self.io = _analyze(io_years=2)

    def test_nothing_above_the_debt_line_moves(self):
        # net_sale_proceeds is deliberately absent: it is the levered
        # figure, net of the balloon, and the balloon is exactly what an
        # IO period changes. Its unlevered twin is asserted instead.
        for key in ("gross_sale_price", "selling_costs", "closing_costs",
                    "capital_transaction_fee", "going_in_cap_rate",
                    "equity_invested", "loan_amount", "noi_exit_year",
                    "unlevered_irr"):
            with self.subTest(key=key):
                self.assertEqual(self.io[key], self.plain[key])

    def test_the_noi_series_is_identical(self):
        self.assertEqual([y["noi"] for y in self.io["years"]],
                         [y["noi"] for y in self.plain["years"]])

    def test_the_unlevered_cash_flows_are_identical(self):
        self.assertEqual(self.io["unlevered_cashflows"],
                         self.plain["unlevered_cashflows"])

    def test_a_capital_transaction_fee_is_unaffected(self):
        with_fee = _analyze(capital_transaction_fee_pct=1.5)
        with_fee_io = _analyze(capital_transaction_fee_pct=1.5, io_years=2)
        self.assertEqual(with_fee_io["capital_transaction_fee"],
                         with_fee["capital_transaction_fee"])

    def test_but_the_levered_side_does_move(self):
        """The control: a test that only asserted sameness could pass on a
        build where the IO period did nothing at all."""
        self.assertNotEqual(self.io["levered_irr"], self.plain["levered_irr"])
        self.assertNotEqual(self.io["loan_balance_at_exit"],
                            self.plain["loan_balance_at_exit"])


if __name__ == "__main__":
    unittest.main()
