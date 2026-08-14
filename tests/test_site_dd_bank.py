"""
Unit tests for the Site DD item bank.

The requirement is "let an inspector add a fireplace". The work is making
an added item indistinguishable from a checklist item everywhere it
matters -- the form, the save, the instance machinery, photos, the
roll-up denominator -- so most of these tests are about the parts that
were not in the requirement.

The one place they MUST differ is bank_item_key: set for a curated pick,
NULL for anything typed. That column is the whole contract with Branch 4,
which can only price a line it has a reference for.
"""

import tempfile
import unittest
from pathlib import Path

from tools import site_dd_bank as bank
from tools import site_dd_checklist as cl
from tools import site_dd_db as db
from tools import site_dd_unit_checklist as uc


class CatalogueTests(unittest.TestCase):
    def test_the_starter_bank_is_the_twenty_that_were_agreed(self):
        self.assertEqual(len(bank.BANK_ITEMS), 20)

    def test_keys_are_unique(self):
        self.assertEqual(len(set(bank.BANK_KEYS)), len(bank.BANK_KEYS))

    def test_every_entry_is_completely_specified(self):
        for entry in bank.BANK_ITEMS:
            with self.subTest(entry["key"]):
                self.assertIn(entry["scope"],
                              (bank.SCOPE_UNIT, bank.SCOPE_ROOM, bank.SCOPE_BOTH))
                self.assertIn(entry["default_kind"],
                              (uc.KIND_CONDITION, uc.KIND_CHOICE))
                self.assertTrue(entry["label"].strip())

    def test_every_category_is_a_real_capex_category(self):
        """A bank entry exists to be priced. One pointing at a category
        that does not exist would be silently unpriceable in Branch 4."""
        for entry in bank.BANK_ITEMS:
            with self.subTest(entry["key"]):
                self.assertIn(entry["category"], cl.CATEGORY_NAMES)

    def test_a_choice_entry_actually_offers_choices(self):
        for entry in bank.BANK_ITEMS:
            if entry["default_kind"] == uc.KIND_CHOICE:
                with self.subTest(entry["key"]):
                    self.assertTrue(entry["options"])

    def test_room_types_are_real_room_types(self):
        valid = set(uc.ROOM_TYPE_LABELS)
        for entry in bank.BANK_ITEMS:
            for rt in entry["room_types"] or ():
                with self.subTest(entry["key"]):
                    self.assertIn(rt, valid)

    def test_no_bank_entry_duplicates_a_question_the_checklist_asks(self):
        """A bank item with the same label as a checklist item in the SAME
        place would be two questions about one object. The picker filters
        by label, so this is about the filter having something to match:
        where a duplicate label exists, the room types must overlap."""
        for entry in bank.BANK_ITEMS:
            if entry["scope"] == bank.SCOPE_UNIT:
                continue
            for room_type in (entry["room_types"] or tuple(uc.ROOM_TYPE_LABELS)):
                labels = {i["label"] for i in uc.items_for_room(room_type)}
                with self.subTest(entry["key"], room=room_type):
                    self.assertNotIn(entry["label"], labels)


