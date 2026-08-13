"""
Unit tests for the Underwriting overhaul: property info, capex budget,
rent-roll/T12 cross-check, and market context.

Same discipline as tests/test_underwriting_math.py: assertions restate the
expected arithmetic independently rather than calling the function under
test to compute its own expected value.

The Eagle Rock figures are the real ones -- 92 units, 85 occupied, and the
twelve-month T12 totals parsed off production.
"""

import ast
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools import underwriting_capex as ucx
from tools import underwriting_crosscheck as uxc
from tools import underwriting_db as db
from tools import underwriting_market as umkt
from tools import underwriting_math as um
from tools import underwriting_property as uprop

EAGLE_EGI = {
    "unit_count": 92,
    "occupied_units": 85,
    "gross_potential_rent": 1_343_580.00,
    "loss_to_lease": 105_696.00,
    "other_income": 73_120.22,
    "effective_gross_income": 1_223_671.52,
}
EAGLE_T12 = {
    "gross_potential_income": 1_315_737.00,
    "other_income": 76_013.89,
    "effective_gross_income": 1_032_914.08,
    "noi": 301_303.99,
}


# ── Property info ────────────────────────────────────────────────────────

class PropertyInfoTests(unittest.TestCase):
    def test_derived_when_no_override(self):
        p = uprop.resolve({}, EAGLE_EGI)
        self.assertEqual(p["unit_count"]["value"], 92)
        self.assertEqual(p["unit_count"]["source"], "derived")
        self.assertIsNone(p["unit_count"]["note"])
        # 85 of 92 is 92.3913...%
        self.assertAlmostEqual(p["occupancy"]["value"], 85 / 92 * 100, places=9)

    def test_override_that_agrees_is_silent(self):
        p = uprop.resolve({"unit_count_override": 92}, EAGLE_EGI)
        self.assertEqual(p["unit_count"]["value"], 92)
        self.assertEqual(p["unit_count"]["source"], "override_agrees")
        self.assertIsNone(p["unit_count"]["note"])
        self.assertEqual(p["disagreements"], [])

    def test_override_that_disagrees_is_used_AND_reported(self):
        """The override wins -- and the page is told it won. A silent
        overwrite is the failure this shape exists to prevent."""
        p = uprop.resolve({"unit_count_override": 88}, EAGLE_EGI)
        self.assertEqual(p["unit_count"]["value"], 88, "the override must win")
        self.assertEqual(p["unit_count"]["source"], "override_disagrees")
        self.assertIn("88", p["unit_count"]["note"])
        self.assertIn("92", p["unit_count"]["note"], "the derived figure must still be shown")
        self.assertEqual(p["unit_count"]["gap"], -4)
        self.assertEqual(len(p["disagreements"]), 1)

    def test_occupancy_override_tolerance(self):
        derived = 85 / 92 * 100
        near = uprop.resolve({"occupancy_pct_override": round(derived, 2)}, EAGLE_EGI)
        self.assertFalse(near["occupancy"]["disagrees"], "rounding is not a disagreement")
        far = uprop.resolve({"occupancy_pct_override": 85.0}, EAGLE_EGI)
        self.assertTrue(far["occupancy"]["disagrees"])

    def test_no_rent_roll_and_no_override_is_not_an_error(self):
        p = uprop.resolve({}, {"unit_count": 0, "occupied_units": 0})
        self.assertIsNone(p["unit_count"]["value"])
        self.assertEqual(p["unit_count"]["source"], "none")
        self.assertIn("not known", p["unit_count"]["note"].lower())

    def test_parking_is_plain_entry_with_a_per_unit_figure(self):
        p = uprop.resolve({"parking_spaces": 46, "parking_notes": "Surface lot"}, EAGLE_EGI)
        self.assertEqual(p["parking_spaces"], 46)
        self.assertAlmostEqual(p["parking_per_unit"], 0.5, places=9)
        self.assertEqual(p["parking_notes"], "Surface lot")

    def test_state_is_normalized_and_blank_stays_none(self):
        self.assertEqual(uprop.resolve({"state": "ca"}, EAGLE_EGI)["state"], "CA")
        self.assertIsNone(uprop.resolve({"state": "  "}, EAGLE_EGI)["state"])


# ── Capex ────────────────────────────────────────────────────────────────

