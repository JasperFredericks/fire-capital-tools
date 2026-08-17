"""
The admin feedback page.

WHY THIS FILE EXISTS AT ALL

feedback_db.list_feedback() was written and then never called by
anything. The widget on every tool page wrote faithfully to
/data/feedback.db and no screen in the app ever read it back, so the
feature was write-only. Three real entries accumulated unseen, two of
them detailed feature requests, and they were only found by querying the
database by hand.

So the tests that matter here are not really about rendering. They are:

  * a route exists and reads the store
  * it is reachable from the navigation, because a page nobody can find
    reproduces the original failure exactly
  * multi-line messages survive, because the two real requests are
    numbered lists and a collapsed list is a half-read list
"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="admin-feedback-")
for _var in ("SITE_DD_DB_PATH", "DEAL_DIVE_DB_PATH", "RENT_COMPS_DB_PATH",
             "MARKET_DATA_DB_PATH", "UNDERWRITING_DB_PATH",
             "SCORECARD_PRO_DB_PATH", "FIRE_METRICS_DB_PATH",
             "FEEDBACK_DB_PATH", "INVESTOR_REPORT_DB_PATH",
             "INVESTOR_NOTES_DB_PATH", "OPENAI_USAGE_DB_PATH",
             "APP_SETTINGS_DB_PATH"):
    os.environ[_var] = os.path.join(_SANDBOX, _var.lower() + ".db")
os.environ.setdefault("UPLOAD_FOLDER_PATH", os.path.join(_SANDBOX, "uploads"))

from tools import feedback_db                            # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Shaped like the real entries, including the numbered-list message.
SAMPLE = [
    ("Deal Analyzer", "lookin good", "/tools/deal-analyzer/?"),
    ("Site DD", "1. Swap out the condition summary\r\n2. Upload the rent roll",
     "/tools/site-dd/assessment/11?"),
    ("Investor Report", "1. remove top description\r\n2. add a section",
     "/tools/investor-report/?deal_id=2"),
]


class AdminFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with feedback_db.get_connection() as conn:
            for tool, message, url in SAMPLE:
                feedback_db.add_feedback(conn, tool, message, url)
        from app import app
        app.config["WTF_CSRF_ENABLED"] = False
        cls.app = app

    def client(self, user=None):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = (user if user is not None
                             else self.app.config.get("ADMIN_USERNAME"))
            s["_fresh"] = True
        return c

    def page(self):
        return self.client().get("/admin/feedback").get_data(as_text=True)

    def test_the_route_exists_and_returns_a_page(self):
        r = self.client().get("/admin/feedback")
        self.assertEqual(r.status_code, 200)

    def test_every_stored_entry_appears(self):
        body = self.page()
        for tool, message, _ in SAMPLE:
            with self.subTest(tool=tool):
                self.assertIn(tool, body)
                self.assertIn(message.splitlines()[0], body)

    def test_the_page_url_is_shown(self):
        """Which screen she was on is half the context of a request."""
        self.assertIn("/tools/site-dd/assessment/11", self.page())

    def test_newest_first(self):
        body = self.page()
        last, first = SAMPLE[-1][1].splitlines()[0], SAMPLE[0][1]
        self.assertLess(body.find(last), body.find(first))

    def test_multi_line_messages_are_not_collapsed(self):
        """A numbered list rendered as one line is a half-read request."""
        self.assertIn("white-space:pre-wrap", self.page())

    def test_the_total_is_reported(self):
        with feedback_db.get_connection() as conn:
            total = len(feedback_db.list_feedback(conn))
        self.assertGreaterEqual(total, len(SAMPLE))
        self.assertIn(str(total), self.page())

    def test_it_is_reachable_from_the_navigation(self):
        """The original bug was invisibility, not absence."""
        self.assertIn("/admin/feedback", self.page())

    def test_only_one_nav_item_is_active_at_a_time(self):
        for url in ("/admin/feedback", "/admin/service-costs"):
            with self.subTest(url=url):
                body = self.client().get(url).get_data(as_text=True)
                self.assertEqual(body.count("nav-link active"), 1)

    def test_a_non_admin_cannot_read_it(self):
        self.assertIn(self.client(user="someone-else")
                      .get("/admin/feedback").status_code, (302, 403))

    def test_an_anonymous_visitor_cannot_read_it(self):
        self.assertIn(self.app.test_client()
                      .get("/admin/feedback").status_code, (302, 401))

    def test_the_page_never_offers_to_delete_or_edit(self):
        """Read-only on purpose: a list that can be cleared can lose
        something before it is acted on.

        Asserted on CONTROLS, not prose. The page's own subtitle says
        entries "cannot be edited or dismissed", so a substring search
        for those words fails on the sentence that promises the
        behaviour -- which is how the first version of this test failed.
        """
        import re as _re

        body = self.page()
        actions = _re.findall(r'<form[^>]*action="([^"]*)"', body)
        # The shared feedback widget posts to /feedback/; nothing else on
        # this page may post anywhere.
        self.assertTrue(all(a.rstrip("/").endswith("feedback") for a in actions),
                        f"unexpected form target on a read-only page: {actions}")
        self.assertNotIn("method=\"POST\" action=\"/admin", body)
        for endpoint in ("delete_feedback", "dismiss_feedback",
                         "admin.delete", "admin.dismiss"):
            with self.subTest(endpoint=endpoint):
                self.assertNotIn(endpoint, body)

    def test_no_write_route_exists_on_the_blueprint(self):
        """Not just absent from the page -- absent from the app."""
        from app import app

        writable = [
            str(r) for r in app.url_map.iter_rules()
            if str(r).startswith("/admin/feedback")
            and {"POST", "PUT", "PATCH", "DELETE"} & r.methods
        ]
        self.assertEqual(writable, [])

    def test_the_route_writes_nothing_to_the_store(self):
        path = feedback_db.get_db_path()
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        before = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        conn.close()
        self.client().get("/admin/feedback")
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        after = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        conn.close()
        self.assertEqual(before, after)


class ListFeedbackIsNoLongerOrphanedTests(unittest.TestCase):
    """The control on the original defect.

    If this assertion ever fails again, the reader has been removed and
    the widget is silently write-only once more.
    """

    def test_something_in_the_app_actually_calls_list_feedback(self):
        callers = []
        for path in sorted((ROOT / "tools").rglob("*.py")):
            if path.name == "feedback_db.py":
                continue
            if "list_feedback(" in path.read_text(encoding="utf-8"):
                callers.append(f"tools/{path.name}")
        self.assertTrue(callers, "no module reads the feedback store")
        self.assertIn("tools/admin.py", callers)

    def test_a_template_renders_the_entries(self):
        body = (ROOT / "templates" / "admin" / "feedback.html").read_text(
            encoding="utf-8")
        for token in ("e.tool", "e.message", "e.created_at", "e.page_url"):
            with self.subTest(token=token):
                self.assertIn(token, body)


if __name__ == "__main__":
    unittest.main()