class ScopeTests(unittest.TestCase):
    def test_a_room_picker_never_offers_unit_items(self):
        offered = {e["key"] for e in bank.for_scope(bank.SCOPE_ROOM, "living")}
        self.assertNotIn("sump_pump", offered)
        self.assertNotIn("half_bath", offered)

    def test_a_unit_picker_never_offers_room_items(self):
        offered = {e["key"] for e in bank.for_scope(bank.SCOPE_UNIT)}
        self.assertNotIn("fireplace", offered)
        self.assertNotIn("ceiling_fan", offered)

    def test_both_scoped_items_appear_in_both(self):
        self.assertIn("balcony_patio",
                      {e["key"] for e in bank.for_scope(bank.SCOPE_UNIT)})
        self.assertIn("balcony_patio",
                      {e["key"] for e in bank.for_scope(bank.SCOPE_ROOM, "living")})

    def test_room_types_narrow_the_offer(self):
        living = {e["key"] for e in bank.for_scope(bank.SCOPE_ROOM, "living")}
        bedroom = {e["key"] for e in bank.for_scope(bank.SCOPE_ROOM, "bedroom")}
        self.assertIn("fireplace", living)
        self.assertNotIn("walk_in_closet", living)
        self.assertIn("walk_in_closet", bedroom)

    def test_an_item_with_no_room_types_is_offered_everywhere(self):
        for room_type in uc.ROOM_TYPE_LABELS:
            with self.subTest(room_type):
                self.assertIn("skylight",
                              {e["key"] for e in bank.for_scope(bank.SCOPE_ROOM,
                                                                room_type)})

    def test_exclusions_are_honoured(self):
        offered = {e["key"] for e in
                   bank.for_scope(bank.SCOPE_ROOM, "living", {"Fireplace"})}
        self.assertNotIn("fireplace", offered)

    def test_grouping_only_emits_categories_that_have_members(self):
        for group in bank.grouped_for_scope(bank.SCOPE_UNIT):
            with self.subTest(group["key"]):
                self.assertTrue(group["items"])

    def test_search_matches_on_label(self):
        hits = {e["key"] for e in bank.search("fire", bank.SCOPE_ROOM, "living")}
        self.assertEqual(hits, {"fireplace"})

    def test_an_empty_search_is_the_whole_list(self):
        self.assertEqual(bank.search("", bank.SCOPE_UNIT),
                         bank.for_scope(bank.SCOPE_UNIT))


class CustomKeyTests(unittest.TestCase):
    def test_a_typed_name_becomes_a_prefixed_slug(self):
        self.assertEqual(bank.custom_key("Koi pond"), "custom_koi_pond")

    def test_the_same_words_always_give_the_same_key(self):
        self.assertEqual(bank.custom_key("Koi Pond"), bank.custom_key("  koi   pond "))

    def test_punctuation_only_input_still_yields_a_usable_key(self):
        self.assertEqual(bank.custom_key("!!! ???"), "custom_item")
        self.assertEqual(bank.custom_key(""), "custom_item")

    def test_a_custom_key_is_recognisable_without_a_join(self):
        self.assertTrue(bank.is_custom_key(bank.custom_key("Koi pond")))
        self.assertFalse(bank.is_custom_key("fireplace"))
        self.assertFalse(bank.is_custom_key("flooring"))

    def test_a_long_name_is_bounded(self):
        key = bank.custom_key("x" * 500)
        self.assertLess(len(key), 70)

    def test_labels_are_collapsed_and_bounded(self):
        self.assertEqual(bank.clean_label("  Koi   pond  "), "Koi pond")
        self.assertLessEqual(len(bank.clean_label("y" * 500)), bank.MAX_CUSTOM_LABEL)


class AsItemTests(unittest.TestCase):
    def test_a_bank_pick_is_shaped_like_a_checklist_item(self):
        item = bank.as_item("fireplace")
        for field in ("key", "label", "kind", "options", "with_condition"):
            self.assertIn(field, item)
        self.assertEqual(item["bank_item_key"], "fireplace")
        self.assertTrue(item["added"])

    def test_a_freeform_item_carries_no_bank_link(self):
        item = bank.as_item("custom_koi_pond", "Koi pond")
        self.assertIsNone(item["bank_item_key"])
        self.assertEqual(item["label"], "Koi pond")
        self.assertEqual(item["kind"], uc.KIND_CONDITION)

    def test_a_freeform_item_with_no_label_still_renders(self):
        self.assertTrue(bank.as_item("custom_item", None)["label"])

    def test_a_choice_entry_keeps_its_options(self):
        self.assertEqual(bank.as_item("washer_dryer")["options"], uc.PRESENCE)


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "sd.db"

    def test_findings_gained_a_nullable_bank_link(self):
        with db.get_connection(self.path) as conn:
            cols = {r[1]: r for r in conn.execute("PRAGMA table_info(site_dd_findings)")}
        self.assertIn("bank_item_key", cols)
        self.assertEqual(cols["bank_item_key"][3], 0, "must be nullable")

    def test_the_table_mirrors_the_module(self):
        with db.get_connection(self.path) as conn:
            rows = db.list_bank_items(conn)
        self.assertEqual([r["key"] for r in rows], list(bank.BANK_KEYS))
        self.assertEqual([r["label"] for r in rows],
                         [e["label"] for e in bank.BANK_ITEMS])

    def test_reseeding_is_idempotent(self):
        for _ in range(4):
            with db.get_connection(self.path) as conn:
                n = conn.execute("SELECT COUNT(*) FROM site_dd_bank_items").fetchone()[0]
        self.assertEqual(n, len(bank.BANK_ITEMS))

    def test_a_withdrawn_entry_is_not_deleted_from_the_table(self):
        """Findings recorded while an entry existed still reference it. A
        label is worth keeping; a stale row is inert."""
        with db.get_connection(self.path) as conn:
            conn.execute(
                "INSERT INTO site_dd_bank_items (key, label, scope, default_kind,"
                " code_version) VALUES ('gone', 'Withdrawn', 'unit', 'condition', 0)")
            conn.commit()
        with db.get_connection(self.path) as conn:
            keys = {r["key"] for r in db.list_bank_items(conn)}
        self.assertIn("gone", keys)

    def test_a_bumped_version_reseeds(self):
        with db.get_connection(self.path) as conn:
            conn.execute("UPDATE site_dd_bank_items SET label = 'wrong', "
                         "code_version = -1")
            conn.commit()
        with db.get_connection(self.path) as conn:
            rows = db.list_bank_items(conn)
        self.assertNotIn("wrong", {r["label"] for r in rows})


class FindingTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "sd.db"
        with db.get_connection(self.path) as conn:
            self.aid = db.create_assessment(conn, {"property_label": "T",
                                                   "checklist_version": 2})
            self.area = db.create_area(conn, self.aid, {"kind": "unit", "label": "1"})
            self.room = db.create_room(conn, self.area, "living")

    def _add(self, key, bank_key=None, label=None):
        with db.get_connection(self.path) as conn:
            return db.add_item(conn, self.aid, key, self.area, self.room,
                               scope="room", bank_item_key=bank_key,
                               instance_label=label)

    def test_a_curated_pick_records_the_bank_link(self):
        self._add("fireplace", "fireplace")
        with db.get_connection(self.path) as conn:
            row = db.get_findings(conn, self.aid, self.area, self.room)["fireplace"][0]
        self.assertEqual(row["bank_item_key"], "fireplace")

    def test_a_freeform_item_records_no_bank_link(self):
        self._add("custom_koi_pond", None, "Koi pond")
        with db.get_connection(self.path) as conn:
            row = db.get_findings(conn, self.aid, self.area,
                                  self.room)["custom_koi_pond"][0]
        self.assertIsNone(row["bank_item_key"])
        self.assertEqual(row["instance_label"], "Koi pond")

    def test_adding_the_same_item_twice_makes_a_second_instance(self):
        self._add("fireplace", "fireplace")
        self._add("fireplace", "fireplace")
        with db.get_connection(self.path) as conn:
            rows = db.get_findings(conn, self.aid, self.area, self.room)["fireplace"]
        self.assertEqual([r["instance_no"] for r in rows], [1, 2])

    def test_a_save_cannot_erase_the_bank_link(self):
        """The room form does not carry bank_item_key for every field. A
        plain assignment in the upsert would null the link on first save
        and silently make the item unpriceable."""
        self._add("fireplace", "fireplace")
        with db.get_connection(self.path) as conn:
            db.upsert_findings(conn, self.aid, [
                {"scope": "room", "area_id": self.area, "room_id": self.room,
                 "item_key": "fireplace", "instance_no": 1, "condition": "repair"},
            ])
            row = db.get_findings(conn, self.aid, self.area, self.room)["fireplace"][0]
        self.assertEqual(row["bank_item_key"], "fireplace")
        self.assertEqual(row["condition"], "repair")

    def test_repeated_saves_do_not_duplicate_an_added_item(self):
        self._add("fireplace", "fireplace")
        for _ in range(3):
            with db.get_connection(self.path) as conn:
                db.upsert_findings(conn, self.aid, [
                    {"scope": "room", "area_id": self.area, "room_id": self.room,
                     "item_key": "fireplace", "instance_no": 1,
                     "bank_item_key": "fireplace", "condition": "good"}])
        with db.get_connection(self.path) as conn:
            rows = db.get_findings(conn, self.aid, self.area, self.room)["fireplace"]
        self.assertEqual(len(rows), 1)

    def test_added_keys_exclude_everything_the_checklist_covers(self):
        self._add("fireplace", "fireplace")
        known = {i["key"] for i in uc.items_for_room("living")}
        with db.get_connection(self.path) as conn:
            db.upsert_findings(conn, self.aid, [
                {"scope": "room", "area_id": self.area, "room_id": self.room,
                 "item_key": "flooring", "instance_no": 1, "condition": "good"}])
            added = db.added_item_keys(conn, self.aid, self.area, self.room, known)
        self.assertEqual([a["item_key"] for a in added], ["fireplace"])

    def test_removing_an_item_takes_every_instance(self):
        self._add("fireplace", "fireplace")
        self._add("fireplace", "fireplace")
        with db.get_connection(self.path) as conn:
            removed = db.delete_item(conn, self.aid, "fireplace", self.area, self.room)
            rows = db.get_findings(conn, self.aid, self.area, self.room)
        self.assertEqual(removed, 2)
        self.assertNotIn("fireplace", rows)

    def test_removing_an_item_detaches_media_rather_than_destroying_it(self):
        fid = self._add("fireplace", "fireplace")
        with db.get_connection(self.path) as conn:
            db.add_media(conn, self.aid, "fireplace", "f.jpg", "s.jpg", None,
                         finding_id=fid, area_id=self.area, room_id=self.room)
            db.delete_item(conn, self.aid, "fireplace", self.area, self.room)
            media = db.list_media(conn, self.aid)
        self.assertEqual(len(media), 1)
        self.assertIsNone(media[0]["finding_id"])

    def test_removing_something_that_is_not_there_is_not_an_error(self):
        with db.get_connection(self.path) as conn:
            self.assertEqual(
                db.delete_item(conn, self.aid, "nothing", self.area, self.room), 0)


