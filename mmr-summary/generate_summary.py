#!/usr/bin/env python3
"""
FIRE Capital - MMR Summary Generator
Reads a Resman MMR .xlsx file and writes/replaces the 'Summary' tab.

Usage:
    python generate_summary.py "ERA_MMR_-_06_15_26.xlsx"
"""

import sys
import re
from pathlib import Path
from datetime import datetime, timedelta

import io

import matplotlib
matplotlib.use("Agg")   # headless — no display needed
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils.datetime import from_excel
from openpyxl.utils import get_column_letter

# ── Colours ────────────────────────────────────────────────────────────────

DARK_BLUE  = "1F4E79"
LIGHT_BLUE = "D6E4F0"
PALE_BLUE  = "EBF5FB"
MID_GRAY   = "595959"
LT_GRAY    = "808080"
GREEN      = "548235"
PALE_GREEN = "E2F0D9"
AMBER      = "C65911"
PALE_AMBER = "FCE4D6"
RED        = "C00000"
PALE_RED   = "F4CCCC"

# ── Style helpers ──────────────────────────────────────────────────────────

def _fill(hex_color):
    return PatternFill(start_color=hex_color, end_color=hex_color, fill_type="solid")

def _side(style="thin", color="BFBFBF"):
    return Side(style=style, color=color)

def _box():
    s = _side()
    return Border(top=s, left=s, right=s, bottom=s)

_FONTS = {
    "title":   Font(name="Calibri", bold=True,  color=DARK_BLUE, size=16),
    "sub":     Font(name="Calibri", bold=True,  color=MID_GRAY,  size=12),
    "meta":    Font(name="Calibri", italic=True, color=LT_GRAY,  size=9),
    "hdr":     Font(name="Calibri", bold=True,  color="FFFFFF",  size=10),
    "col_hdr": Font(name="Calibri", bold=True,  color=DARK_BLUE, size=10),
    "label":   Font(name="Calibri", bold=True,                   size=10),
    "data":    Font(name="Calibri",                               size=10),
}

C = Alignment(horizontal="center", vertical="center", wrap_text=True)
L = Alignment(horizontal="left",   vertical="center", wrap_text=False)
R = Alignment(horizontal="right",  vertical="center")
TL = Alignment(horizontal="left",  vertical="top",    wrap_text=True)


def wc(ws, row, col, value, font="data", fill=None, align=L, border=None, num_fmt=None):
    """Write a single cell with optional styling."""
    cell = ws.cell(row=row, column=col, value=value)
    cell.font      = _FONTS.get(font, _FONTS["data"]) if isinstance(font, str) else font
    cell.alignment = align
    if fill:    cell.fill   = fill
    if border:  cell.border = border
    if num_fmt: cell.number_format = num_fmt
    return cell


def merge_wc(ws, row, c1, c2, value, font="hdr", fill=None, align=C, border=None):
    """Write a merged cell."""
    ws.merge_cells(start_row=row, start_column=c1, end_row=row, end_column=c2)
    return wc(ws, row, c1, value, font=font, fill=fill, align=align, border=border)


def section_hdr(ws, row, title, c1=2, c2=9):
    """Full-width dark-blue section banner."""
    merge_wc(ws, row, c1, c2, title, font="hdr", fill=_fill(DARK_BLUE), align=C)


def col_hdr(ws, row, col, label):
    """Light-blue column header cell."""
    wc(ws, row, col, label, font="col_hdr", fill=_fill(LIGHT_BLUE), align=C, border=_box())


def data_row(ws, row, col, value, zebra=False, align=L, num_fmt=None):
    """Standard data cell with optional zebra shading."""
    fill = _fill(PALE_BLUE) if zebra else None
    wc(ws, row, col, value, font="data", fill=fill, align=align, border=_box(), num_fmt=num_fmt)

# ── Format helpers ─────────────────────────────────────────────────────────

def fmt_pct(v):
    if isinstance(v, (int, float)):
        return f"{v:.1%}"
    return str(v or "")


def fmt_date(v):
    if isinstance(v, datetime):
        return v.strftime("%m/%d/%Y")
    if isinstance(v, (int, float)) and v > 0:
        try:
            return from_excel(v).strftime("%m/%d/%Y")
        except Exception:
            pass
    if isinstance(v, str) and re.match(r"\d{1,2}/\d{1,2}/\d{4}", v):
        return v
    return str(v or "")


def fmt_month(v):
    if isinstance(v, datetime):
        return v.strftime("%B %Y")
    return str(v or "")

# ── Sheet reader ───────────────────────────────────────────────────────────

