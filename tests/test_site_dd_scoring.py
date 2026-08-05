"""
Unit tests for tools/site_dd_checklist.py scoring.

Same discipline as tests/test_deal_analyzer_math.py: assertions restate the
arithmetic independently rather than comparing against whatever the module
happened to produce, so a copy-paste error cannot validate itself. A wrong
inspection score is silently plausible -- nothing about a 3.8 looks wrong --
which is exactly why the roll-up is tested directly rather than only
through the UI.
"""

import unittest

from tools import site_dd_checklist as cl


def all_scored(value):
    """Every one of the 32 items scored the same."""
    return {k: value for k in cl.ITEM_KEYS}


class TestChecklistShape(unittest.TestCase):
    def test_thirty_two_items_across_six_categories(self):
        self.assertEqual(len(cl.CATEGORIES), 6)
        self.assertEqual(cl.TOTAL_ITEMS, 32)
        self.assertEqual(len(cl.ITEM_KEYS), 32)

    def test_item_keys_are_unique(self):
        self.assertEqual(len(set(cl.ITEM_KEYS)), len(cl.ITEM_KEYS))

    def test_every_item_maps_to_its_category(self):
        for cat in cl.CATEGORIES:
            for key, _ in cat["items"]:
                self.assertEqual(cl.ITEM_CATEGORY[key], cat["key"])

    def test_checklist_version_is_stamped(self):
        self.assertIsInstance(cl.CHECKLIST_VERSION, int)


class TestRiskBands(unittest.TestCase):
    def test_band_boundaries_are_contiguous(self):
        cases = [
            (5.00, "Low"), (4.50, "Low"),
            (4.49, "Moderate"), (3.50, "Moderate"),
            (3.49, "Elevated"), (2.50, "Elevated"),
            (2.49, "High"), (1.00, "High"),
        ]
        for score, expected in cases:
            with self.subTest(score=score):
                self.assertEqual(cl.risk_band(score), expected)

    def test_nothing_scored_is_not_assessed_not_high(self):
        """A blank form is an absence of information, not a bad building."""
        self.assertEqual(cl.risk_band(None), cl.NOT_ASSESSED)
        self.assertEqual(cl.score_assessment({})["risk_band"], cl.NOT_ASSESSED)


class TestOverallAndCategoryAverages(unittest.TestCase):
    def test_uniform_scores_average_to_that_score(self):
        for v in (1, 3, 5):
            with self.subTest(score=v):
                r = cl.score_assessment(all_scored(v))
                self.assertAlmostEqual(r["overall"], float(v))
                for c in r["categories"]:
                    self.assertAlmostEqual(c["score"], float(v))

    def test_overall_is_item_weighted_not_category_weighted(self):
        """The two differ whenever categories have unequal item counts.
        Structural & Envelope has 6 items, Site & Exterior has 5 -- score
        one 5 and the other 1 and the item-weighted mean must land nearer
        the 6-item category."""
        items = {}
        for k, _ in cl.CATEGORIES[0]["items"]:   # site_exterior, 5 items
            items[k] = 1
        for k, _ in cl.CATEGORIES[1]["items"]:   # structural_envelope, 6 items
            items[k] = 5
        r = cl.score_assessment(items)
        expected_item_weighted = (5 * 1 + 6 * 5) / 11
        expected_category_mean = (1 + 5) / 2
        self.assertAlmostEqual(r["overall"], expected_item_weighted)
        self.assertNotAlmostEqual(r["overall"], expected_category_mean)

    def test_category_score_is_mean_of_its_own_items_only(self):
        items = {"parking_paving": 5, "drainage_grading": 3, "foundation": 1}
        r = cl.score_assessment(items)
        by_key = {c["key"]: c for c in r["categories"]}
        self.assertAlmostEqual(by_key["site_exterior"]["score"], 4.0)      # (5+3)/2
        self.assertAlmostEqual(by_key["structural_envelope"]["score"], 1.0)
        self.assertIsNone(by_key["mep"]["score"])                          # untouched
        self.assertAlmostEqual(r["overall"], 3.0)                          # (5+3+1)/3

    def test_mixed_scores_average_correctly(self):
        items = dict(zip(cl.ITEM_KEYS, [5, 4, 3, 2, 1] * 7))  # 35 -> first 32 used
        r = cl.score_assessment(items)
        expected = sum(items[k] for k in cl.ITEM_KEYS) / 32
        self.assertAlmostEqual(r["overall"], expected)
        self.assertEqual(r["scored_count"], 32)


