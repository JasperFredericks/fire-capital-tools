"""The disclosure states what the comparables actually are.

WHAT WAS WRONG, AND HOW IT GOT SHIPPED

The first version asserted, unconditionally, that "the comparables below
are matched to that size -- which is why changing the bedroom filter may
not change the rows."

That was inferred from four cached addresses where it happened to hold,
and it is false on three of the seven we have:

    1029 s jackson st        subject 0bd   15 of 15 same size   holds
    11602 apex view dr       subject 1bd   15 of 15 same size   holds
    19 bay vista drive       subject 4bd   15 of 15 same size   holds
    480 warren dr            subject 0bd   15 of 15 same size   holds
    5208 11th street lubbock subject 1bd    2 of 15 same size   FALSE
    598 belvedere            subject 5bd    3 of 15 same size   FALSE
    598 belvedere street     subject 5bd    3 of 15 same size   FALSE

On Lubbock the page told the reader the comparables were matched to a
1-bed when two of fifteen were, and told them the filter would not change
the rows when it changes thirteen of them.

This was written one step after flagging the identical error in Scorecard
Pro's warnings card, which asserts "this file does not state Gross
Potential Rent" when what the code knows is that nothing matched account
4110. Same failure: a cause inferred while writing, from a sample that
agreed.

THE RULE THESE TESTS ENFORCE

The page may only state what it can count. Composition is computed from
the comparables actually rendered, and the three cases -- all, none, some
-- are asserted against the real cached shapes rather than a
representative one. The sample agreeing is precisely how this shipped.
"""

import unittest
from pathlib import Path

from jinja2 import Environment, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "templates" / "tools" / "rent_comps.html"

# Every cached address, with its real subject size and comp bed counts.
REAL_SHAPES = {
    "1029 s jackson st":        (0, [0] * 15),
    "11602 apex view dr":       (1, [1] * 15),
    "19 bay vista drive":       (4, [4] * 15),
    "480 warren dr":            (0, [0] * 15),
    "5208 11th street lubbock": (1, [0] + [1] * 2 + [2] * 12),
    "598 belvedere":            (5, [1] * 5 + [3] * 2 + [4] * 4 + [5] * 3 + [None]),
    "598 belvedere street":     (5, [1] * 5 + [3] * 2 + [4] * 4 + [5] * 3 + [None]),
}


def render(subject_beds, comp_beds, sqft=433, ptype="Apartment"):
    src = TPL.read_text(encoding="utf-8")
    start = src.index("{% if rentcast.property and rentcast.property.bedrooms is not none %}")
    end = src.index("{% endif %}", src.index("no size for this address")) + len("{% endif %}")
    env = Environment(autoescape=select_autoescape(["html"]))
    # The real "no size" shape is a subjectProperty that EXISTS with
    # bedrooms None -- not an absent property, which renders nothing at all.
    prop = {"bedrooms": subject_beds, "square_footage": sqft,
            "property_type": ptype}
    return " ".join(env.from_string(src[start:end]).render(
        rentcast={"property": prop},
        candidates=[{"bedrooms": b} for b in comp_beds],
    ).split())


class EveryRealShapeIsDescribedTruthfullyTests(unittest.TestCase):
    """All seven, because a sample agreeing is how the bug shipped."""

    def test_uniform_sets_say_the_filter_will_not_change_the_rows(self):
        for name in ("1029 s jackson st", "11602 apex view dr",
                     "19 bay vista drive", "480 warren dr"):
            with self.subTest(name):
                beds, comps = REAL_SHAPES[name]
                html = render(beds, comps)
                self.assertIn(f"All {len(comps)} comparables below are that size", html)
                self.assertIn("will not change the rows", html)

    def test_mixed_sets_say_the_filter_will_change_the_rows(self):
        for name in ("5208 11th street lubbock", "598 belvedere",
                     "598 belvedere street"):
            with self.subTest(name):
                beds, comps = REAL_SHAPES[name]
                html = render(beds, comps)
                self.assertIn("will change which rows", html)
                self.assertNotIn("will not change the rows", html)

    def test_mixed_sets_state_the_real_count(self):
        beds, comps = REAL_SHAPES["5208 11th street lubbock"]
        self.assertIn("2 of the 15 comparables below are that size",
                      render(beds, comps))

    def test_belvedere_states_its_real_count(self):
        beds, comps = REAL_SHAPES["598 belvedere"]
        self.assertIn("3 of the 15 comparables below are that size",
                      render(beds, comps))

    def test_the_old_false_claim_is_gone_everywhere(self):
        """The exact sentence that was wrong on three of seven."""
        for name, (beds, comps) in REAL_SHAPES.items():
            with self.subTest(name):
                self.assertNotIn("the comparables below are matched to that size",
                                 render(beds, comps))


class TheEdgeCasesTests(unittest.TestCase):
    def test_no_matching_comps_at_all_is_stated_plainly(self):
        html = render(2, [1] * 15)
        self.assertIn("None of the 15 comparables below are that size", html)

    def test_no_comparables_makes_no_claim_about_them(self):
        html = render(1, [])
        self.assertIn("RentCast matched this address", html)
        for phrase in ("comparables below are that size", "change the rows"):
            with self.subTest(phrase):
                self.assertNotIn(phrase, html)

    def test_an_absent_subject_size_still_gets_its_own_message(self):
        html = render(None, [1, 2, 3])
        self.assertIn("no size for this address", html)
        self.assertNotIn("comparables below are that size", html)

    def test_a_studio_is_still_named_a_studio(self):
        self.assertIn("a studio", render(0, [0] * 3))

    def test_the_estimate_warning_survives_in_every_case(self):
        for beds, comps in ((0, [0] * 3), (1, [2] * 3), (2, [])):
            with self.subTest(beds=beds):
                self.assertIn("the rent estimate is for that size",
                              render(beds, comps).lower())


class NothingIsAssertedThatCannotBeCountedTests(unittest.TestCase):
    def test_the_claim_tracks_the_data_not_a_constant(self):
        """Same subject size, opposite comp sets, opposite sentences."""
        uniform = render(1, [1] * 15)
        mixed = render(1, [1] * 2 + [2] * 13)
        self.assertIn("will not change the rows", uniform)
        self.assertIn("will change which rows", mixed)

    def test_the_composition_is_computed_from_rendered_candidates(self):
        src = TPL.read_text(encoding="utf-8")
        self.assertIn("selectattr('bedrooms', 'equalto', _b)", src)


if __name__ == "__main__":
    unittest.main()
