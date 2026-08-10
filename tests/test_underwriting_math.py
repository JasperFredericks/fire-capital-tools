"""
Unit tests for tools/underwriting_math.py and the expense category rollup.

Weighted toward where Phase 1 said the silent-wrong-number risk actually
concentrates, in that order:

  1. Expense aggregation against real T12s -- the naive-sum trap must not
     reproduce. This is the one that would turn a bad deal into a good one
     with nothing on screen to show for it.
  2. Non-operating exclusion -- debt service counted as opex would be
     charged twice (once here, once by the loan).
  3. EGI sign correctness -- every element below GPR is a subtraction, and
     a flipped sign is invisible in the output.
  4. Grid-to-headline consistency -- the base-case cell must equal the
     headline exactly, or the table is quietly computed with different math.
  5. Rent roll aggregation and its edge cases.

Assertions restate the arithmetic independently wherever practical, the
same discipline used for deal_analyzer_math and site_dd_checklist.
"""

import unittest
from pathlib import Path

from tools import underwriting_math as um
from tools.deal_analyzer_math import analyze as da_analyze
from tools.scorecard_pro.kpis import KPICalculator
from tools.scorecard_pro.parsing import PnLParser

REAL_T12 = {
    "Eagle Rock": r"C:/Users/jaspe/Downloads/test-files-2/Eagle Rock T12 May 2026 Profit and Loss.xlsx",
    "Canyon": r"C:/Users/jaspe/Downloads/test-files-2/Canyon T12 May 2026 Profit and Loss.xlsx",
    "OXPT": r"C:/Users/jaspe/Downloads/test-files-2/OXPT T12 May 2026 Profit and Loss.xlsx",
    "Jackson": r"C:/Users/jaspe/Downloads/Test_5/Jackson T12 Aug 2025-Jul 2026.xlsx",
}

BASE_SCENARIO = {
    "purchase_price": 5_000_000.0, "closing_costs_pct": 2.0, "ltv_pct": 65.0,
    "interest_rate_pct": 6.25, "amort_years": 30, "hold_years": 5,
    "exit_cap_pct": 6.0, "selling_costs_pct": 2.0,
    "rent_growth_pct": 3.0, "expense_growth_pct": 2.5,
    "vacancy_pct": 5.0, "concessions_pct": 1.0, "bad_debt_pct": 0.5,
    "other_income_annual": 60_000.0,
}


def units(n_occupied=10, n_vacant=2, market=1500.0, in_place=1400.0):
    out = [{"unit": f"{i}", "unit_type": "1x1", "sqft": 700,
            "status": "Occupied", "market_rent": market, "in_place_rent": in_place}
           for i in range(n_occupied)]
    out += [{"unit": f"V{i}", "unit_type": "1x1", "sqft": 700,
             "status": "Vacant", "market_rent": market, "in_place_rent": 0.0}
            for i in range(n_vacant)]
    return out


def expenses(total=200_000.0, growth=None):
    return [{"category_key": "opex", "label": "Operating", "annual_amount": total,
             "growth_pct": growth, "is_included": True}]


def load(name):
    path = Path(REAL_T12[name])
    if not path.exists():
        raise unittest.SkipTest(f"real T12 not available: {path}")
    p = PnLParser(str(path))
    p.parse()
    return p.get_data()


# ── 1. Expense aggregation on real T12s ──────────────────────────────────

