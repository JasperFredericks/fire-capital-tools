#!/usr/bin/env python3
"""Add BLS LAUS resident employment growth columns to Clean Cities 100k+.

Inputs (official BLS LAUS bulk files):
- data/cache/bls_laus/la.area
- data/cache/bls_laus/la.series
- data/cache/bls_laus/la.data.65.City

Measure code used: 05 (employment)
Period used: M13 (annual average)
"""

import csv
import datetime as dt
import difflib
import re
import shutil
from collections import defaultdict
from pathlib import Path

from openpyxl import load_workbook


BASE_DIR = Path(__file__).resolve().parent
INPUT_WORKBOOK = BASE_DIR / "output" / "us_cities_100k_population_ranked_WITH_HOME_VALUE_GROWTH.xlsx"
OUTPUT_WORKBOOK = BASE_DIR / "output" / "us_cities_100k_population_ranked_WITH_JOB_GROWTH_FIXED.xlsx"
PREVIOUS_WORKBOOK = BASE_DIR / "output" / "us_cities_100k_population_ranked_WITH_JOB_GROWTH.xlsx"

BLS_DIR = BASE_DIR / "data" / "cache" / "bls_laus"
BLS_AREA = BLS_DIR / "la.area"
BLS_SERIES = BLS_DIR / "la.series"
BLS_CITY_DATA = BLS_DIR / "la.data.65.City"
BLS_MEASURE = BLS_DIR / "la.measure"

STATE_TO_ABBR = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA",
    "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE", "DISTRICT OF COLUMBIA": "DC",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL",
    "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA",
    "MAINE": "ME", "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR",
    "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA",
    "WASHINGTON": "WA", "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
}

PUNCT = re.compile(r"[^\w\s]", re.U)
MULTI = re.compile(r"\s+")
TRAILING_DIGITS = re.compile(r"\d+$")
REMOVE_WORDS = {
    "city",
    "town",
    "village",
    "borough",
    "municipality",
    "county",
    "cdp",
    "urban",
    "balance",
    "metro",
    "metropolitan",
    "unified",
    "government",
    "consolidated",
    "county/city",
}

# Targeted aliases for workbook city names that do not match LAUS names 1:1.
ALIASES = {
    ("PA", "philadelphia"): [
        "philadelphia city",
        "philadelphia county city",
    ],
    ("CA", "san francisco"): [
        "san francisco city",
        "san francisco county city",
    ],
    ("CO", "denver"): [
        "denver city",
        "denver county city",
    ],
    ("HI", "urban honolulu"): [
        "honolulu cdp",
        "urban honolulu cdp",
        "honolulu city",
        "honolulu county",
    ],
    ("KY", "lexington fayette"): [
        "lexington fayette urban county",
        "lexington fayette",
        "lexington city",
        "fayette county",
    ],
    ("AK", "anchorage"): [
        "anchorage municipality",
        "anchorage",
        "anchorage borough municipality",
    ],
    ("GA", "columbus"): [
        "columbus city",
        "columbus",
        "muscogee county",
    ],
    ("GA", "augusta richmond"): [
        "augusta richmond county",
        "augusta",
        "richmond county",
    ],
    ("GA", "macon bibb"): [
        "macon bibb county",
        "macon",
        "bibb county",
    ],
    ("GA", "athens clarke"): [
        "athens clarke county",
        "athens",
        "clarke county",
    ],
    ("TN", "nashville davidson"): [
        "nashville davidson",
        "nashville",
        "davidson county",
    ],
    ("KY", "louisville jefferson"): [
        "louisville jefferson county",
        "louisville",
        "jefferson county",
    ],
    ("DC", "washington"): [
        "district of columbia",
        "washington city",
    ],
    ("IN", "indianapolis"): [
        "indianapolis consolidated",
        "indianapolis incorporated",
    ],
    ("CA", "san buenaventura ventura"): ["san buenaventura"],
}


def normalize_city(value):
    text = "" if value is None else str(value).strip().lower()
    text = PUNCT.sub(" ", text)
    text = TRAILING_DIGITS.sub("", text).strip()
    text = MULTI.sub(" ", text)
    parts = [p for p in text.split(" ") if p]
    while parts and parts[-1] in REMOVE_WORDS:
        parts.pop()
    return " ".join(parts)


def base_city_key(normalized_city):
    parts = [p for p in normalized_city.split(" ") if p and p not in REMOVE_WORDS]
    return " ".join(parts)


def normalize_state_abbr(value):
    raw = "" if value is None else str(value).strip().upper()
    raw = TRAILING_DIGITS.sub("", raw).strip()
    if len(raw) == 2 and raw.isalpha():
        return raw
    return STATE_TO_ABBR.get(raw, "")


