"""
FIRE Capital Tools - Deal readiness reference thresholds.

Compares figures Underwriting ALREADY computes against a set of default
targets. Adds no calculation to the returns engine: every metric below is
read straight off analyze_scenario()'s output, with one exception noted
on its own row (expense ratio, a division of two values that result
already contains).

Why this file exists rather than a database, matching service_costs.py:
five numbers that change rarely, where a code-reviewed edit leaves better
history than a silent form POST. For thresholds that decide whether a
deal reads as acceptable, "who changed 1.25 to 1.10, and why" belongs in
git. There is therefore no storage, no env var, and the standing
persistent-path requirement does not apply.

THE POINT OF THIS MODULE IS THE HONESTY OF ITS LABELS.

These thresholds are NOT FIRE Capital standards. Two of them are Michael
Blank's template defaults; three are industry-convention placeholders
that nobody has confirmed. Presenting all five as equally authoritative
would be the same failure the API cost page guards against -- a
plausible-looking number that gets believed because it is rendered
confidently. So every threshold carries its own provenance and its own
disclaimer, and tests enforce that neither can be dropped.

Deliberately absent, and not to be added without a real requirement:

  * An overall score or verdict. A single "Deal Score: 62/100" built on
    three invented thresholds would be the most quotable and least
    defensible number in the app.
  * A Value-Add / Stable split. That categorization is itself
    unconfirmed; building it speculatively bakes in a structure that may
    be wrong.
  * Reserves per unit. Not reliably derivable -- T12 import sets
    category_key to the GL category code, so the semantic "reserves" key
    is absent on exactly the scenarios built from a real T12.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

# ── Provenance ───────────────────────────────────────────────────────────
#
# Two tiers, because the thresholds are not equally sourced and a reader
# must be able to tell them apart at a glance.

PROVENANCE_TEMPLATE = "from_template"
PROVENANCE_INFERRED = "inferred"

VALID_PROVENANCE = (PROVENANCE_TEMPLATE, PROVENANCE_INFERRED)

# Every disclaimer must contain this phrase. Tests assert it, so the
# labels cannot be softened into meaninglessness by a later edit.
REQUIRED_DISCLAIMER_PHRASE = "not confirmed"

DISCLAIMERS = {
    PROVENANCE_TEMPLATE: (
        "Michael Blank template default — not confirmed as a FIRE Capital standard"
    ),
    PROVENANCE_INFERRED: (
        "Industry-convention placeholder — not confirmed, and not from the template"
    ),
}

# Comparison directions.
MIN = "min"   # actual must be >= threshold
MAX = "max"   # actual must be <= threshold

STATUS_PASS = "pass"
STATUS_ATTENTION = "attention"
STATUS_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Threshold:
    """One reference target.

    `extract` pulls the metric out of an analyze_scenario() result. It is
    a function rather than a key path so the one derived metric (expense
    ratio) can be expressed without adding anything to the engine, and so
    a missing value returns None rather than raising.
    """

    key: str
    label: str
    value: float
    direction: str
    fmt: str                      # "pct" | "ratio" | "multiple"
    provenance: str
    extract: Callable[[dict[str, Any]], Optional[float]]
    source_note: str
    reason_key: Optional[str] = None   # engine's explanation when the value is None

    @property
    def disclaimer(self) -> str:
        return DISCLAIMERS[self.provenance]

    @property
    def is_from_template(self) -> bool:
        return self.provenance == PROVENANCE_TEMPLATE


def _returns(result: dict[str, Any], key: str) -> Optional[float]:
    value = (result or {}).get("returns", {}).get(key)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _expense_ratio(result: dict[str, Any]) -> Optional[float]:
    """Year-1 operating expenses over effective gross income.

    The one metric here that is not read directly off the engine. Both
    operands are already in the result -- this is a division performed for
    display, not a new engine calculation. Returns None rather than
    dividing by zero on a scenario with no income.
    """
    opex = (result or {}).get("operating_expenses_year1")
    egi = ((result or {}).get("egi") or {}).get("effective_gross_income")
    if not isinstance(opex, (int, float)) or not isinstance(egi, (int, float)):
        return None
    if not egi:
        return None
    return opex / egi


THRESHOLDS: tuple[Threshold, ...] = (
    Threshold(
        key="dscr",
        label="DSCR (Year 1)",
        value=1.25,
        direction=MIN,
        fmt="ratio",
        provenance=PROVENANCE_TEMPLATE,
        extract=lambda r: _returns(r, "dscr"),
        reason_key="dscr_reason",
        source_note="Taken from the Michael Blank template's Variables sheet.",
    ),
    Threshold(
        key="levered_irr",
        label="Levered IRR",
        value=0.14,
        direction=MIN,
        fmt="pct",
        provenance=PROVENANCE_TEMPLATE,
        extract=lambda r: _returns(r, "levered_irr"),
        reason_key="levered_irr_reason",
        source_note="Taken from the Michael Blank template's Variables sheet.",
    ),
    Threshold(
        key="expense_ratio",
        label="Operating Expense Ratio",
        value=0.60,
        direction=MAX,
        fmt="pct",
        provenance=PROVENANCE_INFERRED,
        extract=_expense_ratio,
        source_note=(
            "The template tracks an expense ratio, but this specific value was not "
            "read from it — it is a common multifamily rule of thumb."
        ),
    ),
    Threshold(
        key="cash_on_cash",
        label="Cash-on-Cash (Year 1)",
        value=0.08,
        direction=MIN,
        fmt="pct",
        provenance=PROVENANCE_INFERRED,
        extract=lambda r: _returns(r, "cash_on_cash"),
        source_note="A common syndication convention, not sourced from the template.",
    ),
    Threshold(
        key="equity_multiple",
        label="Equity Multiple",
        value=2.0,
        direction=MIN,
        fmt="multiple",
        provenance=PROVENANCE_INFERRED,
        extract=lambda r: _returns(r, "equity_multiple"),
        source_note=(
            "A typical five-year target. The template tracks average annual return "
            "instead, which this tool does not compute."
        ),
    ),
)


def evaluate(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Compare a scenario's computed figures against each threshold.

    Never raises and never invents a value. A metric the engine could not
    compute is reported as unavailable, carrying the engine's own reason
    where it gave one -- a blank row would read as "fine", which is the
    opposite of true.
    """
    rows: list[dict[str, Any]] = []
    for t in THRESHOLDS:
        actual = t.extract(result) if result else None
        if actual is None:
            status = STATUS_UNAVAILABLE
        elif t.direction == MIN:
            status = STATUS_PASS if actual >= t.value else STATUS_ATTENTION
        else:
            status = STATUS_PASS if actual <= t.value else STATUS_ATTENTION

        reason = None
        if actual is None and t.reason_key and result:
            reason = (result.get("returns") or {}).get(t.reason_key)

        rows.append({
            "key": t.key,
            "label": t.label,
            "actual": actual,
            "threshold": t.value,
            "direction": t.direction,
            "fmt": t.fmt,
            "status": status,
            "provenance": t.provenance,
            "disclaimer": t.disclaimer,
            "source_note": t.source_note,
            "is_from_template": t.is_from_template,
            "reason": reason,
        })
    return rows


def counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Row counts by status.

    Deliberately NOT a score. Callers may say "3 of 5 met" -- a factual
    tally of comparisons against unconfirmed targets -- but nothing here
    grades a deal, because three of these five targets are placeholders.
    """
    return {
        STATUS_PASS: sum(1 for r in rows if r["status"] == STATUS_PASS),
        STATUS_ATTENTION: sum(1 for r in rows if r["status"] == STATUS_ATTENTION),
        STATUS_UNAVAILABLE: sum(1 for r in rows if r["status"] == STATUS_UNAVAILABLE),
        "total": len(rows),
    }
