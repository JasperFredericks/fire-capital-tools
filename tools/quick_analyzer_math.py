"""
FIRE Capital Tools - Quick Deal Analyzer calculations.

One question: given a NOI and a cap rate, what should this property be
worth? That is the whole tool.

    Gross Potential Income
  - Vacancy                    (% of GPI)
  + Other Income
  = Effective Gross Income
  - Operating Expenses         (% of EGI, or a dollar amount)
  = Net Operating Income
  / Target Cap Rate
  = Implied Purchase Price

Deliberately pure -- no Flask, no request context, no I/O -- so the
formulas can be unit-tested directly (tests/test_quick_analyzer_math.py)
rather than through an HTTP round trip. Same standalone principle as
tools/deal_analyzer_math.py and tools/site_dd_checklist.py.

WHY THIS IS A SEPARATE MODULE FROM deal_analyzer_math.py

The valuation identity already exists inside that file, as one line of
analyze_noi_series(): `gross_sale = noi_exit / exit_cap`. It is not
reusable from here, because it sits inside a 150-line function that also
requires a purchase price, an LTV, a hold period and an amortization
schedule -- none of which this tool has. Extracting it would mean surgery
on the exact function Underwriting, Waterfall and Investor Report all
depend on, to save one division.

So this module duplicates a division rather than refactoring a shared
engine three other tools rely on. deal_analyzer_math.py is not imported
here and is not touched at all; a test asserts the absence of the import.

WHAT THIS TOOL IS NOT

No leverage, no hold period, no annual cash flow, no IRR, no exit. A
single-point valuation has no time dimension: there is no year 2 to grow
into and no exit to capitalize, which is why there is no NOI growth rate
here. That model still exists, intact and tested, in deal_analyzer_math
.py, and is exercised through Underwriting -- which does it far more
thoroughly, from a real rent roll and itemized expenses.

Nothing here returns NaN or infinity. Anything that cannot be computed
comes back as None with a companion reason string, so the caller renders
an em dash and an explanation rather than "nan".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# ── NOI provenance ───────────────────────────────────────────────────────
#
# Where the NOI came from changes what the resulting price means. A price
# built on a broker's pro-forma NOI and a price built on twelve months of
# actuals are not the same claim, and the tool must not present them as
# though they were. Every result carries its provenance, and the template
# renders the label beside the number.

PROVENANCE_BUILDUP = "buildup"
PROVENANCE_ENTERED = "entered"
PROVENANCE_T12 = "t12"

PROVENANCE_LABELS = {
    PROVENANCE_BUILDUP: "Estimated",
    PROVENANCE_ENTERED: "Entered",
    PROVENANCE_T12: "Actuals — from T12",
}

PROVENANCE_NOTES = {
    PROVENANCE_BUILDUP: (
        "Built up from the income and expense assumptions entered below — "
        "an estimate, not observed performance."
    ),
    PROVENANCE_ENTERED: (
        "Entered directly. The tool cannot tell whether this figure is "
        "actual performance or a pro forma."
    ),
    PROVENANCE_T12: (
        "Totalled from twelve months of actuals in the uploaded T12."
    ),
}

VALID_PROVENANCE = (PROVENANCE_BUILDUP, PROVENANCE_ENTERED, PROVENANCE_T12)

# ── Range toggle ─────────────────────────────────────────────────────────
#
# "How far either side of this number is still worth a closer look."
RANGE_CHOICES = (5, 10, 20)
DEFAULT_RANGE_PCT = 10

# ── Grading ──────────────────────────────────────────────────────────────
#
# THE POINT OF THIS SECTION IS THE HONESTY OF ITS LABELS.
#
# These four bands are NOT FIRE Capital standards, and they are not from
# the Michael Blank template either. They were proposed by the assistant
# that built this tool because a grade was asked for and no source for one
# was available. Michelle was asked for her real thresholds before this
# shipped and had not supplied them.
#
# This follows tools/deal_readiness_defaults.py exactly, and for the same
# reason: a confident red/green verdict on a purchase price is the most
# quotable and least defensible number this app could render. That module
# went as far as refusing to build a composite score at all. A grade was
# explicitly requested here, so it exists -- but it carries its provenance
# on screen, and tests enforce that the disclaimer cannot be dropped.
#
# When real thresholds arrive, change GRADE_BANDS and set
# GRADE_PROVENANCE to PROVENANCE_CONFIRMED. Nothing else needs to move.

PROVENANCE_UNCONFIRMED = "unconfirmed"
PROVENANCE_CONFIRMED = "confirmed"

# Every disclaimer must contain this phrase. A test asserts it, so the
# label cannot be softened into meaninglessness by a later edit.
REQUIRED_DISCLAIMER_PHRASE = "not confirmed"

GRADE_DISCLAIMERS = {
    PROVENANCE_UNCONFIRMED: (
        "These bands are a placeholder — not confirmed as a FIRE Capital "
        "standard, and not from the Michael Blank template. Treat the "
        "colour as a prompt to look closer, not as a verdict."
    ),
    PROVENANCE_CONFIRMED: (
        "Bands confirmed by FIRE Capital."
    ),
}

GRADE_PROVENANCE = PROVENANCE_UNCONFIRMED

GRADE_GREEN = "green"
GRADE_YELLOW = "yellow"
GRADE_ORANGE = "orange"
GRADE_RED = "red"


@dataclass(frozen=True)
class GradeBand:
    """One grading band, expressed as an upper bound on how far the asking
    price may sit above the implied price. `max_over_pct` is None for the
    final, open-ended band."""

    key: str
    label: str
    max_over_pct: Optional[float]
    meaning: str


# Ordered from best to worst. The first band whose bound the overage
# satisfies wins.
GRADE_BANDS: tuple[GradeBand, ...] = (
    GradeBand(GRADE_GREEN, "At or below target", 0.0,
              "Priced at or under what this NOI supports at your cap rate."),
    GradeBand(GRADE_YELLOW, "Slightly above target", 5.0,
              "Within 5% of the implied price — close enough to be worth a look."),
    GradeBand(GRADE_ORANGE, "Above target", 15.0,
              "5–15% above the implied price — needs a story to work."),
    GradeBand(GRADE_RED, "Well above target", None,
              "More than 15% above the implied price at this cap rate."),
)


class ValidationError(ValueError):
    """Raised for input combinations that cannot produce a meaningful
    result at all. The caller turns this into a form error, so the message
    is written to be shown to the user directly."""


def resolve_provenance(claimed: str, submitted: dict[str, Any],
                       imported: dict[str, Any] | None) -> str:
    """Downgrade a T12 provenance claim the moment the figures are edited.

    A label nobody can falsify is the only kind worth rendering. Without
    this, uploading a T12 and then typing over the expenses would still
    show "Actuals — from T12", which would be a false claim about where
    the number came from -- the exact failure this labelling exists to
    prevent.

    `imported` is what the parse produced; `submitted` is what came back
    from the form. Any difference beyond a cent means a human touched it,
    so the claim drops to "Estimated" (or "Entered", resolved upstream).
    Absent an `imported` record there is nothing to substantiate the
    claim, so it is not honoured either.
    """
    if claimed != PROVENANCE_T12:
        return claimed
    if not imported:
        return PROVENANCE_BUILDUP
    for field in ("gross_potential_income", "vacancy_pct", "other_income",
                  "operating_expenses", "noi_direct"):
        was = _f(imported.get(field))
        now = _f(submitted.get(field))
        if was is None and now is None:
            continue
        if was is None or now is None:
            return PROVENANCE_BUILDUP
        # The percentage is carried to six decimals against a figure in
        # the millions; a cent of tolerance on the value it implies is
        # rounding, not an edit.
        if abs(was - now) > 0.01:
            return PROVENANCE_BUILDUP
    return PROVENANCE_T12


def _f(value: Any) -> Optional[float]:
    """Coerce to float, treating blanks and unparseable text as absent
    rather than as zero. A missing vacancy rate and a vacancy rate of zero
    are different claims and must not collapse into each other."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── The NOI build-up ─────────────────────────────────────────────────────

