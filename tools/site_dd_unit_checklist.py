"""
FIRE Capital Tools - Site DD unit and room checklists.

What gets inspected inside a unit: the items common to every room, the
extra items a kitchen or a bathroom needs, and the unit-wide items that
belong to no single room. Pure: no Flask, no database, no I/O.

THREE KINDS OF ITEM, AND WHY

Not everything an inspector records is a condition.

  condition   wear on the five-state scale. Walls, windows, a tub.
  choice      a categorical fact. The flooring is vinyl. The dishwasher
              is a hookup with no machine in it. The smoke alarm is
              missing. These are NOT positions on a wear scale, and
              forcing them onto one would mean recording "Replace" for an
              appliance that was never installed.
  number      a measured quantity with a unit. Water heater capacity in
              gallons, equipment age in years.

A choice item may also carry a condition -- a dishwasher that is present
can still be Good or Replace -- but a dishwasher that is absent has no
condition at all, and the form reflects that.

FLOORING TYPE IS A SEPARATE ITEM FROM FLOORING CONDITION

"Vinyl, Good" and "Carpet, Replace" are two different facts, and a repair
cost cannot be estimated from the condition alone: replacing carpet and
replacing hardwood are not the same line in a budget. So every room
carries both, as two items.

ITEM KEYS REPEAT ACROSS ROOMS ON PURPOSE

`flooring` means the same question in every room. Findings are unique on
(assessment, area, room, item), so the same key in the kitchen and in
bedroom 2 are different rows without needing different names. Namespacing
them would make "show me every floor that needs replacing" a string
-parsing exercise instead of a query.
"""

from __future__ import annotations

from typing import Any

from tools import site_dd_conditions as cond

CHECKLIST_VERSION = 1

KIND_CONDITION = "condition"
KIND_CHOICE = "choice"
KIND_NUMBER = "number"


# ── Capex category per item ──────────────────────────────────────────────
#
# Aligned to how site_dd_checklist already categorises the equivalent
# property-scope item, so the two scopes agree rather than each inventing
# its own grouping:
#
#   windows_doors     -> structural_envelope   so: windows
#   alarms_detectors  -> life_safety           so: smoke / CO alarms
#   egress_signage    -> life_safety           so: egress window
#   hvac_units        -> mep                   so: hvac, hvac_age
#   water_heaters     -> mep                   so: water_heater*
#   electrical_panels -> mep                   so: outlets, lighting, GFCI
#   plumbing_supply   -> mep                   so: fixtures
#   ventilation       -> mep                   so: exhaust fan, dryer vent
#   flooring          -> interior_units
#   walls_ceilings    -> interior_units
#   unit_appliances   -> interior_units        so: every appliance_*
#
# The one judgement not read straight off that list is the fixture/finish
# split inside kitchens and bathrooms. The rule is "who does the work":
# a plumber or electrician (mep), or a contractor doing finishes and
# cabinetry (interior_units). So tub, toilet, sink and faucet are mep;
# cabinets, countertops, flooring and closets are interior_units.
#
# A key absent from this table gets None, which to_capex_lines() reports
# as an uncategorised line. That is the honest outcome for an item nobody
# has classified, and better than a catch-all bucket that looks like a
# decision.
CATEGORIES_BY_ITEM: dict[str, str] = {
    # Every room
    "flooring_type": "interior_units",
    "flooring": "interior_units",
    "walls_ceiling": "interior_units",
    "windows": "structural_envelope",
    "outlets_switches": "mep",
    "lighting": "mep",
    # Kitchen
    "appliance_range": "interior_units",
    "appliance_fridge": "interior_units",
    "appliance_dishwasher": "interior_units",
    "appliance_microwave": "interior_units",
    "appliance_disposal": "interior_units",
    "cabinets": "interior_units",
    "countertops": "interior_units",
    "sink_faucet": "mep",
    "gfci": "mep",
    # Bathroom
    "tub_shower": "mep",
    "toilet": "mep",
    "vanity_sink": "mep",
    "exhaust_fan": "mep",
    "visible_leaks": "mep",
    # Bedroom
    "closet": "interior_units",
    "egress_window": "life_safety",
    "smoke_alarm": "life_safety",
    # Laundry
    "washer": "interior_units",
    "dryer": "interior_units",
    "dryer_vent": "mep",
    # Unit-wide
    "smoke_alarm_unit": "life_safety",
    "co_alarm": "life_safety",
    "water_heater": "mep",
    "water_heater_gal": "mep",
    "water_heater_age": "mep",
    "hvac": "mep",
    "hvac_age": "mep",
    # "Entry door & lock", categorised on the lock rather than the door.
    # An exterior door is envelope work, but a unit entry door is recorded
    # here because it secures the dwelling, and that is what a failing one
    # costs money to put right.
    "entry_door": "life_safety",
    # Added from the v7 form.
    #
    # Mold and pests are environmental findings, not interior finishes.
    # Remediating either is a specialist scope with its own contractor,
    # and burying them under "Interior & Units" would put them in the same
    # budget line as repainting a bedroom.
    "mold": "access_environmental",
    "pest_evidence": "access_environmental",
    "pest_type": "access_environmental",
    # A thermostat is the control end of the HVAC. (gfci already had a
    # category; it already existed as a kitchen and bathroom item.)
    "thermostat": "mep",
    "fire_extinguisher": "life_safety",
}


