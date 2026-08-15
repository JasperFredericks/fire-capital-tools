# FIRE Capital Tools

Internal tooling for FIRE Capital real estate operations.

## Progressive Web App (PWA)

Fire Capital Tools supports PWA installation. Users on Chrome (desktop/Android) and Safari (iOS 16.4+) can install the app to their home screen or app launcher and open it in a standalone window.

### Files involved

| File | Purpose |
|---|---|
| `static/manifest.json` | Web App Manifest — name, icons, colors, display mode |
| `static/service-worker.js` | Service worker — caches static assets, serves offline fallback |
| `static/offline.html` | Shown when the user is offline and navigates to an uncached page |
| `static/img/icon-192.png` | PWA icon 192×192 (generated from `logo-mark.png`) |
| `static/img/icon-512.png` | PWA icon 512×512 (copy of `logo-mark.png`) |

Routes `/manifest.json` and `/service-worker.js` are served at root level by Flask so the service worker scope covers the entire application (`/`).

### How it works

**Manifest:** Linked from `<head>` in `base.html`. Tells the browser the app name, icons, theme color, and that it should open in `standalone` mode (no browser chrome).

**Service worker:** Registered globally via a `<script>` at the bottom of `base.html`. On install it precaches core static assets (CSS, icons). On fetch it applies a cache-first strategy for `/static/` assets. All authenticated routes, tool routes, API calls, and POST requests bypass the cache entirely and always go to the network.

### V1 offline behavior

**What works offline:** The app shell (CSS, icons) is cached. An offline fallback page is shown if the user has no connection and tries to navigate somewhere uncached.

**What does NOT work offline:** Everything dynamic — login, dashboard, tools (FIRE Metric, Deal Analyzer, Scorecard Pro, etc.), database queries, API calls, Google Maps. This is intentional. Fire Capital Tools handles financial/investment data where stale cached results could cause incorrect decisions.

### Testing installation locally

1. Start the app: `flask run` (or `python app.py`)
2. Open Chrome and navigate to `http://localhost:5000`
3. Open DevTools → **Application** tab → **Manifest** — verify name, icons, and display mode load correctly
4. **Service Workers** panel — verify the worker registered with scope `/`
5. To test the install prompt on desktop Chrome: look for the install icon (⊕) in the address bar

### Testing installation in production (Railway)

The production app is already on HTTPS (required for PWA). In Chrome on mobile or desktop:
1. Navigate to the production URL
2. Chrome will show an "Install" banner or address-bar prompt after a short visit
3. On iOS Safari: tap the Share button → **Add to Home Screen**

### Verifying manifest and service worker

```
# Manifest
curl https://<your-domain>/manifest.json

# Service worker
curl https://<your-domain>/service-worker.js
```

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
	- `SCORECARD_PRO_DB_PATH` (optional; defaults to `scorecard_pro_history.db`)
	- `DEAL_DIVE_DB_PATH` (optional; defaults to `deal_dive.db`)
	- `RENTCAST_API_KEY`
	- `GOOGLE_PLACES_API_KEY`
	- `GOOGLE_MAPS_API_KEY` (required for FIRE Metric map rendering)
	- `GOOGLE_MAPS_MAP_ID` (required for FIRE Metric map styling)
	- `MARKET_DATA_DB_PATH` (optional; defaults to `market_data_cache.db`)
	- `UPLOAD_FOLDER_PATH` (optional; defaults to `uploads/` at the repo root)
	- `FEEDBACK_DB_PATH` (optional; defaults to `feedback.db`)
	- `SITE_DD_DB_PATH` (optional; defaults to `site_dd.db`)
	- `UNDERWRITING_DB_PATH` (optional; defaults to `underwriting.db`)
	- `INVESTOR_REPORT_DB_PATH` (optional; defaults to `investor_report.db`)
	- `OPENAI_USAGE_DB_PATH` (optional; defaults to `openai_usage.db`)
	- `INVESTOR_NOTES_DB_PATH` (optional; defaults to `investor_notes.db`)
	- `APP_SETTINGS_DB_PATH` (optional; defaults to `app_settings.db`)
	- `INVESTOR_NOTES_MODEL` (optional; falls back to `FIRE_METRICS_SUMMARY_MODEL`)
	- `CENSUS_API_KEY`
- Required for the FIRE Metric updater to pull ACS/Census data: `CENSUS_API_KEY`
- Required for Scorecard Pro's upload history/trend to persist across Railway deploys: `SCORECARD_PRO_DB_PATH`
- Required for Deal Dive's deals/comps/condition data to persist across Railway deploys: `DEAL_DIVE_DB_PATH`
- Required for Deal Dive's "Auto-Pull Market Data" (RentCast rent estimates/comps + Google Places ratings): `RENTCAST_API_KEY`, `GOOGLE_PLACES_API_KEY`
- Required for the RentCast/Google Places lookup cache to persist across Railway deploys: `MARKET_DATA_DB_PATH`
- Required for Deal Dive's uploaded supporting documents to persist across Railway deploys: `UPLOAD_FOLDER_PATH`
- Required for beta feedback notes to persist across Railway deploys: `FEEDBACK_DB_PATH`
- Required for Site DD assessments to persist across Railway deploys: `SITE_DD_DB_PATH`
- Required for Underwriting scenarios to persist across Railway deploys: `UNDERWRITING_DB_PATH`
- Required for Investor Report waterfalls and capital records to persist across Railway deploys: `INVESTOR_REPORT_DB_PATH`
- Required for user-configured settings (Quick Deal Analyzer grading bands) to persist across Railway deploys: `APP_SETTINGS_DB_PATH`. Without it the tool still works and falls back to its disclosed placeholder bands.
- Required for uploaded meeting transcripts and generated investor updates to persist across Railway deploys: `INVESTOR_NOTES_DB_PATH`. The Meeting Notes page warns when it is unset.
- Required for the OpenAI per-feature usage counter to persist across Railway deploys: `OPENAI_USAGE_DB_PATH`. Without it the counter still works but resets on every deploy, silently under-reporting the month; the Admin → Service Costs page shows a warning when it is unset.
- Required for FIRE Metric Google Maps display: `GOOGLE_MAPS_API_KEY`, `GOOGLE_MAPS_MAP_ID`

### Google Maps API key restrictions

- The browser key must be restricted to the Google Maps JavaScript API.
- Restrict key usage by HTTP referrer to the production Railway domain.
- Optionally allow approved local origins (for example, localhost ports used in development).
- Never commit real API keys to Git; provide values through environment variables only.

### Local development

- Local runs can use `.env` or `fire_metrics/data/cache/census_api_key.txt`
- These files are ignored by Git and must never be committed with real credentials

### Git safety

- Generated workbooks and cache payloads are ignored by Git
- `fire_metrics/output/*` workbooks, `fire_metrics/data/cache/*` runtime cache, and related generated artifacts are excluded
