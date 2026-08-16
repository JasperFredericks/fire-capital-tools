"""
Offering-memorandum extraction: the guards, and the promise it is additive.

An OM is the seller's document. Reproducing it is useful; inventing
anything while reproducing it is worse than useless, because the result
looks like a quotation. So most of this file is about refusal.

The guards are tested by making the model's output wrong on purpose. A
validator that has only ever seen correct input is not known to work --
each check here is handed the exact failure it exists to catch:

    a number that is nowhere in the document      (derived arithmetic)
    a number attributed to the wrong page         (a real citation, misplaced)
    a pitch bullet that was paraphrased           (improved, not quoted)
    a figure inside a "not stated" entry          (an absence with a number)

And one test asserts the thing no comment can: that no OM code writes any
scenario column. The T12 importer in the same module does exactly that --
`UPDATE underwriting_scenarios SET t12_source = ?, other_income_annual = ?`
-- which is legitimate for an importer and is precisely what this feature
must never become. The distinction is enforced here rather than described
in a docstring, because a docstring does not fail.
"""

import ast
import io
import json
import unittest
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                        # noqa: E402
import numpy as np                                     # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages   # noqa: E402

from tools import om_extract as om                     # noqa: E402
from tools import underwriting_db as udb               # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

PAGE_LINES = [
    "EAGLE ROCK APARTMENTS",
    "Offering Memorandum",
    "Asking Price: $6,990,000",
    "92 Units | Year Built 1974",
    "Net Operating Income: $384,455",
    "The property benefits from a supply constrained submarket.",
]


def text_pdf(pages=2, extra=None) -> bytes:
    """A PDF with a real text layer, like a broker's exported OM."""
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        for n in range(1, pages + 1):
            fig = plt.figure(figsize=(8.5, 11))
            y = 0.92
            for line in PAGE_LINES:
                fig.text(0.08, y, line, fontsize=11)
                y -= 0.04
            fig.text(0.08, y, f"Page {n} of {pages}", fontsize=11)
            if extra and n in extra:
                fig.text(0.08, y - 0.04, extra[n], fontsize=11)
            pdf.savefig(fig)
            plt.close(fig)
    return buf.getvalue()


def scanned_pdf(pages=2) -> bytes:
    """An image-only PDF: what a scanned OM actually is."""
    buf = io.BytesIO()
    rng = np.random.default_rng(0)
    with PdfPages(buf) as pdf:
        for _ in range(pages):
            fig = plt.figure(figsize=(8.5, 11))
            ax = fig.add_axes([0, 0, 1, 1])
            ax.axis("off")
            ax.imshow(rng.random((60, 45)), cmap="gray")
            pdf.savefig(fig)
            plt.close(fig)
    return buf.getvalue()


class ReadingTests(unittest.TestCase):
    def test_pages_come_back_one_entry_per_page(self):
        pages = om.read_pages(text_pdf(3))
        self.assertEqual(len(pages), 3)

    def test_page_boundaries_are_preserved(self):
        """The whole design rests on this: a number needs its page."""
        pages = om.read_pages(text_pdf(3))
        self.assertIn("Page 1 of 3", pages[0])
        self.assertIn("Page 3 of 3", pages[2])
        self.assertNotIn("Page 3 of 3", pages[0])

    def test_real_numbers_survive_extraction(self):
        text = "\n".join(om.read_pages(text_pdf(1)))
        self.assertIn("$6,990,000", text)
        self.assertIn("384,455", text)

    def test_inspection_costs_nothing_and_describes_the_file(self):
        info = om.inspect(text_pdf(3))
        self.assertEqual(info["page_count"], 3)
        self.assertEqual(info["pages_used"], [1, 2, 3])
        self.assertEqual(info["pages_skipped"], [])
        self.assertGreater(info["estimated_prompt_tokens"], om.INSTRUCTION_TOKENS)


class ScannedDocumentTests(unittest.TestCase):
    """The refusal that must happen before any spend."""

    def test_an_image_only_pdf_yields_no_text(self):
        pages = om.read_pages(scanned_pdf(2))
        self.assertTrue(all(not p.strip() for p in pages))

    def test_it_is_refused_rather_than_summarised(self):
        with self.assertRaises(om.OMUnreadable) as caught:
            om.inspect(scanned_pdf(2))
        message = str(caught.exception)
        self.assertIn("scanned", message.lower())
        self.assertIn("nothing was charged", message.lower())

    def test_a_mostly_scanned_document_is_also_refused(self):
        """One readable cover page over twenty scanned ones is not an OM."""
        pages = [""] * 20 + ["Asking Price: $6,990,000 " * 5]
        readable = om.readable_pages(pages)
        self.assertEqual(len(readable), 1)
        self.assertLess(len(readable) / len(pages), om.MIN_READABLE_SHARE)


