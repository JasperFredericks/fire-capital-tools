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

## FIRE Metric Dashboard

- Location in app: Markets -> FIRE Metric
- Purpose: searchable city-level market metrics dashboard backed by a runtime JSON index built from the latest FIRE Metrics workbook
- Primary workflow: search first, refresh/index only when data is stale or missing
- Upload/download workflow is still available for admins

### Search behavior

- Search entry supports punctuation/case normalization and common aliases:
	- `St Louis`, `Saint Louis`, `St. Louis`
	- `NYC`, `New York City`
	- `LA`
	- `DC`, `Washington DC`
- API endpoint: `GET /tools/fire-metrics/search?q=<query>`
- Response statuses:
	- `found`
	- `suggestions` (ambiguous/fuzzy)
	- `excluded` (below threshold)
	- `not_found`
	- `error`

### Dashboard sections

- Header/status:
	- last refreshed
	- source updated timestamp
	- data status (`current`, `stale`, `missing`)
- Search bar with suggestions and user-friendly messages
- City metric cards for:
	- population
	- income
	- home value
	- employment
	- climate risk
	- crime
	- density-adjusted crime
	- landlord friendliness
- Methodology notes accordion
- Admin tools:
	- Check for Updates
	- Refresh All Data
	- Format Only
	- Dry Run
	- Rebuild Search Index
	- Download Latest Workbook

### Auto-update and refresh model

- Search reads from cached runtime JSON index files and does not trigger expensive refreshes.
- Freshness is tracked via metadata and stale-hour thresholds.
- Manual refresh/index actions are available in the FIRE Metric admin panel.
- CLI support is available for scheduled jobs:

```bash
python3 -m fire_metrics.update_fire_metrics --refresh-all --rebuild-index
```

- Railway note: true automatic scheduled refreshes require a Railway scheduled job/cron (or manual admin refresh from the dashboard).

### Runtime variables (Railway)

- Configure in Railway: Service -> Variables
- Full variable list:
	- `SECRET_KEY`
	- `FLASK_DEBUG`
	- `ADMIN_USERNAME`
	- `ADMIN_PASSWORD_HASH`
	- `CENSUS_API_KEY`
- Required for the FIRE Metric updater to pull ACS/Census data: `CENSUS_API_KEY`
- Optional:
	- `FIRE_METRICS_DATA_DIR` (defaults to `instance/fire_metrics/`)

### Local development

- Local runs can use `.env` or `fire_metrics/data/cache/census_api_key.txt`
- These files are ignored by Git and must never be committed with real credentials
- Search can still run without `CENSUS_API_KEY` if a previous runtime index exists
- Refresh/update actions fail cleanly with a user message when `CENSUS_API_KEY` is missing

### Git safety

- Generated workbooks and cache payloads are ignored by Git
- `fire_metrics/output/*` workbooks, `fire_metrics/data/cache/*` runtime cache, and related generated artifacts are excluded
- Runtime dashboard artifacts are ignored:
	- `instance/fire_metrics/`
	- `fire_metrics_runtime/`
	- generated JSON indexes and metadata
