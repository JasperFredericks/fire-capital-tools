"""
Tests for per-year assumption schedules.

The claim this phase lives or dies on: a scenario with no schedule
produces output identical to before the per-year rebuild existed --
identical as in `==` on floats, not "close enough". The projection engine
is shared with every scenario in the system, so anything less would mean
silently repricing deals that were never edited.
"""

import json
import sys
import unittest
from decimal import Decimal, getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import deal_analyzer_math as dam
from tools import underwriting_math as um
from tools import underwriting_schedule as us

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


def flat_projection(scenario, units, expenses):
    """The pre-per-year code path: the flat-rate signature, untouched."""
    return um.project_noi_series(
        um.build_egi(units, scenario)["effective_gross_income"], expenses,
        int(scenario["hold_years"]), scenario["rent_growth_pct"],
        scenario["expense_growth_pct"])


class TestAbsentScheduleIsBitIdentical(unittest.TestCase):
    """The load-bearing test of the whole phase."""

    def setUp(self):
        self.scenario, self.units, self.expenses = load_fixture()

    def test_noi_series_is_exactly_equal_not_merely_close(self):
        per_year = um.analyze_scenario(self.scenario, self.units, self.expenses)
        flat = flat_projection(self.scenario, self.units, self.expenses)
        self.assertEqual(per_year["projection"]["noi_series"], flat["noi_series"])

    def test_exit_noi_is_exactly_equal(self):
        per_year = um.analyze_scenario(self.scenario, self.units, self.expenses)
        flat = flat_projection(self.scenario, self.units, self.expenses)
        self.assertEqual(per_year["projection"]["noi_exit"], flat["noi_exit"])

    def test_none_and_empty_schedule_agree(self):
        a = um.analyze_scenario(self.scenario, self.units, self.expenses, None)
        b = um.analyze_scenario(self.scenario, self.units, self.expenses, [])
        self.assertEqual(json.dumps(a["returns"], sort_keys=True, default=str),
                         json.dumps(b["returns"], sort_keys=True, default=str))

    def test_eagle_rock_confirmed_figures_unmoved(self):
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

    def test_has_schedule_is_false(self):
        r = um.analyze_scenario(self.scenario, self.units, self.expenses)
        self.assertFalse(r["has_schedule"])

    def test_a_schedule_that_only_restates_the_flat_rates_changes_nothing(self):
        """Storing the flat value as an explicit override must be a no-op."""
        rows = [{"year": y,
                 "vacancy_pct": self.scenario["vacancy_pct"],
                 "concessions_pct": self.scenario["concessions_pct"],
                 "bad_debt_pct": self.scenario["bad_debt_pct"],
                 "rent_growth_pct": self.scenario["rent_growth_pct"]}
                for y in range(1, int(self.scenario["hold_years"]) + 1)]
        scheduled = um.analyze_scenario(self.scenario, self.units, self.expenses, rows)
        flat = flat_projection(self.scenario, self.units, self.expenses)
        self.assertEqual(scheduled["projection"]["noi_series"], flat["noi_series"])
        self.assertEqual(scheduled["projection"]["noi_exit"], flat["noi_exit"])


class TestDealAnalyzerUntouched(unittest.TestCase):
    """Deal Analyzer must not gain any of this."""

    def test_deal_analyzer_math_does_not_import_the_schedule_module(self):
        """Checked by parsing imports, not by grepping for the word: the
        engine's docstring legitimately says "amortization schedule", and
        a substring test cannot tell that from a dependency."""
        import ast
        tree = ast.parse(Path(dam.__file__).read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module)
        self.assertNotIn("tools.underwriting_schedule", roots)
        self.assertFalse([r for r in roots if "underwriting" in r],
                         "the shared engine must not depend on Underwriting")

    def test_engine_signature_unchanged(self):
        import inspect
        params = list(inspect.signature(dam.analyze_noi_series).parameters)
        self.assertEqual(params, ["inputs", "noi_series", "noi_exit"])

    def test_analyze_still_works_with_no_schedule_concept(self):
        out = dam.analyze({
            "purchase_price": 1_000_000.0, "closing_costs_pct": 2.0, "ltv_pct": 65.0,
            "interest_rate_pct": 6.0, "amort_years": 30, "hold_years": 5,
            "exit_cap_pct": 6.0, "selling_costs_pct": 2.0,
            "noi_year1": 60_000.0, "noi_growth_pct": 3.0,
        })
        self.assertIsNotNone(out["levered_irr"])