def category_for(item_key: str) -> str | None:
    """The capex category for a room or unit checklist item, or None."""
    return CATEGORIES_BY_ITEM.get(item_key)


def _item(key: str, label: str, kind: str = KIND_CONDITION,
          options: tuple[tuple[str, str], ...] | None = None,
          measure: str | None = None, hint: str | None = None,
          with_condition: bool = True) -> dict[str, Any]:
    """One checklist item.

    KIND AND CATEGORY ARE TWO DIFFERENT FACTS, KEPT SEPARATE

    `kind` is a UI fact: whether this renders as condition buttons, a set
    of choices, or a number box. `category` is a CAPEX fact: which budget
    heading the work belongs under once it reaches Underwriting.

    They were conflated. site_dd.py wrote item["kind"] into the findings
    table's category_key column, so every room and unit checklist row
    carried "condition" or "choice" where a real category should have
    been, and the capex export emitted those as budget headings. Nothing
    ever read the column back expecting a kind, so the fix is to stop
    overwriting one with the other -- no stored data has to move.

    The category is looked up from CATEGORIES_BY_ITEM rather than passed
    in, so the whole mapping is readable in one place instead of being
    scattered across forty constructor calls.
    """
    return {
        "key": key, "label": label, "kind": kind,
        # The capex heading this item's work belongs under. Same
        # vocabulary as site_dd_checklist.CATEGORIES and the item bank,
        # so a budget assembled across scopes groups coherently rather
        # than by accident of which scope recorded the finding.
        "category": CATEGORIES_BY_ITEM.get(key),
        "options": options or (), "measure": measure, "hint": hint,
        # Whether a condition is offered alongside a choice. An appliance
        # that is present has a condition; an alarm that is missing does
        # not need one, because "missing" is the whole answer.
        "with_condition": with_condition if kind == KIND_CHOICE else (kind == KIND_CONDITION),
    }


# Public name for the same builder. The item bank shapes its entries like
# checklist items so they flow through the form loop, _collect and the
# roll-up unchanged; that only works if they are built the same way.
make_item = _item


# ── Shared option sets ───────────────────────────────────────────────────

FLOORING_TYPES = (
    ("vinyl", "Vinyl / LVP"),
    ("carpet", "Carpet"),
    ("hardwood", "Hardwood"),
    ("laminate", "Laminate"),
    ("tile", "Tile"),
    ("concrete", "Concrete"),
    ("other", "Other"),
)

