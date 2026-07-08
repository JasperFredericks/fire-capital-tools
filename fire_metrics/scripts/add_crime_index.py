#!/usr/bin/env python3
"""Build Crime Index from FBI Offenses Known to Law Enforcement data.

Uses official FBI CIUS Table 8 city-level crime statistics from:
"Offenses Known to Law Enforcement by State, by City" (2024)

Data source: https://cde.ucr.cjis.gov/LATEST/webapp/#/pages/downloads
File: CIUS_Table_8_Offenses_Known_to_Law_Enforcement_by_State_by_City_2024.xlsx
"""
import argparse
import datetime
import re
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
INPUT_FILE = BASE_DIR / "output" / "us_cities_100k_population_ranked_WITH_LANDLORD_AND_POP_CHANGE.xlsx"
OUTPUT_FILE = BASE_DIR / "output" / "us_cities_100k_population_ranked_WITH_CRIME_INDEX.xlsx"
CACHE_DIR = BASE_DIR / "data" / "cache" / "crime"
GAZETTEER_DIR = BASE_DIR / "data" / "cache" / "census_gazetteer"

# FBI CIUS Table 8 data file
FBI_TABLE_8_FILE = CACHE_DIR / "real_download_test" / "offenses-known-to-le-2024" / "CIUS_Table_8_Offenses_Known_to_Law_Enforcement_by_State_by_City_2024.xlsx"

CITY_COL_CANDIDATES = ["city", "City"]
STATE_COL_CANDIDATES = ["state", "State", "state_abbr"]  # Prefer full state names
POP_COL_CANDIDATES = ["population_2025", "population_2024", "population_2023", "population_2022"]

# Pattern to remove city type suffixes
PLACE_SUFFIX = re.compile(r"\b(city|town|village|cdp|municipality)\b$", re.I)
PUNCTUATION = re.compile(r"[^\w\s]", re.U)
MULTI_SPACE = re.compile(r"\s+")
HYPHEN_SLASH = re.compile(r"[-/]+")
TRAILING_DIGITS = re.compile(r"\d+$")

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

GAZETTEER_URLS = [
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/Gaz_places_national.zip",
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gaz_place_national.zip",
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gaz_place_national.zip",
]


def normalize_city_name(city_name):
    """Remove place type suffixes from city names for matching."""
    if pd.isna(city_name):
        return ""
    text = str(city_name).strip().lower()
    text = HYPHEN_SLASH.sub(" ", text)
    text = PUNCTUATION.sub(" ", text)
    # Remove suffixes like "city", "town", "village", "cdp", "municipality"
    text = PLACE_SUFFIX.sub("", text).strip()
    text = TRAILING_DIGITS.sub("", text).strip()
    text = PLACE_SUFFIX.sub("", text).strip()
    text = MULTI_SPACE.sub(" ", text)
    return text


def normalize_state_to_abbr(state_value):
    """Return 2-letter state code from full name or abbreviation."""
    if pd.isna(state_value):
        return ""
    raw = str(state_value).strip().upper()
    raw = TRAILING_DIGITS.sub("", raw).strip()
    if len(raw) == 2 and raw.isalpha():
        return raw
    return STATE_TO_ABBR.get(raw, raw)


def canonical_city_for_match(city_value, state_abbr):
    """Apply targeted aliases after baseline normalization."""
    city_norm = normalize_city_name(city_value)
    alias_map = {
        ("CA", "los angeles"): "los angeles",
        ("NY", "new york"): "new york",
        ("KY", "louisville jefferson county"): "louisville",
        ("KY", "louisville jefferson county metro government balance"): "louisville metro",
        ("TN", "nashville davidson"): "nashville",
        ("IN", "indianapolis"): "indianapolis",
        ("IN", "indianapolis city balance"): "indianapolis",
        ("FL", "jacksonville"): "jacksonville",
        ("HI", "urban honolulu"): "honolulu",
        ("DC", "washington"): "washington",
        ("ID", "boise city"): "boise",
        ("MO", "kansas"): "kansas city",
        ("KS", "kansas"): "kansas city",
        ("FL", "jacksonville city"): "jacksonville",
        ("GA", "atlanta city"): "atlanta",
        ("LA", "new orleans city"): "new orleans",
        ("MS", "jackson city"): "jackson",
    }
    return alias_map.get((state_abbr, city_norm), city_norm)


def get_rating(score):
    """Rating bands used for both base and density-adjusted crime scores."""
    if pd.isna(score):
        return ""
    s = float(score)
    if s <= 20:
        return "Very Low"
    if s <= 40:
        return "Low"
    if s <= 60:
        return "Moderate"
    if s <= 75:
        return "Elevated"
    if s <= 90:
        return "High"
    return "Very High"


