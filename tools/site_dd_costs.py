"""
FIRE Capital Tools - Site DD cost provenance.

Where a repair estimate came from, and the mapping from findings to
Underwriting's capex budget. Pure: no Flask, no database, no I/O.

THREE SOURCES, AND ONLY ONE OF THEM EXISTS YET

  reference   from a cost table. NOTHING WRITES THIS. The reference
              costs themselves are still gated on the decision between
              Michelle's numbers, RSMeans, and disclaimed placeholders --
              and shipping a column that a later branch fills is very
              different from shipping numbers nobody has agreed to.
  manual      an inspector typed it, standing in the room. The only
              source this branch produces.
  none        no estimate. The default, and not a failure state: most
              findings never need a number.

The column exists now, before anything fills it, for the same reason the
capex source/source_ref hook was built before Site DD did: a provenance
column added after the fact cannot describe the rows already written.
Every row this branch writes says 'manual' truthfully, and when reference
costs land they will not have to guess which of the existing estimates
came from a person.

WHY A MANUAL ESTIMATE IS LABELLED EVERY TIME IT IS SHOWN

An inspector's guess at a water heater and a figure from a cost database
look identical once they are both a number in a column. One is a
tradesman's judgement from ten feet away; the other is a priced
line item. Presenting them the same way would let the first quietly
acquire the authority of the second, which is exactly the failure the
disclaimer discipline in tools/deal_readiness_defaults.py exists to
prevent -- so the same rule applies here, enforced by a test.
"""

from __future__ import annotations

from typing import Any

from tools import site_dd_checklist as cl

SOURCE_REFERENCE = "reference"
SOURCE_MANUAL = "manual"
SOURCE_NONE = "none"

SOURCES = (SOURCE_REFERENCE, SOURCE_MANUAL, SOURCE_NONE)

# Every label for a non-empty source must contain this phrase, so a later
# edit cannot soften "inspector's estimate" into "estimate" and let a
# guess pass for a priced line. A test asserts it.
REQUIRED_PROVENANCE_PHRASE = "not from a cost table"

SOURCE_LABELS = {
    SOURCE_MANUAL: "Inspector estimate — not from a cost table",
    # A researched national average, and labelled as one. It is a
    # starting point for a budget, not a bid, and saying so is what stops
    # a plausible-looking figure being read as a quote.
    SOURCE_REFERENCE: (
        "Researched market average — a national figure, not a quote for "
        "this building"
    ),
    SOURCE_NONE: "",
}

SOURCE_SHORT = {
    SOURCE_MANUAL: "Inspector estimate",
    SOURCE_REFERENCE: "Reference cost",
    SOURCE_NONE: "",
}

# A cost above this is almost certainly a typo -- a mistyped unit cost of
# 350000 for a faucet would swamp a capex budget silently. Rejected at
# the edge rather than stored and explained later.
MAX_UNIT_COST = 1_000_000.0


def is_valid_source(value: Any) -> bool:
    return value in SOURCES


def normalize_source(value: Any) -> str:
    """Anything unrecognised, including NULL, reads as 'none'.

    Rows written before this column existed have NULL in it, and they
    genuinely have no estimate, so NULL and 'none' mean the same thing.
    Collapsing them here means no caller has to remember that.
    """
    return value if value in SOURCES else SOURCE_NONE


def clean_cost(value: Any) -> float | None:
    """A cost, or None. Negative and absurd values are None, not errors.

    A negative repair cost is not a discount, it is a typo, and storing
    it would subtract from a capex budget.
    """
    if value is None or value == "":
        return None
    try:
        cost = float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None
    if cost <= 0 or cost > MAX_UNIT_COST:
        return None
    return cost


def reference_for(finding: dict[str, Any],
                  flooring_type: str | None = None) -> Any:
    """The researched reference cost for a finding, or None.

    The ONLY route by which est_cost_source can become 'reference'. A
    figure that is not in site_dd_reference_costs cannot become one at
    render time, by construction: nothing else in this codebase writes
    that value, and a test asserts it.
    """
    from tools import site_dd_reference_costs as refcosts

    key = finding.get("bank_item_key") or finding.get("item_key")
    return refcosts.for_item(key, flooring_type)


def apply_reference(finding: dict[str, Any],
                    flooring_type: str | None = None) -> dict[str, Any]:
    """Fill in a reference cost, WITHOUT ever overwriting a person.

    An inspector standing in the room beats a national average, always.
    So a finding that already carries a manual estimate is returned
    untouched -- the reference figure is not "better information", it is
    a default for the rows nobody has priced.
    """
    if normalize_source(finding.get("est_cost_source")) == SOURCE_MANUAL:
        return finding
    ref = reference_for(finding, flooring_type)
    if ref is None:
        return finding
    return {**finding,
            "est_unit_cost": ref.unit_cost,
            "est_cost_source": SOURCE_REFERENCE,
            "_reference": ref}


def source_for(cost: Any, previous: Any = None) -> str:
    """The provenance of a cost that has just been typed.

    A typed number is always 'manual' -- typing over a reference figure
    makes it the inspector's, not the table's. Clearing it returns to
    'none' rather than leaving a source pointing at nothing.
    """
    if clean_cost(cost) is None:
        return SOURCE_NONE
    return SOURCE_MANUAL