def rows_of(ws):
    """Return all cell values as a flat list-of-lists (0-indexed)."""
    return [
        [ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
        for r in range(1, ws.max_row + 1)
    ]

# ── Parse helpers ──────────────────────────────────────────────────────────

def norm(s):
    """Normalize for comparison: collapse whitespace, strip, and lowercase."""
    return re.sub(r"\s+", " ", str(s or "").replace("\xa0", " ")).strip().lower()

def safe_get(row, i, default=None):
    """Access row[i] without raising IndexError."""
    try:
        return row[i]
    except (IndexError, TypeError):
        return default


def safe_row(rows, i):
    """Access rows[i] without raising IndexError."""
    try:
        return rows[i]
    except (IndexError, TypeError):
        return []

def coerce_pct(v):
    """
    Convert various percent representations to a 0–1 float.
      0.947   → 0.947
      "94.7%" → 0.947
      "0.947" → 0.947
      94.7    → 0.947  (value > 1 treated as already-in-percent form)
    Returns None if conversion fails.
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if f <= 1.0 else f / 100.0
    s = str(v).strip().rstrip("%")
    try:
        f = float(s)
        return f if f <= 1.0 else f / 100.0
    except ValueError:
        return None

def coerce_num(v, default=0.0):
    """Convert a value to float, returning default on failure."""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip()
    if not s:
        return default
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    s = s.replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        val = float(s)
        return -val if neg else val
    except (ValueError, TypeError):
        return default

def find_col(header_row, *names):
    """First column index (0-based) whose normalized header exactly matches any of names."""
    name_set = {n.lower() for n in names}
    for c, h in enumerate(header_row):
        if norm(h) in name_set:
            return c
    return None

def find_col_contains(header_row, *substrings):
    """First column index whose normalized header contains any of the given substrings."""
    subs = [s.lower() for s in substrings]
    for c, h in enumerate(header_row):
        hn = norm(h)
        if any(sub in hn for sub in subs):
            return c
    return None

def debug_box_score_preleases(header_row, total_row, vacant_prelease_col, notice_prelease_col):
    """Print Box Score occupancy headers and the chosen Preleases sources."""
    print("  Box Score occupancy headers:")
    for c, header in enumerate(header_row, 1):
        if header is not None and str(header).strip():
            print(f"    {get_column_letter(c)}: {str(header).replace(chr(10), ' ')}")

    def describe(col):
        if col is None:
            return "missing", 0
        header = str(safe_get(header_row, col) or "").replace(chr(10), " ")
        value = coerce_num(safe_get(total_row, col), default=0)
        return f"{int(value)} from {get_column_letter(col + 1)} ({header})", int(value)

    vacant_desc, vacant_value = describe(vacant_prelease_col)
    notice_desc, notice_value = describe(notice_prelease_col)
    print(f"  Preleases picked: Vacant Pre-Leased = {vacant_desc}")
    print(f"  Preleases picked: On-Notice Pre-Leased = {notice_desc}")
    print(f"  Preleases total: {vacant_value} + {notice_value} = {vacant_value + notice_value}")


def coerce_excel_date(value):
    """Convert Excel date values, date strings, or serials to datetime."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)) and value > 0:
        try:
            return from_excel(value)
        except Exception:
            return None
    if isinstance(value, str):
        text = value.strip()
        for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                pass
    return None


def describe_raw(value):
    return f"{value!r}<{type(value).__name__}>"


def find_projected_occupancy_header(rows, start_idx):
    for i in range(start_idx, min(len(rows), start_idx + 8)):
        row = rows[i]
        if find_col(row, "date") is None:
            continue
        if find_col(row, "occupied units") is None and find_col(row, "occupancy", "% occupancy", "% occupied") is None:
            continue
        return i, row
    return None, None


def parse_projected_occupancy(rows, start_idx, total_units):
    header_idx, header = find_projected_occupancy_header(rows, start_idx)
    if header_idx is None:
        print("WARNING: Projected Occupancy header row not found.")
        return []

    date_col = find_col(header, "date")
    occ_u_col = find_col(header, "occupied units")
    pct_col = find_col(header, "occupancy", "% occupancy", "% occupied")

    print("  Projected Occupancy headers:")
    for c, value in enumerate(header, 1):
        if value is not None and str(value).strip():
            print(f"    {get_column_letter(c)}: {str(value).replace(chr(10), ' ')}")

    proj_occ = []
    for row_num, drow in enumerate(rows[header_idx + 1:], header_idx + 2):
        raw_date = safe_get(drow, date_col)
        raw_occ_units = safe_get(drow, occ_u_col) if occ_u_col is not None else None
        raw_pct = safe_get(drow, pct_col) if pct_col is not None else None

        dt = coerce_excel_date(raw_date)
        if dt is None:
            if raw_date and not is_junk_row(drow):
                print(f"    stop row {row_num}: date={describe_raw(raw_date)}")
            break

        occ_u = coerce_num(raw_occ_units, default=None) if occ_u_col is not None else None
        pct = coerce_pct(raw_pct) if pct_col is not None else None
        if pct is None and total_units and occ_u is not None:
            pct = occ_u / total_units
        if occ_u is None and total_units and pct is not None:
            occ_u = pct * total_units

        print(
            f"    row {row_num}: raw_date={describe_raw(raw_date)}, "
            f"parsed_date={fmt_date(dt)}, raw_occupied_units={describe_raw(raw_occ_units)}, "
            f"raw_occupancy={describe_raw(raw_pct)}, parsed_pct={pct}"
        )

        if pct is None:
            continue
        occ_display = int(occ_u) if isinstance(occ_u, float) and occ_u.is_integer() else occ_u
        proj_occ.append({"date": dt, "occ": occ_display, "pct": pct})

    print(f"  Projected Occupancy rows extracted: {len(proj_occ)}")
    if 0 < len(proj_occ) < 20:
        last = proj_occ[-1]
        print(
            "  Projected Occupancy extension: carrying forward "
            f"{last['occ']} occupied units / {last['pct']:.4%} through 20 weekly points"
        )
        while len(proj_occ) < 20:
            next_date = proj_occ[-1]["date"] + timedelta(days=7)
            proj_occ.append({
                "date": next_date,
                "occ": last["occ"],
                "pct": last["pct"],
                "carried_forward": True,
            })
            print(
                f"    extended row {len(proj_occ)}: parsed_date={fmt_date(next_date)}, "
                f"occupied_units={last['occ']}, parsed_pct={last['pct']}"
            )

    return proj_occ


def is_junk_row(row):
    """True for blank rows, copyright lines, or ResMan footer rows."""
    first = safe_get(row, 0)
    if first is None:
        return True
    s = str(first).strip()
    return not s or s.startswith("©") or s.startswith("*") or "ResMan" in s or s.startswith("Printed")


def nonempty_values(row):
    """Return non-empty values from a row."""
    return [v for v in row if v is not None and str(v).strip()]


def looks_like_group_header(row):
    """A ResMan section/status header usually has text only in the first cell."""
    first = safe_get(row, 0)
    if not isinstance(first, str) or not first.strip():
        return False
    return len(nonempty_values(row[1:])) == 0


def looks_like_unit_value(v):
    """Accept string or numeric unit IDs while rejecting obvious labels/footers."""
    if v is None:
        return False
    s = str(v).strip()
    if not s or s.startswith("*") or s.startswith("©"):
        return False
    return norm(s) not in {"unit", "total", "totals", "grand total"}


def worksheet_contains(ws, *phrases, max_rows=30, max_cols=30):
    """True if any of the phrases appears in the top-left area of a worksheet."""
    wanted = [norm(p) for p in phrases]
    for row in ws.iter_rows(
        min_row=1,
        max_row=min(ws.max_row, max_rows),
        min_col=1,
        max_col=min(ws.max_column, max_cols),
        values_only=True,
    ):
        for value in row:
            text = norm(value)
            if text and any(p in text for p in wanted):
                return True
    return False


def find_first_text(wb, predicate, max_rows=30, max_cols=12):
    """Find the first top-left workbook cell whose text satisfies predicate."""
    for ws in wb.worksheets:
        for row in ws.iter_rows(
            min_row=1,
            max_row=min(ws.max_row, max_rows),
            min_col=1,
            max_col=min(ws.max_column, max_cols),
            values_only=True,
        ):
            for value in row:
                text = str(value or "").strip()
                if text and predicate(text):
                    return text
    return ""


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


def extract_appfolio_box_score(wb, source_system):
    bs = default_box_score()
    if source_system == "Appfolio":
        prop = find_first_text(
            wb,
            lambda t: "Maple Valley Apartments" in t,
            max_rows=25,
            max_cols=16,
        )
        if not prop:
            prop = find_first_text(
                wb,
                lambda t: "Owned By: KYTX Maple. LLC" in t,
                max_rows=25,
                max_cols=16,
            )
        if "Maple Valley Apartments" in prop:
            prop = prop.split(" - ")[0].strip()
        elif "KYTX Maple. LLC" in prop:
            prop = "Maple Valley"
        bs["property_name"] = prop or "Maple Valley"
        bs["date_range"] = find_first_text(
            wb,
            lambda t: t.startswith("As of:") or t.startswith("Date Range:") or t.startswith("Period Range:"),
            max_rows=15,
            max_cols=6,
        )
        bs["printed"] = find_first_text(wb, lambda t: t.startswith("Exported On:"), max_rows=8, max_cols=4).replace("Exported On:", "").strip()
    return bs


# ══════════════════════════════════════════════════════════════════════════
#  PARSERS
# ══════════════════════════════════════════════════════════════════════════

def parse_box_score(ws):
    rows = rows_of(ws)

    # ── Header block ──────────────────────────────────────────────────────
    prop_name  = str(safe_get(safe_row(rows, 0), 0) or safe_get(safe_row(rows, 5), 0) or "").strip()
    date_range = str(safe_get(safe_row(rows, 3), 0) or "").strip()
    raw_print  = safe_get(safe_row(rows, 4), 0)
    if isinstance(raw_print, datetime):
        printed = raw_print.strftime("%m/%d/%Y")
    else:
        printed = str(raw_print or "").replace("Printed ", "").strip()

    # ── Occupancy table ───────────────────────────────────────────────────
    total_units = occupied = 0
    pct_occ = 0.0
    prelease_count = None
    vacant_prelease_count = None
    notice_prelease_count = None

    found_occ_table = False
    for i, row in enumerate(rows):
        row_norms = [norm(h) for h in row]
        if "unit type" not in row_norms or "total units" not in row_norms:
            continue
        c_units    = find_col(row, "total units")
        c_occ      = find_col(row, "occ", "occupied")
        c_pct      = find_col(row, "% occ", "% occupied", "occ %", "pct occ", "% occ.")
        c_vacant_prelease = find_col(row, "vacant pre-leased", "vacant preleased", "vacant pre leased")
        c_notice_prelease = find_col(
            row,
            "on-notice pre-leased",
            "on notice pre-leased",
            "on-notice preleased",
            "on notice preleased",
            "on-notice pre leased",
            "on notice pre leased",
        )
        if c_units is None or c_occ is None:
            continue
        for trow in rows[i + 1:]:
            units_val = coerce_num(safe_get(trow, c_units), default=None)
            if norm(safe_get(trow, 0)) == "total" and units_val is not None:
                total_units    = int(units_val)
                occupied       = int(coerce_num(safe_get(trow, c_occ), default=0))
                raw_pct        = safe_get(trow, c_pct) if c_pct is not None else None
                pct            = coerce_pct(raw_pct)
                pct_occ        = pct if pct is not None else (occupied / total_units if total_units else 0.0)
                if c_vacant_prelease is None and c_notice_prelease is None:
                    prelease_count = None
                else:
                    vacant_preleases = int(coerce_num(safe_get(trow, c_vacant_prelease), default=0)) if c_vacant_prelease is not None else 0
                    notice_preleases = int(coerce_num(safe_get(trow, c_notice_prelease), default=0)) if c_notice_prelease is not None else 0
                    vacant_prelease_count = vacant_preleases
                    notice_prelease_count = notice_preleases
                    prelease_count = vacant_preleases + notice_preleases
                debug_box_score_preleases(row, trow, c_vacant_prelease, c_notice_prelease)
                found_occ_table = True
                break
        if found_occ_table:
            break

    if not found_occ_table:
        print("WARNING: Occupancy table not found in Box Score.")

    vacant = total_units - occupied

    # ── On-Notice count ────────────────────────────────────────────────────
    on_notice = 0
    for i, row in enumerate(rows):
        if row[0] and "on notice summary" in norm(row[0]):
            for j, row2 in enumerate(rows[i + 1:], i + 1):
                if norm(safe_get(row2, 0)) == "unit type":
                    ntv_col = find_col_contains(row2, "on notice")
                    for trow in rows[j + 1:]:
                        if norm(safe_get(trow, 0)) == "total":
                            on_notice = int(coerce_num(safe_get(trow, ntv_col), default=0)) if ntv_col is not None else 0
                            break
                    break
            break

    # ── Applications / Renewals ────────────────────────────────────────────
    applied = approved = signed = 0
    for i, row in enumerate(rows):
        if row[0] and "applications and renewals" in norm(row[0]):
            for j, arow in enumerate(rows[i + 1:], i + 1):
                row_norms = [norm(h) for h in arow]
                if "applied" in row_norms and "approved" in row_norms:
                    ca   = find_col(arow, "applied")
                    capp = find_col(arow, "approved")
                    cs   = find_col(arow, "signed")
                    for trow in rows[j + 1:]:
                        if norm(safe_get(trow, 0)) == "total":
                            applied  = int(coerce_num(safe_get(trow, ca),   default=0)) if ca   is not None else 0
                            approved = int(coerce_num(safe_get(trow, capp), default=0)) if capp is not None else 0
                            signed   = int(coerce_num(safe_get(trow, cs),   default=0)) if cs   is not None else 0
                            break
                    break
            break

    # ── Projected Occupancy ───────────────────────────────────────────────
    proj_occ = []
    for i, row in enumerate(rows):
        if any(norm(value) == "projected occupancy" for value in row):
            proj_occ = parse_projected_occupancy(rows, i + 1, total_units)
            break

    return {
        "property_name":   prop_name,
        "date_range":      date_range,
        "printed":         printed,
        "total_units":     total_units,
        "occupied":        occupied,
        "vacant":          vacant,
        "pct_occ":         pct_occ,
        "prelease_count":  prelease_count,
        "vacant_prelease_count": vacant_prelease_count,
        "notice_prelease_count": notice_prelease_count,
        "on_notice":       on_notice,
        "applied":         applied,
        "approved":        approved,
        "signed":          signed,
        "proj_occ":        proj_occ[:20],
    }


def parse_delinquency(ws):
    rows = rows_of(ws)

    # Prefer specific balance-column names over generic ones
    BALANCE_HEADERS = ["resident balance", "total due", "amount due", "balance", "total", "amount"]
    balance_col = None
    header_idx  = -1
    resident_col = status_col = None
    for i, row in enumerate(rows):
        if find_col(row, "unit") is None:
            continue
        for name in BALANCE_HEADERS:
            col = find_col(row, name)
            if col is not None:
                balance_col  = col
                header_idx   = i
                resident_col = find_col(row, "residents", "resident", "name")
                status_col   = find_col(row, "status")
                break
        if balance_col is not None:
            break

    grand_total = 0.0

    if balance_col is not None:
        # Look for a labeled Grand Total / Total row first
        found_labeled = False
        for row in rows[header_idx + 1:]:
            if norm(safe_get(row, 0)) in ("grand total", "total", "totals"):
                val = coerce_num(safe_get(row, balance_col), default=None)
                if val is not None and val > 0:
                    grand_total   = val
                    found_labeled = True
                    break
        if not found_labeled:
            total_rows = []
            for row in rows[header_idx + 1:]:
                first = safe_get(row, 0)
                val = coerce_num(safe_get(row, balance_col), default=None)
                has_resident = resident_col is not None and bool(str(safe_get(row, resident_col) or "").strip())
                has_status   = status_col is not None and bool(str(safe_get(row, status_col) or "").strip())
                if isinstance(first, (int, float)) and first > 0 and not (has_resident or has_status) and val is not None and val > 0:
                    total_rows.append(val)

            if total_rows:
                last = total_rows[-1]
                prior_sum = sum(total_rows[:-1])
                if len(total_rows) == 1:
                    grand_total = last
                elif abs(last - prior_sum) <= 0.01:
                    grand_total = last
                elif abs(last - total_rows[-2]) <= 0.01:
                    grand_total = last
                else:
                    grand_total = sum(total_rows)
                print("WARNING: No labeled Total row in Delinquency — using detected total row(s).")
            else:
                print("WARNING: No labeled Total row in Delinquency — summing resident detail rows.")
                for row in rows[header_idx + 1:]:
                    first = safe_get(row, 0)
                    if not looks_like_unit_value(first):
                        continue
                    has_resident = resident_col is not None and bool(str(safe_get(row, resident_col) or "").strip())
                    has_status   = status_col is not None and bool(str(safe_get(row, status_col) or "").strip())
                    if not (has_resident or has_status):
                        continue
                    val = coerce_num(safe_get(row, balance_col), default=None)
                    if val is not None and val > 0:
                        grand_total += val
    else:
        print("WARNING: Could not identify balance column in Delinquency — using fallback col 9.")
        total_rows = []
        for row in rows:
            first = safe_get(row, 0)
            col9  = safe_get(row, 9)
            if isinstance(first, (int, float)) and first > 0 and isinstance(col9, (int, float)):
                total_rows.append(float(col9))
        if total_rows:
            last = total_rows[-1]
            prior_sum = sum(total_rows[:-1])
            if len(total_rows) > 1 and abs(last - prior_sum) <= 0.01:
                grand_total = last
            elif len(total_rows) > 1 and abs(last - total_rows[-2]) <= 0.01:
                grand_total = last
            else:
                grand_total = last

    return {"total": grand_total}


# ── Rent-line detection ────────────────────────────────────────────────────

# Denylist checked first — blocks false positives regardless of other rules
_RENT_DENYLIST = {
    "renters legal liability",
    "renter's legal liability",
    "renter legal liability",
    "renters insurance",
    "renter's insurance",
    "renter insurance",
    "rental insurance",
    "legal liability",
    "rent assistance",
}
_RENT_DENYLIST_STARTS = ("renters", "renter's", "renter ")

# Explicit allowlist for HAP / Section 8 / subsidy descriptions
_RENT_ALLOWLIST = {
    "hap rent",
    "hap rent - subsidy",
    "section 8 rent",
    "tenant rent",
    "resident rent",
    "rental income",
}

# Matches "Rent", "RENT", "Rent Income" but NOT "Renters …" (word-boundary after "rent")
_RENT_RE = re.compile(r"^rent\b", re.IGNORECASE)


def _is_rent_line(description: str, amount=None) -> bool:
    d  = description.strip()
    dl = d.lower()

    # Denylist first
    if dl in _RENT_DENYLIST:
        return False
    for prefix in _RENT_DENYLIST_STARTS:
        if dl.startswith(prefix):
            return False

    # Explicit allowlist
    if dl in _RENT_ALLOWLIST:
        return True

    if dl.startswith("rent assistance"):
        return "hap" in dl or "section 8" in dl or "subsid" in dl

    # Regex: whole-word "rent" at start (catches "Rent", "RENT", "Rent Income")
    if bool(_RENT_RE.match(d)):
        return True

    # Concessions offset gross rent (negative amounts)
    if dl.startswith("concession"):
        amt = coerce_num(amount, default=None)
        return amt is None or amt < 0

    return False


def parse_rent_roll(ws, occupied):
    rows = rows_of(ws)

    desc_col = amt_col = None
    for row in rows:
        dc = find_col(row, "description")
        ac = find_col(row, "amount")
        if dc is not None and ac is not None:
            desc_col = dc
            amt_col  = ac
            break

    total_rental = 0.0
    if desc_col is not None and amt_col is not None:
        for row in rows:
            d = safe_get(row, desc_col)
            a = safe_get(row, amt_col)
            amt = coerce_num(a, default=None)
            if amt is not None and d and _is_rent_line(str(d), amt):
                total_rental += amt
    else:
        print("WARNING: Description/Amount columns not found in Rent Roll.")

    avg_rent = total_rental / occupied if occupied else 0.0
    return {"total_rental": total_rental, "avg_rent": avg_rent}


# ── Available units ─────────────────────────────────────────────────────────

_VACANT_SECTIONS   = {"vacant", "vacant preleased", "vacant pre-leased"}
_NOTICE_SECTIONS   = {"notice to vacate", "notice to vacate preleased", "notice to vacate pre-leased"}
_ALL_AU_SECTIONS   = _VACANT_SECTIONS | _NOTICE_SECTIONS
_PRELEASE_SECTIONS = {"vacant preleased", "vacant pre-leased", "notice to vacate preleased", "notice to vacate pre-leased"}
_AU_NON_SECTION_HEADERS = {
    "available units",
    "unit",
    "total",
    "totals",
    "grand total",
}


def is_ready_status(status):
    s = re.sub(r"\s*\*+$", "", norm(status)).strip()
    return s == "ready"


def display_section_label(value):
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def is_available_units_section_header(row):
    first = safe_get(row, 0)
    if not isinstance(first, str) or not first.strip():
        return False
    text = display_section_label(first)
    sn = norm(text)
    if sn in _AU_NON_SECTION_HEADERS:
        return False
    if sn.startswith("printed") or sn.startswith("copyright") or sn.startswith("*"):
        return False
    if "resman" in sn:
        return False
    if re.match(r"^\d{1,2}/\d{1,2}/\d{4}\b", text):
        return False
    return looks_like_group_header(row)


def parse_available_units(ws):
    rows = rows_of(ws)

    current_section = None
    unit_col = type_col = status_col = None
    in_data  = False
    all_units: list = []

    for row in rows:
        if is_junk_row(row):
            continue

        first = safe_get(row, 0)

        # ── Section header ─────────────────────────────────────────────
        if is_available_units_section_header(row):
            current_section = display_section_label(first)
            unit_col = type_col = status_col = None
            in_data  = False
            continue

        # ── Column header row ─────────────────────────────────────────
        if (current_section
                and find_col(row, "unit") is not None
                and find_col(row, "unit type") is not None
                and find_col(row, "unit status") is not None):
            unit_col   = find_col(row, "unit")
            type_col   = find_col(row, "unit type")
            status_col = find_col(row, "unit status")
            in_data    = True
            continue

        # ── Unit data rows ─────────────────────────────────────────────
        if in_data and unit_col is not None and status_col is not None:
            uval = safe_get(row, unit_col)
            sval = safe_get(row, status_col)
            tval = safe_get(row, type_col) if type_col is not None else None

            if looks_like_unit_value(uval) and sval is not None:
                all_units.append({
                    "unit":    str(uval).strip(),
                    "type":    str(tval or "").strip(),
                    "section": current_section,
                    "status":  str(sval or "").strip(),
                })

    # Ready: normalized status == "ready", but only from operating sections.
    # Eviction-related sections are intentionally excluded even when the row
    # status says Ready.
    ready_units = [
        u for u in all_units
        if is_ready_status(u["status"]) and norm(u["section"]) in _ALL_AU_SECTIONS
    ]
    prelease_count = sum(1 for u in all_units if norm(u["section"]) in _PRELEASE_SECTIONS)

    return {"ready_units": ready_units, "prelease_count": prelease_count}


def parse_expiring_leases(ws, date_range=""):
    rows = rows_of(ws)

    # Determine report start month
    start_dt = datetime.now()
    if date_range:
        try:
            end_str = date_range.split(" - ")[-1].strip()
            start_dt = datetime.strptime(end_str, "%m/%d/%Y")
        except Exception:
            pass
    start_key = (start_dt.year, start_dt.month)

    MONTH_RE = re.compile(r"^[A-Za-z]{3,9}\s+\d{4}$")

    months: dict  = {}
    current_month = None
    current_header = None
    in_unit_rows = False

    def parse_month_label(value):
        if not isinstance(value, str):
            return None
        label = re.sub(r"\s+", " ", value.strip())
        if not MONTH_RE.match(label):
            return None
        for fmt in ("%B %Y", "%b %Y"):
            try:
                return datetime.strptime(label, fmt)
            except ValueError:
                pass
        return None

    for row in rows:
        first = safe_get(row, 0)
        if first is None:
            continue

        first_str  = str(first).strip()
        first_norm = norm(first)

        if not first_str or first_str.startswith("©") or "ResMan" in first_str:
            continue
        if "notes:" in first_norm or "expiration notes" in first_norm or "limit:" in first_norm:
            continue

        unit_col = find_col(row, "unit")
        status_col = find_col(row, "status")
        lease_exp_col = find_col(row, "lease expires", "lease expired")
        if lease_exp_col is None:
            lease_exp_col = find_col_contains(row, "lease expir")
        if unit_col is not None and status_col is not None and lease_exp_col is not None:
            current_header = {
                "unit": unit_col,
                "status": status_col,
                "lease_exp": lease_exp_col,
                "renewal": find_col(row, "renewal start"),
            }
            in_unit_rows = current_month is not None
            continue

        # Month header (e.g. "June 2026")
        dt = parse_month_label(first)
        if dt:
            current_month = (dt.year, dt.month)
            in_unit_rows = current_header is not None
            if current_month not in months:
                months[current_month] = {"dt": dt, "expirations": 0, "renewals": 0}
            continue

        if current_month is None or current_header is None or not in_unit_rows:
            continue

        # Skip the small-integer lease-count summary row ResMan inserts per month.
        if isinstance(first, (int, float)) and not str(safe_get(row, current_header["status"]) or "").strip():
            continue

        if first_norm in {"unit", "status", "total", "totals", "grand total", ""}:
            continue

        unit_val = safe_get(row, current_header["unit"])
        status_val = safe_get(row, current_header["status"])
        lease_exp = safe_get(row, current_header["lease_exp"])
        if not looks_like_unit_value(unit_val) or not str(status_val or "").strip():
            continue
        if lease_exp is None or not str(lease_exp).strip():
            continue
        if isinstance(lease_exp, datetime) and (lease_exp.year, lease_exp.month) != current_month:
            continue

        months[current_month]["expirations"] += 1
        renewal_col = current_header.get("renewal")
        if renewal_col is not None:
            rv = safe_get(row, renewal_col)
            if rv is not None and str(rv).strip():
                months[current_month]["renewals"] += 1

    sorted_months = sorted(months.items())
    result = []
    for key, data in sorted_months:
        if key >= start_key:
            result.append(data)
        if len(result) >= 10:
            break

    return result


def parse_prospect_sources(ws):
    rows = rows_of(ws)

    METRICS = {
        "new prospects": "New Prospects",
        "return prospects": "Return Prospects",
        "new apps": "New Apps",
        "new applications": "New Apps",
        "net leases": "Net Leases",
    }
    col_map: dict = {}
    header_idx = -1

    for i, row in enumerate(rows):
        if norm(safe_get(row, 0)) == "source":
            header_idx = i
            for c, h in enumerate(row):
                key = METRICS.get(norm(h))
                if key:
                    col_map[key] = c
            break

    if header_idx < 0:
        return {}

    sources = []
    for row in rows[header_idx + 1:]:
        first = safe_get(row, 0)
        if not first or not isinstance(first, str):
            continue
        if norm(first) in ("totals",) or first.startswith("*") or first.startswith("©"):
            break
        sources.append(row)

    result = {}
    for metric in ("New Prospects", "Return Prospects", "New Apps", "Net Leases"):
        if metric not in col_map:
            continue
        c = col_map[metric]
        ranked = sorted(
            [(str(safe_get(row, 0)), coerce_num(safe_get(row, c), default=0)) for row in sources],
            key=lambda x: x[1],
            reverse=True,
        )
        result[metric] = ranked[:2]

    return result


_OPEN_WO_STATUSES = {"not started", "submitted", "in progress", "scheduled", "on hold", "open", "new", "pending"}
_CLOSED_WO_STATUSES = {"completed", "complete", "cancelled", "canceled", "closed", "resolved", "rejected", "void"}


def is_open_wo_status(value):
    s = norm(value)
    return s in _OPEN_WO_STATUSES or any(s.startswith(p + " ") for p in _OPEN_WO_STATUSES)


def is_closed_wo_status(value):
    s = norm(value)
    return s in _CLOSED_WO_STATUSES or any(s.startswith(p + " ") for p in _CLOSED_WO_STATUSES)


def coerce_work_order_number(value):
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    s = str(value or "").strip()
    if not s:
        return None
    m = re.search(r"\d+", s)
    return int(m.group(0)) if m else None


_NON_EMERGENCY_WO_PATTERNS = [
    r"\bbroken blinds?\b",
    r"\bblinds?\b",
    r"\broutine maintenance\b",
    r"\bpest\b",
    r"\broach(?:es)?\b",
    r"\bants?\b",
    r"\bbugs?\b",
    r"\brodent(?:s)?\b",
    r"\bgrounds?\b",
    r"\blandscap(?:e|ing)?\b",
    r"\bclean(?:ing)?\b",
    r"\bpaint(?:ing)?\b",
    r"\bcosmetic\b",
    r"\bfilters?\b",
    r"\bflooring\b",
    r"\bcarpet\b",
    r"\block changes?\b",
    r"\bchange locks?\b",
    r"\bkey replacements?\b",
    r"\breplace keys?\b",
]

_EMERGENCY_WO_PATTERNS = [
    ("HVAC/AC", [
        r"\bhvac\b",
        r"\ba\s*/\s*c\b",
        r"\ba\.?\s*c\.?\b",
        r"\bheating\b",
        r"\bheat\b",
        r"\bventilation\b",
        r"\bair condition(?:er|ing)?\b",
        r"\bthermostat\b",
        r"\bheat not working\b",
        r"\bno heat\b",
        r"\bheater not working\b",
        r"\bac is not working\b",
        r"\bnot cooling\b",
        r"\bblowing warm\b",
        r"\bunit was warm\b",
        r"\bthermostat blank screen\b",
        r"\bair conditioner leaking water\b",
        r"\bac leaking water\b",
        r"\bac unit leaking\b",
        r"\bair conditioner in bathroom leaking\b",
    ]),
    ("Water Heater", [
        r"\bno hot water\b",
        r"\bhot water heater\b",
        r"\bwater heater out\b",
        r"\bwater heater not working\b",
        r"\bwater heater\s+(?:is\s+)?(?:out|not working|broken|failed|leaking|dead)\b",
    ]),
    ("Water Leak", [
        r"\bleak(?:ing|s)?\b",
        r"\bleek(?:ing|s)?\b",
        r"\bwater damage\b",
        r"\bplumbing\b",
        r"\bflood(?:ed|ing)?\b",
        r"\bflooded toilet\b",
        r"\btoilet overflow\b",
        r"\bdrip(?:ping|s)?\b",
        r"\bdripping\b",
        r"\bwater coming through\b",
        r"\bceiling leak(?:ing)?\b",
        r"\broof leak\b",
        r"\bwater from ceiling\b",
        r"\bwater on (?:the )?floor\b",
        r"\bwater in (?:the )?kitchen ceiling\b",
        r"\bclog(?:ged)?\b",
        r"\bsewage\b",
        r"\bbackup\b",
        r"\bback(?:ing)?\s+up\b",
        r"\btoilet tank\b",
        r"\btoilet is running\b",
        r"\btoilet.*not.*refill\b",
        r"\btoilet tank empty\b",
        r"\bnot draining\b",
        r"\bnot containable\b",
        r"\bconstant flow\b",
        r"\bflowing\b",
        r"\bcannot contain\b",
        r"\bwasher hookup\b",
        r"\bwasher leak\b",
        r"\bwasher\s*/\s*dryer leak\b",
    ]),
    ("Fire/Smoke", [
        r"\bactive\s+fire\b",
        r"\bon\s+fire\b",
        r"\bfire\s+(?:in|inside|at|coming|started|burning)\b",
        r"\b(?:fire|smoke)\s+(?:alarm|detector)s?\s+(?:is\s+|are\s+|was\s+|were\s+|keeps?\s+|keep\s+)?(?:going\s+off|went\s+off|ringing|beeping|sounding|trigger(?:ed|ing)|activated)\b",
        r"\bsomething\s+(?:is\s+)?burning\b",
        r"\bburning\s+(?:smell|odor).{0,80}\b(?:appliance|wiring|wire|electrical|outlet|dryer|stove|oven|furnace|heater)\b",
        r"\b(?:appliance|wiring|wire|electrical|outlet|dryer|stove|oven|furnace|heater).{0,80}\bburning\s+(?:smell|odor)\b",
        r"\bdryer vent\b",
        r"\bfire hazard\b",
        r"\bsparking\b",
    ]),
    ("Broken Windows", [
        r"\bbroken window\b",
        r"\bwindow (?:won't|wont|will not) close\b",
        r"\bwindow cracked\b",
        r"\bwindow shattered\b",
        r"\bwindow (?:won't|wont|will not) lock\b",
    ]),
    ("Broken Doors", [
        r"\bdoor off hinges?\b",
        r"\bdoor (?:is\s+)?coming off(?: (?:the )?hinges?)?\b",
        r"\bdoor (?:won't|wont|will not) close\b",
        r"\bcannot secure (?:the )?door\b",
        r"\b(?:entry|front) door.{0,80}\b(?:off hinges?|coming off|won't close|wont close|will not close|cannot secure|broken)\b",
        r"\bdoor is broken\b",
    ]),
    ("Broken Appliances", [
        r"\bfridge\b",
        r"\brefrigerator\b",
        r"\bstove\b",
        r"\bwasher\b",
        r"\bdryer\b",
        r"\bdishwasher\b",
        r"\boven\b",
        r"\bappliance(?:s)?\b",
        r"\bsink\b",
        r"\bfaucet\b",
    ]),
    ("Mold/Mildew", [
        r"\bmold\b",
        r"\bmildew\b",
        r"\bblack mold\b",
    ]),
    ("Structural", [
        r"\bdetached from (?:the )?wall\b",
        r"\balmost detached\b",
    ]),
]


_DOOR_EXCLUDE_PATTERNS = [
    r"\bdoor sweep\b",
    r"\bscreen door\b",
    r"\bsliding door\b",
    r"\bcloset doors?\b",
    r"\bdoor handles?\b",
    r"\bdoor knobs?\b",
    r"\bdoor stops?\b",
    r"\bcabinet door\b",
    r"\bdishwasher door\b",
    r"\b(?:fridge|refrigerator|freezer) door\b",
    r"\bbifold door\b",
]

_DOOR_SECURITY_INCLUDE_PATTERNS = [
    r"\bfront door.{0,80}\b(?:off hinges?|coming off|won't close|wont close|will not close|cannot secure|broken)\b",
    r"\bentry door.{0,80}\b(?:off hinges?|coming off|won't close|wont close|will not close|cannot secure|broken)\b",
    r"\bcannot secure (?:the )?door\b",
    r"\bentrance/exit\b",
]

_WINDOW_EXCLUDE_PATTERNS = [
    r"\bwindow screens?\b",
    r"\bscreen windows?\b",
    r"\bwindow blinds?\b",
    r"\bcurtains?\b",
    r"\bwindow sill\b",
    r"\bplastic inserts? for window\b",
]

_FIRE_SMOKE_EXCLUDE_PATTERNS = [
    r"\bsmell of vape\b",
    r"\bvape smell\b",
    r"\bcigarette smell\b",
    r"\bsmell of smoke\b",
    r"\bsmoke detector check\b",
    r"\bsmoke detector install\b",
    r"\bno sparking\b",
    r"\bnot sparking\b",
    r"\bno smoking\b",
    r"\bnot smoking\b",
    r"\bno risk of fire\b",
    r"\bnot (?:a )?fire (?:risk|hazard)\b",
    r"\bno .*risk of fire\b",
]

_APPLIANCE_NON_EMERGENCY_PATTERNS = [
    r"\bstill cooling\b",
    r"\bdoor shelf\b",
    r"\bmissing handle\b",
    r"\bwould go needs repair before installing appliances\b",
]


def normalize_wo_text(value):
    return (
        str(value or "")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .lower()
    )


def wo_matches(text, patterns):
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def is_broken_appliance_emergency(order, text):
    source_category = normalize_wo_text(order.get("source_category") or order.get("category") or "")
    issue_type = normalize_wo_text(order.get("issue_type") or "")

    if wo_matches(text, _APPLIANCE_NON_EMERGENCY_PATTERNS):
        return False

    if "appliance" in source_category:
        return True

    # Work Order Issue column directly naming a known appliance is a strong
    # signal, except when the description says it is only cosmetic/non-urgent.
    _KNOWN_APPLIANCES = {
        "stove", "washer", "dryer", "dishwasher",
        "refrigerator", "fridge", "oven", "microwave",
        "freezer",
    }
    if any(name in issue_type for name in _KNOWN_APPLIANCES):
        return True

    appliance = r"(?:fridge|refrigerator|stove|washer|dryer|dishwasher|oven|appliance|sink|faucet)"
    issue = (
        r"(?:not working|won't|wont|doesn't|doesnt|broken|damaged|leak(?:ing)?|"
        r"repair|replace|out|stopped|isn't draining|is not draining|"
        r"not draining|won't drain|wont drain|doesn't drain|doesnt drain|"
        r"not turning on|not turn on|not functioning|cannot be closed|will not close|"
        r"won't close|wont close|detached from)"
    )
    return wo_matches(text, [
        rf"\b{appliance}s?\b.{{0,60}}\b{issue}\b",
        rf"\b{issue}\b.{{0,60}}\b{appliance}s?\b",
    ])


def classify_emergency_work_order(order):
    text = normalize_wo_text(" ".join(
        str(order.get(key) or "")
        for key in ("source_category", "category", "description", "notes", "issue_type")
    ))

    # Work Order Issue column is the highest-precision signal for certain categories.
    # Check it directly before any keyword matching to avoid false positives from
    # incidental mentions in description text (e.g. tenant mentioning water heater
    # as one of several things they checked when the real issue is noise).
    _ISSUE_TYPE_OVERRIDES = {
        "water heater":     "Water Heater",
        "hot water heater": "Water Heater",
        "ceiling leak":     "Water Leak",
        "roof leak exterior": "Water Leak",
        "bathtub leak":     "Water Leak",
        "sink leaking":     "Water Leak",
        "faucet leak":      "Water Leak",
        "drain/pipe clog":  "Water Leak",
        "toilet is running continuously": "Water Leak",
        "air conditioner":  "HVAC/AC",
        "thermostat":       "HVAC/AC",
        "mold/mildew":      "Mold/Mildew",
        "mold":             "Mold/Mildew",
    }
    issue_type_val = normalize_wo_text(order.get("issue_type") or "").strip()
    mold_patterns = next(p for c, p in _EMERGENCY_WO_PATTERNS if c == "Mold/Mildew")
    if wo_matches(text, mold_patterns):
        return "Mold/Mildew"
    if issue_type_val in _ISSUE_TYPE_OVERRIDES:
        return _ISSUE_TYPE_OVERRIDES[issue_type_val]

    structural_patterns = next(p for c, p in _EMERGENCY_WO_PATTERNS if c == "Structural")
    if wo_matches(text, structural_patterns):
        return "Structural"

    if wo_matches(text, _NON_EMERGENCY_WO_PATTERNS):
        return None

    for category, patterns in _EMERGENCY_WO_PATTERNS:
        if category in ("Mold/Mildew", "Structural"):
            continue  # already handled above
        if not wo_matches(text, patterns):
            continue
        if category == "Fire/Smoke" and wo_matches(text, _FIRE_SMOKE_EXCLUDE_PATTERNS):
            continue
        if category == "Broken Windows" and wo_matches(text, _WINDOW_EXCLUDE_PATTERNS):
            continue
        if (
            category == "Broken Doors"
            and wo_matches(text, _DOOR_EXCLUDE_PATTERNS)
            and not wo_matches(text, _DOOR_SECURITY_INCLUDE_PATTERNS)
        ):
            continue
        if category == "Broken Appliances" and not is_broken_appliance_emergency(order, text):
            continue
        return category
    return None


def parse_work_orders(ws):
    rows = rows_of(ws)

    header_idx = -1
    col_map: dict = {}
    for i, row in enumerate(rows):
        number_col = find_col(row, "number", "wo #", "work order #", "work order number")
        reported_col = find_col(row, "reported", "date reported", "reported date")
        if number_col is not None and (find_col(row, "location") is not None or reported_col is not None):
            header_idx = i
            for c, h in enumerate(row):
                hn = norm(h)
                if hn in ("number", "wo #", "work order #", "work order number"):
                    col_map["number"] = c
                elif hn == "location":
                    col_map["location"] = c
                elif hn in ("reported", "date reported", "reported date"):
                    col_map["reported"] = c
                elif hn in ("category", "description", "notes"):
                    col_map[hn] = c
            break

    work_orders    = []
    current_status = None

    if header_idx >= 0:
        num_col = col_map.get("number", 0)

        for row in rows[header_idx + 1:]:
            if is_junk_row(row):
                continue

            first      = safe_get(row, 0)
            first_norm = norm(first)

            # Open status group header → start counting
            if isinstance(first, str) and is_open_wo_status(first):
                current_status = first_norm
                continue
            # Closed status group header → stop counting rows beneath it
            if isinstance(first, str) and (is_closed_wo_status(first) or looks_like_group_header(row)):
                current_status = None
                continue

            if current_status is None:
                continue

            # Coerce WO number — handles int, float, or string
            wo_num_raw = safe_get(row, num_col)
            wo_num = coerce_work_order_number(wo_num_raw)
            if wo_num is None or wo_num <= 0:
                continue

            loc  = safe_get(row, col_map.get("location",    1))
            rep  = safe_get(row, col_map.get("reported",    2))
            cat  = safe_get(row, col_map.get("category",    3))
            desc = safe_get(row, col_map.get("description", 4))
            notes = safe_get(row, col_map.get("notes", 5))

            # Skip rows with no identifying data beyond the number
            if not any(str(v or "").strip() for v in (loc, rep, cat, desc, notes)):
                continue

            work_orders.append({
                "number":      wo_num,
                "location":    str(loc  or "").strip(),
                "reported":    rep,
                "category":    str(cat  or "").strip(),
                "description": str(desc or "").strip(),
                "notes":       str(notes or "").strip(),
                "status":      current_status,
            })

    # Priority-ordered keyword classification — first match wins
    emergency_orders = []
    count_map = {}
    for wo in work_orders:
        emergency_category = classify_emergency_work_order(wo)
        if not emergency_category:
            continue
        wo["source_category"] = wo.get("category", "")
        wo["category"] = emergency_category
        wo["date_reported"] = fmt_date(wo.get("reported"))
        emergency_orders.append(wo)
        count_map[emergency_category] = count_map.get(emergency_category, 0) + 1

    issue_counts = {
        category: count_map[category]
        for category, _ in _EMERGENCY_WO_PATTERNS
        if count_map.get(category)
    }

    return {"work_orders": emergency_orders, "issue_counts": issue_counts}


# ══════════════════════════════════════════════════════════════════════════
#  SUMMARY BUILDER
# ══════════════════════════════════════════════════════════════════════════

# Column layout (1-indexed):
#  A=1  spacer
#  B=2  labels / table col 1
#  C=3  values / table col 2
#  D=4  table col 3
#  E=5  table col 4
#  F=6  WO description / table col 5
#  G=7  Projected Occ – Week
#  H=8  Projected Occ – Units
#  I=9  Projected Occ – %

COL_WIDTHS = {
    "A": 2,
    "B": 18,
    "C": 13,
    "D": 16,
    "E": 13,
    "F": 18,
    "G": 13,
    "H": 2,
    "I": 13,
    "J": 13,
    "K": 13,
    "L": 13,
    "M": 13,
    "N": 13,
    "O": 13,
    "P": 13,
}


def build_summary_legacy(wb, data):
    if "Summary" in wb.sheetnames:
        del wb["Summary"]
    ws = wb.create_sheet("Summary", 0)

    for col_letter, width in COL_WIDTHS.items():
        ws.column_dimensions[col_letter].width = width

    bs = data["box_score"]
    dl = data["delinquency"]
    rr = data["rent_roll"]
    au = data["available_units"]
    el = data["expiring_leases"]
    ps = data["prospect_sources"]
    wo = data["work_orders"]

    r = 1  # running row cursor

    # ── TITLE BLOCK ───────────────────────────────────────────────────────
    ws.row_dimensions[r].height = 24
    merge_wc(ws, r, 2, 9, bs["property_name"], font=_FONTS["title"], align=C)
    r += 1

    merge_wc(ws, r, 2, 9, "Weekly Property Summary",
             font=_FONTS["sub"], fill=None, align=C)
    r += 1
    merge_wc(ws, r, 2, 9, bs["date_range"],
             font=_FONTS["data"], fill=None, align=C)
    r += 1
    merge_wc(ws, r, 2, 9, f"Date Printed: {bs['printed']}",
             font=_FONTS["meta"], fill=None, align=C)
    r += 1
    ws.row_dimensions[r].height = 6
    r += 1

    top_row = r  # start of two-column section

    # ── LEFT: OCCUPANCY ───────────────────────────────────────────────────
    section_hdr(ws, r, "OCCUPANCY", c1=2, c2=5)
    r += 1

    occ_rows = [
        ("% Occupancy",    fmt_pct(bs["pct_occ"])),
        ("Occupied (Occ)", bs["occupied"]),
        ("Vacant",         bs["vacant"]),
        ("Preleases (Vacant + On-Notice)", au["prelease_count"]),
        ("On-Notice",      bs["on_notice"]),
    ]
    for label, val in occ_rows:
        wc(ws, r, 2, label, font="label", align=L)
        wc(ws, r, 3, val,   font="data",  align=R)
        r += 1

    ws.row_dimensions[r].height = 6
    r += 1

    # ── LEFT: LEASING ACTIVITY ────────────────────────────────────────────
    section_hdr(ws, r, "LEASING ACTIVITY", c1=2, c2=5)
    r += 1

    for label, val in [("Applied", bs["applied"]), ("Approved", bs["approved"]), ("Signed", bs["signed"])]:
        wc(ws, r, 2, label, font="label", align=L)
        wc(ws, r, 3, val,   font="data",  align=R)
        r += 1

    ws.row_dimensions[r].height = 6
    r += 1

    # ── LEFT: DELINQUENCY ─────────────────────────────────────────────────
    section_hdr(ws, r, "DELINQUENCY", c1=2, c2=5)
    r += 1
    wc(ws, r, 2, "Total Delinquency", font="label", align=L)
    wc(ws, r, 3, dl["total"],          font="data",  align=R, num_fmt='"$"#,##0.00')
    r += 1

    ws.row_dimensions[r].height = 6
    r += 1

    # ── LEFT: RENTAL INCOME ───────────────────────────────────────────────
    section_hdr(ws, r, "RENTAL INCOME TO DATE", c1=2, c2=5)
    r += 1
    wc(ws, r, 2, "Total Rental Revenue",     font="label", align=L)
    wc(ws, r, 3, rr["total_rental"],          font="data",  align=R, num_fmt='"$"#,##0.00')
    r += 1
    wc(ws, r, 2, "Average Rent / Unit / Mo", font="label", align=L)
    wc(ws, r, 3, rr["avg_rent"],              font="data",  align=R, num_fmt='"$"#,##0.00')
    r += 1

    bottom_left = r  # track where the left column ends

    # ── RIGHT: PROJECTED OCCUPANCY ────────────────────────────────────────
    pr = top_row
    section_hdr(ws, pr, "PROJECTED OCCUPANCY", c1=7, c2=9)
    pr += 1

    col_hdr(ws, pr, 7, "Week")
    col_hdr(ws, pr, 8, "Occ Units")
    col_hdr(ws, pr, 9, "% Occupied")
    pr += 1

    for i, entry in enumerate(bs["proj_occ"]):
        z = i % 2 == 0
        data_row(ws, pr, 7, fmt_date(entry["date"]), zebra=z, align=C)
        data_row(ws, pr, 8, entry["occ"],             zebra=z, align=C)
        data_row(ws, pr, 9, entry["pct"],             zebra=z, align=C, num_fmt="0.0%")
        pr += 1

    r = max(r, pr)
    ws.row_dimensions[r].height = 8
    r += 1

    # ══════════════════════════════════════════════════════════════════════
    #  FULL-WIDTH TABLES
    # ══════════════════════════════════════════════════════════════════════

    # ── READY UNITS ───────────────────────────────────────────────────────
    section_hdr(ws, r, "READY UNITS — VACANT & VACANT PRE-LEASED")
    r += 1

    for col, label in zip([2, 3, 4, 5], ["Unit", "Unit Type", "Section / Status", "Unit Status"]):
        col_hdr(ws, r, col, label)
    r += 1

    if au["ready_units"]:
        for i, unit in enumerate(au["ready_units"]):
            z = i % 2 == 0
            data_row(ws, r, 2, unit["unit"],    zebra=z, align=C)
            data_row(ws, r, 3, unit["type"],    zebra=z, align=L)
            data_row(ws, r, 4, unit["section"], zebra=z, align=L)
            data_row(ws, r, 5, unit["status"],  zebra=z, align=C)
            r += 1
    else:
        merge_wc(ws, r, 2, 5, "No ready units found", font="data", fill=None, align=C)
        r += 1

    ws.row_dimensions[r].height = 8
    r += 1

    # ── EXPIRING LEASES BY MONTH ──────────────────────────────────────────
    section_hdr(ws, r, "EXPIRING LEASES BY MONTH (NEXT 10 MONTHS)")
    r += 1

    for col, label in zip([2, 3, 4], ["Month", "Lease Expirations", "Renewal Starts"]):
        col_hdr(ws, r, col, label)
    r += 1

    if el:
        for i, m in enumerate(el):
            z = i % 2 == 0
            data_row(ws, r, 2, fmt_month(m["dt"]),  zebra=z, align=L)
            data_row(ws, r, 3, m["expirations"],    zebra=z, align=C)
            data_row(ws, r, 4, m["renewals"],       zebra=z, align=C)
            r += 1
    else:
        merge_wc(ws, r, 2, 4, "No expiring lease data", font="data", fill=None, align=C)
        r += 1

    ws.row_dimensions[r].height = 8
    r += 1

    # ── TOP 2 PROSPECT SOURCES ────────────────────────────────────────────
    section_hdr(ws, r, "TOP 2 PROSPECT SOURCES")
    r += 1

    for col, label in zip([2, 3, 4, 5, 6],
                          ["Category", "#1 Source", "#1 Count", "#2 Source", "#2 Count"]):
        col_hdr(ws, r, col, label)
    r += 1

    METRIC_LABELS = {
        "New Prospects":    "New Prospects",
        "Return Prospects": "Return Prospects",
        "New Apps":         "New Applications",
        "Net Leases":       "Net Leases",
    }
    for i, (key, label) in enumerate(METRIC_LABELS.items()):
        z = i % 2 == 0
        ranked = ps.get(key, [])
        s1, c1_v = ranked[0] if len(ranked) > 0 else ("—", 0)
        s2, c2_v = ranked[1] if len(ranked) > 1 else ("—", 0)
        data_row(ws, r, 2, label, zebra=z, align=L)
        data_row(ws, r, 3, s1,    zebra=z, align=L)
        data_row(ws, r, 4, c1_v,  zebra=z, align=C)
        data_row(ws, r, 5, s2,    zebra=z, align=L)
        data_row(ws, r, 6, c2_v,  zebra=z, align=C)
        r += 1

    ws.row_dimensions[r].height = 8
    r += 1

    # ── OPEN WORK ORDERS ──────────────────────────────────────────────────
    section_hdr(ws, r, f"OPEN WORK ORDERS  ({len(wo['work_orders'])} total)")
    r += 1

    # Issue type summary mini-table
    ic = wo["issue_counts"]
    if any(ic.values()):
        wc(ws, r, 2, "Issue Type", font="col_hdr", fill=_fill(LIGHT_BLUE), align=C, border=_box())
        wc(ws, r, 3, "Count",      font="col_hdr", fill=_fill(LIGHT_BLUE), align=C, border=_box())
        r += 1
        for j, (issue, cnt) in enumerate(ic.items()):
            z = j % 2 == 0
            data_row(ws, r, 2, issue, zebra=z, align=L)
            data_row(ws, r, 3, cnt,   zebra=z, align=C)
            r += 1
        ws.row_dimensions[r].height = 6
        r += 1

    # WO detail table
    for col, label in zip([2, 3, 4, 5, 6],
                          ["WO #", "Unit / Location", "Date Reported", "Category", "Description"]):
        col_hdr(ws, r, col, label)
    r += 1

    if wo["work_orders"]:
        for i, order in enumerate(wo["work_orders"]):
            z = i % 2 == 0
            desc = order["description"]
            if len(desc) > 300:
                desc = desc[:297] + "..."

            data_row(ws, r, 2, order["number"],   zebra=z, align=C)
            data_row(ws, r, 3, order["location"], zebra=z, align=C)
            data_row(ws, r, 4, fmt_date(order["reported"]), zebra=z, align=C)
            data_row(ws, r, 5, order["category"], zebra=z, align=L)

            # Description cell — allow text wrap and auto-height
            fill = _fill(PALE_BLUE) if z else None
            cell = ws.cell(row=r, column=6, value=desc)
            cell.font      = _FONTS["data"]
            cell.alignment = TL
            cell.border    = _box()
            if fill:
                cell.fill = fill

            ws.row_dimensions[r].height = 45
            r += 1
    else:
        merge_wc(ws, r, 2, 6, "No open work orders", font="data", fill=None, align=C)
        r += 1

    # Footer
    ws.row_dimensions[r].height = 6
    r += 1
    merge_wc(ws, r, 2, 9,
             f"Generated by FIRE Capital MMR Summary Tool  |  {datetime.now().strftime('%m/%d/%Y %I:%M %p')}",
             font=_FONTS["meta"], fill=None, align=C)

    # Freeze panes below title block
    ws.freeze_panes = ws.cell(row=6, column=1)

    return ws


def merge_band(ws, row, c1, c2, value, font="hdr", fill_color=DARK_BLUE, align=C, border=None):
    ws.merge_cells(start_row=row, start_column=c1, end_row=row, end_column=c2)
    fill = _fill(fill_color) if fill_color else None
    font_obj = _FONTS.get(font, font) if isinstance(font, str) else font
    for col in range(c1, c2 + 1):
        cell = ws.cell(row=row, column=col)
        cell.font = font_obj
        cell.alignment = align
        if fill:
            cell.fill = fill
        if border:
            cell.border = border
    ws.cell(row=row, column=c1, value=value)
    return ws.cell(row=row, column=c1)


def section_band(ws, row, title, c1=2, c2=7):
    return merge_band(ws, row, c1, c2, title, font="hdr", fill_color=DARK_BLUE, align=C)


def write_kv(ws, row, label_col, value_col, label, value, num_fmt=None):
    wc(ws, row, label_col, label, font="label", align=L)
    wc(ws, row, value_col, value, font="data", align=R, num_fmt=num_fmt)


def na_if_none(value):
    return "N/A" if value is None else value


def write_pair_row(ws, row, left_label, left_value, right_label=None, right_value=None, left_fmt=None, right_fmt=None):
    write_kv(ws, row, 2, 3, left_label, left_value, left_fmt)
    if right_label is not None:
        write_kv(ws, row, 5, 7, right_label, right_value, right_fmt)


def setup_summary_print(ws):
    ws.sheet_view.showGridLines = False
    ws.freeze_panes     = None   # BUG2: no frozen rows
    ws.print_title_rows = None   # BUG2: no repeated header on print
    ws.print_area = "A1:P48"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = 0.25
    ws.page_margins.right = 0.25
    ws.page_margins.top = 0.35
    ws.page_margins.bottom = 0.35
    ws.page_margins.header = 0.15
    ws.page_margins.footer = 0.15
    ws.sheet_properties.pageSetUpPr.autoPageBreaks = False


_NAVY      = "#1A2744"
_BLUE      = "#4A90D9"
_FIG_W     = 7.0    # inches
_FIG_H     = 2.8    # inches — both charts identical height
_FIG_DPI   = 150


def _fig_to_xl_image(fig):
    """Render a matplotlib figure to a BytesIO PNG and return an XLImage.

    openpyxl sizes embedded images using the PNG's pixel dimensions assuming
    96 DPI. We generate at 150 DPI for crispness, so we must override width
    and height to match the intended display size (7" wide, proportional height).
    """
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=_FIG_DPI, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    img = XLImage(buf)
    # Fix display size: width = _FIG_W inches at Excel's 96 DPI, height proportional
    display_w = int(_FIG_W * 96)
    display_h = int(img.height * display_w / img.width)
    img.width  = display_w
    img.height = display_h
    return img


def add_projected_occupancy_chart(ws, entries):
    """Matplotlib line chart: % occupancy over up to 20 weeks, embedded as PNG."""
    if not entries:
        merge_band(ws, 7, 9, 16, "No projected occupancy data", font="data", fill_color=PALE_BLUE, align=C, border=_box())
        return

    rows = entries[:20]
    labels = [fmt_date(e.get("date")) for e in rows]
    values = [float(e.get("pct") or 0) for e in rows]

    fig, ax = plt.subplots(figsize=(_FIG_W, _FIG_H))
    ax.plot(range(len(labels)), values, color=_NAVY, linewidth=2)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right", fontsize=7)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0%}"))
    y_min = max(0.0, float(np.floor((min(values) - 0.02) * 100) / 100))
    y_max = min(1.0, float(np.ceil((max(values) + 0.01) * 100) / 100))
    if y_max <= y_min:
        y_max = min(1.0, y_min + 0.05)
        if y_max <= y_min:
            y_min = max(0.0, y_max - 0.05)
    ax.set_ylim(y_min, y_max)
    ax.yaxis.grid(True, alpha=0.3)
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    fig.patch.set_facecolor("white")
    plt.tight_layout()
    fig.canvas.draw()   # force label render before saving to buffer

    img = _fig_to_xl_image(fig)
    ws.add_image(img, "I7")


