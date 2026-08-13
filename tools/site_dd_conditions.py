"""
FIRE Capital Tools - Site DD condition scale and roll-up.

The five-state condition scale and every figure derived from it. Pure: no
Flask, no database, no I/O -- findings in, counts out -- so the roll-up
can be unit-tested directly. Same principle as
tools/deal_analyzer_math.py, and for the same reason: a wrong summary here
is silently plausible in a way a wrong page layout is not.

WHY THIS REPLACES THE 1-5 NUMERIC SCALE

The old scale stored an integer 1-5 with NULL for N/A, reported a mean
("3.9 overall"), and derived a risk band from that mean. The new scale is
five named states stored as strings.

The change is not cosmetic, and one consequence is deliberate: THERE IS NO
OVERALL SCORE ANY MORE.

Excellent/Good/Satisfactory/Repair/Replace is an ordinal scale. The
distance from Good to Satisfactory is not the same quantity as the
distance from Repair to Replace -- the first is an opinion about wear, the
second is the difference between a work order and a purchase order.
Assigning 5..1 and averaging invents an interval scale that the words do
not support, and then hides the invention behind two decimal places. A
building at "3.9" tells you nothing you can act on.

So the headline is COUNTS BY STATE plus the number of findings that need
work. Every figure is a count of things you could point at. Branch 4 adds
the repair-cost total alongside them, which is the summary an owner
actually wants: not a grade, a bill.

WHAT THIS COSTS

The Deal Dive card loses "Overall 3.90 / 5" and its colour band. That was
the most quotable number the tool produced and the least defensible. It is
replaced by "4 need work, 2 to replace", which is longer to read and
harder to argue with.
"""

from __future__ import annotations

from typing import Any

# The scale, best to worst. Order matters: it drives display order, the
# "worst condition present" summary, and which states count as work.
EXCELLENT = "excellent"
GOOD = "good"
SATISFACTORY = "satisfactory"
REPAIR = "repair"
REPLACE = "replace"

CONDITIONS = (EXCELLENT, GOOD, SATISFACTORY, REPAIR, REPLACE)

CONDITION_LABELS = {
    EXCELLENT: "Excellent",
    GOOD: "Good",
    SATISFACTORY: "Satisfactory",
    REPAIR: "Repair",
    REPLACE: "Replace",
}

CONDITION_HINTS = {
    EXCELLENT: "New or near-new",
    GOOD: "Normal wear, nothing to do",
    SATISFACTORY: "Serviceable, monitor it",
    REPAIR: "Fix it — a work order",
    REPLACE: "Beyond repair — a line in the budget",
}

# The two states that produce work, and therefore the two that become
# capex lines in Branch 4. Defined once here so the definition of "needs
# work" cannot drift between the summary, the export and the capex hand-off.
WORK_CONDITIONS = (REPAIR, REPLACE)

# Colours, matching the deal-status chips elsewhere in the app so green and
# red mean the same thing across tools.
CONDITION_COLOURS = {
    EXCELLENT: "#059669",
    GOOD: "#16a34a",
    SATISFACTORY: "#ca8a04",
    REPAIR: "#ea580c",
    REPLACE: "#b91c1c",
}

# Scope of a finding. Property-scope findings have no room; unit and room
# scopes arrive in Branch 2 and are listed here so the vocabulary is fixed
# once rather than widened later.
SCOPE_PROPERTY = "property"
SCOPE_COMMON = "common"
SCOPE_UNIT = "unit"
SCOPE_ROOM = "room"
SCOPES = (SCOPE_PROPERTY, SCOPE_COMMON, SCOPE_UNIT, SCOPE_ROOM)


def is_valid(value: Any) -> bool:
    """True only for a genuine condition string.

    Everything else -- None, "", a stray integer left over from the old
    numeric scale, a boolean -- is not assessed. Old scores are explicitly
    NOT translated: a stored 2 means "Poor" on a scale that no longer
    exists, and silently reading it as "Repair" would be inventing an
    inspector's opinion.
    """
    return isinstance(value, str) and value in CONDITION_LABELS


def label(value: Any) -> str:
    return CONDITION_LABELS.get(value, "Not assessed")


