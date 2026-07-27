from tools.mmr_report.helpers import norm




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
