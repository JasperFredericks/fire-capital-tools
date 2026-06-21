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

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ── Colours ────────────────────────────────────────────────────────────────

DARK_BLUE  = "1F4E79"
LIGHT_BLUE = "D6E4F0"
PALE_BLUE  = "EBF5FB"
MID_GRAY   = "595959"
LT_GRAY    = "808080"

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

# ══════════════════════════════════════════════════════════════════════════
#  PARSERS
# ══════════════════════════════════════════════════════════════════════════

def parse_box_score(ws):
    rows = rows_of(ws)

    # ── Header ────────────────────────────────────────────────────────────
    prop_name  = str(rows[0][0] or rows[5][0] or "").strip()
    date_range = str(rows[3][0] or "").strip()
    raw_print  = rows[4][0]
    if isinstance(raw_print, datetime):
        printed = raw_print.strftime("%m/%d/%Y")
    else:
        printed = str(raw_print or "").replace("Printed ", "").strip()

    # ── Occupancy Total row ───────────────────────────────────────────────
    # Locate the header row that has "Unit Type" + "Total Units"
    total_units = occupied = 0
    pct_occ = 0.0
    for i, row in enumerate(rows):
        if row[0] == "Unit Type" and row[1] == "Total Units":
            hrow = row
            c_units = next((c for c, h in enumerate(hrow) if h == "Total Units"), 1)
            c_occ   = next((c for c, h in enumerate(hrow) if h == "Occ"),         23)
            c_pct   = next((c for c, h in enumerate(hrow) if h == "% Occ"),       26)
            for trow in rows[i + 1:]:
                if trow[0] == "Total" and isinstance(trow[1], (int, float)):
                    total_units = int(trow[c_units] or 0)
                    occupied    = int(trow[c_occ]   or 0)
                    raw_pct     = trow[c_pct]
                    if isinstance(raw_pct, str):
                        pct_occ = float(raw_pct.strip("%")) / 100
                    elif isinstance(raw_pct, float):
                        pct_occ = raw_pct
                    else:
                        pct_occ = occupied / total_units if total_units else 0
                    break
            break

    vacant = total_units - occupied

    # ── On-Notice count (On Notice Summary section) ────────────────────────
    on_notice = 0
    for i, row in enumerate(rows):
        if row[0] and "On Notice Summary" in str(row[0]):
            for j, row2 in enumerate(rows[i + 1:], i + 1):
                if row2[0] == "Unit Type":
                    ntv_col = next(
                        (c for c, h in enumerate(row2) if h and "On Notice" in str(h)),
                        None,
                    )
                    for trow in rows[j + 1:]:
                        if trow[0] == "Total":
                            on_notice = int(trow[ntv_col] or 0) if ntv_col is not None else 0
                            break
                    break
            break

    # ── Applications / Renewals Total row ─────────────────────────────────
    applied = approved = signed = 0
    for i, row in enumerate(rows):
        if row[0] and "Applications and Renewals" in str(row[0]):
            for j, arow in enumerate(rows[i + 1:], i + 1):
                if "Applied" in arow and "Approved" in arow:
                    ca = arow.index("Applied")
                    capp = arow.index("Approved")
                    cs  = arow.index("Signed")
                    for trow in rows[j + 1:]:
                        if trow[0] == "Total":
                            applied  = int(trow[ca]   or 0)
                            approved = int(trow[capp] or 0)
                            signed   = int(trow[cs]   or 0)
                            break
                    break
            break

    # ── Projected Occupancy ───────────────────────────────────────────────
    proj_occ = []
    for i, row in enumerate(rows):
        if row[0] == "Projected Occupancy":
            for j, row2 in enumerate(rows[i + 1:], i + 1):
                if row2[0] == "Date":
                    # "Occupied Units" may appear twice (duplicate col header bug in some files)
                    occ_u_col = next(
                        (c for c, h in enumerate(row2) if h == "Occupied Units"), 17
                    )
                    for drow in rows[j + 1:]:
                        if isinstance(drow[0], datetime):
                            occ_u = drow[occ_u_col]
                            pct   = (occ_u / total_units) if (total_units and occ_u is not None) else None
                            proj_occ.append({"date": drow[0], "occ": occ_u, "pct": pct})
                        elif drow[0] and not isinstance(drow[0], datetime):
                            break
                    break
            break

    return {
        "property_name": prop_name,
        "date_range":    date_range,
        "printed":       printed,
        "total_units":   total_units,
        "occupied":      occupied,
        "vacant":        vacant,
        "pct_occ":       pct_occ,
        "on_notice":     on_notice,
        "applied":       applied,
        "approved":      approved,
        "signed":        signed,
        "proj_occ":      proj_occ[:20],
    }