class PageCapTests(unittest.TestCase):
    def test_the_cap_is_forty(self):
        self.assertEqual(om.PAGE_CAP, 40)

    def test_pages_beyond_the_cap_are_named_not_dropped_silently(self):
        info = {"page_count": 58, "page_cap": 40,
                "pages_skipped": list(range(41, 59))}
        note = om.skipped_note(info)
        self.assertIn("58", note)
        self.assertIn("41", note)
        self.assertIn("58", note)
        self.assertIn("not sent", note)

    def test_no_note_when_nothing_was_skipped(self):
        self.assertEqual(om.skipped_note({"pages_skipped": []}), "")


class CacheKeyTests(unittest.TestCase):
    def test_the_key_is_the_bytes_and_the_prompt(self):
        data = text_pdf(1)
        key = om.cache_key(om.file_sha256(data))
        self.assertIn(om.PROMPT_VERSION, key)
        self.assertIn(om.file_sha256(data), key)

    def test_identical_bytes_give_an_identical_key(self):
        a, b = text_pdf(2), text_pdf(2)
        self.assertEqual(om.file_sha256(a), om.file_sha256(b))

    def test_a_new_prompt_version_invalidates_the_cache(self):
        sha = om.file_sha256(text_pdf(1))
        self.assertNotEqual(om.cache_key(sha, "om_extraction_v1"),
                            om.cache_key(sha, "om_extraction_v2"))

    def test_the_version_is_the_approved_one(self):
        self.assertEqual(om.PROMPT_VERSION, "om_extraction_v1")


def good_summary():
    return {
        "property": {"name": "Eagle Rock Apartments", "address": "",
                     "unit_count": "92", "year_built": "1974",
                     "property_type": "", "unit_mix": ""},
        "asking_terms": {"asking_price": "$6,990,000", "cap_rate": "",
                         "price_per_unit": "", "financing": "",
                         "guidance": ""},
        "stated_numbers": [
            {"label": "Asking price", "value_as_written": "$6,990,000", "page": 1},
            {"label": "NOI", "value_as_written": "$384,455", "page": 1},
        ],
        "pitch": [
            {"quote": "The property benefits from a supply constrained submarket.",
             "page": 1},
            {"quote": "Offering Memorandum", "page": 1},
            {"quote": "EAGLE ROCK APARTMENTS", "page": 1},
        ],
        "not_stated": ["No T12 is referenced.", "No expense breakdown."],
        "unreadable_pages": [],
    }


