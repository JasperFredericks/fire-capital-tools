"""Word export for the investor update, and the section toggling.

WHY THIS FILE EXISTS SEPARATELY FROM THE PDF/XLSX TESTS

Word was refused for a real reason -- it needed a dependency, and the
module said so in its own docstring rather than quietly adding one. The
dependency is now approved and pinned, so the tests that matter are the
ones that would catch it drifting back out of sync: the pin, the
docstring that used to contradict it, and a document that actually opens.

WHAT THE SECTION RULES ARE

Michelle's real updates carried a different set of sections each time,
so the design is a toggle over the update's own sections rather than a
fixed template. Three properties follow from that and are asserted here:

  no default subset   passing no selection yields EVERY section, not a
                      curated few -- a hardcoded default would be the
                      fixed list this feature exists to replace
  no silent reorder   order comes from the update, never from the order
                      the caller happened to list keys in
  no filler           a section with nothing in it renders its "not
                      discussed" line and stays visibly empty

The last one is the one worth breaking a build over. A generated
paragraph in an investor document is worse than an empty heading.
"""

import os
import tempfile
import unittest
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="word-export-")
for _var in ("SITE_DD_DB_PATH", "DEAL_DIVE_DB_PATH", "RENT_COMPS_DB_PATH",
             "MARKET_DATA_DB_PATH", "UNDERWRITING_DB_PATH",
             "SCORECARD_PRO_DB_PATH", "FIRE_METRICS_DB_PATH",
             "FEEDBACK_DB_PATH", "INVESTOR_REPORT_DB_PATH",
             "INVESTOR_NOTES_DB_PATH", "OPENAI_USAGE_DB_PATH",
             "APP_SETTINGS_DB_PATH"):
    os.environ[_var] = os.path.join(_SANDBOX, _var.lower() + ".db")
os.environ.setdefault("UPLOAD_FOLDER_PATH", os.path.join(_SANDBOX, "uploads"))

ROOT = Path(__file__).resolve().parent.parent

from tools import investor_notes_export as export   # noqa: E402

UPDATE = {"id": 1, "property_label": "Eagle Rock Apartments",
          "period_start": "2026-04-01", "period_end": "2026-06-30",
          "generated_at": "2026-07-02T10:11:12"}

SOURCES = [{"transcript_date": "2026-05-04", "title": "May owner call",
            "original_name": "may.txt", "source": "fathom"}]

NOT_DISCUSSED = "Not discussed in the meetings covering this period."


def sections():
    return [
        {"key": "operations", "name": "Operations", "empty": False,
         "points": [{"text": "Occupancy held at 94% through the quarter.",
                     "title": "May owner call", "date": "2026-05-04"}]},
        {"key": "financial_update", "name": "Financial Update", "empty": False,
         "points": [{"text": "Collections tracked slightly ahead of budget.",
                     "title": "May owner call", "date": "2026-05-04"}]},
        {"key": "market_update", "name": "Market Update", "empty": True,
         "points": [], "empty_text": NOT_DISCUSSED},
    ]


def read_docx(path):
    from docx import Document
    return [p.text for p in Document(str(path)).paragraphs]


class SelectSectionsTests(unittest.TestCase):
    def test_no_selection_means_every_section(self):
        """Not a curated default -- all of them."""
        got = export.select_sections(sections(), None)
        self.assertEqual([s["key"] for s in got],
                         ["operations", "financial_update", "market_update"])

    def test_empty_selection_also_means_every_section(self):
        self.assertEqual(len(export.select_sections(sections(), [])), 3)

    def test_a_selection_filters(self):
        got = export.select_sections(sections(), ["market_update"])
        self.assertEqual([s["key"] for s in got], ["market_update"])

    def test_order_comes_from_the_update_not_the_request(self):
        """Reordering the request must not reorder the document."""
        got = export.select_sections(sections(),
                                     ["market_update", "operations"])
        self.assertEqual([s["key"] for s in got],
                         ["operations", "market_update"])

    def test_an_unknown_key_is_ignored_not_invented(self):
        got = export.select_sections(sections(), ["operations", "nope"])
        self.assertEqual([s["key"] for s in got], ["operations"])

    def test_it_does_not_mutate_the_input(self):
        original = sections()
        export.select_sections(original, ["operations"])
        self.assertEqual(len(original), 3)


