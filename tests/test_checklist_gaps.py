"""Items Paresh's v7 form asks about and ours did not.

WHERE THESE CAME FROM

His KoboToolbox forms turned up after the Site DD rebuild, having been in
production use throughout it -- the previous handoff recorded that no
reference implementation existed, which was false and load-bearing. The
content of those forms is mature in ways ours is not.

WHAT WAS TAKEN, AND WHAT WAS NOT

The content. Not the shape.

His form carries roughly twenty-five item-specific vocabularies where the
condition value IS the scope of work: closet is Replace Rod / Replace
Shelves / Replace Rod and Shelves, bathtub is Replace / Resurface / Good.
That is a real answer to pricing and it is deliberately NOT adopted here.
Our five-state scale produces a value that is comparable across items,
and the rollup, the completion percentage and WORK_CONDITIONS all consume
that. Twenty-five vocabularies cannot answer "worst condition recorded".

Scope-of-work granularity goes into `detail` instead, which already
exists for exactly that and leaves every recorded finding valid.

MISSING-NESS WAS ALREADY SOLVED, AND NOT BY THE CONDITION SCALE

His detector scale is Working / Not Working / Missing, and "Missing" is
not a position on a wear scale -- Replace implies something is there to
replace. This codebase settled that before the question was asked, in
site_dd_unit_checklist's own comment: "an item that is present has a
condition; an alarm that is missing does not need one, because 'missing'
is the whole answer."

So an item that wears takes the five-state scale, and an item that is
present-or-not is a KIND_CHOICE carrying its own states. The new items
follow that split rather than stretching the scale to cover absence.

NO COST WAS INVENTED

Only gfci had a researched figure already. The rest are unpriced with a
written reason -- mold because published figures span two orders of
magnitude for the same words, thermostat because like-for-like and smart
differ by an order of magnitude, the extinguisher because a service visit
and a replacement are different jobs the checklist deliberately tells
apart. A plausible number would have been worse than no number.
"""

import os
import re
import tempfile
import unittest
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="checklist-gaps-")
for _var in ("SITE_DD_DB_PATH", "DEAL_DIVE_DB_PATH", "RENT_COMPS_DB_PATH",
             "MARKET_DATA_DB_PATH", "UNDERWRITING_DB_PATH",
             "SCORECARD_PRO_DB_PATH", "FIRE_METRICS_DB_PATH",
             "FEEDBACK_DB_PATH", "INVESTOR_REPORT_DB_PATH",
             "INVESTOR_NOTES_DB_PATH", "OPENAI_USAGE_DB_PATH",
             "APP_SETTINGS_DB_PATH"):
    os.environ[_var] = os.path.join(_SANDBOX, _var.lower() + ".db")
os.environ.setdefault("UPLOAD_FOLDER_PATH", os.path.join(_SANDBOX, "uploads"))

ROOT = Path(__file__).resolve().parent.parent

from tools import site_dd_checklist as cl              # noqa: E402
from tools import site_dd_conditions as cond           # noqa: E402
from tools import site_dd_db as db                     # noqa: E402
from tools import site_dd_reference_costs as ref       # noqa: E402
from tools import site_dd_unit_checklist as uc         # noqa: E402

# gfci is deliberately NOT here. It was on the "missing" list and should
# not have been -- it already existed as a kitchen and bathroom item,
# scoped to the wet areas where the protection is required. The tests
# below assert that it stayed where it was.
NEW_ITEMS = ("mold", "pest_evidence", "pest_type", "thermostat",
             "fire_extinguisher")
# The five built as KIND_CHOICE. thermostat is the one that wears.
CHOICE_ITEMS = ("mold", "pest_evidence", "pest_type",
                "fire_extinguisher")


def all_keys():
    keys = []
    for room_type, _ in uc.ROOM_TYPES:
        keys += [i["key"] for i in uc.items_for_room(room_type)]
    keys += [i["key"] for i in uc.items_for_unit()]
    return keys


def item(key):
    for room_type, _ in uc.ROOM_TYPES:
        for i in uc.items_for_room(room_type):
            if i["key"] == key:
                return i
    for i in uc.items_for_unit():
        if i["key"] == key:
            return i
    raise AssertionError(f"{key} is not in the catalogue")