def add_expiring_leases_chart(ws, months):
    """Matplotlib grouped bar chart: expirations vs renewals per month, as PNG."""
    if months is None:
        merge_band(ws, 22, 9, 16, "N/A", font="data", fill_color=PALE_BLUE, align=C, border=_box())
        return
    if not months:
        merge_band(ws, 22, 9, 16, "No expiring lease data", font="data", fill_color=PALE_BLUE, align=C, border=_box())
        return

    rows = months[:10]
    labels      = []
    expirations = []
    renewals    = []
    for m in rows:
        dt = m.get("dt")
        labels.append(dt.strftime("%b %Y") if isinstance(dt, datetime) else str(dt or ""))
        expirations.append(int(m.get("expirations") or 0))
        renewals.append(int(m.get("renewals") or 0))

    x     = np.arange(len(labels))
    width = 0.35

    fig, ax = plt.subplots(figsize=(_FIG_W, _FIG_H))
    ax.bar(x - width / 2, expirations, width, label="Expirations", color=_NAVY)
    ax.bar(x + width / 2, renewals,    width, label="Renewals",    color=_BLUE)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))

    ax.yaxis.grid(True, alpha=0.3)
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_xlabel("")
    ax.set_ylabel("")
    plt.legend(loc="upper right", fontsize=8, framealpha=0.7)
    fig.patch.set_facecolor("white")
    fig.tight_layout()

    img = _fig_to_xl_image(fig)
    ws.add_image(img, "I22")


