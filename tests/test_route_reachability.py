"""Every GET route a person is meant to reach must be linked from a template.

THE COMPANION TO test_dead_readers.py

That file catches a reader nothing calls. This one catches a page nothing
links to. They are the same bug at two different layers, and this app has
now produced it four times:

    feedback_db.list_feedback()   a reader with no caller
    notes_db.list_updates()       a reader with no caller
    the notetaker                 a page with no way in
    site_dd.capex_budget          a finished PDF and Excel export,
                                  routed, correct, and linked from
                                  nowhere at all

The last one is why this file exists. Michelle asked for an Excel export
of the Site DD assessment. It had been shipped, working, for weeks. She
could not find it, so she asked for it -- which is what a discoverability
bug sounds like from the outside.

WHAT COUNTS AS REFERENCED

A url_for('endpoint') in any template, OR -- for a route with no dynamic
segments -- its literal path in a template. The second clause matters: an
earlier version of this sweep matched url_for only and reported
/manifest.json and /service-worker.js as unreachable, when base.html
links the first as href="/manifest.json" and registers the second from
JavaScript. Two false positives in the first six results is how a
checker teaches people to ignore it.

WHAT IT STRUCTURALLY CANNOT SEE, STATED PLAINLY

  A SELF-REFERENTIAL CLUSTER. If a group of pages link only to each
  other, every one of them looks referenced while the group as a whole
  has no way in. That is exactly what the notetaker was: its own pages
  carried "back to Meeting Notes" links, so this sweep does NOT flag it
  at the commit before the nav entry was added. Verified, not assumed.

  NavShellTests below is the narrow answer to that specific hole: every
  blueprint's index must be linked from base.html, which is the one
  template reachable from everywhere. It would have caught the notetaker.
  It does not generalise to deep pages, and nothing here pretends it does.

  A link built by string concatenation in JavaScript, or an endpoint
  reached only by a redirect from another endpoint.

WHAT IT EXCLUDES BY DESIGN

  POST-only routes -- they are form targets, not destinations.
  The static endpoint.
  Anything on ALLOWLIST, each with a written reason. "Nobody has linked
  it yet" is not a reason; that is the bug.
"""

import os
import re
import tempfile
import unittest
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="route-reach-")
for _var in ("SITE_DD_DB_PATH", "DEAL_DIVE_DB_PATH", "RENT_COMPS_DB_PATH",
             "MARKET_DATA_DB_PATH", "UNDERWRITING_DB_PATH",
             "SCORECARD_PRO_DB_PATH", "FIRE_METRICS_DB_PATH",
             "FEEDBACK_DB_PATH", "INVESTOR_REPORT_DB_PATH",
             "INVESTOR_NOTES_DB_PATH", "OPENAI_USAGE_DB_PATH",
             "APP_SETTINGS_DB_PATH"):
    os.environ[_var] = os.path.join(_SANDBOX, _var.lower() + ".db")
os.environ.setdefault("UPLOAD_FOLDER_PATH", os.path.join(_SANDBOX, "uploads"))

ROOT = Path(__file__).resolve().parent.parent

JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)

# endpoint -> why it needs no template link.
ALLOWLIST = {
    "fire_metrics.debug_refresh":
        "A diagnostic, not a destination. Refreshes the metrics cache and "
        "is reached deliberately by typing the path while investigating "
        "stale data. Linking it from the UI would invite a person to "
        "trigger a refresh they did not mean to.",
    "mmr.download":
        "Token download. The token is minted by the POST that generates "
        "the report and handed straight back, so the URL cannot be "
        "written into a template -- there is nothing to write until the "
        "report exists.",
    "scorecard.download":
        "Token download, same shape as mmr.download: the token comes from "
        "the POST that produced the file and is only valid for it.",
}


def template_references():
    """(endpoints named via url_for, literal paths) across all templates."""
    endpoints, literals = set(), set()
    for path in ROOT.rglob("templates/**/*.html"):
        text = path.read_text(encoding="utf-8", errors="replace")
        text = HTML_COMMENT.sub(" ", JINJA_COMMENT.sub(" ", text))
        endpoints |= set(re.findall(r"url_for\(\s*['\"]([^'\"]+)['\"]", text))
        literals |= set(re.findall(r"""['"](/[A-Za-z0-9_./-]*)['"]""", text))
    return endpoints, literals