class RollupTests(unittest.TestCase):
    """An added item counts. Adding a fireplace and never assessing it must
    make the unit look LESS complete, because it is."""

    def setUp(self):
        self.rooms = [{"id": 1, "room_type": "living"}]

    def test_an_added_item_raises_the_denominator(self):
        base = uc.summarize_unit({1: {}}, self.rooms, {})
        with_extra = uc.summarize_unit(
            {1: {}}, self.rooms, {}, added_by_room={1: [bank.as_item("fireplace")]})
        self.assertEqual(with_extra["total_items"], base["total_items"] + 1)

    def test_an_assessed_added_item_counts_in_the_state_totals(self):
        s = uc.summarize_unit({1: {"fireplace": ["replace"]}}, self.rooms, {},
                              added_by_room={1: [bank.as_item("fireplace")]})
        self.assertEqual(s["replace_count"], 1)
        self.assertEqual(s["assessed_count"], 1)

    def test_an_unassessed_added_item_is_not_counted_as_assessed(self):
        s = uc.summarize_unit({1: {"fireplace": [None]}}, self.rooms, {},
                              added_by_room={1: [bank.as_item("fireplace")]})
        self.assertEqual(s["assessed_count"], 0)

    def test_unit_scope_added_items_count_too(self):
        base = uc.summarize_unit({1: {}}, self.rooms, {})
        s = uc.summarize_unit({1: {}}, self.rooms, {"sump_pump": ["repair"]},
                              added_unit=[bank.as_item("sump_pump")])
        self.assertEqual(s["total_items"], base["total_items"] + 1)
        self.assertEqual(s["repair_count"], 1)

    def test_completion_never_exceeds_one_hundred_percent(self):
        s = uc.summarize_unit({1: {"fireplace": ["good", "good"]}}, self.rooms, {},
                              added_by_room={1: [bank.as_item("fireplace")]})
        self.assertLessEqual(s["completion_pct"], 100.0)

    def test_an_unknown_key_is_still_ignored(self):
        """The tolerance that survives a stale key from an older checklist
        must not be lost: added items are counted because the CALLER says
        they exist, not because an unrecognised key turned up."""
        s = uc.summarize_unit({1: {"not_a_thing": ["replace"]}}, self.rooms, {})
        self.assertEqual(s["replace_count"], 0)

    def test_a_freeform_item_counts_exactly_like_a_curated_one(self):
        curated = uc.summarize_unit({1: {"fireplace": ["repair"]}}, self.rooms, {},
                                    added_by_room={1: [bank.as_item("fireplace")]})
        free = uc.summarize_unit({1: {"custom_koi_pond": ["repair"]}}, self.rooms, {},
                                 added_by_room={1: [bank.as_item("custom_koi_pond",
                                                                 "Koi pond")]})
        self.assertEqual(curated["total_items"], free["total_items"])
        self.assertEqual(curated["repair_count"], free["repair_count"])