class TestCarryForward(unittest.TestCase):
    """A short schedule carries its last value forward, never zero-fills."""

    def test_level_rate_carries_forward(self):
        schedule = us.normalize([{"year": 1, "vacancy_pct": 8.0},
                                 {"year": 2, "vacancy_pct": 6.0}])
        scenario = {"vacancy_pct": 5.0}
        self.assertEqual(us.resolve(schedule, "vacancy_pct", 5.0, 1), 8.0)
        self.assertEqual(us.resolve(schedule, "vacancy_pct", 5.0, 2), 6.0)
        for year in (3, 4, 7, 30):
            self.assertEqual(us.resolve(schedule, "vacancy_pct", 5.0, year), 6.0,
                             f"year {year} should carry year 2 forward")

    def test_five_year_schedule_on_a_seven_year_hold(self):
        """The example from the requirement, asserted directly."""
        rows = [{"year": y, "rent_growth_pct": r}
                for y, r in zip(range(1, 6), (2.0, 3.0, 4.0, 5.0, 5.0))]
        schedule = us.normalize(rows)
        scenario = {"rent_growth_pct": 3.0}
        for year in (5, 6, 7):
            self.assertEqual(
                us.resolve(schedule, "rent_growth_pct", 3.0, year), 5.0,
                "a 5-year schedule on a 7-year hold means 5% thereafter")

    def test_carry_forward_is_not_zero_fill(self):
        schedule = us.normalize([{"year": 1, "vacancy_pct": 9.0}])
        self.assertNotEqual(us.resolve(schedule, "vacancy_pct", 5.0, 5), 0.0)
        self.assertEqual(us.resolve(schedule, "vacancy_pct", 5.0, 5), 9.0)

    def test_unscheduled_field_falls_back_to_the_flat_rate(self):
        schedule = us.normalize([{"year": 1, "vacancy_pct": 9.0}])
        self.assertEqual(us.resolve(schedule, "bad_debt_pct", 0.5, 3), 0.5)

    def test_blank_cell_is_not_zero(self):
        schedule = us.normalize([{"year": 1, "vacancy_pct": None,
                                  "concessions_pct": 2.0}])
        self.assertEqual(us.resolve(schedule, "vacancy_pct", 5.0, 1), 5.0)
        self.assertEqual(us.resolve(schedule, "concessions_pct", 1.0, 1), 2.0)


