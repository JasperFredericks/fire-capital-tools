# FIRE Capital Tools

Internal tooling for FIRE Capital real estate operations.

## mmr-summary

Automatically generates a formatted **Summary** tab in any Resman MMR (Monthly Management Report) Excel file, replacing a broken VBA approach.

### What it does

Reads an MMR `.xlsx` file and writes a clean `Summary` sheet containing:

| Section | Source Tab |
|---|---|
| Header (property name, date range, printed date) | Box Score |
| Occupancy (%, occupied, vacant, preleases, on-notice) | Box Score + Available Units |
| Leasing Activity (applied, approved, signed) | Box Score |
| Delinquency (grand total $) | Delinquency |
| Rental Income to Date (total revenue, avg rent/unit) | Rent Roll |
| Ready Units — Vacant & Pre-Leased | Available Units |
| Projected Occupancy (next 20 weeks) | Box Score |
| Expiring Leases by Month (next 10 months) | Expiring Leases |
| Top 2 Prospect Sources | Prospect Source Summary |
| Open Work Orders + issue-type counts | Work Order Summary |

### Setup (one-time)

```
pip install openpyxl pandas
```

### Usage

Place your MMR file in the `mmr-summary` folder, then:

**Windows:**
```
run_summary ERA_MMR_-_06_15_26.xlsx
```

**Mac / Linux:**
```
./run_summary.sh ERA_MMR_-_06_15_26.xlsx
```

Or run directly:
```
python generate_summary.py "ERA_MMR_-_06_15_26.xlsx"
```

The script modifies the file **in place** — it adds or replaces the `Summary` sheet.

### Supported Properties

Tested against:
- Eagle Rock Apartments (ERA) — 92 units
- The Canyon Apartments — 91 units
- Oxford Pointe Apartments (OXPT) — 152 units
