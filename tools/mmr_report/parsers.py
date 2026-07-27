from datetime import datetime
import re

from tools.mmr_report.helpers import coerce_num, coerce_pct, debug_box_score_preleases, find_col, find_col_contains, find_first_text, is_junk_row, looks_like_group_header, looks_like_unit_value, norm, parse_projected_occupancy, rows_of, safe_get, safe_row
from tools.mmr_report.sheets import default_box_score




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


# Units currently in the eviction process — still occupied (not vacant) and
# not a "gave notice" status, so counted on its own rather than folded into
# Vacant or On-Notice. Only appears in the report at all when at least one
# unit is under eviction (confirmed against real Canyon/OXPT Available
# Units exports; Eagle Rock had none this period, section absent entirely).
_EVICTION_SECTIONS = {"under eviction"}


# Units being held for an approved applicant (holding fee paid, not yet
# moved in) — a section that only appears in the report at all when at
# least one unit is currently held, confirmed against real Available Units
# exports (Canyon, High Caliber).
_HOLDING_SECTIONS  = {"holding units", "holding"}


# Ready Units counts ready-status units from Vacant/Notice-to-Vacate AND
# Holding (a held unit can already be make-ready while awaiting move-in) --
# but not Eviction, which stays excluded from Ready even if a row is
# individually marked Ready.
_READY_ELIGIBLE_SECTIONS = _ALL_AU_SECTIONS | _HOLDING_SECTIONS


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

    # Ready: normalized status == "ready", from Vacant/Notice-to-Vacate and
    # Holding sections. Eviction-related sections are intentionally excluded
    # even when the row status says Ready.
    ready_units = [
        u for u in all_units
        if is_ready_status(u["status"]) and norm(u["section"]) in _READY_ELIGIBLE_SECTIONS
    ]
    prelease_count = sum(1 for u in all_units if norm(u["section"]) in _PRELEASE_SECTIONS)
    holding_count = sum(1 for u in all_units if norm(u["section"]) in _HOLDING_SECTIONS)
    eviction_count = sum(1 for u in all_units if norm(u["section"]) in _EVICTION_SECTIONS)

    return {
        "ready_units": ready_units,
        "prelease_count": prelease_count,
        "holding_count": holding_count,
        "eviction_count": eviction_count,
    }




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
