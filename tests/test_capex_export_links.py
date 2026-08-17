"""The capital budget export, and the fact that nothing linked to it.

FOURTH INSTANCE OF ONE BUG

    feedback_db.list_feedback()   weeks of feedback, no screen showed it
    notes_db.list_updates()       an update was reachable only by the
                                  redirect right after generating it
    the notetaker                 built, tested, linked from nowhere
    site_dd capex export          PDF and XLSX, both live, both routed,
                                  both linked from no template at all

The capital budget was the most complete of the four. It applies
reference costs at export time, keeps inspector estimates ahead of
national averages, carries quantities, splits the total by provenance,
and ships a sheet naming every item it will not price and why. All of it
reachable only by typing /assessment/<id>/capex.xlsx.

So "Needs to save to an exel" was not a feature request. It was a report
that a finished feature is invisible.

WHY THE NAMES ARE ASSERTED, NOT JUST THE HREFS

Linking the budget puts two PDFs on one page. "Download PDF Report" was
a fine name while it was the only document; beside a capital budget it
no longer says which one you get. A link the reader cannot interpret is
the same failure one step later, so the labels are part of the fix and
are tested as such.
"""

import os
import re
import tempfile
import unittest
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="capex-links-")
for _var in ("SITE_DD_DB_PATH", "DEAL_DIVE_DB_PATH", "RENT_COMPS_DB_PATH",
             "MARKET_DATA_DB_PATH", "UNDERWRITING_DB_PATH",
             "SCORECARD_PRO_DB_PATH", "FIRE_METRICS_DB_PATH",
             "FEEDBACK_DB_PATH", "INVESTOR_REPORT_DB_PATH",
             "INVESTOR_NOTES_DB_PATH", "OPENAI_USAGE_DB_PATH",
             "APP_SETTINGS_DB_PATH"):
    os.environ[_var] = os.path.join(_SANDBOX, _var.lower() + ".db")
os.environ.setdefault("UPLOAD_FOLDER_PATH", os.path.join(_SANDBOX, "uploads"))

ROOT = Path(__file__).resolve().parent.parent

from tools import site_dd_db as db          # noqa: E402


def make_assessment():
    with db.get_connection() as conn:
        aid = db.create_assessment(conn, {
            "property_label": "Nabob Hill", "assessed_on": "2026-08-16",
            "inspector": "MJ", "checklist_version": 2})
        area = db.create_area(conn, aid, {"kind": "unit", "label": "1",
                                          "status": "occupied"})
        room = db.create_room(conn, area, "kitchen")
        db.upsert_findings(conn, aid, [{
            "item_key": "walls_ceiling", "scope": "room",
            "area_id": area, "room_id": room, "condition": "repair"}])
    return aid


class TheDetailPageOffersBothExportsTests(unittest.TestCase):
    """Reachability by navigation, not by knowing the URL."""

    @classmethod
    def setUpClass(cls):
        from app import app
        app.config["WTF_CSRF_ENABLED"] = False
        cls.app = app
        cls.aid = make_assessment()

    def page(self, url=None):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = self.app.config.get("ADMIN_USERNAME")
            s["_fresh"] = True
        url = url or f"/tools/site-dd/assessment/{self.aid}"
        return c.get(url, follow_redirects=True).get_data(as_text=True)

    def hrefs(self):
        return set(re.findall(r'href="([^"]+)"', self.page()))

    def test_the_excel_budget_is_linked(self):
        self.assertTrue(
            any(h.endswith("capex.xlsx") for h in self.hrefs()),
            "the Excel capital budget is reachable only by typing the URL")

    def test_the_pdf_budget_is_linked(self):
        self.assertTrue(any(h.endswith("capex.pdf") for h in self.hrefs()))

    def test_the_condition_report_is_still_linked(self):
        self.assertTrue(any("report" in h for h in self.hrefs()))

    def test_the_two_pdfs_have_distinguishable_names(self):
        """Two PDFs on one page: the labels have to say which is which."""
        html = self.page()
        self.assertIn("Condition Report (PDF)", html)
        self.assertIn("Capital Budget (PDF)", html)
        self.assertNotIn("Download PDF Report", html)

    def test_the_page_names_all_three_states(self):
        """Including the one she will actually hit.

        Promising "prices the items needing work" and then opening a
        budget with no total is how a correct tool gets reported as
        broken. Nobody records areas on the walk yet, so the
        researched-rate-awaiting-measurement bucket can be all of it.
        """
        html = self.page().lower()
        self.assertIn("needing work", html)
        self.assertIn("no researched cost", html)
        self.assertIn("needs a measurement", html)
        self.assertIn("not in the total", html)


class TheLinksActuallyDownloadTests(unittest.TestCase):
    """Following the link has to produce the file, not a redirect."""

    @classmethod
    def setUpClass(cls):
        from app import app
        app.config["WTF_CSRF_ENABLED"] = False
        cls.app = app
        cls.aid = make_assessment()

    def get(self, url, follow_redirects=False):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = self.app.config.get("ADMIN_USERNAME")
            s["_fresh"] = True
        return c.get(url, follow_redirects=follow_redirects)

    def test_the_excel_link_returns_a_workbook(self):
        r = self.get(f"/tools/site-dd/assessment/{self.aid}/capex.xlsx")
        self.assertEqual(r.status_code, 200)
        self.assertIn("spreadsheetml", r.mimetype)
        self.assertEqual(r.data[:2], b"PK")

    def test_the_pdf_link_returns_a_pdf(self):
        r = self.get(f"/tools/site-dd/assessment/{self.aid}/capex.pdf")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "application/pdf")
        self.assertEqual(r.data[:4], b"%PDF")

    def test_an_unknown_format_is_refused_not_guessed(self):
        r = self.get(f"/tools/site-dd/assessment/{self.aid}/capex.docx",
                     follow_redirects=True)
        self.assertIn("PDF or Excel", r.get_data(as_text=True))


class NoTemplateStillHidesTheBudgetTests(unittest.TestCase):
    """The structural half, so the link cannot quietly disappear again."""

    def test_a_template_references_the_endpoint(self):
        found = [p.name for p in (ROOT / "templates").rglob("*.html")
                 if "site_dd.capex_budget" in p.read_text(encoding="utf-8")]
        self.assertTrue(found, "no template references site_dd.capex_budget")


if __name__ == "__main__":
    unittest.main()
