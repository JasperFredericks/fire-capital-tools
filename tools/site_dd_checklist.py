"""
FIRE Capital Tools - Site DD property-scope checklist.

The 32-item property and common-area checklist. Pure: no Flask, no
database, no I/O.

RE-HOMED, NOT REBUILT

These are the same six categories and the same 32 items the first version
shipped with, under the same stable keys. They were always property-level
questions -- a roof, a parking lot and a fire-alarm panel belong to no
unit -- so the unit-by-unit rebuild does not replace them, it puts them at
the scope they already described. Branch 2 adds unit and room checklists
alongside; this list stays where it is.

WHAT CHANGED

Scoring left this module. Responses are now five-state conditions handled
by tools/site_dd_conditions.py, and the old 1-5 mean, its risk bands and
its critical-findings threshold are gone -- see that module's docstring
for why an ordinal scale must not be averaged.

Item keys are unchanged and deliberately so: a key is the stable identity
of a question, and renaming one to celebrate a rewrite would be the one
edit that could silently reassign an answer to a different question.
"""

from __future__ import annotations

from typing import Any

CHECKLIST_VERSION = 2

# The scope every item in this file belongs to. Stated explicitly so a
# reader of a finding row can tell a property question from a unit one
# without inferring it from a NULL room_id.
SCOPE = "property"

CATEGORIES = (
    {
        "key": "site_exterior",
        "name": "Site & Exterior",
        "items": (
            ("parking_paving", "Parking & paving"),
            ("drainage_grading", "Drainage & grading"),
            ("landscaping", "Landscaping"),
            ("exterior_lighting", "Exterior lighting"),
            ("signage_fencing", "Signage & fencing"),
        ),
    },
    {
        "key": "structural_envelope",
        "name": "Structural & Envelope",
        "items": (
            ("foundation", "Foundation"),
            ("framing_walls", "Framing & load-bearing walls"),
            ("roof_covering", "Roof covering"),
            ("roof_drainage", "Roof drainage & gutters"),
            ("windows_doors", "Windows & exterior doors"),
            ("facade_siding", "Façade & siding"),
        ),
    },
    {
        "key": "mep",
        "name": "Mechanical, Electrical & Plumbing",
        "items": (
            ("hvac_units", "HVAC units"),
            ("water_heaters", "Water heaters"),
            ("electrical_panels", "Electrical panels & wiring"),
            ("plumbing_supply", "Plumbing supply lines"),
            ("waste_sewer", "Waste & sewer lines"),
            ("ventilation", "Ventilation & exhaust"),
        ),
    },
    {
        "key": "life_safety",
        "name": "Life Safety",
        "items": (
            ("alarms_detectors", "Alarms & smoke detectors"),
            ("extinguishers_sprinklers", "Extinguishers & sprinklers"),
            ("egress_signage", "Egress routes & exit signage"),
            ("stairs_railings", "Stairs & railings"),
            ("security_lighting", "Security lighting"),
        ),
    },
    {
        "key": "interior_units",
        "name": "Interior & Units",
        "items": (
            ("flooring", "Flooring"),
            ("walls_ceilings", "Walls & ceilings"),
            ("kitchens", "Kitchens"),
            ("bathrooms", "Bathrooms"),
            ("unit_appliances", "Unit appliances"),
        ),
    },
    {
        "key": "access_environmental",
        "name": "Accessibility & Environmental",
        "items": (
            ("ada_parking_path", "ADA parking & path of travel"),
            ("ada_common_areas", "ADA common areas & restrooms"),
            ("moisture_mould", "Moisture & mould indicators"),
            ("pest_evidence", "Pest evidence"),
            ("hazmat_indicators", "Hazmat indicators (asbestos/lead-era)"),
        ),
    },
)

# Flat lookups built once at import. ITEM_KEYS is the authoritative set the
# routes validate submitted keys against, so a hand-crafted POST cannot
# insert a response to an item that does not exist.
ITEM_KEYS = tuple(k for cat in CATEGORIES for k, _ in cat["items"])
ITEM_LABELS = {k: label for cat in CATEGORIES for k, label in cat["items"]}
ITEM_CATEGORY = {k: cat["key"] for cat in CATEGORIES for k, _ in cat["items"]}
CATEGORY_NAMES = {cat["key"]: cat["name"] for cat in CATEGORIES}
TOTAL_ITEMS = len(ITEM_KEYS)

def item_category(item_key: str) -> str | None:
    """The category a key belongs to, or None for an unknown key."""
    return ITEM_CATEGORY.get(item_key)


def is_known(item_key: str) -> bool:
    return item_key in ITEM_LABELS
