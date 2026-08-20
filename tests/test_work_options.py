"""Work recorded on a choice item must reach the capital budget.

THE BUG

`site_dd.capex_export()` filtered findings with:

    work = [f for f in findings if f["condition"] in cond.WORK_CONDITIONS]

A choice item answers in `detail`, not `condition`. So every one of these
was recorded by an inspector and silently discarded:

    smoke_alarm       missing        $260, in every bedroom
    smoke_alarm_unit  missing        $260
    co_alarm          missing        $195
    gfci              not_working    $195, the dangerous case
    hvac              missing        $7,500
    water_heater      missing        $1,725
    every appliance   absent         up to $1,640 each

Three of them are life safety. One stripped unit produced $0.00 against
$15,825.00 of researched figures, and the export printed "No items were
recorded as needing work" on top.

It was an oversight, not a decision: the filter's own comment said "only
findings that actually record a problem reach the budget", and reached for
`water_heater` as its example -- which is itself a choice item.

THE RULE BEING TESTED

Work-ness is a property of the OPTION SET, declared beside it, the same
shape WORK_CONDITIONS has for the wear scale. Not a global set of values
(the same string means opposite things on different items) and not a
per-item map (twenty-one entries nobody will maintain).
"""

import unittest
from pathlib import Path

from tools import site_dd_bank as bank
from tools import site_dd_capex_export as capex_export
from tools import site_dd_conditions as cond
from tools import site_dd_costs as costs
from tools import site_dd_unit_checklist as uc


def option_sets_in_use():
    """Every option tuple reachable from the catalogue or the bank."""
    sets = set()
    for item in bank.every_item().values():
        options = tuple(item.get("options") or ())
        if options:
            sets.add(options)
    return sets


def budget_for(findings):
    """The route's own pipeline: filter, price, build."""
    catalogue = bank.every_item()
    work = [f for f in findings
            if uc.needs_work(catalogue.get(f.get("item_key")),
                             f.get("condition"), f.get("detail"))]
    priced = [costs.apply_reference(f, None) for f in work]
    return capex_export.build_lines(priced, {},
                                    detail_labels=bank.detail_labels())


def finding(item_key, condition=None, detail=None, **over):
    item = bank.every_item()[item_key]
    row = {"scope": "room", "area_id": 1, "room_id": 1,
           "category_key": item.get("category"), "item_key": item_key,
           "instance_no": 1, "instance_label": None,
           "condition": condition, "detail": detail,
           "quantity": None, "measure": None,
           "est_unit_cost": None, "est_cost_source": None, "note": None}
    row.update(over)
    return row


class RegistryTests(unittest.TestCase):

    def test_every_option_set_in_use_has_an_answer(self):
        """Silence is how the gap was built. Absent is a failure, not 'no work'.

        An empty frozenset is a legitimate answer -- FLOORING_TYPES and
        PEST_TYPE record what a thing IS -- but it has to be written down,
        so that "considered, and the answer is none" is distinguishable
        from "nobody looked".
        """
        missing = sorted(
            [v for v, _ in options][:4]
            for options in option_sets_in_use()
            if options not in uc.WORK_OPTIONS)
        self.assertEqual(
            missing, [],
            "Option sets with no WORK_OPTIONS entry. Add one, even if it is "
            f"empty: {missing}")

    def test_no_stale_registry_entries(self):
        in_use = option_sets_in_use()
        stale = sorted([v for v, _ in options][:4]
                       for options in uc.WORK_OPTIONS
                       if options not in in_use)
        self.assertEqual(stale, [],
                         f"WORK_OPTIONS describes sets nobody uses: {stale}")

    def test_work_values_are_real_members_of_their_set(self):
        """A typo in a work value is silent: it simply never matches."""
        for options, work in uc.WORK_OPTIONS.items():
            values = {v for v, _ in options}
            with self.subTest(options=sorted(values)):
                self.assertTrue(
                    work <= values,
                    f"{sorted(work - values)} is not in this option set")

    def test_the_same_value_means_different_things_in_different_sets(self):
        """Why this is keyed by SET and can never be a global value list."""
        self.assertIn("present", uc.WORK_OPTIONS[uc.MOLD_STATES])
        self.assertNotIn("present", uc.WORK_OPTIONS[uc.PRESENCE])
        self.assertIn("none", uc.WORK_OPTIONS[uc.EGRESS_STATES])
        self.assertNotIn("none", uc.WORK_OPTIONS[uc.LEAK_STATES])

    def test_gfci_is_defined_once_and_shared(self):
        """Its tuple used to be written out twice, in KITCHEN and BATHROOM."""
        kitchen = uc.item_map(uc.items_for_room("kitchen"))["gfci"]
        bathroom = uc.item_map(uc.items_for_room("bathroom"))["gfci"]
        self.assertIs(kitchen["options"], uc.GFCI_STATES)
        self.assertIs(bathroom["options"], uc.GFCI_STATES)

    def test_the_bank_shares_the_named_sets(self):
        """A bank item with an inline tuple is one the registry cannot name."""
        items = bank.every_item()
        self.assertIs(items["washer_dryer"]["options"], uc.PRESENCE)
        self.assertIs(items["disposal"]["options"], uc.PRESENCE)
        self.assertIs(items["wd_hookups"]["options"], uc.WD_HOOKUP_STATES)


