"""
Tests for the capital transaction fee and the management fee.

Both are property-level costs: deducted inside Underwriting, before the
cash-flow vector leaves analyze_scenario, so Investor Report allocates
post-fee cash and never sees the gross.

The load-bearing test is the zero case. Two fee fields that are absent or
zero must leave every existing scenario arithmetically untouched --
otherwise this phase silently repriced every deal in the system.
"""

import json
import sys
import unittest
from decimal import Decimal, getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import deal_analyzer_math as dam
from tools import underwriting_math as um
from tools import waterfall_math as wm

getcontext().prec = 40

FIXTURE = Path(__file__).parent / "fixtures" / "eagle_rock_scenario4.json"

EAGLE_ROCK = {
    "noi_year1": 384455.38,
    "opex_year1": 839216.14,
    "egi_year1": 1223671.52,
    "equity": 2586300.00,
    "levered_irr_pct": 8.11,
    "dscr": 1.12,
}


def load_fixture():
    d = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return d["scenario"], d["units"], d["expenses"]


class TestZeroFeesChangeNothing(unittest.TestCase):
    """Absent, None and 0.0 must all be the no-fee case, and all three must
    agree exactly with the pre-fee result."""

    def setUp(self):
        self.scenario, self.units, self.expenses = load_fixture()

    def test_fixture_has_no_fee_fields_at_all(self):
        """The fixture predates this phase, which is what makes it a real
        test of the absent case."""
        self.assertNotIn("management_fee_pct", self.scenario)
        self.assertNotIn("capital_transaction_fee_pct", self.scenario)

    def test_eagle_rock_unchanged_with_fees_absent(self):
        r = um.analyze_scenario(self.scenario, self.units, self.expenses)
        self.assertAlmostEqual(r["projection"]["years"][0]["noi"],
                               EAGLE_ROCK["noi_year1"], places=2)
        self.assertAlmostEqual(r["operating_expenses_year1"],
                               EAGLE_ROCK["opex_year1"], places=2)
        self.assertAlmostEqual(r["returns"]["equity_invested"],
                               EAGLE_ROCK["equity"], places=2)
        self.assertAlmostEqual(r["returns"]["levered_irr"] * 100,
                               EAGLE_ROCK["levered_irr_pct"], places=2)
        self.assertAlmostEqual(r["returns"]["dscr"], EAGLE_ROCK["dscr"], places=2)

    def test_absent_none_and_zero_are_identical(self):
        base = um.analyze_scenario(self.scenario, self.units, self.expenses)
        for value in (None, 0.0, 0):
            with self.subTest(value=value):
                s = dict(self.scenario)
                s["management_fee_pct"] = value
                s["capital_transaction_fee_pct"] = value
                r = um.analyze_scenario(s, self.units, self.expenses)
                self.assertEqual(
                    json.dumps(r["returns"], sort_keys=True, default=str),
                    json.dumps(base["returns"], sort_keys=True, default=str))
                self.assertEqual(r["projection"]["noi_series"],
                                 base["projection"]["noi_series"])

    def test_zero_fee_adds_no_management_expense(self):
        r = um.analyze_scenario(self.scenario, self.units, self.expenses)
        self.assertEqual(r["management_fee_year1"], 0.0)
        self.assertAlmostEqual(r["operating_expenses_year1"],
                               r["expense_lines_year1"], places=9)

    def test_engine_key_absent_is_zero_fee(self):
        """Deal Analyzer never sets capital_transaction_fee_pct."""
        inputs = {
            "purchase_price": 6_990_000.0, "closing_costs_pct": 2.0, "ltv_pct": 65.0,
            "interest_rate_pct": 6.5, "amort_years": 30, "hold_years": 5,
            "exit_cap_pct": 6.0, "selling_costs_pct": 2.0,
            "noi_year1": 1.0, "noi_growth_pct": 0.0,
        }
        series = [384455.38, 400185.0, 416492.0, 433395.0, 450915.0]
        without = dam.analyze_noi_series(inputs, series, 469_000.0)
        with_zero = dam.analyze_noi_series(
            dict(inputs, capital_transaction_fee_pct=0.0), series, 469_000.0)
        self.assertEqual(without["net_sale_proceeds"], with_zero["net_sale_proceeds"])
        self.assertEqual(without["capital_transaction_fee"], 0.0)


