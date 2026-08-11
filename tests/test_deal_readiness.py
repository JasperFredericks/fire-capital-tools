"""
Tests for the Deal Readiness reference thresholds.

The arithmetic here is trivial -- five comparisons. What these tests
actually guard is the HONESTY OF THE LABELS.

Two of the five thresholds come from Michael Blank's template; three are
industry-convention placeholders nobody has confirmed. If a later edit
drops the disclaimers, nothing breaks, nothing raises, and the page keeps
rendering five confident-looking targets that a reader will reasonably
take for FIRE Capital standards. That is the failure mode worth a test,
and it is the same one the API cost page guards against.

DisclaimerEnforcementTests below is the guardrail. It checks the config
AND the rendered HTML, because a config-only test would pass while the
template quietly stopped displaying the labels.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import deal_readiness_defaults as dr  # noqa: E402

TEMPLATE_PATH = (Path(__file__).resolve().parent.parent
                 / "templates" / "tools" / "underwriting_detail.html")


def _result(**returns):
    base = {"dscr": 1.30, "levered_irr": 0.16, "cash_on_cash": 0.09,
            "equity_multiple": 2.10}
    base.update(returns)
    return {"returns": base, "operating_expenses_year1": 400_000.0,
            "egi": {"effective_gross_income": 1_000_000.0}}


class DisclaimerEnforcementTests(unittest.TestCase):
    """THE GUARDRAIL. If these fail, the page is presenting unconfirmed
    numbers as though they were confirmed."""

    def test_every_threshold_has_a_disclaimer(self):
        for t in dr.THRESHOLDS:
            with self.subTest(t.key):
                self.assertTrue(t.disclaimer.strip(),
                                f"{t.key} has no disclaimer")

    def test_every_disclaimer_says_not_confirmed(self):
        """The label must actually disclaim. A disclaimer softened to
        something reassuring is worse than none, because it looks like
        due diligence was done."""
        for t in dr.THRESHOLDS:
            with self.subTest(t.key):
                self.assertIn(dr.REQUIRED_DISCLAIMER_PHRASE, t.disclaimer.lower())

    def test_every_threshold_declares_valid_provenance(self):
        for t in dr.THRESHOLDS:
            with self.subTest(t.key):
                self.assertIn(t.provenance, dr.VALID_PROVENANCE)

    def test_both_provenance_tiers_have_distinct_disclaimers(self):
        """Two tiers exist so a reader can tell a template default from an
        invented placeholder. Collapsing them to one string defeats that."""
        self.assertEqual(set(dr.DISCLAIMERS), set(dr.VALID_PROVENANCE))
        self.assertNotEqual(dr.DISCLAIMERS[dr.PROVENANCE_TEMPLATE],
                            dr.DISCLAIMERS[dr.PROVENANCE_INFERRED])

    def test_inferred_disclaimer_admits_it_is_not_from_the_template(self):
        """The three invented thresholds must not be allowed to read as
        though they came from the template."""
        self.assertIn("not from the template",
                      dr.DISCLAIMERS[dr.PROVENANCE_INFERRED].lower())

    def test_evaluate_emits_a_disclaimer_on_every_row(self):
        for row in dr.evaluate(_result()):
            with self.subTest(row["key"]):
                self.assertTrue(row["disclaimer"].strip())
                self.assertIn(dr.REQUIRED_DISCLAIMER_PHRASE, row["disclaimer"].lower())

    def test_template_renders_a_disclaimer_per_row(self):
        """Guards the TEMPLATE, not just the config.

        The disclaimer must be emitted inside the per-row loop. A
        config-only test would still pass if someone deleted the label
        from the markup or demoted it to a title= tooltip.
        """
        html = TEMPLATE_PATH.read_text(encoding="utf-8")
        readiness = html[html.index('id="readiness"'):html.index('id="income"')]
        self.assertIn("row.disclaimer", readiness,
                      "The readiness table no longer renders row.disclaimer — "
                      "every threshold must display its own provenance label.")
        # It must be rendered as visible text, not hidden in an attribute.
        self.assertNotRegex(
            readiness, r'title\s*=\s*"\{\{\s*row\.disclaimer',
            "row.disclaimer must be visible text, not a tooltip.")

    def test_template_keeps_the_section_level_warning(self):
        html = TEMPLATE_PATH.read_text(encoding="utf-8")
        readiness = html[html.index('id="readiness"'):html.index('id="income"')]
        self.assertIn("not FIRE Capital standards", readiness)


class NoScoreTests(unittest.TestCase):
    """No grade is produced. Three of five targets are placeholders, so a
    composite would be the most quotable and least defensible number in
    the app."""

    def test_counts_is_a_tally_not_a_score(self):
        c = dr.counts(dr.evaluate(_result()))
        self.assertEqual(set(c), {dr.STATUS_PASS, dr.STATUS_ATTENTION,
                                  dr.STATUS_UNAVAILABLE, "total"})
        for key in ("score", "grade", "rating", "verdict"):
            self.assertNotIn(key, c)

    def test_module_exposes_no_scoring_function(self):
        for name in ("score", "grade", "rate_deal", "verdict", "overall"):
            self.assertFalse(hasattr(dr, name), f"dr.{name} should not exist")


class ThresholdProvenanceTests(unittest.TestCase):
    def test_five_thresholds(self):
        self.assertEqual(len(dr.THRESHOLDS), 5)

    def test_only_dscr_and_irr_claim_template_provenance(self):
        """Only these two were actually read from the file. If a later
        edit relabels an invented number as template-sourced, that is a
        false provenance claim and this fails."""
        from_template = {t.key for t in dr.THRESHOLDS if t.is_from_template}
        self.assertEqual(from_template, {"dscr", "levered_irr"})

    def test_template_sourced_values_match_the_file(self):
        by_key = {t.key: t for t in dr.THRESHOLDS}
        self.assertEqual(by_key["dscr"].value, 1.25)
        self.assertEqual(by_key["levered_irr"].value, 0.14)

    def test_no_deal_type_bifurcation(self):
        """The Value-Add / Stable split is unconfirmed and must not be
        built speculatively."""
        for name in ("DEAL_TYPES", "VALUE_ADD", "STABLE", "THRESHOLDS_BY_TYPE"):
            self.assertFalse(hasattr(dr, name))


class EvaluationTests(unittest.TestCase):
    def test_min_direction_passes_at_and_above(self):
        rows = {r["key"]: r for r in dr.evaluate(_result(dscr=1.25))}
        self.assertEqual(rows["dscr"]["status"], dr.STATUS_PASS)
        rows = {r["key"]: r for r in dr.evaluate(_result(dscr=1.24))}
        self.assertEqual(rows["dscr"]["status"], dr.STATUS_ATTENTION)

    def test_max_direction_passes_at_and_below(self):
        r = {x["key"]: x for x in dr.evaluate(
            {"returns": {}, "operating_expenses_year1": 600_000.0,
             "egi": {"effective_gross_income": 1_000_000.0}})}
        self.assertEqual(r["expense_ratio"]["actual"], 0.60)
        self.assertEqual(r["expense_ratio"]["status"], dr.STATUS_PASS)
        r = {x["key"]: x for x in dr.evaluate(
            {"returns": {}, "operating_expenses_year1": 610_000.0,
             "egi": {"effective_gross_income": 1_000_000.0}})}
        self.assertEqual(r["expense_ratio"]["status"], dr.STATUS_ATTENTION)

    def test_uncomputable_metric_is_unavailable_not_a_pass(self):
        """A blank row must never read as 'fine'."""
        rows = {r["key"]: r for r in dr.evaluate(_result(levered_irr=None))}
        self.assertEqual(rows["levered_irr"]["status"], dr.STATUS_UNAVAILABLE)
        self.assertIsNone(rows["levered_irr"]["actual"])

    def test_engine_reason_is_carried_through(self):
        res = _result(levered_irr=None)
        res["returns"]["levered_irr_reason"] = "All cash flows are negative."
        rows = {r["key"]: r for r in dr.evaluate(res)}
        self.assertEqual(rows["levered_irr"]["reason"], "All cash flows are negative.")

    def test_no_result_yields_all_unavailable(self):
        rows = dr.evaluate(None)
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(r["status"] == dr.STATUS_UNAVAILABLE for r in rows))

    def test_zero_egi_does_not_divide_by_zero(self):
        rows = {r["key"]: r for r in dr.evaluate(
            {"returns": {}, "operating_expenses_year1": 100.0,
             "egi": {"effective_gross_income": 0}})}
        self.assertIsNone(rows["expense_ratio"]["actual"])

    def test_bool_is_not_accepted_as_a_metric_value(self):
        """isinstance(True, int) is True, so a stray boolean would compare
        as 1 and render as a real figure."""
        rows = {r["key"]: r for r in dr.evaluate(_result(dscr=True))}
        self.assertEqual(rows["dscr"]["status"], dr.STATUS_UNAVAILABLE)


class NoEngineChangeTests(unittest.TestCase):
    def test_module_does_not_import_the_engine(self):
        """This feature compares numbers that already exist. Importing the
        returns engine here would be the first step toward recomputing
        them differently."""
        src = (Path(__file__).resolve().parent.parent
               / "tools" / "deal_readiness_defaults.py").read_text(encoding="utf-8")
        self.assertNotIn("deal_analyzer_math", src)
        self.assertNotIn("underwriting_math", src)

    def test_module_is_flask_free(self):
        src = (Path(__file__).resolve().parent.parent
               / "tools" / "deal_readiness_defaults.py").read_text(encoding="utf-8")
        self.assertNotRegex(src, r"^\s*(from|import)\s+flask", )


if __name__ == "__main__":
    unittest.main()
