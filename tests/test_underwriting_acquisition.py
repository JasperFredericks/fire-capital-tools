"""
Tests for itemized acquisition costs.

Two failure modes are worth locking down, because both are silent:

  * An acquisition line leaking into operating expenses. That depresses
    every year's NOI and is then capitalized into the exit price by the
    cap rate, so a one-time $150k cost becomes a multi-million valuation
    error that looks like a plausible number.

  * Itemized costs ADDING to the flat percentage instead of replacing it.
    Both describe the same money, so adding double-counts it -- and the
    result is still a plausible-looking figure.

Neither raises. Only a test catches them.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import underwriting_math as um  # noqa: E402

PRICE = 10_000_000.0
FLAT_PCT = 2.0
FLAT_TOTAL = 200_000.0


def _acq(amount, key="legal"):
    return {"category_key": key, "label": key, "annual_amount": amount,
            "is_included": True, "line_kind": um.ACQUISITION_COST_KIND}


def _op(amount, label="Payroll"):
    return {"category_key": "payroll", "label": label, "annual_amount": amount,
            "is_included": True, "line_kind": "operating"}


def _scenario(**over):
    base = dict(purchase_price=PRICE, closing_costs_pct=FLAT_PCT, ltv_pct=70.0,
                interest_rate_pct=6.0, amort_years=30, hold_years=5,
                exit_cap_pct=5.5, selling_costs_pct=2.0, vacancy_pct=5.0,
                concessions_pct=0.0, bad_debt_pct=0.0, other_income_annual=0.0,
                rent_growth_pct=3.0, expense_growth_pct=2.5)
    base.update(over)
    return base


def _units(n=50):
    return [{"unit": str(i), "unit_type": "2x2", "sqft": 900, "status": "Occupied",
             "in_place_rent": 1500, "market_rent": 1600} for i in range(n)]


class SeparationFromOperatingTests(unittest.TestCase):
    """An acquisition cost is a t=0 capital outlay, never an annual expense."""

    def test_acquisition_lines_excluded_from_operating_total(self):
        op_only = [_op(200_000)]
        mixed = op_only + [_acq(150_000)]
        self.assertEqual(um.total_operating_expenses(op_only),
                         um.total_operating_expenses(mixed))

    def test_is_included_alone_does_not_admit_them(self):
        """The guard must be line_kind, not is_included -- these lines are
        genuinely included, just in a different total."""
        line = _acq(150_000)
        self.assertTrue(line["is_included"])
        self.assertEqual(um.total_operating_expenses([line]), 0.0)

    def test_noi_series_unaffected_by_acquisition_lines(self):
        op = [_op(200_000)]
        a = um.project_noi_series(1_000_000, op, 5, 3.0, 2.5)
        b = um.project_noi_series(1_000_000, op + [_acq(150_000)], 5, 3.0, 2.5)
        self.assertEqual(a["noi_series"], b["noi_series"])
        self.assertEqual(a["noi_exit"], b["noi_exit"])

    def test_full_scenario_noi_unchanged(self):
        scen, units, op = _scenario(), _units(), [_op(200_000)]
        before = um.analyze_scenario(scen, units, op)
        after = um.analyze_scenario(scen, units, op + [_acq(150_000)])
        self.assertAlmostEqual(before["projection"]["noi_series"][0],
                               after["projection"]["noi_series"][0], places=6)
        self.assertAlmostEqual(before["operating_expenses_year1"],
                               after["operating_expenses_year1"], places=6)

    def test_classification_is_by_kind_not_label(self):
        """"Legal" is an operating expense on one deal and a closing cost on
        another; only line_kind can tell them apart."""
        self.assertFalse(um.is_acquisition_line(_op(1000, label="Legal")))
        self.assertTrue(um.is_acquisition_line(_acq(1000, key="legal")))


class OverrideNotAddTests(unittest.TestCase):
    def test_no_lines_falls_back_to_flat_percentage(self):
        a = um.acquisition_costs([], PRICE, FLAT_PCT)
        self.assertFalse(a["is_itemized"])
        self.assertEqual(a["effective_total"], FLAT_TOTAL)

    def test_itemizing_replaces_rather_than_adds(self):
        a = um.acquisition_costs([_acq(187_500)], PRICE, FLAT_PCT)
        self.assertTrue(a["is_itemized"])
        self.assertEqual(a["effective_total"], 187_500)
        self.assertNotEqual(a["effective_total"], FLAT_TOTAL + 187_500)

    def test_flat_total_still_reported_for_display(self):
        """The override must never be silent -- both numbers are returned so
        the UI can show what was replaced."""
        a = um.acquisition_costs([_acq(187_500)], PRICE, FLAT_PCT)
        self.assertEqual(a["flat_total"], FLAT_TOTAL)
        self.assertEqual(a["itemized_total"], 187_500)

    def test_effective_pct_matches_effective_dollars(self):
        """The engine is handed a percentage; it must reproduce exactly the
        dollars shown, or the page and the maths disagree."""
        a = um.acquisition_costs([_acq(187_500)], PRICE, FLAT_PCT)
        self.assertAlmostEqual(PRICE * a["effective_pct"] / 100.0,
                               a["effective_total"], places=6)

    def test_engine_receives_the_itemized_total(self):
        scen, units, op = _scenario(), _units(), [_op(200_000)]
        res = um.analyze_scenario(scen, units, op + [_acq(187_500)])
        self.assertAlmostEqual(res["returns"]["closing_costs"], 187_500, places=2)

    def test_excluded_acquisition_line_does_not_trigger_override(self):
        line = _acq(50_000)
        line["is_included"] = False
        a = um.acquisition_costs([line], PRICE, FLAT_PCT)
        self.assertFalse(a["is_itemized"])
        self.assertEqual(a["effective_total"], FLAT_TOTAL)

    def test_zero_purchase_price_does_not_divide_by_zero(self):
        a = um.acquisition_costs([_acq(5_000)], 0, FLAT_PCT)
        self.assertEqual(a["effective_pct"], 0.0)


class ShortfallWarningTests(unittest.TestCase):
    def test_no_warning_on_a_complete_itemization(self):
        self.assertFalse(um.acquisition_costs([_acq(187_500)], PRICE, FLAT_PCT)["shortfall_warning"])

    def test_warns_at_and_beyond_the_threshold(self):
        at = um.acquisition_costs([_acq(100_000)], PRICE, FLAT_PCT)
        self.assertAlmostEqual(at["shortfall_pct"], 50.0, places=6)
        self.assertTrue(at["shortfall_warning"])
        self.assertTrue(um.acquisition_costs([_acq(8_500)], PRICE, FLAT_PCT)["shortfall_warning"])

    def test_does_not_warn_just_below_the_threshold(self):
        self.assertFalse(um.acquisition_costs([_acq(101_000)], PRICE, FLAT_PCT)["shortfall_warning"])

    def test_warning_does_not_alter_the_figure(self):
        """The entered data still wins; the warning is a prompt to check."""
        a = um.acquisition_costs([_acq(8_500)], PRICE, FLAT_PCT)
        self.assertTrue(a["shortfall_warning"])
        self.assertEqual(a["effective_total"], 8_500)

    def test_no_warning_when_there_is_no_flat_baseline(self):
        a = um.acquisition_costs([_acq(8_500)], PRICE, 0.0)
        self.assertIsNone(a["shortfall_pct"])
        self.assertFalse(a["shortfall_warning"])


class SharedEngineUntouchedTests(unittest.TestCase):
    def test_default_path_is_byte_identical_to_before(self):
        """With no acquisition lines, the engine must be given exactly the
        scenario's own closing_costs_pct -- proving this feature is inert
        for every existing scenario."""
        scen = _scenario()
        res = um.analyze_scenario(scen, _units(), [_op(200_000)])
        self.assertAlmostEqual(res["returns"]["closing_costs"], FLAT_TOTAL, places=6)
        self.assertAlmostEqual(res["returns"]["inputs"]["closing_costs_pct"], FLAT_PCT, places=9)


if __name__ == "__main__":
    unittest.main()


class AcquisitionFeeTests(unittest.TestCase):
    """The GP's fee for sourcing and closing the deal.

    Distinct in kind from closing costs, so it ADDS rather than
    participating in the itemize-vs-percentage override. Getting that
    backwards would either double-count the fee or silently drop a
    six-figure use of funds.
    """

    def test_absent_fee_changes_nothing(self):
        """Zero regression for every scenario saved before this existed."""
        scen, units, op = _scenario(), _units(), [_op(200_000)]
        before = um.analyze_scenario(scen, units, op)
        after = um.analyze_scenario(dict(scen, acquisition_fee_pct=None), units, op)
        self.assertEqual(before["returns"]["equity_invested"],
                         after["returns"]["equity_invested"])
        self.assertEqual(before["returns"]["closing_costs"],
                         after["returns"]["closing_costs"])
        self.assertEqual(before["returns"]["levered_irr"],
                         after["returns"]["levered_irr"])

    def test_zero_fee_is_the_same_as_absent(self):
        scen, units, op = _scenario(), _units(), [_op(200_000)]
        a = um.analyze_scenario(scen, units, op)
        b = um.analyze_scenario(dict(scen, acquisition_fee_pct=0.0), units, op)
        self.assertEqual(a["returns"]["equity_invested"], b["returns"]["equity_invested"])

    def test_equity_increases_by_exactly_the_fee(self):
        scen, units, op = _scenario(), _units(), [_op(200_000)]
        base = um.analyze_scenario(scen, units, op)
        withfee = um.analyze_scenario(dict(scen, acquisition_fee_pct=2.5), units, op)
        delta = (withfee["returns"]["equity_invested"]
                 - base["returns"]["equity_invested"])
        self.assertAlmostEqual(delta, PRICE * 0.025, places=6)

    def test_fee_adds_to_the_flat_percentage(self):
        a = um.acquisition_costs([], PRICE, FLAT_PCT, 2.5)
        self.assertAlmostEqual(a["acquisition_fee_total"], 250_000, places=6)
        self.assertAlmostEqual(a["costs_before_fee"], FLAT_TOTAL, places=6)
        self.assertAlmostEqual(a["effective_total"], FLAT_TOTAL + 250_000, places=6)

    def test_fee_adds_on_top_of_itemized_costs(self):
        """The override is between itemized and flat only. The fee is in
        neither, so it survives itemization."""
        a = um.acquisition_costs([_acq(150_000)], PRICE, FLAT_PCT, 2.5)
        self.assertTrue(a["is_itemized"])
        self.assertAlmostEqual(a["costs_before_fee"], 150_000, places=6)
        self.assertAlmostEqual(a["effective_total"], 150_000 + 250_000, places=6)

    def test_fee_is_excluded_from_the_shortfall_comparison(self):
        """Shortfall compares the two descriptions of the same money. Folding
        the fee in would make a complete itemization look like a shortfall."""
        a = um.acquisition_costs([_acq(200_000)], PRICE, FLAT_PCT, 3.5)
        self.assertAlmostEqual(a["shortfall_pct"], 0.0, places=6)
        self.assertFalse(a["shortfall_warning"])

    def test_fee_reaches_the_engine_as_part_of_closing_costs(self):
        scen, units, op = _scenario(), _units(), [_op(200_000)]
        res = um.analyze_scenario(dict(scen, acquisition_fee_pct=2.5), units, op)
        self.assertAlmostEqual(res["returns"]["closing_costs"],
                               FLAT_TOTAL + 250_000, places=2)

    def test_fee_does_not_touch_noi_or_operating_expenses(self):
        scen, units, op = _scenario(), _units(), [_op(200_000)]
        a = um.analyze_scenario(scen, units, op)
        b = um.analyze_scenario(dict(scen, acquisition_fee_pct=3.5), units, op)
        self.assertEqual(a["projection"]["noi_series"], b["projection"]["noi_series"])
        self.assertEqual(a["operating_expenses_year1"], b["operating_expenses_year1"])

    def test_fee_lowers_irr(self):
        """More cash in at close for the same cash out must reduce return."""
        scen, units, op = _scenario(), _units(), [_op(200_000)]
        a = um.analyze_scenario(scen, units, op)
        b = um.analyze_scenario(dict(scen, acquisition_fee_pct=2.5), units, op)
        self.assertLess(b["returns"]["levered_irr"], a["returns"]["levered_irr"])

    def test_effective_pct_still_reproduces_effective_dollars(self):
        a = um.acquisition_costs([_acq(150_000)], PRICE, FLAT_PCT, 2.5)
        self.assertAlmostEqual(PRICE * a["effective_pct"] / 100.0,
                               a["effective_total"], places=6)
