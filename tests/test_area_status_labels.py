"""An area's status is displayed by label, not by its stored key.

THE TRAP THIS REMOVES

Every render site was `{{ area.status|title }}` -- the stored value,
title-cased. That is correct only while every value happens to be a
single lowercase word, which is true of `occupied`, `vacant` and `down`
and is an accident of them being the first three.

The vocabulary is under discussion: a unit that is vacant and needs a
turn is neither `vacant` nor `down`, and `notice` has no equivalent at
all. The natural values for those are multi-word, and the first one added
would have shipped to an inspector's screen as "Vacant_Not_Ready".

That is the same defect as `not_working` appearing in a capital budget,
which the work-options fix had to correct at the same moment it started
admitting those findings. Here the map lands FIRST, so whatever
vocabulary is chosen is a data change rather than a data change plus a
display fix.

DISPLAY-NEUTRAL BY CONSTRUCTION

Every label is byte-identical to what `|title` already produced, and
test_no_label_changes_what_is_on_screen_today pins that. Nothing an
inspector sees moves. Any wording change belongs with the vocabulary
decision, which is Michelle's.
"""

import os
import re
import tempfile
import unittest
from pathlib import Path

import jinja2

from tools import site_dd_db as db


class LabelMapTests(unittest.TestCase):

    def test_every_status_has_a_label(self):
        missing = [s for s in db.AREA_STATUSES if s not in db.AREA_STATUS_LABELS]
        self.assertEqual(missing, [],
                         f"AREA_STATUSES values with no label: {missing}")

    def test_no_stale_labels(self):
        """A label for a value nobody can store is a claim about a
        vocabulary that no longer exists."""
        stale = [k for k in db.AREA_STATUS_LABELS if k not in db.AREA_STATUSES]
        self.assertEqual(stale, [],
                         f"labels for values not in AREA_STATUSES: {stale}")

    def test_no_label_changes_what_is_on_screen_today(self):
        """The property that makes this change provably safe to merge.

        Jinja's `title` filter, not Python's str.title() -- they differ on
        some inputs, and it is Jinja's output that was on screen.
        """
        env = jinja2.Environment()
        for status in db.AREA_STATUSES:
            with self.subTest(status=status):
                before = env.filters["title"](status)
                self.assertEqual(db.AREA_STATUS_LABELS[status], before)

    def test_the_map_is_not_vacuously_equivalent_to_title_casing(self):
        """The positive control: show the trap is real.

        If `|title` handled every plausible value correctly there would be
        no reason for this map to exist. It does not -- a multi-word key
        renders with its underscores intact -- and that is exactly the
        shape the vocabulary discussion is heading toward.
        """
        env = jinja2.Environment()
        rendered = env.filters["title"]("vacant_not_ready")
        self.assertIn("_", rendered,
                      "Jinja's title filter no longer leaks underscores; "
                      "re-examine whether this map is still needed")
        self.assertNotEqual(rendered, "Vacant, needs turn")

    def test_unstated_and_unknown_read_as_not_stated(self):
        """`status` is nullable and create_area() writes NULL for anything
        outside the tuple, so unset is a normal state. A value left by an
        older vocabulary reads as unstated rather than as a raw key."""
        for value in (None, "", "vacant_not_ready", "notice", 0, "OCCUPIED"):
            with self.subTest(value=value):
                self.assertEqual(db.area_status_label(value), "Not stated")

    def test_a_real_status_reads_as_its_label(self):
        self.assertEqual(db.area_status_label(db.AREA_OCCUPIED), "Occupied")
        self.assertEqual(db.area_status_label(db.AREA_VACANT), "Vacant")
        self.assertEqual(db.area_status_label(db.AREA_DOWN), "Down")

    def test_the_assessment_vocabulary_is_untouched(self):
        """site_dd.html renders `a.status|title` for an ASSESSMENT, which
        is draft/complete and a different vocabulary. It is deliberately
        out of scope, and this pins that the two have not been merged."""
        self.assertEqual(db.STATUSES, ("draft", "complete"))
        for s in db.STATUSES:
            self.assertNotIn(s, db.AREA_STATUS_LABELS)


