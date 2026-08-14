"""
FIRE Capital Tools - Site DD item bank.

The curated list of things a property might have that the fixed checklist
deliberately does not ask about. Pure: no Flask, no database, no I/O.

WHY A BANK AND NOT A LONGER CHECKLIST

The room checklists are answered in full, every room, every time. Every
item added to them is a question an inspector must look at and dismiss in
a unit that does not have one. Six extra items across nine room types is
not six extra questions, it is hundreds -- and the cost lands on the
common case to serve the rare one.

So the rare ones live here instead. Nothing in this file is asked
unprompted; an inspector standing in front of a fireplace adds it, and
the unit next door with no fireplace is never asked about one. The
checklist stays the floor of what must be looked at, and the bank is
what a specific building happens to have.

WHY THE BANK IS CODE AND NOT A TABLE THE UI EDITS

It is seeded into site_dd_bank_items on every connection, from this file,
which is the source of truth. A user-editable bank would need its own
screen, its own permissions and its own answer to what happens to
existing findings when an entry is renamed or deleted -- and the value of
the bank is that its keys are stable enough for Branch 4 to price
against. Confirmed as code-only for now; the table exists so that
decision can be revisited without a migration.

THE FREEFORM FALLBACK IS NOT A LESSER PATH

An inspector who types "koi pond" gets a real finding row, with a
condition, a note, photos and a place in the roll-up -- identical in
every respect to a curated pick except that bank_item_key is NULL. That
NULL is the whole difference, and it means exactly one thing: nobody has
mapped this to a capex category, so Branch 4 cannot price it
automatically. It is a missing reference, not a second-class record.
"""

from __future__ import annotations

import re
from typing import Any

from tools import site_dd_checklist as cl
from tools import site_dd_unit_checklist as uc

SCOPE_UNIT = "unit"
SCOPE_ROOM = "room"
SCOPE_BOTH = "both"

# A freeform item's key is derived from what was typed, under this prefix.
# The prefix is what makes "is this from the bank" answerable from the key
# alone, without a join, in the report and the export.
CUSTOM_PREFIX = "custom_"
MAX_CUSTOM_LABEL = 80
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _bank(key: str, label: str, scope: str, category: str,
          room_types: tuple[str, ...] | None = None,
          kind: str = uc.KIND_CONDITION,
          options: tuple[tuple[str, str], ...] | None = None,
          hint: str | None = None) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "scope": scope,
        # None means "any room type". A tuple narrows it, so a walk-in
        # closet is not offered in a kitchen.
        "room_types": room_types,
        "category": category,
        "default_kind": kind,
        "options": options or (),
        "hint": hint,
    }


# ── The starter bank ─────────────────────────────────────────────────────
#
# Twenty items, confirmed. Chosen as the things that turned up repeatedly
# in Michelle's notes and in the checklist's own gaps: present often
# enough to be worth curating, rare enough that putting them on every
# room's list would be a tax on every other unit.
#
# Order here IS display order within a category.

BANK_ITEMS: tuple[dict[str, Any], ...] = (
    # Interior
    _bank("fireplace", "Fireplace", SCOPE_ROOM, "interior_units",
          ("living", "dining", "bedroom", "other"),
          hint="Note gas or wood, and whether the flue was checked."),
    _bank("wet_bar", "Wet bar", SCOPE_ROOM, "interior_units",
          ("living", "dining", "kitchen", "other")),
    _bank("walk_in_closet", "Walk-in closet", SCOPE_ROOM, "interior_units",
          ("bedroom", "other")),
    _bank("pantry", "Pantry", SCOPE_ROOM, "interior_units",
          ("kitchen", "dining", "other")),
    _bank("linen_closet", "Linen closet", SCOPE_ROOM, "interior_units",
          ("bathroom", "hallway", "bedroom", "other")),
    _bank("half_bath", "Extra half-bath", SCOPE_UNIT, "interior_units"),
    _bank("storage_locker", "Storage unit / locker", SCOPE_UNIT, "interior_units"),

    # Envelope and site
    _bank("skylight", "Skylight", SCOPE_ROOM, "structural_envelope", None,
          hint="Check the surround for staining as well as the glazing."),
    _bank("balcony_patio", "Balcony / patio", SCOPE_BOTH, "site_exterior",
          ("living", "bedroom", "other")),
    _bank("garage_carport", "Attached garage / carport", SCOPE_UNIT, "site_exterior"),

    # Mechanical, electrical, plumbing
    _bank("washer_dryer", "In-unit washer / dryer", SCOPE_UNIT, "mep",
          None, uc.KIND_CHOICE, uc.PRESENCE),
    _bank("wd_hookups", "W/D hookups only", SCOPE_UNIT, "mep", None,
          uc.KIND_CHOICE,
          (("complete", "Drain, vent and power"),
           ("partial", "Incomplete"),
           ("absent", "None")),
          hint="The hookups themselves, where no machines are installed."),
    _bank("ceiling_fan", "Ceiling fan", SCOPE_ROOM, "mep"),
    _bank("window_ac", "Window AC unit", SCOPE_ROOM, "mep"),
    _bank("baseboard_heater", "Baseboard / wall heater", SCOPE_ROOM, "mep"),
    _bank("disposal", "Garbage disposal", SCOPE_ROOM, "mep",
          ("laundry", "other"), uc.KIND_CHOICE, uc.PRESENCE,
          hint="The kitchen checklist already asks about its own."),
    _bank("water_softener", "Water softener", SCOPE_UNIT, "mep"),
    _bank("sump_pump", "Sump pump", SCOPE_UNIT, "mep"),
    _bank("tankless_water_heater", "Tankless water heater", SCOPE_UNIT, "mep"),

    # Life safety
    _bank("security_screen_door", "Security screen door", SCOPE_BOTH, "life_safety",
          ("entry", "other")),
)

