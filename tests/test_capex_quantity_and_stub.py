"""
Two fixes that share a shape: a thing that was written but not reachable.

QUANTITY

site_dd_costs.to_capex_lines() has always grouped findings and counted
instances -- forty toilets are one line of quantity 40. It was never
called by anything. The export that IS called, site_dd_capex_export.
build_lines(), hard-coded quantity to 1, so a unit cost entered by hand
produced a line total of exactly that unit cost however many of the thing
there were. Michelle asked for "that amount X quantity of the item"; the
arithmetic existed and the wire did not.

THE REFERENCE FIGURE

The same screen where a cost is typed did not show the researched figure
that would be used if it were left blank. Overriding a number you cannot
see is not overriding it. These tests pin that the figure is shown and,
just as importantly, that showing it did not give the capture screen the
ability to STORE it.

STUB PROPERTIES

A property is visible to the notetaker only through a Deal Dive,
Underwriting or Site DD record. Aliases attach by key, so an alias for a
property with no record is written and then never read by anything --
demonstrated, not assumed, in the orphan test below.
"""

import os
import tempfile
import unittest
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="capex-qty-")
for _var in ("SITE_DD_DB_PATH", "DEAL_DIVE_DB_PATH", "RENT_COMPS_DB_PATH",
             "MARKET_DATA_DB_PATH", "UNDERWRITING_DB_PATH",
             "SCORECARD_PRO_DB_PATH", "FIRE_METRICS_DB_PATH",
             "FEEDBACK_DB_PATH", "INVESTOR_REPORT_DB_PATH",
             "INVESTOR_NOTES_DB_PATH", "OPENAI_USAGE_DB_PATH",
             "APP_SETTINGS_DB_PATH"):
    os.environ[_var] = os.path.join(_SANDBOX, _var.lower() + ".db")
os.environ.setdefault("UPLOAD_FOLDER_PATH", os.path.join(_SANDBOX, "uploads"))

from tools import site_dd_capex_export as capex          # noqa: E402
from tools import site_dd_costs as costs                 # noqa: E402
from tools import site_dd_reference_costs as refcosts    # noqa: E402
from tools import underwriting_capex as ucx              # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def finding(**over):
    base = {"id": 1, "area_id": None, "room_id": 7, "scope": "room",
            "item_key": "toilet", "condition": "replace",
            "instance_no": 1, "instance_label": None,
            "category_key": "mep", "est_unit_cost": None,
            "est_cost_source": None}
    base.update(over)
    return base


LABELS = {"toilet": "Toilet", "sink": "Sink", "hvac": "HVAC"}


class QuantityTests(unittest.TestCase):
    """The arithmetic Michelle asked for: amount x quantity."""

    def test_forty_identical_toilets_are_one_line_of_forty(self):
        rows = [finding(id=i, instance_no=i, est_unit_cost=600.0,
                        est_cost_source="manual") for i in range(1, 41)]
        lines = capex.build_lines(rows, LABELS)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["quantity"], 40.0)

    def test_and_the_total_is_the_unit_cost_multiplied(self):
        rows = [finding(id=i, instance_no=i, est_unit_cost=600.0,
                        est_cost_source="manual") for i in range(1, 41)]
        line = capex.build_lines(rows, LABELS)[0]
        self.assertEqual(line["unit_cost"], 600.0)
        self.assertEqual(line["total"], 24_000.0)

    def test_the_multiplication_is_the_shared_one(self):
        """Not a second copy of qty x unit that could drift from the first."""
        rows = [finding(id=i, est_unit_cost=125.0, est_cost_source="manual")
                for i in (1, 2, 3)]
        line = capex.build_lines(rows, LABELS)[0]
        self.assertIsNone(line["total_cost"],
                          "the total must be derived, not written twice")
        self.assertEqual(line["total"], ucx.line_total(line))

    def test_a_single_instance_is_unchanged(self):
        """The old behaviour, for the case that was never wrong."""
        line = capex.build_lines(
            [finding(est_unit_cost=1725.0, est_cost_source="manual")],
            LABELS)[0]
        self.assertEqual(line["quantity"], 1.0)
        self.assertEqual(line["total"], 1725.0)

    def test_the_summary_totals_the_multiplied_lines(self):
        rows = [finding(id=i, est_unit_cost=600.0, est_cost_source="manual")
                for i in range(1, 11)]
        summary = capex.summarize(capex.build_lines(rows, LABELS))
        self.assertEqual(summary["total"], 6_000.0)
        self.assertEqual(summary["by_source"]["manual"], 6_000.0)

    def test_an_unpriced_item_still_contributes_nothing(self):
        """None, not 0.0.

        Zero is a claim that the work is free, and it sums silently into
        a total that then looks complete. None cannot be added up by
        accident, which is why summarize() has to route it to the
        unpriced set rather than into by_source.
        """
        rows = [finding(id=i) for i in (1, 2, 3)]
        line = capex.build_lines(rows, LABELS)[0]
        self.assertEqual(line["quantity"], 3.0)
        self.assertIsNone(line["unit_cost"])
        self.assertIsNone(line["total"])

        summary = capex.summarize([line])
        # None, not 0.0: there IS a line and it could not be priced, so a
        # zero would read as "this work is free" rather than "unknown".
        self.assertIsNone(summary["total"])
        self.assertEqual(summary["unpriced_count"], 1)
        self.assertEqual(summary["priced_count"], 0)


