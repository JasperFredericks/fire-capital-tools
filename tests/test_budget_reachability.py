"""Every priced item must be reachable from something an inspector can record.

THE BUG THIS CATCHES

`site_dd_reference_costs` priced a smoke alarm at $260, a CO alarm at
$195 and a GFCI at $195. None of the three could ever appear in a capital
budget. The capex filter admitted a finding only if its CONDITION was
repair or replace, and all three are choice items that answer in `detail`
-- so an inspector recording "Missing" produced nothing, and the export
printed "No items were recorded as needing work" over the top of it. One
stripped unit came out at $0.00 against $15,825.00 of researched figures.

WHY THE OTHER TWO SWEEPS CANNOT SEE IT, AND ARE RIGHT TO PASS

test_dead_readers and test_route_reachability look for a FUNCTION nothing
calls. That is the right instrument for the four features that shipped
invisible, and for to_capex_lines(), which is dead in the same way today.

This is a different shape. Every function involved here has a live caller:
build_lines() is called, apply_reference() is called, REFERENCE_COSTS is
read on every export. What was dead was a VALUE -- $195 that no input
could reach, because a predicate upstream excluded every finding that
could carry it. No reference-counting sweep can see that, so this one asks
a different question and is deliberately kept separate rather than folded
into either of them.

WHAT IT DOES

Enumerates every catalogue item and every state an inspector can actually
record on it, pushes each through the REAL pipeline -- the same filter,
the same apply_reference, the same build_lines that
site_dd.capex_export() assembles -- and requires that a priced item have
at least one state producing a budget line.

Bounded and cheap: 39 items, at most ~40 states each, well under a second.
It would have failed on the day 0dbd3df landed.

THE ALLOWLIST

An item is exempt only with a stated reason, and a stale entry fails too:
once an item becomes reachable it has to leave the list, or the list stops
meaning anything. Same discipline as the other two sweeps.
"""

import unittest

from tools import site_dd_bank as bank
from tools import site_dd_capex_export as capex_export
from tools import site_dd_conditions as cond
from tools import site_dd_costs as costs
from tools import site_dd_reference_costs as refcosts
from tools import site_dd_unit_checklist as uc


# item key -> why it can never produce a budget line, and why that is right.
ALLOWED_UNREACHABLE = {
    "flooring_type": (
        "NOT_A_COST_ITEM. Records what the floor IS, not work on it -- the "
        "material feeds the flooring rate, and the condition is recorded on "
        "the separate `flooring` item. No answer to 'what is it' is a repair."),
    "pest_type": (
        "NOT_A_COST_ITEM. Species does not change whether a treatment is "
        "needed; `pest_evidence` carries that and is reachable."),
    "hvac_age": "NOT_A_COST_ITEM. A KIND_NUMBER reading, not a defect.",
    "water_heater_age": "NOT_A_COST_ITEM. A KIND_NUMBER reading, not a defect.",
    "water_heater_gal": "NOT_A_COST_ITEM. A KIND_NUMBER capacity, not a defect.",
}


def catalogue_items():
    """Every room and unit checklist item, deduplicated by key.

    Keys repeat across room types on purpose -- `flooring` is the same
    question everywhere -- so the first definition wins.
    """
    out = {}
    for room_type, _ in uc.ROOM_TYPES:
        for item in uc.items_for_room(room_type):
            out.setdefault(item["key"], item)
    for item in uc.items_for_unit():
        out.setdefault(item["key"], item)
    return out


def recordable_states(item):
    """Every (condition, detail) pair the capture form can produce.

    A choice item with with_condition=True offers both, but the form
    renders the condition under "Condition, if present" -- so the
    (None, 'absent') pair is included, and it is the one that matters.
    """
    states = []
    if item["kind"] == uc.KIND_CONDITION:
        states += [(c, None) for c in cond.CONDITIONS]
    elif item["kind"] == uc.KIND_CHOICE:
        for value, _label in item["options"]:
            states.append((None, value))
            if item["with_condition"]:
                states += [(c, value) for c in cond.CONDITIONS]
    else:
        states.append((None, None))
    return states


def lines_for(item, condition, detail, catalogue=None, detail_labels=None):
    """One finding through the real pipeline, exactly as the route builds it."""
    finding = {
        "scope": "room", "area_id": 1, "room_id": 1,
        "category_key": item.get("category"), "item_key": item["key"],
        "instance_no": 1, "instance_label": None,
        "condition": condition, "detail": detail,
        "quantity": None, "measure": None,
        "est_unit_cost": None, "est_cost_source": None, "note": None,
    }
    catalogue = bank.every_item() if catalogue is None else catalogue
    work = [f for f in [finding]
            if uc.needs_work(catalogue.get(f["item_key"]),
                             f["condition"], f["detail"])]
    priced = [costs.apply_reference(f, None) for f in work]
    return capex_export.build_lines(
        priced, {}, detail_labels=bank.detail_labels()
        if detail_labels is None else detail_labels)


