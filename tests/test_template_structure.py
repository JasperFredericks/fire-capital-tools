"""Standing structural checks on every template in the app.

WHY THIS EXISTS

A grading-thresholds block was once moved to make it reachable and
landed in the middle of `<select name="expenses_mode">`. Nothing looked
wrong. The template read fine, Jinja rendered it without complaint, and
every functional test passed, because the server-side code was correct
and the tests exercised the server side.

What broke was the *browser's* parse. Per the HTML5 "in select"
insertion mode, an `<input>` start tag implies `</select>`, so the
hidden CSRF input inside that block closed the select early. Two
user-visible bugs followed, both invisible in the source:

  * the `$ per year` option fell outside the select and vanished from
    the dropdown, leaving operating expenses enterable only as a
    percentage
  * the block's own `<form>` became a form nested inside the analyzer's
    form, which browsers do not build -- the inner start tag is dropped
    and its fields are adopted by the outer form, so "Save thresholds"
    submitted a valuation instead of saving thresholds

THE TRAP IN TESTING THIS

The obvious assertion -- "no `<input>` inside a `<select>` in the parsed
DOM" -- passes on the broken template. It has to: the parser is what
moved the input out. Asserting on the repaired tree asks the wrong
question, because the repair is the bug.

So these tests assert on the two things that actually differ:

  * the parser's own error stream, which records the repair it had to
    perform, and
  * the evidence the repair leaves behind in the DOM -- an `<option>`
    with no `<select>` ancestor

Both are DOM-level facts from a spec-compliant parser (html5lib), not
string matching, because string matching is exactly what failed to see
this the first time.

WHY THE RAW SOURCE IS PARSED

Rendering all 31 templates would mean constructing a request context and
plausible data for each. The nesting questions here are answered by the
static markup, so the source is parsed directly. Jinja *comments* are
stripped first: they are not markup, and one of them legitimately
discusses `<select>` and `<input>` tags while explaining this very bug.
Jinja statements and expressions are left in place -- html5lib treats
them as text, which is what they are as far as element nesting goes.
"""

import os
import re
import tempfile
import unittest
from pathlib import Path

import html5lib

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

# Module scope on purpose. `unittest discover -s tests` imports test
# modules top-level, so tests/__init__.py never runs and a guard placed
# there would not fire. Every persistent store is redirected into a
# temporary directory before app.py is imported anywhere below, so
# rendering a page here cannot touch a real database.
_SANDBOX = tempfile.mkdtemp(prefix="template-structure-")
for _var in ("SITE_DD_DB_PATH", "DEAL_DIVE_DB_PATH", "RENT_COMPS_DB_PATH",
             "MARKET_DATA_DB_PATH", "UNDERWRITING_DB_PATH",
             "SCORECARD_PRO_DB_PATH", "FIRE_METRICS_DB_PATH",
             "FEEDBACK_DB_PATH", "INVESTOR_REPORT_DB_PATH",
             "INVESTOR_NOTES_DB_PATH", "OPENAI_USAGE_DB_PATH",
             "APP_SETTINGS_DB_PATH"):
    os.environ[_var] = os.path.join(_SANDBOX, _var.lower() + ".db")
os.environ.setdefault("UPLOAD_FOLDER_PATH", os.path.join(_SANDBOX, "uploads"))

# Jinja comments are not markup. Everything else is left alone.
JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)

# The parser reports one of these whenever it had to close a <select>
# early or discard content it found inside one.
SELECT_NESTING_CODES = frozenset({
    "unexpected-input-in-select",
    "unexpected-start-tag-in-select",
    "unexpected-end-tag-in-select",
    "unexpected-select-in-select",
})


def _templates():
    found = sorted(TEMPLATES.rglob("*.html"))
    assert found, f"no templates found under {TEMPLATES}"
    return found


def _parse(source):
    """Parse template source the way a browser would.

    Returns the document, the parser's error list, and a child->parent
    map, since ElementTree offers no parent pointer.
    """
    parser = html5lib.HTMLParser(strict=False, namespaceHTMLElements=False)
    doc = parser.parse(JINJA_COMMENT.sub("", source))
    parents = {child: parent for parent in doc.iter() for child in parent}
    return doc, parser.errors, parents


def _ancestors(element, parents):
    while element in parents:
        element = parents[element]
        yield element


def _select_errors(errors):
    return sorted({code for _, code, _ in errors if code in SELECT_NESTING_CODES})


def _nested_form_errors(errors):
    """Signals that a <form> start or end tag appeared where it cannot.

    A form inside another form produces `unexpected-start-tag` naming
    `form`; the parser drops the inner start tag and adopts its fields
    into the outer form.
    """
    return [data.get("name") for _, code, data in errors
            if code in ("unexpected-start-tag", "unexpected-end-tag")
            and data.get("name") == "form"]


def _orphan_options(doc, parents):
    """<option> elements the parser could not keep inside a <select>.

    This is the fingerprint the select-splitting bug leaves behind, and
    the reason it is checked separately from the error stream: it is a
    statement about the tree the user's browser ends up with, not about
    how the parser got there.
    """
    return [option.get("value") for option in doc.iter("option")
            if not any(a.tag == "select" for a in _ancestors(option, parents))]