def get_rules(app):
    return [r for r in app.url_map.iter_rules()
            if "GET" in (r.methods or set()) and r.endpoint != "static"]


class EnumerationTests(unittest.TestCase):
    """A sweep that silently stops sweeping passes forever."""

    @classmethod
    def setUpClass(cls):
        from app import app
        cls.app = app

    def test_it_finds_the_routes(self):
        self.assertGreaterEqual(len(get_rules(self.app)), 30)

    def test_it_finds_template_references(self):
        endpoints, literals = template_references()
        self.assertGreaterEqual(len(endpoints), 30)
        self.assertTrue(literals)

    def test_comments_do_not_count_as_references(self):
        """The dead-reader sweep was once satisfied by a code comment."""
        endpoints, _ = template_references()
        self.assertNotIn("a_commented_out_endpoint_that_does_not_exist",
                         endpoints)


class EveryGetRouteIsLinkedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import app
        cls.app = app
        cls.endpoints, cls.literals = template_references()

    def referenced(self, rule):
        if rule.endpoint in self.endpoints:
            return True
        # A static path can be linked literally (href="/manifest.json") or
        # registered from JavaScript (the service worker). Both are real.
        return not rule.arguments and rule.rule in self.literals

    def test_no_get_route_is_unreachable(self):
        orphans = sorted(
            f"{r.endpoint}  ({r.rule})" for r in get_rules(self.app)
            if r.endpoint not in ALLOWLIST and not self.referenced(r))
        self.assertEqual(
            orphans, [],
            "These GET routes are linked from no template, so a person can "
            "reach them only by typing the URL:\n  " + "\n  ".join(orphans)
            + "\n\nLink them from the page someone would look on, or add "
              "them to ALLOWLIST with a reason.")

    def test_the_allowlist_has_no_stale_entries(self):
        linked = sorted(e for e in ALLOWLIST
                        for r in get_rules(self.app)
                        if r.endpoint == e and self.referenced(r))
        self.assertEqual(
            linked, [],
            "Now linked from a template; remove from ALLOWLIST: "
            + ", ".join(linked))

    def test_the_allowlist_only_names_routes_that_exist(self):
        known = {r.endpoint for r in self.app.url_map.iter_rules()}
        missing = sorted(e for e in ALLOWLIST if e not in known)
        self.assertEqual(
            missing, [],
            "ALLOWLIST names routes that no longer exist: " + ", ".join(missing))

    def test_every_allowlist_entry_states_a_reason(self):
        for endpoint, reason in ALLOWLIST.items():
            with self.subTest(endpoint=endpoint):
                self.assertGreater(len(reason.strip()), 40)


class NavShellTests(unittest.TestCase):
    """The narrow answer to the self-referential-cluster hole.

    A group of pages that link only to each other satisfies the sweep
    above while having no way in from the rest of the app. The notetaker
    was precisely that, and the sweep above does not catch it.

    Every blueprint's index is a destination someone must be able to
    reach without already being inside it, so it has to appear in
    base.html -- the one template rendered on every page.
    """

    @classmethod
    def setUpClass(cls):
        from app import app
        cls.app = app
        cls.base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        cls.shell = set(re.findall(r"url_for\(\s*['\"]([^'\"]+)['\"]", cls.base))

    def test_every_tool_index_is_in_the_navigation(self):
        missing = sorted(
            r.endpoint for r in self.app.url_map.iter_rules()
            if r.endpoint.endswith(".index")
            and "GET" in (r.methods or set())
            and r.endpoint not in self.shell)
        self.assertEqual(
            missing, [],
            "These tool landing pages are not linked from the nav shell, so "
            "they are reachable only from inside themselves:\n  "
            + "\n  ".join(missing))

    def test_the_notetaker_specifically(self):
        """The instance that motivated it."""
        self.assertIn("investor_notes.index", self.shell)

    def test_the_shell_check_is_actually_reading_base_html(self):
        self.assertGreaterEqual(len(self.shell), 10)


if __name__ == "__main__":
    unittest.main()
