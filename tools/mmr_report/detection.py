import re

from tools.mmr_report.helpers import find_first_text, worksheet_contains
from tools.mmr_report.styles import AMBER, GREEN, PALE_AMBER, PALE_GREEN, PALE_RED, RED




def detect_resman(wb):
    """
    Detect ResMan exports using high-confidence sheet and header fingerprints.
    Rent Roll and Delinquency alone are not enough because Maple/Appfolio exports
    can use those same sheet names.
    """
    sheetnames = set(wb.sheetnames)
    fingerprints = 0

    if "Box Score" in sheetnames:
        fingerprints += 1
        box = wb["Box Score"]
        if worksheet_contains(box, "occupancy", "occ%", "% occ", max_rows=80, max_cols=40):
            fingerprints += 1
        if worksheet_contains(box, "box score", max_rows=8, max_cols=4):
            fingerprints += 1
        header_text = " ".join(str(box.cell(r, 1).value or "") for r in range(1, min(box.max_row, 8) + 1))
        if re.search(r"\b(apartments?|pointe|canyon|rock)\b", header_text, re.IGNORECASE):
            fingerprints += 1

    for name in ("Work Order Summary", "Rent Roll", "Delinquency"):
        if name in sheetnames:
            fingerprints += 1

    if "Work Order Summary" in sheetnames:
        wo = wb["Work Order Summary"]
        if worksheet_contains(wo, "work order summary", "number", "reported", max_rows=40, max_cols=12):
            fingerprints += 1

    has_resman_anchor = "Box Score" in sheetnames or "Work Order Summary" in sheetnames
    return has_resman_anchor and fingerprints >= 2




def detect_appfolio(wb):
    """Detect the Maple Valley/Appfolio-style workbook."""
    sheetnames = set(wb.sheetnames)
    expected_sheets = {
        "Cash Flow", "Work Order", "Tenant Tickler", "Vacancy",
        "Rent Roll", "Check Register", "Delinquency", "Deposit Register",
        "General Ledger",
    }
    fingerprints = len(expected_sheets & sheetnames)

    if "Cash Flow" in sheetnames and worksheet_contains(
        wb["Cash Flow"],
        "copy of cash flow - 12 month maple",
        "period range:",
        "active properties owned by:",
        max_rows=15,
        max_cols=6,
    ):
        fingerprints += 2
    if "Rent Roll" in sheetnames and worksheet_contains(
        wb["Rent Roll"],
        "maple rent roll",
        "bd/ba",
        "market rent",
        "lease to",
        max_rows=15,
        max_cols=16,
    ):
        fingerprints += 2
    if "Work Order" in sheetnames and worksheet_contains(
        wb["Work Order"],
        "copy of work order maple",
        "work order number",
        "current work order status:",
        max_rows=20,
        max_cols=18,
    ):
        fingerprints += 2
    if find_first_text(wb, lambda t: "KYTX Maple. LLC" in t or "Maple Valley Apartments" in t, max_rows=20, max_cols=16):
        fingerprints += 2

    return fingerprints >= 6




def detect_source_system(wb):
    if detect_resman(wb):
        return "Resman"
    if detect_appfolio(wb):
        return "Appfolio"
    return "Unrecognized Format"




def source_system_display(source_system):
    if source_system == "Resman":
        return "✓ Resman Format", GREEN, PALE_GREEN
    if source_system == "Appfolio":
        return "⚠ Appfolio Format", AMBER, PALE_AMBER
    return "✗ Unrecognized Format", RED, PALE_RED
