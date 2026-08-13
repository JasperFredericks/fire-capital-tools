"""
Unit tests for tools/site_dd_conditions.py and the re-homed checklist.

Replaces the tests for the old 1-5 numeric scale, which tested a scale
that no longer exists. Same discipline as tests/test_deal_analyzer_math.py:
assertions restate the expected result independently rather than calling
the function under test to compute its own expected value.
"""

import unittest

from tools import site_dd_checklist as cl
from tools import site_dd_conditions as cond


def responses(**kw):
    """A conditions map keyed by real checklist keys."""
    return dict(kw)


class ScaleTests(unittest.TestCase):
    def test_the_five_states_in_order(self):
        self.assertEqual(cond.CONDITIONS,
                         ("excellent", "good", "satisfactory", "repair", "replace"))
        self.assertEqual([cond.CONDITION_LABELS[c] for c in cond.CONDITIONS],
                         ["Excellent", "Good", "Satisfactory", "Repair", "Replace"])

    def test_only_repair_and_replace_count_as_work(self):
        self.assertEqual(cond.WORK_CONDITIONS, ("repair", "replace"))
        for c in ("excellent", "good", "satisfactory"):
            self.assertFalse(cond.needs_work(c))
        for c in ("repair", "replace"):
            self.assertTrue(cond.needs_work(c))

    def test_every_state_has_a_label_hint_and_colour(self):
        for c in cond.CONDITIONS:
            self.assertTrue(cond.CONDITION_LABELS[c])
            self.assertTrue(cond.CONDITION_HINTS[c])
            self.assertTrue(cond.CONDITION_COLOURS[c])

    def test_old_numeric_scores_are_rejected_not_translated(self):
        """A stored 2 meant 'Poor' on a scale that no longer exists.
        Reading it as 'Repair' would invent an inspector's opinion."""
        for value in (1, 2, 3, 4, 5, 0, True, False, None, "", "Poor", "REPAIR", 2.0):
            with self.subTest(value=value):
                self.assertFalse(cond.is_valid(value))
        for value in cond.CONDITIONS:
            self.assertTrue(cond.is_valid(value))

    def test_label_of_anything_invalid_reads_not_assessed(self):
        self.assertEqual(cond.label(None), "Not assessed")
        self.assertEqual(cond.label(2), "Not assessed")
        self.assertEqual(cond.label("replace"), "Replace")

    def test_unassessed_ranks_below_every_real_state(self):
        for c in cond.CONDITIONS:
            self.assertGreater(cond.rank(c), cond.rank(None))


