"""
FIRE Capital Tools - Shared form-input coercion.

Turning a text form field into a number, leniently: strips the formatting
people actually paste in ("$1,250,000", "6.5%") and returns None rather
than raising when the field is blank or unparseable, so a route can decide
what a missing value means instead of catching exceptions.

Extracted from tools/deal_dive.py and tools/rent_comps.py, which had
byte-identical private copies of both functions. Deal Analyzer would have
been the third, and it is the tool most sensitive to numeric coercion
being right, so the duplication stops here.

Deliberately dependency-free -- no Flask, no app imports -- so it can be
called from a blueprint, a pure-math module, or a test with no request
context, the same principle as tools/market_data_service.py.
"""

from __future__ import annotations


def to_float(value) -> float | None:
    """Parse a form value as a float, tolerating currency/percent
    decoration. Returns None for blank or unparseable input."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value.replace(",", "").replace("$", "").replace("%", ""))
    except ValueError:
        return None


def to_int(value) -> int | None:
    """Parse a form value as an int, tolerating thousands separators and a
    decimal tail ("12.0" -> 12). Returns None for blank or unparseable
    input."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(float(value.replace(",", "")))
    except ValueError:
        return None
