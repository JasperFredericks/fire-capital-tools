"""
Integration tests for summary_for_deal() and the promote-split default.

These touch the database rather than pure functions, because the two
claims under test are about stored state:

  * summary_for_deal() now returns computed figures (LP IRR, GP promote,
    per-partner split), not just the stored scenario row
  * changing the default promote split to 70/30 does not reach back and
    alter a waterfall that was already saved at 80/20

Each test gets its own temporary databases, so nothing here can see or
disturb a developer's local data.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TempDatabases(unittest.TestCase):
    """Point every *_DB_PATH at a fresh temp directory for the test."""

    ENV_KEYS = ("UNDERWRITING_DB_PATH", "INVESTOR_REPORT_DB_PATH",
                "DEAL_DIVE_DB_PATH", "UPLOAD_FOLDER_PATH")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self.ENV_KEYS}
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        os.environ.update({
            "UNDERWRITING_DB_PATH": str(base / "uw.db"),
            "INVESTOR_REPORT_DB_PATH": str(base / "ir.db"),
            "DEAL_DIVE_DB_PATH": str(base / "dd.db"),
            "UPLOAD_FOLDER_PATH": str(base / "uploads"),
        })
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()


def seed(deal_and_scenario=True):
    """A deal, a computable underwriting scenario, and one LP contribution."""
    from tools import deal_dive_db as ddb
    from tools import investor_report_db as irdb
    from tools import underwriting_db as udb

    with ddb.get_connection() as conn:
        ddb.init_schema(conn)
        deal_id = ddb.create_deal(conn, {
            "address": "1 Test Way", "city": "Indianapolis", "state": "IN",
            "property_type": "Multifamily", "unit_count": 10,
            "status": "active_review"})

    with udb.get_connection() as conn:
        udb.init_schema(conn)
        uw_id = udb.create_scenario(conn, {
            "deal_id": deal_id, "name": "Base case", "property_label": "Test",
            "purchase_price": 1_000_000.0, "closing_costs_pct": 2.0, "ltv_pct": 65.0,
            "interest_rate_pct": 6.0, "amort_years": 30, "hold_years": 5,
            "exit_cap_pct": 6.0, "selling_costs_pct": 2.0, "vacancy_pct": 5.0,
            "concessions_pct": 1.0, "bad_debt_pct": 0.5, "other_income_annual": 0.0,
            "rent_growth_pct": 3.0, "expense_growth_pct": 2.5})
        udb.replace_unit_lines(conn, uw_id, [
            {"unit": str(i), "unit_type": "1/1", "sqft": 700, "status": "C",
             "in_place_rent": 1200.0, "market_rent": 1250.0} for i in range(10)])
        udb.replace_expense_lines(conn, uw_id, [
            {"category_key": "payroll", "category_name": "Payroll", "label": "Payroll",
             "annual_amount": 30_000.0, "growth_pct": None, "is_included": True,
             "line_kind": "operating"}])

    with irdb.get_connection() as conn:
        irdb.init_schema(conn)
        investor_id = irdb.create_investor(conn, "Test LP", "LLC")
        irdb.add_contribution(conn, investor_id, deal_id, 370_000.0)

    return deal_id, uw_id


class TestSummaryForDeal(TempDatabases):
    """Requirement 6: it must return the figures, not just the row."""

    def setUp(self):
        super().setUp()
        self.deal_id, self.uw_id = seed()
        from tools import investor_report_db as irdb
        with irdb.get_connection() as conn:
            self.scenario_id = irdb.create_scenario(conn, {
                "deal_id": self.deal_id, "underwriting_scenario_id": self.uw_id,
                "name": "Base waterfall", "property_label": "Test",
                "pref_rate_pct": 8.0, "pref_convention": "accrual"})

    def summary(self):
        from tools import investor_report
        return investor_report.summary_for_deal(self.deal_id)

    def test_returns_none_when_no_waterfall_exists(self):
        from tools import investor_report
        self.assertIsNone(investor_report.summary_for_deal(999_999))

    def test_computes_rather_than_only_returning_the_row(self):
        s = self.summary()
        self.assertTrue(s["computed"], s.get("reason"))
        for key in ("lp_contributed", "lp_distributed", "lp_irr",
                    "gp_promote", "gp_split", "partners"):
            self.assertIn(key, s)

    def test_lp_contributed_matches_the_contribution(self):
        self.assertAlmostEqual(self.summary()["lp_contributed"], 370_000.0, places=2)

    def test_lp_irr_is_a_number(self):
        s = self.summary()
        self.assertIsInstance(s["lp_irr"], float)

    def test_default_split_is_one_bucket(self):
        s = self.summary()
        self.assertTrue(s["gp_split"]["is_default"])
        self.assertEqual(len(s["partners"]), 1)

    def test_partner_split_is_reported_and_reconciles(self):
        from tools import investor_report_db as irdb
        with irdb.get_connection() as conn:
            irdb.replace_gp_partners(conn, self.scenario_id, [
                {"name": "A", "share_pct": 60.0, "sort_order": 0},
                {"name": "B", "share_pct": 25.0, "sort_order": 1},
                {"name": "C", "share_pct": 15.0, "sort_order": 2}])
        s = self.summary()
        self.assertEqual(len(s["partners"]), 3)
        self.assertAlmostEqual(sum(p["distributed"] for p in s["partners"]),
                               s["gp_promote"], places=2)

    def test_splitting_does_not_change_the_promote_itself(self):
        from tools import investor_report_db as irdb
        before = self.summary()["gp_promote"]
        with irdb.get_connection() as conn:
            irdb.replace_gp_partners(conn, self.scenario_id, [
                {"name": "A", "share_pct": 70.0, "sort_order": 0},
                {"name": "B", "share_pct": 30.0, "sort_order": 1}])
        self.assertEqual(self.summary()["gp_promote"], before)

    def test_never_raises_when_the_source_scenario_is_gone(self):
        from tools import underwriting_db as udb
        with udb.get_connection() as conn:
            udb.delete_scenario(conn, self.uw_id)
        s = self.summary()
        self.assertFalse(s["computed"])
        self.assertIn("no longer exists", s["reason"])

    def test_never_raises_when_there_are_no_contributions(self):
        from tools import investor_report_db as irdb
        with irdb.get_connection() as conn:
            conn.execute("DELETE FROM capital_contributions")
            conn.commit()
        s = self.summary()
        self.assertFalse(s["computed"])
        self.assertIsNotNone(s["reason"])


class TestPromoteDefaultReachesTheForm(unittest.TestCase):
    """The default a user actually experiences is the one prefilled in the
    new-waterfall form. It was hardcoded to 20 in the template and so
    survived the change to 70/30 -- caught in production verification,
    not by any test, which is why this one exists."""

    def test_template_does_not_hardcode_a_promote_default(self):
        tpl = (Path(__file__).resolve().parent.parent
               / "templates" / "tools" / "investor_report.html").read_text(encoding="utf-8")
        marker = 'name="promote_gp_pct"'
        i = tpl.index(marker)
        field = tpl[i:i + 200]
        self.assertIn("default_promote_gp_pct", field,
                      "the form must read the default from the module")
        self.assertNotIn('value="20"', field)
        self.assertNotIn('value="30"', field,
                         "even the right number hardcoded will go stale again")

    def test_route_supplies_the_default_to_the_template(self):
        src = (Path(__file__).resolve().parent.parent
               / "tools" / "investor_report.py").read_text(encoding="utf-8")
        self.assertIn("default_promote_gp_pct=db.DEFAULT_PROMOTE_GP_PCT", src)


class TestPromoteDefaultDoesNotRewriteHistory(TempDatabases):
    """Part C. The default moves to 70/30; stored scenarios do not move."""

    def setUp(self):
        super().setUp()
        self.deal_id, self.uw_id = seed()

    def test_new_scenario_defaults_to_seventy_thirty(self):
        from tools import investor_report_db as irdb
        from tools import waterfall_math as wm
        with irdb.get_connection() as conn:
            sid = irdb.create_scenario(conn, {
                "deal_id": self.deal_id, "underwriting_scenario_id": self.uw_id,
                "name": "New", "property_label": "Test",
                "pref_rate_pct": 8.0, "pref_convention": "accrual"})
            row = irdb.get_scenario(conn, sid)
            promote = [t for t in irdb.list_tiers(conn, sid)
                       if t["tier_type"] == wm.TIER_PROMOTE][0]
        self.assertEqual(row["promote_gp_pct"], 30.0)
        self.assertEqual(row["promote_lp_pct"], 70.0)
        self.assertEqual(promote["gp_share_pct"], 30.0)

    def test_a_scenario_saved_at_eighty_twenty_stays_there(self):
        from tools import investor_report_db as irdb
        from tools import waterfall_math as wm
        with irdb.get_connection() as conn:
            sid = irdb.create_scenario(conn, {
                "deal_id": self.deal_id, "underwriting_scenario_id": self.uw_id,
                "name": "Legacy", "property_label": "Test",
                "pref_rate_pct": 8.0, "pref_convention": "accrual",
                "promote_lp_pct": 80.0, "promote_gp_pct": 20.0})

        # Re-open exactly as the app would on a later request.
        with irdb.get_connection() as conn:
            row = irdb.get_scenario(conn, sid)
            promote = [t for t in irdb.list_tiers(conn, sid)
                       if t["tier_type"] == wm.TIER_PROMOTE][0]

        self.assertEqual(row["promote_gp_pct"], 20.0,
                         "the default change rewrote a stored scenario")
        self.assertEqual(row["promote_lp_pct"], 80.0)
        self.assertEqual(promote["gp_share_pct"], 20.0,
                         "the default change rewrote a stored tier row")

    def test_an_explicit_split_survives_an_unrelated_edit(self):
        """Editing the name must not quietly reset the promote."""
        from tools import investor_report_db as irdb
        with irdb.get_connection() as conn:
            sid = irdb.create_scenario(conn, {
                "deal_id": self.deal_id, "underwriting_scenario_id": self.uw_id,
                "name": "Legacy", "property_label": "Test",
                "pref_rate_pct": 8.0, "pref_convention": "accrual",
                "promote_lp_pct": 80.0, "promote_gp_pct": 20.0})
            irdb.update_scenario(conn, sid, {
                "name": "Legacy renamed", "pref_rate_pct": 8.0,
                "pref_convention": "accrual",
                "promote_lp_pct": 80.0, "promote_gp_pct": 20.0})
            row = irdb.get_scenario(conn, sid)
        self.assertEqual(row["promote_gp_pct"], 20.0)
        self.assertEqual(row["name"], "Legacy renamed")


if __name__ == "__main__":
    unittest.main()