def label_for(source: Any) -> str:
    return SOURCE_LABELS.get(normalize_source(source), "")


def describe(row: Any) -> dict[str, Any]:
    """How one finding's estimate should be presented.

    Returns the figure, its provenance and the words that must accompany
    it, together, so a caller cannot render the number without the label
    by taking the convenient half.
    """
    cost = clean_cost((row or {}).get("est_unit_cost"))
    source = normalize_source((row or {}).get("est_cost_source"))
    if cost is None:
        source = SOURCE_NONE
    return {
        "cost": cost,
        "source": source,
        "label": SOURCE_LABELS.get(source, ""),
        "short": SOURCE_SHORT.get(source, ""),
        "is_estimate": source == SOURCE_MANUAL,
        "has_cost": cost is not None,
    }


# ── The hand-off to Underwriting's capex budget ──────────────────────────
#
# Branch 4 owns the export: which findings go, whether a contingency is
# added, what happens to a finding with no estimate. This is only the
# FIELD MAPPING, so the shape agreed in Phase 1 can be tested against the
# real underwriting_capex_lines table before anything depends on it.

CAPEX_SOURCE = "site_dd"

# underwriting_capex_lines.scope accepts 'exterior' or 'interior' ONLY,
# and silently rewrites anything else to 'interior'. Site DD's scopes are
# property/unit/room, so a mapping is required and was not part of the
# Phase 1 field list -- without it every roof and parking lot would land
# in the interior budget without complaint.
SCOPE_MAP = {
    "property": "exterior",
    "unit": "interior",
    "room": "interior",
}

# Property-scope findings that are plainly not exterior work. The scope
# alone is too coarse: a property-level furnace is not an exterior line.
_INTERIOR_CATEGORIES = {"interior_units", "mep", "life_safety"}


def capex_category(finding: dict[str, Any]) -> str | None:
    """The capex category, or None when the finding does not have one.

    Every scope now writes a real category: the property checklist, the
    room and unit checklists, and the item bank all draw on the same
    vocabulary. Room and unit rows used to hold the input KIND instead --
    'condition', 'choice', 'number' -- which the capex export emitted as
    budget headings. That was corrected at the write site, and existing
    rows were rewritten by site_dd_db._backfill_capex_categories.

    This filter stays regardless. It is what makes the export's output
    provably inside one vocabulary rather than merely expected to be:
    a database restored from an old backup, a row written by a build that
    predates the fix, or a future catalogue that forgets to map a new key
    all arrive here, and all become None rather than putting a
    meaningless heading into a capital budget. Meaningless is worse than
    blank, because it looks like a real grouping.
    """
    value = finding.get("category_key")
    return value if value in cl.CATEGORY_NAMES else None


def capex_scope(finding: dict[str, Any]) -> str:
    scope = SCOPE_MAP.get(finding.get("scope"), "interior")
    if scope == "exterior" and finding.get("category_key") in _INTERIOR_CATEGORIES:
        return "interior"
    return scope


def to_capex_lines(findings: list[dict[str, Any]],
                   labels: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Map findings onto underwriting_capex_lines rows.

    Grouped by (assessment scope, item), because quantity is the INSTANCE
    COUNT: two sinks needing replacement are one budget line of quantity
    2, not two lines that a reader has to add up.

    THE LABEL IS NOT JUST instance_label

    Phase 1 wrote "label <- instance label", which is right only for a
    freeform item. A curated bank pick leaves instance_label NULL, so
    taken literally every fireplace would arrive in the budget as
    "Item 1". The label falls back to the catalogue name, and a typed
    name wins over it when there is one.

    source_ref carries the FIRST finding's id. The column is TEXT and
    holds one reference, so a grouped line points at the row a reader
    should open to see the photographs -- not at all of them.
    """
    labels = labels or {}
    groups: dict[tuple, dict[str, Any]] = {}
    order: list[tuple] = []

    for f in findings or []:
        key = (f.get("area_id"), f.get("room_id"), f.get("item_key"))
        if key not in groups:
            groups[key] = {"rows": [], "first": f}
            order.append(key)
        groups[key]["rows"].append(f)

    lines = []
    for i, key in enumerate(order):
        rows = groups[key]["rows"]
        first = groups[key]["first"]
        item_key = first.get("item_key")
        typed = (first.get("instance_label") or "").strip()
        cost = None
        for r in rows:
            cost = clean_cost(r.get("est_unit_cost"))
            if cost is not None:
                break
        lines.append({
            "sort_order": i,
            "scope": capex_scope(first),
            "category": capex_category(first),
            "label": typed or labels.get(item_key) or item_key,
            "quantity": float(len(rows)),
            "unit_cost": cost,
            # Left to underwriting_capex.line_total, which multiplies
            # quantity by unit cost. Writing a total here as well would
            # create two numbers that can disagree.
            "total_cost": None,
            "is_contingency": 0,
            "source": CAPEX_SOURCE,
            "source_ref": str(first.get("id")) if first.get("id") is not None else None,
        })
    return lines