class RenderedPageTests(unittest.TestCase):
    """Both screens that show an area status, driven through the routes."""

    @classmethod
    def setUpClass(cls):
        os.environ["SITE_DD_DB_PATH"] = str(
            Path(tempfile.mkdtemp()) / "area_status_labels.db")
        from tools import site_dd_db as fresh
        cls.db = fresh
        from app import app
        app.config["WTF_CSRF_ENABLED"] = False
        cls.app = app
        with fresh.get_connection() as conn:
            cls.aid = fresh.create_assessment(conn, {
                "property_label": "Status Labels", "assessed_on": "2026-08-19",
                "inspector": "test", "checklist_version": 2})
            cls.areas = {
                status: fresh.create_area(conn, cls.aid,
                                          {"kind": "unit", "label": f"U-{status}",
                                           "status": status})
                for status in fresh.AREA_STATUSES}

    def page(self, url):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = self.app.config.get("ADMIN_USERNAME")
            s["_fresh"] = True
        return c.get(url, follow_redirects=True).get_data(as_text=True)

    # Both display sites render "Unit &middot; <label>". Asserting on that
    # markup rather than on the bare word matters: every unit page also
    # carries a PICKER listing all three labels, so a bare assertIn would
    # pass even if the display site were still emitting the raw key.
    def test_the_assessment_page_lists_areas_by_label(self):
        html = self.page(f"/tools/site-dd/assessment/{self.aid}")
        for status, label in self.db.AREA_STATUS_LABELS.items():
            with self.subTest(status=status):
                self.assertIn(f"&middot; {label}", html)

    def test_the_unit_page_shows_its_status_by_label(self):
        for status, label in self.db.AREA_STATUS_LABELS.items():
            with self.subTest(status=status):
                html = self.page(f"/tools/site-dd/assessment/{self.aid}"
                                 f"/areas/{self.areas[status]}")
                self.assertIn(f"&middot; {label}", html)

    def test_the_display_assertion_is_not_vacuous(self):
        """Control for the two tests above: a label nothing stores must
        NOT be found in that markup, or they prove nothing."""
        html = self.page(f"/tools/site-dd/assessment/{self.aid}")
        self.assertNotIn("&middot; Vacant, needs turn", html)
        self.assertNotIn("&middot; Occupied_Fully", html)

    def test_both_pickers_offer_every_status_by_label(self):
        for url in (f"/tools/site-dd/assessment/{self.aid}",
                    f"/tools/site-dd/assessment/{self.aid}"
                    f"/areas/{self.areas['occupied']}"):
            html = self.page(url)
            for status, label in self.db.AREA_STATUS_LABELS.items():
                with self.subTest(url=url, status=status):
                    self.assertIn(
                        f'value="{status}"', html,
                        "the picker must still POST the stored key")
                    self.assertIn(f">{label}</option>", html)

    def test_an_area_with_no_status_renders_nothing_rather_than_a_key(self):
        with self.db.get_connection() as conn:
            blank = self.db.create_area(conn, self.aid,
                                        {"kind": "unit", "label": "U-blank"})
        html = self.page(f"/tools/site-dd/assessment/{self.aid}"
                         f"/areas/{blank}")
        self.assertIn("U-blank", html)
        self.assertNotIn("None", html.split("U-blank")[1][:200])


if __name__ == "__main__":
    unittest.main()


class AssessmentStatusLabelTests(unittest.TestCase):
    """The same construction, one vocabulary over.

    Nothing was broken: `draft` and `complete` are single lowercase
    words, so `{{ a.status|title }}` happened to be right. That is an
    accident of the two values chosen, and this map exists so the
    accident never has to hold.
    """

    def test_every_status_has_a_label(self):
        missing = [s for s in db.STATUSES if s not in db.ASSESSMENT_STATUS_LABELS]
        self.assertEqual(missing, [], f"STATUSES with no label: {missing}")

    def test_no_stale_labels(self):
        stale = [k for k in db.ASSESSMENT_STATUS_LABELS if k not in db.STATUSES]
        self.assertEqual(stale, [], f"labels for unknown statuses: {stale}")

    def test_no_label_changes_what_is_on_screen_today(self):
        env = jinja2.Environment()
        for status in db.STATUSES:
            with self.subTest(status=status):
                self.assertEqual(db.ASSESSMENT_STATUS_LABELS[status],
                                 env.filters["title"](status))

    def test_an_unknown_status_reads_as_draft_not_as_blank(self):
        """Unlike an area's status this column is NOT NULL with a default
        of `draft`, so there is no unstated state to report. A value from
        an older vocabulary is most honestly read as "not finished"."""
        for value in (None, "", "in_review", 0):
            with self.subTest(value=value):
                self.assertEqual(db.assessment_status_label(value), "Draft")

    def test_the_two_vocabularies_stay_separate(self):
        for s in db.STATUSES:
            self.assertNotIn(s, db.AREA_STATUS_LABELS)
        for s in db.AREA_STATUSES:
            self.assertNotIn(s, db.ASSESSMENT_STATUS_LABELS)