def _interactive_inside_select(doc, parents):
    """Form controls sitting inside a <select> in the built tree.

    The literal form of the rule. It does not catch the historical bug
    on its own -- the parser relocates the offender before this can see
    it -- but it does catch markup the parser tolerates rather than
    repairs, so it is kept alongside the checks that do.
    """
    kinds = ("form", "input", "textarea", "button", "select")
    return [f"<{e.tag} name={e.get('name')!r}>"
            for e in doc.iter()
            if e.tag in kinds
            and any(a.tag == "select" for a in _ancestors(e, parents))]


class TemplateStructure(unittest.TestCase):
    """Every template in the app, not just the one that broke."""

    def test_no_form_or_input_is_nested_inside_a_select(self):
        offenders = {}
        for path in _templates():
            doc, errors, parents = _parse(path.read_text(encoding="utf-8"))
            problems = (_select_errors(errors)
                        + _interactive_inside_select(doc, parents))
            if problems:
                offenders[path.name] = problems
        self.assertEqual(
            offenders, {},
            "form controls inside a <select> -- the browser will close the "
            "select early and silently relocate whatever follows")

    def test_no_option_is_orphaned_outside_its_select(self):
        offenders = {}
        for path in _templates():
            doc, _, parents = _parse(path.read_text(encoding="utf-8"))
            orphans = _orphan_options(doc, parents)
            if orphans:
                offenders[path.name] = orphans
        self.assertEqual(
            offenders, {},
            "an <option> ended up outside every <select>, so it will not "
            "appear in the dropdown the user sees")

    def test_no_form_is_nested_inside_another_form(self):
        offenders = {}
        for path in _templates():
            _, errors, _ = _parse(path.read_text(encoding="utf-8"))
            nested = _nested_form_errors(errors)
            if nested:
                offenders[path.name] = len(nested)
        self.assertEqual(
            offenders, {},
            "a nested <form> is dropped by the browser and its fields are "
            "adopted by the outer form, so its submit button posts to the "
            "wrong endpoint")


class TheseChecksActuallyCatchTheBug(unittest.TestCase):
    """Guards on the guards.

    A structural test that cannot fail is worse than none, because it
    reads as coverage. Each check is run here against markup carrying
    the exact defect it exists to catch.
    """

    SPLIT_SELECT = (
        '<!doctype html><title>t</title><form action="/a">'
        '<select name="expenses_mode">'
        '<option value="pct">% of EGI</option>'
        '<form action="/save"><input type="hidden" name="csrf_token"></form>'
        '<option value="amount">$ per year</option>'
        '</select></form>'
    )
    NESTED_FORM = (
        '<!doctype html><title>t</title>'
        '<form action="/a"><input name="x">'
        '<form action="/save"><input name="y"></form></form>'
    )
    CLEAN = (
        '<!doctype html><title>t</title>'
        '<form action="/a"><select name="expenses_mode">'
        '<option value="pct">% of EGI</option>'
        '<option value="amount">$ per year</option>'
        '</select></form>'
        '<form action="/save"><input type="hidden" name="csrf_token"></form>'
    )

    def test_the_select_check_fires_on_a_split_select(self):
        _, errors, _ = _parse(self.SPLIT_SELECT)
        self.assertTrue(_select_errors(errors))

    def test_the_orphan_check_finds_the_option_that_disappeared(self):
        doc, _, parents = _parse(self.SPLIT_SELECT)
        self.assertEqual(_orphan_options(doc, parents), ["amount"],
                         "the '$ per year' option is the one users lost")

    def test_the_repaired_tree_alone_would_NOT_catch_it(self):
        """The reason the error stream and orphan checks exist.

        Pinning this on purpose: if html5lib ever stops relocating the
        input, this test fails and the simpler assertion becomes
        sufficient. Until then it documents why it is not.
        """
        doc, _, parents = _parse(self.SPLIT_SELECT)
        self.assertEqual(_interactive_inside_select(doc, parents), [],
                         "the parser moves the offender out, which is "
                         "precisely why this cannot be the only check")

    def test_the_form_check_fires_on_a_nested_form(self):
        _, errors, _ = _parse(self.NESTED_FORM)
        self.assertTrue(_nested_form_errors(errors))

    def test_the_nested_form_loses_its_action(self):
        doc, _, _ = _parse(self.NESTED_FORM)
        self.assertEqual([f.get("action") for f in doc.iter("form")], ["/a"],
                         "the inner form is gone; its submit posts to /a")

    def test_all_three_checks_pass_on_correct_markup(self):
        doc, errors, parents = _parse(self.CLEAN)
        self.assertEqual(_select_errors(errors), [])
        self.assertEqual(_orphan_options(doc, parents), [])
        self.assertEqual(_nested_form_errors(errors), [])
        self.assertEqual(_interactive_inside_select(doc, parents), [])

    def test_jinja_comments_do_not_trip_the_checks(self):
        """A comment explaining the bug must not be read as the bug."""
        commented = ('<!doctype html><title>t</title>'
                     '{# beware: <select><input name=c></select> #}'
                     '<p>fine</p>')
        _, errors, _ = _parse(commented)
        self.assertEqual(_select_errors(errors), [])


