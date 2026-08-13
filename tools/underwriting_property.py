"""
FIRE Capital Tools - Underwriting property information.

Unit count, occupancy and parking, resolved from two possible sources:
what the rent roll says, and what someone typed.

Pure: no Flask, no database, no I/O.

THE OVERRIDE RULE

Unit count and occupancy are already computed from the rent roll by
underwriting_math.build_egi(). Those derived figures are the default and
stay the default. An override does not replace the derived figure
quietly -- it replaces it *visibly*:

    no override            the derived figure is used, and it is the
                           only figure on screen
    override, agrees       the override is used; nothing to say
    override, disagrees    the override is used, AND both figures are
                           reported along with the gap

A silent overwrite is the failure this shape exists to prevent. A rent
roll saying 92 units and a form saying 88 is a real discrepancy about a
real property, and the tool's job is to surface it, not to pick a winner
and hide the question. Same reasoning as the T12 reconciliation gate in
quick_analyzer_t12: figures that disagree must say so.

Parking has no derived source at all -- no rent roll this app parses
carries it -- so it is plain entry with no override machinery.
"""

from __future__ import annotations

from typing import Any

# How far apart the derived and entered figures must be before the
# disagreement is worth reporting. Occupancy is a percentage derived from
# a unit count, so it carries rounding that a unit count does not.
UNIT_COUNT_TOLERANCE = 0        # exact: units are whole things
OCCUPANCY_TOLERANCE_PCT = 0.05  # half a tenth of a point


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> int | None:
    f = _f(value)
    return int(f) if f is not None else None


def resolve(scenario: dict[str, Any], egi: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve the property facts for display.

    `egi` is build_egi()'s output, which already carries unit_count and
    occupied_units. Passing it in rather than recomputing keeps a single
    source for the derived numbers -- two independent counts of the same
    rent roll is exactly how they would drift.
    """
    egi = egi or {}

    # A scenario with no rent roll gets unit_count 0 from build_egi, which
    # is a count of nothing rather than a property with no units. Treated
    # as absent so the card reads "not known yet" instead of asserting
    # "0 units — from the rent roll" about a building nobody has described.
    derived_units = _i(egi.get("unit_count")) or None
    occupied = _i(egi.get("occupied_units"))
    derived_occ = None
    if derived_units:
        derived_occ = (occupied or 0) / derived_units * 100.0

    unit_count = _field(
        derived=derived_units,
        override=_i(scenario.get("unit_count_override")),
        tolerance=UNIT_COUNT_TOLERANCE,
        label="Unit count",
        derived_source="the rent roll",
    )
    occupancy = _field(
        derived=derived_occ,
        override=_f(scenario.get("occupancy_pct_override")),
        tolerance=OCCUPANCY_TOLERANCE_PCT,
        label="Occupancy",
        derived_source="the rent roll",
        is_pct=True,
    )

    spaces = _i(scenario.get("parking_spaces"))
    per_unit = None
    if spaces is not None and unit_count["value"]:
        per_unit = spaces / unit_count["value"]

    return {
        "unit_count": unit_count,
        "occupancy": occupancy,
        "occupied_units": occupied,
        "parking_spaces": spaces,
        "parking_notes": (scenario.get("parking_notes") or "").strip() or None,
        "parking_per_unit": per_unit,
        "city": (scenario.get("city") or "").strip() or None,
        "state": (scenario.get("state") or "").strip().upper() or None,
        # True when anything at all is worth showing in the card, so the
        # page can stay quiet on a scenario that has none of it.
        "has_any": any([unit_count["value"], occupancy["value"], spaces,
                        (scenario.get("parking_notes") or "").strip(),
                        (scenario.get("city") or "").strip()]),
        "disagreements": [f for f in (unit_count, occupancy) if f["disagrees"]],
    }


def _field(derived, override, tolerance, label, derived_source, is_pct=False):
    """One resolved figure, carrying its own provenance.

    Never raises. A field with neither a derived nor an entered value
    returns value None with a reason, which the template renders as an em
    dash -- absent is a legitimate state, not an error.
    """
    used = override if override is not None else derived
    disagrees = False
    gap = None
    if override is not None and derived is not None:
        gap = override - derived
        disagrees = abs(gap) > tolerance

    if used is None:
        source = "none"
        note = f"{label} is not known — no rent roll imported and nothing entered."
    elif override is None:
        source = "derived"
        note = None
    elif not disagrees:
        source = "override_agrees"
        note = None
    else:
        source = "override_disagrees"
        fmt = (lambda v: f"{v:,.1f}%") if is_pct else (lambda v: f"{v:,.0f}")
        note = (f"Using the entered {label.lower()} of {fmt(override)}. "
                f"{derived_source.capitalize()} says {fmt(derived)} "
                f"({fmt(abs(gap))} {'higher' if gap > 0 else 'lower'}).")

    return {
        "value": used,
        "derived": derived,
        "override": override,
        "source": source,
        "disagrees": disagrees,
        "gap": gap,
        "note": note,
        "label": label,
    }