class TestExpenseAggregationRealT12(unittest.TestCase):
    """The Phase-1 trap: parents and children both present, so summing every
    account double-counts. Measured at ~8x on Eagle Rock's 6xxx+7xxx."""

    def _naive_sum(self, accounts):
        return sum(sum(v or 0.0 for v in a["data"].values())
                   for c, a in accounts.items() if str(c)[:1] in "67")

    def test_naive_sum_trap_does_not_reproduce(self):
        for name in ("Eagle Rock", "Canyon", "OXPT"):
            with self.subTest(property=name):
                d = load(name)
                br = KPICalculator(d).category_breakdown()
                naive = self._naive_sum(d["accounts"])
                leaf = br["operating_total"] + br["excluded_total"]
                self.assertLess(leaf, naive,
                                "leaf total must be below the double-counted naive sum")
                self.assertGreater(naive / leaf, 1.5,
                                   "tree format must show real double-counting in the naive sum")
                self.assertGreater(br["operating_total"], 0)

    def test_no_account_counted_twice(self):
        """Every leaf appears exactly once across all categories."""
        for name in ("Eagle Rock", "Canyon", "OXPT", "Jackson"):
            with self.subTest(property=name):
                br = KPICalculator(load(name)).category_breakdown()
                codes = [l["code"] for l in br["lines"]]
                self.assertEqual(len(codes), len(set(codes)))
                from_cats = [l["code"] for c in br["categories"] for l in c["lines"]]
                self.assertEqual(sorted(codes), sorted(from_cats))

    def test_no_leaf_is_also_a_parent(self):
        """A code that has children must never be summed as a line item."""
        for name in ("Eagle Rock", "Canyon", "OXPT"):
            with self.subTest(property=name):
                d = load(name)
                k = KPICalculator(d)
                leaf_codes = {l["code"] for l in k.category_breakdown()["lines"]}
                ordered = list(d["accounts"].items())
                depths = [a.get("depth") for _, a in ordered]
                for i, (code, acc) in enumerate(ordered):
                    nxt = next((depths[j] for j in range(i + 1, len(ordered))
                                if depths[j] is not None), None)
                    if acc.get("depth") is not None and nxt is not None and nxt > acc["depth"]:
                        self.assertNotIn(code, leaf_codes,
                                         f"{code} has children but was summed as a leaf")

    def test_category_total_equals_sum_of_its_lines(self):
        for name in ("Eagle Rock", "Canyon", "OXPT", "Jackson"):
            with self.subTest(property=name):
                for c in KPICalculator(load(name)).category_breakdown()["categories"]:
                    self.assertAlmostEqual(
                        c["leaf_total"], sum(l["annual_total"] for l in c["lines"]), places=6)

    def test_flat_format_has_no_rollup_rows(self):
        """Jackson's cash-flow export is flat -- no depth, so no parents, and
        naive == leaf is correct rather than a failure to avoid the trap."""
        d = load("Jackson")
        br = KPICalculator(d).category_breakdown()
        naive = self._naive_sum(d["accounts"])
        leaf = br["operating_total"] + br["excluded_total"]
        self.assertAlmostEqual(naive, leaf, places=2)
        self.assertTrue(all(l["depth"] is None for l in br["lines"]))

    def test_discrepancies_reported_not_silently_resolved(self):
        """Eagle Rock's parents and children genuinely disagree; the rollup
        must surface that rather than pick one quietly."""
        br = KPICalculator(load("Eagle Rock")).category_breakdown()
        self.assertTrue(br["discrepancies"], "known real discrepancy not reported")
        for d in br["discrepancies"]:
            self.assertAlmostEqual(d["difference"], d["parent_total"] - d["leaf_total"], places=6)


# ── 2. Non-operating exclusion ───────────────────────────────────────────