class CapexTests(unittest.TestCase):
    LINES = [
        {"scope": "exterior", "label": "Roof", "total_cost": 184_000},
        {"scope": "exterior", "label": "Paint", "quantity": 1, "unit_cost": 62_000},
        {"scope": "interior", "label": "Unit turns", "quantity": 92, "unit_cost": 8_500},
    ]

    def test_scope_totals_and_line_math(self):
        s = ucx.summarize(self.LINES, unit_count=92)
        self.assertAlmostEqual(s["exterior_total"], 246_000.0, places=6)   # 184k + 62k
        self.assertAlmostEqual(s["interior_total"], 782_000.0, places=6)   # 92 * 8500
        self.assertAlmostEqual(s["itemized_total"], 1_028_000.0, places=6)

    def test_explicit_total_beats_quantity_times_unit_cost(self):
        line = {"total_cost": 100, "quantity": 5, "unit_cost": 999}
        self.assertAlmostEqual(ucx.line_total(line), 100.0, places=9)

    def test_contingency_defaults_to_five_percent_but_is_not_hardcoded(self):
        self.assertEqual(ucx.DEFAULT_CONTINGENCY_PCT, 5.0)
        default = ucx.summarize(self.LINES, 92, contingency_pct=None)
        self.assertAlmostEqual(default["contingency_total"], 1_028_000 * 0.05, places=6)
        self.assertAlmostEqual(default["total"], 1_028_000 * 1.05, places=6)
        # An explicit zero is honoured -- not treated as "unset".
        zero = ucx.summarize(self.LINES, 92, contingency_pct=0)
        self.assertAlmostEqual(zero["contingency_total"], 0.0, places=9)
        self.assertAlmostEqual(zero["total"], 1_028_000.0, places=6)
        ten = ucx.summarize(self.LINES, 92, contingency_pct=10)
        self.assertAlmostEqual(ten["contingency_total"], 102_800.0, places=6)

    def test_an_explicit_contingency_line_is_not_charged_contingency(self):
        lines = self.LINES + [{"scope": "interior", "label": "Held back",
                               "total_cost": 50_000, "is_contingency": True}]
        s = ucx.summarize(lines, 92, contingency_pct=5)
        self.assertAlmostEqual(s["itemized_total"], 1_028_000.0, places=6,
                               msg="an explicit holdback is not part of the base")
        self.assertAlmostEqual(s["contingency_total"], 1_028_000 * 0.05 + 50_000, places=6)

    def test_per_unit(self):
        s = ucx.summarize(self.LINES, unit_count=92, contingency_pct=0)
        self.assertAlmostEqual(s["per_unit"], 1_028_000 / 92, places=9)
        self.assertIsNone(ucx.summarize(self.LINES, unit_count=None)["per_unit"])
        self.assertTrue(ucx.summarize(self.LINES, None)["per_unit_reason"])

    def test_effective_percentage_of_price(self):
        self.assertAlmostEqual(ucx.effective_pct_of_price(1_000_000, 10_000_000),
                               10.0, places=9)
        # No price means nothing to express it against, not a crash.
        for price in (0, None, ""):
            self.assertEqual(ucx.effective_pct_of_price(500, price), 0.0)

    def test_empty_budget_is_exactly_zero(self):
        """The guarantee that a scenario without capex is untouched."""
        for empty in (None, []):
            s = ucx.summarize(empty, 92)
            self.assertEqual(s["total"], 0.0)
            self.assertFalse(s["has_lines"])
            self.assertEqual(ucx.effective_pct_of_price(s["total"], 6_990_000), 0.0)

    def test_site_dd_hook_is_carried_not_interpreted(self):
        lines = [{"scope": "interior", "label": "From inspection", "total_cost": 1_000,
                  "source": ucx.SOURCE_SITE_DD, "source_ref": "assessment:7/item:3"}]
        s = ucx.summarize(lines, 92, 0)
        self.assertEqual(s["source_counts"][ucx.SOURCE_SITE_DD], 1)
        self.assertAlmostEqual(s["total"], 1_000.0, places=6,
                               msg="a site_dd line counts exactly like a manual one")

    def test_capex_module_never_reads_expense_lines(self):
        """The structural guarantee that the forward budget and the T12's
        historical capex are never summed: this module cannot see the
        expense lines at all."""
        src = Path(__file__).resolve().parents[1] / "tools" / "underwriting_capex.py"
        text = src.read_text(encoding="utf-8")
        code = "\n".join(l for l in text.splitlines()
                         if not l.strip().startswith("#"))
        for forbidden in ("expense_lines", "is_acquisition_line", "line_kind"):
            self.assertNotIn(forbidden, code.split('"""')[-1],
                             f"capex must not reference {forbidden}")