def ensure_gazetteer_places_file():
    """Ensure Census Gazetteer place file is downloaded and extracted once."""
    GAZETTEER_DIR.mkdir(parents=True, exist_ok=True)

    existing = sorted(GAZETTEER_DIR.glob("*Gaz_place*_national.txt"))
    if not existing:
        existing = sorted(GAZETTEER_DIR.glob("Gaz_places_national.txt"))
    if existing:
        return existing[0]

    for url in GAZETTEER_URLS:
        zip_name = url.split("/")[-1]
        zip_path = GAZETTEER_DIR / zip_name
        try:
            print(f"  Downloading Gazetteer: {url}")
            urllib.request.urlretrieve(url, zip_path)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(GAZETTEER_DIR)
            extracted = sorted(GAZETTEER_DIR.glob("*Gaz_place*_national.txt"))
            if not extracted:
                extracted = sorted(GAZETTEER_DIR.glob("Gaz_places_national.txt"))
            if extracted:
                print(f"  ✓ Gazetteer cached at: {extracted[0]}")
                return extracted[0]
        except Exception:
            continue

    raise RuntimeError(
        f"Could not download Census Gazetteer place file. Please place it under {GAZETTEER_DIR}"
    )


def load_gazetteer_places():
    """Load Gazetteer places with state+city normalization and land area in sq mi."""
    gaz_file = ensure_gazetteer_places_file()
    gaz = pd.read_csv(gaz_file, sep="\t", dtype=str, encoding="latin-1")
    gaz.columns = [str(c).strip().upper() for c in gaz.columns]

    if "USPS" not in gaz.columns or "NAME" not in gaz.columns:
        raise RuntimeError(f"Unexpected Gazetteer schema in {gaz_file}")

    # Prefer ALAND_SQMI when present; fallback to ALAND meters^2.
    if "ALAND_SQMI" in gaz.columns:
        land = pd.to_numeric(gaz["ALAND_SQMI"], errors="coerce")
    elif "ALAND" in gaz.columns:
        land = pd.to_numeric(gaz["ALAND"], errors="coerce") / 2589988.110336
    else:
        land = pd.Series(np.nan, index=gaz.index)

    places = pd.DataFrame({
        "state": gaz["USPS"].astype(str).str.strip().str.upper(),
        "city": gaz["NAME"].astype(str).str.strip(),
        "Land Area Sq Mi": land.round(4),
    })

    places = places[(places["state"] != "") & (places["city"] != "") & (places["Land Area Sq Mi"] > 0)]
    places["city_normalized"] = places.apply(
        lambda r: canonical_city_for_match(r["city"], r["state"]),
        axis=1,
    )
    # Deduplicate by exact key; keep first observed row.
    places = places.drop_duplicates(subset=["state", "city_normalized"], keep="first")
    return places[["state", "city_normalized", "Land Area Sq Mi"]]


def load_clean_cities(sample=None):
    """Load Clean Cities 100k+ from input workbook."""
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Input workbook not found: {INPUT_FILE}")
    
    df = pd.read_excel(INPUT_FILE, sheet_name="Clean Cities 100k+", dtype=str, engine="openpyxl")
    
    city_col = next((c for c in CITY_COL_CANDIDATES if c in df.columns), None)
    state_col = next((c for c in STATE_COL_CANDIDATES if c in df.columns), None)
    pop_col = next((c for c in POP_COL_CANDIDATES if c in df.columns), None)
    
    if not all([city_col, state_col, pop_col]):
        raise ValueError(f"Missing required columns. Found: {df.columns.tolist()}")
    
    df = df.copy().reset_index(drop=True)
    
    # Sample: 1 city per state (if requested) - do this BEFORE renaming
    if sample and sample > 0:
        cities_per_state = df.groupby(state_col).first().reset_index()
        df = cities_per_state.head(sample)
    
    # Now rename columns to standardized names
    df = df.rename(columns={
        city_col: "city",
        state_col: "state",
        pop_col: "Census City Population"
    })
    
    # Clean up data
    df["city"] = df["city"].astype(str).fillna("")
    df["state"] = df["state"].astype(str).fillna("")
    df["Census City Population"] = pd.to_numeric(df["Census City Population"], errors="coerce")
    
    # Return only the columns we need
    return df[["city", "state", "Census City Population"]]


