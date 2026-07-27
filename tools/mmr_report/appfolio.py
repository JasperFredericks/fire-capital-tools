from datetime import datetime
from datetime import timedelta

from tools.mmr_report.helpers import fmt_date
from tools.mmr_report.work_orders import _EMERGENCY_WO_PATTERNS, classify_emergency_work_order




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
    as_of_date        = None
    total_units       = 0
    occupied          = 0
    pct_occ           = 0.0
    total_rental      = 0.0
    avg_rent_val      = 0.0
    delinquency_total = 0.0
    delinquency_count = 0
    vacant_prelease_count  = 0
    notice_prelease_count  = 0
    on_notice_count        = 0
    eviction_count         = 0
    lease_expiration_dates = []   # Lease To dates for Current/Notice residents
    occupancy_events       = []   # (date, delta) — known future move-ins/move-outs
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
    #
    # Individual unit rows also carry a Status column whose values follow the
    # {Vacant,Notice}-{Rented,Unrented} convention (confirmed against the raw
    # Maple Valley export — the "-Unrented" suffix implies a "-Rented"
    # counterpart marks a vacant/notice unit that already has a signed future
    # lease, i.e. a pre-lease). We use the same pass to also collect Lease To
    # dates (for the Expiring Leases chart) and known Move-out dates for
    # on-notice residents (for the Projected Occupancy chart).
    _PRELEASE_STATUSES = {"vacant-rented", "notice-rented"}
    _OCCUPIED_STATUSES = {"current", "notice-unrented", "notice-rented"}
    if "Rent Roll" in wb.sheetnames:
        for row in wb["Rent Roll"].iter_rows(min_row=1, max_row=500, values_only=True):
            first  = str(row[0] or "").strip()
            status = str(row[4] or "").strip() if len(row) > 4 else ""
            status_norm = status.strip().lower()
            if as_of_date is None and first.lower().startswith("as of:"):
                try:
                    as_of_date = datetime.strptime(first.split(":", 1)[-1].strip(), "%m/%d/%Y")
                except ValueError:
                    pass
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

            if status_norm in _PRELEASE_STATUSES:
                if status_norm == "vacant-rented":
                    vacant_prelease_count += 1
                else:
                    notice_prelease_count += 1

            # On-notice count: residents who gave notice, whether or not the
            # unit is already re-leased (Notice-Rented is a subset of this,
            # not a separate population — mirrors ResMan's Box Score, where
            # "On-Notice" is the total and "On-Notice Pre-Leased" a subset).
            if status_norm in ("notice-unrented", "notice-rented"):
                on_notice_count += 1

            # Eviction: still occupied (not vacant), and legally/practically
            # distinct from a resident who gave notice — counted on its own,
            # confirmed against the real Maple Valley export's "Evict" status.
            if status_norm == "evict":
                eviction_count += 1

            if status_norm in _OCCUPIED_STATUSES and len(row) > 10 and isinstance(row[10], datetime):
                lease_expiration_dates.append(row[10])

            # "Move-out" (col 13 / index 12) is the resident's known departure
            # date once they're on notice — the only concretely-known future
            # vacate event Appfolio's export exposes.
            if status_norm in ("notice-unrented", "notice-rented") and len(row) > 12 and isinstance(row[12], datetime):
                occupancy_events.append((row[12], -1))

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
            # "Next Move In" (col 14 / index 13) is the only concretely-known
            # future move-in date this export exposes for vacant/notice units.
            if len(row) > 13 and isinstance(row[13], datetime):
                occupancy_events.append((row[13], 1))

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
                # "Unit Turn" work orders are vacant-unit make-ready punch
                # lists (paint, cleaning, appliance install checklists,
                # etc.), not resident-reported problems — never eligible for
                # emergency classification regardless of description wording.
                if wo_type.strip().lower() == "unit turn":
                    continue
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

    # ── Expiring Leases (by month) from Rent Roll "Lease To" dates ─────────
    # Appfolio's export has no equivalent to ResMan's "Renewal Start" signal,
    # so renewals are always reported as 0 here — a known data gap, not a
    # miscount.
    reference_date = as_of_date or datetime.now()
    start_key = (reference_date.year, reference_date.month)
    lease_month_counts: dict = {}
    for dt in lease_expiration_dates:
        key = (dt.year, dt.month)
        if key < start_key:
            continue
        lease_month_counts[key] = lease_month_counts.get(key, 0) + 1
    expiring_leases = [
        {"dt": datetime(year, month, 1), "expirations": count, "renewals": 0}
        for (year, month), count in sorted(lease_month_counts.items())
    ][:10]

    # ── Projected Occupancy from known future move-in/move-out events ─────
    # Unlike Resman's Box Score (a system-generated multi-week leasing
    # pipeline projection), Appfolio's export only exposes concretely-known
    # events: a Move-out date once a resident is on notice, and a Next Move
    # In date once a vacant/notice unit has a signed future lease. Weeks with
    # no known event simply carry the last known occupied count forward.
    proj_occ = []
    if total_units:
        occupancy_events.sort(key=lambda ev: ev[0])
        running_occupied = occupied
        event_idx = 0
        point_date = reference_date
        for _ in range(20):
            while event_idx < len(occupancy_events) and occupancy_events[event_idx][0] <= point_date:
                running_occupied += occupancy_events[event_idx][1]
                event_idx += 1
            running_occupied = max(0, min(total_units, running_occupied))
            proj_occ.append({
                "date": point_date,
                "occ":  running_occupied,
                "pct":  running_occupied / total_units,
            })
            point_date = point_date + timedelta(days=7)

    prelease_count = vacant_prelease_count + notice_prelease_count

    return {
        "box_score": {
            "property_name": property_name,
            "date_range":    date_range,
            "printed":       printed,
            "total_units":   total_units,
            "occupied":      occupied,
            "vacant":        max(total_units - occupied, 0),
            "pct_occ":       pct_occ,
            "prelease_count": prelease_count,
            "vacant_prelease_count": vacant_prelease_count,
            "notice_prelease_count": notice_prelease_count,
            "on_notice":     on_notice_count,
            "applied":       0,
            "approved":      0,
            "signed":        0,
            "proj_occ":      proj_occ,
        },
        "delinquency":  {"total": delinquency_total, "count": delinquency_count},
        "rent_roll":    {"total_rental": total_rental, "avg_rent": avg_rent_val},
        "available_units": {"ready_units": ready_list, "prelease_count": prelease_count, "holding_count": None, "eviction_count": eviction_count},
        "expiring_leases":  expiring_leases,
        # No prospect/lead-source data exists anywhere in Appfolio's export
        # (checked all 9 sheets) — None (not {}) so build_summary renders its
        # existing "N/A" path instead of a table of blank dashes.
        "prospect_sources": None,
        "work_orders": {"work_orders": wo_list, "issue_counts": issue_counts},
    }
