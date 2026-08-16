"""
Turnover items are capital, not operating.

Eagle Rock's T12 gave Underwriting a 68.58% expense ratio where the file's
own rollup parents -- what Scorecard Pro reads and reports -- say 59.79%.
Michelle named the cause: flooring and appliance turnover was being
counted as operating expense.

Four lines carry $97,665.38 of the $107,606.05 gap. Moving them gives
60.60%, which is 0.81 points off the file's own figure instead of 8.79.
Those are the numbers pinned below, taken from the real T12 rather than a
fixture that resembles it.

The other half of this file is about what must NOT move. A substring
match on "carpet" or "appliance" would also take repairs, cleaning and
consumables, understate operating expense and overstate NOI -- the same
error in the opposite direction, and worse, because it flatters the deal.
"""

import unittest

from tools import underwriting_math as um
from tools import underwriting_turnover as ut

# Eagle Rock, from the real T12 on production.
EGI = 1_223_671.52
BEFORE_OPEX = 839_216.14
FILE_OPEX = 731_610.09          # the T12's own 6000 + 7000

TURNOVER = {
    "Appliances": 33_991.30,
    "Floor Covering - Carpet": 27_294.12,
    "Countertop and Tub Resurfacing": 24_602.96,
    "Floor Covering - Vinyl / Tile / Wood": 11_777.00,
}
STAYS_OPERATING = {
    "Carpet Repair & Cleaning (occupied)": 1_272.26,
    "Contract Carpet Cleaning": 2_273.26,
    "Appliance Repair and Supplies": 343.46,
    "Appliance Parts & Supplies": 124.53,
    "Subfloor Repairs": 387.99,
    "Paint & Sheetrock - Interior": 21_217.12,
}
ALREADY_CAPEX = {
    "HVAC Replacement": 33_053.20,
    "Interior Rehab": 278_977.73,
    "Cabinets & Countertop Replacement": 7_286.87,
    "Blinds - Wallpaper Replacement": 11.96,
}


def line(label, amount, kind="operating", included=True):
    return {"label": label, "annual_amount": amount, "line_kind": kind,
            "is_included": included, "category_key": "x", "gl_code": "6100"}


class WhatMovesTests(unittest.TestCase):
    def test_each_turnover_line_is_reclassified(self):
        for label, amount in TURNOVER.items():
            with self.subTest(label=label):
                out = ut.reclassify([line(label, amount)])[0]
                self.assertEqual(out["line_kind"], "capex")
                self.assertFalse(out["is_included"])

    def test_the_real_four_move_the_real_amount(self):
        lines = [line(l, a) for l, a in TURNOVER.items()]
        moved = ut.summarize(lines, ut.reclassify(lines))
        self.assertEqual(moved["count"], 4)
        self.assertAlmostEqual(moved["amount"], 97_665.38, places=2)


class WhatMustNotMoveTests(unittest.TestCase):
    """Repairs, cleaning and consumables are operating expenses."""

    def test_maintenance_lines_stay_operating(self):
        for label, amount in STAYS_OPERATING.items():
            with self.subTest(label=label):
                out = ut.reclassify([line(label, amount)])[0]
                self.assertEqual(out["line_kind"], "operating", label)
                self.assertTrue(out["is_included"])

    def test_a_repair_of_a_capital_item_is_still_a_repair(self):
        out = ut.reclassify([line("Carpet Repair", 500.0)])[0]
        self.assertEqual(out["line_kind"], "operating")

    def test_interior_paint_is_deliberately_left_alone(self):
        """Moving it too takes the ratio below the file's own figure."""
        opex = BEFORE_OPEX - sum(TURNOVER.values()) - 21_217.12
        self.assertLess(opex / EGI, FILE_OPEX / EGI,
                        "paint would overshoot past the T12's own number")

    def test_it_only_ever_moves_out_of_operating(self):
        for kind in ("capex", "non_operating", "acquisition_cost"):
            with self.subTest(kind=kind):
                out = ut.reclassify([line("Floor Covering - Carpet", 100.0,
                                          kind=kind, included=False)])[0]
                self.assertEqual(out["line_kind"], kind)
                self.assertFalse(out["is_included"])

    def test_lines_the_shared_classifier_already_called_capex_are_untouched(self):
        lines = [line(l, a, kind="capex", included=False)
                 for l, a in ALREADY_CAPEX.items()]
        after = ut.reclassify(lines)
        self.assertEqual([l["line_kind"] for l in after], ["capex"] * len(lines))

    def test_the_input_is_not_mutated(self):
        lines = [line("Appliances", 33_991.30)]
        ut.reclassify(lines)
        self.assertEqual(lines[0]["line_kind"], "operating")
        self.assertTrue(lines[0]["is_included"])


