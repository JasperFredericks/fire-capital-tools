"""What RentCast decided the subject is, said on the page.

WHY THIS EXISTS

RentCast's rent endpoint resolves an address to ONE unit and returns
comparables matched to that unit's size. On a multi-unit building it
picks one. 1029 S Jackson St came back as a studio, 433 sqft, and all
fifteen comparables came back studios.

Two consequences were invisible on this page:

  * The bedroom filter appears to do nothing, because every row is
    already that size. A tester hit exactly this and reported the control
    as broken. It is not -- both selects are client-side and wired
    identically; there was simply nothing to filter out.
  * The rent estimate is an estimate FOR THAT SIZE. Apex View resolved to
    a 1-bed and returned $1,010, which is a correct number about a
    possibly wrong apartment.

The fix is disclosure, not correction. Correcting it means re-requesting
against a chosen size or fetching the building's floorplans, and both
spend RentCast quota against a 50/month cap. Those are Michelle's calls.

THE STUDIO BUG THIS ALSO CLOSES

`{{ bedrooms or '—' }}` swallows a studio, because 0 is falsy. A real
studio rendered identically to "RentCast told us nothing" -- on the exact
address the tester was looking at.
"""

import os
import re
import tempfile
import unittest
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="rc-disclosure-")
for _var in ("SITE_DD_DB_PATH", "DEAL_DIVE_DB_PATH", "RENT_COMPS_DB_PATH",
             "MARKET_DATA_DB_PATH", "UNDERWRITING_DB_PATH",
             "SCORECARD_PRO_DB_PATH", "FIRE_METRICS_DB_PATH",
             "FEEDBACK_DB_PATH", "INVESTOR_REPORT_DB_PATH",
             "INVESTOR_NOTES_DB_PATH", "OPENAI_USAGE_DB_PATH",
             "APP_SETTINGS_DB_PATH"):
    os.environ[_var] = os.path.join(_SANDBOX, _var.lower() + ".db")
os.environ.setdefault("UPLOAD_FOLDER_PATH", os.path.join(_SANDBOX, "uploads"))

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "templates" / "tools" / "rent_comps.html"

from jinja2 import Environment, FileSystemLoader, select_autoescape  # noqa: E402


def render_block(prop, comps=None):
    """Render just the disclosure, with the surrounding page stubbed out."""
    src = TPL.read_text(encoding="utf-8")
    start = src.index("{% if rentcast.property and rentcast.property.bedrooms is not none %}")
    end = src.index("{% endif %}", src.index("no size for this address")) + len("{% endif %}")
    env = Environment(autoescape=select_autoescape(["html"]))
    return env.from_string(src[start:end]).render(
        rentcast={"property": prop},
        candidates=[{"bedrooms": b} for b in (comps or [])])


class TheDisclosureTests(unittest.TestCase):
    def test_a_studio_is_named_a_studio(self):
        html = render_block({"bedrooms": 0, "square_footage": 433,
                             "property_type": "Apartment"})
        self.assertIn("a studio", html)
        self.assertIn("433", html)

    def test_a_one_bed_reads_naturally(self):
        html = render_block({"bedrooms": 1, "square_footage": 756,
                             "property_type": "Apartment"})
        self.assertIn("a 1-bedroom", html)

    def test_larger_units_read_naturally(self):
        self.assertIn("a 3-bedroom", render_block({"bedrooms": 3}))

    def test_it_explains_what_the_filter_will_do(self):
        """The thing the tester lost time to.

        The wording moved from an unconditional "may not change the rows"
        to a statement computed from the comparables actually present --
        the unconditional form was false on three of seven cached
        addresses. tests/test_rentcast_disclosure_honesty.py covers the
        three cases against every real shape; this asserts only that the
        page still answers the question at all.
        """
        html = render_block({"bedrooms": 0}, comps=[0, 0, 0]).lower()
        self.assertIn("change the rows", html)

    def test_it_warns_the_estimate_is_for_that_size(self):
        html = render_block({"bedrooms": 1}).lower()
        self.assertIn("estimate is for that size", html)
        self.assertIn("read low", html)

    def test_no_size_gets_its_own_message_not_a_wrong_one(self):
        html = render_block({"bedrooms": None}).lower()
        self.assertIn("no size for this address", html)
        self.assertNotIn("may not change the rows", html)


class TheStudioRenderingBugTests(unittest.TestCase):
    """0 is falsy. `or '—'` turned a real studio into 'unknown'."""

    def subject_box(self, prop):
        src = TPL.read_text(encoding="utf-8")
        i = src.index('{% if rentcast.property.bedrooms is none %}')
        j = src.index("</div>", i)
        env = Environment(autoescape=select_autoescape(["html"]))
        return env.from_string(src[i:j]).render(rentcast={"property": prop})

    def test_a_studio_does_not_render_as_unknown(self):
        out = self.subject_box({"bedrooms": 0, "bathrooms": 1,
                                "square_footage": 433})
        self.assertIn("Studio", out)
        self.assertNotIn("—bd", out)

    def test_genuinely_unknown_still_renders_a_dash(self):
        out = self.subject_box({"bedrooms": None, "bathrooms": None,
                                "square_footage": None})
        self.assertIn("—bd", out)

    def test_a_normal_bed_count_is_unchanged(self):
        self.assertIn("2bd", self.subject_box(
            {"bedrooms": 2, "bathrooms": 1, "square_footage": 900}))

    def test_zero_bathrooms_and_sqft_are_not_swallowed_either(self):
        """Same falsy-zero class of bug, same fix."""
        out = self.subject_box({"bedrooms": 1, "bathrooms": 0,
                                "square_footage": 0})
        self.assertIn("0ba", out)
        self.assertIn("0 sqft", out)


class NoQuotaWasSpentTests(unittest.TestCase):
    """Display-only. This fix must not add a RentCast call."""

    def test_the_template_makes_no_request(self):
        src = TPL.read_text(encoding="utf-8")
        block = src[src.index("RentCast matched this address"):]
        block = block[:block.index("{% endif %}")]
        for forbidden in ("url_for('rent_comps.refresh", "fetch(", "XMLHttpRequest"):
            with self.subTest(forbidden):
                self.assertNotIn(forbidden, block)

    def test_the_service_still_sends_only_the_address(self):
        """Fix 2 and 3 would add parameters here. Neither was built."""
        src = (ROOT / "tools" / "market_data_service.py").read_text(encoding="utf-8")
        call = src[src.index("avm/rent/long-term"):]
        call = call[:call.index("timeout=")]
        self.assertIn('"address": full_address', call)
        self.assertNotIn("bedrooms", call)


if __name__ == "__main__":
    unittest.main()