class CapexReachesEquityTests(unittest.TestCase):
    """Capex rides the itemized-to-percentage channel into equity without
    the shared returns engine changing shape."""

    SCENARIO = {
        "purchase_price": 10_000_000, "closing_costs_pct": 2.0,
        "ltv_pct": 65.0, "interest_rate_pct": 6.0, "amort_years": 30,
        "hold_years": 5, "exit_cap_pct": 6.0, "selling_costs_pct": 2.0,
        "vacancy_pct": 5.0, "concessions_pct": 0.0, "bad_debt_pct": 0.0,
        "other_income_annual": 0.0, "rent_growth_pct": 3.0,
        "expense_growth_pct": 2.5,
    }
    UNITS = [{"unit": str(i), "market_rent": 1_500, "in_place_rent": 1_500,
              "status": "occupied"} for i in range(50)]
    EXPENSES = [{"label": "Opex", "annual_amount": 300_000, "is_included": 1,
                 "line_kind": None, "category_key": "payroll"}]

    def test_no_capex_is_identical_to_not_passing_capex_at_all(self):
        a = um.analyze_scenario(self.SCENARIO, self.UNITS, self.EXPENSES)
        b = um.analyze_scenario(self.SCENARIO, self.UNITS, self.EXPENSES, capex_lines=[])
        c = um.analyze_scenario(self.SCENARIO, self.UNITS, self.EXPENSES, capex_lines=None)
        for other in (b, c):
            self.assertEqual(a["returns"]["equity_invested"],
                             other["returns"]["equity_invested"])
            self.assertEqual(a["returns"]["levered_irr"], other["returns"]["levered_irr"])
            self.assertEqual(a["returns"]["equity_multiple"],
                             other["returns"]["equity_multiple"])

    def test_capex_increases_equity_by_exactly_its_total(self):
        base = um.analyze_scenario(self.SCENARIO, self.UNITS, self.EXPENSES)
        lines = [{"scope": "interior", "label": "Renovation", "total_cost": 500_000}]
        with_capex = um.analyze_scenario(self.SCENARIO, self.UNITS, self.EXPENSES,
                                         capex_lines=lines)
        # 500,000 + 5% contingency = 525,000
        expected = base["returns"]["equity_invested"] + 525_000
        self.assertAlmostEqual(with_capex["returns"]["equity_invested"], expected, places=4)
        self.assertAlmostEqual(with_capex["capex"]["total"], 525_000.0, places=6)

    def test_capex_lowers_the_levered_irr(self):
        base = um.analyze_scenario(self.SCENARIO, self.UNITS, self.EXPENSES)
        lines = [{"scope": "exterior", "label": "Roof", "total_cost": 750_000}]
        after = um.analyze_scenario(self.SCENARIO, self.UNITS, self.EXPENSES,
                                    capex_lines=lines)
        self.assertLess(after["returns"]["levered_irr"], base["returns"]["levered_irr"],
                        "more capital in, same cash out, must be a lower return")

    def test_noi_is_untouched_by_capex(self):
        """Capex is a capital outlay, not an operating expense. If it ever
        reached NOI it would also be capitalized into the exit value."""
        base = um.analyze_scenario(self.SCENARIO, self.UNITS, self.EXPENSES)
        after = um.analyze_scenario(self.SCENARIO, self.UNITS, self.EXPENSES,
                                    capex_lines=[{"scope": "interior", "label": "x",
                                                  "total_cost": 900_000}])
        self.assertEqual(base["projection"]["noi_series"], after["projection"]["noi_series"])
        self.assertEqual(base["returns"]["gross_sale_price"],
                         after["returns"]["gross_sale_price"])

    def test_shared_engine_source_is_unchanged(self):
        """deal_analyzer_math must not have been touched to make capex work."""
        src = Path(__file__).resolve().parents[1] / "tools" / "deal_analyzer_math.py"
        text = src.read_text(encoding="utf-8")
        self.assertNotIn("capex", text.lower())


# ── Cross-check ──────────────────────────────────────────────────────────

