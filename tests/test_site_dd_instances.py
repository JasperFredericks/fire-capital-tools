"""
Unit tests for repeatable item instances.

The requirement is "a bathroom can have two sinks". The work is removing
an item_key-as-identity assumption that ran through a UNIQUE constraint,
the upsert, both read helpers, both roll-up functions and three
templates -- so most of these tests are about the parts that were NOT in
the requirement.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools import site_dd_checklist as cl
from tools import site_dd_conditions as cond
from tools import site_dd_db as db
from tools import site_dd_unit_checklist as uc


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "sd.db"

    def test_the_unique_key_now_includes_the_instance(self):
        with db.get_connection(self.path) as conn:
            uniques = []
            for idx in conn.execute("PRAGMA index_list('site_dd_findings')"):
                if idx[2]:
                    uniques.append([r[2] for r in
                                    conn.execute(f"PRAGMA index_info('{idx[1]}')")])
        self.assertTrue(any("instance_no" in u for u in uniques),
                        f"no instance-aware unique key: {uniques}")

    def test_two_instances_of_one_item_can_coexist(self):
        with db.get_connection(self.path) as conn:
            aid = db.create_assessment(conn, {"property_label": "T",
                                              "checklist_version": 2})
            area = db.create_area(conn, aid, {"kind": "unit", "label": "204"})
            room = db.create_room(conn, area, "bathroom")
            db.upsert_findings(conn, aid, [
                {"scope": "room", "area_id": area, "room_id": room,
                 "item_key": "vanity_sink", "instance_no": 1, "condition": "good"},
                {"scope": "room", "area_id": area, "room_id": room,
                 "item_key": "vanity_sink", "instance_no": 2, "condition": "replace"},
            ])
            found = db.get_findings(conn, aid, area, room)
        self.assertEqual(len(found["vanity_sink"]), 2)
        self.assertEqual([r["condition"] for r in found["vanity_sink"]],
                         ["good", "replace"])

    def test_resaving_updates_the_right_instance(self):
        with db.get_connection(self.path) as conn:
            aid = db.create_assessment(conn, {"property_label": "T",
                                              "checklist_version": 2})
            db.upsert_findings(conn, aid, [
                {"scope": "property", "item_key": "roof_covering",
                 "instance_no": 1, "condition": "good"},
                {"scope": "property", "item_key": "roof_covering",
                 "instance_no": 2, "condition": "repair"},
            ])
            db.upsert_findings(conn, aid, [
                {"scope": "property", "item_key": "roof_covering",
                 "instance_no": 2, "condition": "replace"},
            ])
            found = db.get_findings(conn, aid, None, None)
        self.assertEqual([r["condition"] for r in found["roof_covering"]],
                         ["good", "replace"], "instance 1 must be untouched")

    def test_add_instance_numbers_sequentially(self):
        """The first tap backfills instance 1 and adds instance 2, because
        the checklist already shows a first instance whether or not a row
        exists for it. Three taps therefore leave four."""
        with db.get_connection(self.path) as conn:
            aid = db.create_assessment(conn, {"property_label": "T",
                                              "checklist_version": 2})
            for _ in range(3):
                db.add_instance(conn, aid, "alarms_detectors", None, None,
                                scope="property")
            found = db.get_findings(conn, aid, None, None)
        self.assertEqual([r["instance_no"] for r in found["alarms_detectors"]],
                         [1, 2, 3, 4])

    def test_the_first_add_backfills_instance_one(self):
        """Otherwise the inspector taps 'Add another' on an untouched item
        and watches nothing appear -- the row created would be the one
        already on screen."""
        with db.get_connection(self.path) as conn:
            aid = db.create_assessment(conn, {"property_label": "T",
                                              "checklist_version": 2})
            db.add_instance(conn, aid, "roof_covering", None, None, scope="property")
            found = db.get_findings(conn, aid, None, None)
        self.assertEqual(len(found["roof_covering"]), 2,
                         "one tap must produce a visible second instance")

    def test_backfill_does_not_overwrite_an_existing_first_instance(self):
        with db.get_connection(self.path) as conn:
            aid = db.create_assessment(conn, {"property_label": "T",
                                              "checklist_version": 2})
            db.upsert_findings(conn, aid, [
                {"scope": "property", "item_key": "roof_covering",
                 "instance_no": 1, "condition": "replace", "note": "keep"}])
            db.add_instance(conn, aid, "roof_covering", None, None, scope="property")
            found = db.get_findings(conn, aid, None, None)
        self.assertEqual(len(found["roof_covering"]), 2)
        self.assertEqual(found["roof_covering"][0]["condition"], "replace")
        self.assertEqual(found["roof_covering"][0]["note"], "keep")

    def test_instances_are_scoped_per_room(self):
        """`vanity_sink` in bathroom 1 and bathroom 2 are different items,
        not instances of each other."""
        with db.get_connection(self.path) as conn:
            aid = db.create_assessment(conn, {"property_label": "T",
                                              "checklist_version": 2})
            area = db.create_area(conn, aid, {"kind": "unit", "label": "204"})
            r1 = db.create_room(conn, area, "bathroom")
            r2 = db.create_room(conn, area, "bathroom")
            db.add_instance(conn, aid, "vanity_sink", area, r1, scope="room")
            db.add_instance(conn, aid, "vanity_sink", area, r2, scope="room")
            self.assertEqual(db.get_findings(conn, aid, area, r1)["vanity_sink"][0]
                             ["instance_no"], 1)
            self.assertEqual(db.get_findings(conn, aid, area, r2)["vanity_sink"][0]
                             ["instance_no"], 1)

    def test_deleting_an_instance_detaches_media_rather_than_destroying_it(self):
        """A photo is evidence somebody took. Losing a row should not lose
        the picture."""
        with db.get_connection(self.path) as conn:
            aid = db.create_assessment(conn, {"property_label": "T",
                                              "checklist_version": 2})
            fid = db.add_instance(conn, aid, "roof_covering", None, None,
                                  scope="property")
            mid = db.add_media(conn, aid, "roof_covering", "a.jpg", "x_a.jpg", None,
                               finding_id=fid)
            db.delete_instance(conn, fid)
            media = db.list_media(conn, aid)
            self.assertEqual(len(media), 1, "the photo must survive")
            self.assertIsNone(media[0]["finding_id"], "but be detached")
            self.assertIsNone(db.get_finding(conn, fid))


class MigrationTests(unittest.TestCase):
    """An existing database has the old four-column unique key inline in
    CREATE TABLE, which SQLite cannot alter -- so the table is rebuilt."""

    def _legacy_db(self):
        path = Path(tempfile.mkdtemp()) / "old.db"
        conn = sqlite3.connect(path)
        conn.executescript("""
        CREATE TABLE site_dd_assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, deal_id INTEGER,
            property_label TEXT NOT NULL, assessed_on TEXT, inspector TEXT,
            checklist_version INTEGER NOT NULL, overall_notes TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE site_dd_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, assessment_id INTEGER NOT NULL,
            area_id INTEGER, room_id INTEGER, scope TEXT NOT NULL DEFAULT 'property',
            category_key TEXT, item_key TEXT NOT NULL, condition TEXT, detail TEXT,
            note TEXT, quantity REAL, measure TEXT, created_at TEXT NOT NULL,
            UNIQUE (assessment_id, area_id, room_id, item_key));
        """)
        conn.execute("INSERT INTO site_dd_assessments (property_label,"
                     " checklist_version, created_at, updated_at)"
                     " VALUES ('Legacy', 2, 'x', 'x')")
        conn.execute("INSERT INTO site_dd_findings (assessment_id, scope, item_key,"
                     " condition, note, created_at)"
                     " VALUES (1, 'property', 'roof_covering', 'replace', 'keep me', 'x')")
        conn.commit()
        conn.close()
        return path

    def test_existing_rows_survive_the_rebuild(self):
        path = self._legacy_db()
        with db.get_connection(path) as conn:
            rows = conn.execute("SELECT * FROM site_dd_findings").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["condition"], "replace")
        self.assertEqual(rows[0]["note"], "keep me")
        self.assertEqual(rows[0]["instance_no"], 1,
                         "existing rows become instance 1")

    def test_the_rebuild_leaves_no_debris(self):
        path = self._legacy_db()
        with db.get_connection(path) as conn:
            leftover = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE '%_old'")]
        self.assertEqual(leftover, [])

    def test_the_rebuild_is_idempotent(self):
        path = self._legacy_db()
        for _ in range(3):
            with db.get_connection(path) as conn:
                n = conn.execute("SELECT COUNT(*) FROM site_dd_findings").fetchone()[0]
        self.assertEqual(n, 1, "reopening must not duplicate or re-run")

    def test_a_second_instance_works_after_migrating(self):
        path = self._legacy_db()
        with db.get_connection(path) as conn:
            db.add_instance(conn, 1, "roof_covering", None, None, scope="property")
            found = db.get_findings(conn, 1, None, None)
        self.assertEqual([r["instance_no"] for r in found["roof_covering"]], [1, 2])


class RollupTests(unittest.TestCase):
    K = cl.ITEM_KEYS

    def test_instances_count_independently(self):
        s = cond.summarize({self.K[0]: ["replace", "replace"]}, cl.CATEGORIES)
        self.assertEqual(s["replace_count"], 2, "two objects, two work orders")
        self.assertEqual(s["work_count"], 2)

    def test_the_denominator_grows_with_instances(self):
        one = cond.summarize({self.K[0]: ["good"]}, cl.CATEGORIES)
        two = cond.summarize({self.K[0]: ["good", "replace"]}, cl.CATEGORIES)
        self.assertEqual(one["total_items"], 32)
        self.assertEqual(two["total_items"], 33, "a second sink is a 33rd thing")
        self.assertEqual(two["assessed_count"], 2)

    def test_completion_can_never_exceed_100_percent(self):
        every = {k: ["good", "good", "good"] for k in cl.ITEM_KEYS}
        s = cond.summarize(every, cl.CATEGORIES)
        self.assertEqual(s["assessed_count"], s["total_items"])
        self.assertAlmostEqual(s["completion_pct"], 100.0, places=6)

    def test_an_unanswered_extra_instance_still_counts_in_the_denominator(self):
        s = cond.summarize({self.K[0]: ["good", None]}, cl.CATEGORIES)
        self.assertEqual(s["assessed_count"], 1)
        self.assertEqual(s["total_items"], 33)
        self.assertEqual(s["not_assessed_count"], 32)

    def test_category_counts_still_reconcile_with_the_total(self):
        given = {self.K[0]: ["repair", "replace"], self.K[6]: ["good"]}
        s = cond.summarize(given, cl.CATEGORIES)
        for state in cond.CONDITIONS:
            self.assertEqual(sum(c["counts"][state] for c in s["categories"]),
                             s["counts"][state])
        self.assertEqual(sum(c["item_count"] for c in s["categories"]),
                         s["total_items"])

    def test_a_bare_scalar_is_still_accepted(self):
        """Callers with a single value should not have to wrap it, and a
        scalar must never be read as zero instances."""
        self.assertEqual(cond.as_instances("good"), ["good"])
        self.assertEqual(cond.as_instances(None), [])
        self.assertEqual(cond.as_instances(["a", "b"]), ["a", "b"])
        s = cond.summarize({self.K[0]: "replace"}, cl.CATEGORIES)
        self.assertEqual(s["work_count"], 1)

    def test_work_items_lists_each_instance(self):
        s = cond.summarize({self.K[0]: ["replace", "repair"]}, cl.CATEGORIES)
        self.assertEqual(len(s["work_items"]), 2)


class UnitRollupTests(unittest.TestCase):
    ROOMS = [{"id": 1, "room_type": "bathroom", "label": None, "sort_order": 0}]

    def test_two_sinks_count_twice_and_raise_the_denominator(self):
        one = uc.summarize_unit({1: {"vanity_sink": ["good"]}}, self.ROOMS, {})
        two = uc.summarize_unit({1: {"vanity_sink": ["good", "replace"]}},
                                self.ROOMS, {})
        self.assertEqual(two["total_items"], one["total_items"] + 1)
        self.assertEqual(two["work_count"], 1)
        self.assertEqual(two["assessed_count"], 2)

    def test_unit_wide_items_repeat_too(self):
        """Michelle's own example: a unit with two smoke alarms."""
        one = uc.summarize_unit({}, [], {"smoke_alarm_unit": ["working"]})
        two = uc.summarize_unit({}, [], {"water_heater": ["repair", "replace"]})
        self.assertEqual(two["work_count"], 2)
        self.assertEqual(two["total_items"], one["total_items"] + 1)

    def test_room_item_count_reflects_instances(self):
        s = uc.summarize_unit({1: {"vanity_sink": ["good", "good", "good"]}},
                              self.ROOMS, {})
        room = s["rooms"][0]
        self.assertEqual(room["assessed_count"], 3)
        self.assertGreaterEqual(room["item_count"], 3)