def require_inputs():
    missing = [p for p in (BLS_AREA, BLS_SERIES, BLS_CITY_DATA) if not p.exists()]
    if missing:
        msg = ["Missing required BLS LAUS file(s):"]
        msg.extend([f"- {p}" for p in missing])
        msg.append("Download official files from https://download.bls.gov/pub/time.series/la/ and place them in data/cache/bls_laus/.")
        raise FileNotFoundError("\n".join(msg))


def verify_measure_code():
    if not BLS_MEASURE.exists():
        return
    with BLS_MEASURE.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter="\t")
        next(reader, None)
        for row in reader:
            if len(row) >= 2 and row[0].strip() == "05":
                if row[1].strip().lower() != "employment":
                    raise RuntimeError("BLS measure code 05 is not employment in la.measure")
                return


def parse_area_file():
    area_by_code = {}
    state_areas = defaultdict(list)
    with BLS_AREA.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter="\t")
        next(reader, None)
        for row in reader:
            if len(row) < 3:
                continue
            area_type = row[0].strip()
            area_code = row[1].strip()
            area_text = row[2].strip()
            m = re.match(r"^(.*?),\s*([A-Z]{2})(?:\b|\s|$)", area_text)
            if not m:
                continue
            city_part = m.group(1).strip()
            st = m.group(2).strip()
            area_by_code[area_code] = {
                "area_type": area_type,
                "area_code": area_code,
                "area_text": area_text,
                "state": st,
                "city_norm": normalize_city(city_part),
                "base_key": base_city_key(normalize_city(city_part)),
            }
            state_areas[st].append(area_by_code[area_code])
    return area_by_code, state_areas


def parse_series_file(area_by_code):
    series_to_area = {}
    with BLS_SERIES.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter="\t")
        next(reader, None)
        for row in reader:
            if len(row) < 4:
                continue
            series_id = row[0].strip()
            area_type = row[1].strip()
            area_code = row[2].strip()
            measure_code = row[3].strip()

            if measure_code != "05":
                continue
            if area_type not in {"G", "I"}:
                continue
            if area_code not in area_by_code:
                continue

            series_to_area[series_id] = area_code
    return series_to_area


def parse_city_annual_data(series_to_area):
    annual_by_series = defaultdict(dict)
    latest_year = 0

    with BLS_CITY_DATA.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter="\t")
        next(reader, None)
        for row in reader:
            if len(row) < 4:
                continue
            series_id = row[0].strip()
            if series_id not in series_to_area:
                continue

            year_raw = row[1].strip()
            period = row[2].strip()
            value_raw = row[3].strip()

            if period != "M13":
                continue
            try:
                year = int(year_raw)
                value = float(value_raw)
            except Exception:
                continue
            if year < 2021:
                continue

            annual_by_series[series_id][year] = value
            if year > latest_year:
                latest_year = year

    if latest_year == 0:
        raise RuntimeError("No annual M13 data found in la.data.65.City for measure 05")

    prior_year = latest_year - 1
    return annual_by_series, prior_year, latest_year


def choose_best(candidates):
    if not candidates:
        return None
    # Prefer direct city records (G), then split-city records (I), then higher latest value.
    def score(rec):
        area_priority = 1 if rec["area_type"] == "G" else 0
        latest_val = rec.get("latest")
        latest_score = latest_val if latest_val is not None else -1
        return (area_priority, latest_score)

    return sorted(candidates, key=score, reverse=True)[0]


def choose_by_population(candidates, population):
    if not candidates:
        return None
    if population is None:
        return choose_best(candidates)

    scored = []
    for rec in candidates:
        latest = rec.get("latest")
        if latest is None:
            continue
        diff = abs(latest - population)
        rel = diff / max(float(population), 1.0)
        scored.append((rel, diff, rec))

    if not scored:
        return choose_best(candidates)

    scored.sort(key=lambda x: (x[0], x[1]))
    best_rel, _, best = scored[0]
    if best_rel > 0.80:
        return None
    if len(scored) > 1:
        second_rel = scored[1][0]
        if second_rel - best_rel < 0.05:
            return None
    return best


def closest_same_state_candidates(st, city_norm, state_areas, limit=10):
    candidates = []
    for area in state_areas.get(st, []):
        cand = area.get("city_norm", "")
        ratio = difflib.SequenceMatcher(None, city_norm, cand).ratio()
        token_overlap = len(set(city_norm.split()) & set(cand.split()))
        candidates.append((ratio, token_overlap, area.get("area_text", ""), area.get("area_type", "")))
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    out = []
    seen = set()
    for ratio, _, text, area_type in candidates:
        if text in seen:
            continue
        seen.add(text)
        out.append((text, area_type, round(ratio, 4)))
        if len(out) >= limit:
            break
    return out


