"""FIRE Metrics — module-level constants (verbatim, except REPO_ROOT depth)."""

from __future__ import annotations

from pathlib import Path


# NOTE: this module lives one directory deeper than the original
# tools/fire_metrics.py, so this uses three .parent hops instead of two
# to resolve to the exact same repo root (verified byte-identical).
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# A refresh in the "running" state for longer than this is treated as
# crashed/stuck rather than genuinely in progress, so one dead subprocess
# (e.g. killed by an OOM or a Railway restart) can't permanently block
# every future refresh. Generous relative to the real chain (climate risk
# alone is documented elsewhere in this file as "several minutes" on a
# cold cache) but still bounded.
REFRESH_STALE_AFTER_SECONDS = 60 * 60

TOP_CITY_METRICS: dict[str, dict[str, str]] = {
    "crime_index_score": {
        "column": "crime_index_score",
        "direction": "asc",
        "label": "Lowest Crime",
    },
    "density_adjusted_crime_score": {
        "column": "density_adjusted_crime_score",
        "direction": "asc",
        "label": "Lowest Density-Adjusted Crime",
    },
    "employment_growth_recent": {
        "column": "employment_growth_recent",
        "direction": "desc",
        "label": "Highest Job Growth",
    },
    "population_growth_recent": {
        "column": "population_growth_recent",
        "direction": "desc",
        "label": "Highest Population Growth",
    },
    "median_income_growth_recent": {
        "column": "median_income_growth_recent",
        "direction": "desc",
        "label": "Highest Income Growth",
    },
    "median_home_value_growth_recent": {
        "column": "median_home_value_growth_recent",
        "direction": "desc",
        "label": "Highest Home-Value Growth",
    },
    "climate_risk_score": {
        "column": "climate_risk_score",
        "direction": "asc",
        "label": "Lowest Climate Risk",
    },
    "fire_score": {
        "column": "",
        "direction": "desc",
        "label": "Highest FIRE Score",
        "computed": "fire_score",
    },
}


_SCRIPTS_DIR = REPO_ROOT / "fire_metrics" / "scripts"

# The real FBI Table 8 workbook is a few MB; this is generous headroom
# while still catching an obviously-wrong file quickly with a clear
# message. Flask's own global MAX_CONTENT_LENGTH (20 MB, see config.py) is
# a hard backstop above this for the whole app, independent of this check.
MAX_CRIME_WORKBOOK_BYTES = 10 * 1024 * 1024

# Matches add_crime_index.load_fbi_table_8's header=3 (0-indexed) -- the
# workbook has a few title rows before the real header.
CRIME_WORKBOOK_HEADER_ROW = 4
CRIME_WORKBOOK_REQUIRED_COLUMNS = {"state", "city", "population", "violent crime", "property crime"}
