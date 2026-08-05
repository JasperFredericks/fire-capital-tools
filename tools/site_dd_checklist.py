"""
FIRE Capital Tools - Site DD checklist definition and scoring.

The 32-item inspection checklist and every number derived from it. Pure:
no Flask, no database, no I/O -- item definitions in, scores out -- so the
roll-up can be unit-tested directly (tests/test_site_dd_scoring.py). Same
principle as tools/deal_analyzer_math.py, and for the same reason: a wrong
score here is silently plausible in a way a wrong page layout is not.

The checklist is fixed for v1. Editable templates were considered and
deliberately deferred, because the real cost is not the editing UI but
template versioning: change the item list and every past assessment either
re-renders against items it never had, or needs a frozen copy of the list
it was taken against.

Two things make a future v2 possible without corrupting v1 records:

  * CHECKLIST_VERSION is stamped onto each assessment when it is created.
  * Items are addressed by stable string keys ("roof_covering"), never by
    position. Reordering or inserting items cannot silently reassign an
    existing response to a different question.

The escape hatch for "my item isn't on the list" is a freeform
observations note per category, which costs nothing and avoids
configurability nobody asked for yet.
"""

from __future__ import annotations

from typing import Any

CHECKLIST_VERSION = 1

# Score meanings, shown in the UI next to each row so the scale isn't
# guesswork. N/A is represented as a stored NULL, not a sixth value --
# "doesn't apply" is the absence of a score, and averaging it as a zero (or
# as a three) would quietly distort every roll-up above it.
SCORE_LABELS = {
    5: "Excellent — new or near-new",
    4: "Good — normal wear",
    3: "Fair — monitor",
    2: "Poor — repair needed",
    1: "Critical — immediate attention",
}

# An item scored at or below this is a critical finding. Surfaced as a
# separate count rather than folded into the average as a weight: a
# weighted score moves for reasons nobody can reconstruct by eye, whereas
# "3.9 overall, 2 critical" states both facts plainly.
CRITICAL_THRESHOLD = 2

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

# Contiguous, no gaps. "Not assessed" is deliberately its own band rather
# than being folded into High: an assessment with nothing scored yet is an
# absence of information, not evidence of a bad building, and colouring it
# red would misrepresent a blank form.
RISK_BANDS = (
    (4.50, "Low"),
    (3.50, "Moderate"),
    (2.50, "Elevated"),
    (0.00, "High"),
)
NOT_ASSESSED = "Not assessed"


def valid_score(value: Any) -> bool:
    """True only for a genuine 1-5 integer.

    bool is excluded explicitly because it subclasses int in Python, and
    True == 1 -- so without this guard a stray boolean would be silently
    accepted as a score of 1, the most severe rating on the scale. A
    checkbox or JSON value leaking in would turn a healthy item into a
    critical finding with nothing on screen to indicate why."""
    if isinstance(value, bool):
        return False
    return isinstance(value, int) and value in SCORE_LABELS


def risk_band(overall: float | None) -> str:
    if overall is None:
        return NOT_ASSESSED
    for threshold, label in RISK_BANDS:
        if overall >= threshold:
            return label
    return "High"


def score_assessment(items: dict[str, Any]) -> dict[str, Any]:
    """Roll item-level scores up into category, overall and completion
    figures.

    `items` maps item_key -> score, where a score is 1-5 or None for N/A.
    Unknown keys are ignored rather than raising, so a stale key left over
    from a future checklist revision can never break the scoring of an
    assessment that is otherwise fine.

    Overall is the mean of *all* non-N/A item scores (item-weighted), not
    the mean of the category means. With near-equal category sizes the two
    barely differ, and "the average of everything you scored" is a sentence
    that can be verified by eye -- which matters more here than the
    marginal fairness of equal category weighting.
    """
    scored: dict[str, int] = {
        k: v for k, v in items.items() if k in ITEM_LABELS and valid_score(v)
    }

    categories = []
    for cat in CATEGORIES:
        keys = [k for k, _ in cat["items"]]
        values = [scored[k] for k in keys if k in scored]
        categories.append({
            "key": cat["key"],
            "name": cat["name"],
            "score": (sum(values) / len(values)) if values else None,
            "scored_count": len(values),
            "item_count": len(keys),
            "critical_count": sum(1 for v in values if v <= CRITICAL_THRESHOLD),
        })

    all_values = list(scored.values())
    overall = (sum(all_values) / len(all_values)) if all_values else None

    return {
        "overall": overall,
        "risk_band": risk_band(overall),
        "critical_count": sum(1 for v in all_values if v <= CRITICAL_THRESHOLD),
        "critical_items": sorted(
            (k for k, v in scored.items() if v <= CRITICAL_THRESHOLD),
            key=lambda k: ITEM_KEYS.index(k),
        ),
        "scored_count": len(all_values),
        "total_items": TOTAL_ITEMS,
        "completion_pct": (len(all_values) / TOTAL_ITEMS) * 100 if TOTAL_ITEMS else 0.0,
        "na_count": sum(
            1 for k in ITEM_KEYS if k in items and not valid_score(items.get(k))
        ),
        "categories": categories,
    }
