"""
Unit tests for the Site DD unit/room walkthrough: room ordering, the
per-room-type checklists, copy-layout, and the unit roll-up.

Same discipline as the rest: assertions restate the expected result
independently rather than asking the code under test what it thinks.
"""

import tempfile
import unittest
from pathlib import Path

from tools import site_dd_conditions as cond
from tools import site_dd_db as db
from tools import site_dd_unit_checklist as uc


class RoomOrderTests(unittest.TestCase):
    """The feature: the order rooms are tapped is the order they are walked."""

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "sd.db"
        with db.get_connection(self.path) as conn:
            self.aid = db.create_assessment(conn, {
                "property_label": "Test", "checklist_version": 2})
            self.area = db.create_area(conn, self.aid,
                                       {"kind": "unit", "label": "204"})

    def test_tap_order_is_walk_order(self):
        tapped = ["kitchen", "living", "bedroom", "bathroom", "bedroom"]
        with db.get_connection(self.path) as conn:
            for t in tapped:
                db.create_room(conn, self.area, t)
            rooms = db.list_rooms(conn, self.area)
        self.assertEqual([r["room_type"] for r in rooms], tapped)
        self.assertEqual([r["sort_order"] for r in rooms], [0, 1, 2, 3, 4])

    def test_a_different_tap_order_gives_a_different_walk_order(self):
        """Nothing sorts alphabetically or by type anywhere -- if it did,
        both of these would come back the same."""
        with db.get_connection(self.path) as conn:
            other = db.create_area(conn, self.aid, {"kind": "unit", "label": "205"})
            for t in ["bathroom", "bedroom", "kitchen"]:
                db.create_room(conn, other, t)
            for t in ["kitchen", "bedroom", "bathroom"]:
                db.create_room(conn, self.area, t)
            a = [r["room_type"] for r in db.list_rooms(conn, self.area)]
            b = [r["room_type"] for r in db.list_rooms(conn, other)]
        self.assertEqual(a, ["kitchen", "bedroom", "bathroom"])
        self.assertEqual(b, ["bathroom", "bedroom", "kitchen"])
        self.assertNotEqual(a, b)

    def test_deleting_a_room_leaves_the_rest_in_order(self):
        with db.get_connection(self.path) as conn:
            ids = [db.create_room(conn, self.area, t)
                   for t in ["kitchen", "living", "bedroom"]]
            db.delete_room(conn, ids[1])
            rooms = db.list_rooms(conn, self.area)
        self.assertEqual([r["room_type"] for r in rooms], ["kitchen", "bedroom"])

    def test_rooms_belong_to_their_area_only(self):
        with db.get_connection(self.path) as conn:
            other = db.create_area(conn, self.aid, {"kind": "unit", "label": "205"})
            db.create_room(conn, self.area, "kitchen")
            db.create_room(conn, other, "bathroom")
            self.assertEqual(len(db.list_rooms(conn, self.area)), 1)
            self.assertEqual(len(db.list_rooms(conn, other)), 1)


class CopyLayoutTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "sd.db"
        with db.get_connection(self.path) as conn:
            self.aid = db.create_assessment(conn, {
                "property_label": "Test", "checklist_version": 2})
            self.src = db.create_area(conn, self.aid, {"kind": "unit", "label": "204"})
            self.dst = db.create_area(conn, self.aid, {"kind": "unit", "label": "205"})
            self.src_rooms = [db.create_room(conn, self.src, t)
                              for t in ["kitchen", "living", "bathroom", "bedroom"]]
            # Findings recorded in the source unit.
            db.upsert_findings(conn, self.aid, [{
                "scope": "room", "area_id": self.src, "room_id": self.src_rooms[0],
                "item_key": "flooring", "condition": "replace"}])

    def test_layout_copies(self):
        with db.get_connection(self.path) as conn:
            n = db.copy_layout(conn, self.src, self.dst)
            rooms = db.list_rooms(conn, self.dst)
        self.assertEqual(n, 4)
        self.assertEqual([r["room_type"] for r in rooms],
                         ["kitchen", "living", "bathroom", "bedroom"])
        self.assertEqual([r["sort_order"] for r in rooms], [0, 1, 2, 3])

    def test_findings_do_NOT_copy(self):
        """The distinction the whole feature turns on. Two units can have
        identical layouts and completely different condition; copying an
        inspection would fabricate an observation nobody made."""
        with db.get_connection(self.path) as conn:
            db.copy_layout(conn, self.src, self.dst)
            dst_rooms = db.list_rooms(conn, self.dst)
            for r in dst_rooms:
                found = db.get_findings(conn, self.aid, self.dst, r["id"])
                self.assertEqual(found, {}, "a copied unit must start empty")
            # The source keeps its own.
            src_found = db.get_findings(conn, self.aid, self.src, self.src_rooms[0])
            self.assertEqual(src_found["flooring"][0]["condition"], "replace")

    def test_copying_twice_does_not_duplicate_rooms(self):
        with db.get_connection(self.path) as conn:
            db.copy_layout(conn, self.src, self.dst)
            db.copy_layout(conn, self.src, self.dst)
            self.assertEqual(len(db.list_rooms(conn, self.dst)), 4)

    def test_finding_count_gates_the_offer(self):
        with db.get_connection(self.path) as conn:
            self.assertEqual(db.area_finding_count(conn, self.dst), 0)
            self.assertEqual(db.area_finding_count(conn, self.src), 1)
            # A row with no condition is not a finding -- an untouched form
            # save must not lock a unit out of copy-layout.
            db.upsert_findings(conn, self.aid, [{
                "scope": "room", "area_id": self.dst, "room_id": None,
                "item_key": "hvac", "condition": None}])
            self.assertEqual(db.area_finding_count(conn, self.dst), 0)