class GroupingBoundaryTests(unittest.TestCase):
    """What may be absorbed into a quantity, and what may not.

    to_capex_lines() groups on (area, room, item) and takes the first
    non-null cost, which silently loses a differing price. These are the
    cases where collapsing would be wrong.
    """

    def test_different_prices_do_not_collapse(self):
        rows = [finding(id=1, est_unit_cost=450.0, est_cost_source="manual"),
                finding(id=2, est_unit_cost=600.0, est_cost_source="manual")]
        lines = capex.build_lines(rows, LABELS)
        self.assertEqual(len(lines), 2)
        self.assertEqual(capex.summarize(lines)["total"], 1050.0,
                         "neither price may vanish into the other")

    def test_different_conditions_do_not_collapse(self):
        rows = [finding(id=1, condition="replace", est_unit_cost=600.0,
                        est_cost_source="manual"),
                finding(id=2, condition="repair", est_unit_cost=600.0,
                        est_cost_source="manual")]
        self.assertEqual(len(capex.build_lines(rows, LABELS)), 2)

    def test_different_rooms_do_not_collapse(self):
        rows = [finding(id=1, room_id=7, est_unit_cost=600.0,
                        est_cost_source="manual"),
                finding(id=2, room_id=8, est_unit_cost=600.0,
                        est_cost_source="manual")]
        self.assertEqual(len(capex.build_lines(rows, LABELS)), 2)

    def test_a_manual_and_a_reference_price_do_not_collapse(self):
        """Provenance is part of what makes two lines the same line."""
        rows = [finding(id=1, est_unit_cost=600.0, est_cost_source="manual"),
                finding(id=2, est_unit_cost=600.0, est_cost_source="reference")]
        lines = capex.build_lines(rows, LABELS)
        self.assertEqual(len(lines), 2)
        self.assertEqual({l["source_label"] for l in lines},
                         {"Inspector estimate", "Researched average"})

    def test_a_named_instance_keeps_its_own_line(self):
        rows = [finding(id=1, instance_label="hallway", est_unit_cost=600.0,
                        est_cost_source="manual"),
                finding(id=2, est_unit_cost=600.0, est_cost_source="manual")]
        self.assertEqual(len(capex.build_lines(rows, LABELS)), 2)


class ReferenceStillSupersededTests(unittest.TestCase):
    """No regression: a person beats the table, always."""

    def test_a_manual_cost_survives_apply_reference(self):
        f = finding(item_key="hvac", est_unit_cost=4200.0,
                    est_cost_source="manual")
        out = costs.apply_reference(f)
        self.assertEqual(out["est_unit_cost"], 4200.0)
        self.assertEqual(out["est_cost_source"], "manual")

    def test_and_the_table_would_have_said_something_else(self):
        """The control: proves the test above is not vacuous."""
        self.assertIsNotNone(refcosts.for_item("hvac"))
        self.assertNotEqual(refcosts.for_item("hvac").unit_cost, 4200.0)

    def test_an_unpriced_finding_does_take_the_reference(self):
        out = costs.apply_reference(finding(item_key="hvac"))
        self.assertEqual(out["est_unit_cost"],
                         refcosts.for_item("hvac").unit_cost)
        self.assertEqual(out["est_cost_source"], "reference")

    def test_the_multiplied_total_uses_the_manual_figure(self):
        rows = [finding(id=i, item_key="hvac", est_unit_cost=4200.0,
                        est_cost_source="manual") for i in (1, 2)]
        line = capex.build_lines([costs.apply_reference(f) for f in rows],
                                 LABELS)[0]
        self.assertEqual(line["quantity"], 2.0)
        self.assertEqual(line["total"], 8400.0)
        self.assertEqual(line["source_label"], "Inspector estimate")


