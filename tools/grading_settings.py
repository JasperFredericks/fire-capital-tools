"""
FIRE Capital Tools - user-configured grading bands.

Sits between app_settings (generic storage) and quick_analyzer_math
(pure arithmetic). Neither of those knows about the other; this module is
the only place that does.

WHAT IS CONFIGURABLE, AND WHAT IS NOT

Three numbers: the upper bound of green, of yellow, and of orange, each
expressed as how far the asking price may sit above the implied price.
Red is whatever is left, and is deliberately not configurable -- a band
that catches everything above the last threshold has no upper bound to
set, and offering one would invite a value that leaves a gap nothing
falls into.

The band KEYS, their order, and their labels are also fixed. Renaming
"green" or adding a fifth colour is a different feature; letting the
numbers move is what was asked for.

VALIDATION IS ABOUT MEANING, NOT TYPES

An out-of-order set of bands does not error at render time -- it produces
a grade that silently never returns yellow, because the first matching
band wins and green already covered it. That is the failure this
validation exists to prevent, and it is why "green must be tighter than
yellow" is enforced here rather than trusted.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from tools import app_settings
from tools import quick_analyzer_math as calc

NAMESPACE = "quick_analyzer_grading"
KEY_BANDS = "bands"

# The three configurable bounds, best to worst. Red is the open-ended
# remainder and has no bound to set.
CONFIGURABLE = (calc.GRADE_GREEN, calc.GRADE_YELLOW, calc.GRADE_ORANGE)

# Bounds outside this range are refused. Not arbitrary: a negative bound
# would mean "green requires the asking price to be BELOW the implied
# price by some margin", which is a coherent policy and is allowed; a
# bound past 200% means the band covers essentially everything and the
# grade stops distinguishing anything.
MIN_BOUND = -100.0
MAX_BOUND = 200.0


class InvalidThresholds(ValueError):
    """Raised with a message written to be shown to the user."""


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def validate(green: Any, yellow: Any, orange: Any) -> tuple[float, float, float]:
    """Three bounds, in order, or a message explaining what is wrong."""
    values = []
    for label, raw in (("Green", green), ("Yellow", yellow), ("Orange", orange)):
        num = _num(raw)
        if num is None:
            raise InvalidThresholds(
                f"{label} needs a number — the percentage the asking price "
                f"may sit above the implied price.")
        if not (MIN_BOUND <= num <= MAX_BOUND):
            raise InvalidThresholds(
                f"{label} must be between {MIN_BOUND:g}% and {MAX_BOUND:g}%. "
                f"{num:g}% is outside anything that would grade meaningfully.")
        values.append(num)

    g, y, o = values
    # Strictly ascending. Equal bounds are refused as well as inverted
    # ones: two bands with the same bound means the second can never be
    # reached, which is an invisible failure rather than a visible one.
    if not (g < y):
        raise InvalidThresholds(
            f"Green ({g:g}%) must be tighter than Yellow ({y:g}%). As written, "
            f"nothing could ever grade Yellow — Green would already have "
            f"caught it.")
    if not (y < o):
        raise InvalidThresholds(
            f"Yellow ({y:g}%) must be tighter than Orange ({o:g}%). As written, "
            f"nothing could ever grade Orange.")
    return (g, y, o)


def bands_from(green: float, yellow: float, orange: float
               ) -> tuple[calc.GradeBand, ...]:
    """Build the band tuple, with meanings that state the real numbers.

    The stock meanings quote "5%" and "5-15%" as literals. Leaving those
    in place while the bounds moved would put a sentence on screen that
    contradicts the number beside it, which is worse than no sentence.
    """
    return (
        calc.GradeBand(
            calc.GRADE_GREEN, "At or below target", green,
            f"At or under {green:g}% above the price this NOI supports at "
            f"your cap rate."),
        calc.GradeBand(
            calc.GRADE_YELLOW, "Slightly above target", yellow,
            f"Between {green:g}% and {yellow:g}% above the implied price — "
            f"close enough to be worth a look."),
        calc.GradeBand(
            calc.GRADE_ORANGE, "Above target", orange,
            f"Between {yellow:g}% and {orange:g}% above the implied price — "
            f"needs a story to work."),
        calc.GradeBand(
            calc.GRADE_RED, "Well above target", None,
            f"More than {orange:g}% above the implied price at this cap rate."),
    )


def load(conn: sqlite3.Connection) -> dict[str, Any]:
    """The bands to grade with, and where they came from.

    With nothing configured this returns the module defaults and the
    unconfirmed provenance -- byte-identical to the behaviour before this
    feature existed, which is the point.
    """
    stored = app_settings.get(conn, NAMESPACE, KEY_BANDS)
    if not isinstance(stored, dict):
        return {
            "configured": False,
            "bands": calc.GRADE_BANDS,
            "provenance": calc.GRADE_PROVENANCE,
            "disclaimer": calc.GRADE_DISCLAIMERS[calc.GRADE_PROVENANCE],
            "values": {k: b.max_over_pct for k, b in
                       zip(CONFIGURABLE, calc.GRADE_BANDS)},
            "updated_at": None,
        }
    try:
        g, y, o = validate(stored.get("green"), stored.get("yellow"),
                           stored.get("orange"))
    except InvalidThresholds:
        # A stored value that no longer validates falls back rather than
        # raising. The tool keeps working and shows the placeholder
        # disclaimer, which is the honest description of what it is then
        # using.
        return load_defaults()
    return {
        "configured": True,
        "bands": bands_from(g, y, o),
        "provenance": calc.PROVENANCE_USER,
        "disclaimer": calc.GRADE_DISCLAIMERS[calc.PROVENANCE_USER],
        "values": {"green": g, "yellow": y, "orange": o},
        "updated_at": app_settings.updated_at(conn, NAMESPACE, KEY_BANDS),
    }


def load_defaults() -> dict[str, Any]:
    return {
        "configured": False,
        "bands": calc.GRADE_BANDS,
        "provenance": calc.GRADE_PROVENANCE,
        "disclaimer": calc.GRADE_DISCLAIMERS[calc.GRADE_PROVENANCE],
        "values": {k: b.max_over_pct for k, b in
                   zip(CONFIGURABLE, calc.GRADE_BANDS)},
        "updated_at": None,
    }


def save(conn: sqlite3.Connection, green: Any, yellow: Any, orange: Any) -> None:
    g, y, o = validate(green, yellow, orange)
    app_settings.set_value(conn, NAMESPACE, KEY_BANDS,
                           {"green": g, "yellow": y, "orange": o})


def clear(conn: sqlite3.Connection) -> bool:
    """Back to the placeholders, exactly as they were."""
    return app_settings.clear(conn, NAMESPACE, KEY_BANDS) > 0