_PROPERTY_ABBREVS = {
    "oxford pointe":  "OXPT",
    "eagle rock":     "ERA",
    "the canyon":     "Canyon",
    "canyon":         "Canyon",
    "maple valley":   "Maple Valley",
}


def make_download_filename(property_name: str, date_range: str, printed: str = "") -> str:
    """Return e.g. 'OXPT Summary 06.22.26.xlsx' from property name + date range."""
    pn = (property_name or "").strip()
    pn_lower = pn.lower()
    range_text = str(date_range or "")
    range_lower = range_text.lower()
    is_appfolio_range = (
        "maple valley" in pn_lower
        or (" to " in range_lower and "trailing" in range_lower)
        or range_lower.startswith("period range:")
    )

    abbrev = None
    for key, val in _PROPERTY_ABBREVS.items():
        if key in pn_lower:
            abbrev = val
            break
    if abbrev is None:
        # First word, skipping "The" prefix
        words = pn.split()
        abbrev = words[1] if words and words[0].lower() == "the" and len(words) > 1 else (words[0] if words else "Property")

    def parse_printed_date(value):
        clean_printed = str(value or "").replace("Printed", "").replace("Exported On:", "").strip()
        date_match = re.search(r"\d{1,2}/\d{1,2}/\d{4}", clean_printed)
        if date_match:
            return datetime.strptime(date_match.group(0), "%m/%d/%Y")
        return None

    def parse_resman_range_end(value):
        end_str = str(value or "").split(" - ")[-1].strip()
        return datetime.strptime(end_str, "%m/%d/%Y")

    def parse_appfolio_range_end(value):
        import calendar as _cal
        match = re.search(r"to\s+([A-Za-z]+\s+\d{4})", str(value or ""))
        if not match:
            return None
        month_label = match.group(1).strip()
        for fmt in ("%b %Y", "%B %Y"):
            try:
                dt = datetime.strptime(month_label, fmt)
                last_day = _cal.monthrange(dt.year, dt.month)[1]
                return dt.replace(day=last_day)
            except ValueError:
                pass
        return None

    dt = None
    for parser, value in (
        (parse_printed_date, printed),
        (parse_resman_range_end, None if is_appfolio_range else range_text),
        (parse_appfolio_range_end, None if not is_appfolio_range else range_text),
    ):
        if dt is not None or not value:
            continue
        try:
            dt = parser(value)
        except Exception:
            dt = None

    if dt is None:
        dt = datetime.now()

    return f"{abbrev} Summary {dt.strftime('%m.%d.%y')}.xlsx"