class ReferenceHintTests(unittest.TestCase):
    """The figure is shown on the capture screen -- and only shown."""

    def test_it_reports_what_the_table_would_charge(self):
        hint = costs.reference_hint(finding(item_key="hvac"))
        self.assertEqual(hint["unit_cost"], refcosts.for_item("hvac").unit_cost)
        self.assertIn(refcosts.RESEARCHED_ON, hint["researched_on"])

    def test_it_is_none_when_there_is_nothing_to_override(self):
        self.assertIsNone(costs.reference_hint(finding(item_key="foundation")))

    def test_it_says_when_a_person_has_already_overridden_it(self):
        hint = costs.reference_hint(finding(item_key="hvac",
                                            est_unit_cost=4200.0,
                                            est_cost_source="manual"))
        self.assertTrue(hint["overridden"])
        self.assertTrue(hint["differs"])

    def test_it_carries_no_provenance_value(self):
        """It must not be possible to store what this returns as a cost."""
        hint = costs.reference_hint(finding(item_key="hvac"))
        self.assertNotIn("est_cost_source", hint)
        self.assertNotIn(costs.SOURCE_REFERENCE, hint.values())

    def test_showing_it_did_not_let_a_template_assign_it(self):
        """The existing discipline, re-asserted after touching the screens."""
        for path in sorted((ROOT / "templates").rglob("*.html")):
            body = path.read_text(encoding="utf-8")
            with self.subTest(template=path.name):
                self.assertNotIn("SOURCE_REFERENCE", body)
                self.assertNotIn("est_cost_source =", body)

    def test_the_input_is_not_prefilled_with_the_reference_figure(self):
        """A prefilled box would be submitted as the inspector's own.

        Leaving it empty is what keeps "I did not price this" different
        from "I priced this at the national average".
        """
        for name in ("site_dd_area.html", "site_dd_room.html"):
            body = (ROOT / "templates" / "tools" / name).read_text(encoding="utf-8")
            with self.subTest(template=name):
                self.assertIn("ref.unit_cost", body, "the figure is shown")
                self.assertNotIn('value="{{ ref', body,
                                 "but never sitting in the input")


if __name__ == "__main__":
    unittest.main()