class CrossCheckTests(unittest.TestCase):
    def test_unavailable_without_both_documents(self):
        for roll, t12 in ((True, False), (False, True), (False, False)):
            with self.subTest(roll=roll, t12=t12):
                r = uxc.build(EAGLE_EGI, EAGLE_T12, has_rentroll=roll, has_t12=t12)
                self.assertFalse(r["available"])
                self.assertTrue(r["reason"])
                self.assertEqual(r["checks"], [])

    def test_the_real_eagle_rock_egi_gap(self):
        """The finding from the design investigation, asserted directly:
        the model runs $190,757 (18.5%) above what the property produced."""
        r = uxc.build(EAGLE_EGI, EAGLE_T12, has_rentroll=True, has_t12=True)
        egi = next(c for c in r["checks"] if c["key"] == "egi")
        self.assertTrue(egi["fires"])
        self.assertAlmostEqual(egi["gap"], 190_757.44, places=2)
        self.assertAlmostEqual(egi["gap_pct"], 18.47, places=1)
        self.assertIn("18.5% above", egi["message"])
        self.assertIn("$190,757", egi["message"])

    def test_gpr_agrees_within_tolerance(self):
        r = uxc.build(EAGLE_EGI, EAGLE_T12, has_rentroll=True, has_t12=True)
        gpr = next(c for c in r["checks"] if c["key"] == "gpr")
        self.assertFalse(gpr["fires"], "2.1% is inside the 5% band")
        self.assertAlmostEqual(gpr["gap_pct"], 2.12, places=1)

    def test_messages_are_not_judgemental(self):
        r = uxc.build(EAGLE_EGI, EAGLE_T12, has_rentroll=True, has_t12=True)
        for c in r["checks"]:
            with self.subTest(c["key"]):
                lowered = c["message"].lower()
                for banned in ("error", "wrong", "invalid", "must fix", "incorrect"):
                    self.assertNotIn(banned, lowered)

    def test_a_missing_figure_produces_no_finding(self):
        r = uxc.build(EAGLE_EGI, {**EAGLE_T12, "other_income": None},
                      has_rentroll=True, has_t12=True)
        self.assertNotIn("other_income", [c["key"] for c in r["checks"]])

    def test_unit_count_disagreement_is_reported(self):
        r = uxc.build(EAGLE_EGI, EAGLE_T12, has_rentroll=True, has_t12=True,
                      unit_count=88)
        uc = next(c for c in r["checks"] if c["key"] == "unit_count")
        self.assertTrue(uc["fires"])
        self.assertIn("88", uc["message"])
        self.assertIn("92", uc["message"])

    def test_matching_unit_count_produces_no_check(self):
        r = uxc.build(EAGLE_EGI, EAGLE_T12, has_rentroll=True, has_t12=True,
                      unit_count=92)
        self.assertNotIn("unit_count", [c["key"] for c in r["checks"]])

    def test_nothing_here_blocks(self):
        """Every finding is advisory. The result carries no flag any caller
        could read as 'refuse to render'."""
        r = uxc.build(EAGLE_EGI, EAGLE_T12, has_rentroll=True, has_t12=True)
        self.assertTrue(r["available"])
        for banned in ("blocked", "fatal", "abort", "invalid"):
            self.assertNotIn(banned, json.dumps(r, default=str).lower())


# ── Market context ───────────────────────────────────────────────────────

