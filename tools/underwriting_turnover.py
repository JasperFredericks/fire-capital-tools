"""
FIRE Capital Tools - turnover items are capital, not operating.

WHAT WAS WRONG

Eagle Rock's T12 gave Underwriting a 68.58% expense ratio where Scorecard
Pro reported 60%. Michelle flagged the cause directly: flooring and
appliance turnover costs were being counted as operating expenses when
they are capital.

The measured gap was $107,606.05. Four lines account for $97,665.38 of
it -- 90.8%:

    Appliances                              33,991.30
    Floor Covering - Carpet                 27,294.12
    Countertop and Tub Resurfacing          24,602.96
    Floor Covering - Vinyl / Tile / Wood    11,777.00

Moving them takes the ratio from 68.58% to 60.60%, against the T12's own
59.79% -- the file's rollup parents (6000 + 7000), which is the figure
Scorecard Pro reads and reports.

WHY THIS IS NOT IN THE SHARED CLASSIFIER

The keyword classifier lives in scorecard_pro/kpis.py and is read by
three tools: Underwriting's T12 import, Scorecard Pro, and Quick Deal
Analyzer's T12 import. Michelle's correction is about how UNDERWRITING
should model a hold -- a turnover cost belongs in the capital budget, not
the NOI -- and is not a statement about what Scorecard Pro should report
or what a quick valuation should assume. Changing the shared function
would have moved all three, silently, on the strength of one tool's
requirement.

So this runs after the shared classifier, on Underwriting's import path
only. Everything the shared classifier already decided is left exactly as
it decided it: this can move a line from operating to capex and can do
nothing else.

WHY REPAIRS ARE NOT SWEPT UP

"Floor Covering - Carpet" is a turn cost. "Carpet Repair & Cleaning
(occupied)" is maintenance on a unit somebody is living in, and
"Appliance Parts & Supplies" is consumables. A substring match on
"carpet" or "appliance" alone would take all of them, understate
operating expense and overstate NOI -- the same error in the opposite
direction, which is worse because it flatters the deal.

    Carpet Repair & Cleaning (occupied)     1,272.26   stays operating
    Contract Carpet Cleaning                2,273.26   stays operating
    Appliance Repair and Supplies             343.46   stays operating
    Appliance Parts & Supplies                124.53   stays operating
    Subfloor Repairs                          387.99   stays operating

Interior paint is deliberately left operating too. Moving it as well
takes the ratio to 58.87%, below the file's own figure -- turn paint is
conventionally expensed, and the arithmetic agrees.

THIS IS A DEFAULT, NOT A RULE

Every line stays editable in the Underwriting expense table, in both
directions. This only changes what a NEW import proposes; a scenario
already saved keeps the classification it was saved with until somebody
re-imports its T12.
"""

from __future__ import annotations

import re
from typing import Any

CAPEX_KIND = "capex"
OPERATING_KIND = "operating"

# Turn costs: the physical thing is replaced between residents and the
# replacement lasts years. Matched on the account name, like the shared
# classifier, because code ranges differ between charts of accounts.
TURNOVER_CAPEX_PATTERNS: tuple[str, ...] = (
    "floor covering",
    "flooring",
    "appliance",
    "resurfacing",
    "countertop",
    "cabinet",
)

# Words that mean the line is work done ON a thing, or the consumables to
# do it, rather than the thing itself. Checked first: a repair is an
# operating expense whatever it is a repair OF.
MAINTENANCE_PATTERNS: tuple[str, ...] = (
    "repair",
    "cleaning",
    "clean",
    "parts",
    "supplies",
    "maintenance",
    "service",
    "contract",
)

_SPACES = re.compile(r"\s+")


def _normalize(name: Any) -> str:
    return _SPACES.sub(" ", str(name or "").strip().lower())


def is_maintenance(name: Any) -> bool:
    """Work on a thing, or the consumables for it. Never capital."""
    low = _normalize(name)
    return any(pat in low for pat in MAINTENANCE_PATTERNS)


def is_turnover_capital(name: Any) -> bool:
    """A turn cost that should sit in the capital budget.

    Maintenance wins: "Carpet Repair & Cleaning" is not a turn cost even
    though it contains "carpet".
    """
    low = _normalize(name)
    if not low or is_maintenance(low):
        return False
    return any(pat in low for pat in TURNOVER_CAPEX_PATTERNS)


def reclassify(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the turnover default to freshly imported lines.

    One direction only. A line the shared classifier already called capex
    or non_operating is returned untouched -- this can move a line out of
    operating and can never move one in, so it cannot undo a decision
    made upstream.

    Returns new dicts; the input is not mutated.
    """
    out = []
    for line in lines or []:
        row = dict(line)
        if row.get("line_kind") == OPERATING_KIND and is_turnover_capital(row.get("label")):
            row["line_kind"] = CAPEX_KIND
            # Excluded from the operating total for the same reason every
            # other capex line is: it is not an annual operating expense.
            # Still stored, still listed, still re-includable by hand.
            row["is_included"] = False
        out.append(row)
    return out


def summarize(before: list[dict[str, Any]],
              after: list[dict[str, Any]]) -> dict[str, Any]:
    """What the reclassification moved, for a message to the user."""
    moved = [a for b, a in zip(before or [], after or [])
             if b.get("line_kind") != a.get("line_kind")]
    return {
        "moved": moved,
        "count": len(moved),
        "amount": sum(float(m.get("annual_amount") or 0.0) for m in moved),
    }