# Michelle's four states for an appliance, kept verbatim in meaning:
# it is there and works, it is there and is poor, there is only a hookup,
# or there is nothing at all.
PRESENCE = (
    ("present", "Present"),
    ("hookup_only", "Hookup only"),
    ("absent", "Not there"),
)

ALARM_STATES = (
    ("working", "Working"),
    ("missing", "Missing"),
    ("replace", "Needs replacing"),
)

EQUIPMENT_STATES = (
    ("working", "Working"),
    ("missing", "Missing"),
    ("replace", "Needs replacing"),
)

# GFCI, egress and leaks were written inline at their use sites. They are
# named here because an option set has to be a nameable thing before
# WORK_OPTIONS below can say anything about it -- and because the gfci
# tuple was written out TWICE, once in KITCHEN and once in BATHROOM, which
# is a live drift risk: changing one and not the other would give the same
# question two different answer sets depending on which room you were
# standing in.
GFCI_STATES = (
    ("present", "Present & working"),
    ("not_working", "Present, not working"),
    ("absent", "None"),
)

EGRESS_STATES = (
    ("compliant", "Opens, meets egress"),
    ("restricted", "Opens, restricted"),
    ("none", "No egress window"),
)

LEAK_STATES = (
    ("none", "None seen"),
    ("minor", "Minor / staining"),
    ("active", "Active leak"),
)

# Used only by the item bank, defined here because this module owns the
# option-set vocabulary -- the bank already imports PRESENCE from here for
# washer_dryer and disposal. Keeping it inline in the bank would leave one
# option set that WORK_OPTIONS could not name.
WD_HOOKUP_STATES = (
    ("complete", "Drain, vent and power"),
    ("partial", "Incomplete"),
    ("absent", "None"),
)


# ── States taken from Paresh's v7 form ───────────────────────────────────
#
# His forms turned up after the Site DD rebuild, having been in
# production use throughout it. The CONTENT is what is valuable: items a
# mature instrument asks about that ours never did.
#
# The shape is ours, not his. Where a thing wears, it takes our five-state
# condition scale so the rollup and completion percentage still work.
# Where a thing is present or not, it is a choice -- the distinction this
# file already draws for alarms, and the reason it draws it is unchanged:
# "missing" is not a position on a wear scale.

# Mold gets three values, not yes/no. An inspector who is unsure needs
# somewhere to put that, and "Suspected" is the state that actually
# triggers a specialist rather than a work order.
MOLD_STATES = (
    ("none", "None seen"),
    ("suspected", "Suspected"),
    ("present", "Present"),
)

# Evidence first, species second. Droppings and live pests are different
# urgencies, and damage without either is a different finding again.
PEST_EVIDENCE = (
    ("none", "None seen"),
    ("droppings", "Droppings"),
    ("live", "Live pests"),
    ("damage", "Damage"),
)

PEST_TYPE = (
    ("rodents", "Rodents"),
    ("roaches", "Roaches"),
    ("bed_bugs", "Bed bugs"),
    ("other", "Other / unsure"),
)

# Whether the extinguisher's inspection is CURRENT, which is a compliance
# fact with a date behind it, not a judgement about the extinguisher.
EXTINGUISHER_STATES = (
    ("current", "Inspected, in date"),
    ("expired", "Inspection out of date"),
    ("missing", "Missing"),
)

# NOTE ON GFCI, WHICH IS ALREADY HERE
#
# It was on the "missing from our checklist" list and it should not have
# been: gfci already exists as a kitchen and bathroom item, scoped to the
# wet areas where the protection is actually required. Paresh's form asks
# it once per unit; per wet room is the better question and we already
# ask it.
#
# The real difference his form exposed -- ours recorded presence, his
# recorded whether the outlet TRIPS -- is closed: GFCI_STATES above
# carries `not_working`, "Present, not working", which is the dangerous
# case neither set caught alone. It did NOT need to be a `detail`
# question on top of a condition, as an earlier note here claimed; it is
# a third option value on a choice item, which is what the item already
# was.