if __name__ == "__main__":
    unittest.main()


class PropertyScopeIdentityTests(unittest.TestCase):
    """The bug this work exposed, and the reason identity moved to an
    expression index.

    SQLite treats NULLs as DISTINCT in a unique constraint. Property-scope
    findings have area_id and room_id both NULL, so the old
    UNIQUE(assessment_id, area_id, room_id, item_key) never fired for
    them: every save of the property checklist INSERTED another 32 rows.
    Measured on master before the fix: 32, then 64, then 96.

    It went unseen because the previous {item_key: row} read collapsed the
    duplicates on the way out -- the summary kept saying "1 of 32" over a
    table that was growing without bound.
    """

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "sd.db"
        with db.get_connection(self.path) as conn:
            self.aid = db.create_assessment(conn, {"property_label": "T",
                                                   "checklist_version": 2})

    def _save_property(self, condition):
        with db.get_connection(self.path) as conn:
            db.upsert_findings(conn, self.aid, [
                {"scope": "property", "area_id": None, "room_id": None,
                 "item_key": k, "instance_no": 1,
                 "condition": condition if k == cl.ITEM_KEYS[0] else None}
                for k in cl.ITEM_KEYS])

    def _count(self):
        with db.get_connection(self.path) as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM site_dd_findings WHERE assessment_id = ?",
                (self.aid,)).fetchone()[0]

    def test_saving_the_property_checklist_repeatedly_does_not_duplicate(self):
        for i, value in enumerate(("good", "repair", "replace"), start=1):
            self._save_property(value)
            self.assertEqual(self._count(), 32,
                             f"save #{i} should update, not insert a 33rd..64th row")

    def test_the_last_save_wins(self):
        self._save_property("good")
        self._save_property("replace")
        with db.get_connection(self.path) as conn:
            found = db.get_findings(conn, self.aid, None, None)
        self.assertEqual(found[cl.ITEM_KEYS[0]][0]["condition"], "replace")
        self.assertEqual(len(found[cl.ITEM_KEYS[0]]), 1)

    def test_instances_still_work_at_property_scope(self):
        """Fixing NULL-equality must not also block a legitimate second
        instance, which differs by instance_no."""
        self._save_property("good")
        with db.get_connection(self.path) as conn:
            db.add_instance(conn, self.aid, cl.ITEM_KEYS[0], None, None,
                            scope="property")
            found = db.get_findings(conn, self.aid, None, None)
        self.assertEqual([r["instance_no"] for r in found[cl.ITEM_KEYS[0]]], [1, 2])
        self.assertEqual(self._count(), 33)

    def test_the_identity_index_exists_and_is_null_safe(self):
        with db.get_connection(self.path) as conn:
            names = [i[1] for i in conn.execute("PRAGMA index_list('site_dd_findings')")
                     if i[2]]
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND name='ux_sitedd_finding_identity'").fetchone()
        self.assertIn("ux_sitedd_finding_identity", names)
        self.assertIn("COALESCE", sql[0].upper(),
                      "identity must compare NULL scopes as equal")

    def test_a_legacy_database_with_duplicates_is_collapsed_on_migration(self):
        """A database that already suffered the bug should come out clean,
        keeping the most recent save -- what the upsert would have done."""
        path = Path(tempfile.mkdtemp()) / "dupes.db"
        conn = sqlite3.connect(path)
        conn.executescript("""
        CREATE TABLE site_dd_assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, deal_id INTEGER,
            property_label TEXT NOT NULL, assessed_on TEXT, inspector TEXT,
            checklist_version INTEGER NOT NULL, overall_notes TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE site_dd_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, assessment_id INTEGER NOT NULL,
            area_id INTEGER, room_id INTEGER, scope TEXT NOT NULL DEFAULT 'property',
            category_key TEXT, item_key TEXT NOT NULL, condition TEXT, detail TEXT,
            note TEXT, quantity REAL, measure TEXT, created_at TEXT NOT NULL,
            UNIQUE (assessment_id, area_id, room_id, item_key));
        """)
        conn.execute("INSERT INTO site_dd_assessments (property_label,"
                     " checklist_version, created_at, updated_at)"
                     " VALUES ('Dupes', 2, 'x', 'x')")
        for value in ("good", "repair", "replace"):
            conn.execute("INSERT INTO site_dd_findings (assessment_id, scope,"
                         " item_key, condition, created_at)"
                         " VALUES (1, 'property', 'roof_covering', ?, 'x')", (value,))
        conn.commit()
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM site_dd_findings").fetchone()[0], 3)
        conn.close()

        with db.get_connection(path) as conn:
            rows = conn.execute("SELECT condition FROM site_dd_findings").fetchall()
        self.assertEqual(len(rows), 1, "duplicates collapsed")
        self.assertEqual(rows[0]["condition"], "replace", "newest save kept")