class StubPropertyTests(unittest.TestCase):
    """A property that exists as a name, and is therefore real."""

    @classmethod
    def setUpClass(cls):
        from app import app
        app.config["WTF_CSRF_ENABLED"] = False
        cls.app = app

    def client(self):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = self.app.config.get("ADMIN_USERNAME")
            s["_fresh"] = True
        return c

    def entries(self):
        from tools import investor_notes as notes
        from tools import investor_notes_db as ndb
        with ndb.get_connection() as conn:
            return notes._property_entries(conn)

    def test_a_stub_becomes_a_findable_property(self):
        name = "Testville Gardens"
        before = {e["label"] for e in self.entries()}
        self.assertNotIn(name, before)
        self.client().post("/tools/investor-report/notes/properties",
                           data={"property_label": name},
                           follow_redirects=True)
        after = {e["label"]: e for e in self.entries()}
        self.assertIn(name, after)
        self.assertEqual(after[name]["sources"], ["Underwriting"])

    def test_it_uses_the_same_key_derivation_as_every_other_scenario(self):
        from tools import investor_notes_properties as props
        name = "Keyderiv Apartments"
        self.client().post("/tools/investor-report/notes/properties",
                           data={"property_label": name}, follow_redirects=True)
        entry = next(e for e in self.entries() if e["label"] == name)
        self.assertEqual(entry["key"], props.label_key(name))

    def test_the_scenario_carries_no_assumptions(self):
        from tools import underwriting_db as udb
        name = "Empty Assumptions Court"
        self.client().post("/tools/investor-report/notes/properties",
                           data={"property_label": name}, follow_redirects=True)
        with udb.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM underwriting_scenarios WHERE property_label = ?",
                (name,)).fetchone()
        self.assertIsNotNone(row)
        for column in ("purchase_price", "ltv_pct", "exit_cap_pct"):
            self.assertIsNone(dict(row)[column],
                              f"{column} should be empty on a stub")

    def test_a_blank_name_is_refused(self):
        before = len(self.entries())
        self.client().post("/tools/investor-report/notes/properties",
                           data={"property_label": "   "}, follow_redirects=True)
        self.assertEqual(len(self.entries()), before)

    def test_a_duplicate_is_refused_rather_than_doubled(self):
        name = "Only Once Place"
        c = self.client()
        c.post("/tools/investor-report/notes/properties",
               data={"property_label": name}, follow_redirects=True)
        out = c.post("/tools/investor-report/notes/properties",
                     data={"property_label": name.lower()},
                     follow_redirects=True).get_data(as_text=True)
        self.assertIn("already here", out)
        self.assertEqual(
            len([e for e in self.entries()
                 if e["label"].lower() == name.lower()]), 1)

    def test_a_stub_can_be_aliased_and_then_matches(self):
        """The end the whole thing exists for."""
        from tools import investor_notes_match as matching
        name = "The Canyonesque Apartments"
        c = self.client()
        c.post("/tools/investor-report/notes/properties",
               data={"property_label": name}, follow_redirects=True)
        entry = next(e for e in self.entries() if e["label"] == name)
        c.post("/tools/investor-report/notes/aliases",
               data={"property_key": entry["key"], "alias": "Canyonesque"},
               follow_redirects=True)
        refreshed = next(e for e in self.entries() if e["label"] == name)
        self.assertIn("Canyonesque", refreshed["aliases"])
        body = ("Michelle: Canyonesque next. The Canyonesque lease-up is done "
                "and Canyonesque insurance renewed. Good quarter for Canyonesque.")
        result = matching.match(body, self.entries())
        self.assertEqual(result["outcome"], "matched")
        self.assertEqual(result["key"], entry["key"])


class AliasValidationTests(unittest.TestCase):
    """An alias aimed at nothing used to be stored and never read."""

    def test_an_unknown_key_is_refused_when_the_caller_supplies_the_real_ones(self):
        from tools import investor_notes_db as ndb
        with ndb.get_connection() as conn:
            with self.assertRaises(ndb.UnknownProperty):
                ndb.add_alias(conn, "label:nothing at all", "Ghost",
                              valid_keys={"deal:1"})

    def test_and_nothing_was_written(self):
        from tools import investor_notes_db as ndb
        with ndb.get_connection() as conn:
            try:
                ndb.add_alias(conn, "label:nothing here", "Ghost",
                              valid_keys={"deal:1"})
            except ndb.UnknownProperty:
                pass
            stored = [a["property_key"] for a in ndb.list_aliases(conn)]
        self.assertNotIn("label:nothing here", stored)

    def test_a_known_key_still_works(self):
        from tools import investor_notes_db as ndb
        with ndb.get_connection() as conn:
            self.assertTrue(
                ndb.add_alias(conn, "deal:99", "Ninetynine",
                              valid_keys={"deal:99"}))

    def test_omitting_valid_keys_keeps_the_old_behaviour(self):
        """Existing callers and tests are not broken by the new argument."""
        from tools import investor_notes_db as ndb
        with ndb.get_connection() as conn:
            self.assertTrue(ndb.add_alias(conn, "deal:1234", "Unchecked"))

    def test_the_route_refuses_and_says_why(self):
        from app import app
        app.config["WTF_CSRF_ENABLED"] = False
        c = app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = app.config.get("ADMIN_USERNAME")
            s["_fresh"] = True
        out = c.post("/tools/investor-report/notes/aliases",
                     data={"property_key": "label:does not exist",
                           "alias": "Phantom"},
                     follow_redirects=True).get_data(as_text=True)
        self.assertIn("No property is known by the key", out)

    def test_the_orphan_it_prevents_really_was_invisible(self):
        """The control: shows why the refusal matters.

        An alias on a key with no property is attached to nothing by
        build(), so it is stored and then never read by anything.
        """
        from tools import investor_notes_properties as props
        entries = props.build(deals=[], underwriting_labels=[],
                              site_dd_labels=[],
                              aliases={"label:ghost town": ["Ghost"]})
        self.assertEqual(entries, [])