def parse_delinquency(ws):
    rows = rows_of(ws)
    grand_total = 0.0
    # Last row where col[0] is a positive number AND col[9] holds the running total
    for row in rows:
        if isinstance(row[0], (int, float)) and row[0] > 0 and isinstance(row[9], (int, float)):
            grand_total = float(row[9])
    return {"total": grand_total}


_RENT_RE = re.compile(r"^rent\b", re.IGNORECASE)


def _is_rent_line(description: str) -> bool:
    """
    True for rent charges and rent concessions across all Resman property styles:
      'Rent', 'RENT', 'Rent Income'  → regex ^rent\b
      'HAP Rent'                      → explicit match
      'Concession …', 'Concessions'  → starts with 'concession' (amounts are negative)
    """
    d = description.strip()
    dl = d.lower()
    return (
        bool(_RENT_RE.match(d))
        or dl in ("hap rent",)
        or dl.startswith("concession")
    )


def parse_rent_roll(ws, occupied):
    rows = rows_of(ws)

    # Locate Description and Amount columns from the header row
    desc_col = amt_col = None
    for row in rows:
        for c, h in enumerate(row):
            if h == "Description" and desc_col is None:
                desc_col = c
            elif h == "Amount" and amt_col is None:
                amt_col = c
        if desc_col is not None and amt_col is not None:
            break

    total_rental = 0.0
    if desc_col is not None and amt_col is not None:
        for row in rows:
            d = row[desc_col]
            a = row[amt_col]
            if isinstance(a, (int, float)) and d and _is_rent_line(str(d)):
                total_rental += a

    avg_rent = total_rental / occupied if occupied else 0.0
    return {"total_rental": total_rental, "avg_rent": avg_rent}


def parse_available_units(ws):
    rows = rows_of(ws)

    SECTIONS = {
        "Vacant",
        "Notice to Vacate",
        "Vacant PreLeased",
        "Notice To Vacate PreLeased",
        "Notice to Vacate PreLeased",
    }
    PRELEASE = {
        "Vacant PreLeased",
        "Notice To Vacate PreLeased",
        "Notice to Vacate PreLeased",
    }

    current_section = None
    unit_col = type_col = status_col = None
    in_data = False
    all_units = []

    for row in rows:
        first = row[0]

        # ── Section header ─────────────────────────────────────────────
        if isinstance(first, str) and first.strip() in SECTIONS:
            current_section = first.strip()
            unit_col = type_col = status_col = None
            in_data = False
            continue

        # ── Column header row ─────────────────────────────────────────
        if current_section and first == "Unit" and len(row) > 1 and row[1] == "Unit Type":
            for c, h in enumerate(row):
                if h == "Unit":        unit_col   = c
                elif h == "Unit Type": type_col   = c
                elif h == "Unit Status": status_col = c
            in_data = True
            continue

        # ── Unit data rows ─────────────────────────────────────────────
        if in_data and unit_col is not None and status_col is not None:
            uval = row[unit_col]
            sval = row[status_col]
            tval = row[type_col] if type_col is not None else None

            # A unit row has a non-empty string unit number and a status value
            if (uval
                    and isinstance(uval, str)
                    and uval.strip()
                    and uval.strip() not in ("\n", "\r\n", "")
                    and not uval.startswith("*")
                    and not uval.startswith("©")
                    and sval is not None):
                all_units.append({
                    "unit":    uval.strip(),
                    "type":    str(tval or "").strip(),
                    "section": current_section,
                    "status":  str(sval or "").strip(),
                })

    ready_units    = [u for u in all_units if u["status"].strip() == "Ready"]
    prelease_count = sum(1 for u in all_units if u["section"] in PRELEASE)

    return {"ready_units": ready_units, "prelease_count": prelease_count}