def rank(value: Any) -> int:
    """Position on the scale, worst-first ordering helper. Unassessed
    sorts before everything so it cannot masquerade as a good result."""
    return CONDITIONS.index(value) if is_valid(value) else -1


def needs_work(value: Any) -> bool:
    return value in WORK_CONDITIONS


def summarize(findings: dict[str, Any], catalogue: Any) -> dict[str, Any]:
    """Roll condition responses up into counts.

    `findings` maps item_key -> condition string (or None / absent for not
    assessed). `catalogue` is the checklist module's CATEGORIES tuple, so
    this module holds the scale and the checklist holds the content.

    Unknown keys are ignored rather than raising, so a stale key left over
    from a future checklist revision can never break the summary of an
    assessment that is otherwise fine -- the same tolerance the old
    scoring had, and for the same reason.
    """
    valid_keys = [k for cat in catalogue for k, _ in cat["items"]]
    valid_set = set(valid_keys)

    assessed = {
        k: v for k, v in (findings or {}).items()
        if k in valid_set and is_valid(v)
    }

    counts = {c: 0 for c in CONDITIONS}
    for v in assessed.values():
        counts[v] += 1

    work_items = sorted(
        (k for k, v in assessed.items() if needs_work(v)),
        key=lambda k: (CONDITIONS.index(assessed[k]), valid_keys.index(k)),
        reverse=False,
    )
    # Worst first: Replace above Repair, then checklist order within each.
    work_items.sort(key=lambda k: (-CONDITIONS.index(assessed[k]), valid_keys.index(k)))

    categories = []
    for cat in catalogue:
        keys = [k for k, _ in cat["items"]]
        cat_counts = {c: 0 for c in CONDITIONS}
        for k in keys:
            if k in assessed:
                cat_counts[assessed[k]] += 1
        cat_assessed = sum(cat_counts.values())
        worst = None
        for c in reversed(CONDITIONS):
            if cat_counts[c]:
                worst = c
                break
        categories.append({
            "key": cat["key"],
            "name": cat["name"],
            "counts": cat_counts,
            "assessed_count": cat_assessed,
            "item_count": len(keys),
            "work_count": sum(cat_counts[c] for c in WORK_CONDITIONS),
            "worst": worst,
            "worst_label": label(worst) if worst else None,
        })

    total_items = len(valid_keys)
    assessed_count = len(assessed)

    worst_overall = None
    for c in reversed(CONDITIONS):
        if counts[c]:
            worst_overall = c
            break

    return {
        "counts": counts,
        # Ordered for display: one row per state, worst first.
        "ordered_counts": [
            {"key": c, "label": CONDITION_LABELS[c], "count": counts[c],
             "colour": CONDITION_COLOURS[c], "is_work": c in WORK_CONDITIONS}
            for c in reversed(CONDITIONS)
        ],
        "work_count": sum(counts[c] for c in WORK_CONDITIONS),
        "repair_count": counts[REPAIR],
        "replace_count": counts[REPLACE],
        "work_items": work_items,
        "worst": worst_overall,
        "worst_label": label(worst_overall) if worst_overall else None,
        "assessed_count": assessed_count,
        "total_items": total_items,
        "not_assessed_count": total_items - assessed_count,
        "completion_pct": (assessed_count / total_items * 100) if total_items else 0.0,
        "categories": categories,
        # Deliberately absent: "overall". See the module docstring -- an
        # ordinal scale has no mean, and a headline number nobody can
        # defend is worse than no headline number.
        "headline": _headline(counts, assessed_count, total_items),
    }


def _headline(counts, assessed_count, total_items) -> str:
    """One sentence of plain English, built from counts only."""
    if not assessed_count:
        return "Nothing assessed yet."
    work = counts[REPAIR] + counts[REPLACE]
    if not work:
        return f"{assessed_count} of {total_items} assessed — nothing needs work."
    bits = []
    if counts[REPAIR]:
        bits.append(f"{counts[REPAIR]} to repair")
    if counts[REPLACE]:
        bits.append(f"{counts[REPLACE]} to replace")
    return f"{assessed_count} of {total_items} assessed — " + ", ".join(bits) + "."
