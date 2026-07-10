# U.S. Cities 100k+ Population Ranking

This project downloads the Census Bureau Vintage 2025 estimate file for incorporated places and creates a clean Excel workbook with every U.S. city/place with a July 1, 2025 population estimate of 100,000 or more.

## Files

- `main.py`: Main Python script to download the Census source file, clean the data, and export the Excel workbook.
- `requirements.txt`: Python package requirements.
- `data/raw/`: Raw downloaded Census source file.
- `data/processed/`: Reserved for processed intermediate data.
- `output/`: Generated Excel workbook.

## Output

The script writes:

- `output/us_cities_100k_population_ranked.xlsx`

Workbook sheets:

- `README`: Source explanation and notes.
- `Raw Data`: The downloaded Census data in its imported form.
- `Clean Cities 100k+`: Filtered and formatted city/place population list.

## Usage

Install packages:

```bash
python3 -m pip install -r requirements.txt
```

Run the script:

```bash
python3 main.py
```

## Secrets and Railway Deployment

- Do not commit `.env` files, API key files, cache files, or generated outputs.
- Set production secrets in Railway under Service -> Variables.
- Required Railway variable:
	- `CENSUS_API_KEY`
	- `FBI_CRIME_WORKBOOK_PATH=/data/crime/CIUS_Table_8_Offenses_Known_to_Law_Enforcement_by_State_by_City_2024.xlsx`
- Flask app variables:
	- `FLASK_SECRET_KEY`
	- `ADMIN_USERNAME`
	- `ADMIN_PASSWORD_HASH`
- Local development can use `.env` or `data/cache/census_api_key.txt`, and both are ignored by Git.
- Locally, if `FBI_CRIME_WORKBOOK_PATH` is not set, the FBI crime workbook defaults to `data/cache/crime/CIUS_Table_8_Offenses_Known_to_Law_Enforcement_by_State_by_City_2024.xlsx`.
