"""
Deletion-cascade tests for Site DD.

Site DD assessments live in their own database and additionally own files
on disk, so nothing about deleting a deal reaches them automatically:
deal_dive_db.delete_deal() only clears deal_comps and deal_files, and it
cannot see across database files at all. delete_assessments_for_deal() and
purge_for_deal() are the explicit bridge, and this is the test that they
actually work -- rows, child rows, and the uploaded bytes.

This is new behaviour rather than something inherited from Rent Comps
(which has no files), so it is tested end-to-end through the real
delete_deal route rather than by calling the helper directly.

Everything runs against temporary databases and a temporary upload folder;
no developer or production data is touched.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path


class TestSiteDDCascade(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["DEAL_DIVE_DB_PATH"] = str(self.tmp / "deal_dive.db")
        os.environ["SITE_DD_DB_PATH"] = str(self.tmp / "site_dd.db")
        os.environ["RENT_COMPS_DB_PATH"] = str(self.tmp / "rent_comps.db")
        os.environ["FEEDBACK_DB_PATH"] = str(self.tmp / "feedback.db")
        os.environ["UPLOAD_FOLDER_PATH"] = str(self.tmp / "uploads")
        os.environ.setdefault("SECRET_KEY", "test-key")

        # Imported after the env vars are set so every get_db_path() and
        # the app config resolve into the temp dir.
        import importlib
        import config as config_module
        importlib.reload(config_module)
        from tools import deal_dive_db, site_dd_db, site_dd_checklist
        importlib.reload(deal_dive_db)
        importlib.reload(site_dd_db)
        self.ddb = deal_dive_db
        self.sdb = site_dd_db
        self.cl = site_dd_checklist

        import app as app_module
        importlib.reload(app_module)
        self.app = app_module.create_app(config_module.Config)
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.app.config["LOGIN_DISABLED"] = True

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        for k in ("DEAL_DIVE_DB_PATH", "SITE_DD_DB_PATH", "RENT_COMPS_DB_PATH",
                  "FEEDBACK_DB_PATH", "UPLOAD_FOLDER_PATH"):
            os.environ.pop(k, None)

    def _make_deal_with_assessment(self):
        with self.ddb.get_connection() as conn:
            deal_id = self.ddb.create_deal(conn, {
                "address": "1 Cascade Way", "city": "Portland", "state": "OR"})
        with self.sdb.get_connection() as conn:
            aid = self.sdb.create_assessment(conn, {
                "deal_id": deal_id, "property_label": "1 Cascade Way",
                "checklist_version": self.cl.CHECKLIST_VERSION})
            self.sdb.upsert_findings(conn, aid, [
                {"category_key": self.cl.ITEM_CATEGORY[k], "item_key": k,
                 "condition": "good", "note": "ok", "scope": self.cl.SCOPE}
                for k in self.cl.ITEM_KEYS[:5]
            ])
            self.sdb.add_media(conn, aid, "foundation", "crack.png", "tok_crack.png", "hairline")

        photo_dir = Path(self.app.config["UPLOAD_FOLDER"]) / "site-dd" / str(aid)
        photo_dir.mkdir(parents=True, exist_ok=True)
        photo_file = photo_dir / "tok_crack.png"
        photo_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        return deal_id, aid, photo_file

    def test_deleting_deal_removes_assessment_findings_media_and_files(self):
        deal_id, aid, photo_file = self._make_deal_with_assessment()

        # everything exists first
        with self.sdb.get_connection() as conn:
            self.assertIsNotNone(self.sdb.get_assessment(conn, aid))
            self.assertEqual(len(self.sdb.get_findings(conn, aid)), 5)
            self.assertEqual(len(self.sdb.list_media(conn, aid)), 1)
        self.assertTrue(photo_file.exists())

        with self.app.test_client() as client:
            resp = client.post(f"/tools/deal-dive/deal/{deal_id}/delete")
            self.assertIn(resp.status_code, (302, 303))

        with self.sdb.get_connection() as conn:
            self.assertIsNone(self.sdb.get_assessment(conn, aid), "assessment row orphaned")
            self.assertEqual(self.sdb.get_findings(conn, aid), {}, "finding rows orphaned")
            self.assertEqual(self.sdb.list_media(conn, aid), [], "media rows orphaned")
        self.assertFalse(photo_file.exists(), "uploaded file left on disk")
        self.assertFalse(photo_file.parent.exists(), "upload directory left behind")

    def test_standalone_assessments_survive_a_deal_deletion(self):
        """deal_id NULL means it belongs to no deal and must never be
        collected by another deal's cleanup."""
        deal_id, aid, _ = self._make_deal_with_assessment()
        with self.sdb.get_connection() as conn:
            standalone = self.sdb.create_assessment(conn, {
                "deal_id": None, "property_label": "Unrelated walkthrough",
                "checklist_version": self.cl.CHECKLIST_VERSION})

        with self.app.test_client() as client:
            client.post(f"/tools/deal-dive/deal/{deal_id}/delete")

        with self.sdb.get_connection() as conn:
            self.assertIsNotNone(self.sdb.get_assessment(conn, standalone))

    def test_other_deals_assessments_survive(self):
        deal_a, aid_a, _ = self._make_deal_with_assessment()
        deal_b, aid_b, file_b = self._make_deal_with_assessment()

        with self.app.test_client() as client:
            client.post(f"/tools/deal-dive/deal/{deal_a}/delete")

        with self.sdb.get_connection() as conn:
            self.assertIsNone(self.sdb.get_assessment(conn, aid_a))
            self.assertIsNotNone(self.sdb.get_assessment(conn, aid_b), "wrong deal's data removed")
        self.assertTrue(file_b.exists(), "wrong deal's files removed")

    def test_delete_assessments_for_deal_returns_removed_ids(self):
        """The caller needs the ids back to clear the upload directories --
        if this returned nothing, the rows would go and the files would
        silently remain."""
        deal_id, aid, _ = self._make_deal_with_assessment()
        with self.sdb.get_connection() as conn:
            ids = self.sdb.delete_assessments_for_deal(conn, deal_id)
        self.assertEqual(ids, [aid])

    def test_deleting_assessment_directly_also_clears_children(self):
        _, aid, photo_file = self._make_deal_with_assessment()
        with self.app.test_client() as client:
            resp = client.post(f"/tools/site-dd/assessment/{aid}/delete")
            self.assertIn(resp.status_code, (302, 303))
        with self.sdb.get_connection() as conn:
            self.assertIsNone(self.sdb.get_assessment(conn, aid))
            self.assertEqual(self.sdb.get_findings(conn, aid), {})
            self.assertEqual(self.sdb.list_media(conn, aid), [])
        self.assertFalse(photo_file.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