class TheItemsExistTests(unittest.TestCase):
    def test_every_new_item_is_in_the_catalogue(self):
        keys = set(all_keys())
        for k in NEW_ITEMS:
            with self.subTest(k):
                self.assertIn(k, keys)

    def test_mold_and_pests_are_asked_in_every_room(self):
        """Found in a place. 'Which room' is the first thing anyone asks."""
        for room_type, _ in uc.ROOM_TYPES:
            with self.subTest(room_type):
                keys = {i["key"] for i in uc.items_for_room(room_type)}
                self.assertIn("mold", keys)
                self.assertIn("pest_evidence", keys)
                self.assertIn("pest_type", keys)

    def test_thermostat_and_extinguisher_are_unit_wide(self):
        keys = {i["key"] for i in uc.items_for_unit()}
        for k in ("thermostat", "fire_extinguisher"):
            with self.subTest(k):
                self.assertIn(k, keys)

    def test_gfci_stayed_where_it_already_was(self):
        """It was on the gap list by mistake. It lives in the wet areas,
        which is a better question than once per unit, and adding a
        unit-wide copy made it the only cross-scope duplicate key in the
        catalogue."""
        for room_type in ("kitchen", "bathroom"):
            with self.subTest(room_type):
                self.assertIn("gfci",
                              {i["key"] for i in uc.items_for_room(room_type)})
        self.assertNotIn("gfci", {i["key"] for i in uc.items_for_unit()})

    def test_no_key_appears_in_both_room_and_unit_scope(self):
        """The check that caught the duplicate."""
        room = set()
        for room_type, _ in uc.ROOM_TYPES:
            room |= {i["key"] for i in uc.items_for_room(room_type)}
        unit = {i["key"] for i in uc.items_for_unit()}
        self.assertEqual(room & unit, set())

    def test_they_are_known_to_the_catalogue_lookup(self):
        """is_known_item gates what may be saved at all."""
        for k in NEW_ITEMS:
            with self.subTest(k):
                self.assertTrue(uc.is_known_item(k))


class TheShapeIsOursNotHisTests(unittest.TestCase):
    def test_a_thermostat_wears_so_it_takes_the_condition_scale(self):
        self.assertEqual(item("thermostat")["kind"], uc.KIND_CONDITION)

    def test_things_that_can_be_absent_are_choices(self):
        for k in CHOICE_ITEMS:
            with self.subTest(k):
                self.assertEqual(item(k)["kind"], uc.KIND_CHOICE)

    def test_and_they_do_not_also_acquire_a_condition(self):
        """'Missing' is the whole answer; a wear rating on top is noise.

        This is the assertion the whole shape decision rests on.
        """
        for k in CHOICE_ITEMS:
            with self.subTest(k):
                self.assertFalse(item(k)["with_condition"])

    def test_mold_has_three_states_not_two(self):
        """Suspected is the state that triggers a specialist rather than
        a work order, and yes/no cannot express it."""
        self.assertEqual({v for v, _ in item("mold")["options"]},
                         {"none", "suspected", "present"})

    def test_the_extinguisher_records_inspection_currency(self):
        """The tag date, not the gauge."""
        self.assertEqual({v for v, _ in item("fire_extinguisher")["options"]},
                         {"current", "expired", "missing"})

    def test_pest_evidence_and_pest_type_ask_different_questions(self):
        self.assertNotEqual({v for v, _ in item("pest_evidence")["options"]},
                            {v for v, _ in item("pest_type")["options"]})

    def test_every_option_value_is_valid_for_its_item(self):
        for k in NEW_ITEMS:
            spec = item(k)
            for value, _ in (spec["options"] or ()):
                with self.subTest(item=k, value=value):
                    self.assertTrue(uc.is_valid_option(spec, value))

    def test_an_invented_option_is_refused(self):
        self.assertFalse(uc.is_valid_option(item("mold"), "definitely_fine"))