class TemplatesCallTheAccessorTests(unittest.TestCase):
    """No template may subscript a label map.

    THE REASON, WHICH IS NOT THE ONE FIRST WRITTEN DOWN

    An earlier note claimed `labels[key]` RAISES on a missing key. It does
    not. This app runs Jinja's default Undefined, so a missing key renders
    as the empty string -- verified below rather than asserted.

    Silent is the worse half of the trade. A display site guarded by
    `{% if area.status %}` emits "&middot; " with nothing after it when
    the value is unrecognised: a dangling separator, and no trace that
    anything was wrong. The accessor answers "Not stated", which is a
    statement rather than a gap.
    """

    TEMPLATES = sorted(Path("templates").rglob("*.html"))

    def test_the_subscript_renders_empty_rather_than_raising(self):
        """The premise, checked. If Jinja is ever configured with
        StrictUndefined this test fails and the docstring above needs
        rewriting -- which is the point of pinning it."""
        from app import app
        self.assertEqual(app.jinja_env.undefined.__name__, "Undefined")
        out = app.jinja_env.from_string("[{{ m[k] }}]").render(
            m=db.AREA_STATUS_LABELS, k="vacant_not_ready")
        self.assertEqual(out, "[]")

    def test_the_accessor_states_something_where_the_subscript_is_silent(self):
        from app import app
        tmpl = "{%- if area.status %} &middot; {{ shown }}{% endif %}"
        sub = app.jinja_env.from_string(
            tmpl.replace("shown", "m[area.status]")).render(
                area={"status": "vacant_not_ready"}, m=db.AREA_STATUS_LABELS)
        acc = app.jinja_env.from_string(
            tmpl.replace("shown", "f(area.status)")).render(
                area={"status": "vacant_not_ready"}, f=db.area_status_label)
        self.assertEqual(sub, " &middot; ")          # dangling separator
        self.assertEqual(acc, " &middot; Not stated")

    # Scoped to the STATUS maps deliberately.
    #
    # Written broad first, over every `*_labels[` in templates/, and it
    # found ten pre-existing subscripts. All ten are safe, for a reason
    # worth writing down rather than allowlisting:
    #
    #   condition_labels[c]   `c` iterates the CONDITIONS tuple, so the
    #                         key is present by construction
    #   room_type_labels[...] reads a stored value, but site_dd.py:622
    #                         rejects anything outside ROOM_TYPE_LABELS
    #                         before create_room() is reached
    #   source_labels[s]      loops over known keys
    #
    # So they are guarded at the loop or at the route rather than at the
    # read. That is a weaker place for a guard to live -- a second writer
    # has to remember it -- but it is not a live hazard, and widening
    # this test to force ten unrelated edits is scope this run did not
    # ask for. Recorded so it reads as a decision, not an omission.
    STATUS_MAPS = re.compile(r"\b((?:area|assessment)_status_labels)\s*\[")

    def test_no_template_subscripts_a_status_label_map(self):
        offenders = []
        for path in self.TEMPLATES:
            text = path.read_text(encoding="utf-8")
            for match in self.STATUS_MAPS.finditer(text):
                line = text[:match.start()].count("\n") + 1
                offenders.append(f"{path.as_posix()}:{line} {match.group(1)}[")
        self.assertEqual(offenders, [],
                         "call the accessor instead:\n  " + "\n  ".join(offenders))

    def test_the_subscript_guard_is_not_vacuous(self):
        """It has to match something, or it certifies nothing."""
        self.assertTrue(self.STATUS_MAPS.search("{{ area_status_labels[st] }}"))
        self.assertTrue(self.STATUS_MAPS.search("{{ assessment_status_labels[s] }}"))
        self.assertIsNone(self.STATUS_MAPS.search("{{ area_status_label(st) }}"))

    def test_no_status_is_rendered_by_bare_title_casing(self):
        offenders = []
        for path in self.TEMPLATES:
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"\bstatus\s*\|\s*title\b", text):
                line = text[:match.start()].count("\n") + 1
                offenders.append(f"{path.as_posix()}:{line}")
        self.assertEqual(offenders, [],
                         "a stored key title-cased is not a label:\n  "
                         + "\n  ".join(offenders))