def build_lookup(area_by_code, series_to_area, annual_by_series, prior_year, latest_year):
    by_key = defaultdict(list)

    for series_id, years in annual_by_series.items():
        area_code = series_to_area.get(series_id)
        area = area_by_code.get(area_code)
        if not area:
            continue

        rec = {
            "state": area["state"],
            "city_norm": area["city_norm"],
            "area_text": area["area_text"],
            "series_id": series_id,
            "area_type": area["area_type"],
            "v2021": years.get(2021),
            "vprior": years.get(prior_year),
            "latest": years.get(latest_year),
        }
        by_key[(rec["state"], rec["city_norm"])].append(rec)

    return by_key


def find_header_index(ws, names):
    headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
    norm = [str(h).strip().lower() if h is not None else "" for h in headers]
    for name in names:
        if name in norm:
            return norm.index(name) + 1
    return None


def remove_existing_job_columns(ws):
    prefixes = (
        "resident employment in ",
        "resident employment growth ",
        "bls laus area name",
        "bls laus series id",
    )
    to_delete = []
    for c in range(1, ws.max_column + 1):
        h = ws.cell(1, c).value
        text = str(h).strip().lower() if h is not None else ""
        if any(text.startswith(p) for p in prefixes):
            to_delete.append(c)
    for c in reversed(to_delete):
        ws.delete_cols(c, 1)


def append_job_columns(ws, prior_year, latest_year, by_key, state_areas):
    city_col = find_header_index(ws, ["city"])
    state_col = find_header_index(ws, ["state_abbr", "state"])
    state_name_col = find_header_index(ws, ["state"])
    pop_col = find_header_index(ws, ["population_2025", "population", "population_2024"])

    if city_col is None or state_col is None:
        raise RuntimeError("Could not find city/state columns in Clean Cities 100k+ sheet")

    remove_existing_job_columns(ws)

    start = ws.max_column + 1
    headers = [
        "Resident employment in 2021",
        f"Resident employment in {prior_year}",
        f"Resident employment in {latest_year}",
        f"Employment growth 2021-{latest_year} (%)",
        f"Employment growth {prior_year}-{latest_year} (%)",
        "BLS LAUS area name",
        "BLS LAUS series ID",
    ]
    for i, h in enumerate(headers):
        ws.cell(1, start + i, h)

    matched = 0
    unmatched = []
    unmatched_candidates = {}
    sanity = []

    for r in range(2, ws.max_row + 1):
        city = ws.cell(r, city_col).value
        raw_state = ws.cell(r, state_col).value
        state_name = ws.cell(r, state_name_col).value if state_name_col else raw_state

        st = normalize_state_abbr(raw_state)
        if not st:
            st = normalize_state_abbr(state_name)
        city_norm = normalize_city(city)
        base_key = base_city_key(city_norm)

        population = None
        if pop_col is not None:
            pv = ws.cell(r, pop_col).value
            try:
                population = float(pv) if pv is not None else None
            except Exception:
                population = None

        direct = choose_best(by_key.get((st, city_norm), []))
        if direct is None:
            direct = choose_best(by_key.get((st, base_key), []))
        rec = direct

        if rec is None:
            alias_candidates = []
            for alias_city in ALIASES.get((st, base_key), []):
                alias_norm = normalize_city(alias_city)
                alias_base = base_city_key(alias_norm)
                alias_candidates.extend(by_key.get((st, alias_norm), []))
                alias_candidates.extend(by_key.get((st, alias_base), []))
            if alias_candidates:
                # De-duplicate by series ID before population tie-break.
                dedup = {}
                for c in alias_candidates:
                    dedup[c["series_id"]] = c
                rec = choose_by_population(list(dedup.values()), population)

        c2021 = ws.cell(r, start)
        cprior = ws.cell(r, start + 1)
        clatest = ws.cell(r, start + 2)
        g1 = ws.cell(r, start + 3)
        g2 = ws.cell(r, start + 4)
        area_name = ws.cell(r, start + 5)
        series_id = ws.cell(r, start + 6)

        if rec is None:
            unmatched.append((city, st))
            unmatched_candidates[(city, st)] = closest_same_state_candidates(st, city_norm, state_areas, limit=10)
            continue

        v2021 = rec.get("v2021")
        vprior = rec.get("vprior")
        vlast = rec.get("latest")

        c2021.value = v2021
        cprior.value = vprior
        clatest.value = vlast
        area_name.value = rec.get("area_text")
        series_id.value = rec.get("series_id")

        if v2021 and vlast and v2021 != 0:
            g1.value = (vlast - v2021) / v2021
        else:
            g1.value = None

        if vprior and vlast and vprior != 0:
            g2.value = (vlast - vprior) / vprior
        else:
            g2.value = None

        c2021.number_format = "#,##0"
        cprior.number_format = "#,##0"
        clatest.number_format = "#,##0"
        g1.number_format = "0.00%"
        g2.number_format = "0.00%"

        matched += 1
        if len(sanity) < 5:
            sanity.append((city, st, v2021, vprior, vlast, g1.value, g2.value, rec.get("area_text")))

    return matched, unmatched, sanity, unmatched_candidates