class NeedsWorkTests(unittest.TestCase):

    def setUp(self):
        self.items = bank.every_item()

    def test_rule_1_a_condition_still_decides(self):
        toilet = self.items["toilet"]
        for state in cond.WORK_CONDITIONS:
            self.assertTrue(uc.needs_work(toilet, state, None), state)
        for state in ("excellent", "good", "satisfactory", None, ""):
            self.assertFalse(uc.needs_work(toilet, state, None), state)

    def test_rule_2_a_work_option_decides_when_there_is_no_condition(self):
        for key, value in (("smoke_alarm", "missing"),
                           ("co_alarm", "missing"),
                           ("gfci", "not_working"),
                           ("gfci", "absent"),
                           ("appliance_range", "absent"),
                           ("appliance_range", "hookup_only"),
                           ("hvac", "missing"),
                           ("water_heater", "missing"),
                           ("mold", "suspected"),
                           ("pest_evidence", "droppings"),
                           ("visible_leaks", "minor"),
                           ("egress_window", "none"),
                           ("fire_extinguisher", "expired"),
                           ("wd_hookups", "partial")):
            with self.subTest(item=key, detail=value):
                self.assertTrue(uc.needs_work(self.items[key], None, value))

    def test_a_clean_option_is_not_work(self):
        for key, value in (("smoke_alarm", "working"),
                           ("gfci", "present"),
                           ("appliance_range", "present"),
                           ("mold", "none"),
                           ("visible_leaks", "none"),
                           ("egress_window", "compliant"),
                           ("fire_extinguisher", "current"),
                           ("wd_hookups", "complete"),
                           ("flooring_type", "carpet"),
                           ("pest_type", "rodents")):
            with self.subTest(item=key, detail=value):
                self.assertFalse(uc.needs_work(self.items[key], None, value))

    def test_rule_3_a_work_condition_string_in_detail_is_never_dropped(self):
        """The smoke_alarm case, which is what made this rule necessary.

        ALARM_STATES stores the literal value `replace`. The old filter
        read `condition`, so the same string it would have accepted one
        column over was discarded.
        """
        self.assertTrue(uc.needs_work(self.items["smoke_alarm"], None, "replace"))
        # And with no item resolved at all -- a stale key from an older
        # checklist -- it still cannot be dropped.
        self.assertTrue(uc.needs_work(None, None, "replace"))
        self.assertTrue(uc.needs_work(None, None, "repair"))

    def test_an_unknown_item_falls_back_to_the_condition(self):
        """Same tolerance is_known_item() gives a stale key: ignore, do not guess."""
        self.assertTrue(uc.needs_work(None, "replace", None))
        self.assertFalse(uc.needs_work(None, "good", "absent"))
        self.assertFalse(uc.needs_work(None, None, "absent"))

    def test_nothing_recorded_is_not_work(self):
        self.assertFalse(uc.needs_work(self.items["smoke_alarm"], None, None))
        self.assertFalse(uc.needs_work(self.items["toilet"], None, None))