class GuardTests(unittest.TestCase):
    """Each guard, handed the failure it exists for."""

    def setUp(self):
        self.pages = om.read_pages(text_pdf(2))
        self.used = [1, 2]

    def test_a_correct_summary_passes(self):
        self.assertEqual(om.validate(good_summary(), self.pages, self.used), [])

    def test_a_fabricated_number_is_rejected(self):
        """Derived arithmetic: a cap rate the OM never printed."""
        bad = good_summary()
        bad["asking_terms"]["cap_rate"] = "5.50%"
        reasons = om.validate(bad, self.pages, self.used)
        self.assertTrue(reasons)
        self.assertIn("do not appear anywhere", reasons[0])

    def test_a_computed_price_per_unit_is_rejected(self):
        bad = good_summary()
        bad["asking_terms"]["price_per_unit"] = "$75,978"
        self.assertTrue(om.validate(bad, self.pages, self.used))

    def test_a_number_attributed_to_the_wrong_page_is_rejected(self):
        bad = good_summary()
        bad["stated_numbers"].append(
            {"label": "Asking price", "value_as_written": "$6,990,000",
             "page": 9})
        reasons = om.validate(bad, self.pages, self.used)
        self.assertTrue(any("was not read" in r for r in reasons))

    def test_a_real_number_on_a_page_that_lacks_it_is_rejected(self):
        pages = ["Asking Price: $6,990,000", "no figures on this page"]
        summary = good_summary()
        summary["stated_numbers"] = [
            {"label": "Asking price", "value_as_written": "$6,990,000",
             "page": 2}]
        summary["pitch"] = [{"quote": "Asking Price", "page": 1}] * 3
        summary["not_stated"] = []
        reasons = om.validate(summary, pages, [1, 2])
        self.assertTrue(any("does not contain it" in r for r in reasons))

    def test_a_paraphrased_pitch_bullet_is_rejected(self):
        bad = good_summary()
        bad["pitch"][0] = {
            "quote": "The asset enjoys strong fundamentals in a "
                     "supply-limited market.",
            "page": 1}
        reasons = om.validate(bad, self.pages, self.used)
        self.assertTrue(any("not in the document as written" in r
                            for r in reasons))

    def test_a_verbatim_quote_survives_whitespace_artifacts(self):
        """'Payroll T axes' is a real extraction artifact, not a mismatch."""
        pages = ["Payroll T axes are included in the operating expenses."]
        summary = good_summary()
        summary["stated_numbers"] = []
        summary["not_stated"] = []
        summary["property"] = {k: "" for k in summary["property"]}
        summary["asking_terms"] = {k: "" for k in summary["asking_terms"]}
        summary["pitch"] = [
            {"quote": "Payroll T axes are included in the operating expenses.",
             "page": 1}] * 3
        self.assertEqual(om.validate(summary, pages, [1]), [])

    def test_a_number_in_a_not_stated_entry_is_rejected(self):
        bad = good_summary()
        bad["not_stated"] = ["No T12 for the trailing 12 months"]
        reasons = om.validate(bad, self.pages, self.used)
        self.assertTrue(any("may never" in r for r in reasons))

    def test_too_few_pitch_bullets_is_rejected(self):
        bad = good_summary()
        bad["pitch"] = bad["pitch"][:2]
        self.assertTrue(any("3 to 5" in r
                            for r in om.validate(bad, self.pages, self.used)))

    def test_too_many_pitch_bullets_is_rejected(self):
        bad = good_summary()
        bad["pitch"] = bad["pitch"] * 3
        self.assertTrue(any("3 to 5" in r
                            for r in om.validate(bad, self.pages, self.used)))

    def test_page_integers_are_not_treated_as_document_numbers(self):
        """`page: 7` must not need the document to contain a '7'."""
        pages = ["Asking Price: $6,990,000"] * 8
        summary = good_summary()
        summary["stated_numbers"] = [
            {"label": "Asking price", "value_as_written": "$6,990,000",
             "page": 8}]
        summary["property"] = {k: "" for k in summary["property"]}
        summary["asking_terms"] = {k: "" for k in summary["asking_terms"]}
        summary["not_stated"] = []
        summary["pitch"] = [{"quote": "Asking Price", "page": 8}] * 3
        self.assertEqual(om.validate(summary, pages, list(range(1, 9))), [])


class UnapprovedNumberTests(unittest.TestCase):
    """The analogue of contains_unapproved_numbers, asserted directly."""

    PAGES = ["Asking Price: $6,990,000 and NOI of $384,455"]

    def test_a_number_in_the_source_is_approved(self):
        parsed = {"asking_terms": {"asking_price": "$6,990,000"}}
        self.assertEqual(om.unapproved_numbers(parsed, self.PAGES), [])

    def test_a_number_absent_from_the_source_is_flagged(self):
        parsed = {"asking_terms": {"cap_rate": "5.50%"}}
        self.assertEqual(om.unapproved_numbers(parsed, self.PAGES), ["5.50"])

    def test_it_looks_everywhere_in_the_structure(self):
        parsed = {"a": {"b": [{"c": "somewhere 12,345 deep"}]}}
        self.assertIn("12,345", om.unapproved_numbers(parsed, self.PAGES))


