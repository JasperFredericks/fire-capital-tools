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


def _item(key: str, label: str, kind: str = KIND_CONDITION,
          options: tuple[tuple[str, str], ...] | None = None,
          measure: str | None = None, hint: str | None = None,
          with_condition: bool = True) -> dict[str, Any]:
    return {
        "key": key, "label": label, "kind": kind,
        "options": options or (), "measure": measure, "hint": hint,
        # Whether a condition is offered alongside a choice. An appliance
        # that is present has a condition; an alarm that is missing does
        # not need one, because "missing" is the whole answer.
        "with_condition": with_condition if kind == KIND_CHOICE else (kind == KIND_CONDITION),
    }


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


# ── Items in every room ──────────────────────────────────────────────────

EVERY_ROOM = (
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
    _item("gfci", "GFCI outlets", KIND_CHOICE,
          (("present", "Present & working"), ("not_working", "Present, not working"),
           ("absent", "None")),
          hint="Required within reach of the sink.", with_condition=False),
)

BATHROOM = (
    _item("tub_shower", "Tub / shower & surround"),
    _item("toilet", "Toilet"),
    _item("vanity_sink", "Vanity & sink"),
    _item("exhaust_fan", "Exhaust fan", KIND_CHOICE, PRESENCE),
    _item("gfci", "GFCI outlets", KIND_CHOICE,
          (("present", "Present & working"), ("not_working", "Present, not working"),
           ("absent", "None")),
          with_condition=False),
    _item("visible_leaks", "Visible leaks", KIND_CHOICE,
          (("none", "None seen"), ("minor", "Minor / staining"), ("active", "Active leak")),
          with_condition=False),
)

BEDROOM = (
    _item("closet", "Closet"),
    _item("egress_window", "Egress window", KIND_CHOICE,
          (("compliant", "Opens, meets egress"), ("restricted", "Opens, restricted"),
           ("none", "No egress window")),
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


def item_map(items) -> dict[str, dict[str, Any]]:
    return {i["key"]: i for i in items}


def summarize_unit(findings_by_room: dict[Any, dict[str, Any]],
                   rooms: list[dict[str, Any]],
                   unit_findings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Roll a whole unit up: every room plus the unit-wide items.

    `findings_by_room` maps room_id -> {item_key: condition}. Counts come
    from tools/site_dd_conditions, so a unit summary and the property
    summary are produced by the same code and cannot drift apart.

    Only conditions are counted. A choice like "Hookup only" is a fact
    about the unit, not a rating, and totalling it alongside wear states
    would produce a number that means nothing.
    """
    counts = {c: 0 for c in cond.CONDITIONS}
    assessed = 0
    total = 0
    room_rows = []

    for room in rooms:
        items = items_for_room(room["room_type"])
        condition_keys = [i["key"] for i in items if i["with_condition"]]
        answers = findings_by_room.get(room["id"], {}) or {}
        room_counts = {c: 0 for c in cond.CONDITIONS}
        for key in condition_keys:
            total += 1
            value = answers.get(key)
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
            "item_count": len(condition_keys),
            "work_count": sum(room_counts[c] for c in cond.WORK_CONDITIONS),
            "worst": worst,
            "worst_label": cond.label(worst) if worst else None,
        })

    unit_answers = unit_findings or {}
    for item in UNIT_WIDE:
        if not item["with_condition"]:
            continue
        total += 1
        value = unit_answers.get(item["key"])
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
