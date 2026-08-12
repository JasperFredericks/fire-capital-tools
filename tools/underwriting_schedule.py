"""
Per-year assumption schedules.

A scenario's income assumptions -- vacancy, concessions, bad debt, rent
growth -- are flat rates by default: one number applied to every year.
This module lets any of them vary year by year ("gas is up 2% next year
but 10% the year after"), without a scenario that has no schedule
behaving one bit differently.

── The absent case is the whole design ──────────────────────────────────

Every function here returns the flat rate when no schedule is present, by
construction rather than by a special case sprinkled through the callers.
resolve() with an empty schedule returns the scenario's own flat value for
every year, so the per-year path and the flat path are the same code
executing the same arithmetic. That is what makes "absent == byte
identical" testable rather than merely intended.

── Carry-forward ────────────────────────────────────────────────────────

A schedule shorter than the hold carries its LAST value forward, it does
not zero-fill. A five-year schedule on a seven-year hold means "5% and
then 5% thereafter" -- which is what an underwriter who typed five years
of assumptions meant. Zero-filling would silently model two years of no
rent growth and no vacancy, which is both wrong and flattering.

── Growth-rate convention ───────────────────────────────────────────────

A growth rate indexed at year t is the growth applied going FROM year t
INTO year t+1. So year 1's rate moves year 1 into year 2, and every field
the user fills in affects something -- including the last one, which
carries the final operating year into the exit year the sale capitalizes.

The alternative (year t's rate describes growth already applied to reach
year t) leaves year 1's field inert, because year 1 is the base. A form
field that does nothing is worse than a convention that needs stating.

Level assumptions -- vacancy, concessions, bad debt -- carry no such
subtlety: the value at year t simply applies during year t.
"""

from __future__ import annotations

import json
from typing import Any

# Per-year overrides the UI offers. Rent growth is a growth rate and
# follows the convention above; the other three are level rates.
LEVEL_FIELDS = ("vacancy_pct", "concessions_pct", "bad_debt_pct")
GROWTH_FIELDS = ("rent_growth_pct",)
SCHEDULE_FIELDS = LEVEL_FIELDS + GROWTH_FIELDS

# The form offers at most this many individual year fields. A hold longer
# than this still models correctly -- the last value carries forward.
MAX_SCHEDULE_YEARS = 12


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize(rows: list[dict[str, Any]] | None) -> dict[int, dict[str, float]]:
    """Schedule rows -> {year: {field: value}}, dropping blanks.

    A blank cell is not zero: it means "no override for this field in this
    year", and resolve() falls back to the flat rate for it. Storing it as
    0.0 would model a year of zero vacancy, which is a very different
    claim from leaving the box empty.
    """
    out: dict[int, dict[str, float]] = {}
    for row in rows or []:
        year = row.get("year")
        try:
            year = int(year)
        except (TypeError, ValueError):
            continue
        if year < 1:
            continue
        values = {}
        for field in SCHEDULE_FIELDS:
            value = _f(row.get(field))
            if value is not None:
                values[field] = value
        if values:
            out[year] = values
    return out


def resolve(schedule: dict[int, dict[str, float]] | None, field: str,
            flat_value: Any, year: int) -> float:
    """The effective rate for `field` in `year`.

    Resolution order, and the reason for each step:

      1. an explicit override for this exact year          -- the user said so
      2. the latest earlier year that overrides this field -- carry-forward
      3. the scenario's flat rate                          -- no schedule at all

    Step 3 is what makes an absent schedule identical to today's
    behaviour: every year resolves to the same flat number the flat path
    would have used.
    """
    flat = _f(flat_value) or 0.0
    if not schedule:
        return flat

    if year in schedule and field in schedule[year]:
        return schedule[year][field]

    # Carry the most recent earlier override forward.
    earlier = [y for y in schedule if y < year and field in schedule[y]]
    if earlier:
        return schedule[max(earlier)][field]

    return flat


def assumptions_for_year(scenario: dict[str, Any],
                         schedule: dict[int, dict[str, float]] | None,
                         year: int) -> dict[str, float]:
    """Every scheduled assumption resolved for one year."""
    return {field: resolve(schedule, field, scenario.get(field), year)
            for field in SCHEDULE_FIELDS}


