"""
FIRE Capital Tools - Rent roll parsing for Underwriting.

Turns a ResMan rent roll export into per-unit lines: unit, type, square
footage, status, in-place rent, market rent, and lease dates.

Why this is new code rather than reuse. mmr_report.parsers.parse_rent_roll
exists, but it answers a different question -- it returns two scalars,
{"total_rental", "avg_rent"}, for an operations report, and takes an MMR
worksheet plus an occupied count from the box score. Nothing in the MMR
package captures per-unit rents, square footage or lease dates, and
parse_available_units only covers the vacant/notice/holding subset. So the
extraction is new; the *helpers* are not -- the cell utilities and the
rent-line allowlist below are imported from the MMR package rather than
reimplemented, because they already encode real knowledge about which
ledger lines count as rent (HAP and other subsidies do, renters insurance
does not, concessions offset).

ResMan only for this beta. Any other layout raises UnrecognizedRentRoll
rather than guessing -- the Scorecard Pro property-name collision came
from a parser that guessed when it did not recognize a file, and the same
mistake here would silently under- or over-state income for the whole
model.

Structure of a real export (confirmed against Eagle Rock, May 2026):

    row 8   Unit | Type | Sq. Feet | Residents | Status | Market Rent | ...
                 ... | Ledger | Description | Amount | Move In | Lease Start | Lease End
    row 9   0101 | 1/1 Upgraded | 690 | ... | C | 1065 | Resident | Rent   | 310
    row 10                                                        | HAP Rent| 665
    row 11                                                        | Total   | 975

Each unit spans several rows: one carrying the unit's attributes and its
first charge line, then further charge lines, then a Total. In-place rent
is summed from the charge lines that _is_rent_line accepts rather than
read off the Total row, so a unit whose ledger mixes rent with
non-rent charges is still counted correctly.

The Unit column is merged in the export, so its value lands one column to
the left of its own header. That is handled explicitly below rather than
by trusting the header index.
"""

from __future__ import annotations

import datetime
from typing import Any

import openpyxl

from tools.mmr_report.helpers import (
    coerce_num,
    find_col,
    find_col_contains,
    looks_like_unit_value,
    norm,
    rows_of,
    safe_get,
)
from tools.mmr_report.parsers import _is_rent_line

MAX_HEADER_SCAN_ROWS = 40

# The export ends with a charge-type summary block ("Total Charges", then
# "Rent", "HAP Rent", "Pet Rent", ... with totals). Those labels satisfy
# looks_like_unit_value(), so without an explicit stop the summary reads as
# 27 extra phantom units on a real 92-unit Eagle Rock roll -- inflating the
# unit count and dragging every per-unit average toward zero.
_SUMMARY_TERMINATORS = ("total charges", "total credits", "grand total",
                        "summary", "charge summary")


class UnrecognizedRentRoll(ValueError):
    """Raised when the file is not a rent roll layout this parser
    understands. The message is written to be shown to the user."""


def _header_index(rows) -> int | None:
    """Row index of the column header band. Requires both a Unit column and
    a Market Rent column -- either alone appears in other ResMan reports, so
    demanding both is what distinguishes a rent roll from, say, an Available
    Units export."""
    for idx, row in enumerate(rows[:MAX_HEADER_SCAN_ROWS]):
        has_unit = find_col(row, "unit") is not None
        has_market = (find_col(row, "market rent") is not None
                      or find_col_contains(row, "market rent") is not None)
        if has_unit and has_market:
            return idx
    return None


def _as_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _unit_value(row, unit_col):
    """The unit number. Checked at the header's own column and the one to
    its left, because the export merges the Unit cell and openpyxl reports a
    merged value at the range's anchor."""
    for col in (unit_col - 1, unit_col, unit_col + 1):
        if col is None or col < 0:
            continue
        v = safe_get(row, col)
        if looks_like_unit_value(v):
            return str(v).strip()
    return None