def build_noi(gpi: Any, vacancy_pct: Any, other_income: Any,
              expenses_mode: str, expenses_value: Any) -> dict[str, Any]:
    """Gross Potential Income down to NOI, one line at a time.

    Returns every intermediate figure, not just the total: the point of
    the 2-Minute Analysis format is that the reader can follow the
    subtraction, and a caller that only receives NOI cannot show the work.

    `expenses_mode` is "pct" (a percentage of EGI) or "amount" (dollars).
    The percentage is taken against EGI rather than GPI deliberately --
    an expense ratio quoted against gross potential income would flatter
    every property with real vacancy.
    """
    gpi_v = _f(gpi)
    if gpi_v is None:
        raise ValidationError("Gross Potential Income is required.")
    if gpi_v < 0:
        raise ValidationError("Gross Potential Income cannot be negative.")

    vac_pct = _f(vacancy_pct)
    if vac_pct is None:
        raise ValidationError("Vacancy is required (enter 0 for a fully occupied property).")
    if vac_pct < 0:
        raise ValidationError("Vacancy cannot be negative.")
    if vac_pct > 100:
        raise ValidationError("Vacancy cannot exceed 100% — that would be negative rental income.")

    other = _f(other_income) or 0.0
    if other < 0:
        raise ValidationError("Other income cannot be negative.")

    vacancy_loss = gpi_v * (vac_pct / 100.0)
    net_rental_income = gpi_v - vacancy_loss
    egi = net_rental_income + other

    if expenses_mode not in ("pct", "amount"):
        raise ValidationError("Operating expenses must be entered as a percentage or a dollar amount.")

    exp_v = _f(expenses_value)
    if exp_v is None:
        raise ValidationError("Operating expenses are required.")
    if exp_v < 0:
        raise ValidationError("Operating expenses cannot be negative.")

    if expenses_mode == "pct":
        if exp_v > 100:
            raise ValidationError("An expense ratio above 100% of EGI would mean a negative NOI before debt.")
        operating_expenses = egi * (exp_v / 100.0)
        expense_ratio = exp_v / 100.0
    else:
        operating_expenses = exp_v
        expense_ratio = (operating_expenses / egi) if egi else None

    noi = egi - operating_expenses

    return {
        "gross_potential_income": gpi_v,
        "vacancy_pct": vac_pct,
        "vacancy_loss": vacancy_loss,
        "net_rental_income": net_rental_income,
        "other_income": other,
        "effective_gross_income": egi,
        "operating_expenses": operating_expenses,
        "expenses_mode": expenses_mode,
        "expense_ratio": expense_ratio,
        "noi": noi,
    }