# ── Which option values mean work is needed ──────────────────────────────
#
# WORK_CONDITIONS says which positions on the WEAR SCALE cost money. This
# is the same statement for choice items, and it is deliberately the same
# shape: declared once, beside the thing it describes, in the module that
# owns it. It is NOT a per-item map, because a per-item map is twenty-one
# entries that each have to be remembered when an item is added, and
# forgetting one is exactly how a missing smoke alarm stopped reaching a
# capital budget.
#
# IT CANNOT BE A GLOBAL SET OF VALUES, AND THAT IS NOT A STYLE CHOICE
#
# Two values mean OPPOSITE things depending on which set they belong to:
#
#   present   work on `mold` (there is mold), no work on the eight
#             PRESENCE appliances (the appliance is there)
#   none      work on `egress_window` ("No egress window"), no work on
#             `mold`, `pest_evidence` and `visible_leaks` ("None seen")
#
# So WORK_OPTIONS = {"missing", "absent", "present", ...} would be wrong
# on at least four items. The set a value belongs to is what gives it its
# meaning, so the set is what carries the answer.
#
# Keyed by the option tuple itself. Tuples hash by value, so every item
# sharing a set inherits one answer and cannot drift from its siblings --
# PRESENCE alone covers eight appliances plus two bank items.
#
# ALARM_STATES and EQUIPMENT_STATES are EQUAL TUPLES, so they collapse to
# one entry here. That is correct today -- working/missing/replace means
# the same for an alarm and for a water heater -- but it means the two
# can never be given different answers while their values match. If they
# ever need to differ, they have to stop being equal first.
#
# A set that is absent from this table is a FAILURE, not a default of "no
# work": tests/test_work_options.py requires every option set reachable
# from the catalogue or the bank to appear here. Silence is how the last
# gap was built.
WORK_OPTIONS: dict[tuple[tuple[str, str], ...], frozenset[str]] = {
    # A hookup with no machine in it needs a machine bought; nothing
    # there needs the same. Both are work; `present` defers to the
    # condition recorded beside it.
    PRESENCE: frozenset({"hookup_only", "absent"}),
    # ALARM_STATES == EQUIPMENT_STATES, so this is one entry covering
    # smoke_alarm, smoke_alarm_unit, co_alarm, water_heater and hvac.
    ALARM_STATES: frozenset({"missing", "replace"}),
    # An outlet that is present and does not trip is the dangerous case,
    # and it costs the same electrician as one that is not there at all.
    GFCI_STATES: frozenset({"not_working", "absent"}),
    # A restricted egress window is a code problem with a cost; no egress
    # window at all is a bigger one. `compliant` is the only clean state.
    EGRESS_STATES: frozenset({"restricted", "none"}),
    # Staining is evidence of a leak that has happened. Both need a
    # plumber; neither is priced yet, which is a separate question.
    LEAK_STATES: frozenset({"minor", "active"}),
    # An expired inspection is work -- somebody has to service the
    # extinguisher -- and a missing one is work twice over.
    EXTINGUISHER_STATES: frozenset({"expired", "missing"}),
    # "Suspected" is work: it triggers a specialist. That is the whole
    # reason the third state exists rather than a yes/no.
    MOLD_STATES: frozenset({"suspected", "present"}),
    # Evidence of any kind means a treatment. Species does not change
    # that, which is why PEST_TYPE below is empty.
    PEST_EVIDENCE: frozenset({"droppings", "live", "damage"}),
    # Hookups that are incomplete need finishing; none at all need
    # installing. `complete` is the only state with nothing to do.
    WD_HOOKUP_STATES: frozenset({"partial", "absent"}),
    # ── Sets where NO value implies work, stated rather than omitted ────
    # Both are NOT_A_COST_ITEM in the reference table. They record what a
    # thing IS, and no answer to "what is it" is by itself a repair. They
    # are present with an empty set so the completeness test can tell
    # "considered, and the answer is none" from "nobody looked".
    FLOORING_TYPES: frozenset(),
    PEST_TYPE: frozenset(),
}


