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
from datetime import datetime

import io

import matplotlib
matplotlib.use("Agg")   # headless — no display needed
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
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
    Rent Roll and Delinquency alone are not enough because Maple/AppFolio exports
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


def detect_maple(wb):
    """Detect the Maple Valley/AppFolio-style placeholder workbook."""
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
    if detect_maple(wb):
        return "Placeholder(Maple)"
    return "Unrecognized Format"


def source_system_display(source_system):
    if source_system == "Resman":
        return "✓ Resman Format", GREEN, PALE_GREEN
    if source_system == "Placeholder(Maple)":
        return "⚠ Placeholder(Maple) Format", AMBER, PALE_AMBER
    return "✗ Unrecognized Format", RED, PALE_RED


def extract_placeholder_box_score(wb, source_system):
    bs = default_box_score()
    if source_system == "Placeholder(Maple)":
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

    found_occ_table = False
    for i, row in enumerate(rows):
        row_norms = [norm(h) for h in row]
        if "unit type" not in row_norms or "total units" not in row_norms:
            continue
        c_units    = find_col(row, "total units")
        c_occ      = find_col(row, "occ", "occupied")
        c_pct      = find_col(row, "% occ", "% occupied", "occ %", "pct occ", "% occ.")
        c_prelease = find_col(row, "vacant pre-leased", "vacant preleased",
                              "pre-leased", "preleased", "pre leased")
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
                prelease_count = int(coerce_num(safe_get(trow, c_prelease), default=0)) if c_prelease is not None else None
                found_occ_table = True
                break
        if found_occ_table:
            break

    if not found_occ_table:
        print("WARNING: Occupancy table not found in Box Score.")
        prelease_count = None

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
        if norm(safe_get(row, 0)) == "projected occupancy":
            for j, row2 in enumerate(rows[i + 1:], i + 1):
                if norm(safe_get(row2, 0)) == "date":
                    occ_u_col = find_col(row2, "occupied units")
                    if occ_u_col is None:
                        occ_u_col = find_col_contains(row2, "occupied")
                    if occ_u_col is None:
                        print("WARNING: 'Occupied Units' column not found in Projected Occupancy.")
                        break
                    for drow in rows[j + 1:]:
                        d0 = safe_get(drow, 0)
                        if isinstance(d0, datetime):
                            occ_u_raw = safe_get(drow, occ_u_col)
                            occ_u = coerce_num(occ_u_raw, default=None)
                            pct   = (occ_u / total_units) if (total_units and occ_u is not None) else None
                            proj_occ.append({"date": d0, "occ": int(occ_u) if occ_u is not None and occ_u.is_integer() else occ_u, "pct": pct})
                        elif d0 and not isinstance(d0, datetime):
                            break
                    break
            break

    return {
        "property_name":   prop_name,
        "date_range":      date_range,
        "printed":         printed,
        "total_units":     total_units,
        "occupied":        occupied,
        "vacant":          vacant,
        "pct_occ":         pct_occ,
        "prelease_count":  prelease_count,   # from Box Score "Vacant Pre-Leased" column
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


def is_ready_status(status):
    s = re.sub(r"\s*\*+$", "", norm(status)).strip()
    return s == "ready"


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
        if isinstance(first, str):
            sn = norm(first)
            if sn in _ALL_AU_SECTIONS:
                current_section = sn
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

    # Ready: normalized status == "ready", across ALL sections
    # (Vacant, Notice to Vacate, Vacant PreLeased, Notice To Vacate PreLeased)
    ready_units = [u for u in all_units if is_ready_status(u["status"])]
    prelease_count = sum(1 for u in all_units if u["section"] in _PRELEASE_SECTIONS)

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


def parse_work_orders(ws):
    rows = rows_of(ws)

    header_idx = -1
    col_map: dict = {}
    for i, row in enumerate(rows):
        number_col = find_col(row, "number", "wo #", "work order #", "work order number")
        if number_col is not None and (find_col(row, "location") is not None or find_col(row, "reported") is not None):
            header_idx = i
            for c, h in enumerate(row):
                hn = norm(h)
                if hn in ("number", "wo #", "work order #", "work order number"):
                    col_map["number"] = c
                elif hn in ("location", "reported", "category", "description"):
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

            # Skip rows with no identifying data beyond the number
            if not any(str(v or "").strip() for v in (loc, rep, cat, desc)):
                continue

            work_orders.append({
                "number":      wo_num,
                "location":    str(loc  or "").strip(),
                "reported":    rep,
                "category":    str(cat  or "").strip(),
                "description": str(desc or "").strip(),
                "status":      current_status,
            })

    # Priority-ordered keyword classification — first match wins
    KEYWORDS = [
        ("HVAC/AC",     ["hvac", "a/c", " ac ", "heating", "ventilation",
                         "air condition", " heat "]),
        ("Water Leak",  ["leak", "water", "plumbing", "flood", "drip"]),
        ("Mold/Mildew", ["mold", "mildew", "fungus"]),
        ("Fire",        ["fire", "smoke", "alarm"]),
        ("Broken",      ["broken", "damaged", "won't", "not working",
                         "not turn on", "door", "blind", "appliance"]),
    ]
    issue_counts = {k: 0 for k, _ in KEYWORDS}
    for wo in work_orders:
        text = f"{wo['description']} {wo['category']}".lower()
        for issue, kws in KEYWORDS:
            if any(k in text for k in kws):
                issue_counts[issue] += 1
                break  # one category only

    return {"work_orders": work_orders, "issue_counts": issue_counts}


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
        ("Preleases",      au["prelease_count"]),
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
    ax.set_ylim(0.87, 1.00)
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


def make_download_filename(property_name: str, date_range: str) -> str:
    """Return e.g. 'OXPT Summary 06.22.26.xlsx' from property name + date range."""
    pn = (property_name or "").strip()
    pn_lower = pn.lower()

    abbrev = None
    for key, val in _PROPERTY_ABBREVS.items():
        if key in pn_lower:
            abbrev = val
            break
    if abbrev is None:
        # First word, skipping "The" prefix
        words = pn.split()
        abbrev = words[1] if words and words[0].lower() == "the" and len(words) > 1 else (words[0] if words else "Property")

    date_str = ""
    if date_range:
        # Resman format: "6/14/2026 - 6/21/2026"
        try:
            end_str = date_range.split(" - ")[-1].strip()
            dt = datetime.strptime(end_str, "%m/%d/%Y")
            date_str = dt.strftime("%m.%d.%y")
        except Exception:
            pass
        # AppFolio/Maple format: "Period Range: Mar 2026 to May 2026 (Trailing...)"
        if not date_str:
            try:
                import re as _re
                import calendar as _cal
                m = _re.search(r"to\s+([A-Za-z]+\s+\d{4})", date_range)
                if m:
                    dt = datetime.strptime(m.group(1).strip(), "%b %Y")
                    last_day = _cal.monthrange(dt.year, dt.month)[1]
                    date_str = dt.replace(day=last_day).strftime("%m.%d.%y")
            except Exception:
                pass

    if date_str:
        return f"{abbrev} Summary {date_str}.xlsx"
    return f"{abbrev} Summary.xlsx"


def build_summary(wb, data):
    if "Summary" in wb.sheetnames:
        del wb["Summary"]
    ws = wb.create_sheet("Summary", 0)

    for col_letter, width in COL_WIDTHS.items():
        ws.column_dimensions[col_letter].width = width
    # Columns R–U no longer hold visible data; chart data lives in chart_data sheet

    bs = data.get("box_score", default_box_score())
    dl = data.get("delinquency", {"total": 0.0})
    rr = data.get("rent_roll", {"total_rental": 0.0, "avg_rent": 0.0})
    au = data.get("available_units", {"ready_units": [], "prelease_count": 0})
    el = data.get("expiring_leases", [])
    ps = data.get("prospect_sources", {})
    wo = data.get("work_orders", {"work_orders": [], "issue_counts": {}})
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
    write_pair_row(ws, 7, "% Occupancy", fmt_pct(bs.get("pct_occ")), "Occupied", bs.get("occupied"))
    write_pair_row(ws, 8, "Vacant", bs.get("vacant"), "Total Units", bs.get("total_units"))
    # Prefer Box Score "Vacant Pre-Leased" column value; fall back to Available Units count
    prelease_val = bs.get("prelease_count") if bs.get("prelease_count") is not None else au.get("prelease_count", 0)
    write_pair_row(ws, 9, "Preleases", prelease_val, "On-Notice", bs.get("on_notice"))

    section_band(ws, 11, "LEASING / FINANCIAL", 2, 7)
    write_pair_row(ws, 12, "Applied", bs.get("applied"), "Approved", bs.get("approved"))
    write_pair_row(ws, 13, "Signed", bs.get("signed"), "Total Delinquency", dl.get("total", 0.0), right_fmt='"$"#,##0.00')
    write_pair_row(ws, 14, "Total Rental Revenue", rr.get("total_rental", 0.0), "Average Rent / Unit", rr.get("avg_rent", 0.0),
                   left_fmt='"$"#,##0.00', right_fmt='"$"#,##0.00')

    section_band(ws, 16, "READY UNITS - VACANT & PRE-LEASED", 2, 7)
    for col, label in zip([2, 3, 5, 7], ["Unit", "Unit Type", "Section", "Status"]):
        col_hdr(ws, 17, col, label)
    ready_units = au.get("ready_units", [])
    if ready_units:
        for i, unit in enumerate(ready_units[:8], 18):
            z = (i - 18) % 2 == 0
            data_row(ws, i, 2, unit.get("unit", ""), zebra=z, align=C)
            data_row(ws, i, 3, unit.get("type", ""), zebra=z, align=L)
            data_row(ws, i, 5, unit.get("section", ""), zebra=z, align=L)
            data_row(ws, i, 7, unit.get("status", ""), zebra=z, align=C)
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

    section_band(ws, 35, f"OPEN WORK ORDERS ({len(wo.get('work_orders', []))} total)", 2, 7)
    wc(ws, 36, 2, "Issue Type", font="col_hdr", fill=_fill(LIGHT_BLUE), align=C, border=_box())
    wc(ws, 36, 3, "Count", font="col_hdr", fill=_fill(LIGHT_BLUE), align=C, border=_box())
    issue_counts = wo.get("issue_counts", {})
    issue_rows = list(issue_counts.items())[:5] or [("No classified issues", 0)]
    for i, (issue, count) in enumerate(issue_rows, 37):
        z = (i - 37) % 2 == 0
        data_row(ws, i, 2, issue, zebra=z, align=L)
        data_row(ws, i, 3, count, zebra=z, align=C)

    wc(ws, 36, 5, "Recent WO", font="col_hdr", fill=_fill(LIGHT_BLUE), align=C, border=_box())
    wc(ws, 36, 6, "Location", font="col_hdr", fill=_fill(LIGHT_BLUE), align=C, border=_box())
    wc(ws, 36, 7, "Category", font="col_hdr", fill=_fill(LIGHT_BLUE), align=C, border=_box())
    work_orders = wo.get("work_orders", [])
    if work_orders:
        for i, order in enumerate(work_orders[:5], 37):
            z = (i - 37) % 2 == 0
            data_row(ws, i, 5, order.get("number", ""), zebra=z, align=C)
            data_row(ws, i, 6, order.get("location", ""), zebra=z, align=C)
            data_row(ws, i, 7, order.get("category", ""), zebra=z, align=L)
    else:
        merge_band(ws, 37, 5, 7, "No open work orders", font="data", fill_color=None, align=C, border=_box())

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
        "on_notice": 0,
        "applied": 0,
        "approved": 0,
        "signed": 0,
        "proj_occ": [],
    }