def unreachable_priced_items(catalogue=None):
    """Priced items no recordable state can turn into a budget line."""
    catalogue = bank.every_item() if catalogue is None else catalogue
    dead = []
    for key, item in sorted(catalogue_items().items()):
        if refcosts.for_item(key) is None:
            continue
        if not any(lines_for(item, c, d, catalogue)
                   for c, d in recordable_states(item)):
            dead.append(key)
    return dead


class ControlsTests(unittest.TestCase):
    """An instrument that has never returned a difference has not been tested."""

    def test_positive_control_a_replaced_toilet_produces_a_line(self):
        lines = lines_for(catalogue_items()["toilet"], "replace", None)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["unit_cost"], 600.00)

    def test_negative_control_a_good_toilet_produces_nothing(self):
        self.assertEqual(lines_for(catalogue_items()["toilet"], "good", None), [])

    def test_negative_control_nothing_recorded_produces_nothing(self):
        items = catalogue_items()
        for key, condition, detail in (
                ("smoke_alarm", None, None),        # not visited
                ("smoke_alarm", None, "working"),   # visited, fine
                ("appliance_range", None, "present"),
                ("mold", None, "none"),
                ("egress_window", None, "compliant"),
        ):
            with self.subTest(item=key, detail=detail):
                self.assertEqual(lines_for(items[key], condition, detail), [])

    def test_the_check_itself_fails_when_a_work_value_is_removed(self):
        """The positive control for the SWEEP, not for the pipeline.

        Empty the GFCI option set's answer and `gfci` -- $195, in the
        kitchen and every bathroom -- must be reported BY NAME. A check
        that cannot fail proves nothing.

        gfci is the right probe precisely because nothing else can rescue
        it: it is with_condition=False, so there is no condition to fall
        back on, and none of its values is a work-condition string, so
        needs_work()'s rule 3 does not apply either. That is exactly the
        shape of the original bug.
        """
        original = uc.WORK_OPTIONS[uc.GFCI_STATES]
        try:
            uc.WORK_OPTIONS[uc.GFCI_STATES] = frozenset()
            dead = unreachable_priced_items()
            self.assertIn("gfci", dead)
        finally:
            uc.WORK_OPTIONS[uc.GFCI_STATES] = original
        self.assertEqual(unreachable_priced_items(), [],
                         "restore failed -- the registry was left modified")

    def test_an_alarm_survives_its_registry_row_being_emptied(self):
        """Rule 3 is load-bearing, and this is where that is demonstrated.

        Emptying the alarm option set's answer does NOT make smoke_alarm
        unreachable, because ALARM_STATES carries the literal value
        `replace` -- a string that is in WORK_CONDITIONS -- and
        needs_work() admits that whatever the registry says.

        This is the case the old filter dropped: the same string it would
        have accepted in `condition` was discarded in `detail`. Pinned
        here so the belt-and-braces rule cannot be removed as redundant.
        """
        original = uc.WORK_OPTIONS[uc.ALARM_STATES]
        try:
            uc.WORK_OPTIONS[uc.ALARM_STATES] = frozenset()
            items = catalogue_items()
            still = lines_for(items["smoke_alarm"], None, "replace")
            self.assertEqual(len(still), 1)
            self.assertEqual(still[0]["unit_cost"], 260.00)
            # `missing` is NOT a work-condition string, so it does go dark
            # -- which is what makes the registry row necessary as well.
            self.assertEqual(lines_for(items["smoke_alarm"], None, "missing"), [])
        finally:
            uc.WORK_OPTIONS[uc.ALARM_STATES] = original


class ReachabilityTests(unittest.TestCase):

    def test_every_priced_item_can_reach_the_budget(self):
        dead = unreachable_priced_items()
        self.assertEqual(
            dead, [],
            "These items have a researched cost that NO recordable state can "
            "apply. A figure nothing can reach is a budget line that silently "
            "never appears:\n  " + "\n  ".join(dead))

    def test_allowlisted_items_are_still_unreachable(self):
        """Stale entries fail. Once an item becomes reachable it must leave."""
        items = catalogue_items()
        for key, reason in sorted(ALLOWED_UNREACHABLE.items()):
            with self.subTest(item=key):
                self.assertIn(key, items, f"{key} is no longer a catalogue item")
                self.assertTrue(reason.strip(), f"{key} needs a stated reason")
                produced = any(lines_for(items[key], c, d)
                               for c, d in recordable_states(items[key]))
                self.assertFalse(
                    produced,
                    f"{key} now produces a budget line — remove it from "
                    f"ALLOWED_UNREACHABLE.")

    def test_the_allowlist_covers_exactly_the_unpriced_dead_items(self):
        """Nothing unreachable may go unexplained, priced or not."""
        items = catalogue_items()
        dead = {key for key, item in items.items()
                if not any(lines_for(item, c, d)
                           for c, d in recordable_states(item))}
        self.assertEqual(
            dead, set(ALLOWED_UNREACHABLE),
            "An item became unreachable without a stated reason, or an "
            "allowlist entry is stale.")


if __name__ == "__main__":
    unittest.main()