def needs_work(item: dict[str, Any] | None, condition: Any,
               detail: Any) -> bool:
    """True when this finding records something that costs money.

    THE ONE DEFINITION. site_dd.capex_export() filters on this, and the
    capture screen opens its cost box on it, so the form and the budget
    cannot disagree about what counts -- which is the disagreement that
    produced the gap this function closes.

    Three ways a finding can be work, in order:

    1. Its CONDITION is repair or replace. Unchanged, and the only rule
       there was before this.
    2. Its DETAIL is a work-implying value for this item's option set.
       A missing smoke alarm, a dead GFCI, a range that is not there.
    3. Its DETAIL is itself a work condition string.

    Rule 3 is belt and braces and it earns its place. ALARM_STATES
    carries the literal value `replace` -- a string that IS in
    WORK_CONDITIONS -- in the `detail` column, and the old filter read
    `condition`, so "Needs replacing" on a smoke alarm was discarded by a
    filter that would have admitted the identical string one column over.
    Rule 2 already catches that particular case; rule 3 means no future
    option set can reintroduce it by being added and not registered.
    Checked: no option set anywhere uses `repair` or `replace` to mean
    anything other than work, so this cannot fire wrongly.

    `item` may be None -- a stale key from an older checklist, or a
    property-scope finding whose catalogue lives elsewhere. Then only
    rules 1 and 3 apply, which is the same tolerance is_known_item()
    already gives an unrecognised key: ignore what cannot be resolved
    rather than guessing at it.
    """
    if cond.needs_work(condition):
        return True
    if detail in cond.WORK_CONDITIONS:
        return True
    if not item:
        return False
    return detail in WORK_OPTIONS.get(tuple(item.get("options") or ()),
                                      frozenset())


# ── Items in every room ──────────────────────────────────────────────────

EVERY_ROOM = (
    # Added from the v7 form. Per room, because mold and pests are found
    # in a place, and "which room" is the first thing anyone asks.
    _item("mold", "Mold", KIND_CHOICE, MOLD_STATES, with_condition=False,
          hint="Suspected is a real answer. It triggers a specialist, not a work order."),
    _item("pest_evidence", "Pest evidence", KIND_CHOICE, PEST_EVIDENCE,
          with_condition=False),
    _item("pest_type", "Pest type", KIND_CHOICE, PEST_TYPE,
          with_condition=False,
          hint="Only if there is evidence above."),
    _item("flooring_type", "Flooring type", KIND_CHOICE, FLOORING_TYPES,
          hint="What it is — separate from what condition it is in.",
          with_condition=False),
    _item("flooring", "Flooring condition"),
    _item("walls_ceiling", "Walls & ceiling"),
    _item("windows", "Windows"),
    _item("outlets_switches", "Outlets & switches"),
    _item("lighting", "Lighting"),
)

KITCHEN = (
    _item("appliance_range", "Range / oven", KIND_CHOICE, PRESENCE),
    _item("appliance_fridge", "Refrigerator", KIND_CHOICE, PRESENCE),
    _item("appliance_dishwasher", "Dishwasher", KIND_CHOICE, PRESENCE),
    _item("appliance_microwave", "Microwave", KIND_CHOICE, PRESENCE),
    _item("appliance_disposal", "Garbage disposal", KIND_CHOICE, PRESENCE),
    _item("cabinets", "Cabinets"),
    _item("countertops", "Countertops"),
    _item("sink_faucet", "Sink & faucet"),
    _item("gfci", "GFCI outlets", KIND_CHOICE, GFCI_STATES,
          hint="Required within reach of the sink.", with_condition=False),
)

BATHROOM = (
    _item("tub_shower", "Tub / shower & surround"),
    _item("toilet", "Toilet"),
    _item("vanity_sink", "Vanity & sink"),
    _item("exhaust_fan", "Exhaust fan", KIND_CHOICE, PRESENCE),
    _item("gfci", "GFCI outlets", KIND_CHOICE, GFCI_STATES, with_condition=False),
    _item("visible_leaks", "Visible leaks", KIND_CHOICE, LEAK_STATES,
          with_condition=False),
)