class TheAnalyzerPageSpecifically(unittest.TestCase):
    """The page the regression was on, asserted on its rendered output.

    Rendered rather than read, because the fields that mattered are
    written by a Jinja loop -- `name="{{ key }}"` -- and only become
    `green`/`yellow`/`orange` once the template runs. Checking the source
    here would be checking the wrong string.
    """

    @classmethod
    def setUpClass(cls):
        from flask import url_for
        from app import app                      # after the sandbox above
        app.config["WTF_CSRF_ENABLED"] = False
        # Resolved, not spelled out: the endpoint is save_grading but the
        # URL is /settings, and asserting on a guessed path would pass or
        # fail for reasons unrelated to form nesting.
        with app.test_request_context():
            cls.grading_action = url_for("deal_analyzer.save_grading")
            cls.analyzer_action = url_for("deal_analyzer.index")
        with app.test_client() as client:
            with client.session_transaction() as session:
                session["_user_id"] = app.config.get("ADMIN_USERNAME")
                session["_fresh"] = True
            cls.html = client.get("/tools/deal-analyzer/").get_data(as_text=True)

    def setUp(self):
        self.doc, self.errors, self.parents = _parse(self.html)

    def test_the_expenses_dropdown_offers_both_modes(self):
        selects = [s for s in self.doc.iter("select")
                   if s.get("name") == "expenses_mode"]
        self.assertEqual(len(selects), 1)
        values = [o.get("value") for o in selects[0].iter("option")]
        self.assertEqual(values, ["pct", "amount"],
                         "'$ per year' must be inside the select, not "
                         "orphaned beside it")

    def test_the_grading_form_survives_as_its_own_form(self):
        actions = [f.get("action") or "" for f in self.doc.iter("form")]
        self.assertIn(
            self.grading_action, actions,
            f"the grading form was absorbed by another form; found {actions}")

    def test_the_grading_fields_are_not_in_the_analyzer_form(self):
        for form in self.doc.iter("form"):
            names = {e.get("name") for e in form.iter() if e.get("name")}
            if not {"green", "yellow", "orange"} <= names:
                continue
            self.assertEqual(form.get("action"), self.grading_action,
                             "the threshold fields belong to the grading "
                             "form, not the valuation form")
            break
        else:
            self.fail("no form carries the grading threshold fields")

    def test_the_save_button_belongs_to_the_grading_form(self):
        """Where the button lives is what actually broke.

        The fields being in the right form is not enough: a submit button
        posts to whichever form contains it, and when the grading form
        was swallowed the button submitted a valuation.
        """
        for form in self.doc.iter("form"):
            labels = [" ".join(b.itertext()) for b in form.iter("button")]
            if any("Save thresholds" in l for l in labels):
                self.assertEqual(form.get("action"), self.grading_action)
                break
        else:
            self.fail("no form contains the 'Save thresholds' button")

    def test_saved_thresholds_are_rendered_back_into_the_form(self):
        """`grading.values` is a dict method, not the 'values' key.

        Jinja resolves dotted access by attribute first, so
        `grading.values[key]` subscripted the built-in method, failed
        silently, and rendered an empty box. The bands saved and graded
        correctly the whole time -- only the screen for editing them came
        up blank, which is the kind of failure nothing errors on.
        """
        from app import app
        from tools import app_settings, grading_settings

        with app_settings.get_connection() as conn:
            grading_settings.save(conn, "3", "7", "12")
        try:
            with app.test_client() as client:
                with client.session_transaction() as session:
                    session["_user_id"] = app.config.get("ADMIN_USERNAME")
                    session["_fresh"] = True
                html = client.get("/tools/deal-analyzer/").get_data(as_text=True)
            doc, _, _ = _parse(html)
            rendered = {e.get("name"): e.get("value") for e in doc.iter("input")
                        if e.get("name") in ("green", "yellow", "orange")}
            self.assertEqual(
                {k: float(v) for k, v in rendered.items() if v not in (None, "")},
                {"green": 3.0, "yellow": 7.0, "orange": 12.0},
                f"the configured thresholds did not come back: {rendered}")
        finally:
            with app_settings.get_connection() as conn:
                grading_settings.clear(conn)

    def test_the_valuation_form_does_not_carry_threshold_fields(self):
        """The second half of the same bug, stated from the other side."""
        for form in self.doc.iter("form"):
            if (form.get("action") or "").split("?")[0] != self.analyzer_action:
                continue
            names = {e.get("name") for e in form.iter() if e.get("name")}
            self.assertEqual(
                names & {"green", "yellow", "orange"}, set(),
                "threshold fields were adopted by the valuation form, so "
                "'Save thresholds' would submit a valuation")


if __name__ == "__main__":
    unittest.main()
