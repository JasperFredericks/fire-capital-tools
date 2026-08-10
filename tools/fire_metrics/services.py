from __future__ import annotations

import io
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

from flask import current_app

from fire_metrics.fire_metrics_updater import db as db_module
from tools import fire_metrics_ai_summary as ai_summary
from tools import fire_metrics_score
from tools.fire_metrics.constants import (
    REFRESH_STALE_AFTER_SECONDS,
    REPO_ROOT,
    TOP_CITY_METRICS,
)


def _parse_top_cities_limit(value: str | None) -> int:
    if value is None or not str(value).strip():
        return 10
    try:
        parsed = int(str(value).strip())
    except ValueError as exc:
        raise ValueError("limit must be an integer") from exc
    if parsed < 1:
        raise ValueError("limit must be at least 1")
    return min(parsed, 10)


def _fetch_top_cities(
    conn,
    *,
    metric_key: str,
    limit: int,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    spec = TOP_CITY_METRICS.get(metric_key)
    if not spec:
        raise KeyError(metric_key)

    all_included = db_module.fetch_all_included_cities(conn)
    score_index = fire_metrics_score.build_fire_score_index(all_included)

    if metric_key == "fire_score" or spec.get("computed") == "fire_score":
        sort_values = score_index.get("sort_score_by_city_key", {})
        scored = fire_metrics_score.enrich_cities_with_fire_score(all_included, score_index)
        ranked = [city for city in scored if city.get("fire_score") is not None]
        ranked.sort(
            key=lambda city: (
                -(sort_values.get(city.get("city_key")) or 0.0),
                -(float(city.get("fire_score_coverage") or 0.0)),
                -(float(city.get("population_current") or 0.0)),
                str(city.get("state") or ""),
                str(city.get("city") or ""),
            )
        )
        cities = ranked[:limit]
    else:
        column = spec["column"]
        primary_dir = "ASC" if spec["direction"] == "asc" else "DESC"

        rows = conn.execute(
            f"""
            SELECT *
            FROM cities
            WHERE include_flag = 1
              AND {column} IS NOT NULL
            ORDER BY
              {column} {primary_dir},
              population_current DESC,
              state ASC,
              city ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        cities = [
            fire_metrics_score.enrich_city_with_fire_score(
                db_module.city_row_to_dict(row),
                score_index,
            )
            for row in rows
        ]

    for city in cities:
        aliases = conn.execute(
            "SELECT search_key FROM search_aliases WHERE city = ? AND state = ?",
            (city["city"], city["state"]),
        ).fetchall()
        city["search_keys"] = [item["search_key"] for item in aliases]
        city["warnings"] = list(city.get("warnings") or [])

    return spec, cities


def _build_fire_score_index(conn) -> dict[str, Any]:
    all_included = db_module.fetch_all_included_cities(conn)
    return fire_metrics_score.build_fire_score_index(all_included)


def _enrich_search_payload_with_fire_score(payload: dict[str, Any], score_index: dict[str, Any]) -> dict[str, Any]:
    status = str(payload.get("status") or "")
    if status != "found" or not isinstance(payload.get("city"), dict):
        return payload
    enriched = dict(payload)
    enriched["city"] = fire_metrics_score.enrich_city_with_fire_score(payload["city"], score_index)
    return enriched


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_refresh_running(metadata: dict) -> bool:
    """Derive "is a refresh actually running" from persisted state alone --
    no in-process thread/lock object, since the real work now happens in a
    separate OS process (possibly started by a different web request than
    the one asking) and status must be readable regardless of which
    process/request is checking.
    """
    if metadata.get("refresh_running") != "1":
        return False
    started_at = metadata.get("refresh_started_at")
    if not started_at:
        return False
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return False
    age_seconds = (datetime.now(timezone.utc) - started).total_seconds()
    return age_seconds < REFRESH_STALE_AFTER_SECONDS


def _refresh_status() -> dict:
    with db_module.get_connection() as conn:
        metadata = db_module.get_metadata(conn)
        total_cities = conn.execute("SELECT COUNT(*) FROM cities").fetchone()[0]

    running = _is_refresh_running(metadata)
    status = "running" if running else metadata.get("last_refresh_status", "missing" if total_cities == 0 else "current")

    return {
        "status": status,
        "running": running,
        "last_refresh_at": metadata.get("last_refresh_at"),
        "last_refresh_error": metadata.get("last_refresh_error"),
        "city_count": total_cities,
    }


def _summary_enabled() -> bool:
    return bool(current_app.config.get("FIRE_METRICS_AI_SUMMARIES_ENABLED", False))


def _summary_model_name() -> str:
    return str(current_app.config.get("FIRE_METRICS_SUMMARY_MODEL") or "").strip()


def _summary_api_key() -> str:
    return str(current_app.config.get("OPENAI_API_KEY") or "").strip()


def _cre_research_model_name() -> str:
    return str(current_app.config.get("FIRE_METRICS_CRE_MODEL") or "gpt-4o-mini-search-preview").strip()


def _summary_unavailable_response(
    *,
    selected_city: dict[str, Any] | None,
    benchmark_data: dict[str, Any] | None,
    reason: str,
    data_refreshed_at: str | None = None,
):
    if selected_city and benchmark_data:
        structured = ai_summary.fallback_summary(selected_city, benchmark_data)
        combined = ai_summary.combined_summary(structured)
        return {
            "status": "ready",
            "summary": combined,
            "summary_structured": structured,
            "research_sources": [],
            "generated_at": ai_summary.utc_now_iso(),
            "data_refreshed_at": data_refreshed_at,
            "cached": False,
            "city_key": ai_summary.city_key(selected_city),
            "relative_market_profile_score": benchmark_data.get("relative_market_profile_score"),
            "relative_market_profile_percentile": benchmark_data.get("relative_market_profile_percentile"),
            "tracked_city_relative_market_profile_average": benchmark_data.get("tracked_city_relative_market_profile_average"),
            "recommendation_category": benchmark_data.get("recommendation_category"),
            "score": benchmark_data.get("selected_overall_score"),
            "computed_composite_score": benchmark_data.get("selected_overall_score"),
            "tracked_city_average": benchmark_data.get("tracked_city_average"),
            "tracked_city_composite_average": benchmark_data.get("tracked_city_average"),
            "tracked_city_count": benchmark_data.get("tracked_city_count"),
            "percentile": benchmark_data.get("selected_percentile"),
            "source": "fallback",
            "note": reason,
        }
    return {
        "status": "unavailable",
        "summary": "AI market overview is unavailable for this city.",
        "summary_structured": {
            "strength_sentence": "The strongest currently available signals are limited by missing values.",
            "weakness_sentence": "The largest currently available risks are limited by missing values.",
            "comparison_sentence": "The computed FIRE Metrics composite score assessment is limited because too many component values are missing.",
        },
        "generated_at": ai_summary.utc_now_iso(),
        "data_refreshed_at": data_refreshed_at,
        "cached": False,
        "city_key": ai_summary.city_key(selected_city) if selected_city else None,
        "relative_market_profile_score": None,
        "relative_market_profile_percentile": None,
        "tracked_city_relative_market_profile_average": None,
        "recommendation_category": None,
        "score": None,
        "computed_composite_score": None,
        "tracked_city_average": None,
        "tracked_city_composite_average": None,
        "tracked_city_count": 0,
        "percentile": None,
        "source": "fallback",
        "note": reason,
    }


def _start_refresh(skip_climate: bool = False, skip_crime: bool = True) -> bool:
    """Start the refresh as a real, separate OS process (fire_metrics/
    fire_metrics_updater/refresh_worker.py) -- not a thread. Threads share
    this process's GIL, so the CPU-heavy climate-risk step (geopandas/GDAL
    processing) was starving this same process's ability to answer the
    request that triggered it, until Railway's proxy gave up and returned
    a 502 -- confirmed empirically, and confirmed NOT fixed by
    threaded=True (that only helps connection-accept concurrency, not
    GIL/CPU contention). A real subprocess has its own GIL.

    Returns False if a refresh is already running and not stale (the
    caller should show that as a message, not start a second overlapping
    one). There's a small theoretical check-then-write race if two
    requests hit this within microseconds of each other -- acceptable for
    an admin button a human clicks, not worth the extra complexity of a
    manual SQLite write-lock for.
    """
    with db_module.get_connection() as conn:
        metadata = db_module.get_metadata(conn)
        if _is_refresh_running(metadata):
            return False

        args = [sys.executable, "-m", "fire_metrics.fire_metrics_updater.refresh_worker"]
        if skip_climate:
            args.append("--skip-climate")
        if skip_crime:
            args.append("--skip-crime")

        proc = subprocess.Popen(
            args,
            cwd=str(REPO_ROOT),
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Written here (the parent), not by the worker at its own startup:
        # this must be visible to a second request arriving microseconds
        # later, before the subprocess has even finished interpreter
        # startup and its own imports -- Popen() already returns proc.pid
        # synchronously, so there's no need to wait on the child to report
        # it back.
        db_module.set_metadata(
            conn,
            refresh_running="1",
            refresh_started_at=_utc_now(),
            refresh_pid=str(proc.pid),
        )
    return True


def _reingest_from_disk() -> dict:
    """Re-ingest whatever pipeline output files are already on disk, with
    no live API calls -- for picking up a script that was run by hand.
    """
    from fire_metrics.fire_metrics_updater import index_builder, orchestrator as orch

    results = {}
    with db_module.get_connection() as conn:
        if orch.POP_LANDLORD_FILE.exists():
            results["population"] = index_builder.ingest_population_and_landlord(orch.POP_LANDLORD_FILE, conn)
            results["income"] = index_builder.ingest_income(orch.POP_LANDLORD_FILE, conn)
        if orch.HOME_VALUE_FILE.exists():
            results["home_value"] = index_builder.ingest_home_value(orch.HOME_VALUE_FILE, conn)
        if orch.JOB_GROWTH_FILE.exists():
            results["employment"] = index_builder.ingest_employment(orch.JOB_GROWTH_FILE, conn)
        if orch.CLIMATE_RISK_FILE.exists():
            results["climate"] = index_builder.ingest_climate_risk(orch.CLIMATE_RISK_FILE, conn)
        if orch.CRIME_FINAL_FILE.exists():
            results["crime"] = index_builder.ingest_crime(orch.CRIME_FINAL_FILE, conn)
        results["coordinates"] = index_builder.backfill_city_coordinates(conn)
    return results


def _export_workbook() -> bytes:
    import openpyxl

    with db_module.get_connection() as conn:
        cities = db_module.fetch_all_cities(conn)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "City Metrics"
    if cities:
        headers = [k for k in cities[0].keys() if k != "search_keys"]
        ws.append(headers)
        for city in cities:
            ws.append([city.get(h) for h in headers])

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