class SummaryTests(unittest.TestCase):
    KEYS = cl.ITEM_KEYS

    def test_empty_assessment(self):
        s = cond.summarize({}, cl.CATEGORIES)
        self.assertEqual(s["work_count"], 0)
        self.assertEqual(s["assessed_count"], 0)
        self.assertEqual(s["not_assessed_count"], 32)
        self.assertEqual(s["completion_pct"], 0.0)
        self.assertIsNone(s["worst"])
        self.assertEqual(s["headline"], "Nothing assessed yet.")

    def test_counts_are_counts(self):
        given = {
            self.KEYS[0]: "excellent",
            self.KEYS[1]: "good",
            self.KEYS[2]: "good",
            self.KEYS[3]: "satisfactory",
            self.KEYS[4]: "repair",
            self.KEYS[5]: "replace",
            self.KEYS[6]: "replace",
        }
        s = cond.summarize(given, cl.CATEGORIES)
        self.assertEqual(s["counts"]["excellent"], 1)
        self.assertEqual(s["counts"]["good"], 2)
        self.assertEqual(s["counts"]["satisfactory"], 1)
        self.assertEqual(s["counts"]["repair"], 1)
        self.assertEqual(s["counts"]["replace"], 2)
        self.assertEqual(s["work_count"], 3)          # 1 repair + 2 replace
        self.assertEqual(s["repair_count"], 1)
        self.assertEqual(s["replace_count"], 2)
        self.assertEqual(s["assessed_count"], 7)
        self.assertEqual(s["not_assessed_count"], 25)
        self.assertAlmostEqual(s["completion_pct"], 7 / 32 * 100, places=9)

    def test_there_is_deliberately_no_overall_score(self):
        """The central design decision of this rebuild. If an 'overall'
        key ever reappears, this test fails and someone has to justify it."""
        s = cond.summarize({self.KEYS[0]: "good"}, cl.CATEGORIES)
        for banned in ("overall", "score", "risk_band", "mean", "average"):
            self.assertNotIn(banned, s)

    def test_worst_is_the_worst_present_not_an_average(self):
        s = cond.summarize({self.KEYS[0]: "excellent", self.KEYS[1]: "replace"},
                           cl.CATEGORIES)
        self.assertEqual(s["worst"], "replace")
        self.assertEqual(s["worst_label"], "Replace")
        # Nine excellents do not dilute one replace.
        many = {self.KEYS[i]: "excellent" for i in range(9)}
        many[self.KEYS[9]] = "replace"
        self.assertEqual(cond.summarize(many, cl.CATEGORIES)["worst"], "replace")

    def test_work_items_are_ordered_worst_first(self):
        given = {
            self.KEYS[0]: "repair",
            self.KEYS[1]: "replace",
            self.KEYS[2]: "repair",
            self.KEYS[3]: "good",
        }
        s = cond.summarize(given, cl.CATEGORIES)
        self.assertEqual(s["work_items"][0], self.KEYS[1], "replace sorts above repair")
        self.assertEqual(set(s["work_items"]), {self.KEYS[0], self.KEYS[1], self.KEYS[2]})
        self.assertNotIn(self.KEYS[3], s["work_items"])

    def test_ordered_counts_are_worst_first_for_display(self):
        s = cond.summarize({}, cl.CATEGORIES)
        self.assertEqual([c["key"] for c in s["ordered_counts"]],
                         ["replace", "repair", "satisfactory", "good", "excellent"])
        self.assertEqual([c["is_work"] for c in s["ordered_counts"]],
                         [True, True, False, False, False])

    def test_unknown_keys_are_ignored_not_fatal(self):
        s = cond.summarize({"a_key_that_never_existed": "replace",
                            self.KEYS[0]: "good"}, cl.CATEGORIES)
        self.assertEqual(s["assessed_count"], 1)
        self.assertEqual(s["work_count"], 0)

    def test_invalid_values_count_as_not_assessed(self):
        s = cond.summarize({self.KEYS[0]: 2, self.KEYS[1]: "", self.KEYS[2]: None,
                            self.KEYS[3]: "good"}, cl.CATEGORIES)
        self.assertEqual(s["assessed_count"], 1)
        self.assertEqual(s["not_assessed_count"], 31)

    def test_category_rollup(self):
        first = cl.CATEGORIES[0]
        keys = [k for k, _ in first["items"]]
        given = {keys[0]: "replace", keys[1]: "repair", keys[2]: "good"}
        s = cond.summarize(given, cl.CATEGORIES)
        cat = next(c for c in s["categories"] if c["key"] == first["key"])
        self.assertEqual(cat["assessed_count"], 3)
        self.assertEqual(cat["item_count"], len(keys))
        self.assertEqual(cat["work_count"], 2)
        self.assertEqual(cat["worst"], "replace")
        # Every other category is untouched.
        others = [c for c in s["categories"] if c["key"] != first["key"]]
        self.assertTrue(all(c["assessed_count"] == 0 for c in others))
        self.assertTrue(all(c["worst"] is None for c in others))

    def test_category_counts_sum_to_the_overall_counts(self):
        given = {k: c for k, c in zip(cl.ITEM_KEYS,
                                      list(cond.CONDITIONS) * 10)}
        s = cond.summarize(given, cl.CATEGORIES)
        for state in cond.CONDITIONS:
            self.assertEqual(sum(c["counts"][state] for c in s["categories"]),
                             s["counts"][state],
                             f"category counts must reconcile for {state}")

    def test_headline_reads_as_english(self):
        self.assertEqual(
            cond.summarize({self.KEYS[0]: "good"}, cl.CATEGORIES)["headline"],
            "1 of 32 assessed — nothing needs work.")
        self.assertEqual(
            cond.summarize({self.KEYS[0]: "repair", self.KEYS[1]: "replace"},
                           cl.CATEGORIES)["headline"],
            "2 of 32 assessed — 1 to repair, 1 to replace.")


class ChecklistTests(unittest.TestCase):
    def test_the_32_items_survived_the_rebuild_unchanged(self):
        """Re-homed, not rebuilt. Item keys are the stable identity of a
        question; renaming one to celebrate a rewrite would silently
        reassign an answer to a different question."""
        self.assertEqual(cl.TOTAL_ITEMS, 32)
        self.assertEqual(len(cl.CATEGORIES), 6)
        for key in ("parking_paving", "foundation", "roof_covering", "hvac_units",
                    "alarms_detectors", "flooring", "hazmat_indicators"):
            self.assertIn(key, cl.ITEM_LABELS)

    def test_keys_are_unique_across_categories(self):
        self.assertEqual(len(cl.ITEM_KEYS), len(set(cl.ITEM_KEYS)))

    def test_scope_is_property(self):
        self.assertEqual(cl.SCOPE, cond.SCOPE_PROPERTY)

    def test_the_old_numeric_api_is_gone(self):
        for gone in ("score_assessment", "SCORE_LABELS", "RISK_BANDS",
                     "CRITICAL_THRESHOLD", "valid_score", "risk_band"):
            self.assertFalse(hasattr(cl, gone), f"{gone} should have been removed")

    def test_checklist_version_advanced(self):
        """The stored version stamps which question set an assessment was
        taken against. The scale changed, so the version had to move."""
        self.assertGreaterEqual(cl.CHECKLIST_VERSION, 2)


if __name__ == "__main__":
    unittest.main()
