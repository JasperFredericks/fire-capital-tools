"""
FIRE Capital Tools - Underwriting capex budget.

The forward capital plan: what you intend to spend improving the property
after closing, itemized exterior and interior, with a contingency, a
total and a per-unit figure, feeding cash to close.

Pure: no Flask, no database, no I/O. Inputs in, a result dict out.

WHY THIS IS NOT THE CAPEX ALREADY IN THE EXPENSE TABLE

underwriting_expense_lines already contains rows with line_kind 'capex'.
Those come out of the T12, classified by KPICalculator, and are HISTORICAL
-- money the seller already spent, excluded from operating expenses so it
is not charged against NOI. This module is the FORWARD budget: money you
have not spent yet.

Same word, opposite direction in time, and they must never be added
together. A test asserts that no function here reads the expense lines at
all, which is the only way to make that guarantee structural rather than
a matter of remembering.

HOW IT REACHES THE RETURNS

It does not touch the returns engine. underwriting_math already converts
itemized acquisition dollars into an equivalent percentage of the
purchase price and hands the engine that, precisely so
deal_analyzer_math.analyze_noi_series() never has to change. Capex rides
the same channel: effective_pct_of_price() converts the budget into the
same units, underwriting_math adds it to the acquisition percentage, and
the engine sees one number it already understood.

The consequence is deliberate and worth stating plainly: capex increases
equity invested, and therefore appears in the denominator of IRR, cash-on-
cash and equity multiple. That is correct -- capex is capital you put in
-- but it does mean adding a capex line changes a scenario's returns. A
scenario with no capex lines has an effective percentage of exactly zero
and is bit-identical to one computed before this module existed.
"""

from __future__ import annotations

from typing import Any

# The contingency default. A default, not a rule: a scenario can set its
# own percentage, including zero, and that is honoured.
DEFAULT_CONTINGENCY_PCT = 5.0

SCOPE_EXTERIOR = "exterior"
SCOPE_INTERIOR = "interior"
SCOPES = (SCOPE_EXTERIOR, SCOPE_INTERIOR)

SCOPE_LABELS = {SCOPE_EXTERIOR: "Exterior", SCOPE_INTERIOR: "Interior"}

# Where a line came from. 'manual' is everything today; 'site_dd' is the
# reserved value for rows Site DD's repair list will one day write. No
# code here treats them differently beyond reporting the split, which is
# the whole extent of the integration hook.
SOURCE_MANUAL = "manual"
SOURCE_SITE_DD = "site_dd"


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def line_total(line: dict[str, Any]) -> float:
    """The cost of one line.

    An explicit total wins over quantity x unit cost. Both are offered
    because real budgets carry both shapes -- "roof: $84,000" and "42
    units x $3,200" -- and forcing one would make the other a lie.
    """
    explicit = _f(line.get("total_cost"))
    if explicit is not None:
        return explicit
    qty = _f(line.get("quantity"))
    unit = _f(line.get("unit_cost"))
    if qty is not None and unit is not None:
        return qty * unit
    return 0.0


def summarize(capex_lines: list[dict[str, Any]] | None,
              unit_count: int | None = None,
              contingency_pct: Any = None) -> dict[str, Any]:
    """Roll the budget up.

    The contingency is computed on the itemized subtotal and returned as
    its own figure rather than folded in, so the page can show the
    holdback as a separate statement. A line already flagged
    is_contingency is treated as an explicit, hand-entered holdback: it
    counts toward the total but is excluded from the base the percentage
    is applied to, because charging a contingency on a contingency is
    double counting.
    """
    lines = list(capex_lines or [])

    itemized = [l for l in lines if not l.get("is_contingency")]
    explicit_contingency_lines = [l for l in lines if l.get("is_contingency")]

    by_scope = {s: 0.0 for s in SCOPES}
    for l in itemized:
        scope = l.get("scope") if l.get("scope") in SCOPES else SCOPE_INTERIOR
        by_scope[scope] += line_total(l)

    itemized_total = sum(by_scope.values())
    explicit_contingency = sum(line_total(l) for l in explicit_contingency_lines)

    pct = _f(contingency_pct)
    if pct is None:
        pct = DEFAULT_CONTINGENCY_PCT
    pct = max(0.0, pct)
    computed_contingency = itemized_total * (pct / 100.0)

    contingency_total = computed_contingency + explicit_contingency
    total = itemized_total + contingency_total

    units = None
    try:
        units = int(unit_count) if unit_count else None
    except (TypeError, ValueError):
        units = None

    return {
        "lines": lines,
        "line_count": len(lines),
        "by_scope": by_scope,
        "exterior_total": by_scope[SCOPE_EXTERIOR],
        "interior_total": by_scope[SCOPE_INTERIOR],
        "itemized_total": itemized_total,
        "contingency_pct": pct,
        "contingency_computed": computed_contingency,
        "contingency_explicit": explicit_contingency,
        "contingency_total": contingency_total,
        "total": total,
        "unit_count": units,
        "per_unit": (total / units) if units else None,
        "per_unit_reason": None if units else
            "Add a unit count to see cost per unit.",
        "source_counts": {
            SOURCE_MANUAL: sum(1 for l in lines
                               if (l.get("source") or SOURCE_MANUAL) == SOURCE_MANUAL),
            SOURCE_SITE_DD: sum(1 for l in lines
                                if (l.get("source") or SOURCE_MANUAL) == SOURCE_SITE_DD),
        },
        "has_lines": bool(lines),
    }


def effective_pct_of_price(total: float, purchase_price: Any) -> float:
    """Express the budget as a percentage of the purchase price.

    The unit conversion that lets capex reach equity without the returns
    engine changing shape. Returns 0.0 for an absent or zero price rather
    than dividing -- a scenario with no price has no returns to affect
    either, so there is nothing to express.
    """
    price = _f(purchase_price)
    if not price or price <= 0:
        return 0.0
    return (float(total) / price) * 100.0