def parse_expiring_leases(ws, date_range=""):
    rows = rows_of(ws)

    # Determine start month from report end date
    start_dt = datetime.now()
    if date_range:
        try:
            end_str = date_range.split(" - ")[-1].strip()
            start_dt = datetime.strptime(end_str, "%m/%d/%Y")
        except Exception:
            pass
    start_key = (start_dt.year, start_dt.month)

    # Find Renewal Start column
    renewal_col = None
    for row in rows:
        if row[0] == "Unit" and len(row) > 1 and row[1] == "Status":
            for c, h in enumerate(row):
                if h == "Renewal Start":
                    renewal_col = c
                    break
            break

    months: dict = {}
    current_month = None

    for row in rows:
        first = row[0]

        # Month header: e.g. "June 2026"
        if isinstance(first, str) and re.match(r"^[A-Za-z]+ \d{4}$", first.strip()):
            try:
                dt = datetime.strptime(first.strip(), "%B %Y")
                current_month = (dt.year, dt.month)
                if current_month not in months:
                    months[current_month] = {"dt": dt, "expirations": 0, "renewals": 0}
            except ValueError:
                pass
            continue

        if current_month is None:
            continue

        # Count row: small integer in col[0] indicating number of leases that month
        if (isinstance(first, (int, float))
                and 0 < first < 500
                and months[current_month]["expirations"] == 0):
            months[current_month]["expirations"] = int(first)
            continue

        # Unit row: non-empty string that is not a header / note
        if (isinstance(first, str)
                and first.strip()
                and not re.match(r"^[A-Za-z]+ \d{4}$", first.strip())
                and "Notes:" not in first
                and "Limit:"  not in first
                and "ResMan"  not in first
                and first.strip() not in ("Unit", "Status", "©")):
            if renewal_col is not None and row[renewal_col] is not None:
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

    METRICS = ["New Prospects", "Return Prospects", "New Apps", "Net Leases"]
    col_map: dict = {}
    header_idx = -1

    for i, row in enumerate(rows):
        if row[0] == "Source":
            header_idx = i
            for c, h in enumerate(row):
                if h in METRICS:
                    col_map[h] = c
            break

    if header_idx < 0:
        return {}

    sources = []
    for row in rows[header_idx + 1:]:
        first = row[0]
        if not first or not isinstance(first, str):
            continue
        if first in ("Totals",) or first.startswith("*") or first.startswith("©"):
            break
        sources.append(row)

    result = {}
    for metric in METRICS:
        if metric not in col_map:
            continue
        c = col_map[metric]
        ranked = sorted(
            [(str(row[0]), row[c] if isinstance(row[c], (int, float)) else 0) for row in sources],
            key=lambda x: x[1],
            reverse=True,
        )
        result[metric] = ranked[:2]

    return result