BEDROOM = (
    _item("closet", "Closet"),
    _item("egress_window", "Egress window", KIND_CHOICE, EGRESS_STATES,
          with_condition=False),
    _item("smoke_alarm", "Smoke alarm", KIND_CHOICE, ALARM_STATES,
          with_condition=False),
)

LAUNDRY = (
    _item("washer", "Washer", KIND_CHOICE, PRESENCE),
    _item("dryer", "Dryer", KIND_CHOICE, PRESENCE),
    _item("dryer_vent", "Dryer vent"),
)

# Keyed by room_type. A room type with no extras gets EVERY_ROOM alone.
ROOM_EXTRAS: dict[str, tuple[dict[str, Any], ...]] = {
    "kitchen": KITCHEN,
    "bathroom": BATHROOM,
    "bedroom": BEDROOM,
    "laundry": LAUNDRY,
}

ROOM_TYPES = (
    ("kitchen", "Kitchen"),
    ("living", "Living room"),
    ("dining", "Dining"),
    ("bedroom", "Bedroom"),
    ("bathroom", "Bathroom"),
    ("hallway", "Hallway"),
    ("laundry", "Laundry"),
    ("entry", "Entry"),
    ("other", "Other"),
)
ROOM_TYPE_LABELS = dict(ROOM_TYPES)


# ── Unit-wide items ──────────────────────────────────────────────────────
#
# Recorded once per unit, not per room: they belong to the dwelling rather
# than to any one space. Stored with area_id set and room_id NULL, which
# is the same shape the property scope uses one level up.

UNIT_WIDE = (
    _item("smoke_alarm_unit", "Smoke alarm (unit)", KIND_CHOICE, ALARM_STATES,
          with_condition=False),
    _item("co_alarm", "CO alarm", KIND_CHOICE, ALARM_STATES, with_condition=False),
    _item("water_heater", "Water heater", KIND_CHOICE, EQUIPMENT_STATES),
    _item("water_heater_gal", "Water heater capacity", KIND_NUMBER, measure="gal",
          hint="Photograph the data plate if you are unsure."),
    _item("water_heater_age", "Water heater age", KIND_NUMBER, measure="yr"),
    _item("hvac", "HVAC", KIND_CHOICE, EQUIPMENT_STATES),
    _item("hvac_age", "HVAC age", KIND_NUMBER, measure="yr"),
    _item("entry_door", "Entry door & lock"),
    # Added from the v7 form.
    # A thermostat wears and gets replaced, so it takes the condition
    # scale rather than a presence set.
    _item("thermostat", "Thermostat"),
    _item("fire_extinguisher", "Fire extinguisher", KIND_CHOICE,
          EXTINGUISHER_STATES, with_condition=False,
          hint="Check the inspection tag date, not the gauge."),
)


def items_for_room(room_type: str) -> tuple[dict[str, Any], ...]:
    """Every item to inspect in a room of this type, in walk order:
    the shared items first, then whatever the type adds."""
    return EVERY_ROOM + ROOM_EXTRAS.get(room_type, ())


def items_for_unit() -> tuple[dict[str, Any], ...]:
    return UNIT_WIDE


def option_label(item: dict[str, Any], value: Any) -> str | None:
    for key, label in item.get("options", ()):
        if key == value:
            return label
    return None


def is_valid_option(item: dict[str, Any], value: Any) -> bool:
    return any(key == value for key, _ in item.get("options", ()))


def is_known_item(item_key: str) -> bool:
    """True for any key this module defines, in any room type or unit-wide.
    Used to validate a capture's item_key without the caller needing to
    know which room it came from."""
    if any(i["key"] == item_key for i in UNIT_WIDE):
        return True
    for room_type, _ in ROOM_TYPES:
        if any(i["key"] == item_key for i in items_for_room(room_type)):
            return True
    return False


def item_map(items) -> dict[str, dict[str, Any]]:
    return {i["key"]: i for i in items}