def parse_maple(wb):
    """
    Parse an AppFolio-style Maple Valley MMR workbook and return a data dict
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
        s_col = wo_type_col = wo_num_col = unit_col = None
        for row in wb["Work Order"].iter_rows(min_row=1, max_row=2000, values_only=True):
            if not header_seen:
                if row[0] == "Property":
                    header_seen = True
                    for idx, v in enumerate(row):
                        sv = str(v or "")
                        if sv == "Status":           s_col       = idx
                        elif sv == "Work Order Type": wo_type_col = idx
                        elif sv == "Work Order Number": wo_num_col = idx
                        elif sv == "Unit":            unit_col    = idx
                    s_col       = s_col       or 7
                    wo_type_col = wo_type_col or 2
                    wo_num_col  = wo_num_col  or 4
                    unit_col    = unit_col    or 9
                continue
            if not (row[0] and "Maple Valley" in str(row[0] or "")):
                continue
            status = str(row[s_col] or "").strip().lower()
            if status in _OPEN:
                wo_type = str(row[wo_type_col] or "").strip()
                wo_list.append({
                    "number":   str(row[wo_num_col] or "").strip(),
                    "location": str(row[unit_col]   or "").strip(),
                    "category": wo_type,
                    "status":   status,
                })
                issue_counts[wo_type] = issue_counts.get(wo_type, 0) + 1

    return {
        "box_score": {
            "property_name": property_name,
            "date_range":    date_range,
            "printed":       printed,
            "total_units":   total_units,
            "occupied":      occupied,
            "vacant":        max(total_units - occupied, 0),
            "pct_occ":       pct_occ,
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
        dl = parse_optional_sheet(wb, "Delinquency", parse_delinquency, {"total": 0.0})
        print(f"  Total delinquency: ${dl['total']:,.2f}")

        print("\nParsing Rent Roll ...")
        rr = parse_optional_sheet(wb, "Rent Roll", parse_rent_roll, {"total_rental": 0.0, "avg_rent": 0.0}, bs["occupied"])
        print(f"  Total rental revenue : ${rr['total_rental']:,.2f}")
        print(f"  Average rent / unit  : ${rr['avg_rent']:,.2f}")

        print("\nParsing Available Units ...")
        au = parse_optional_sheet(wb, "Available Units", parse_available_units, {"ready_units": [], "prelease_count": 0})
        print(f"  Ready units : {len(au['ready_units'])}")
        print(f"  Preleases   : {au['prelease_count']}")

        print("\nParsing Expiring Leases ...")
        el = parse_optional_sheet(wb, "Expiring Leases", parse_expiring_leases, [], bs["date_range"])
        print(f"  Months: {[fmt_month(m['dt']) for m in el]}")

        print("\nParsing Prospect Sources ...")
        ps = parse_optional_sheet(wb, "Prospect Source Summary", parse_prospect_sources, {})

        print("\nParsing Work Orders ...")
        wo = parse_optional_sheet(wb, "Work Order Summary", parse_work_orders, {"work_orders": [], "issue_counts": {}})
        print(f"  Open work orders: {len(wo['work_orders'])}")
        for k, v in wo["issue_counts"].items():
            if v:
                print(f"    {k}: {v}")
    elif source_system == "Placeholder(Maple)":
        print("Maple Valley / AppFolio format detected — parsing available data ...")
        maple = parse_maple(wb)
        bs = maple["box_score"]
        dl = maple["delinquency"]
        rr = maple["rent_roll"]
        au = maple["available_units"]
        el = maple["expiring_leases"]
        ps = maple["prospect_sources"]
        wo = maple["work_orders"]
        print(f"  Property  : {bs['property_name']}")
        print(f"  Period    : {bs['date_range']}")
        print(f"  Occupancy : {bs['occupied']}/{bs['total_units']} units  ({fmt_pct(bs['pct_occ'])})")
        print(f"  Delinquency : ${dl['total']:,.2f}  ({dl.get('count', 0)} residents)")
        print(f"  Total Rental: ${rr['total_rental']:,.2f}   Avg Rent: ${rr['avg_rent']:,.2f}")
        print(f"  Ready Units : {len(au['ready_units'])}")
        print(f"  Open WOs    : {len(wo['work_orders'])}")
    else:
        bs = extract_placeholder_box_score(wb, source_system)
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
