import re

from tools.mmr_report.helpers import find_col, fmt_date, is_junk_row, looks_like_group_header, norm, rows_of, safe_get




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
        r"\bflush(?:ing)?\b.{0,30}\bnot working\b",
        r"\bnot flushing\b",
        r"\bwon'?t flush\b",
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
        r"\bdoor is off (?:its|the|it'?s|his|her)?\s*hinges?\b",
        r"\bdoor\b.{0,60}\bhinges?\b.{0,60}\b(?:broken|unscrewed|not screwed in|detached)\b",
        r"\bhinges?\b.{0,60}\b(?:broken|unscrewed|not screwed in|detached)\b.{0,60}\bdoor\b",
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



_HVAC_EXCLUDE_PATTERNS = [
    r"\bheating coils?\b",
    r"\bheating elements?\b",
    r"\bair filters?\b",
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
    r"\bfilters?\b",
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
        r"repair|replace|out of (?:order|service)|stopped working|stopped|isn't draining|is not draining|"
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
    # A mold mention that's only a hypothetical future consequence ("can
    # cause mold") isn't itself the reported problem — let the ticket fall
    # through to whatever category actually describes the current issue
    # (e.g. a leaking faucet) instead of mislabeling it as Mold/Mildew.
    _MOLD_HYPOTHETICAL_PATTERNS = [r"\b(?:can|could|may|might)\s+cause\s+mold\b"]
    if wo_matches(text, mold_patterns) and not wo_matches(text, _MOLD_HYPOTHETICAL_PATTERNS):
        return "Mold/Mildew"
    if issue_type_val in _ISSUE_TYPE_OVERRIDES:
        return _ISSUE_TYPE_OVERRIDES[issue_type_val]

    structural_patterns = next(p for c, p in _EMERGENCY_WO_PATTERNS if c == "Structural")
    if wo_matches(text, structural_patterns):
        return "Structural"

    # _NON_EMERGENCY_WO_PATTERNS is intentionally not checked here as a
    # blanket short-circuit: a ticket mentioning one routine/cosmetic item
    # (e.g. "blinds") alongside a genuinely separate emergency issue (e.g.
    # "flush not working") must not have the real signal suppressed just
    # because it isn't the only thing mentioned in the ticket. A category
    # match below always wins; a ticket that matches nothing here already
    # returns None regardless of whether it also contains a denylisted term.
    for category, patterns in _EMERGENCY_WO_PATTERNS:
        if category in ("Mold/Mildew", "Structural"):
            continue  # already handled above
        if not wo_matches(text, patterns):
            continue
        if category == "HVAC/AC" and wo_matches(text, _HVAC_EXCLUDE_PATTERNS):
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
                elif hn == "make ready":
                    col_map["make_ready"] = c
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

            # Make-Ready rows are vacant-unit turn/punch-list items (paint,
            # cleaning, lock changes, appliance install checklists, etc.),
            # not resident-reported problems — never eligible for emergency
            # classification regardless of what words their description uses.
            make_ready_col = col_map.get("make_ready")
            if make_ready_col is not None and str(safe_get(row, make_ready_col) or "").strip():
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
