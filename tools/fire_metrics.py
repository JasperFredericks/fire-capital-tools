"""
FIRE Capital Tools — FIRE Metric search dashboard.

Search UI/frontend originally built by Beckett (BeckettTest branch, commit
04d3b47) on top of a JSON-file index and a validate-and-copy scaffold.
This version keeps his templates/city_search.py matching logic/dashboard
card layout, but the backend underneath is now SQLite (fire_metrics_updater
/db.py) fed by the real, working pipeline scripts (fire_metrics/scripts/,
fixed in JasperTest Priorities 1-4) via a real orchestration function
(fire_metrics_updater/orchestrator.py) instead of a scaffold that only
copied the uploaded file.

Changes from Beckett's version, and why (see final report for full detail):
- Upload-a-workbook-and-process-it is removed. The live pipeline no longer
  needs a user-uploaded workbook to gather fresh data (population/income/
  home-value/employment/climate all come from live APIs); there's no
  coherent thing left for an arbitrary uploaded workbook to do.
- Format Only / Dry Run options are removed along with it -- both were
  specific to the old single-workbook run_update() scaffold and don't map
  to the new per-metric-family orchestration.
- Refresh All Data now runs the real orchestrator as a background thread
  (Beckett's version called it synchronously, guarded only by a
  re-entrancy lock -- not actually a background job).
- Rebuild Search Index is repurposed: it now re-ingests from whatever
  pipeline output files already exist on disk, without calling any live
  API -- useful after running a script by hand from the CLI.
- Download Latest Workbook now exports the current database state to a
  fresh .xlsx on demand, since there's no longer one static "latest
  workbook" file -- SQLite is the source of truth.
"""

from __future__ import annotations

import io
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import login_required

from fire_metrics.fire_metrics_updater import db as db_module
from fire_metrics.fire_metrics_updater.city_search import find_city_match
from fire_metrics.fire_metrics_updater.orchestrator import run_full_refresh

fire_metrics_bp = Blueprint("fire_metrics", __name__)

_refresh_lock = threading.Lock()
_refresh_thread: threading.Thread | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _refresh_status() -> dict:
    with db_module.get_connection() as conn:
        metadata = db_module.get_metadata(conn)
        total_cities = conn.execute("SELECT COUNT(*) FROM cities").fetchone()[0]

    running = _refresh_thread is not None and _refresh_thread.is_alive()
    status = "running" if running else metadata.get("last_refresh_status", "missing" if total_cities == 0 else "current")

    return {
        "status": status,
        "running": running,
        "last_refresh_at": metadata.get("last_refresh_at"),
        "last_refresh_error": metadata.get("last_refresh_error"),
        "city_count": total_cities,
    }


def _run_refresh_background(skip_climate: bool, skip_crime: bool) -> None:
    try:
        result = run_full_refresh(skip_climate=skip_climate, skip_crime=skip_crime)
        with db_module.get_connection() as conn:
            db_module.set_metadata(
                conn,
                last_refresh_status="current" if not result["errors"] else "error",
                last_refresh_error="; ".join(f"{e['step']}: {e['error']}" for e in result["errors"]) or None,
            )
    except Exception as exc:
        with db_module.get_connection() as conn:
            db_module.set_metadata(conn, last_refresh_status="error", last_refresh_error=str(exc))
    finally:
        _refresh_lock.release()


def _start_refresh(skip_climate: bool = False, skip_crime: bool = True) -> bool:
    """Start the refresh as a background thread. Returns False if a refresh
    is already running (the caller should show that as a message, not
    start a second overlapping one).
    """
    global _refresh_thread
    if not _refresh_lock.acquire(blocking=False):
        return False
    _refresh_thread = threading.Thread(
        target=_run_refresh_background, args=(skip_climate, skip_crime), daemon=True
    )
    _refresh_thread.start()
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


@fire_metrics_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    status = _refresh_status()
    context = {
        "status": status,
        "success_message": None,
        "error_message": None,
        "search_query": "",
        "search_payload": None,
        "city_preview": [],
    }

    with db_module.get_connection() as conn:
        context["city_preview"] = db_module.fetch_all_cities(conn)[:5]

    if request.method == "GET":
        query = request.args.get("q", "").strip()
        if query:
            context["search_query"] = query
            with db_module.get_connection() as conn:
                city_index = db_module.build_city_index_payload(conn)
                excluded_index = db_module.build_excluded_index_payload(conn)
            context["search_payload"] = find_city_match(query, city_index, excluded_index)
        return render_template("tools/fire_metrics.html", **context)

    action = request.form.get("action", "")

    if action == "refresh_all":
        started = _start_refresh(skip_climate=False, skip_crime=False)
        if started:
            context["success_message"] = "Refresh started in the background. This can take several minutes (climate risk especially, on a cold cache). Reload this page to check progress."
        else:
            context["error_message"] = "A refresh is already running. Check back shortly."
        context["status"] = _refresh_status()
        return render_template("tools/fire_metrics.html", **context)

    if action == "refresh_live_only":
        # Population/income/home-value/employment only -- skips the slow
        # climate step and the manual/periodic crime step.
        started = _start_refresh(skip_climate=True, skip_crime=True)
        if started:
            context["success_message"] = "Refreshing live metrics (population, income, home value, employment) in the background."
        else:
            context["error_message"] = "A refresh is already running. Check back shortly."
        context["status"] = _refresh_status()
        return render_template("tools/fire_metrics.html", **context)

    if action == "rebuild_index":
        try:
            results = _reingest_from_disk()
            if not results:
                context["error_message"] = "No pipeline output files found on disk yet. Run a refresh first."
            else:
                context["success_message"] = f"Re-ingested from disk: {', '.join(results.keys())}."
        except Exception as exc:
            context["error_message"] = f"Could not re-ingest from disk: {exc}"
        context["status"] = _refresh_status()
        return render_template("tools/fire_metrics.html", **context)

    context["error_message"] = "Unknown action."
    return render_template("tools/fire_metrics.html", **context), 400


@fire_metrics_bp.route("/search")
@login_required
def search():
    query = request.args.get("q", "").strip()
    try:
        with db_module.get_connection() as conn:
            city_index = db_module.build_city_index_payload(conn)
            excluded_index = db_module.build_excluded_index_payload(conn)
        payload = find_city_match(query, city_index, excluded_index)
        payload["query"] = query
        payload["status_meta"] = _refresh_status()
        return jsonify(payload)
    except Exception as exc:
        return jsonify({"status": "error", "query": query, "user_message": f"Search failed: {exc}"}), 500


@fire_metrics_bp.route("/refresh-status")
@login_required
def refresh_status():
    return jsonify(_refresh_status())


@fire_metrics_bp.route("/download-latest")
@login_required
def download_latest():
    data = _export_workbook()
    from flask import send_file

    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name="fire_metrics_city_data.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
