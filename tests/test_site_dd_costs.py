"""
Unit tests for Site DD cost provenance and the capex hand-off.

Two things are being protected here.

The first is that a number and where it came from stay attached. An
inspector's guess and a priced line item are indistinguishable once they
are both a figure in a column, so the tests assert that no path produces
one without the other, and that the wording cannot be softened into
meaninglessness by a later edit.

The second is the forward hook designed in Phase 1. Site DD and
Underwriting's capex table were built months apart against a written
agreement about field names; these tests are the first thing that checks
the agreement actually holds against the real table rather than against
what both sides remembered.
"""

import tempfile
import unittest
from pathlib import Path

from tools import site_dd_bank as bank
from tools import site_dd_checklist as cl
from tools import site_dd_costs as costs
from tools import site_dd_db as db
from tools import site_dd_unit_checklist as uc
from tools import underwriting_capex as ucx
from tools import underwriting_db as udb


class SourceTests(unittest.TestCase):
    def test_the_three_sources_are_the_ones_that_were_agreed(self):
        self.assertEqual(costs.SOURCES, ("reference", "manual", "none"))

    def test_null_reads_as_none(self):
        """Rows written before the column existed hold NULL, and they
        genuinely have no estimate. The two must mean the same thing or
        every caller has to remember which is which."""
        self.assertEqual(costs.normalize_source(None), costs.SOURCE_NONE)
        self.assertEqual(costs.normalize_source(""), costs.SOURCE_NONE)
        self.assertEqual(costs.normalize_source("nonsense"), costs.SOURCE_NONE)

    def test_a_typed_number_is_always_the_inspectors(self):
        self.assertEqual(costs.source_for("450"), costs.SOURCE_MANUAL)

    def test_clearing_a_cost_returns_the_source_to_none(self):
        """A source pointing at a figure that is gone is a claim about
        nothing."""
        self.assertEqual(costs.source_for(""), costs.SOURCE_NONE)
        self.assertEqual(costs.source_for(None), costs.SOURCE_NONE)

    def test_typing_over_a_reference_figure_makes_it_manual(self):
        self.assertEqual(costs.source_for("500", previous="reference"),
                         costs.SOURCE_MANUAL)


class CleanCostTests(unittest.TestCase):
    def test_a_plain_number(self):
        self.assertEqual(costs.clean_cost("450"), 450.0)

    def test_dollars_and_commas_are_accepted(self):
        self.assertEqual(costs.clean_cost("$1,250"), 1250.0)
        self.assertEqual(costs.clean_cost(" 1,250.50 "), 1250.50)

    def test_a_negative_cost_is_a_typo_not_a_discount(self):
        self.assertIsNone(costs.clean_cost("-99"))

    def test_zero_is_not_an_estimate(self):
        self.assertIsNone(costs.clean_cost("0"))

    def test_nonsense_is_rejected_rather_than_raising(self):
        self.assertIsNone(costs.clean_cost("banana"))
        self.assertIsNone(costs.clean_cost(None))
        self.assertIsNone(costs.clean_cost(""))

    def test_an_absurd_figure_is_refused_at_the_edge(self):
        """A mistyped unit cost swamps a capex budget silently. Caught
        where it is entered, not explained afterwards."""
        self.assertIsNone(costs.clean_cost(costs.MAX_UNIT_COST + 1))
        self.assertEqual(costs.clean_cost(costs.MAX_UNIT_COST),
                         costs.MAX_UNIT_COST)


