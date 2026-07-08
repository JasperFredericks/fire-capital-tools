#!/usr/bin/env python3
"""
Quick verification script to check Crime Index setup status.
Run this to confirm everything is ready.
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
checks = []

def check(condition, description):
    status = "✓" if condition else "✗"
    print(f"  {status} {description}")
    checks.append((condition, description))

print("\n" + "="*50)
print("  Crime Index Setup Verification")
print("="*50 + "\n")

# Check input workbook
check(
    (BASE_DIR / "output" / "us_cities_100k_population_ranked_WITH_LANDLORD_AND_POP_CHANGE.xlsx").exists(),
    "Input workbook found"
)

# Check script
check(
    (BASE_DIR / "add_crime_index.py").exists(),
    "Crime index script found"
)

# Check CDE data
cde_file = BASE_DIR / "data" / "cache" / "crime" / "cde_agency_data.csv"
check(
    cde_file.exists(),
    f"CDE data file found: {cde_file.name}"
)

if cde_file.exists():
    import pandas as pd
    try:
        df = pd.read_csv(cde_file)
        check(len(df) > 0, f"  CDE data has {len(df)} records")
        required_cols = ['agency_name', 'state_abbr', 'violent_crime', 'property_crime']
        missing = [c for c in required_cols if c not in df.columns]
        if not missing:
            check(True, f"  All required columns present")
        else:
            check(False, f"  Missing columns: {', '.join(missing)}")
    except Exception as e:
        check(False, f"  Error reading CDE data: {e}")

# Check documentation
check(
    (BASE_DIR / "CRIME_INDEX_SETUP.md").exists(),
    "Setup documentation found"
)

check(
    (BASE_DIR / "CRIME_INDEX_COMPLETE.md").exists(),
    "Implementation summary found"
)

# Summary
passed = sum(1 for c, _ in checks if c)
total = len(checks)

print(f"\n  Results: {passed}/{total} checks passed\n")

if passed == total:
    print("  ✓ Setup complete! Next steps:\n")
    print("    1. VERIFY real CDE data:")
    print("       - Download from: https://cde.ucr.cjis.gov/LATEST/webapp/#/pages/downloads")
    print("       - Save to: data/cache/crime/cde_agency_data.csv\n")
    print("    2. TEST sample (5 cities):")
    print("       python3 add_crime_index.py --sample 5\n")
    print("    3. RUN full dataset (all 343 cities):")
    print("       python3 add_crime_index.py\n")
    sys.exit(0)
else:
    print("  ✗ Setup incomplete. See issues above.\n")
    sys.exit(1)
