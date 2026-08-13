"""
Unit tests for tools/quick_analyzer_math.py and tools/quick_analyzer_t12.py.

Same discipline as tests/test_deal_analyzer_math.py: assertions restate
the expected arithmetic independently rather than calling the function
under test to compute its own expected value, so a wrong formula cannot
agree with itself.

The Eagle Rock figures below are the real twelve-month totals parsed out
of that property's actual T12 on production, recorded here so the
reconciliation can be asserted without needing the file.
"""

import ast
import unittest
from pathlib import Path

from tools import quick_analyzer_math as m

# Real Eagle Rock T12 totals (12 months, parsed 2026-08-13).
EAGLE = {
    "gross_potential_income": 1_315_737.00,
    "deductions": 358_836.81,
    "net_rental_income": 956_900.19,
    "other_income": 76_013.89,
    "effective_gross_income": 1_032_914.08,
    "operating_expenses": 731_610.09,
    "noi": 301_303.99,
}


class BuildNoiTests(unittest.TestCase):
    def test_buildup_arithmetic_line_by_line(self):
        r = m.build_noi(gpi=1_000_000, vacancy_pct=10, other_income=50_000,
                        expenses_mode="pct", expenses_value=40)
        self.assertAlmostEqual(r["vacancy_loss"], 100_000.0, places=6)
        self.assertAlmostEqual(r["net_rental_income"], 900_000.0, places=6)
        # EGI is net rental income plus other income: 900,000 + 50,000.
        self.assertAlmostEqual(r["effective_gross_income"], 950_000.0, places=6)
        # 40% of EGI, not of GPI -- an expense ratio quoted against gross
        # potential income would flatter every property with real vacancy.
        self.assertAlmostEqual(r["operating_expenses"], 380_000.0, places=6)
        self.assertAlmostEqual(r["noi"], 570_000.0, places=6)

    def test_expense_percentage_is_taken_against_egi_not_gpi(self):
        r = m.build_noi(1_000_000, 20, 0, "pct", 50)
        # 50% of EGI (800,000) is 400,000. Against GPI it would be 500,000.
        self.assertAlmostEqual(r["operating_expenses"], 400_000.0, places=6)
        self.assertNotAlmostEqual(r["operating_expenses"], 500_000.0, places=2)

    def test_dollar_expenses_are_used_verbatim_and_imply_a_ratio(self):
        r = m.build_noi(1_000_000, 0, 0, "amount", 375_000)
        self.assertAlmostEqual(r["operating_expenses"], 375_000.0, places=6)
        self.assertAlmostEqual(r["expense_ratio"], 0.375, places=9)
        self.assertAlmostEqual(r["noi"], 625_000.0, places=6)

    def test_zero_vacancy_and_missing_vacancy_are_different(self):
        r = m.build_noi(1_000_000, 0, 0, "pct", 0)
        self.assertAlmostEqual(r["noi"], 1_000_000.0, places=6)
        with self.assertRaises(m.ValidationError):
            m.build_noi(1_000_000, None, 0, "pct", 0)
        with self.assertRaises(m.ValidationError):
            m.build_noi(1_000_000, "", 0, "pct", 0)

    def test_rejects_impossible_inputs(self):
        for kwargs in (
            dict(gpi=None, vacancy_pct=5, other_income=0, expenses_mode="pct", expenses_value=40),
            dict(gpi=-1, vacancy_pct=5, other_income=0, expenses_mode="pct", expenses_value=40),
            dict(gpi=1000, vacancy_pct=101, other_income=0, expenses_mode="pct", expenses_value=40),
            dict(gpi=1000, vacancy_pct=-1, other_income=0, expenses_mode="pct", expenses_value=40),
            dict(gpi=1000, vacancy_pct=5, other_income=-1, expenses_mode="pct", expenses_value=40),
            dict(gpi=1000, vacancy_pct=5, other_income=0, expenses_mode="pct", expenses_value=101),
            dict(gpi=1000, vacancy_pct=5, other_income=0, expenses_mode="pct", expenses_value=None),
            dict(gpi=1000, vacancy_pct=5, other_income=0, expenses_mode="nonsense", expenses_value=40),
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(m.ValidationError):
                    m.build_noi(**kwargs)

    def test_eagle_rocks_real_totals_reconcile_through_the_buildup(self):
        """The real T12 numbers, run through the same build-up the form uses,
        must reproduce the NOI the file itself reports."""
        vac_pct = EAGLE["deductions"] / EAGLE["gross_potential_income"] * 100.0
        r = m.build_noi(EAGLE["gross_potential_income"], vac_pct,
                        EAGLE["other_income"], "amount", EAGLE["operating_expenses"])
        self.assertAlmostEqual(r["net_rental_income"], EAGLE["net_rental_income"], places=2)
        self.assertAlmostEqual(r["effective_gross_income"], EAGLE["effective_gross_income"], places=2)
        self.assertAlmostEqual(r["noi"], EAGLE["noi"], places=2)


class ImpliedPriceTests(unittest.TestCase):
    def test_the_whole_tool_in_one_assertion(self):
        # $600,000 NOI at a 6% cap is $10,000,000.
        self.assertAlmostEqual(m.implied_price(600_000, 6.0), 10_000_000.0, places=6)

    def test_eagle_rock_noi_at_six_percent(self):
        self.assertAlmostEqual(m.implied_price(EAGLE["noi"], 6.0),
                               301_303.99 / 0.06, places=6)

    def test_lower_cap_rate_implies_a_higher_price(self):
        self.assertGreater(m.implied_price(500_000, 4.0), m.implied_price(500_000, 8.0))

    def test_zero_or_negative_cap_rate_is_refused_not_infinite(self):
        for cap in (0, 0.0, -1, None, ""):
            with self.subTest(cap=cap):
                with self.assertRaises(m.ValidationError):
                    m.implied_price(500_000, cap)


class PriceRangeTests(unittest.TestCase):
    def test_band_is_symmetric_about_the_price(self):
        r = m.price_range(1_000_000, 10)
        self.assertAlmostEqual(r["low"], 900_000.0, places=6)
        self.assertAlmostEqual(r["high"], 1_100_000.0, places=6)
        self.assertAlmostEqual(r["delta"], 100_000.0, places=6)

    def test_all_three_offered_choices(self):
        for pct, low, high in ((5, 950_000.0, 1_050_000.0),
                               (10, 900_000.0, 1_100_000.0),
                               (20, 800_000.0, 1_200_000.0)):
            with self.subTest(pct=pct):
                r = m.price_range(1_000_000, pct)
                self.assertAlmostEqual(r["low"], low, places=6)
                self.assertAlmostEqual(r["high"], high, places=6)

    def test_offered_choices_are_the_ones_documented(self):
        self.assertEqual(m.RANGE_CHOICES, (5, 10, 20))
        self.assertIn(m.DEFAULT_RANGE_PCT, m.RANGE_CHOICES)

    def test_zero_range_collapses_to_the_price(self):
        r = m.price_range(1_000_000, 0)
        self.assertEqual(r["low"], r["high"], 1_000_000.0)


class GradeTests(unittest.TestCase):
    def test_each_band_on_its_own_side_of_every_boundary(self):
        implied = 1_000_000.0
        cases = [
            (900_000, m.GRADE_GREEN),     # 10% below
            (1_000_000, m.GRADE_GREEN),   # exactly at the implied price
            (1_000_001, m.GRADE_YELLOW),  # a dollar over
            (1_050_000, m.GRADE_YELLOW),  # exactly 5% over -- still yellow
            (1_050_001, m.GRADE_ORANGE),  # a dollar past 5%
            (1_150_000, m.GRADE_ORANGE),  # exactly 15% over -- still orange
            (1_150_001, m.GRADE_RED),     # a dollar past 15%
            (2_000_000, m.GRADE_RED),
        ]
        for ask, expected in cases:
            with self.subTest(ask=ask):
                g = m.grade(ask, implied)
                self.assertTrue(g["graded"])
                self.assertEqual(g["key"], expected)

    def test_over_pct_is_relative_to_the_implied_price(self):
        g = m.grade(1_200_000, 1_000_000)
        self.assertAlmostEqual(g["over_pct"], 20.0, places=9)
        self.assertAlmostEqual(g["difference"], 200_000.0, places=6)

    def test_absent_asking_price_is_not_an_error(self):
        for ask in (None, "", 0, -5):
            with self.subTest(ask=ask):
                g = m.grade(ask, 1_000_000)
                self.assertFalse(g["graded"])
                self.assertTrue(g["reason"])

    def test_the_disclaimer_is_present_whether_or_not_a_grade_was_produced(self):
        for ask in (None, 1_000_000):
            with self.subTest(ask=ask):
                g = m.grade(ask, 1_000_000)
                self.assertIn(m.REQUIRED_DISCLAIMER_PHRASE, g["disclaimer"])

    def test_thresholds_are_declared_unconfirmed(self):
        """The bands were invented for lack of a real source. If that ever
        stops being disclosed, this test fails."""
        self.assertEqual(m.GRADE_PROVENANCE, m.PROVENANCE_UNCONFIRMED)
        self.assertIn(m.REQUIRED_DISCLAIMER_PHRASE,
                      m.GRADE_DISCLAIMERS[m.PROVENANCE_UNCONFIRMED])

    def test_bands_are_ordered_and_cover_every_overage(self):
        bounds = [b.max_over_pct for b in m.GRADE_BANDS]
        self.assertIsNone(bounds[-1], "the last band must be open-ended")
        finite = [b for b in bounds if b is not None]
        self.assertEqual(finite, sorted(finite), "bands must ascend")
        # No overage can fall through: every value lands in some band.
        # Bounded below by -99%: a more negative overage means a negative
        # asking price, which grade() refuses rather than bands.
        for over in (-99, -50, -1, 0, 0.001, 5, 5.001, 15, 15.001, 1000):
            with self.subTest(over=over):
                ask = 1_000_000 * (1 + over / 100.0)
                self.assertTrue(m.grade(ask, 1_000_000)["graded"])


class ProvenanceTests(unittest.TestCase):
    BASE = {"cap_rate_pct": 6.0, "gross_potential_income": 1_000_000,
            "vacancy_pct": 10, "other_income": 0,
            "expenses_mode": "pct", "operating_expenses": 40}

    def test_buildup_path_is_labelled_estimated(self):
        r = m.analyze(dict(self.BASE))
        self.assertEqual(r["noi_provenance"], m.PROVENANCE_BUILDUP)
        self.assertEqual(r["noi_provenance_label"], "Estimated")
        self.assertIsNotNone(r["buildup"])
        self.assertAlmostEqual(r["noi"], 540_000.0, places=6)

    def test_direct_entry_is_labelled_entered_and_skips_the_buildup(self):
        r = m.analyze({**self.BASE, "noi_direct": 500_000})
        self.assertEqual(r["noi_provenance"], m.PROVENANCE_ENTERED)
        self.assertEqual(r["noi_provenance_label"], "Entered")
        self.assertIsNone(r["buildup"], "the build-up must be skipped, not merely ignored")
        self.assertAlmostEqual(r["noi"], 500_000.0, places=6)

    def test_direct_entry_overrides_the_buildup_figures(self):
        """Both sets of inputs present: the directly entered NOI wins, and
        the build-up's 540,000 must not leak into the price."""
        r = m.analyze({**self.BASE, "noi_direct": 100_000})
        self.assertAlmostEqual(r["noi"], 100_000.0, places=6)
        self.assertAlmostEqual(r["implied_price"], 100_000 / 0.06, places=6)

    def test_t12_provenance_survives_direct_entry(self):
        """A T12-sourced NOI arrives in the direct field. It must stay
        labelled as actuals rather than being downgraded to 'Entered' --
        but only while backed by the import it claims to come from."""
        submitted = {**self.BASE, "noi_direct": EAGLE["noi"],
                     "noi_provenance": m.PROVENANCE_T12}
        # The snapshot covers every field the import wrote, exactly as the
        # form carries it. A partial snapshot would mean the figures on
        # screen are not the ones that were imported.
        submitted["imported"] = {
            k: submitted[k] for k in
            ("gross_potential_income", "vacancy_pct", "other_income",
             "operating_expenses", "noi_direct")}
        r = m.analyze(submitted)
        self.assertEqual(r["noi_provenance"], m.PROVENANCE_T12)
        self.assertEqual(r["noi_provenance_label"], "Actuals — from T12")

    def test_an_unsubstantiated_t12_claim_is_downgraded(self):
        """The same call without the import behind it. Claiming actuals is
        not the same as having them."""
        r = m.analyze({**self.BASE, "noi_direct": EAGLE["noi"],
                       "noi_provenance": m.PROVENANCE_T12})
        self.assertEqual(r["noi_provenance"], m.PROVENANCE_ENTERED)

    def test_every_provenance_has_a_label_and_a_note(self):
        for p in m.VALID_PROVENANCE:
            with self.subTest(p=p):
                self.assertTrue(m.PROVENANCE_LABELS[p])
                self.assertTrue(m.PROVENANCE_NOTES[p])

    def test_unknown_provenance_is_refused(self):
        with self.assertRaises(m.ValidationError):
            m.analyze({**self.BASE, "noi_provenance": "made_up"})

    def test_entered_provenance_without_a_figure_is_refused(self):
        with self.assertRaises(m.ValidationError):
            m.analyze({**self.BASE, "noi_provenance": m.PROVENANCE_ENTERED})


class AnalyzeTests(unittest.TestCase):
    BASE = {"cap_rate_pct": 5.0, "noi_direct": 500_000}

    def test_end_to_end_price_range_and_grade(self):
        r = m.analyze({**self.BASE, "range_pct": 10, "asking_price": 11_000_000})
        self.assertAlmostEqual(r["implied_price"], 10_000_000.0, places=6)
        self.assertAlmostEqual(r["range"]["low"], 9_000_000.0, places=6)
        self.assertAlmostEqual(r["range"]["high"], 11_000_000.0, places=6)
        self.assertTrue(r["grade"]["graded"])
        self.assertEqual(r["grade"]["key"], m.GRADE_ORANGE)   # 10% over

    def test_price_per_unit(self):
        r = m.analyze({**self.BASE, "unit_count": 50})
        self.assertAlmostEqual(r["price_per_unit"], 200_000.0, places=6)

    def test_price_per_unit_absent_without_a_unit_count(self):
        self.assertIsNone(m.analyze(dict(self.BASE))["price_per_unit"])

    def test_default_range_applied_when_none_given(self):
        r = m.analyze(dict(self.BASE))
        self.assertEqual(r["range"]["range_pct"], m.DEFAULT_RANGE_PCT)

    def test_zero_or_negative_noi_cannot_be_capitalized(self):
        for noi in (0, -1):
            with self.subTest(noi=noi):
                with self.assertRaises(m.ValidationError):
                    m.analyze({"cap_rate_pct": 5.0, "noi_direct": noi})
        # And through the build-up: expenses exceeding EGI.
        with self.assertRaises(m.ValidationError):
            m.analyze({"cap_rate_pct": 5.0, "gross_potential_income": 100_000,
                       "vacancy_pct": 0, "other_income": 0,
                       "expenses_mode": "pct", "operating_expenses": 100})

    def test_nothing_returns_nan_or_infinity(self):
        r = m.analyze({**self.BASE, "asking_price": 9_000_000, "unit_count": 10})
        for key in ("noi", "implied_price", "price_per_unit"):
            self.assertEqual(r[key], r[key], f"{key} is NaN")
            self.assertNotIn(r[key], (float("inf"), float("-inf")))


class IsolationTests(unittest.TestCase):
    """The pivot's central safety claim: this tool shares no code with the
    returns engine Underwriting, Waterfall and Investor Report depend on."""

    def _imports(self, filename):
        src = Path(__file__).resolve().parents[1] / "tools" / filename
        tree = ast.parse(src.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    def test_quick_analyzer_math_does_not_import_the_returns_engine(self):
        imports = self._imports("quick_analyzer_math.py")
        self.assertNotIn("tools.deal_analyzer_math", imports)
        self.assertFalse([i for i in imports if "deal_analyzer" in i],
                         "quick_analyzer_math must not depend on the returns engine")

    def test_quick_analyzer_math_is_pure(self):
        imports = self._imports("quick_analyzer_math.py")
        for forbidden in ("flask", "flask_login", "sqlite3", "os", "requests"):
            self.assertNotIn(forbidden, imports)

    def test_t12_module_does_not_import_underwritings_itemized_machinery(self):
        imports = self._imports("quick_analyzer_t12.py")
        self.assertNotIn("tools.underwriting", imports)
        self.assertNotIn("tools.underwriting_math", imports)
        self.assertNotIn("flask", imports)


class T12ReconciliationTests(unittest.TestCase):
    """The gate that stopped a $112,546 error reaching the screen."""

    def setUp(self):
        from tools import quick_analyzer_t12 as t
        self.t = t

    def _totals(self, **overrides):
        base = {
            "gross_potential_income": EAGLE["gross_potential_income"],
            "deductions": EAGLE["deductions"],
            "other_income": EAGLE["other_income"],
            "effective_gross_income": EAGLE["effective_gross_income"],
            "operating_expenses": EAGLE["operating_expenses"],
            "noi": EAGLE["noi"],
        }
        base.update(overrides)
        return base

    def test_real_eagle_rock_totals_tie(self):
        self.t.reconcile(self._totals())   # must not raise

    def test_the_naive_vacancy_only_read_is_caught(self):
        """Reading code 4220 alone gives deductions of 243,397 instead of
        358,836.81. That build-up produces a NOI $115,439.81 too high, and
        the gate must refuse it rather than let it render."""
        with self.assertRaises(self.t.T12ReconciliationError):
            self.t.reconcile(self._totals(deductions=243_397.00))

    def test_a_cent_of_drift_is_tolerated_and_a_dollar_is_not(self):
        self.t.reconcile(self._totals(noi=EAGLE["noi"] + 0.005))
        with self.assertRaises(self.t.T12ReconciliationError):
            self.t.reconcile(self._totals(noi=EAGLE["noi"] + 1.0))

    def test_unreadable_and_unreconciled_are_different_failures(self):
        """One is a file the user can work around by typing; the other is a
        defect in this code. They must not be the same exception."""
        self.assertFalse(issubclass(self.t.T12ReconciliationError, self.t.T12Unreadable))
        self.assertFalse(issubclass(self.t.T12Unreadable, self.t.T12ReconciliationError))


if __name__ == "__main__":
    unittest.main()


class ProvenanceCannotBeFalsifiedTests(unittest.TestCase):
    """A label nobody can falsify is the only kind worth rendering.

    Uploading a T12 and then typing over the figures must not keep
    claiming the number came from actuals.
    """

    IMPORTED = {
        "gross_potential_income": EAGLE["gross_potential_income"],
        "vacancy_pct": EAGLE["deductions"] / EAGLE["gross_potential_income"] * 100.0,
        "other_income": EAGLE["other_income"],
        "operating_expenses": EAGLE["operating_expenses"],
        "noi_direct": EAGLE["noi"],
    }

    def _submitted(self, **overrides):
        s = dict(self.IMPORTED)
        s.update(overrides)
        return s

    def test_untouched_figures_keep_the_t12_claim(self):
        self.assertEqual(
            m.resolve_provenance(m.PROVENANCE_T12, self._submitted(), self.IMPORTED),
            m.PROVENANCE_T12)

    def test_editing_any_figure_drops_the_claim(self):
        for field in self.IMPORTED:
            with self.subTest(field=field):
                edited = self._submitted(**{field: self.IMPORTED[field] + 1000})
                self.assertEqual(
                    m.resolve_provenance(m.PROVENANCE_T12, edited, self.IMPORTED),
                    m.PROVENANCE_BUILDUP,
                    f"editing {field} must drop the actuals claim")

    def test_clearing_a_figure_drops_the_claim(self):
        edited = self._submitted(operating_expenses=None)
        self.assertEqual(
            m.resolve_provenance(m.PROVENANCE_T12, edited, self.IMPORTED),
            m.PROVENANCE_BUILDUP)

    def test_a_claim_with_no_import_behind_it_is_not_honoured(self):
        """Someone posting noi_provenance=t12 by hand gets no free label."""
        for imported in (None, {}):
            with self.subTest(imported=imported):
                self.assertEqual(
                    m.resolve_provenance(m.PROVENANCE_T12, self._submitted(), imported),
                    m.PROVENANCE_BUILDUP)

    def test_sub_cent_rounding_is_not_treated_as_an_edit(self):
        edited = self._submitted(operating_expenses=EAGLE["operating_expenses"] + 0.004)
        self.assertEqual(
            m.resolve_provenance(m.PROVENANCE_T12, edited, self.IMPORTED),
            m.PROVENANCE_T12)

    def test_other_provenances_pass_through_untouched(self):
        for p in (m.PROVENANCE_BUILDUP, m.PROVENANCE_ENTERED):
            with self.subTest(p=p):
                self.assertEqual(m.resolve_provenance(p, {}, None), p)

    def test_end_to_end_through_analyze(self):
        base = {"cap_rate_pct": 6.0, "noi_provenance": m.PROVENANCE_T12,
                "imported": self.IMPORTED, **self.IMPORTED}
        self.assertEqual(m.analyze(dict(base))["noi_provenance"], m.PROVENANCE_T12)
        tampered = dict(base)
        tampered["noi_direct"] = 900_000.0
        self.assertEqual(m.analyze(tampered)["noi_provenance"], m.PROVENANCE_ENTERED,
                         "a hand-edited NOI is 'Entered', never 'Actuals'")