class ProvenanceLabelTests(unittest.TestCase):
    def test_the_manual_label_says_it_is_not_from_a_cost_table(self):
        """The whole point. A later edit that softens this to 'Estimate'
        lets a guess acquire the authority of a priced line."""
        self.assertIn(costs.REQUIRED_PROVENANCE_PHRASE,
                      costs.SOURCE_LABELS[costs.SOURCE_MANUAL])

    def test_a_cost_can_never_be_described_without_its_label(self):
        d = costs.describe({"est_unit_cost": 450, "est_cost_source": "manual"})
        self.assertTrue(d["has_cost"])
        self.assertTrue(d["label"])
        self.assertTrue(d["is_estimate"])

    def test_a_source_with_no_figure_reports_no_figure(self):
        d = costs.describe({"est_cost_source": "manual"})
        self.assertFalse(d["has_cost"])
        self.assertEqual(d["source"], costs.SOURCE_NONE)

    def test_a_figure_with_no_source_is_not_presented_as_an_estimate(self):
        d = costs.describe({"est_unit_cost": 450})
        self.assertFalse(d["is_estimate"])

    def test_describe_survives_a_missing_row(self):
        self.assertFalse(costs.describe(None)["has_cost"])

    def test_the_none_label_is_empty_rather_than_reassuring(self):
        self.assertEqual(costs.SOURCE_LABELS[costs.SOURCE_NONE], "")


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "sd.db"

    def test_both_columns_exist_and_are_nullable(self):
        with db.get_connection(self.path) as conn:
            cols = {r[1]: r for r in conn.execute("PRAGMA table_info(site_dd_findings)")}
        for name in ("est_unit_cost", "est_cost_source"):
            self.assertIn(name, cols)
            self.assertEqual(cols[name][3], 0, f"{name} must be nullable")

    def test_a_cost_round_trips(self):
        with db.get_connection(self.path) as conn:
            aid = db.create_assessment(conn, {"property_label": "T",
                                              "checklist_version": 2})
            db.upsert_findings(conn, aid, [
                {"scope": "property", "item_key": "roof", "instance_no": 1,
                 "condition": "replace", "est_unit_cost": 84000.0,
                 "est_cost_source": "manual"}])
            row = db.get_findings(conn, aid)["roof"][0]
        self.assertEqual(row["est_unit_cost"], 84000.0)
        self.assertEqual(row["est_cost_source"], "manual")

    def test_an_unrecognised_source_is_stored_as_none(self):
        """The column is free text at the SQL level; the normalization
        happens on the way in so a hand-crafted write cannot invent a
        fourth provenance."""
        with db.get_connection(self.path) as conn:
            aid = db.create_assessment(conn, {"property_label": "T",
                                              "checklist_version": 2})
            db.upsert_findings(conn, aid, [
                {"scope": "property", "item_key": "roof", "instance_no": 1,
                 "est_unit_cost": 1000.0, "est_cost_source": "rsmeans_2027"}])
            row = db.get_findings(conn, aid)["roof"][0]
        self.assertEqual(row["est_cost_source"], "none")

    def test_nothing_in_the_codebase_writes_a_reference_cost(self):
        """The reference table is still gated on the numbers decision.
        The column shipping ahead of it is the point; the column being
        quietly populated with placeholders is not."""
        # Comments describing the column are expected and are not writes.
        comment_starts = ("#", "--", "*", '"', "'")
        assignments = []
        for path in sorted(Path("tools").glob("*.py")):
            if path.name == "site_dd_costs.py":
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                body = line.strip()
                if body.startswith(comment_starts):
                    continue
                if "reference" in body and ("est_cost_source" in body
                                            or "SOURCE_REFERENCE" in body):
                    assignments.append(f"{path}:{n}: {body}")
        self.assertEqual(assignments, [])

    def test_no_runtime_path_can_produce_a_reference_source(self):
        for value in ("450", 450, "$1,250", "", None, "reference", 0, -1):
            with self.subTest(value):
                self.assertNotEqual(costs.source_for(value),
                                    costs.SOURCE_REFERENCE)