BANK_BY_KEY = {b["key"]: b for b in BANK_ITEMS}
BANK_KEYS = tuple(b["key"] for b in BANK_ITEMS)

# Display order for the picker's category headings, following the
# property checklist's own order so the two read the same way.
CATEGORY_ORDER = tuple(cat["key"] for cat in cl.CATEGORIES)


def get(key: str) -> dict[str, Any] | None:
    return BANK_BY_KEY.get(key)


def is_bank_item(key: str) -> bool:
    return key in BANK_BY_KEY


def is_custom_key(key: str) -> bool:
    return bool(key) and key.startswith(CUSTOM_PREFIX)


def custom_key(label: str) -> str:
    """A stable key for a freeform item, derived from what was typed.

    Deriving rather than counting means the same words typed in two rooms
    produce the same key, so "koi pond" is one thing across an assessment
    and not koi_pond_1 and koi_pond_4. Two DIFFERENT things that slug the
    same way collide into instances of one item, which is the same
    behaviour as adding a second sink and reads correctly on the page.
    """
    slug = _SLUG_RE.sub("_", (label or "").strip().lower()).strip("_")
    return CUSTOM_PREFIX + (slug[:48].strip("_") or "item")


def clean_label(label: str) -> str:
    return " ".join((label or "").split())[:MAX_CUSTOM_LABEL]


def as_item(key: str, label: str | None = None) -> dict[str, Any]:
    """Shape a bank pick or a freeform entry like a checklist item.

    Everything downstream -- the form loop, _collect, the roll-up -- takes
    checklist-shaped dicts. Added items go through the same code paths
    rather than a parallel set, which is the reason a bank item gets
    instances, photos and validation without any of it being written
    twice.
    """
    entry = BANK_BY_KEY.get(key)
    if entry:
        item = uc.make_item(entry["key"], entry["label"], entry["default_kind"],
                        entry["options"] or None, hint=entry["hint"])
        item["bank_item_key"] = entry["key"]
        item["category"] = entry["category"]
    else:
        item = uc.make_item(key, clean_label(label) or "Item", uc.KIND_CONDITION)
        item["bank_item_key"] = None
        item["category"] = None
    item["added"] = True
    return item


def for_scope(scope: str, room_type: str | None = None,
              exclude_labels: set[str] | None = None) -> list[dict[str, Any]]:
    """What the picker offers here, already filtered.

    Two filters, both about not wasting an inspector's attention: an item
    that does not belong at this scope is not shown, and neither is one
    the checklist for this exact room already asks about -- offering
    "Garbage disposal" in a kitchen that has it on the list produces two
    questions about one appliance.
    """
    lowered = {label.strip().lower() for label in (exclude_labels or set())}
    out = []
    for entry in BANK_ITEMS:
        if entry["scope"] not in (scope, SCOPE_BOTH):
            continue
        if scope == SCOPE_ROOM and entry["room_types"] is not None:
            if room_type not in entry["room_types"]:
                continue
        if entry["label"].strip().lower() in lowered:
            continue
        out.append(entry)
    return out


def grouped_for_scope(scope: str, room_type: str | None = None,
                      exclude_labels: set[str] | None = None
                      ) -> list[dict[str, Any]]:
    """The same list, in category groups, for the picker's headings."""
    items = for_scope(scope, room_type, exclude_labels)
    groups = []
    for cat_key in CATEGORY_ORDER:
        members = [i for i in items if i["category"] == cat_key]
        if members:
            groups.append({
                "key": cat_key,
                "name": cl.CATEGORY_NAMES.get(cat_key, cat_key),
                "items": members,
            })
    return groups


def search(query: str, scope: str, room_type: str | None = None,
           exclude_labels: set[str] | None = None) -> list[dict[str, Any]]:
    """Substring match on the label. Twenty items do not need an index."""
    q = (query or "").strip().lower()
    items = for_scope(scope, room_type, exclude_labels)
    if not q:
        return items
    return [i for i in items if q in i["label"].lower()]


# Bumped whenever BANK_ITEMS changes. The database copy is reseeded when
# its row count for this version does not match, which is one cheap COUNT
# per connection rather than twenty upserts.
BANK_VERSION = 1