class AdditiveOnlyTests(unittest.TestCase):
    """The promise that this feature reads and never writes.

    Asserted at the source level. The T12 importer in underwriting.py
    writes scenario columns from an upload, which is correct for an
    importer; the OM feature must not acquire that shape by drift, and a
    comment saying so would not notice if it did.
    """

    OM_MODULES = ("tools/om_extract.py", "tools/om_db.py")
    OM_FUNCTIONS = ("upload_om", "extract_om", "om_file", "delete_om",
                    "_om_documents", "_om_redirect", "_om_model_name",
                    "_om_api_key")

    def _om_route_source(self):
        source = (ROOT / "tools" / "underwriting.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        chunks = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in self.OM_FUNCTIONS:
                chunks.append(ast.get_source_segment(source, node) or "")
        self.assertEqual(len(chunks), len(self.OM_FUNCTIONS),
                         "an OM route was renamed; update this test")
        return "\n".join(chunks)

    def test_no_om_module_names_a_scenario_column(self):
        blob = "\n".join((ROOT / m).read_text(encoding="utf-8")
                         for m in self.OM_MODULES)
        blob += self._om_route_source()
        offenders = [c for c in udb.SCENARIO_NUMERIC if c in blob]
        self.assertEqual(
            offenders, [],
            f"OM code references scenario input columns {offenders}; the "
            "summary is reference material and must never feed the model")

    def test_no_om_code_updates_the_scenarios_table(self):
        blob = "\n".join((ROOT / m).read_text(encoding="utf-8")
                         for m in self.OM_MODULES)
        blob += self._om_route_source()
        lowered = blob.lower()
        for forbidden in ("update underwriting_scenarios",
                          "update_scenario", "update_scenario_partial"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)

    def test_the_anti_pattern_it_guards_against_really_exists(self):
        """The control.

        If the T12 importer stopped writing scenario columns, the two
        tests above would still pass while asserting nothing meaningful.
        This one fails in that case, so the guard cannot quietly become
        vacuous.
        """
        source = (ROOT / "tools" / "underwriting.py").read_text(encoding="utf-8")
        self.assertIn("UPDATE underwriting_scenarios SET t12_source", source)

    def test_om_storage_is_its_own_tables(self):
        schema = (ROOT / "tools" / "om_db.py").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS om_documents", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS om_extractions", schema)

    def test_the_extraction_cache_is_not_scoped_to_a_scenario(self):
        """Re-uploading one OM to a second scenario must not re-spend."""
        import re as _re
        schema = (ROOT / "tools" / "om_db.py").read_text(encoding="utf-8")
        table = schema.split("CREATE TABLE IF NOT EXISTS om_extractions")[1]
        table = table.split(");")[0]
        # SQL comments stripped: the table explains in prose why it has no
        # scenario_id, and the word appearing there is the explanation,
        # not a column.
        columns = _re.sub(r"--[^\n]*", "", table)
        self.assertNotIn("scenario_id", columns)


class SchemaTests(unittest.TestCase):
    def test_every_object_forbids_extra_properties(self):
        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertFalse(node.get("additionalProperties", True))
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)
        walk(om.SCHEMA)

    def test_the_six_sections_are_all_required(self):
        self.assertEqual(
            set(om.SCHEMA["required"]),
            {"property", "asking_terms", "stated_numbers", "pitch",
             "not_stated", "unreadable_pages"})

    def test_a_stated_number_always_carries_its_page(self):
        item = om.SCHEMA["properties"]["stated_numbers"]["items"]
        self.assertEqual(set(item["required"]),
                         {"label", "value_as_written", "page"})

    def test_a_pitch_bullet_always_carries_its_page(self):
        item = om.SCHEMA["properties"]["pitch"]["items"]
        self.assertEqual(set(item["required"]), {"quote", "page"})

    def test_the_instructions_forbid_calculation(self):
        text = om.build_instructions().lower()
        self.assertIn("never calculate", text)
        self.assertIn("verbatim", text)
        self.assertIn("never put a number in 'not_stated'", text)

    def test_the_document_is_page_labelled_for_the_model(self):
        built = om.build_input(["alpha", "beta"], [1, 2])
        self.assertIn("--- PAGE 1 ---", built)
        self.assertIn("--- PAGE 2 ---", built)


class UploadLimitTests(unittest.TestCase):
    def test_the_om_endpoint_has_its_own_limit(self):
        from tools import upload_limits as ul
        self.assertEqual(ul.ENDPOINT_LIMITS["underwriting.upload_om"],
                         ul.DOCUMENT_BYTES)

    def test_pdf_is_not_added_to_the_shared_spreadsheet_extensions(self):
        """Widening the shared set would let a PDF reach the T12 importer."""
        from tools import underwriting as uw
        self.assertNotIn(".pdf", uw.ALLOWED_UPLOAD_EXT)
        self.assertEqual(uw.OM_UPLOAD_EXT, {".pdf"})


if __name__ == "__main__":
    unittest.main()