def get_matched_set(workbook_path):
    if not workbook_path.exists():
        return set()
    wb = load_workbook(workbook_path, read_only=True, data_only=True)
    if "Clean Cities 100k+" not in wb.sheetnames:
        return set()
    ws = wb["Clean Cities 100k+"]

    headers = [str(c.value).strip().lower() if c.value is not None else "" for c in next(ws.iter_rows(min_row=1, max_row=1))]
    try:
        city_i = headers.index("city")
    except ValueError:
        return set()

    state_i = headers.index("state_abbr") if "state_abbr" in headers else (headers.index("state") if "state" in headers else None)
    series_i = headers.index("bls laus series id") if "bls laus series id" in headers else None
    if state_i is None or series_i is None:
        return set()

    matched = set()
    for row in ws.iter_rows(min_row=2, values_only=True):
        city = row[city_i]
        state = row[state_i]
        sid = row[series_i]
        if city is None or state is None or sid in (None, ""):
            continue
        matched.add((str(city).strip(), normalize_state_abbr(state)))
    return matched


def append_readme_note(wb, prior_year, latest_year):
    if "README" not in wb.sheetnames:
        return

    ws = wb["README"]
    note = (
        "Resident employment uses BLS LAUS (Local Area Unemployment Statistics) bulk files "
        "la.area, la.series, and la.data.65.City with measure code 05 (employment) and annual "
        f"average period M13 for years 2021, {prior_year}, and {latest_year}."
    )

    for r in range(1, ws.max_row + 1):
        v = ws.cell(r, 1).value
        if v is not None and note in str(v):
            return

    ws.append([""])
    ws.append([note])


def main():
    if not INPUT_WORKBOOK.exists():
        raise FileNotFoundError(f"Input workbook not found: {INPUT_WORKBOOK}")

    require_inputs()
    verify_measure_code()

    previous_matched = get_matched_set(PREVIOUS_WORKBOOK)

    area_by_code, state_areas = parse_area_file()
    series_to_area = parse_series_file(area_by_code)
    annual_by_series, prior_year, latest_year = parse_city_annual_data(series_to_area)
    by_key = build_lookup(area_by_code, series_to_area, annual_by_series, prior_year, latest_year)

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = INPUT_WORKBOOK.with_name(f"{INPUT_WORKBOOK.stem}_backup_{stamp}{INPUT_WORKBOOK.suffix}")
    shutil.copy2(INPUT_WORKBOOK, backup_path)

    shutil.copy2(INPUT_WORKBOOK, OUTPUT_WORKBOOK)
    wb = load_workbook(OUTPUT_WORKBOOK)
    if "Clean Cities 100k+" not in wb.sheetnames:
        raise RuntimeError("Sheet 'Clean Cities 100k+' not found")

    ws = wb["Clean Cities 100k+"]
    matched, unmatched, sanity, unmatched_candidates = append_job_columns(ws, prior_year, latest_year, by_key, state_areas)
    append_readme_note(wb, prior_year, latest_year)
    wb.save(OUTPUT_WORKBOOK)

    current_matched = get_matched_set(OUTPUT_WORKBOOK)
    newly_matched = sorted(current_matched - previous_matched)

    total_rows = ws.max_row - 1
    failed = len(unmatched)
    match_rate = (matched / total_rows * 100.0) if total_rows else 0.0

    print(f"Output file path: {OUTPUT_WORKBOOK}")
    print(f"BLS years used: 2021, {prior_year}, {latest_year}")
    print(f"Total city rows: {total_rows}")
    print(f"Matched count: {matched}")
    print(f"Failed count: {failed}")
    print(f"Match rate: {match_rate:.2f}%")
    print(f"Newly matched cities compared with previous run: {len(newly_matched)}")
    for city, st in newly_matched:
        print(f"- {city}, {st}")
    print("Remaining unmatched cities:")
    for city, st in unmatched:
        print(f"- {city}, {st}")
        print("  Closest same-state BLS candidates:")
        for name, area_type, ratio in unmatched_candidates.get((city, st), []):
            print(f"  - {name} [type {area_type}] similarity={ratio}")
    print("5 sanity-check rows:")
    for city, st, v1, vp, vl, g1, g2, area in sanity:
        print(
            f"- {city}, {st} | 2021: {v1} | {prior_year}: {vp} | {latest_year}: {vl} "
            f"| growth 2021-{latest_year}: {g1} | growth {prior_year}-{latest_year}: {g2} | area: {area}"
        )


if __name__ == "__main__":
    main()