def parse_rent_roll_workbook(path) -> dict[str, Any]:
    """Parse a ResMan rent roll .xlsx into per-unit lines.

    Raises UnrecognizedRentRoll if the layout is not recognized or yields no
    units -- never returns a partially-guessed result."""
    try:
        wb = openpyxl.load_workbook(str(path), data_only=True)
    except Exception as exc:
        raise UnrecognizedRentRoll(f"Could not open the rent roll file: {exc}") from exc

    ws = wb[wb.sheetnames[0]]
    rows = rows_of(ws)
    header_idx = _header_index(rows)
    if header_idx is None:
        raise UnrecognizedRentRoll(
            "This does not look like a ResMan rent roll — no row with both a "
            "'Unit' and a 'Market Rent' column was found. Only ResMan rent roll "
            "exports are supported in this beta; upload one of those, or enter "
            "the rent roll manually."
        )

    header = rows[header_idx]
    cols = {
        "unit": find_col(header, "unit"),
        "type": find_col(header, "type", "unit type"),
        "sqft": find_col(header, "sq. feet", "sq feet", "sqft", "square feet"),
        "status": find_col(header, "status"),
        "market": find_col(header, "market rent") or find_col_contains(header, "market rent"),
        "description": find_col(header, "description"),
        "amount": find_col(header, "amount"),
        "lease_start": find_col(header, "lease start"),
        "lease_end": find_col(header, "lease end", "lease expires"),
        "move_in": find_col(header, "move in"),
        "move_out": find_col(header, "move out"),
    }
    if cols["unit"] is None or cols["market"] is None:
        raise UnrecognizedRentRoll("Rent roll header found but its Unit/Market Rent columns could not be read.")

    units: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    warnings: list[str] = []

    def close(u):
        if u is not None:
            u["in_place_rent"] = round(u.pop("_rent_accum", 0.0), 2)
            units.append(u)

    for row in rows[header_idx + 1:]:
        first = norm(safe_get(row, 0) or "")
        if first in _SUMMARY_TERMINATORS:
            break                      # per-unit detail is over

        unit_val = _unit_value(row, cols["unit"])
        # A genuine unit row carries at least one unit attribute alongside
        # its number. Requiring corroboration keeps stray labels elsewhere in
        # the sheet from opening a phantom unit block.
        if unit_val and not any((
            safe_get(row, cols["type"]),
            safe_get(row, cols["sqft"]),
            safe_get(row, cols["status"]),
            coerce_num(safe_get(row, cols["market"]), default=None) is not None,
        )):
            unit_val = None

        if unit_val:
            close(current)
            current = {
                "unit": unit_val,
                "unit_type": (str(safe_get(row, cols["type"]) or "").strip() or None),
                "sqft": coerce_num(safe_get(row, cols["sqft"]), default=None),
                "status": (str(safe_get(row, cols["status"]) or "").strip() or None),
                "market_rent": coerce_num(safe_get(row, cols["market"]), default=None),
                "lease_start": _as_date(safe_get(row, cols["lease_start"])),
                "lease_end": _as_date(safe_get(row, cols["lease_end"])),
                "move_in": _as_date(safe_get(row, cols["move_in"])),
                "move_out": _as_date(safe_get(row, cols["move_out"])),
                "_rent_accum": 0.0,
            }

        if current is None:
            continue

        desc = safe_get(row, cols["description"])
        amt = coerce_num(safe_get(row, cols["amount"]), default=None)
        if desc is None or amt is None:
            continue
        label = str(desc).strip()
        if norm(label) in ("total", "totals"):
            continue          # summary line; the charge lines above already counted
        if _is_rent_line(label, amt):
            current["_rent_accum"] += amt

    close(current)

    if not units:
        raise UnrecognizedRentRoll(
            "The rent roll header was recognized but no unit rows could be read "
            "from it. Check the file is a full rent roll export rather than a "
            "summary."
        )

    missing_market = sum(1 for u in units if u["market_rent"] is None)
    if missing_market:
        warnings.append(f"{missing_market} unit(s) have no market rent on file; "
                        f"their in-place rent is used for gross potential rent instead.")
    missing_sqft = sum(1 for u in units if u["sqft"] is None)
    if missing_sqft:
        warnings.append(f"{missing_sqft} unit(s) have no square footage; they are "
                        f"excluded from average-sqft figures rather than counted as zero.")

    return {
        "units": units,
        "unit_count": len(units),
        "warnings": warnings,
        "source_format": "ResMan Rent Roll",
    }
