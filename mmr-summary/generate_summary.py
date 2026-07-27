#!/usr/bin/env python3
"""FIRE Capital MMR Summary Generator — CLI entry point.

Usage:
    python generate_summary.py "ERA_MMR_-_06_15_26.xlsx"

The parsing/sheet-building logic now lives in the importable package
tools/mmr_report/. This shim adds the repo root to sys.path so the package
is importable when the script is run standalone, then runs main() verbatim.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl
from tools.mmr_report import (
    build_summary,
    default_box_score,
    detect_source_system,
    extract_appfolio_box_score,
    fmt_month,
    fmt_pct,
    parse_appfolio,
    parse_available_units,
    parse_box_score,
    parse_delinquency,
    parse_expiring_leases,
    parse_optional_sheet,
    parse_prospect_sources,
    parse_rent_roll,
    parse_work_orders,
    sheet_by_name,
)




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
        au = parse_optional_sheet(wb, "Available Units", parse_available_units, {"ready_units": None, "prelease_count": None, "holding_count": None, "eviction_count": None})
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
        au = {"ready_units": [], "prelease_count": 0, "holding_count": 0, "eviction_count": 0}
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
