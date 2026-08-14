"""
Unit tests for the capex category on room and unit checklist items.

The defect: site_dd.py wrote item["kind"] into the findings table's
category_key column, so every room and unit row carried "condition",
"choice" or "number" where a capex heading should have been -- and the
Underwriting export emitted those as budget categories.

Two facts were sharing one column. They are unrelated: the kind decides
what input a form draws, the category decides what budget heading the
work lands under. Nothing ever read the column back expecting a kind,
which is why the fix is a correction at the write site plus a backfill,
not a data migration in any interesting sense.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools import site_dd_bank as bank
from tools import site_dd_checklist as cl
from tools import site_dd_costs as costs
from tools import site_dd_db as db
from tools import site_dd_unit_checklist as uc

KINDS = (uc.KIND_CONDITION, uc.KIND_CHOICE, uc.KIND_NUMBER)


def _all_items():
    seen = {}
    for room_type, _ in uc.ROOM_TYPES:
        for item in uc.items_for_room(room_type):
            seen[item["key"]] = item
    for item in uc.items_for_unit():
        seen[item["key"]] = item
    return seen


class CatalogueTests(unittest.TestCase):
    def test_every_checklist_item_has_a_category(self):
        missing = [k for k, i in _all_items().items() if not i.get("category")]
        self.assertEqual(missing, [], f"uncategorised items: {missing}")

    def test_every_category_is_in_the_real_vocabulary(self):
        """The same vocabulary as the property checklist and the item
        bank. A fourth vocabulary would recreate the problem in a new
        place."""
        for key, item in _all_items().items():
            with self.subTest(key):
                self.assertIn(item["category"], cl.CATEGORY_NAMES)

    def test_no_category_is_an_input_kind(self):
        """The regression, stated directly."""
        for key, item in _all_items().items():
            with self.subTest(key):
                self.assertNotIn(item["category"], KINDS)

    def test_the_kind_is_still_there_and_still_correct(self):
        """The cleanup must not cost the information it was tangled with."""
        items = _all_items()
        self.assertEqual(items["flooring_type"]["kind"], uc.KIND_CHOICE)
        self.assertEqual(items["flooring"]["kind"], uc.KIND_CONDITION)
        self.assertEqual(items["hvac_age"]["kind"], uc.KIND_NUMBER)
        for key, item in items.items():
            with self.subTest(key):
                self.assertIn(item["kind"], KINDS)

    def test_kind_and_category_are_independent(self):
        """Items sharing a kind can differ in category, and vice versa --
        which is the whole reason they cannot be one column."""
        items = _all_items()
        self.assertEqual(items["toilet"]["kind"], items["flooring"]["kind"])
        self.assertNotEqual(items["toilet"]["category"],
                            items["flooring"]["category"])
        self.assertEqual(items["water_heater"]["category"],
                         items["hvac_age"]["category"])
        self.assertNotEqual(items["water_heater"]["kind"],
                            items["hvac_age"]["kind"])

    def test_the_mapping_has_no_dead_entries(self):
        stale = sorted(set(uc.CATEGORIES_BY_ITEM) - set(_all_items()))
        self.assertEqual(stale, [], f"mapped keys no item uses: {stale}")

    def test_an_unmapped_key_is_none_rather_than_a_catch_all(self):
        self.assertIsNone(uc.category_for("not_a_real_item"))

    def test_the_mapping_agrees_with_the_property_checklist(self):
        """Where both scopes describe the same kind of work they must
        agree, or a budget groups the same job under two headings."""
        self.assertEqual(uc.category_for("windows"),
                         cl.ITEM_CATEGORY["windows_doors"])
        self.assertEqual(uc.category_for("hvac"),
                         cl.ITEM_CATEGORY["hvac_units"])
        self.assertEqual(uc.category_for("water_heater"),
                         cl.ITEM_CATEGORY["water_heaters"])
        self.assertEqual(uc.category_for("smoke_alarm_unit"),
                         cl.ITEM_CATEGORY["alarms_detectors"])
        self.assertEqual(uc.category_for("flooring"),
                         cl.ITEM_CATEGORY["flooring"])
        self.assertEqual(uc.category_for("walls_ceiling"),
                         cl.ITEM_CATEGORY["walls_ceilings"])

    def test_sensible_groupings_not_one_bucket(self):
        """A mapping that put everything under one heading would satisfy
        'has a category' while being useless."""
        used = {i["category"] for i in _all_items().values()}
        self.assertGreaterEqual(len(used), 4, f"only {used} used")
        self.assertEqual(uc.category_for("outlets_switches"), "mep")
        self.assertEqual(uc.category_for("lighting"), "mep")
        self.assertEqual(uc.category_for("gfci"), "mep")
        self.assertEqual(uc.category_for("egress_window"), "life_safety")
        self.assertEqual(uc.category_for("cabinets"), "interior_units")
        self.assertEqual(uc.category_for("appliance_range"), "interior_units")


class WriteSiteTests(unittest.TestCase):
    """_collect must put the category in the category column."""

    def test_collect_writes_the_category_not_the_kind(self):
        from tools import site_dd as routes
        items = uc.items_for_room("bathroom")
        rows = routes._collect({}, items, scope="room", area_id=1, room_id=2)
        by_key = {r["item_key"]: r for r in rows}
        self.assertEqual(by_key["toilet"]["category_key"], "mep")
        self.assertEqual(by_key["flooring"]["category_key"], "interior_units")
        for r in rows:
            with self.subTest(r["item_key"]):
                self.assertNotIn(r["category_key"], KINDS)

    def test_unit_scope_too(self):
        from tools import site_dd as routes
        rows = routes._collect({}, uc.items_for_unit(),
                               scope="unit", area_id=1, room_id=None)
        by_key = {r["item_key"]: r for r in rows}
        self.assertEqual(by_key["co_alarm"]["category_key"], "life_safety")
        self.assertEqual(by_key["hvac"]["category_key"], "mep")


class BackfillTests(unittest.TestCase):
    """Rows written before the fix gain a real category and lose nothing."""

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "sd.db"
        with db.get_connection(self.path) as conn:
            self.aid = db.create_assessment(conn, {"property_label": "T",
                                                   "checklist_version": 2})
            self.area = db.create_area(conn, self.aid, {"kind": "unit", "label": "1"})
            # Written the way the old code wrote them.
            db.upsert_findings(conn, self.aid, [
                {"scope": "unit", "area_id": self.area, "room_id": None,
                 "item_key": "water_heater", "instance_no": 1,
                 "category_key": "choice", "condition": "replace",
                 "detail": "missing", "note": "rusted", "quantity": None},
                {"scope": "unit", "area_id": self.area, "room_id": None,
                 "item_key": "hvac_age", "instance_no": 1,
                 "category_key": "number", "quantity": 14.0, "measure": "yr"},
                {"scope": "unit", "area_id": self.area, "room_id": None,
                 "item_key": "entry_door", "instance_no": 1,
                 "category_key": "condition", "condition": "good"},
            ])

    def _rows(self):
        with db.get_connection(self.path) as conn:
            return {r["item_key"]: dict(r) for r in conn.execute(
                "SELECT * FROM site_dd_findings ORDER BY id")}

    def test_legacy_rows_gain_a_real_category(self):
        rows = self._rows()
        self.assertEqual(rows["water_heater"]["category_key"], "mep")
        self.assertEqual(rows["hvac_age"]["category_key"], "mep")
        self.assertEqual(rows["entry_door"]["category_key"], "life_safety")

    def test_nothing_else_on_those_rows_moved(self):
        rows = self._rows()
        self.assertEqual(rows["water_heater"]["condition"], "replace")
        self.assertEqual(rows["water_heater"]["detail"], "missing")
        self.assertEqual(rows["water_heater"]["note"], "rusted")
        self.assertEqual(rows["hvac_age"]["quantity"], 14.0)
        self.assertEqual(rows["hvac_age"]["measure"], "yr")
        self.assertEqual(rows["entry_door"]["condition"], "good")

    def test_the_backfill_is_idempotent(self):
        before = self._rows()
        for _ in range(3):
            with db.get_connection(self.path) as conn:
                pass
        self.assertEqual(before, self._rows())

    def test_it_reports_nothing_to_do_once_done(self):
        with db.get_connection(self.path) as conn:
            self.assertEqual(db._backfill_capex_categories(conn), 0)

    def test_a_real_category_is_never_rewritten(self):
        """It must not be able to reach a property-scope or bank row."""
        with db.get_connection(self.path) as conn:
            db.upsert_findings(conn, self.aid, [
                {"scope": "property", "item_key": "roof_covering",
                 "instance_no": 1, "category_key": "structural_envelope",
                 "condition": "repair"}])
        with db.get_connection(self.path) as conn:
            db._backfill_capex_categories(conn)
        self.assertEqual(self._rows()["roof_covering"]["category_key"],
                         "structural_envelope")

    def test_a_null_category_is_left_alone(self):
        with db.get_connection(self.path) as conn:
            db.upsert_findings(conn, self.aid, [
                {"scope": "room", "area_id": self.area, "room_id": 99,
                 "item_key": "custom_koi_pond", "instance_no": 1,
                 "category_key": None, "condition": "replace"}])
            db._backfill_capex_categories(conn)
        self.assertIsNone(self._rows()["custom_koi_pond"]["category_key"])

    def test_an_unmapped_legacy_key_keeps_its_value_rather_than_being_guessed(self):
        with db.get_connection(self.path) as conn:
            db.upsert_findings(conn, self.aid, [
                {"scope": "room", "area_id": self.area, "room_id": 98,
                 "item_key": "some_retired_item", "instance_no": 1,
                 "category_key": "condition"}])
            db._backfill_capex_categories(conn)
        self.assertEqual(self._rows()["some_retired_item"]["category_key"],
                         "condition")
        # ...and the export still refuses to emit it as a heading.
        line = costs.to_capex_lines([self._rows()["some_retired_item"]])[0]
        self.assertIsNone(line["category"])


class ExportTests(unittest.TestCase):
    def test_room_findings_export_with_real_categories(self):
        findings = [
            {"id": 1, "scope": "room", "area_id": 5, "room_id": 9,
             "item_key": "toilet", "category_key": uc.category_for("toilet"),
             "instance_label": None, "est_unit_cost": 450.0},
            {"id": 2, "scope": "room", "area_id": 5, "room_id": 9,
             "item_key": "flooring", "category_key": uc.category_for("flooring"),
             "instance_label": None, "est_unit_cost": 2200.0},
            {"id": 3, "scope": "unit", "area_id": 5, "room_id": None,
             "item_key": "co_alarm", "category_key": uc.category_for("co_alarm"),
             "instance_label": None, "est_unit_cost": 60.0},
        ]
        lines = costs.to_capex_lines(
            findings, {"toilet": "Toilet", "flooring": "Flooring condition",
                       "co_alarm": "CO alarm"})
        cats = {l["label"]: l["category"] for l in lines}
        self.assertEqual(cats["Toilet"], "mep")
        self.assertEqual(cats["Flooring condition"], "interior_units")
        self.assertEqual(cats["CO alarm"], "life_safety")
        for l in lines:
            with self.subTest(l["label"]):
                self.assertIsNotNone(l["category"])
                self.assertNotIn(l["category"], KINDS)

    def test_the_budget_groups_by_something_meaningful(self):
        """The point of the whole exercise: lines group by the kind of
        work, not by the kind of form field."""
        findings = [
            {"id": i, "scope": "room", "area_id": 5, "room_id": 9,
             "item_key": k, "category_key": uc.category_for(k),
             "instance_label": None, "est_unit_cost": 100.0}
            for i, k in enumerate(("toilet", "sink_faucet", "cabinets",
                                   "countertops", "smoke_alarm"), start=1)
        ]
        lines = costs.to_capex_lines(findings)
        groups = {}
        for l in lines:
            groups.setdefault(l["category"], []).append(l["label"])
        self.assertEqual(sorted(groups["mep"]), ["sink_faucet", "toilet"])
        self.assertEqual(sorted(groups["interior_units"]),
                         ["cabinets", "countertops"])
        self.assertEqual(groups["life_safety"], ["smoke_alarm"])


if __name__ == "__main__":
    unittest.main()


class BankAgreesWithChecklistTests(unittest.TestCase):
    """The bank and the checklists must categorise the same kind of work
    the same way.

    They are separate catalogues that both feed one capex budget. When
    they disagree, a budget shows the same job under two headings --
    "In-unit washer / dryer" under MEP and "Washer" under Interior --
    and the grouping stops meaning anything.
    """

    EQUIVALENTS = {
        # bank key           checklist key it describes the same work as
        "washer_dryer": "washer",
        "disposal": "appliance_disposal",
        "tankless_water_heater": "water_heater",
        "skylight": "windows",
        "security_screen_door": "entry_door",
        "ceiling_fan": "lighting",
        "walk_in_closet": "closet",
    }

    def test_equivalent_items_share_a_category(self):
        for bank_key, checklist_key in self.EQUIVALENTS.items():
            with self.subTest(bank_key):
                self.assertEqual(
                    bank.get(bank_key)["category"],
                    uc.category_for(checklist_key),
                    f"{bank_key} and {checklist_key} describe the same work")

    def test_appliances_are_interior_not_mep(self):
        """The specific mismatch this fixes. A washer is an appliance;
        the pipe it drains into is MEP, and that is wd_hookups."""
        self.assertEqual(bank.get("washer_dryer")["category"], "interior_units")
        self.assertEqual(bank.get("disposal")["category"], "interior_units")

    def test_the_hookups_stay_mep(self):
        """Not over-corrected: hookups with no machine on them are
        plumbing and electrical infrastructure, not an appliance."""
        self.assertEqual(bank.get("wd_hookups")["category"], "mep")

    def test_no_other_bank_item_disagrees(self):
        """Guards against fixing two and leaving a third."""
        mismatches = []
        for bank_key, checklist_key in self.EQUIVALENTS.items():
            if bank.get(bank_key)["category"] != uc.category_for(checklist_key):
                mismatches.append(bank_key)
        self.assertEqual(mismatches, [])

    def test_every_bank_category_is_in_the_shared_vocabulary(self):
        for entry in bank.BANK_ITEMS:
            with self.subTest(entry["key"]):
                self.assertIn(entry["category"], cl.CATEGORY_NAMES)

    def test_the_mirror_is_reseeded_when_a_category_changes(self):
        """A stale row in site_dd_bank_items would report the old
        category to anything reading the table instead of the module."""
        path = Path(tempfile.mkdtemp()) / "sd.db"
        with db.get_connection(path) as conn:
            conn.execute(
                "UPDATE site_dd_bank_items SET category = 'mep', code_version = 1 "
                "WHERE key IN ('washer_dryer', 'disposal')")
            conn.commit()
        with db.get_connection(path) as conn:
            rows = {r["key"]: r["category"] for r in db.list_bank_items(conn)}
        self.assertEqual(rows["washer_dryer"], "interior_units")
        self.assertEqual(rows["disposal"], "interior_units")