class BuildDocxTests(unittest.TestCase):
    def build(self, chosen=None):
        out = Path(tempfile.mkdtemp()) / "update.docx"
        return export.build_docx(
            out, UPDATE, export.select_sections(sections(), chosen), SOURCES)

    def test_it_writes_a_file_word_can_open(self):
        text = read_docx(self.build())
        self.assertTrue(any("Investor Update" in t for t in text))

    def test_the_property_and_period_are_on_it(self):
        blob = "\n".join(read_docx(self.build()))
        self.assertIn("Eagle Rock Apartments", blob)
        self.assertIn("2026-04-01", blob)

    def test_every_section_heading_appears(self):
        text = read_docx(self.build())
        for name in ("Operations", "Financial Update", "Market Update"):
            with self.subTest(name=name):
                self.assertIn(name, text)

    def test_points_are_carried_verbatim(self):
        text = read_docx(self.build())
        self.assertIn("Occupancy held at 94% through the quarter.", text)

    def test_points_are_attributed(self):
        blob = "\n".join(read_docx(self.build()))
        self.assertIn("May owner call", blob)
        self.assertIn("2026-05-04", blob)

    def test_an_empty_section_says_so_and_is_not_filled(self):
        """The assertion worth breaking a build over."""
        text = read_docx(self.build())
        self.assertIn(NOT_DISCUSSED, text)
        heading = text.index("Market Update")
        after = [t for t in text[heading + 1:] if t.strip()]
        self.assertTrue(after[0].startswith("Not discussed"), after[:2])

    def test_deselecting_a_section_removes_it_entirely(self):
        text = read_docx(self.build(["operations"]))
        self.assertIn("Operations", text)
        self.assertNotIn("Market Update", text)
        self.assertNotIn("Financial Update", text)

    def test_nothing_is_generated_to_fill_an_empty_section(self):
        """Only the stored empty_text may appear under an empty heading."""
        text = read_docx(self.build(["market_update"]))
        body = [t for t in text if t.strip()]
        self.assertIn(NOT_DISCUSSED, body)
        # No sentence beyond the heading, the empty line, the sources and
        # the disclaimer -- nothing invented about the market.
        self.assertNotIn("market", " ".join(
            t for t in body if t != NOT_DISCUSSED
            and not t.startswith("Market Update")).lower().replace(
                "Market Update", ""))

    def test_the_sources_are_listed(self):
        blob = "\n".join(read_docx(self.build()))
        self.assertIn("Sources", blob)
        self.assertIn("May owner call", blob)

    def test_the_disclaimer_survives(self):
        """Same claim the PDF and XLSX carry: narrative, not accounting."""
        blob = "\n".join(read_docx(self.build()))
        self.assertIn("not accounting records", blob)

    def test_the_filename_uses_the_docx_extension(self):
        self.assertTrue(
            export.suggested_filename(UPDATE, "docx").endswith(".docx"))


class DependencyTests(unittest.TestCase):
    """The pin, and the docstring that used to contradict it."""

    def test_python_docx_is_pinned_in_requirements(self):
        req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("python-docx==", req)

    def test_the_module_no_longer_says_word_is_not_offered(self):
        src = (ROOT / "tools" / "investor_notes_export.py").read_text(
            encoding="utf-8")
        self.assertNotIn("WORD IS NOT OFFERED", src)

    def test_the_route_no_longer_refuses_docx(self):
        src = (ROOT / "tools" / "investor_notes.py").read_text(encoding="utf-8")
        self.assertNotIn("Word would need a", src)


class ReachabilityTests(unittest.TestCase):
    """Distinct from correctness. This feature area shipped fully
    unreachable once because verification only drove it by URL."""

    def tpl(self):
        return (ROOT / "templates" / "tools" /
                "investor_notes_update.html").read_text(encoding="utf-8")

    def test_the_update_page_offers_a_word_download(self):
        self.assertIn("fmt='docx'", self.tpl())
        self.assertIn("Download Word", self.tpl())

    def test_the_page_exposes_a_toggle_per_section(self):
        self.assertIn('name="section"', self.tpl())
        self.assertIn("for section in sections", self.tpl())

    def test_every_toggle_starts_ticked(self):
        """All, not a default subset."""
        self.assertRegex(self.tpl(), r'name="section"[^>]*checked')


class GeneratedUpdatesAreListedTests(unittest.TestCase):
    """The gap found while checking reachability for the Word button.

    list_updates() had been in the database layer since the table
    existed, and no route ever called it. An update was reachable by the
    redirect straight after generating it, or by re-running the identical
    review query and hitting the cache -- nothing else. Navigate away and
    the document was gone, and with it every export button on it.

    A Word export nobody can navigate back to is the same bug this
    feature area already shipped once, so it is asserted here rather than
    left to be noticed later.
    """

    def test_the_index_route_asks_for_saved_updates(self):
        src = (ROOT / "tools" / "investor_notes.py").read_text(encoding="utf-8")
        index = src.split("def index(")[1].split("def ")[0]
        self.assertIn("list_updates", index)
        self.assertIn("updates=updates", index)

    def test_the_index_template_links_to_each_one(self):
        tpl = (ROOT / "templates" / "tools" /
               "investor_notes.html").read_text(encoding="utf-8")
        self.assertIn("investor_notes.view_update", tpl)
        self.assertIn("for u in updates", tpl)

    def test_the_listing_names_the_export_formats(self):
        """So the way to the Word file is visible, not guessed at."""
        tpl = (ROOT / "templates" / "tools" /
               "investor_notes.html").read_text(encoding="utf-8")
        self.assertIn("Word", tpl)


if __name__ == "__main__":
    unittest.main()