def summarize_unit(findings_by_room: dict[Any, dict[str, Any]],
                   rooms: list[dict[str, Any]],
                   unit_findings: dict[str, Any] | None = None,
                   added_by_room: dict[Any, list[dict[str, Any]]] | None = None,
                   added_unit: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Roll a whole unit up: every room plus the unit-wide items.

    `findings_by_room` maps room_id -> {item_key: [condition per instance]}.
    Counts come from tools/site_dd_conditions, so a unit summary and the
    property summary are produced by the same code and cannot drift apart.

    Instances count independently, and the denominator counts them too --
    see site_dd_conditions.summarize for why dividing by the catalogue
    size stops being right once an item can occur twice.

    Only conditions are counted. A choice like "Hookup only" is a fact
    about the unit, not a rating, and totalling it alongside wear states
    would produce a number that means nothing.

    ADDED ITEMS COUNT LIKE ANY OTHER

    `added_by_room` and `added_unit` carry the item-bank picks and the
    freeform entries recorded on this unit, already shaped like checklist
    items. They are passed in rather than inferred from unrecognised keys
    in the answers, so the tolerance that lets a stale key from an older
    checklist be ignored is preserved -- an unknown key is still skipped,
    and an added item is counted because the caller said it exists.

    A fireplace added to a living room raises the denominator by one, the
    same way a second sink does. Adding something and then never
    assessing it should make the unit look less complete, because it is.
    """
    counts = {c: 0 for c in cond.CONDITIONS}
    assessed = 0
    total = 0
    room_rows = []

    added_rooms = added_by_room or {}
    for room in rooms:
        items = tuple(items_for_room(room["room_type"])) +             tuple(added_rooms.get(room["id"], ()))
        condition_keys = [i["key"] for i in items if i["with_condition"]]
        answers = findings_by_room.get(room["id"], {}) or {}
        room_counts = {c: 0 for c in cond.CONDITIONS}
        room_total = 0
        for key in condition_keys:
            values = cond.as_instances(answers.get(key))
            room_total += max(len(values), 1)
            total += max(len(values), 1)
            for value in values:
                if cond.is_valid(value):
                    assessed += 1
                    counts[value] += 1
                    room_counts[value] += 1
        worst = None
        for c in reversed(cond.CONDITIONS):
            if room_counts[c]:
                worst = c
                break
        room_rows.append({
            "room": room,
            "counts": room_counts,
            "assessed_count": sum(room_counts.values()),
            "item_count": room_total,
            "work_count": sum(room_counts[c] for c in cond.WORK_CONDITIONS),
            "worst": worst,
            "worst_label": cond.label(worst) if worst else None,
        })

    unit_answers = unit_findings or {}
    for item in tuple(UNIT_WIDE) + tuple(added_unit or ()):
        if not item["with_condition"]:
            continue
        values = cond.as_instances(unit_answers.get(item["key"]))
        total += max(len(values), 1)
        for value in values:
            if cond.is_valid(value):
                assessed += 1
                counts[value] += 1

    worst_overall = None
    for c in reversed(cond.CONDITIONS):
        if counts[c]:
            worst_overall = c
            break

    return {
        "counts": counts,
        "ordered_counts": [
            {"key": c, "label": cond.CONDITION_LABELS[c], "count": counts[c],
             "colour": cond.CONDITION_COLOURS[c], "is_work": c in cond.WORK_CONDITIONS}
            for c in reversed(cond.CONDITIONS)
        ],
        "work_count": sum(counts[c] for c in cond.WORK_CONDITIONS),
        "repair_count": counts[cond.REPAIR],
        "replace_count": counts[cond.REPLACE],
        "assessed_count": assessed,
        "total_items": total,
        "not_assessed_count": total - assessed,
        "completion_pct": (assessed / total * 100) if total else 0.0,
        "worst": worst_overall,
        "worst_label": cond.label(worst_overall) if worst_overall else None,
        "rooms": room_rows,
    }