class TestNAExclusion(unittest.TestCase):
    def test_na_items_excluded_from_averages(self):
        """N/A must not be averaged as zero or as a middling three."""
        items = {"parking_paving": 4, "drainage_grading": None, "landscaping": 2}
        r = cl.score_assessment(items)
        by_key = {c["key"]: c for c in r["categories"]}
        self.assertAlmostEqual(by_key["site_exterior"]["score"], 3.0)   # (4+2)/2, not /3
        self.assertAlmostEqual(r["overall"], 3.0)
        self.assertEqual(r["scored_count"], 2)
        self.assertEqual(r["na_count"], 1)

    def test_all_na_behaves_like_nothing_scored(self):
        r = cl.score_assessment({k: None for k in cl.ITEM_KEYS})
        self.assertIsNone(r["overall"])
        self.assertEqual(r["risk_band"], cl.NOT_ASSESSED)
        self.assertEqual(r["scored_count"], 0)
        self.assertEqual(r["na_count"], 32)

    def test_na_does_not_count_toward_completion(self):
        items = {k: None for k in cl.ITEM_KEYS}
        items["foundation"] = 4
        r = cl.score_assessment(items)
        self.assertEqual(r["scored_count"], 1)
        self.assertAlmostEqual(r["completion_pct"], 1 / 32 * 100)

    def test_invalid_scores_are_treated_as_unscored(self):
        for bad in (0, 6, -1, "4", 4.5, True):
            with self.subTest(value=bad):
                r = cl.score_assessment({"foundation": bad})
                self.assertEqual(r["scored_count"], 0, f"{bad!r} was accepted")


class TestCriticalFindings(unittest.TestCase):
    def test_counts_ones_and_twos_only(self):
        items = {"foundation": 1, "roof_covering": 2, "framing_walls": 3,
                 "windows_doors": 4, "facade_siding": 5}
        r = cl.score_assessment(items)
        self.assertEqual(r["critical_count"], 2)
        self.assertEqual(r["critical_items"], ["foundation", "roof_covering"])

    def test_none_when_all_healthy(self):
        self.assertEqual(cl.score_assessment(all_scored(3))["critical_count"], 0)

    def test_all_critical(self):
        r = cl.score_assessment(all_scored(1))
        self.assertEqual(r["critical_count"], 32)
        self.assertEqual(r["risk_band"], "High")

    def test_per_category_critical_counts(self):
        items = {"foundation": 1, "roof_covering": 1, "parking_paving": 2}
        by_key = {c["key"]: c for c in cl.score_assessment(items)["categories"]}
        self.assertEqual(by_key["structural_envelope"]["critical_count"], 2)
        self.assertEqual(by_key["site_exterior"]["critical_count"], 1)
        self.assertEqual(by_key["mep"]["critical_count"], 0)

    def test_good_average_can_still_hide_criticals(self):
        """The reason critical count is reported separately: a healthy
        average and a life-safety failure must be distinguishable."""
        items = all_scored(5)
        items["alarms_detectors"] = 1
        r = cl.score_assessment(items)
        self.assertGreater(r["overall"], 4.5)
        self.assertEqual(r["risk_band"], "Low")
        self.assertEqual(r["critical_count"], 1)


class TestCompletion(unittest.TestCase):
    def test_full_and_empty(self):
        self.assertAlmostEqual(cl.score_assessment(all_scored(4))["completion_pct"], 100.0)
        self.assertAlmostEqual(cl.score_assessment({})["completion_pct"], 0.0)

    def test_partial(self):
        items = {k: 4 for k in cl.ITEM_KEYS[:8]}
        r = cl.score_assessment(items)
        self.assertEqual(r["scored_count"], 8)
        self.assertAlmostEqual(r["completion_pct"], 25.0)

    def test_unknown_keys_ignored_not_counted(self):
        """A stale key from a future checklist revision must not inflate
        completion or break scoring."""
        items = {"foundation": 4, "no_such_item_v2": 5}
        r = cl.score_assessment(items)
        self.assertEqual(r["scored_count"], 1)
        self.assertAlmostEqual(r["overall"], 4.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