class TheRatioTests(unittest.TestCase):
    """The figure that was actually reported as wrong."""

    def _eagle_rock(self):
        lines = [line(l, a) for l, a in TURNOVER.items()]
        lines += [line(l, a) for l, a in STAYS_OPERATING.items()]
        rest = BEFORE_OPEX - sum(TURNOVER.values()) - sum(STAYS_OPERATING.values())
        lines.append(line("Everything else", rest))
        return lines

    def test_before_the_fix_it_is_the_reported_68_58(self):
        total = um.total_operating_expenses(self._eagle_rock())
        self.assertAlmostEqual(total, BEFORE_OPEX, places=2)
        self.assertAlmostEqual(total / EGI * 100, 68.58, places=2)

    def test_after_the_fix_it_is_60_60(self):
        after = ut.reclassify(self._eagle_rock())
        total = um.total_operating_expenses(after)
        self.assertAlmostEqual(total, 741_550.76, places=2)
        self.assertAlmostEqual(total / EGI * 100, 60.60, places=2)

    def test_the_gap_to_the_file_closes_from_8_79_to_0_81(self):
        file_ratio = FILE_OPEX / EGI * 100
        before = BEFORE_OPEX / EGI * 100
        after = um.total_operating_expenses(
            ut.reclassify(self._eagle_rock())) / EGI * 100
        self.assertAlmostEqual(abs(before - file_ratio), 8.79, places=2)
        self.assertAlmostEqual(abs(after - file_ratio), 0.81, places=2)


class StillOverridableTests(unittest.TestCase):
    """A default, not a rule.

    The control that decides whether a line counts is `is_included` -- the
    checkbox on the expense table -- because operating_expense_lines()
    filters on it. Ticking it puts a reclassified line back into the
    operating total; clearing it takes any line out.
    """

    def test_re_including_a_reclassified_line_counts_it_again(self):
        out = ut.reclassify([line("Appliances", 33_991.30)])
        self.assertEqual(um.total_operating_expenses(out), 0.0)
        out[0]["is_included"] = True
        self.assertEqual(um.total_operating_expenses(out), 33_991.30)

    def test_excluding_an_ordinary_line_takes_it_out(self):
        rows = [line("Property Taxes", 113_961.05)]
        self.assertEqual(um.total_operating_expenses(rows), 113_961.05)
        rows[0]["is_included"] = False
        self.assertEqual(um.total_operating_expenses(rows), 0.0)

    def test_the_expense_form_posts_that_checkbox_per_line(self):
        from pathlib import Path
        body = (Path(__file__).resolve().parent.parent / "templates" / "tools"
                / "underwriting_detail.html").read_text(encoding="utf-8")
        self.assertIn('name="included_{{ l.id }}"', body)


class ScopedToUnderwritingTests(unittest.TestCase):
    """This is Underwriting's judgement, not a change to a shared tool.

    The keyword classifier in scorecard_pro/kpis.py is read by Scorecard
    Pro and the Quick Analyzer as well. Michelle's correction is about how
    Underwriting models a hold; applying it there would have moved three
    tools on the strength of one tool's requirement.
    """

    def _source(self, name):
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent
                / name).read_text(encoding="utf-8")

    def test_the_shared_classifier_is_not_imported_here(self):
        """Asserted on the code, not the prose.

        The module docstring names scorecard_pro/kpis.py deliberately, to
        say why this lives apart from it -- so a substring check would
        fail on its own explanation. What matters is that nothing is
        imported from it and nothing calls into it.
        """
        import ast

        tree = ast.parse(self._source("tools/underwriting_turnover.py"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        self.assertEqual([m for m in imported if "kpis" in m or "scorecard" in m],
                         [], str(imported))
        self.assertEqual(imported, ["__future__", "re", "typing"], str(imported))

    def test_only_underwritings_import_applies_it(self):
        import subprocess
        out = subprocess.run(
            ["git", "grep", "-l", "underwriting_turnover"],
            capture_output=True, text=True).stdout.split()
        callers = [p for p in out if p.startswith("tools/")
                   and p != "tools/underwriting_turnover.py"]
        self.assertEqual(callers, ["tools/underwriting.py"], str(callers))

    def test_the_quick_analyzer_import_is_untouched(self):
        body = self._source("tools/quick_analyzer_t12.py")
        self.assertNotIn("turnover", body)

    def test_scorecard_pros_own_ratio_does_not_use_this(self):
        """Its expense ratio reads the file's rollup parents, 6000+7000."""
        body = self._source("tools/scorecard_pro/kpis.py")
        self.assertNotIn("turnover", body)
        self.assertIn('self.get_val("6000", month)', body)


if __name__ == "__main__":
    unittest.main()
