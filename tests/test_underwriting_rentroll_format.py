"""
Format validation for the rent-roll importer.

The bug these guard against was silent, not loud. An MMR (Weekly Property
Summary) export was accepted by the rent-roll importer: its first sheet is
"Available Units", which carries Unit and Market Rent columns, so the old
header check matched. That sheet lists only VACANT units and has no charge
lines at all, so every unit came back with an in-place rent of zero -- and
Underwriting went on to build a complete, plausible-looking model out of
nothing but vacant units.

Nothing raised. Nothing warned. The number was simply wrong.

The fix is a positive signature rather than a blocklist: a rent roll must
carry the Description/Amount charge-line pair, because that is where
in-place rent is actually summed from. A sheet without it cannot produce a
rent roll, only a shell.

These tests build the fixtures in memory rather than depending on files in
Downloads, so they still mean something on a machine that has never seen
an ERA export.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.underwriting_rentroll import (  # noqa: E402
    UnrecognizedRentRoll,
    _looks_like_mmr,
    parse_rent_roll_workbook,
)

RENT_ROLL_HEADER = ["Unit", "Type", "Sq. Feet", "Residents", "Status", "Market Rent",
                    "Ledger", "Description", "Amount", "Move In", "Lease Start",
                    "Lease End", "Move Out"]

# The MMR "Available Units" header: Unit and Market Rent are present, the
# charge-line columns are not. This is the shape that used to slip through.
AVAILABLE_UNITS_HEADER = ["Unit", "Unit Type", "Term", "Prior Rent", "Market Rent",
                          "Specials", "Total Charges", "Building - Floor",
                          "Square Feet", "Unit Status", "Days Vacant"]

MMR_SHEETS_LONG = ["Available Units", "Bank Deposit Register", "Box Score",
                   "Cash Flow Statement", "Delinquency", "Expiring Leases",
                   "General Ledger", "Rent Roll", "Work Order Summary"]
MMR_SHEETS_SHORT = ["Summary", "Cash Flow", "Work Order", "Tenant Tickler",
                    "Vacancy", "Rent Roll", "Check Register", "Delinquency",
                    "Deposit Register", "General Ledger"]


def _write(sheets: dict[str, list[list]]) -> str:
    """Write a workbook and return its path. sheets maps name -> rows."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name[:31])
        for r in rows:
            ws.append(r)
    path = Path(tempfile.mkdtemp()) / "book.xlsx"
    wb.save(path)
    return str(path)


def _real_rent_roll_rows():
    return [
        ["Test Apartments"], ["Some Property Management"], ["Rent Roll"],
        ["5/31/2026"], ["Printed"], [], ["Current"], [],
        RENT_ROLL_HEADER,
        # Mirrors the real Eagle Rock shape: base rent plus a HAP subsidy
        # (which counts toward in-place rent) and a Pet Rent line (which
        # deliberately does not -- it is an ancillary fee, not base rent).
        ["0101", "1/1 Classic", 690, "A Resident", "C", 1035,
         "Resident", "Rent", 310, None, None, None, None],
        [None, None, None, None, None, None, "Resident", "HAP Rent", 665],
        [None, None, None, None, None, None, "Resident", "Pet Rent", 25],
        [None, None, None, None, None, None, None, "Total", 1000],
        ["0102", "2/1 Classic", 850, "B Resident", "C", 1300,
         "Resident", "Rent", 1250, None, None, None, None],
    ]


def _available_units_rows():
    return [
        ["Test Apartments"], ["Some Property Management"], ["Available Units"],
        ["7/20/2026"], ["Printed"], [],
        AVAILABLE_UNITS_HEADER,
        ["0105", "1/1 Classic", "Market", 995, 1035, 0, 1035, "1 - 2", 690, "Ready", 17],
        ["0406", "2/1 Classic", "Market", 1250, 1300, 0, 1300, "4 - 1", 850, "Ready", 9],
    ]


