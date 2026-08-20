"""The occupancy gauge is data-bound, and its geometry was wrong.

WHAT WAS REPORTED AND WHAT WAS ACTUALLY BROKEN

Reported as "the occupancy icon is static and doesn't match the
percentage", with a request to make it a dynamic gauge. It was already
dynamic: the JS reads physical_occupancy_pct, converts it to degrees, and
sets --occ-deg, which the CSS consumes through a conic-gradient. So this
was never a decorative placeholder needing to be made live -- it was a
live gauge rendering wrongly, which is a defect fix rather than the design
change the request sounded like.

THE GEOMETRY

A conic-gradient starts at 12 o'clock and sweeps clockwise, so a plain
0deg-180deg sweep paints the RIGHT half of the circle. The gauge box is
100x54 with overflow hidden -- the TOP half. Those overlap only in the
top-right quadrant.

Consequences, both visible in the reported symptom:

  * the grey track rendered as a quarter arc rather than a half
  * the fill stopped moving at 50%, because everything past 90deg of the
    sweep fell below the visible area

Every one of Michelle's properties sits above 50% occupancy, so every
gauge drew an identical full quarter regardless of the number beside it.
"Static and doesn't match the percentage" is a literally accurate
description.

`from -90deg` puts 0deg at 9 o'clock, so the 180deg sweep runs left ->
top -> right and lands exactly on the visible semicircle.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
TPL = (ROOT / "templates" / "tools" / "mmr_summary.html").read_text(encoding="utf-8")


def rule(selector, must_contain=None):
    """The body of one CSS rule.

    `must_contain` picks the right one when a selector appears in more
    than one rule -- .occ-gauge-fill is both its own rule and part of a
    shared `.occ-gauge-bg, .occ-gauge-fill` block that sets geometry.
    """
    start = 0
    while True:
        i = CSS.index(selector + " {", start)
        body = CSS[i:CSS.index("}", i)]
        if must_contain is None or must_contain in body:
            return body
        start = i + 1


class TheGaugeIsDataBoundTests(unittest.TestCase):
    """It always was. Recording that, because 'make it dynamic' implies
    otherwise and the distinction changes what the fix is."""

    def test_the_script_reads_the_occupancy_figure(self):
        self.assertIn("s.physical_occupancy_pct", TPL)

    def test_it_sets_the_custom_property_the_css_consumes(self):
        self.assertIn('setProperty("--occ-deg"', TPL)
        self.assertIn("var(--occ-deg", CSS)

    def test_the_full_sweep_is_a_half_circle(self):
        """100% occupancy must map to 180 degrees, not 360."""
        m = re.search(r"physical_occupancy_pct\s*/\s*100\)\s*\*\s*(\d+)", TPL)
        self.assertIsNotNone(m, "the percent-to-degrees mapping moved")
        self.assertEqual(m.group(1), "180")

    def test_it_is_clamped_to_the_visible_arc(self):
        self.assertIn("Math.min(180", TPL)
        self.assertIn("Math.max(0", TPL)


class TheSweepLandsOnTheVisibleHalfTests(unittest.TestCase):
    def test_the_track_starts_at_nine_oclock(self):
        self.assertIn("from -90deg", rule(".occ-gauge-bg", "conic-gradient"))

    def test_the_fill_starts_at_nine_oclock(self):
        self.assertIn("from -90deg", rule(".occ-gauge-fill", "conic-gradient"))

    def test_neither_gradient_uses_the_default_start(self):
        """A bare conic-gradient here is the bug: it paints the right half
        of the circle while the visible box is the top half."""
        for selector in (".occ-gauge-bg", ".occ-gauge-fill"):
            with self.subTest(selector=selector):
                body = rule(selector, "conic-gradient")
                # Assert the positive: every conic-gradient in this rule
                # names its start angle. A negative lookahead backtracks
                # over the newline and passes vacuously.
                opens = body.count("conic-gradient(")
                rotated = len(re.findall(r"conic-gradient\(\s*from\s", body))
                self.assertEqual(rotated, opens,
                                 "a conic-gradient here has no start angle")

    def test_the_box_really_is_a_half_circle(self):
        """The geometry the rotation is compensating for. If this ever
        becomes a full circle, the rotation has to be revisited."""
        body = rule(".occ-gauge")
        self.assertIn("width: 100px", body)
        self.assertIn("height: 54px", body)
        self.assertIn("overflow: hidden", body)


class TheArcMovesAcrossTheWholeRangeTests(unittest.TestCase):
    """The symptom, expressed as arithmetic.

    Before the fix only the first 90deg of the sweep was visible, so any
    occupancy at or above 50% produced an identical picture.
    """

    @staticmethod
    def visible_before(pct):
        return min(pct / 100 * 180, 90)

    @staticmethod
    def visible_after(pct):
        return pct / 100 * 180

    def test_the_old_geometry_flatlined_above_half(self):
        self.assertEqual(self.visible_before(50), self.visible_before(88))
        self.assertEqual(self.visible_before(88), self.visible_before(100))

    def test_the_new_geometry_distinguishes_every_real_occupancy(self):
        """The four properties' actual figures must render differently."""
        seen = {round(self.visible_after(p), 3)
                for p in (86.2, 88.0, 89.1, 91.3)}
        self.assertEqual(len(seen), 4)

    def test_zero_and_full_are_the_ends_of_the_arc(self):
        self.assertEqual(self.visible_after(0), 0)
        self.assertEqual(self.visible_after(100), 180)

    def test_the_centre_cutout_is_still_a_circle(self):
        """An earlier fix made this round; it must not regress to an egg."""
        body = CSS[CSS.index(".occ-gauge::after"):]
        body = body[:body.index("}")]
        self.assertIn("width: 72px", body)
        self.assertIn("height: 72px", body)


if __name__ == "__main__":
    unittest.main()
