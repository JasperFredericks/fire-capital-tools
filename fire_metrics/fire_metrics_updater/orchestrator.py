"""Real "Refresh All Data" orchestration.

Replaces update_fire_metrics.py's run_update(), which only validated the
uploaded workbook and copied it -- it never actually called any of the real
data-refresh scripts. This module calls the actual, working pipeline
scripts from fire_metrics/scripts/ (fixed in JasperTest Priorities 1-4) in
the correct order, then ingests each one's output into SQLite (db.py) with
its own metric-family timestamp.

Crime stays manual/periodic (see crime_pipeline.py's own docstring -- the
FBI API is dead, there is no live source): this orchestrator only refreshes
crime from the FBI Table 8 workbook uploaded through Admin Data Tools and
resolved by FBI_CRIME_WORKBOOK_PATH. If that file isn't present, crime is
skipped (not an error) and its timestamp is left untouched, exactly like
the rest of the "only stamp what actually refreshed" rule below.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from . import db as db_module
from . import index_builder

BASE_DIR = Path(__file__).resolve().parent.parent
POP_LANDLORD_FILE = BASE_DIR / "output" / "us_cities_100k_population_ranked_WITH_LANDLORD_AND_POP_CHANGE.xlsx"
HOME_VALUE_FILE = BASE_DIR / "output" / "us_cities_100k_population_ranked_WITH_HOME_VALUE_GROWTH.xlsx"
JOB_GROWTH_FILE = BASE_DIR / "output" / "us_cities_100k_population_ranked_WITH_JOB_GROWTH_FIXED.xlsx"
CLIMATE_RISK_FILE = BASE_DIR / "output" / "us_cities_100k_population_ranked_WITH_CLIMATE_RISK.xlsx"
CRIME_FINAL_FILE = BASE_DIR / "output" / "us_cities_100k_population_ranked_ALL_METRICS_CLEAN.xlsx"


def _step(steps: list[dict[str, Any]], name: str, fn, *args, **kwargs) -> Any:
    """Run one pipeline step, recording success/failure without aborting
    the whole refresh -- one metric family failing (e.g. a transient BLS
    API hiccup) shouldn't prevent the others from refreshing.

    Also catches SystemExit: a missing optional dependency (e.g. geopandas
    not installed in a given environment) surfaces as `sys.exit(1)` from
    that module's own import guard, and SystemExit is a BaseException, not
    an Exception -- left uncaught, it silently kills the background
    refresh thread with no trace anywhere, leaving that metric's
    last-refreshed timestamp untouched with no visible sign of failure.
    """
    try:
        result = fn(*args, **kwargs)
        steps.append({"step": name, "status": "ok"})
        return result
    except SystemExit as exc:
        detail = f"step exited via sys.exit({exc.code}) -- likely a missing dependency in this environment; check server logs"
        steps.append({"step": name, "status": "error", "error": detail})
        return None
    except Exception as exc:
        steps.append({"step": name, "status": "error", "error": str(exc)})
        return None


def run_full_refresh(
    db_path: str | Path | None = None,
    skip_climate: bool = False,
    skip_crime: bool = False,
) -> dict[str, Any]:
    """Run the real Census/ACS chain, BLS job growth, climate risk, and
    (if an uploaded FBI file is present) the crime pipeline, then
    ingest everything into SQLite. Returns a summary of what ran and what
    got skipped/failed, plus each metric family's row/timestamp outcome.

    skip_climate/skip_crime exist because both are slow (climate: a
    nationwide TIGER download the first time it runs; crime: requires a
    uploaded FBI workbook) -- a caller doing a quick "refresh the live
    stuff" pass can skip them without losing anything, since neither one's
    timestamp gets touched unless it actually ran.
    """
    from add_income_growth import add_income_growth
    from add_home_value_growth import add_home_value_growth
    from add_job_growth import add_job_growth
    import update_fire_metrics as population_updater

    steps: list[dict[str, Any]] = []
    ingest_results: dict[str, Any] = {}

    with db_module.get_connection(db_path) as conn:
        # 1. Population + landlord (Census population file + internal
        #    landlord-friendliness scoring). Writes POP_LANDLORD_FILE.
        _step(steps, "population_fetch", population_updater.main)
        if POP_LANDLORD_FILE.exists():
            ingest_results["population"] = _step(
                steps, "population_ingest", index_builder.ingest_population_and_landlord, POP_LANDLORD_FILE, conn
            )

        # 2. Income growth -- mutates POP_LANDLORD_FILE in place.
        _step(steps, "income_fetch", add_income_growth)
        if POP_LANDLORD_FILE.exists():
            ingest_results["income"] = _step(
                steps, "income_ingest", index_builder.ingest_income, POP_LANDLORD_FILE, conn
            )

        # 3. Home value growth -- reads POP_LANDLORD_FILE, writes HOME_VALUE_FILE.
        _step(steps, "home_value_fetch", add_home_value_growth)
        if HOME_VALUE_FILE.exists():
            ingest_results["home_value"] = _step(
                steps, "home_value_ingest", index_builder.ingest_home_value, HOME_VALUE_FILE, conn
            )

        # 4. Job growth (real BLS API) -- reads HOME_VALUE_FILE, writes JOB_GROWTH_FILE.
        _step(steps, "employment_fetch", add_job_growth)
        if JOB_GROWTH_FILE.exists():
            ingest_results["employment"] = _step(
                steps, "employment_ingest", index_builder.ingest_employment, JOB_GROWTH_FILE, conn
            )

        # 5. Climate risk -- slow (nationwide shapefile download on a cold
        #    cache), so skippable independently of the fast Census/ACS/BLS
        #    metrics above.
        #
        #    The import is wrapped inside the _step()-covered call (rather
        #    than done bare, above the call) specifically so a missing
        #    optional dependency in this environment -- which surfaces as
        #    SystemExit from add_climate_risk.py's own import guard -- is
        #    recorded as a climate_fetch error instead of silently killing
        #    the whole background refresh thread.
        def _run_climate_fetch():
            from add_climate_risk import run_climate_risk
            return run_climate_risk()

        if not skip_climate:
            _step(steps, "climate_fetch", _run_climate_fetch)
            if CLIMATE_RISK_FILE.exists():
                ingest_results["climate"] = _step(
                    steps, "climate_ingest", index_builder.ingest_climate_risk, CLIMATE_RISK_FILE, conn
                )
        else:
            steps.append({"step": "climate_fetch", "status": "skipped"})

        # 6. Crime -- manual/periodic. Only refreshes if an uploaded FBI
        #    Table 8 workbook already exists at FBI_CRIME_WORKBOOK_PATH;
        #    otherwise this is a no-op, not an error, and crime_updated_at
        #    is left untouched.
        #
        #    The imports themselves are wrapped in a crime_import step for
        #    the same reason as climate above -- a failure here (missing
        #    dependency, etc.) must be recorded, not silently swallowed.
        def _load_crime_modules():
            from crime_pipeline import run_crime_pipeline
            from add_crime_index import get_fbi_crime_workbook_path
            return run_crime_pipeline, get_fbi_crime_workbook_path()

        if not skip_crime:
            loaded = _step(steps, "crime_import", _load_crime_modules)
            if loaded is not None:
                run_crime_pipeline, fbi_workbook_path = loaded
                if fbi_workbook_path.exists():
                    _step(steps, "crime_fetch", run_crime_pipeline)
                    if CRIME_FINAL_FILE.exists():
                        ingest_results["crime"] = _step(
                            steps, "crime_ingest", index_builder.ingest_crime, CRIME_FINAL_FILE, conn
                        )
                else:
                    steps.append({
                        "step": "crime_fetch", "status": "skipped",
                        "reason": f"No uploaded FBI Table 8 workbook at {fbi_workbook_path} -- upload one via Admin Data Tools.",
                    })
        else:
            steps.append({"step": "crime_fetch", "status": "skipped"})

        # Ensure coordinates are populated for all currently indexed cities,
        # including rows carried over from older DBs built before lat/lon existed.
        ingest_results["coordinates"] = _step(
            steps, "coordinates_backfill", index_builder.backfill_city_coordinates, conn
        )

        total_cities = conn.execute("SELECT COUNT(*) FROM cities").fetchone()[0]
        db_module.set_metadata(
            conn,
            last_refresh_at=index_builder._utc_now(),
            city_count=total_cities,
        )

    return {
        "steps": steps,
        "ingest_results": ingest_results,
        "total_cities": total_cities,
        "errors": [s for s in steps if s["status"] == "error"],
    }