class CapexMappingTests(unittest.TestCase):
    """The Phase 1 forward hook, against the real table."""

    def _finding(self, **kw):
        base = {"id": 1, "scope": "room", "area_id": 5, "room_id": 9,
                "item_key": "toilet", "instance_no": 1, "instance_label": None,
                "category_key": "condition", "est_unit_cost": 450.0,
                "est_cost_source": "manual"}
        base.update(kw)
        return base

    def test_the_five_agreed_fields_map(self):
        line = costs.to_capex_lines([self._finding()], {"toilet": "Toilet"})[0]
        self.assertEqual(line["label"], "Toilet")
        self.assertEqual(line["quantity"], 1.0)
        self.assertEqual(line["unit_cost"], 450.0)
        self.assertEqual(line["source"], "site_dd")
        self.assertEqual(line["source_ref"], "1")

    def test_quantity_is_the_instance_count(self):
        lines = costs.to_capex_lines(
            [self._finding(id=1, item_key="vanity_sink", instance_no=1),
             self._finding(id=2, item_key="vanity_sink", instance_no=2)],
            {"vanity_sink": "Vanity & sink"})
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["quantity"], 2.0)

    def test_instances_in_different_rooms_are_different_lines(self):
        lines = costs.to_capex_lines(
            [self._finding(id=1, room_id=9), self._finding(id=2, room_id=10)],
            {"toilet": "Toilet"})
        self.assertEqual(len(lines), 2)

    def test_a_typed_name_wins_over_the_catalogue(self):
        line = costs.to_capex_lines(
            [self._finding(item_key="custom_koi_pond", instance_label="Koi pond")])[0]
        self.assertEqual(line["label"], "Koi pond")

    def test_a_bank_pick_falls_back_to_the_catalogue_not_to_item_1(self):
        """Phase 1 said 'label <- instance label', which is right only for
        a freeform item. A curated pick leaves instance_label NULL, so
        taken literally every fireplace would arrive as 'Item 1'."""
        line = costs.to_capex_lines(
            [self._finding(item_key="fireplace", instance_label=None,
                           category_key="interior_units")],
            {"fireplace": "Fireplace"})[0]
        self.assertEqual(line["label"], "Fireplace")

    def test_a_bank_items_category_is_its_capex_category(self):
        line = costs.to_capex_lines(
            [self._finding(item_key="fireplace", category_key="interior_units")])[0]
        self.assertEqual(line["category"], "interior_units")

    def test_a_legacy_kind_value_is_still_refused(self):
        """Room and unit rows used to carry the input KIND here. They are
        rewritten on connect now, but a database restored from an older
        backup can still present one, and emitting 'condition' as a budget
        heading would look like a real grouping while meaning nothing."""
        line = costs.to_capex_lines([self._finding(category_key="condition")])[0]
        self.assertIsNone(line["category"])

    def test_a_room_checklist_item_now_carries_a_real_category(self):
        """The fix: a toilet is plumbing work, and the export says so."""
        line = costs.to_capex_lines(
            [self._finding(item_key="toilet",
                           category_key=uc.category_for("toilet"))])[0]
        self.assertEqual(line["category"], "mep")
        self.assertIn(line["category"], cl.CATEGORY_NAMES)

    def test_every_emitted_category_is_a_real_capex_category(self):
        for key in ("condition", "choice", "number", "interior_units", "mep", None):
            line = costs.to_capex_lines([self._finding(category_key=key)])[0]
            with self.subTest(key):
                self.assertTrue(line["category"] is None
                                or line["category"] in cl.CATEGORY_NAMES)

    def test_scope_maps_into_underwritings_vocabulary(self):
        """underwriting_capex_lines silently rewrites an unknown scope to
        'interior'. Site DD's scopes are property/unit/room, so without a
        mapping every roof would land in the interior budget."""
        for scope in ("property", "unit", "room"):
            line = costs.to_capex_lines([self._finding(scope=scope,
                                                       category_key=None)])[0]
            with self.subTest(scope):
                self.assertIn(line["scope"], udb.CAPEX_SCOPES)

    def test_property_scope_work_is_exterior(self):
        line = costs.to_capex_lines([self._finding(scope="property",
                                                   category_key="site_exterior")])[0]
        self.assertEqual(line["scope"], "exterior")

    def test_but_a_property_level_furnace_is_not_exterior_work(self):
        line = costs.to_capex_lines([self._finding(scope="property",
                                                   category_key="mep")])[0]
        self.assertEqual(line["scope"], "interior")

    def test_no_total_is_written_alongside_quantity_and_unit_cost(self):
        """line_total prefers an explicit total. Writing both would
        create two numbers that can disagree."""
        line = costs.to_capex_lines([self._finding()])[0]
        self.assertIsNone(line["total_cost"])
        self.assertEqual(ucx.line_total(line), 450.0)

    def test_a_finding_with_no_estimate_still_maps_with_no_unit_cost(self):
        line = costs.to_capex_lines([self._finding(est_unit_cost=None)])[0]
        self.assertIsNone(line["unit_cost"])
        self.assertEqual(ucx.line_total(line), 0.0)

    def test_nothing_is_marked_a_contingency(self):
        line = costs.to_capex_lines([self._finding()])[0]
        self.assertEqual(line["is_contingency"], 0)

    def test_an_empty_input_produces_an_empty_budget(self):
        self.assertEqual(costs.to_capex_lines([]), [])