class TestManagementFee(unittest.TestCase):
    """3% of EGI, charged annually."""

    def setUp(self):
        self.scenario, self.units, self.expenses = load_fixture()
        self.scenario = dict(self.scenario, management_fee_pct=3.0)
        self.result = um.analyze_scenario(self.scenario, self.units, self.expenses)

    def test_year1_fee_is_three_percent_of_egi(self):
        expected = float(Decimal(str(EAGLE_ROCK["egi_year1"])) * Decimal("0.03"))
        self.assertAlmostEqual(self.result["management_fee_year1"], expected, places=6)
        self.assertAlmostEqual(self.result["management_fee_year1"], 36710.1456, places=4)

    def test_year1_noi_drops_by_exactly_the_fee(self):
        self.assertAlmostEqual(self.result["projection"]["years"][0]["noi"],
                               347745.2344, places=4)
        base = um.analyze_scenario(
            dict(self.scenario, management_fee_pct=0.0), self.units, self.expenses)
        self.assertAlmostEqual(
            base["projection"]["years"][0]["noi"] - self.result["projection"]["years"][0]["noi"],
            self.result["management_fee_year1"], places=6)

    def test_fee_grows_with_income_without_its_own_rate(self):
        years = self.result["projection"]["years"]
        rg = 1 + float(self.scenario["rent_growth_pct"]) / 100.0
        self.assertAlmostEqual(years[1]["management_fee"] / years[0]["management_fee"],
                               rg, places=9)

    def test_fee_is_included_in_the_operating_expense_headline(self):
        self.assertAlmostEqual(
            self.result["operating_expenses_year1"],
            self.result["expense_lines_year1"] + self.result["management_fee_year1"],
            places=6)

    def test_headline_expenses_are_what_is_actually_subtracted(self):
        y1 = self.result["projection"]["years"][0]
        self.assertAlmostEqual(y1["income"] - self.result["operating_expenses_year1"],
                               y1["noi"], places=6)

    def test_fee_reduces_the_exit_value_because_exit_capitalizes_noi(self):
        """Consequence of modelling it as an operating expense, asserted
        so the behaviour is deliberate rather than incidental."""
        base = um.analyze_scenario(
            dict(self.scenario, management_fee_pct=0.0), self.units, self.expenses)
        self.assertLess(self.result["returns"]["gross_sale_price"],
                        base["returns"]["gross_sale_price"])

    def test_fee_lowers_dscr_and_irr(self):
        base = um.analyze_scenario(
            dict(self.scenario, management_fee_pct=0.0), self.units, self.expenses)
        self.assertLess(self.result["returns"]["dscr"], base["returns"]["dscr"])
        self.assertLess(self.result["returns"]["levered_irr"],
                        base["returns"]["levered_irr"])


class TestCapitalTransactionFee(unittest.TestCase):
    """1% of the gross sale price, charged at exit only."""

    def setUp(self):
        self.scenario, self.units, self.expenses = load_fixture()
        self.base = um.analyze_scenario(self.scenario, self.units, self.expenses)
        self.result = um.analyze_scenario(
            dict(self.scenario, capital_transaction_fee_pct=1.0),
            self.units, self.expenses)

    def test_fee_is_one_percent_of_gross_sale(self):
        gross = self.result["returns"]["gross_sale_price"]
        self.assertAlmostEqual(self.result["returns"]["capital_transaction_fee"],
                               gross * 0.01, places=6)

    def test_net_sale_proceeds_drop_by_exactly_the_fee(self):
        self.assertAlmostEqual(
            self.base["returns"]["net_sale_proceeds"]
            - self.result["returns"]["net_sale_proceeds"],
            self.result["returns"]["capital_transaction_fee"], places=6)

    def test_operating_cash_flows_are_untouched(self):
        """An exit fee must not reach the operating years."""
        self.assertEqual([y["cash_flow"] for y in self.base["returns"]["years"]],
                         [y["cash_flow"] for y in self.result["returns"]["years"]])

    def test_noi_and_gross_sale_are_untouched(self):
        self.assertEqual(self.base["projection"]["noi_series"],
                         self.result["projection"]["noi_series"])
        self.assertAlmostEqual(self.base["returns"]["gross_sale_price"],
                               self.result["returns"]["gross_sale_price"], places=9)

    def test_total_distributions_drop_by_the_fee(self):
        self.assertAlmostEqual(
            self.base["returns"]["total_distributions"]
            - self.result["returns"]["total_distributions"],
            self.result["returns"]["capital_transaction_fee"], places=6)

    def test_unlevered_exit_also_bears_the_fee(self):
        self.assertLess(self.result["returns"]["unlevered_irr"],
                        self.base["returns"]["unlevered_irr"])