class TestPerYearEgiHandCalculated(unittest.TestCase):
    """Every year's EGI checked against arithmetic done independently."""

    def setUp(self):
        self.scenario, self.units, self.expenses = load_fixture()
        self.base = um.build_egi(self.units, self.scenario)
        # vacancy climbs, rent growth varies, over a 5-year hold
        self.rows = [
            {"year": 1, "vacancy_pct": 5.0, "rent_growth_pct": 2.0},
            {"year": 2, "vacancy_pct": 7.0, "rent_growth_pct": 10.0},
            {"year": 3, "vacancy_pct": 9.0, "rent_growth_pct": 3.0},
        ]
        self.schedule = us.normalize(self.rows)

    def _expected_egi(self, year):
        """Independent recomputation with Decimal."""
        gpr1 = Decimal(str(self.base["gross_potential_rent"]))
        ltl1 = Decimal(str(self.base["loss_to_lease"]))
        oi1 = Decimal(str(self.base["other_income"]))
        growth = {1: "2.0", 2: "10.0", 3: "3.0"}
        factor = Decimal(1)
        for t in range(1, year):
            rate = growth.get(t, growth[max(growth)])
            factor *= (1 + Decimal(rate) / 100)
        vac = {1: "5.0", 2: "7.0", 3: "9.0"}
        v = Decimal(vac.get(year, vac[max(vac)]))
        conc = Decimal(str(self.scenario["concessions_pct"]))
        bd = Decimal(str(self.scenario["bad_debt_pct"]))
        gpr = gpr1 * factor
        return (gpr - ltl1 * factor - gpr * v / 100 - gpr * conc / 100
                - gpr * bd / 100 + oi1 * factor)

    def test_each_year_egi_matches_hand_calculation(self):
        for year in range(1, 7):
            with self.subTest(year=year):
                got = um.build_egi_for_year(
                    self.base, self.scenario, self.schedule, year)["effective_gross_income"]
                self.assertAlmostEqual(got, float(self._expected_egi(year)), places=6)

    def test_year1_is_the_base_year_unscaled(self):
        got = um.build_egi_for_year(self.base, self.scenario, self.schedule, 1)
        self.assertEqual(got["rent_growth_factor"], 1.0)

    def test_rent_growth_carries_a_year_into_the_next(self):
        """Year 2 is year 1 grown by year 1's rate, per the convention."""
        y1 = um.build_egi_for_year(self.base, self.scenario, self.schedule, 1)
        y2 = um.build_egi_for_year(self.base, self.scenario, self.schedule, 2)
        self.assertAlmostEqual(y2["rent_growth_factor"] / y1["rent_growth_factor"],
                               1.02, places=12)
        y3 = um.build_egi_for_year(self.base, self.scenario, self.schedule, 3)
        self.assertAlmostEqual(y3["rent_growth_factor"] / y2["rent_growth_factor"],
                               1.10, places=12)

    def test_rising_vacancy_lowers_noi(self):
        """Vacancy only. The mixed schedule above also raises rent growth
        to 10%, which more than offsets the vacancy rise -- so isolating
        the variable is what makes this test mean anything."""
        vacancy_only = [{"year": 1, "vacancy_pct": 5.0},
                        {"year": 2, "vacancy_pct": 7.0},
                        {"year": 3, "vacancy_pct": 9.0}]
        flat = um.analyze_scenario(self.scenario, self.units, self.expenses)
        scheduled = um.analyze_scenario(self.scenario, self.units,
                                        self.expenses, vacancy_only)
        # Year 1 keeps the flat 5% and must be untouched.
        self.assertEqual(scheduled["projection"]["noi_series"][0],
                         flat["projection"]["noi_series"][0])
        for year_index in (1, 2, 3, 4):
            self.assertLess(scheduled["projection"]["noi_series"][year_index],
                            flat["projection"]["noi_series"][year_index],
                            f"year {year_index + 1} should fall as vacancy rises")

    def test_rent_growth_alone_raises_noi(self):
        growth_only = [{"year": 2, "rent_growth_pct": 10.0}]
        flat = um.analyze_scenario(self.scenario, self.units, self.expenses)
        scheduled = um.analyze_scenario(self.scenario, self.units,
                                        self.expenses, growth_only)
        self.assertEqual(scheduled["projection"]["noi_series"][0],
                         flat["projection"]["noi_series"][0])
        self.assertGreater(scheduled["projection"]["noi_series"][2],
                           flat["projection"]["noi_series"][2])

    def test_has_schedule_is_true(self):
        r = um.analyze_scenario(self.scenario, self.units, self.expenses, self.rows)
        self.assertTrue(r["has_schedule"])