def build_summary(wb, data):
    if "Summary" in wb.sheetnames:
        del wb["Summary"]
    ws = wb.create_sheet("Summary", 0)

    for col_letter, width in COL_WIDTHS.items():
        ws.column_dimensions[col_letter].width = width
    # Columns R–U no longer hold visible data; chart data lives in chart_data sheet

    bs = data.get("box_score", default_box_score())
    dl = data.get("delinquency") or {"total": None}
    rr = data.get("rent_roll") or {"total_rental": None, "avg_rent": None}
    au = data.get("available_units") or {"ready_units": None, "prelease_count": None}
    el = data.get("expiring_leases", [])
    ps = data.get("prospect_sources")
    wo = data.get("work_orders") or {"work_orders": None, "issue_counts": {}}
    source_system = data.get("source_system") or detect_source_system(wb)
    source_text, source_color, source_fill = source_system_display(source_system)

    setup_summary_print(ws)
    for row in range(1, 49):
        ws.row_dimensions[row].height = 15
    ws.row_dimensions[1].height = 22
    ws.row_dimensions[3].height = 19
    ws.row_dimensions[5].height = 4
    ws.row_dimensions[26].height = 4
    ws.row_dimensions[47].height = 4

    title = bs.get("property_name") or "MMR Summary"
    period = bs.get("date_range") or ""
    printed = bs.get("printed") or ""

    merge_band(ws, 1, 2, 16, title, font=_FONTS["title"], fill_color=None, align=C)
    merge_band(ws, 2, 2, 16, f"Weekly Property Summary  |  {period}  |  Date Printed: {printed}".strip(" |"),
               font=_FONTS["sub"], fill_color=None, align=C)
    merge_band(
        ws,
        3,
        2,
        16,
        f"Source System: {source_text}",
        font=Font(name="Calibri", bold=True, color=source_color, size=11),
        fill_color=source_fill,
        align=C,
        border=_box(),
    )

    # Left side: key stats and compact detail tables.
    section_band(ws, 6, "OCCUPANCY", 2, 7)
    occupancy_value = fmt_pct(bs.get("pct_occ")) if bs.get("pct_occ") is not None else "N/A"
    write_pair_row(ws, 7, "% Occupancy", occupancy_value, "Occupied", na_if_none(bs.get("occupied")))
    write_pair_row(ws, 8, "Vacant", na_if_none(bs.get("vacant")), "Total Units", na_if_none(bs.get("total_units")))
    # Prefer Box Score "Vacant Pre-Leased" column value; fall back to Available Units count
    prelease_val = bs.get("prelease_count") if bs.get("prelease_count") is not None else au.get("prelease_count", 0)
    write_pair_row(ws, 9, "Preleases (Vacant + On-Notice)", na_if_none(prelease_val), "On-Notice", na_if_none(bs.get("on_notice")))

    section_band(ws, 11, "LEASING / FINANCIAL", 2, 7)
    write_pair_row(ws, 12, "Applied", na_if_none(bs.get("applied")), "Approved", na_if_none(bs.get("approved")))
    write_pair_row(ws, 13, "Signed", na_if_none(bs.get("signed")), "Total Delinquency", na_if_none(dl.get("total")), right_fmt='"$"#,##0.00')
    write_pair_row(ws, 14, "Total Rental Revenue", na_if_none(rr.get("total_rental")), "Average Rent / Unit", na_if_none(rr.get("avg_rent")),
                   left_fmt='"$"#,##0.00', right_fmt='"$"#,##0.00')

    ready_units = au.get("ready_units")
    ready_count = "N/A" if ready_units is None else len(ready_units)
    section_band(ws, 16, f"READY UNITS ({ready_count} total)", 2, 7)
    for col, label in zip([2, 3, 4, 5, 6, 7], ["Unit", "Section", "Status", "Unit", "Section", "Status"]):
        col_hdr(ws, 17, col, label)
    if ready_units is None:
        merge_band(ws, 18, 2, 7, "N/A", font="data", fill_color=None, align=C, border=_box())
    elif ready_units:
        for idx, unit in enumerate(ready_units[:18]):
            row = 18 + (idx % 9)
            base_col = 2 if idx < 9 else 5
            z = (row - 18) % 2 == 0
            data_row(ws, row, base_col, unit.get("unit", ""), zebra=z, align=C)
            data_row(ws, row, base_col + 1, unit.get("section", ""), zebra=z, align=L)
            data_row(ws, row, base_col + 2, unit.get("status", ""), zebra=z, align=C)
    else:
        merge_band(ws, 18, 2, 7, "No ready units found", font="data", fill_color=None, align=C, border=_box())

    section_band(ws, 27, "TOP 2 PROSPECT SOURCES", 2, 7)
    for col, label in zip([2, 3, 4, 5, 7], ["Category", "#1 Source", "#1 Count", "#2 Source", "#2 Count"]):
        col_hdr(ws, 28, col, label)
    metric_labels = {
        "New Prospects": "New Prospects",
        "Return Prospects": "Return Prospects",
        "New Apps": "New Applications",
        "Net Leases": "Net Leases",
    }
    if ps is None:
        merge_band(ws, 29, 2, 7, "N/A", font="data", fill_color=None, align=C, border=_box())
        metric_labels = {}
    for i, (key, label) in enumerate(metric_labels.items(), 29):
        ranked = ps.get(key, [])
        s1, c1_v = ranked[0] if len(ranked) > 0 else ("—", 0)
        s2, c2_v = ranked[1] if len(ranked) > 1 else ("—", 0)
        z = (i - 29) % 2 == 0
        data_row(ws, i, 2, label, zebra=z, align=L)
        data_row(ws, i, 3, s1, zebra=z, align=L)
        data_row(ws, i, 4, c1_v, zebra=z, align=C)
        data_row(ws, i, 5, s2, zebra=z, align=L)
        data_row(ws, i, 7, c2_v, zebra=z, align=C)

    work_orders = wo.get("work_orders")
    work_order_count = "N/A" if work_orders is None else len(work_orders)
    section_band(ws, 35, f"EMERGENCY WORK ORDERS ({work_order_count} total)", 2, 7)
    issue_counts = wo.get("issue_counts", {})
    summary_text = "N/A" if work_orders is None else (" | ".join(f"{issue}: {count}" for issue, count in issue_counts.items()) or "No emergency work orders")
    wc(ws, 36, 2, "Issue Types", font="col_hdr", fill=_fill(LIGHT_BLUE), align=C, border=_box())
    merge_band(ws, 36, 3, 7, summary_text, font="data", fill_color=LIGHT_BLUE, align=L, border=_box())

    for col, label in zip([2, 3, 4, 5], ["WO #", "Unit/Location", "Date Reported", "Category"]):
        col_hdr(ws, 37, col, label)
    merge_band(ws, 37, 6, 7, "Description", font="col_hdr", fill_color=LIGHT_BLUE, align=C, border=_box())
    if work_orders is None:
        merge_band(ws, 38, 2, 7, "N/A", font="data", fill_color=None, align=C, border=_box())
    elif work_orders:
        for i, order in enumerate(work_orders[:8], 38):
            z = (i - 38) % 2 == 0
            data_row(ws, i, 2, order.get("number", ""), zebra=z, align=C)
            data_row(ws, i, 3, order.get("location", ""), zebra=z, align=C)
            data_row(ws, i, 4, order.get("date_reported") or fmt_date(order.get("reported")), zebra=z, align=C)
            data_row(ws, i, 5, order.get("category", ""), zebra=z, align=L)
            merge_band(ws, i, 6, 7, order.get("description", ""), font="data",
                       fill_color=PALE_BLUE if z else None, align=L, border=_box())
    else:
        merge_band(ws, 38, 2, 7, "No emergency work orders", font="data", fill_color=None, align=C, border=_box())

    merge_band(ws, 46, 2, 7, f"Generated by FIRE Capital MMR Summary Tool | {datetime.now().strftime('%m/%d/%Y %I:%M %p')}",
               font=_FONTS["meta"], fill_color=None, align=C)

    # Clean up any leftover chart_data sheet from previous runs
    if "chart_data" in wb.sheetnames:
        del wb["chart_data"]

    # Right side: charts (matplotlib PNGs embedded as images).
    merge_band(ws, 6, 9, 16, "PROJECTED OCCUPANCY", font="hdr", fill_color=DARK_BLUE, align=C)
    add_projected_occupancy_chart(ws, bs.get("proj_occ", []))
    merge_band(ws, 21, 9, 16, "EXPIRING LEASES BY MONTH", font="hdr", fill_color=DARK_BLUE, align=C)
    add_expiring_leases_chart(ws, el)

    return ws