def load_fbi_table_8():
    """Load FBI Table 8 city-level crime data.
    
    Table 8: Offenses Known to Law Enforcement by State, by City
    
    Expected columns:
    - State
    - City
    - Population
    - Violent crime
    - Murder and nonnegligent manslaughter
    - Rape
    - Robbery
    - Aggravated assault
    - Property crime
    - Burglary
    - Larceny-theft
    - Motor vehicle theft
    """
    if not FBI_TABLE_8_FILE.exists():
        print(f"\n❌ FBI Table 8 file not found:")
        print(f"   {FBI_TABLE_8_FILE}")
        print(f"\n📥 Download from:")
        print(f"   https://cde.ucr.cjis.gov/LATEST/webapp/#/pages/downloads")
        print(f"\n📋 Look for: 'Crime in the United States Annual Reports'")
        print(f"   2024 Download → Extract → CIUS_Table_8_Offenses_Known_to_Law_Enforcement_by_State_by_City_2024.xlsx")
        raise RuntimeError(f"FBI Table 8 file required at: {FBI_TABLE_8_FILE}")
    
    # Read Excel with header at row 3 (0-indexed)
    df = pd.read_excel(FBI_TABLE_8_FILE, sheet_name=0, header=3, engine="openpyxl")
    
    print(f"✓ Loaded FBI Table 8 data: {len(df)} records")
    print(f"  Columns: {', '.join(str(c) for c in df.columns.tolist()[:5])}...")
    
    # Clean up column names: convert to string, remove newlines, lowercase, strip
    df.columns = [str(c).replace('\n', ' ').strip().lower() for c in df.columns]
    
    # Normalize State and City
    df = df.copy()
    df['state'] = df.get('state', '').map(normalize_state_to_abbr)
    df['city'] = df.get('city', '').astype(str).str.strip()
    
    # Convert crime columns to numeric
    df['population'] = pd.to_numeric(df.get('population', 0), errors='coerce').fillna(0)
    df['violent crime'] = pd.to_numeric(df.get('violent crime', 0), errors='coerce').fillna(0)
    df['property crime'] = pd.to_numeric(df.get('property crime', 0), errors='coerce').fillna(0)
    
    # Remove rows with no data
    df = df[(df['state'] != '') & (df['city'] != '') & (df['population'] > 0)]
    
    return df[['state', 'city', 'population', 'violent crime', 'property crime']]


def match_cities_to_fbi_data(clean_cities_df, fbi_df):
    """Match Clean Cities to FBI Table 8 data by exact State + City match.
    
    Args:
        clean_cities_df: DataFrame with Clean Cities 100k+ data
        fbi_df: DataFrame with FBI Table 8 city-level crime data
        
    Returns:
        DataFrame with matched crime data
    """
    result = []
    fbi_work = fbi_df.copy()
    fbi_work["city_normalized"] = fbi_work.apply(
        lambda r: canonical_city_for_match(r["city"], r["state"]),
        axis=1,
    )
    
    for idx, row in clean_cities_df.iterrows():
        # Normalize the city name by removing suffixes
        clean_state = normalize_state_to_abbr(row['state'])
        clean_city = canonical_city_for_match(row['city'], clean_state)
        
        # Try exact match (State + normalized City)
        match = fbi_work[
            (fbi_work['state'] == clean_state)
            & (fbi_work['city_normalized'] == clean_city)
        ]
        
        if not match.empty:
            fbi_row = match.iloc[0]
            result.append({
                'city': row['city'],  # Keep original name
                'state': clean_state,
                'census_population': row.get('Census City Population', pd.NA),
                'fbi_population': int(fbi_row['population']) if fbi_row['population'] > 0 else pd.NA,
                'violent_crime': int(fbi_row['violent crime']) if fbi_row['violent crime'] > 0 else pd.NA,
                'property_crime': int(fbi_row['property crime']) if fbi_row['property crime'] > 0 else pd.NA,
                'match_status': 'Exact Match',
                'fbi_data_available': True,
            })
        else:
            # No match found
            result.append({
                'city': row['city'],  # Keep original name
                'state': clean_state,
                'census_population': row.get('Census City Population', pd.NA),
                'fbi_population': pd.NA,
                'violent_crime': pd.NA,
                'property_crime': pd.NA,
                'match_status': 'No Match',
                'fbi_data_available': False,
            })
    
    return pd.DataFrame(result)