class TestWaterfallSeesPostFeeCash(unittest.TestCase):
    """The coordination requirement: Investor Report must allocate the
    post-fee vector, and its invariants must still hold.

    They need no rewrite. Both invariant 9 and invariant 10 compare the
    waterfall against the SAME returns dict that periods_from_underwriting
    read its cash from -- so when fees move that dict, both sides move
    together. Asserted here rather than assumed.
    """

    def setUp(self):
        self.scenario, self.units, self.expenses = load_fixture()
        self.scenario = dict(self.scenario,
                             management_fee_pct=3.0,
                             capital_transaction_fee_pct=1.0)
        self.result = um.analyze_scenario(self.scenario, self.units, self.expenses)
        self.returns = self.result["returns"]

    def _run(self):
        contributions = [{"investor_id": 1, "amount": self.returns["equity_invested"],
                          "investor_class": "LP"}]
        tiers = [{"tier_type": wm.TIER_RETURN_OF_CAPITAL, "sort_order": 0,
                  "lp_share_pct": 100.0, "gp_share_pct": 0.0, "hurdle_rate_pct": None},
                 {"tier_type": wm.TIER_PROMOTE, "sort_order": 1,
                  "lp_share_pct": 100.0, "gp_share_pct": 0.0, "hurdle_rate_pct": None}]
        return wm.run_waterfall(
            contributions, wm.periods_from_underwriting(self.returns),
            {"pref_rate_pct": 0.0, "pref_convention": wm.PREF_CONVENTION_ACCRUAL,
             "tiers": tiers})

    def test_periods_carry_the_post_fee_cash(self):
        periods = wm.periods_from_underwriting(self.returns)
        self.assertAlmostEqual(periods[0]["operating_cash"],
                               self.returns["years"][0]["cash_flow"], places=9)
        self.assertAlmostEqual(periods[-1]["sale_proceeds"],
                               self.returns["net_sale_proceeds"], places=9)

    def test_invariants_hold_unchanged(self):
        result = self._run()
        checks = wm.check_invariants(result)
        self.assertTrue(all(c["passed"] is not False for c in checks),
                        [c for c in checks if c["passed"] is False])

    def test_invariant_9_matches_the_post_fee_total(self):
        result = self._run()
        checks = wm.verify_against_source(
            result, self.returns["total_distributions"],
            source_levered_cashflows=self.returns.get("levered_cashflows"))
        nine = [c for c in checks if c["n"] == 9]
        self.assertTrue(nine and all(c["passed"] for c in nine), checks)

    def test_invariant_10_reproduces_the_post_fee_property_flows(self):
        result = self._run()
        checks = wm.verify_against_source(
            result, self.returns["total_distributions"],
            source_levered_irr=self.returns["levered_irr"],
            source_levered_cashflows=self.returns.get("levered_cashflows"))
        ten = [c for c in checks if c["n"] == 10]
        self.assertTrue(ten, "invariant 10 did not run")
        self.assertTrue(all(c["passed"] for c in ten), ten)

    def test_waterfall_total_is_below_the_pre_fee_total(self):
        """Sanity: the fees really did remove cash before allocation."""
        pre = um.analyze_scenario(load_fixture()[0], self.units, self.expenses)
        self.assertLess(self.returns["total_distributions"],
                        pre["returns"]["total_distributions"])


if __name__ == "__main__":
    unittest.main()