class BudgetTests(unittest.TestCase):
    """The numbers from the investigation, pinned."""

    STRIPPED = (
        ("hvac", "missing", 7500.00),
        ("water_heater", "missing", 1725.00),
        ("appliance_fridge", "absent", 1640.00),
        ("appliance_range", "absent", 1150.00),
        ("washer", "absent", 925.00),
        ("dryer", "absent", 925.00),
        ("appliance_disposal", "absent", 375.00),
        ("appliance_microwave", "absent", 350.00),
        ("exhaust_fan", "absent", 325.00),
        ("smoke_alarm", "missing", 260.00),
        ("smoke_alarm_unit", "missing", 260.00),
        ("co_alarm", "missing", 195.00),
        ("gfci", "not_working", 195.00),
        ("appliance_dishwasher", "absent", None),   # UNPRICED, still a line
    )

    def test_the_stripped_unit_totals_15825(self):
        lines = budget_for([finding(k, detail=d) for k, d, _ in self.STRIPPED])
        self.assertEqual(len(lines), len(self.STRIPPED))
        total = sum(l["total"] for l in lines if l["total"] is not None)
        self.assertEqual(total, 15825.00)

    def test_each_item_carries_its_researched_figure(self):
        by_key = {l["item_key"]: l for l in
                  budget_for([finding(k, detail=d) for k, d, _ in self.STRIPPED])}
        for key, _detail, expected in self.STRIPPED:
            with self.subTest(item=key):
                self.assertEqual(by_key[key]["unit_cost"], expected)

    def test_an_unpriced_item_is_listed_rather_than_dropped(self):
        """Dropping it would make the budget look complete when it is not."""
        lines = budget_for([finding("appliance_dishwasher", detail="absent")])
        self.assertEqual(len(lines), 1)
        self.assertIsNone(lines[0]["unit_cost"])
        self.assertIsNone(lines[0]["total"])
        self.assertTrue(lines[0]["reason"])

    def test_a_life_safety_item_lands_under_life_safety(self):
        lines = budget_for([finding("smoke_alarm_unit", detail="missing")])
        self.assertEqual(lines[0]["category"], "life_safety")
        self.assertNotEqual(lines[0]["category_name"], "Uncategorised")

    def test_a_working_unit_still_produces_no_budget(self):
        clean = [finding("smoke_alarm", detail="working"),
                 finding("gfci", detail="present"),
                 finding("appliance_range", detail="present"),
                 finding("toilet", condition="good"),
                 finding("mold", detail="none")]
        self.assertEqual(budget_for(clean), [])


class LineStateTests(unittest.TestCase):
    """A line asking for $260 has to say what for."""

    def test_a_choice_line_reports_the_words_the_inspector_saw(self):
        for key, detail, expected in (
                ("smoke_alarm", "missing", "Missing"),
                ("gfci", "not_working", "Present, not working"),
                ("appliance_range", "absent", "Not there"),
                ("smoke_alarm", "replace", "Needs replacing")):
            with self.subTest(item=key, detail=detail):
                line = budget_for([finding(key, detail=detail)])[0]
                self.assertEqual(line["state"], expected)

    def test_a_condition_line_still_reports_its_condition(self):
        line = budget_for([finding("toilet", condition="replace")])[0]
        self.assertEqual(line["state"], "Replace")

    def test_the_raw_stored_value_is_never_shown(self):
        line = budget_for([finding("gfci", detail="not_working")])[0]
        self.assertNotIn("_", line["state"])


class GroupingTests(unittest.TestCase):

    def test_two_states_of_one_item_do_not_collapse(self):
        """Admitting these findings without widening the key would have
        replaced a silent drop with a silent merge: both alarms are $260."""
        lines = budget_for([finding("smoke_alarm", detail="missing"),
                            finding("smoke_alarm", detail="replace")])
        self.assertEqual(len(lines), 2)
        self.assertEqual({l["state"] for l in lines},
                         {"Missing", "Needs replacing"})

    def test_identical_findings_still_collapse_into_a_quantity(self):
        """Forty toilets are one line of quantity 40, and that is unchanged."""
        lines = budget_for([finding("smoke_alarm", detail="missing"),
                            finding("smoke_alarm", detail="missing")])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["quantity"], 2.0)
        self.assertEqual(lines[0]["total"], 520.00)