def sheet_by_name(wb, expected_name):
    """Find a worksheet by exact or normalized name."""
    if expected_name in wb.sheetnames:
        return wb[expected_name]
    expected = norm(expected_name)
    for name in wb.sheetnames:
        if norm(name) == expected:
            return wb[name]
    return None


def parse_optional_sheet(wb, sheet_name, parser, default_value, *args):
    ws = sheet_by_name(wb, sheet_name)
    if ws is None:
        print(f"WARNING: Missing tab '{sheet_name}' — using blank defaults.")
        return default_value
    try:
        return parser(ws, *args)
    except Exception as exc:
        print(f"WARNING: Could not parse tab '{sheet_name}' ({exc}) — using blank defaults.")
        return default_value


def default_box_score():
    return {
        "property_name": "",
        "date_range": "",
        "printed": "",
        "total_units": 0,
        "occupied": 0,
        "vacant": 0,
        "pct_occ": 0.0,
        "prelease_count": None,
        "vacant_prelease_count": None,
        "notice_prelease_count": None,
        "on_notice": 0,
        "applied": 0,
        "approved": 0,
        "signed": 0,
        "proj_occ": [],
    }


def parse_appfolio(wb):
    """
    Parse an Appfolio-style Maple Valley MMR workbook and return a data dict
    whose structure exactly matches the format expected by build_summary() and
    process_mmr():  box_score / delinquency / rent_roll / available_units /
    expiring_leases / prospect_sources / work_orders.
    """
    import re as _re

    property_name     = "Maple Valley Apartments"
    date_range        = ""
    printed           = ""
    total_units       = 0
    occupied          = 0
    pct_occ           = 0.0
    total_rental      = 0.0
    avg_rent_val      = 0.0
    delinquency_total = 0.0
    delinquency_count = 0
    ready_list        = []
    wo_list           = []
    issue_counts      = {}

    # ── Period + printed date from Cash Flow header ───────────────────────
    if "Cash Flow" in wb.sheetnames:
        for row in wb["Cash Flow"].iter_rows(min_row=1, max_row=15, values_only=True):
            cell = str(row[0] or "").strip()
            if "Period Range:" in cell:
                date_range = cell
            elif "Exported On:" in cell:
                printed = cell.replace("Exported On:", "").strip()

    # ── Occupancy + scheduled rent from Rent Roll summary row ────────────
    # The summary row looks like: ["64 Units", …, "90.6% Occupied", …, 63694, …]
    if "Rent Roll" in wb.sheetnames:
        for row in wb["Rent Roll"].iter_rows(min_row=1, max_row=500, values_only=True):
            first  = str(row[0] or "").strip()
            status = str(row[4] or "").strip() if len(row) > 4 else ""
            # Grab property name from the property-header rows
            if "Maple Valley" in first and " - " in first and not property_name.endswith(first.split(" - ")[0]):
                property_name = first.split(" - ")[0].strip()
            # Summary row: "64 Units" / "Total 64 Units", status "90.6% Occupied"
            if "Units" in first and "Occupied" in status:
                m_u = _re.search(r"(\d+)\s+[Uu]nits", first)
                m_p = _re.search(r"([\d.]+)%\s*[Oo]ccupied", status)
                if m_u:
                    total_units = int(m_u.group(1))
                if m_p:
                    pct_occ  = float(m_p.group(1)) / 100.0
                    occupied = round(total_units * pct_occ)
                # Scheduled rent total is in the "Rent" column (index 7)
                if len(row) > 7 and isinstance(row[7], (int, float)):
                    total_rental = float(row[7])
                    if occupied > 0:
                        avg_rent_val = total_rental / occupied
                break   # only the first (non-"Total") summary row needed

    # ── Delinquency total + resident count from Delinquency sheet ────────
    # Columns (0-indexed): 0=Unit, 8=Amount Receivable
    if "Delinquency" in wb.sheetnames:
        header_seen = False
        for row in wb["Delinquency"].iter_rows(min_row=1, max_row=500, values_only=True):
            first = str(row[0] or "").strip()
            if not header_seen:
                if first == "Unit" and len(row) > 8:
                    header_seen = True
                continue
            if first.lower() == "total":
                if len(row) > 8 and isinstance(row[8], (int, float)) and row[8] > 0:
                    delinquency_total = float(row[8])
                break
            # Count resident rows (unit IDs look like "5704-100")
            if _re.match(r"^\d{4}-\d{3}$", first):
                val = row[8] if len(row) > 8 else None
                if isinstance(val, (int, float)) and val > 0:
                    delinquency_count += 1

    # ── Ready units from Vacancy sheet (Rent Ready == "Yes") ─────────────
    if "Vacancy" in wb.sheetnames:
        header_seen = False
        for row in wb["Vacancy"].iter_rows(min_row=1, max_row=500, values_only=True):
            if not header_seen:
                if row[0] == "Unit" and len(row) > 5 and "Rent Ready" in str(row[5] or ""):
                    header_seen = True
                continue
            unit_id = str(row[0] or "").strip()
            if not unit_id or unit_id.lower() == "total" or "Maple Valley" in unit_id:
                continue
            if str(row[5] or "").strip().lower() == "yes":
                ready_list.append({
                    "unit":    unit_id,
                    "type":    str(row[2] or "").strip(),
                    "section": "",
                    "status":  "Ready",
                })

    # ── Open work orders from Work Order sheet ────────────────────────────
    _OPEN = {"new", "new by appfolio", "assigned", "scheduled",
             "in progress", "waiting on parts", "estimate"}
    if "Work Order" in wb.sheetnames:
        header_seen = False
        s_col = wo_type_col = wo_num_col = unit_col = desc_col = notes_col = created_col = issue_col = None
        for row in wb["Work Order"].iter_rows(min_row=1, max_row=2000, values_only=True):
            if not header_seen:
                if row[0] == "Property":
                    header_seen = True
                    for idx, v in enumerate(row):
                        sv = str(v or "")
                        if sv == "Status":             s_col       = idx
                        elif sv == "Work Order Type":  wo_type_col = idx
                        elif sv == "Work Order Number":wo_num_col  = idx
                        elif sv == "Job Description":  desc_col    = idx
                        elif sv == "Instructions":     notes_col   = idx
                        elif sv == "Unit":             unit_col    = idx
                        elif sv == "Created At":       created_col = idx
                        elif sv == "Work Order Issue": issue_col   = idx
                    s_col       = s_col       or 7
                    wo_type_col = wo_type_col or 2
                    wo_num_col  = wo_num_col  or 4
                    desc_col    = desc_col    or 5
                    notes_col   = notes_col   or 6
                    unit_col    = unit_col    or 9
                    created_col = created_col or 11
                    issue_col   = issue_col   if issue_col is not None else 26
                continue
            if not (row[0] and "Maple Valley" in str(row[0] or "")):
                continue
            status = str(row[s_col] or "").strip().lower()
            if status in _OPEN:
                wo_type = str(row[wo_type_col] or "").strip()
                wo_list.append({
                    "number":     str(row[wo_num_col] or "").strip(),
                    "location":   str(row[unit_col]   or "").strip(),
                    "reported":   row[created_col] if created_col is not None and len(row) > created_col else None,
                    "category":   wo_type,
                    "description":str(row[desc_col]   or "").strip() if desc_col is not None and len(row) > desc_col else "",
                    "notes":      str(row[notes_col]  or "").strip() if notes_col is not None and len(row) > notes_col else "",
                    "issue_type": str(row[issue_col]  or "").strip() if len(row) > issue_col else "",
                    "status":     status,
                })

        filtered_wo_list = []
        count_map = {}
        for wo in wo_list:
            emergency_category = classify_emergency_work_order(wo)
            if not emergency_category:
                continue
            wo["source_category"] = wo.get("category", "")
            wo["category"] = emergency_category
            wo["date_reported"] = fmt_date(wo.get("reported"))
            filtered_wo_list.append(wo)
            count_map[emergency_category] = count_map.get(emergency_category, 0) + 1
        wo_list = filtered_wo_list
        issue_counts = {
            category: count_map[category]
            for category, _ in _EMERGENCY_WO_PATTERNS
            if count_map.get(category)
        }
        print(f"  Appfolio emergency work orders after filtering: {len(wo_list)}")
        for order in wo_list:
            print(f"    {order['number']} -> {order['category']}")

    return {
        "box_score": {
            "property_name": property_name,
            "date_range":    date_range,
            "printed":       printed,
            "total_units":   total_units,
            "occupied":      occupied,
            "vacant":        max(total_units - occupied, 0),
            "pct_occ":       pct_occ,
            "prelease_count": 0,
            "vacant_prelease_count": 0,
            "notice_prelease_count": 0,
            "on_notice":     0,
            "applied":       0,
            "approved":      0,
            "signed":        0,
            "proj_occ":      [],
        },
        "delinquency":  {"total": delinquency_total, "count": delinquency_count},
        "rent_roll":    {"total_rental": total_rental, "avg_rent": avg_rent_val},
        "available_units": {"ready_units": ready_list, "prelease_count": 0},
        "expiring_leases":  [],
        "prospect_sources": {},
        "work_orders": {"work_orders": wo_list, "issue_counts": issue_counts},
    }


# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print("Usage: python generate_summary.py <path_to_mmr.xlsx>")
        sys.exit(1)

    filepath = Path(sys.argv[1])
    if not filepath.exists():
        print(f"Error: file not found: {filepath}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  FIRE Capital MMR Summary Generator")
    print(f"  File: {filepath.name}")
    print(f"{'='*60}\n")

    wb = openpyxl.load_workbook(str(filepath), data_only=True)
    source_system = detect_source_system(wb)
    print(f"Source system: {source_system}")
    if source_system == "Unrecognized Format":
        print("WARNING: Workbook format is not recognized. Summary will contain placeholder values.")

    if source_system == "Resman":
        required = {
            "Box Score", "Delinquency", "Rent Roll",
            "Available Units", "Expiring Leases",
            "Prospect Source Summary", "Work Order Summary",
        }
        missing = {name for name in required if sheet_by_name(wb, name) is None}
        if missing:
            print(f"WARNING: Missing tabs: {missing}\n")

        print("Parsing Box Score ...")
        bs = parse_optional_sheet(wb, "Box Score", parse_box_score, default_box_score())
        print(f"  Property  : {bs['property_name']}")
        print(f"  Period    : {bs['date_range']}")
        print(f"  Occupancy : {bs['occupied']}/{bs['total_units']} units  ({fmt_pct(bs['pct_occ'])})")
        print(f"  On-Notice : {bs['on_notice']}   Applied/Approved/Signed: {bs['applied']}/{bs['approved']}/{bs['signed']}")

        print("\nParsing Delinquency ...")
        dl = parse_optional_sheet(wb, "Delinquency", parse_delinquency, {"total": None, "count": None})
        print(f"  Total delinquency: {('$' + format(dl['total'], ',.2f')) if dl.get('total') is not None else 'N/A'}")

        print("\nParsing Rent Roll ...")
        rr = parse_optional_sheet(wb, "Rent Roll", parse_rent_roll, {"total_rental": None, "avg_rent": None}, bs["occupied"])
        print(f"  Total rental revenue : {('$' + format(rr['total_rental'], ',.2f')) if rr.get('total_rental') is not None else 'N/A'}")
        print(f"  Average rent / unit  : {('$' + format(rr['avg_rent'], ',.2f')) if rr.get('avg_rent') is not None else 'N/A'}")

        print("\nParsing Available Units ...")
        au = parse_optional_sheet(wb, "Available Units", parse_available_units, {"ready_units": None, "prelease_count": None})
        print(f"  Ready units : {len(au['ready_units']) if au.get('ready_units') is not None else 'N/A'}")
        print(f"  Preleases   : {au['prelease_count']}")

        print("\nParsing Expiring Leases ...")
        el = parse_optional_sheet(wb, "Expiring Leases", parse_expiring_leases, None, bs["date_range"])
        print(f"  Months: {[fmt_month(m['dt']) for m in el] if el is not None else 'N/A'}")

        print("\nParsing Prospect Sources ...")
        ps = parse_optional_sheet(wb, "Prospect Source Summary", parse_prospect_sources, None)

        print("\nParsing Work Orders ...")
        wo = parse_optional_sheet(wb, "Work Order Summary", parse_work_orders, {"work_orders": None, "issue_counts": {}})
        print(f"  Emergency work orders: {len(wo['work_orders']) if wo.get('work_orders') is not None else 'N/A'}")
        for k, v in wo["issue_counts"].items():
            if v:
                print(f"    {k}: {v}")
    elif source_system == "Appfolio":
        print("Appfolio format detected — parsing available data ...")
        appfolio = parse_appfolio(wb)
        bs = appfolio["box_score"]
        dl = appfolio["delinquency"]
        rr = appfolio["rent_roll"]
        au = appfolio["available_units"]
        el = appfolio["expiring_leases"]
        ps = appfolio["prospect_sources"]
        wo = appfolio["work_orders"]
        print(f"  Property  : {bs['property_name']}")
        print(f"  Period    : {bs['date_range']}")
        print(f"  Occupancy : {bs['occupied']}/{bs['total_units']} units  ({fmt_pct(bs['pct_occ'])})")
        print(f"  Delinquency : ${dl['total']:,.2f}  ({dl.get('count', 0)} residents)")
        print(f"  Total Rental: ${rr['total_rental']:,.2f}   Avg Rent: ${rr['avg_rent']:,.2f}")
        print(f"  Ready Units : {len(au['ready_units'])}")
        print(f"  Emergency WOs: {len(wo['work_orders'])}")
        for w in wo["work_orders"]:
            src = w.get("source_category") or w.get("issue_type") or ""
            print(f"    {w['number']:12s} -> {w['category']:<22s} | issue_type={src}")
    else:
        bs = extract_appfolio_box_score(wb, source_system)
        dl = {"total": 0.0}
        rr = {"total_rental": 0.0, "avg_rent": 0.0}
        au = {"ready_units": [], "prelease_count": 0}
        el = []
        ps = {}
        wo = {"work_orders": [], "issue_counts": {}}

    data = {
        "box_score":       bs,
        "delinquency":     dl,
        "rent_roll":       rr,
        "available_units": au,
        "expiring_leases": el,
        "prospect_sources": ps,
        "work_orders":     wo,
        "source_system":    source_system,
    }

    print("\nWriting Summary tab ...")
    build_summary(wb, data)
    wb.save(str(filepath))
    print(f"\n  Done!  Summary written to '{filepath.name}'\n")


if __name__ == "__main__":
    main()
