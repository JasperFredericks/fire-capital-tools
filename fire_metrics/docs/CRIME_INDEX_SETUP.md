# Crime Index Builder - Setup Guide

## Overview

The `add_crime_index_v2.py` script enriches your Clean Cities 100k+ workbook with FBI Crime Data Explorer (CDE) agency-level crime statistics, creating a **Crime Index Score** for each city.

## Prerequisites

1. **Python 3.9+** with pandas, openpyxl
2. **CDE Crime Data CSV** (see step 2 below)
3. **Input Workbook**: `output/us_cities_100k_population_ranked_WITH_LANDLORD_AND_POP_CHANGE.xlsx`

## Step 1: Obtain CDE Agency-Level Crime Data

The script requires official FBI Crime Data Explorer (CDE) data in CSV format.

### Option A: Download Official CDE Data (Recommended)

1. Visit: https://cde.ucr.cjis.gov/LATEST/webapp/#/pages/downloads
2. Look for **Agency-level annual summary** data (includes violent crime, property crime counts)
3. Download as CSV
4. Save to: `data/cache/crime/cde_agency_data.csv`

### Option B: Expected CSV Format

If obtaining the official CDE file, you can use any CSV with these columns:

```
agency_name,state_abbr,year,violent_crime,property_crime,population,ori
Police Department,CA,2023,450,3200,250000,CA001
Sheriff's Office,CA,2023,280,1900,180000,CA002
San Francisco Police Department,CA,2023,380,2100,200000,CA003
...
```

**Required columns:**
- `agency_name` - Law enforcement agency name
- `state_abbr` - State code (CA, NY, TX, etc.)
- `violent_crime` - Count of violent crimes
- `property_crime` - Count of property crimes
- `population` - Population served by agency

**Optional columns:**
- `year` - Data year
- `ori` - ORI code

## Step 2: Run Script

### Test Run (5 Sample Cities)

```bash
python3 add_crime_index_v2.py --sample 5
```

This runs on 1 city per state (up to 5 states) to verify configuration.

**Output:**
- `output/us_cities_100k_population_ranked_WITH_CRIME_INDEX.xlsx` - Workbook with Crime Index sheet
- `data/cache/crime/crime_agency_mapping.csv` - City-to-agency match mapping

### Full Run (All 343 Cities)

```bash
python3 add_crime_index_v2.py
```

## Step 3: Review Output

### Crime Index Sheet

Contains these columns:
- **City, State** - City location
- **Census City Population** - 2025 estimate from source data
- **FBI Agency Name** - Matched law enforcement agency
- **Agency Population** - Population served by agency
- **Violent Crime Rate per 100k** - Calculated rate
- **Property Crime Rate per 100k** - Calculated rate
- **Crime Index Score** - 0-100 composite score (75% violent, 25% property)
- **Crime Rating** - Very Low, Low, Moderate, High, Very High
- **Needs Manual Review** - Yes/Maybe if match confidence low

### Crime Mapping Sheet

Shows matching logic:
- **City, State** - City
- **FBI Agency Name** - Matched agency
- **Match Reason** - Why this agency was selected
- **Needs Manual Review** - Manual override needed?

## Troubleshooting

### "CDE data file not found"

The script cannot find CDE data at `data/cache/crime/cde_agency_data.csv`. 

**Solution:** Download and save CDE CSV file to that location (see Step 1).

### Low Match Rates

If many cities fail to match, it may be due to:
- Agency names differ from city names (state police, county sheriffs, etc.)
- Limited CDE data for that state
- City outside major agency jurisdiction

**Solution:** Review `Crime Mapping` sheet and manually update agency names as needed. Rerun script.

### Empty Crime Index Scores

If many rows have no crime scores, the CDE data may have:
- Missing crime columns
- Different column naming

**Solution:** Check CDE CSV column names match expected format (see above).

## Output Files

| File | Purpose |
|------|---------|
| `output/us_cities_100k_population_ranked_WITH_CRIME_INDEX.xlsx` | Final workbook with Crime Index |
| `data/cache/crime/cde_agency_data.csv` | Input CDE data (user-provided) |
| `data/cache/crime/crime_agency_mapping.csv` | City-to-agency mapping details |

## Manual Review & Adjustments

If you want to manually override agency matches:

1. Edit `data/cache/crime/crime_agency_mapping.csv`
2. Update `FBI Agency Name` column with correct agency
3. Set `Needs Manual Review` to empty string
4. Rerun script (it will use your edits)

## Notes

- Crime scores are based on **violent crime count** (75% weight) and **property crime count** (25% weight)
- All original workbook sheets are preserved
- Scores are for comparison only; actual crime rates depend on many factors
- Data year depends on CDE data provided (typically 1-2 years behind current)
