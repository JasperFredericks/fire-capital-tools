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

## FIRE Metric Tool (Phase 2 Flask Integration)

- Location in app: Markets -> FIRE Metric
- Purpose: refresh market indicators workbook through the standalone FIRE Metrics updater workspace
- Input: `.xlsx` workbook upload
- Output: updated `.xlsx` workbook download

### Runtime variables (Railway)

- Configure in Railway: Service -> Variables
- Full variable list:
	- `SECRET_KEY`
	- `FLASK_DEBUG`
	- `ADMIN_USERNAME`
	- `ADMIN_PASSWORD_HASH`
	- `CENSUS_API_KEY`
- Required for the FIRE Metric updater to pull ACS/Census data: `CENSUS_API_KEY`

### Local development

- Local runs can use `.env` or `fire_metrics/data/cache/census_api_key.txt`
- These files are ignored by Git and must never be committed with real credentials

### Git safety

- Generated workbooks and cache payloads are ignored by Git
- `fire_metrics/output/*` workbooks, `fire_metrics/data/cache/*` runtime cache, and related generated artifacts are excluded