if __name__ == "__main__":
    unittest.main()


class LabelSurvivalTests(unittest.TestCase):
    """A save that does not mention a label must not erase it.

    No template rendered label_* for a checklist item, so treating an
    absent field as an empty one nulled every instance label on every
    save. Latent while nothing set labels; fatal once a freeform item's
    typed name became the only thing identifying it.
    """

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "sd.db"
        with db.get_connection(self.path) as conn:
            self.aid = db.create_assessment(conn, {"property_label": "T",
                                                   "checklist_version": 2})
            self.area = db.create_area(conn, self.aid, {"kind": "unit", "label": "1"})
            self.room = db.create_room(conn, self.area, "living")

    def _label(self, key="custom_koi_pond"):
        with db.get_connection(self.path) as conn:
            return db.get_findings(conn, self.aid, self.area,
                                   self.room)[key][0]["instance_label"]

    def _add(self):
        with db.get_connection(self.path) as conn:
            db.add_item(conn, self.aid, "custom_koi_pond", self.area, self.room,
                        scope="room", instance_label="Koi pond")

    def _save(self, form):
        from tools import site_dd as routes
        items = [bank.as_item("custom_koi_pond", "Koi pond")]
        with db.get_connection(self.path) as conn:
            existing = db.get_findings(conn, self.aid, self.area, self.room)
            db.upsert_findings(conn, self.aid, routes._collect(
                form, items, scope="room", area_id=self.area,
                room_id=self.room, existing=existing))

    def test_a_save_with_no_label_field_keeps_the_name(self):
        self._add()
        self._save({"condition_custom_koi_pond": "replace"})
        self.assertEqual(self._label(), "Koi pond")

    def test_a_posted_label_replaces_it(self):
        self._add()
        self._save({"condition_custom_koi_pond": "replace",
                    "label_custom_koi_pond": "Ornamental pond"})
        self.assertEqual(self._label(), "Ornamental pond")

    def test_a_posted_empty_label_clears_it(self):
        """Absent means unchanged; present-and-empty means cleared. The
        two must stay distinguishable or one of them is unreachable."""
        self._add()
        self._save({"condition_custom_koi_pond": "replace",
                    "label_custom_koi_pond": "  "})
        self.assertIsNone(self._label())

    def test_repeated_saves_never_erode_the_name(self):
        self._add()
        for _ in range(4):
            self._save({"condition_custom_koi_pond": "replace"})
        self.assertEqual(self._label(), "Koi pond")

    def test_a_cleared_name_still_reads_as_something(self):
        self.assertEqual(bank.as_item("custom_koi_pond", None)["label"], "Koi pond")
        self.assertEqual(bank.label_from_key("custom_koi_pond"), "Koi pond")
        self.assertEqual(bank.label_from_key("custom_item"), "Item")

    def test_instance_labels_on_checklist_items_survive_too(self):
        """The same bug, on the feature that introduced it: a second sink
        named "by the window" lost its name on the next room save."""
        with db.get_connection(self.path) as conn:
            bath = db.create_room(conn, self.area, "bathroom")
            db.upsert_findings(conn, self.aid, [
                {"scope": "room", "area_id": self.area, "room_id": bath,
                 "item_key": "vanity_sink", "instance_no": 1,
                 "instance_label": "by the window", "condition": "good"}])
        from tools import site_dd as routes
        with db.get_connection(self.path) as conn:
            existing = db.get_findings(conn, self.aid, self.area, bath)
            db.upsert_findings(conn, self.aid, routes._collect(
                {"condition_vanity_sink": "repair"}, uc.items_for_room("bathroom"),
                scope="room", area_id=self.area, room_id=bath, existing=existing))
            row = db.get_findings(conn, self.aid, self.area, bath)["vanity_sink"][0]
        self.assertEqual(row["instance_label"], "by the window")
        self.assertEqual(row["condition"], "repair")