class ChecklistContentTests(unittest.TestCase):
    def test_every_room_gets_the_shared_items(self):
        for room_type, _ in uc.ROOM_TYPES:
            keys = [i["key"] for i in uc.items_for_room(room_type)]
            for shared in ("flooring_type", "flooring", "walls_ceiling",
                           "windows", "outlets_switches", "lighting"):
                self.assertIn(shared, keys, f"{room_type} missing {shared}")

    def test_flooring_type_is_separate_from_flooring_condition(self):
        items = uc.item_map(uc.items_for_room("living"))
        self.assertEqual(items["flooring_type"]["kind"], uc.KIND_CHOICE)
        self.assertFalse(items["flooring_type"]["with_condition"],
                         "a floor's material is not a wear state")
        self.assertEqual(items["flooring"]["kind"], uc.KIND_CONDITION)
        self.assertIn(("carpet", "Carpet"), items["flooring_type"]["options"])

    def test_kitchen_appliances_are_individual_presence_items(self):
        items = uc.item_map(uc.items_for_room("kitchen"))
        for key in ("appliance_range", "appliance_fridge", "appliance_dishwasher",
                    "appliance_microwave", "appliance_disposal"):
            self.assertIn(key, items)
            self.assertEqual(items[key]["kind"], uc.KIND_CHOICE)
            self.assertIn(("hookup_only", "Hookup only"), items[key]["options"])
            self.assertIn(("absent", "Not there"), items[key]["options"])
            self.assertTrue(items[key]["with_condition"],
                            "an appliance that IS there still has a condition")
        for key in ("cabinets", "countertops", "sink_faucet"):
            self.assertEqual(items[key]["kind"], uc.KIND_CONDITION)
        self.assertIn("gfci", items)

    def test_bathroom_items(self):
        items = uc.item_map(uc.items_for_room("bathroom"))
        for key in ("tub_shower", "toilet", "vanity_sink", "exhaust_fan",
                    "gfci", "visible_leaks"):
            self.assertIn(key, items)

    def test_bedroom_items(self):
        items = uc.item_map(uc.items_for_room("bedroom"))
        for key in ("closet", "egress_window", "smoke_alarm"):
            self.assertIn(key, items)
        self.assertFalse(items["smoke_alarm"]["with_condition"],
                         "a missing alarm has no condition")

    def test_unit_wide_items(self):
        items = uc.item_map(uc.items_for_unit())
        for key in ("smoke_alarm_unit", "co_alarm", "water_heater",
                    "water_heater_gal", "water_heater_age", "hvac", "hvac_age"):
            self.assertIn(key, items)
        self.assertEqual(items["water_heater_gal"]["kind"], uc.KIND_NUMBER)
        self.assertEqual(items["water_heater_gal"]["measure"], "gal")
        self.assertEqual(items["hvac_age"]["measure"], "yr")

    def test_keys_repeat_across_rooms_deliberately(self):
        """`flooring` means the same question everywhere. Findings are
        unique on (assessment, area, room, item), so the same key in two
        rooms is two rows without needing two names."""
        self.assertIn("flooring", [i["key"] for i in uc.items_for_room("kitchen")])
        self.assertIn("flooring", [i["key"] for i in uc.items_for_room("bedroom")])

    def test_gfci_appears_where_code_requires_it(self):
        for room_type in ("kitchen", "bathroom"):
            self.assertIn("gfci", [i["key"] for i in uc.items_for_room(room_type)])

    def test_option_validation_rejects_invented_values(self):
        item = uc.item_map(uc.items_for_room("kitchen"))["appliance_range"]
        self.assertTrue(uc.is_valid_option(item, "hookup_only"))
        for bad in ("HOOKUP_ONLY", "maybe", "", None, 2, "excellent"):
            self.assertFalse(uc.is_valid_option(item, bad))