class EveryNewItemIsAccountedForTests(unittest.TestCase):
    """Priced, explicitly unpriced, or explicitly not a cost item."""

    def test_none_are_unknown(self):
        for k in NEW_ITEMS:
            with self.subTest(k):
                self.assertNotEqual(ref.status(k), "unknown")

    def test_none_of_the_new_items_carries_a_researched_figure(self):
        """gfci was the one that did, and it turned out to already exist."""
        for k in ("mold", "thermostat", "fire_extinguisher", "pest_evidence"):
            with self.subTest(k):
                self.assertEqual(ref.status(k), "unpriced")

    def test_no_unpriced_item_can_produce_a_number(self):
        """Not priced, and not zero either -- zero sums into a total as
        though the work were free."""
        for k in ("mold", "thermostat", "fire_extinguisher", "pest_evidence"):
            with self.subTest(k):
                self.assertIsNone(ref.for_item(k))

    def test_pest_type_is_not_a_cost_item(self):
        """It identifies the pest; the work hangs off pest_evidence, and
        pricing both would count the same job twice."""
        self.assertEqual(ref.status("pest_type"), "not_a_cost_item")

    def test_every_unpriced_reason_is_written_out(self):
        for k in ("mold", "thermostat", "fire_extinguisher"):
            with self.subTest(k):
                self.assertGreater(len(ref.UNPRICED[k].strip()), 40)

    def test_the_reasons_reach_the_report_a_person_reads(self):
        labels = {k: k for k in NEW_ITEMS}
        rows = {r["key"]: r for r in ref.unpriced_report(labels)}
        for k in ("mold", "thermostat", "fire_extinguisher"):
            with self.subTest(k):
                self.assertIn(k, rows)
                self.assertTrue(rows[k]["reason"].strip())

    def test_every_new_item_has_a_capex_category(self):
        for k in NEW_ITEMS:
            with self.subTest(k):
                self.assertIn(uc.category_for(k), cl.CATEGORY_NAMES)

    def test_mold_and_pests_are_environmental_not_interior(self):
        """Specialist scopes with their own contractor. Under Interior &
        Units they would be budgeted beside repainting a bedroom."""
        for k in ("mold", "pest_evidence", "pest_type"):
            with self.subTest(k):
                self.assertEqual(uc.category_for(k), "access_environmental")

    def test_the_thermostat_is_mep(self):
        self.assertEqual(uc.category_for("thermostat"), "mep")


class ReachableByARealUserTests(unittest.TestCase):
    """Distinct from correctness, and the failure this app has shipped
    four times: correct code nobody can navigate to."""

    @classmethod
    def setUpClass(cls):
        from app import app
        app.config["WTF_CSRF_ENABLED"] = False
        cls.app = app
        with db.get_connection() as conn:
            cls.aid = db.create_assessment(conn, {
                "property_label": "Checklist Gaps", "assessed_on": "2026-08-18",
                "inspector": "test", "checklist_version": 2})
            cls.area = db.create_area(conn, cls.aid,
                                      {"kind": "unit", "label": "1"})
            cls.room = db.create_room(conn, cls.area, "bedroom")

    def page(self, url):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = self.app.config.get("ADMIN_USERNAME")
            s["_fresh"] = True
        return c.get(url, follow_redirects=True).get_data(as_text=True)

    def test_the_room_page_asks_about_mold_and_pests(self):
        html = self.page(f"/tools/site-dd/assessment/{self.aid}"
                         f"/areas/{self.area}/rooms/{self.room}")
        for name in ("mold", "pest_evidence", "pest_type"):
            with self.subTest(name):
                self.assertIn(name, html)

    def test_the_unit_page_asks_about_thermostat_and_extinguisher(self):
        html = self.page(f"/tools/site-dd/assessment/{self.aid}"
                         f"/areas/{self.area}")
        for name in ("thermostat", "fire_extinguisher"):
            with self.subTest(name):
                self.assertIn(name, html)

    def test_the_choice_items_render_their_states_not_condition_buttons(self):
        html = self.page(f"/tools/site-dd/assessment/{self.aid}"
                         f"/areas/{self.area}/rooms/{self.room}")
        self.assertIn("Suspected", html)
        self.assertIn("Droppings", html)


class NothingRecordedWasDisturbedTests(unittest.TestCase):
    """Pure addition. No migration, no existing finding affected."""

    def test_the_condition_scale_is_unchanged(self):
        self.assertEqual(
            cond.CONDITIONS,
            ("excellent", "good", "satisfactory", "repair", "replace"))

    def test_work_conditions_are_unchanged(self):
        self.assertEqual(cond.WORK_CONDITIONS, ("repair", "replace"))

    def test_every_pre_existing_item_is_still_present(self):
        keys = set(all_keys())
        for k in ("flooring", "walls_ceiling", "smoke_alarm_unit", "co_alarm",
                  "water_heater", "hvac", "entry_door", "cabinets",
                  "countertops", "sink_faucet", "toilet", "tub_shower"):
            with self.subTest(k):
                self.assertIn(k, keys)

    def test_no_unit_item_key_collides(self):
        unit_keys = [i["key"] for i in uc.items_for_unit()]
        self.assertEqual(len(unit_keys), len(set(unit_keys)))

    def test_no_room_item_key_collides(self):
        for room_type, _ in uc.ROOM_TYPES:
            keys = [i["key"] for i in uc.items_for_room(room_type)]
            with self.subTest(room_type):
                self.assertEqual(len(keys), len(set(keys)))


if __name__ == "__main__":
    unittest.main()
