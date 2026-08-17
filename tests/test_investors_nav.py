"""
The Investors navigation section, and the entry point that was missing.

WHAT WAS WRONG

The notetaker had no way in. Not a buried link or an unclear label -- no
nav item, no dashboard card, and no href to it from any template in the
app outside its own pages. It was reachable only by typing the URL.

That is why Michelle asked for two features that already existed and
worked: uploading transcripts for a date range, and adding a property.
Both live on that page. Both had been built and verified by driving the
routes directly, which is exactly the blind spot that let an unreachable
page look finished.

THE ACTIVE-STATE HALF

/tools/investor-report is shared by two blueprints -- investor_report for
the waterfall and investor_notes for the notetaker. Before the nav entry
existed, the notetaker page highlighted NOTHING in the sidebar, because
the only Investors link matched on `request.blueprint == 'investor_report'`.
With two links, the risk inverts: an expression that is too loose (say,
matching the URL prefix) would light up both at once, which is the bug
already fixed once on the admin section.

So the assertion here is exactly one active item per page, checked on
both pages rather than on the one that happened to work.
"""

import os
import re
import tempfile
import unittest
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="investors-nav-")
for _var in ("SITE_DD_DB_PATH", "DEAL_DIVE_DB_PATH", "RENT_COMPS_DB_PATH",
             "MARKET_DATA_DB_PATH", "UNDERWRITING_DB_PATH",
             "SCORECARD_PRO_DB_PATH", "FIRE_METRICS_DB_PATH",
             "FEEDBACK_DB_PATH", "INVESTOR_REPORT_DB_PATH",
             "INVESTOR_NOTES_DB_PATH", "OPENAI_USAGE_DB_PATH",
             "APP_SETTINGS_DB_PATH"):
    os.environ[_var] = os.path.join(_SANDBOX, _var.lower() + ".db")
os.environ.setdefault("UPLOAD_FOLDER_PATH", os.path.join(_SANDBOX, "uploads"))

ROOT = Path(__file__).resolve().parent.parent

WATERFALL = "/tools/investor-report/"
NOTETAKER = "/tools/investor-report/notes"


class InvestorsNavTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import app
        app.config["WTF_CSRF_ENABLED"] = False
        cls.app = app

    def page(self, url):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = self.app.config.get("ADMIN_USERNAME")
            s["_fresh"] = True
        return c.get(url, follow_redirects=True).get_data(as_text=True)

    def investors_section(self, html):
        m = re.search(
            r'<span class="nav-section-label">Investors</span>(.*?)</div>',
            html, re.S)
        self.assertIsNotNone(m, "the Investors nav section is missing")
        return m.group(1)

    def entries(self, html):
        return re.findall(r'href="([^"]+)"[^>]*class="nav-link ([^"]*)"',
                          self.investors_section(html))

    def test_the_section_has_both_tools(self):
        hrefs = [h for h, _ in self.entries(self.page(WATERFALL))]
        self.assertEqual(len(hrefs), 2, hrefs)
        self.assertTrue(any(h.rstrip("/").endswith("investor-report")
                            for h in hrefs), hrefs)
        self.assertTrue(any(h.endswith("/notes") for h in hrefs), hrefs)

    def test_exactly_one_is_active_on_the_waterfall_page(self):
        entries = self.entries(self.page(WATERFALL))
        active = [h for h, cls in entries if "active" in cls]
        self.assertEqual(len(active), 1, active)
        self.assertTrue(active[0].rstrip("/").endswith("investor-report"))

    def test_exactly_one_is_active_on_the_notetaker_page(self):
        """Before the second link existed, this page highlighted nothing."""
        entries = self.entries(self.page(NOTETAKER))
        active = [h for h, cls in entries if "active" in cls]
        self.assertEqual(len(active), 1, active)
        self.assertTrue(active[0].endswith("/notes"))

    def test_neither_link_double_highlights(self):
        for url in (WATERFALL, NOTETAKER):
            with self.subTest(url=url):
                entries = self.entries(self.page(url))
                self.assertEqual(
                    sum("active" in cls for _, cls in entries), 1)

    def test_the_notetaker_is_reachable_from_every_page(self):
        """The gap this closes: it was reachable only by typing the URL."""
        for url in (WATERFALL, "/dashboard", "/tools/underwriting/",
                    "/tools/site-dd/"):
            with self.subTest(url=url):
                self.assertIn("/tools/investor-report/notes", self.page(url))


class WaterfallCrossLinkTests(unittest.TestCase):
    """The link on the page she was actually standing on."""

    @classmethod
    def setUpClass(cls):
        from app import app
        app.config["WTF_CSRF_ENABLED"] = False
        cls.app = app

    def body(self, url=WATERFALL):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = self.app.config.get("ADMIN_USERNAME")
            s["_fresh"] = True
        html = c.get(url, follow_redirects=True).get_data(as_text=True)
        return html.split('<main class="main-content">')[-1]

    def test_the_page_body_links_to_the_notetaker(self):
        hrefs = set(re.findall(r'href="([^"]+)"', self.body()))
        self.assertIn("/tools/investor-report/notes", hrefs, sorted(hrefs))

    def test_it_says_what_the_notetaker_is_for(self):
        body = self.body().lower()
        for phrase in ("transcript", "date range"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, body)

    def test_it_mentions_adding_a_property(self):
        """The second feature she asked for lives on that page too."""
        self.assertIn("add a property", self.body().lower())

    def test_it_survives_a_deal_being_selected(self):
        hrefs = set(re.findall(r'href="([^"]+)"',
                               self.body(WATERFALL + "?deal_id=2")))
        self.assertIn("/tools/investor-report/notes", hrefs)


class NothingElseLostItsEntryPointTests(unittest.TestCase):
    """A control on the class of bug, not just this instance.

    Every GET page a person is meant to browse to should be reachable
    from the navigation. This asserts it for the two Investors pages
    rather than for the whole app, because that is the section that
    demonstrably had the problem.
    """

    def test_both_investor_pages_are_linked_from_the_shell(self):
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        for endpoint in ("investor_report.index", "investor_notes.index"):
            with self.subTest(endpoint=endpoint):
                self.assertIn(endpoint, base)


if __name__ == "__main__":
    unittest.main()