class MmrIsRejectedTests(unittest.TestCase):
    """The regression itself."""

    def test_mmr_long_form_is_rejected_with_the_named_message(self):
        sheets = {"Available Units": _available_units_rows()}
        for s in MMR_SHEETS_LONG[1:]:
            sheets[s] = [[s]]
        with self.assertRaises(UnrecognizedRentRoll) as ctx:
            parse_rent_roll_workbook(_write(sheets))
        self.assertIn("Weekly Property Summary", str(ctx.exception))
        self.assertIn("rent roll", str(ctx.exception).lower())

    def test_mmr_short_form_dialect_is_also_named(self):
        """Maple Valley's export uses shorter sheet names. Both dialects
        must produce the helpful message, not the generic one."""
        sheets = {"Summary": _available_units_rows()}
        for s in MMR_SHEETS_SHORT[1:]:
            sheets[s] = [[s]]
        with self.assertRaises(UnrecognizedRentRoll) as ctx:
            parse_rent_roll_workbook(_write(sheets))
        self.assertIn("Weekly Property Summary", str(ctx.exception))

    def test_available_units_sheet_never_yields_units(self):
        """The precise failure: this sheet must not parse at all. Before the
        fix it returned vacant units with an in-place rent of zero."""
        with self.assertRaises(UnrecognizedRentRoll):
            parse_rent_roll_workbook(_write({"Available Units": _available_units_rows()}))

    def test_unit_and_market_rent_alone_are_not_sufficient(self):
        """The old signature. Kept as an explicit test so nobody restores
        it: these two columns appear on several ResMan reports."""
        rows = [["Unit", "Market Rent"], ["0101", 1035], ["0102", 1300]]
        with self.assertRaises(UnrecognizedRentRoll):
            parse_rent_roll_workbook(_write({"Sheet": rows}))


class RealRentRollStillWorksTests(unittest.TestCase):
    def test_real_layout_parses(self):
        result = parse_rent_roll_workbook(_write({"Sheet": _real_rent_roll_rows()}))
        self.assertEqual(result["unit_count"], 2)
        self.assertEqual(result["source_format"], "ResMan Rent Roll")

    def test_in_place_rent_comes_from_the_charge_lines(self):
        units = parse_rent_roll_workbook(
            _write({"Sheet": _real_rent_roll_rows()}))["units"]
        by_unit = {u["unit"]: u for u in units}
        # 310 Rent + 665 HAP Rent = 975. Pet Rent (25) is excluded as an
        # ancillary fee, and the Total row must not be double-counted.
        self.assertAlmostEqual(by_unit["0101"]["in_place_rent"], 975.0, places=2)
        self.assertAlmostEqual(by_unit["0102"]["in_place_rent"], 1250.0, places=2)

    def test_ancillary_fees_are_not_counted_as_rent(self):
        """Pet Rent is in the fixture precisely so this stays true -- a fee
        folded into in-place rent would overstate income on every unit."""
        units = parse_rent_roll_workbook(
            _write({"Sheet": _real_rent_roll_rows()}))["units"]
        self.assertNotAlmostEqual(
            {u["unit"]: u for u in units}["0101"]["in_place_rent"], 1000.0, places=2)

    def test_attributes_survive(self):
        u = parse_rent_roll_workbook(
            _write({"Sheet": _real_rent_roll_rows()}))["units"][0]
        self.assertEqual(u["unit_type"], "1/1 Classic")
        self.assertEqual(u["sqft"], 690)
        self.assertEqual(u["market_rent"], 1035)


class FailLoudOnAnythingElseTests(unittest.TestCase):
    """Same principle as the property-name fix: never guess."""

    def test_unrelated_workbook_is_rejected(self):
        rows = [["Account", "Jan", "Feb"], ["6000 Payroll", 100, 110]]
        with self.assertRaises(UnrecognizedRentRoll):
            parse_rent_roll_workbook(_write({"Sheet": rows}))

    def test_empty_workbook_is_rejected(self):
        with self.assertRaises(UnrecognizedRentRoll):
            parse_rent_roll_workbook(_write({"Sheet": [[]]}))

    def test_unopenable_file_is_rejected_not_crashed(self):
        p = Path(tempfile.mkdtemp()) / "not.xlsx"
        p.write_text("this is not a spreadsheet", encoding="utf-8")
        with self.assertRaises(UnrecognizedRentRoll):
            parse_rent_roll_workbook(str(p))

    def test_generic_message_explains_what_was_missing(self):
        rows = [["Unit", "Market Rent"], ["0101", 1035]]
        with self.assertRaises(UnrecognizedRentRoll) as ctx:
            parse_rent_roll_workbook(_write({"Sheet": rows}))
        msg = str(ctx.exception)
        self.assertIn("Description", msg)
        self.assertIn("Amount", msg)


class MmrDetectionTests(unittest.TestCase):
    def test_single_sheet_rent_roll_is_not_an_mmr(self):
        self.assertFalse(_looks_like_mmr(["Sheet"]))

    def test_one_marker_alone_is_not_enough(self):
        """A future rent-roll variant carrying a single 'Vacancy' tab must
        not be mislabelled."""
        self.assertFalse(_looks_like_mmr(["Sheet", "Vacancy"]))

    def test_three_markers_classify(self):
        self.assertTrue(_looks_like_mmr(["Box Score", "Delinquency", "Cash Flow Statement"]))

    def test_detection_is_case_and_space_insensitive(self):
        self.assertTrue(_looks_like_mmr(["  BOX SCORE ", "delinquency", "Cash Flow Statement"]))


if __name__ == "__main__":
    unittest.main()