class CapexRoundTripTests(unittest.TestCase):
    """Through the real writer, not a mock of it."""

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "uw.db"

    def _store(self, lines):
        with udb.get_connection(self.path) as conn:
            sid = udb.create_scenario(conn, {"name": "S", "property_label": "P"})
            udb.replace_capex_lines(conn, sid, lines)
            return udb.list_capex_lines(conn, sid)

    def _lines(self):
        rows = [
            {"id": 8, "scope": "room", "area_id": 5, "room_id": 9,
             "item_key": "toilet", "category_key": "condition",
             "instance_label": None, "est_unit_cost": 450.0},
            {"id": 9, "scope": "room", "area_id": 5, "room_id": 9,
             "item_key": "vanity_sink", "category_key": "condition",
             "instance_label": None, "est_unit_cost": 1250.0},
            {"id": 10, "scope": "room", "area_id": 5, "room_id": 9,
             "item_key": "vanity_sink", "category_key": "condition",
             "instance_label": None, "est_unit_cost": 1250.0},
            {"id": 11, "scope": "property", "area_id": None, "room_id": None,
             "item_key": "roof", "category_key": "structural_envelope",
             "instance_label": None, "est_unit_cost": 84000.0},
        ]
        return costs.to_capex_lines(rows, {"toilet": "Toilet",
                                           "vanity_sink": "Vanity & sink",
                                           "roof": "Roof"})

    def test_the_source_hook_survives_the_writer(self):
        """replace_capex_lines defaults source to 'manual'. A row Site DD
        wrote must not be relabelled as hand-typed on the way in."""
        stored = self._store(self._lines())
        self.assertEqual({r["source"] for r in stored}, {"site_dd"})

    def test_source_ref_survives_and_points_at_a_finding(self):
        stored = self._store(self._lines())
        self.assertEqual([r["source_ref"] for r in stored],
                         ["8", "9", "11"])

    def test_no_label_is_replaced_by_a_placeholder(self):
        stored = self._store(self._lines())
        self.assertEqual([r["label"] for r in stored],
                         ["Toilet", "Vanity & sink", "Roof"])

    def test_no_scope_is_silently_rewritten(self):
        lines = self._lines()
        stored = self._store(lines)
        self.assertEqual([r["scope"] for r in stored],
                         [l["scope"] for l in lines])
        self.assertIn("exterior", {r["scope"] for r in stored})

    def test_the_budget_totals_quantity_times_unit_cost(self):
        stored = self._store(self._lines())
        summary = ucx.summarize(stored, unit_count=1, contingency_pct=0)
        self.assertAlmostEqual(summary["itemized_total"],
                               450.0 + 1250.0 * 2 + 84000.0, places=2)

    def test_the_exterior_and_interior_split_is_preserved(self):
        stored = self._store(self._lines())
        summary = ucx.summarize(stored, unit_count=1, contingency_pct=0)
        self.assertAlmostEqual(summary["by_scope"]["exterior"], 84000.0, places=2)
        self.assertAlmostEqual(summary["by_scope"]["interior"],
                               450.0 + 2500.0, places=2)

    def test_a_scenario_with_no_site_dd_lines_is_unaffected(self):
        """Site DD must be able to write nothing without changing a
        scenario that never used it."""
        stored = self._store([])
        self.assertEqual(stored, [])
        self.assertEqual(ucx.summarize(stored, unit_count=1)["itemized_total"], 0.0)


if __name__ == "__main__":
    unittest.main()