def compound(rates: list[float]) -> float:
    """Compound a list of annual percentage rates into one factor.

    ── Why the uniform case is special-cased ────────────────────────────

    Compounding a constant rate by repeated multiplication and by a single
    pow() are equal in exact arithmetic but NOT in floating point: the
    loop rounds once per year, pow() rounds once. On a real scenario the
    two disagree in the last bit or two -- around 1e-10 of a dollar by
    year 5.

    That is immaterial as money and fatal as a guarantee. Every figure
    this system has already quoted, verified and committed to was produced
    by the pow() expression, so a per-year rebuild that reaches for the
    loop unconditionally would shift previously-confirmed numbers in their
    final digits for no modelling reason at all.

    So when every rate is the same -- which is exactly the case when
    nothing is scheduled -- this uses pow() and reproduces the old value
    bit for bit. Only a genuinely varying schedule takes the loop, and
    there is no prior value to preserve.
    """
    return compound_fractions([r / 100.0 for r in rates])


def compound_fractions(fractions: list[float]) -> float:
    """compound(), but taking rates already expressed as fractions.

    Exists so callers that already hold a fraction do not round-trip it
    through percent and back. That round-trip is not free: (g/100)*100/100
    is not always g in floating point, and the resulting last-bit drift is
    exactly what compound() is here to avoid.
    """
    if not fractions:
        return 1.0
    first = fractions[0]
    if all(f == first for f in fractions):
        return (1.0 + first) ** len(fractions)
    factor = 1.0
    for f in fractions:
        factor *= 1.0 + f
    return factor


def rent_growth_factor(scenario: dict[str, Any],
                       schedule: dict[int, dict[str, float]] | None,
                       year: int) -> float:
    """Cumulative rent growth from year 1 up to `year`.

    Year 1 is the base and is always 1.0. Reaching year t compounds the
    rates for years 1..t-1, following the convention in the module
    docstring.

    With no schedule every rate is the same flat g, so this returns
    (1+g)^(t-1) exactly -- bit for bit the expression the flat path used.
    """
    rates = [resolve(schedule, "rent_growth_pct",
                     scenario.get("rent_growth_pct"), t)
             for t in range(1, int(year))]
    return compound(rates)


# ── Per-expense-line growth schedules ────────────────────────────────────

def parse_line_schedule(raw: Any) -> list[float] | None:
    """A line's growth_schedule column -> a list of annual rates, or None.

    Stored as JSON so one nullable TEXT column carries the whole override
    without a second table for what is usually a handful of numbers on a
    handful of lines. None and an empty list both mean "no schedule",
    because a line that has never been scheduled and one whose schedule
    was cleared should behave identically.

    Malformed JSON returns None rather than raising: a corrupt override
    should degrade a line to its flat rate, not make the scenario
    unopenable.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, (list, tuple)):
        values = list(raw)
    else:
        try:
            values = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(values, list):
            return None

    out = []
    for value in values:
        parsed = _f(value)
        if parsed is None:
            return None
        out.append(parsed)
    return out or None


def dump_line_schedule(values: list[float] | None) -> str | None:
    """Inverse of parse_line_schedule, for storage."""
    if not values:
        return None
    return json.dumps([float(v) for v in values])


def line_growth_for_year(line: dict[str, Any], default_expense_growth_pct: Any,
                         year: int) -> float:
    """The growth rate carrying `line` from `year` into `year + 1`.

    Falls back, in order, to the line's own schedule, its flat growth_pct,
    then the scenario default -- the same precedence the flat path uses,
    with the schedule inserted above it.
    """
    schedule = parse_line_schedule(line.get("growth_schedule"))
    if schedule:
        idx = min(int(year), len(schedule)) - 1      # carry the last value forward
        return schedule[max(0, idx)] / 100.0

    own = line.get("growth_pct")
    if own is None:
        return (_f(default_expense_growth_pct) or 0.0) / 100.0
    return (_f(own) or 0.0) / 100.0


def line_amount_for_year(line: dict[str, Any], base_amount: float,
                         default_expense_growth_pct: Any, year: int) -> float:
    """`base_amount` grown to `year`.

    Compounds years 1..t-1 through compound(), so an unscheduled line at a
    single flat rate reproduces amount * (1+g)^(t-1) bit for bit -- see
    compound() for why that exactness is deliberate.
    """
    fractions = [line_growth_for_year(line, default_expense_growth_pct, t)
                 for t in range(1, int(year))]
    return float(base_amount) * compound_fractions(fractions)


def has_any_schedule(schedule: dict[int, dict[str, float]] | None,
                     expense_lines: list[dict[str, Any]] | None) -> bool:
    """True when anything on this scenario actually overrides a flat rate.

    Used to decide whether to tell the user the model is running on
    per-year assumptions -- not to decide which code path runs, since
    both paths are the same code.
    """
    if schedule:
        return True
    return any(parse_line_schedule(l.get("growth_schedule"))
               for l in (expense_lines or []))