class TestPerLineExpenseSchedule(unittest.TestCase):
    """The "gas year 1 2%, year 2 10%" case."""

    LINE = {"label": "Natural Gas", "annual_amount": 10_000.0, "growth_pct": 3.0,
            "is_included": 1, "line_kind": "operating",
            "growth_schedule": "[2.0, 10.0]"}

    def test_schedule_overrides_the_flat_rate(self):
        self.assertAlmostEqual(us.line_growth_for_year(self.LINE, 2.5, 1), 0.02, places=12)
        self.assertAlmostEqual(us.line_growth_for_year(self.LINE, 2.5, 2), 0.10, places=12)

    def test_schedule_carries_last_value_forward(self):
        for year in (3, 4, 9):
            self.assertAlmostEqual(us.line_growth_for_year(self.LINE, 2.5, year),
                                   0.10, places=12)

    def test_amount_compounds_the_schedule(self):
        self.assertAlmostEqual(us.line_amount_for_year(self.LINE, 10_000.0, 2.5, 1),
                               10_000.0, places=9)
        self.assertAlmostEqual(us.line_amount_for_year(self.LINE, 10_000.0, 2.5, 2),
                               10_200.0, places=9)
        self.assertAlmostEqual(us.line_amount_for_year(self.LINE, 10_000.0, 2.5, 3),
                               10_200.0 * 1.10, places=9)

    def test_no_schedule_uses_own_rate_then_default(self):
        own = {"annual_amount": 100.0, "growth_pct": 4.0}
        self.assertAlmostEqual(us.line_growth_for_year(own, 2.5, 3), 0.04, places=12)
        inherit = {"annual_amount": 100.0, "growth_pct": None}
        self.assertAlmostEqual(us.line_growth_for_year(inherit, 2.5, 3), 0.025, places=12)

    def test_explicit_zero_does_not_inherit(self):
        zero = {"annual_amount": 100.0, "growth_pct": 0.0}
        self.assertEqual(us.line_growth_for_year(zero, 10.0, 4), 0.0)

    def test_malformed_schedule_degrades_to_the_flat_rate(self):
        bad = {"annual_amount": 100.0, "growth_pct": 4.0, "growth_schedule": "not json"}
        self.assertIsNone(us.parse_line_schedule("not json"))
        self.assertAlmostEqual(us.line_growth_for_year(bad, 2.5, 2), 0.04, places=12)

    def test_round_trip_through_storage(self):
        raw = us.dump_line_schedule([2.0, 10.0])
        self.assertEqual(us.parse_line_schedule(raw), [2.0, 10.0])
        self.assertIsNone(us.dump_line_schedule([]))
        self.assertIsNone(us.dump_line_schedule(None))
        self.assertIsNone(us.parse_line_schedule(None))

    def test_scheduled_line_moves_the_projection(self):
        scenario, units, expenses = load_fixture()
        scheduled = [dict(l) for l in expenses]
        target = next(l for l in scheduled if l.get("is_included"))
        target["growth_schedule"] = "[0.0, 50.0]"
        base = um.analyze_scenario(scenario, units, expenses)
        after = um.analyze_scenario(scenario, units, scheduled)
        self.assertEqual(base["projection"]["noi_series"][0],
                         after["projection"]["noi_series"][0])
        self.assertNotEqual(base["projection"]["noi_series"][2],
                            after["projection"]["noi_series"][2])


class TestCompoundExactness(unittest.TestCase):
    """The uniform-rate special case exists to preserve previously-quoted
    numbers exactly; assert it does."""

    def test_uniform_rates_use_the_pow_expression(self):
        self.assertEqual(us.compound([3.0] * 4), (1.03) ** 4)
        self.assertEqual(us.compound_fractions([0.025] * 3), (1.025) ** 3)

    def test_empty_is_unity(self):
        self.assertEqual(us.compound([]), 1.0)
        self.assertEqual(us.compound_fractions([]), 1.0)

    def test_varying_rates_compound_in_order(self):
        got = us.compound([2.0, 10.0])
        self.assertAlmostEqual(got, 1.02 * 1.10, places=12)

    def test_no_percent_round_trip_error(self):
        """compound_fractions must not route through percent and back."""
        self.assertEqual(us.compound_fractions([0.025] * 5), (1.025) ** 5)


class TestValueCoercion(unittest.TestCase):
    """us._f is used by the save route to read the scenario's own stored
    value, which arrives from sqlite as a float rather than a form string.
    An earlier version routed that through the form parser, which assumes
    a string and raised AttributeError -- caught end-to-end, pinned here."""

    def test_accepts_floats_ints_and_strings(self):
        self.assertEqual(us._f(5.0), 5.0)
        self.assertEqual(us._f(5), 5.0)
        self.assertEqual(us._f("5"), 5.0)
        self.assertEqual(us._f("5.25"), 5.25)

    def test_blank_and_none_are_none(self):
        self.assertIsNone(us._f(None))
        self.assertIsNone(us._f(""))

    def test_garbage_is_none_not_an_exception(self):
        self.assertIsNone(us._f("abc"))
        self.assertIsNone(us._f(object()))


class TestPurity(unittest.TestCase):
    def test_schedule_module_is_pure(self):
        import ast
        tree = ast.parse(Path(us.__file__).read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        self.assertNotIn("flask", roots)
        self.assertNotIn("sqlite3", roots)


if __name__ == "__main__":
    unittest.main()