class HonestyMessageTests(unittest.TestCase):
    """Both sentences were false under the gap. Confirmed, not assumed."""

    def test_no_items_recorded_is_only_said_when_nothing_was(self):
        recorded = capex_export.summarize(budget_for([
            finding("smoke_alarm_unit", detail="missing"),
            finding("co_alarm", detail="missing"),
            finding("gfci", detail="not_working"),
            finding("appliance_range", detail="absent")]))
        self.assertNotIn("No items were recorded",
                         recorded["coverage_sentence"])

        nothing = capex_export.summarize(budget_for([
            finding("toilet", condition="good"),
            finding("smoke_alarm", detail="working")]))
        self.assertEqual(nothing["coverage_sentence"],
                         "No items were recorded as needing work.")

    def test_whole_recorded_budget_is_only_claimed_when_it_is_whole(self):
        priced = capex_export.summarize(budget_for([
            finding("smoke_alarm_unit", detail="missing"),
            finding("co_alarm", detail="missing")]))
        self.assertIn("whole recorded budget", priced["coverage_sentence"])
        self.assertFalse(priced["total_is_partial"])

        # closet is UNPRICED, so the budget is no longer whole.
        partial = capex_export.summarize(budget_for([
            finding("smoke_alarm_unit", detail="missing"),
            finding("co_alarm", detail="missing"),
            finding("closet", condition="replace")]))
        self.assertIn("NOT the full budget", partial["coverage_sentence"])
        self.assertTrue(partial["total_is_partial"])


if __name__ == "__main__":
    unittest.main()


class CaptureScreenTests(unittest.TestCase):
    """The screen that invites a cost and the export that spends it must
    agree about what counts as work. They did not: the cost box stayed
    collapsed on a missing smoke alarm, so even the manual override was
    hidden behind the assumption that only conditions cost money."""

    @classmethod
    def setUpClass(cls):
        import os, tempfile
        os.environ["SITE_DD_DB_PATH"] = str(
            Path(tempfile.mkdtemp()) / "work_options.db")
        from tools import site_dd_db as db
        cls.db = db
        from app import app
        app.config["WTF_CSRF_ENABLED"] = False
        cls.app = app
        with db.get_connection() as conn:
            cls.aid = db.create_assessment(conn, {
                "property_label": "Work Options", "assessed_on": "2026-08-19",
                "inspector": "test", "checklist_version": 2})
            cls.area = db.create_area(conn, cls.aid, {"kind": "unit", "label": "1"})
            cls.room = db.create_room(conn, cls.area, "bedroom")

    def page(self, url):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = self.app.config.get("ADMIN_USERNAME")
            s["_fresh"] = True
        return c.get(url, follow_redirects=True).get_data(as_text=True)

    def room_url(self):
        return (f"/tools/site-dd/assessment/{self.aid}"
                f"/areas/{self.area}/rooms/{self.room}")

    def box_for(self, html, item_key):
        """The <details> element for one item, as rendered."""
        anchor = f'id="item-{item_key}"'
        start = html.index(anchor)
        opening = html.index("<details", start)
        return html[opening:html.index(">", opening) + 1]

    def record(self, item_key, condition=None, detail=None):
        with self.db.get_connection() as conn:
            self.db.upsert_findings(conn, self.aid, [{
                "scope": "room", "area_id": self.area, "room_id": self.room,
                "category_key": bank.every_item()[item_key].get("category"),
                "item_key": item_key, "instance_no": 1,
                "condition": condition, "detail": detail,
            }])

    def test_the_cost_box_opens_on_a_missing_alarm(self):
        self.record("smoke_alarm", detail="missing")
        self.assertIn("open", self.box_for(self.page(self.room_url()),
                                           "smoke_alarm"))

    def test_the_cost_box_stays_shut_on_a_working_alarm(self):
        self.record("smoke_alarm", detail="working")
        self.assertNotIn("open", self.box_for(self.page(self.room_url()),
                                              "smoke_alarm"))

    def test_the_form_still_says_condition_if_present(self):
        """The instruction is correct and the fix belongs in the filter,
        not in what the inspector is asked.

        Read from a KITCHEN: the heading renders only above a choice item
        that offers a condition too, and a bedroom has none -- closet is a
        condition item, and egress_window and smoke_alarm are
        with_condition=False.
        """
        with self.db.get_connection() as conn:
            kitchen = self.db.create_room(conn, self.area, "kitchen")
        html = self.page(f"/tools/site-dd/assessment/{self.aid}"
                         f"/areas/{self.area}/rooms/{kitchen}")
        self.assertIn("Condition, if present", html)