class TestNonOperatingExclusion(unittest.TestCase):
    def test_debt_service_and_capex_excluded_by_default(self):
        br = KPICalculator(load("Eagle Rock")).category_breakdown()
        excluded = {l["name"].lower(): l for l in br["lines"] if not l["is_included_default"]}
        self.assertTrue(any("mortgage interest" in n for n in excluded))
        self.assertTrue(any("interior rehab" in n for n in excluded))
        self.assertTrue(any("replacement" in n for n in excluded))
        for name, l in excluded.items():
            self.assertIn(l["line_kind"], ("non_operating", "capex"))

    def test_excluded_lines_are_visible_not_dropped(self):
        br = KPICalculator(load("Eagle Rock")).category_breakdown()
        self.assertGreater(br["excluded_total"], 0)
        self.assertTrue(any(not l["is_included_default"] for l in br["lines"]))

    def test_excluded_lines_contribute_nothing_to_opex(self):
        lines = [{"annual_amount": 100_000.0, "is_included": True},
                 {"annual_amount": 500_000.0, "is_included": False}]
        self.assertAlmostEqual(um.total_operating_expenses(lines), 100_000.0)

    def test_noi_does_not_collapse_when_debt_service_present(self):
        """Including debt service as opex would charge it twice and crush
        NOI. With the default exclusion it must not."""
        br = KPICalculator(load("Eagle Rock")).category_breakdown()
        lines = [{"annual_amount": l["annual_total"], "growth_pct": None,
                  "is_included": l["is_included_default"]} for l in br["lines"]]
        egi = 1_200_000.0
        proj = um.project_noi_series(egi, lines, 5, 3.0, 2.5)
        self.assertGreater(proj["noi_series"][0], 0, "NOI collapsed to zero or below")
        naive_lines = [dict(l, is_included=True) for l in lines]
        naive_noi = um.project_noi_series(egi, naive_lines, 5, 3.0, 2.5)["noi_series"][0]
        self.assertGreater(proj["noi_series"][0], naive_noi)


# ── 3. EGI build-up signs ────────────────────────────────────────────────

class TestEGISigns(unittest.TestCase):
    def test_gpr_is_market_rent_annualized(self):
        e = um.build_egi(units(10, 2, market=1500.0, in_place=1400.0),
                         {"vacancy_pct": 0, "concessions_pct": 0, "bad_debt_pct": 0})
        self.assertAlmostEqual(e["gross_potential_rent"], 12 * 1500.0 * 12)

    def test_each_deduction_reduces_egi(self):
        base = {"vacancy_pct": 0, "concessions_pct": 0, "bad_debt_pct": 0}
        u = units(10, 2)
        e0 = um.build_egi(u, base)["effective_gross_income"]
        for field in ("vacancy_pct", "concessions_pct", "bad_debt_pct"):
            with self.subTest(field=field):
                e1 = um.build_egi(u, dict(base, **{field: 5.0}))["effective_gross_income"]
                self.assertLess(e1, e0, f"{field} did not reduce EGI -- sign flip")

    def test_loss_to_lease_reduces_egi_and_is_occupied_only(self):
        base = {"vacancy_pct": 0, "concessions_pct": 0, "bad_debt_pct": 0}
        at_market = um.build_egi(units(10, 2, market=1500.0, in_place=1500.0), base)
        below = um.build_egi(units(10, 2, market=1500.0, in_place=1400.0), base)
        self.assertAlmostEqual(at_market["loss_to_lease"], 0.0)
        self.assertAlmostEqual(below["loss_to_lease"], 10 * 100.0 * 12)  # 10 occupied only
        self.assertLess(below["effective_gross_income"], at_market["effective_gross_income"])

    def test_in_place_above_market_is_upside_not_floored(self):
        e = um.build_egi(units(10, 0, market=1400.0, in_place=1500.0),
                         {"vacancy_pct": 0, "concessions_pct": 0, "bad_debt_pct": 0})
        self.assertLess(e["loss_to_lease"], 0)

    def test_other_income_adds(self):
        base = {"vacancy_pct": 0, "concessions_pct": 0, "bad_debt_pct": 0}
        a = um.build_egi(units(), base)["effective_gross_income"]
        b = um.build_egi(units(), dict(base, other_income_annual=50_000.0))["effective_gross_income"]
        self.assertAlmostEqual(b - a, 50_000.0)

    def test_full_buildup_arithmetic_restated(self):
        u = units(10, 2, market=1500.0, in_place=1400.0)
        a = {"vacancy_pct": 5.0, "concessions_pct": 1.0, "bad_debt_pct": 0.5,
             "other_income_annual": 60_000.0}
        e = um.build_egi(u, a)
        gpr = 12 * 1500.0 * 12
        expected = (gpr - (10 * 100.0 * 12) - gpr * 0.05 - gpr * 0.01
                    - gpr * 0.005 + 60_000.0)
        self.assertAlmostEqual(e["effective_gross_income"], expected, places=6)