# ── Valuation ────────────────────────────────────────────────────────────

def implied_price(noi: float, cap_rate_pct: float) -> float:
    """NOI / cap rate. The entire tool, in one line.

    A zero or negative cap rate is rejected rather than returned as
    infinity: at a cap rate of zero the implied price is unbounded, which
    is not a valuation, it is a division by zero wearing a hat.
    """
    cap = _f(cap_rate_pct)
    if cap is None:
        raise ValidationError("Target cap rate is required.")
    if cap <= 0:
        raise ValidationError(
            "Target cap rate must be greater than zero — a zero cap rate implies an infinite price."
        )
    return float(noi) / (cap / 100.0)


def price_range(price: float, range_pct: float) -> dict[str, float]:
    """A symmetric band around the implied price.

    Not a confidence interval and not a statistical claim -- it is "how
    far either side of this number would still be worth investigating".
    Computed here as well as in the browser so a page with JavaScript
    disabled still shows a band, and so the arithmetic is testable.
    """
    r = _f(range_pct)
    if r is None or r < 0:
        raise ValidationError("Range must be zero or greater.")
    delta = price * (r / 100.0)
    return {"range_pct": r, "low": price - delta, "high": price + delta, "delta": delta}


# ── Grading ──────────────────────────────────────────────────────────────