class UnitRollupTests(unittest.TestCase):
    def _rooms(self):
        return [
            {"id": 1, "room_type": "kitchen", "label": None, "sort_order": 0},
            {"id": 2, "room_type": "bedroom", "label": None, "sort_order": 1},
        ]

    def test_rollup_spans_every_room_plus_the_unit_items(self):
        by_room = {
            1: {"flooring": "replace", "walls_ceiling": "good", "cabinets": "repair"},
            2: {"flooring": "good", "closet": "satisfactory"},
        }
        unit = {"water_heater": "repair", "hvac": "good"}
        s = uc.summarize_unit(by_room, self._rooms(), unit)
        self.assertEqual(s["counts"]["replace"], 1)
        self.assertEqual(s["counts"]["repair"], 2)   # cabinets + water heater
        self.assertEqual(s["counts"]["good"], 3)     # walls + flooring + hvac
        self.assertEqual(s["counts"]["satisfactory"], 1)
        self.assertEqual(s["work_count"], 3)
        self.assertEqual(s["assessed_count"], 7)
        self.assertEqual(s["worst"], "replace")

    def test_per_room_rows_are_reported_separately(self):
        by_room = {1: {"flooring": "replace"}, 2: {}}
        s = uc.summarize_unit(by_room, self._rooms(), {})
        kitchen = next(r for r in s["rooms"] if r["room"]["id"] == 1)
        bedroom = next(r for r in s["rooms"] if r["room"]["id"] == 2)
        self.assertEqual(kitchen["work_count"], 1)
        self.assertEqual(kitchen["worst"], "replace")
        self.assertEqual(bedroom["work_count"], 0)
        self.assertIsNone(bedroom["worst"])

    def test_choices_are_not_counted_as_conditions(self):
        """'Hookup only' is a fact about the unit, not a rating. Counting
        it beside wear states would produce a number meaning nothing."""
        by_room = {1: {"flooring_type": "carpet", "appliance_range": "absent"}, 2: {}}
        s = uc.summarize_unit(by_room, self._rooms(), {})
        self.assertEqual(s["assessed_count"], 0)
        self.assertEqual(sum(s["counts"].values()), 0)

    def test_totals_reconcile_with_the_room_rows(self):
        by_room = {
            1: {"flooring": "repair", "cabinets": "replace", "windows": "good"},
            2: {"flooring": "good", "closet": "repair"},
        }
        s = uc.summarize_unit(by_room, self._rooms(), {"hvac": "replace"})
        room_work = sum(r["work_count"] for r in s["rooms"])
        self.assertEqual(room_work, 3, "2 in the kitchen, 1 in the bedroom")
        self.assertEqual(s["work_count"], room_work + 1, "plus the unit-wide HVAC")

    def test_empty_unit(self):
        s = uc.summarize_unit({}, [], {})
        self.assertEqual(s["work_count"], 0)
        self.assertEqual(s["assessed_count"], 0)
        self.assertIsNone(s["worst"])
        self.assertEqual(s["completion_pct"], 0.0)

    def test_the_scale_is_shared_with_the_property_scope(self):
        """One definition of a condition across both scopes -- a unit
        summary and a property summary cannot disagree about what
        'needs work' means."""
        self.assertEqual(uc.summarize_unit({}, [], {})["ordered_counts"][0]["key"],
                         cond.REPLACE)


class AreaTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "sd.db"
        with db.get_connection(self.path) as conn:
            self.aid = db.create_assessment(conn, {
                "property_label": "Test", "checklist_version": 2})

    def test_kinds_and_statuses_are_validated(self):
        with db.get_connection(self.path) as conn:
            a = db.create_area(conn, self.aid, {"kind": "nonsense", "label": "X",
                                                "status": "nonsense"})
            row = db.get_area(conn, a)
        self.assertEqual(row["kind"], "unit", "an unknown kind falls back to unit")
        self.assertIsNone(row["status"], "an unknown status is not stored")

    def test_status_vocabulary_supports_the_lite_mode(self):
        self.assertEqual(db.AREA_STATUSES, ("occupied", "vacant", "down"))

    def test_deleting_an_area_removes_its_rooms_and_findings(self):
        with db.get_connection(self.path) as conn:
            area = db.create_area(conn, self.aid, {"kind": "unit", "label": "204"})
            room = db.create_room(conn, area, "kitchen")
            db.upsert_findings(conn, self.aid, [
                {"scope": "room", "area_id": area, "room_id": room,
                 "item_key": "flooring", "condition": "good"},
                {"scope": "unit", "area_id": area, "room_id": None,
                 "item_key": "hvac", "condition": "repair"},
            ])
            db.delete_area(conn, area)
            self.assertEqual(db.list_rooms(conn, area), [])
            self.assertEqual(db.get_findings(conn, self.aid, area, room), {})
            self.assertEqual(db.get_findings(conn, self.aid, area, None), {},
                             "unit-scope findings must go too")

    def test_property_scope_survives_a_unit_being_deleted(self):
        """Both scopes coexist in one assessment and must not disturb
        each other."""
        with db.get_connection(self.path) as conn:
            db.upsert_findings(conn, self.aid, [
                {"scope": "property", "area_id": None, "room_id": None,
                 "item_key": "roof_covering", "condition": "repair"}])
            area = db.create_area(conn, self.aid, {"kind": "unit", "label": "204"})
            db.delete_area(conn, area)
            prop = db.get_findings(conn, self.aid, None, None)
        self.assertEqual(prop["roof_covering"][0]["condition"], "repair")


if __name__ == "__main__":
    unittest.main()