# ── 4. Grid-to-headline consistency ──────────────────────────────────────

class TestGridConsistency(unittest.TestCase):
    def setUp(self):
        self.u, self.e = units(40, 4), expenses(250_000.0)
        self.result = um.analyze_scenario(BASE_SCENARIO, self.u, self.e)

    def test_base_cell_equals_headline_irr(self):
        grid = um.sensitivity_grid(BASE_SCENARIO, self.u, self.e)
        base = [c for r in grid["rows"] for c in r["cells"] if c["is_base"]]
        self.assertEqual(len(base), 1, "expected exactly one base-case cell")
        self.assertAlmostEqual(base[0]["value"], self.result["returns"]["levered_irr"], places=12)

    def test_base_cell_equals_headline_on_price_grid(self):
        grid = um.sensitivity_grid(BASE_SCENARIO, self.u, self.e, variable="price")
        base = [c for r in grid["rows"] for c in r["cells"] if c["is_base"]]
        self.assertEqual(len(base), 1)
        self.assertAlmostEqual(base[0]["value"], self.result["returns"]["levered_irr"], places=12)

    def test_equity_multiple_metric_also_matches(self):
        grid = um.sensitivity_grid(BASE_SCENARIO, self.u, self.e, metric="equity_multiple")
        base = [c for r in grid["rows"] for c in r["cells"] if c["is_base"]][0]
        self.assertAlmostEqual(base["value"], self.result["returns"]["equity_multiple"], places=12)

    def test_grid_shape_and_monotonicity(self):
        grid = um.sensitivity_grid(BASE_SCENARIO, self.u, self.e)
        self.assertEqual(len(grid["rows"]), um.EXIT_CAP_STEPS)
        self.assertTrue(all(len(r["cells"]) == um.RENT_GROWTH_STEPS for r in grid["rows"]))
        mid = grid["rows"][len(grid["rows"]) // 2]["cells"]
        vals = [c["value"] for c in mid if c["value"] is not None]
        self.assertEqual(vals, sorted(vals), "higher rent growth must not lower IRR")
        first = [r["cells"][0]["value"] for r in grid["rows"] if r["cells"][0]["value"] is not None]
        self.assertEqual(first, sorted(first, reverse=True), "higher exit cap must not raise IRR")

    def test_no_cell_is_nan(self):
        for r in um.sensitivity_grid(BASE_SCENARIO, self.u, self.e)["rows"]:
            for c in r["cells"]:
                if c["value"] is not None:
                    self.assertEqual(c["value"], c["value"])
                else:
                    self.assertTrue(c["reason"])


# ── 5. Rent roll aggregation ─────────────────────────────────────────────

class TestUnitMix(unittest.TestCase):
    def test_counts_and_averages(self):
        u = [{"unit_type": "1x1", "sqft": 700, "in_place_rent": 1400, "market_rent": 1500},
             {"unit_type": "1x1", "sqft": 750, "in_place_rent": 1450, "market_rent": 1550},
             {"unit_type": "2x2", "sqft": 1000, "in_place_rent": 1900, "market_rent": 2000}]
        mix = {m["unit_type"]: m for m in um.unit_mix(u)}
        self.assertEqual(mix["1x1"]["count"], 2)
        self.assertAlmostEqual(mix["1x1"]["avg_sqft"], 725.0)
        self.assertAlmostEqual(mix["1x1"]["avg_in_place_rent"], 1425.0)
        self.assertEqual(mix["2x2"]["count"], 1)

    def test_missing_values_skipped_not_zeroed(self):
        u = [{"unit_type": "1x1", "sqft": 700, "in_place_rent": 1400, "market_rent": 1500},
             {"unit_type": "1x1", "sqft": None, "in_place_rent": None, "market_rent": 1500}]
        m = um.unit_mix(u)[0]
        self.assertAlmostEqual(m["avg_sqft"], 700.0)
        self.assertAlmostEqual(m["avg_in_place_rent"], 1400.0)
        self.assertEqual(m["count"], 2)

    def test_all_missing_gives_none_not_zero(self):
        m = um.unit_mix([{"unit_type": "1x1"}])[0]
        self.assertIsNone(m["avg_sqft"])
        self.assertIsNone(m["avg_in_place_rent"])

    def test_untyped_units_bucketed_not_dropped(self):
        self.assertEqual(um.unit_mix([{"in_place_rent": 1000}])[0]["unit_type"], "Unspecified")

    def test_unit_with_no_market_rent_still_contributes_gpr(self):
        e = um.build_egi([{"status": "Occupied", "in_place_rent": 1200, "market_rent": None}],
                         {"vacancy_pct": 0, "concessions_pct": 0, "bad_debt_pct": 0})
        self.assertAlmostEqual(e["gross_potential_rent"], 1200 * 12)
        self.assertAlmostEqual(e["loss_to_lease"], 0.0)

    def test_empty_rent_roll_is_zero_not_error(self):
        e = um.build_egi([], {"vacancy_pct": 5.0})
        self.assertEqual(e["unit_count"], 0)
        self.assertAlmostEqual(e["effective_gross_income"], 0.0)


# ── Shared-engine equivalence ────────────────────────────────────────────

class TestSharedEngine(unittest.TestCase):
    def test_flat_noi_series_matches_deal_analyzer(self):
        """A scenario with zero growth and one expense line must produce the
        same returns as Deal Analyzer fed the equivalent single NOI."""
        u = units(40, 0, market=1000.0, in_place=1000.0)
        e = expenses(100_000.0, growth=0.0)
        s = dict(BASE_SCENARIO, rent_growth_pct=0.0, expense_growth_pct=0.0,
                 vacancy_pct=0.0, concessions_pct=0.0, bad_debt_pct=0.0,
                 other_income_annual=0.0)
        mine = um.analyze_scenario(s, u, e)
        noi1 = mine["projection"]["noi_series"][0]
        theirs = da_analyze({
            "purchase_price": s["purchase_price"], "closing_costs_pct": s["closing_costs_pct"],
            "ltv_pct": s["ltv_pct"], "interest_rate_pct": s["interest_rate_pct"],
            "amort_years": s["amort_years"], "noi_year1": noi1, "noi_growth_pct": 0.0,
            "hold_years": s["hold_years"], "exit_cap_pct": s["exit_cap_pct"],
            "selling_costs_pct": s["selling_costs_pct"]})
        for k in ("levered_irr", "unlevered_irr", "equity_multiple", "dscr",
                  "cash_on_cash", "going_in_cap_rate"):
            with self.subTest(metric=k):
                self.assertAlmostEqual(mine["returns"][k], theirs[k], places=12)

    def test_per_line_growth_overrides_default(self):
        e = [{"annual_amount": 100_000.0, "growth_pct": 10.0, "is_included": True},
             {"annual_amount": 100_000.0, "growth_pct": None, "is_included": True}]
        proj = um.project_noi_series(1_000_000.0, e, 2, 0.0, 0.0)
        self.assertAlmostEqual(proj["years"][1]["expenses"], 110_000.0 + 100_000.0, places=6)

    def test_exit_uses_forward_noi(self):
        e = expenses(0.0, growth=0.0)
        proj = um.project_noi_series(100_000.0, e, 3, 10.0, 0.0)
        self.assertAlmostEqual(proj["noi_exit"], 100_000.0 * 1.1 ** 3, places=6)
        self.assertEqual(len(proj["noi_series"]), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
