"""
FIRE Capital Tools - Underwriting rent roll / T12 cross-check.

Four named comparisons between what the rent roll and assumptions imply
and what the T12 actually recorded. Nothing else.

Pure: no Flask, no database, no I/O.

WHY FOUR, AND NOT A VALIDATION ENGINE

Adding a fifth comparison should be a decision someone makes, not a
configuration someone fills in. An open-ended validation engine would
accumulate checks nobody chose, each with a threshold nobody set, and the
warnings would stop being read. These four are the ones worth making:

    GPR           the rent roll annualized, against the T12's gross
                  potential rent -- do the two documents describe the
                  same building?
    EGI           the modelled effective gross income against the T12's
                  total income -- is the underwriting above or below what
                  the property actually produced?
    Other income  the scenario assumption against what the T12 booked
    Unit count    the rent roll's count against the property-info figure

WHY THEY ARE WARNINGS AND NEVER BLOCKS

A value-add deal is *supposed* to underwrite above trailing performance.
On Eagle Rock the modelled EGI runs 18.5% above the T12, which may be
entirely intentional: raise rents, cut vacancy, that is the plan. The
tool has no way to know, so it must not pretend to. Every message here
states both figures, states the gap, and asks. None of them stops
anything, none of them says "error", and none of them implies the model
is wrong.

They are only produced when BOTH a rent roll and a T12 are present.
Comparing a model against a document that was never imported would be
comparing it against zero.
"""

from __future__ import annotations

from typing import Any

# How far apart two figures must be before the difference is worth a
# reader's attention. Chosen to be loose: a rent roll is a point-in-time
# snapshot and a T12 is twelve months of history, so they are never
# expected to agree exactly, and a warning that fires on every scenario
# teaches people to ignore warnings.
GPR_TOLERANCE_PCT = 5.0
EGI_TOLERANCE_PCT = 5.0
OTHER_INCOME_TOLERANCE_PCT = 10.0
UNIT_COUNT_TOLERANCE = 0

SEVERITY_INFO = "info"
SEVERITY_ATTENTION = "attention"


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _compare(key, label, model, actual, tolerance_pct, model_name, actual_name,
             question, fmt="money"):
    """One comparison. Returns None when either side is missing.

    A missing figure is not a finding: it means the comparison could not
    be made, and reporting "your model is 100% above trailing" because the
    T12 line was absent would be worse than saying nothing.
    """
    m, a = _f(model), _f(actual)
    if m is None or a is None:
        return None
    if a == 0:
        return None
    gap = m - a
    gap_pct = gap / abs(a) * 100.0
    fires = abs(gap_pct) > tolerance_pct

    direction = "above" if gap > 0 else "below"
    if fmt == "money":
        shown = f"${m:,.0f} vs ${a:,.0f}"
        gap_text = f"${abs(gap):,.0f}"
    else:
        shown = f"{m:,.0f} vs {a:,.0f}"
        gap_text = f"{abs(gap):,.0f}"

    return {
        "key": key,
        "label": label,
        "model": m,
        "actual": a,
        "model_name": model_name,
        "actual_name": actual_name,
        "gap": gap,
        "gap_pct": gap_pct,
        "fires": fires,
        "severity": SEVERITY_ATTENTION if fires else SEVERITY_INFO,
        "summary": shown,
        "message": (
            f"{label}: your model is {abs(gap_pct):,.1f}% {direction} the T12 "
            f"({shown}, a {gap_text} difference). {question}"
        ),
        "within_message": (
            f"{label}: {shown}, within {tolerance_pct:,.0f}%."
        ),
        "tolerance_pct": tolerance_pct,
    }


def build(egi: dict[str, Any] | None,
          t12_totals: dict[str, Any] | None,
          *,
          has_rentroll: bool,
          has_t12: bool,
          unit_count: Any = None) -> dict[str, Any]:
    """The four comparisons.

    `t12_totals` is the shape quick_analyzer_t12.extract_totals() returns
    -- reused rather than reimplemented, so the two tools cannot disagree
    about what a T12 says. `egi` is build_egi()'s output.
    """
    if not (has_rentroll and has_t12) or not egi or not t12_totals:
        return {
            "available": False,
            "reason": (
                "A cross-check needs both a rent roll and a T12. "
                + ("Import a T12 to compare against the rent roll."
                   if has_rentroll else
                   "Import a rent roll to compare against the T12."
                   if has_t12 else
                   "Import a rent roll and a T12 to compare them.")
            ),
            "checks": [],
            "firing": [],
        }

    checks = []

    checks.append(_compare(
        "gpr", "Gross potential rent",
        egi.get("gross_potential_rent"), t12_totals.get("gross_potential_income"),
        GPR_TOLERANCE_PCT, "rent roll, annualized", "T12",
        "A rent roll is a snapshot and a T12 is twelve months, so some gap is "
        "normal — but a large one usually means the two documents are not "
        "describing the same unit set."))

    checks.append(_compare(
        "egi", "Effective gross income",
        egi.get("effective_gross_income"), t12_totals.get("effective_gross_income"),
        EGI_TOLERANCE_PCT, "model", "T12",
        "On a value-add deal that is expected — you are underwriting the "
        "improvement, not the history. Worth confirming it is intentional."))

    checks.append(_compare(
        "other_income", "Other income",
        egi.get("other_income"), t12_totals.get("other_income"),
        OTHER_INCOME_TOLERANCE_PCT, "assumption", "T12",
        "Other income is usually the most stable line year to year."))

    roll_units = _f((egi or {}).get("unit_count"))
    prop_units = _f(unit_count)
    if roll_units is not None and prop_units is not None and roll_units != prop_units:
        checks.append({
            "key": "unit_count",
            "label": "Unit count",
            "model": prop_units,
            "actual": roll_units,
            "model_name": "property info",
            "actual_name": "rent roll",
            "gap": prop_units - roll_units,
            "gap_pct": ((prop_units - roll_units) / roll_units * 100.0) if roll_units else None,
            "fires": abs(prop_units - roll_units) > UNIT_COUNT_TOLERANCE,
            "severity": SEVERITY_ATTENTION,
            "summary": f"{prop_units:,.0f} vs {roll_units:,.0f}",
            "message": (
                f"Unit count: property info says {prop_units:,.0f}, the rent roll "
                f"has {roll_units:,.0f}. Which is right?"),
            "within_message": None,
            "tolerance_pct": None,
        })

    checks = [c for c in checks if c]
    firing = [c for c in checks if c["fires"]]

    return {
        "available": True,
        "reason": None,
        "checks": checks,
        "firing": firing,
        "count": len(firing),
        "all_clear": not firing,
    }