def parse_work_orders(ws):
    rows = rows_of(ws)

    OPEN_STATUSES = {"Not Started", "Submitted", "In Progress", "Scheduled", "On Hold"}

    # Find header row
    header_idx = -1
    col_map: dict = {}
    for i, row in enumerate(rows):
        if row[0] == "Number":
            header_idx = i
            for c, h in enumerate(row):
                if h in ("Number", "Location", "Reported", "Category", "Description"):
                    col_map[h] = c
            break

    work_orders = []
    current_status = None

    if header_idx >= 0:
        for row in rows[header_idx + 1:]:
            first = row[0]
            if isinstance(first, str) and first.strip() in OPEN_STATUSES:
                current_status = first.strip()
                continue
            # WO data rows have a large WO number (typically 4+ digits) in col 0
            if current_status and isinstance(first, (int, float)) and first > 999:
                loc = row[col_map.get("Location", 1)]
                work_orders.append({
                    "number":      int(first),
                    "location":    str(loc or "").strip(),
                    "reported":    row[col_map.get("Reported", 2)],
                    "category":    str(row[col_map.get("Category", 3)] or "").strip(),
                    "description": str(row[col_map.get("Description", 4)] or "").strip(),
                    "status":      current_status,
                })

    # Keyword classification
    KEYWORDS = {
        "Water Leak":  ["leak", "water damage", "drip"],
        "HVAC/AC":     ["hvac", " ac ", "a/c", "ac leak", "air filter", "heating",
                        "ventilation", "air condition"],
        "Broken":      ["broken", "broke ", "came off", "off the hinge", "fell off"],
        "Mold/Mildew": ["mold", "mildew"],
        "Fire":        ["fire"],
    }
    issue_counts = {k: 0 for k in KEYWORDS}
    for wo in work_orders:
        text = f"{wo['description']} {wo['category']}".lower()
        for issue, kws in KEYWORDS.items():
            if any(k in text for k in kws):
                issue_counts[issue] += 1

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
    "B": 28,
    "C": 22,
    "D": 25,
    "E": 16,
    "F": 52,
    "G": 16,
    "H": 12,
    "I": 12,
}


def build_summary(wb, data):
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

    required = {
        "Box Score", "Delinquency", "Rent Roll",
        "Available Units", "Expiring Leases",
        "Prospect Source Summary", "Work Order Summary",
    }
    missing = required - set(wb.sheetnames)
    if missing:
        print(f"WARNING: Missing tabs: {missing}\n")

    print("Parsing Box Score ...")
    bs = parse_box_score(wb["Box Score"])
    print(f"  Property  : {bs['property_name']}")
    print(f"  Period    : {bs['date_range']}")
    print(f"  Occupancy : {bs['occupied']}/{bs['total_units']} units  ({fmt_pct(bs['pct_occ'])})")
    print(f"  On-Notice : {bs['on_notice']}   Applied/Approved/Signed: {bs['applied']}/{bs['approved']}/{bs['signed']}")

    print("\nParsing Delinquency ...")
    dl = parse_delinquency(wb["Delinquency"])
    print(f"  Total delinquency: ${dl['total']:,.2f}")

    print("\nParsing Rent Roll ...")
    rr = parse_rent_roll(wb["Rent Roll"], bs["occupied"])
    print(f"  Total rental revenue : ${rr['total_rental']:,.2f}")
    print(f"  Average rent / unit  : ${rr['avg_rent']:,.2f}")

    print("\nParsing Available Units ...")
    au = parse_available_units(wb["Available Units"])
    print(f"  Ready units : {len(au['ready_units'])}")
    print(f"  Preleases   : {au['prelease_count']}")

    print("\nParsing Expiring Leases ...")
    el = parse_expiring_leases(wb["Expiring Leases"], bs["date_range"])
    print(f"  Months: {[fmt_month(m['dt']) for m in el]}")

    print("\nParsing Prospect Sources ...")
    ps = parse_prospect_sources(wb["Prospect Source Summary"])

    print("\nParsing Work Orders ...")
    wo = parse_work_orders(wb["Work Order Summary"])
    print(f"  Open work orders: {len(wo['work_orders'])}")
    for k, v in wo["issue_counts"].items():
        if v:
            print(f"    {k}: {v}")

    data = {
        "box_score":       bs,
        "delinquency":     dl,
        "rent_roll":       rr,
        "available_units": au,
        "expiring_leases": el,
        "prospect_sources": ps,
        "work_orders":     wo,
    }

    print("\nWriting Summary tab ...")
    build_summary(wb, data)
    wb.save(str(filepath))
    print(f"\n  Done!  Summary written to '{filepath.name}'\n")


if __name__ == "__main__":
    main()