def calculate_crime_rates(matched_df):
    """Calculate crime rates per 100k and Crime Index Score."""
    result = matched_df.copy()
    
    # Convert to numeric types for calculation
    result['fbi_population'] = pd.to_numeric(result['fbi_population'], errors='coerce')
    result['violent_crime'] = pd.to_numeric(result['violent_crime'], errors='coerce')
    result['property_crime'] = pd.to_numeric(result['property_crime'], errors='coerce')
    
    # Initialize percentile columns
    result['violent_crime_percentile'] = np.nan
    result['property_crime_percentile'] = np.nan
    
    # Calculate rates per 100k
    result['Violent Crime Rate per 100k'] = (
        (result['violent_crime'] / result['fbi_population'] * 100000)
        .round(2)
    )
    result['Property Crime Rate per 100k'] = (
        (result['property_crime'] / result['fbi_population'] * 100000)
        .round(2)
    )
    
    # Calculate percentiles (only for rows with data)
    with_data = result[result['fbi_data_available']].copy()
    
    if not with_data.empty:
        violent_percentiles = with_data['Violent Crime Rate per 100k'].rank(pct=True) * 100
        property_percentiles = with_data['Property Crime Rate per 100k'].rank(pct=True) * 100
        
        result.loc[result['fbi_data_available'], 'violent_crime_percentile'] = violent_percentiles.values
        result.loc[result['fbi_data_available'], 'property_crime_percentile'] = property_percentiles.values
    
    # Crime Index Score: 75% violent percentile + 25% property percentile
    result['Crime Index Score'] = (
        0.75 * result['violent_crime_percentile'] + 
        0.25 * result['property_crime_percentile']
    ).round(2)
    
    result['Crime Rating'] = result['Crime Index Score'].map(get_rating)
    result['Last Updated'] = datetime.date.today().isoformat()
    
    return result


def apply_density_adjustment(crime_df):
    """Add Gazetteer-based density fields and adjusted crime score."""
    result = crime_df.copy()
    gaz = load_gazetteer_places()

    work = result.copy()
    work["city_normalized"] = work.apply(
        lambda r: canonical_city_for_match(r["city"], r["state"]),
        axis=1,
    )

    work = work.merge(
        gaz,
        on=["state", "city_normalized"],
        how="left",
    )

    work["Population Density"] = (
        pd.to_numeric(work["census_population"], errors="coerce")
        / pd.to_numeric(work["Land Area Sq Mi"], errors="coerce")
    ).replace([np.inf, -np.inf], np.nan).round(2)

    work["Density Percentile"] = np.nan
    valid_density = work[work["Population Density"].notna()].copy()
    if not valid_density.empty:
        density_pct = valid_density["Population Density"].rank(pct=True) * 100
        work.loc[valid_density.index, "Density Percentile"] = density_pct.round(2)

    # Density Adjustment = -5 * ((Density Percentile - 50) / 50), clamped [-5, +5]
    work["Density Adjustment"] = (
        -5.0 * ((work["Density Percentile"] - 50.0) / 50.0)
    ).clip(lower=-5, upper=5).round(2)

    work["Density-Adjusted Crime Score"] = (
        pd.to_numeric(work["Crime Index Score"], errors="coerce")
        + pd.to_numeric(work["Density Adjustment"], errors="coerce")
    ).clip(lower=0, upper=100).round(2)

    # Keep adjusted score blank if no base score or no density match.
    work.loc[work["Crime Index Score"].isna(), "Density-Adjusted Crime Score"] = np.nan
    work.loc[work["Land Area Sq Mi"].isna(), "Density-Adjusted Crime Score"] = np.nan

    work["Density-Adjusted Crime Rating"] = work["Density-Adjusted Crime Score"].map(get_rating)
    return work.drop(columns=["city_normalized"])


def build_output_sheet(crime_data_df):
    """Build Crime Index sheet with all Clean Cities rows (matched and unmatched)."""
    output = crime_data_df.copy()

    output['Coverage Rate'] = (
        pd.to_numeric(output['fbi_population'], errors='coerce')
        / pd.to_numeric(output['census_population'], errors='coerce')
    ).round(4)

    # Mark manual review when no FBI match or no land-area match.
    output['Manual Review'] = output.apply(
        lambda r: 'Yes' if (r.get('match_status') != 'Exact Match' or pd.isna(r.get('Land Area Sq Mi'))) else '',
        axis=1,
    )
    output.loc[output['Manual Review'] == 'Yes', 'Crime Index Score'] = output['Crime Index Score']
    output.loc[output['Crime Index Score'].isna(), 'Density-Adjusted Crime Score'] = np.nan
    output.loc[output['Crime Index Score'].isna(), 'Density-Adjusted Crime Rating'] = ''

    output = output.rename(columns={
        'city': 'City',
        'state': 'State',
        'census_population': 'Census City Population',
        'fbi_population': 'FBI Population',
    })

    output_cols = [
        'City', 'State', 'Census City Population', 'FBI Population',
        'Coverage Rate',
        'Violent Crime Rate per 100k', 'Property Crime Rate per 100k',
        'Crime Index Score', 'Crime Rating',
        'Land Area Sq Mi', 'Population Density', 'Density Percentile',
        'Density Adjustment', 'Density-Adjusted Crime Score', 'Density-Adjusted Crime Rating',
        'Last Updated', 'Manual Review'
    ]

    return output[output_cols]