class MarketContextTests(unittest.TestCase):
    """The join must go through the alias table. A naive city-name match
    silently returns nothing for San Francisco, which is stored under the
    Census place name 'San Francisco city' -- that failure is what this
    fixture reproduces."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = Path(self.dir) / "fm.db"
        conn = sqlite3.connect(self.path)
        conn.executescript("""
            CREATE TABLE cities (
                city TEXT, state TEXT, display_name TEXT,
                normalized_city TEXT, normalized_display_name TEXT,
                search_key TEXT, population_current REAL,
                median_income_current REAL, crime_index_score REAL,
                crime_rating TEXT
            );
            CREATE TABLE search_aliases (search_key TEXT, city TEXT, state TEXT);
        """)
        conn.execute("INSERT INTO cities VALUES "
                     "('San Francisco city','CA','San Francisco, CA',"
                     "'san francisco city','san francisco ca','san francisco ca',"
                     "826079.0, 139801.0, 61.2, 'Elevated')")
        conn.execute("INSERT INTO search_aliases VALUES "
                     "('san francisco ca','San Francisco city','CA')")
        conn.commit()
        conn.close()

    def test_alias_join_finds_a_census_place_name(self):
        r = umkt.lookup("San Francisco", "CA", db_path=self.path)
        self.assertTrue(r["available"], "the alias join must resolve this")
        self.assertEqual(r["display_name"], "San Francisco, CA")

    def test_the_naive_match_this_replaces_would_have_failed(self):
        """Proof the fixture reproduces the real trap rather than a
        friendlier version of it."""
        conn = sqlite3.connect(self.path)
        naive = conn.execute(
            "SELECT * FROM cities WHERE lower(city) = lower(?)",
            ("San Francisco",)).fetchone()
        conn.close()
        self.assertIsNone(naive, "a plain city= match must miss, as it did in production")

    def test_uncovered_city_gets_an_honest_message_not_a_blank(self):
        r = umkt.lookup("Mill Valley", "CA", db_path=self.path)
        self.assertFalse(r["available"])
        self.assertIn("100,000", r["reason"])
        self.assertIn("Mill Valley", r["reason"])

    def test_missing_city_asks_for_one(self):
        for city, state in (("", "CA"), ("San Francisco", ""), (None, None)):
            with self.subTest(city=city, state=state):
                r = umkt.lookup(city, state, db_path=self.path)
                self.assertFalse(r["available"])
                self.assertIn("city and state", r["reason"])

    def test_a_missing_database_degrades_rather_than_raises(self):
        r = umkt.lookup("San Francisco", "CA", db_path=Path(self.dir) / "nope.db")
        self.assertFalse(r["available"])
        self.assertTrue(r["reason"])

    def test_metrics_carry_their_ratings(self):
        r = umkt.lookup("San Francisco", "CA", db_path=self.path)
        crime = next(m for m in r["metrics"] if m["key"] == "crime_index_score")
        self.assertEqual(crime["rating"], "Elevated")
        self.assertTrue(crime["available"])


# ── Persistence ──────────────────────────────────────────────────────────

class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = Path(self.dir) / "uw.db"

    def test_property_columns_are_not_in_the_assumptions_payload(self):
        """Saving the assumptions form must not blank the property
        overrides it never posts."""
        for col in ("unit_count_override", "occupancy_pct_override",
                    "parking_spaces", "parking_notes", "city", "state",
                    "capex_contingency_pct"):
            self.assertNotIn(col, db.SCENARIO_NUMERIC)
            self.assertIn(col, db.SCENARIO_PARTIAL_ONLY)

    def test_partial_update_leaves_other_columns_alone(self):
        with db.get_connection(self.path) as conn:
            sid = db.create_scenario(conn, {"property_label": "Test",
                                            "purchase_price": 5_000_000,
                                            "hold_years": 5})
            db.update_scenario_partial(conn, sid, {"city": "Austin", "state": "TX"},
                                       db.PROPERTY_FIELDS)
            row = db.get_scenario(conn, sid)
        self.assertEqual(row["city"], "Austin")
        self.assertEqual(row["purchase_price"], 5_000_000,
                         "a partial update must not touch the price")

    def test_partial_update_refuses_columns_not_allowed(self):
        with db.get_connection(self.path) as conn:
            sid = db.create_scenario(conn, {"property_label": "Test",
                                            "purchase_price": 5_000_000})
            db.update_scenario_partial(conn, sid, {"purchase_price": 1},
                                       db.PROPERTY_FIELDS)
            row = db.get_scenario(conn, sid)
        self.assertEqual(row["purchase_price"], 5_000_000)

    def test_capex_round_trip_and_delete_cascade(self):
        with db.get_connection(self.path) as conn:
            sid = db.create_scenario(conn, {"property_label": "Test"})
            db.replace_capex_lines(conn, sid, [
                {"scope": "exterior", "label": "Roof", "total_cost": 1000},
                {"scope": "bogus", "label": "", "total_cost": 5,
                 "source": "site_dd", "source_ref": "a:1"},
            ])
            rows = db.list_capex_lines(conn, sid)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["scope"], "exterior")
            self.assertEqual(rows[1]["scope"], "interior", "unknown scope falls back")
            self.assertEqual(rows[1]["label"], "Item 2", "a blank label gets a placeholder")
            self.assertEqual(rows[1]["source"], "site_dd")
            self.assertEqual(rows[1]["source_ref"], "a:1")

            db.delete_scenario(conn, sid)
            self.assertEqual(db.list_capex_lines(conn, sid), [])


class ManagementFeeUntouchedTests(unittest.TestCase):
    """The GI/GOI question is still open and this work must not have
    answered it by accident."""

    def test_no_new_module_mentions_the_management_fee_basis(self):
        root = Path(__file__).resolve().parents[1] / "tools"
        for name in ("underwriting_capex.py", "underwriting_property.py",
                     "underwriting_crosscheck.py", "underwriting_market.py",
                     "underwriting_scenario_export.py"):
            text = (root / name).read_text(encoding="utf-8")
            with self.subTest(name):
                self.assertNotIn("management_fee_pct", text)

    def test_the_fee_calculation_still_reads_from_the_same_place(self):
        text = (Path(__file__).resolve().parents[1] / "tools"
                / "underwriting_math.py").read_text(encoding="utf-8")
        tree = ast.parse(text)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "project_noi_series")
        args = [a.arg for a in fn.args.kwonlyargs]
        self.assertIn("management_fee_pct", args,
                      "the fee still arrives as its own keyword argument")


if __name__ == "__main__":
    unittest.main()