def grade(asking_price: Any, implied: float) -> dict[str, Any]:
    """Grade an asking price against the implied price.

    Returns a dict with `graded: False` and a reason rather than raising
    when there is nothing to grade: an asking price is optional, and a
    valuation with no asking price to compare against is a perfectly
    valid use of this tool, not an error.

    The bands are unconfirmed placeholders. Every return carries the
    disclaimer so a caller cannot render the colour without it.
    """
    base = {
        "provenance": GRADE_PROVENANCE,
        "disclaimer": GRADE_DISCLAIMERS[GRADE_PROVENANCE],
        "bands": GRADE_BANDS,
    }

    ask = _f(asking_price)
    if ask is None:
        return {**base, "graded": False,
                "reason": "Enter an asking price to see how it compares with this valuation."}
    if ask <= 0:
        return {**base, "graded": False,
                "reason": "Asking price must be greater than zero to compare against."}
    if implied <= 0:
        return {**base, "graded": False,
                "reason": "A valuation of zero or less cannot be compared against an asking price."}

    over_pct = (ask - implied) / implied * 100.0

    band = GRADE_BANDS[-1]
    for candidate in GRADE_BANDS:
        if candidate.max_over_pct is not None and over_pct <= candidate.max_over_pct:
            band = candidate
            break

    return {
        **base,
        "graded": True,
        "asking_price": ask,
        "implied_price": implied,
        "difference": ask - implied,
        "over_pct": over_pct,
        "band": band,
        "key": band.key,
        "label": band.label,
        "meaning": band.meaning,
    }


# ── Entry point ──────────────────────────────────────────────────────────

def analyze(inputs: dict[str, Any]) -> dict[str, Any]:
    """The Quick Deal Analyzer entry point.

    Two NOI routes converge here. When `noi_direct` carries a value the
    build-up is skipped entirely and that figure is used as-is; otherwise
    NOI is built from the income and expense lines. The T12 path is not a
    third route through the arithmetic -- it prefills the same fields and
    then travels one of these two, carrying its provenance with it. That
    is why provenance is an input rather than something inferred here:
    only the caller knows whether the numbers in the form arrived by hand
    or out of a parsed file.
    """
    provenance = inputs.get("noi_provenance") or PROVENANCE_BUILDUP
    if provenance not in VALID_PROVENANCE:
        raise ValidationError(f"Unknown NOI source: {provenance!r}")

    # A T12 claim only survives while the figures still match the import.
    provenance = resolve_provenance(provenance, inputs, inputs.get("imported"))

    noi_direct = _f(inputs.get("noi_direct"))
    if noi_direct is not None:
        buildup = None
        noi = noi_direct
        # A directly entered figure is "Entered" unless it came out of a
        # T12, which is a stronger claim and must not be downgraded.
        if provenance == PROVENANCE_BUILDUP:
            provenance = PROVENANCE_ENTERED
    else:
        if provenance == PROVENANCE_ENTERED:
            raise ValidationError("NOI is required when entering it directly.")
        buildup = build_noi(
            inputs.get("gross_potential_income"),
            inputs.get("vacancy_pct"),
            inputs.get("other_income"),
            inputs.get("expenses_mode") or "pct",
            inputs.get("operating_expenses"),
        )
        noi = buildup["noi"]

    if noi <= 0:
        raise ValidationError(
            "NOI is zero or negative, so there is no income to capitalize into a value."
        )

    cap_pct = _f(inputs.get("cap_rate_pct"))
    price = implied_price(noi, cap_pct)

    range_pct = _f(inputs.get("range_pct"))
    if range_pct is None:
        range_pct = DEFAULT_RANGE_PCT

    return {
        "inputs": dict(inputs),
        "buildup": buildup,
        "noi": noi,
        "noi_provenance": provenance,
        "noi_provenance_label": PROVENANCE_LABELS[provenance],
        "noi_provenance_note": PROVENANCE_NOTES[provenance],
        "cap_rate_pct": cap_pct,
        "implied_price": price,
        "range": price_range(price, range_pct),
        "range_choices": RANGE_CHOICES,
        "grade": grade(inputs.get("asking_price"), price),
        "price_per_unit": (price / int(inputs["unit_count"]))
                          if _f(inputs.get("unit_count")) else None,
    }