def main(sample=None):
    """Main workflow."""
    print("\n" + "="*50)
    print("   Crime Index Builder")
    print("   FBI Table 8 City-Level Data")
    print("="*50 + "\n")
    
    # Step 1: Load Clean Cities
    print("1. Loading Clean Cities 100k+...")
    try:
        clean_cities = load_clean_cities(sample=sample)
        print(f"   ✓ Loaded {len(clean_cities)} cities")
    except Exception as e:
        print(f"   ✗ Error loading Clean Cities: {e}")
        return
    
    # Step 2: Load FBI Table 8
    print("\n2. Loading FBI Table 8 city-level crime data...")
    try:
        fbi_data = load_fbi_table_8()
        print(f"   ✓ Loaded {len(fbi_data)} records from FBI data")
    except Exception as e:
        print(f"   ✗ Error loading FBI data: {e}")
        return
    
    # Step 3: Match cities
    print("\n3. Matching cities to FBI data...")
    matched = match_cities_to_fbi_data(clean_cities, fbi_data)
    matched_count = len(matched[matched['fbi_data_available']])
    print(f"   ✓ Matched {matched_count}/{len(matched)} cities")
    
    # Step 4: Calculate crime rates and index
    print("\n4. Calculating crime rates and index...")
    crime_index = calculate_crime_rates(matched)
    print(f"   ✓ Calculated rates for {matched_count} cities")

    # Step 5: Apply density adjustment
    print("\n5. Applying Census Gazetteer density adjustment...")
    try:
        crime_index = apply_density_adjustment(crime_index)
        gaz_matches = int(crime_index['Land Area Sq Mi'].notna().sum())
        print(f"   ✓ Land area matched for {gaz_matches}/{len(crime_index)} cities")
    except Exception as e:
        print(f"   ✗ Error applying density adjustment: {e}")
        return
    
    # Step 6: Build output sheet
    print("\n6. Building output workbook...")
    output_sheet = build_output_sheet(crime_index)
    
    # Step 7: Write output
    print("\n7. Writing output...")
    # Read all sheets from input workbook
    wb_dict = pd.read_excel(INPUT_FILE, sheet_name=None, dtype=str, engine="openpyxl")
    
    # Add Crime Index sheet
    wb_dict["Crime Index"] = output_sheet

    # Update README methodology with density adjustment notes.
    readme_marker = "Crime methodology update: density-adjusted scoring"
    readme_lines = [
        readme_marker,
        "Base Crime Score remains as Crime Index Score.",
        "Population Density = Census City Population / Land Area Sq Mi (Census Gazetteer places).",
        "Density Percentile is rank percentile across included cities with valid density.",
        "Density Adjustment = -5 * ((Density Percentile - 50) / 50), clamped to [-5, +5].",
        "Density-Adjusted Crime Score = clamp(Crime Index Score + Density Adjustment, 0, 100).",
        "Rating bands (both scores): 0-20 Very Low, 21-40 Low, 41-60 Moderate, 61-75 Elevated, 76-90 High, 91-100 Very High.",
    ]
    if "README" in wb_dict:
        readme_df = wb_dict["README"].copy()
        first_col = readme_df.columns[0]
        existing_text = "\n".join(readme_df[first_col].fillna("").astype(str).tolist())
        if readme_marker not in existing_text:
            extra = pd.DataFrame({first_col: [""] + readme_lines})
            wb_dict["README"] = pd.concat([readme_df, extra], ignore_index=True)
    
    # Write to output
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        for sheet_name, df in wb_dict.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)
    
    print(f"   ✓ Saved: {OUTPUT_FILE}")
    
    # Summary
    with_scores = len(crime_index[crime_index['Crime Index Score'].notna()])
    needs_review = len(output_sheet[output_sheet['Manual Review'].astype(bool)])
    
    print(f"\n   Summary:")
    print(f"   - Total cities: {len(output_sheet)}")
    print(f"   - With crime data: {with_scores}")
    print(f"   - Needs manual review: {needs_review}")
    print(f"\n✓ Complete!\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Build Crime Index from FBI Table 8 city-level crime data"
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=0,
        help="Run on sample of N cities (1 per state, up to N)"
    )
    args = parser.parse_args()
    
    main(sample=args.sample if args.sample > 0 else None)
