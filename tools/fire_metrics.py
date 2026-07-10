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
- Refresh All Data now runs the real orchestrator in a separate OS process
  (fire_metrics_updater/refresh_worker.py, launched via subprocess.Popen),
  with status tracked in the refresh_metadata table rather than an
  in-process thread/lock -- a prior in-process-thread version of this
  caused 502s in production, since the CPU-heavy climate-risk step shares
  this process's GIL with (and starves) the request that triggered it.
  (Beckett's version called it synchronously, guarded only by a
  re-entrancy lock -- not actually a background job.)
- Rebuild Search Index is repurposed: it now re-ingests from whatever
  pipeline output files already exist on disk, without calling any live
  API -- useful after running a script by hand from the CLI.
- Download Latest Workbook now exports the current database state to a
  fresh .xlsx on demand, since there's no longer one static "latest
  workbook" file -- SQLite is the source of truth.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import login_required

from fire_metrics.fire_metrics_updater import db as db_module
from fire_metrics.fire_metrics_updater.city_search import find_city_match

fire_metrics_bp = Blueprint("fire_metrics", __name__)

REPO_ROOT = Path(__file__).resolve().parent.parent

# A refresh in the "running" state for longer than this is treated as
# crashed/stuck rather than genuinely in progress, so one dead subprocess
# (e.g. killed by an OOM or a Railway restart) can't permanently block
# every future refresh. Generous relative to the real chain (climate risk
# alone is documented elsewhere in this file as "several minutes" on a
# cold cache) but still bounded.
REFRESH_STALE_AFTER_SECONDS = 60 * 60


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
    # Computed first (depends only on request headers, can't itself raise)
    # so the outermost except below always knows whether this caller's own
    # JS is going to do res.json() unconditionally on whatever comes back.
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    def safe_refresh_status() -> dict:
        # _refresh_status() opens its own SQLite connection -- and, right
        # after _start_refresh() below, so does the refresh subprocess it
        # just spawned (schema-init runs on every get_connection() call).
        # A transient "database is locked" race between those two is
        # possible. If it happens, fall back to a status payload built
        # from what we already know rather than letting the exception
        # propagate out of this view. "running" defaults to False here --
        # if we can't even read the DB, we genuinely don't know, and
        # assuming "not running" lets the user retry rather than looking
        # permanently stuck.
        try:
            return _refresh_status()
        except Exception as exc:
            return {
                "status": "error",
                "running": False,
                "last_refresh_at": None,
                "last_refresh_error": f"Could not read refresh status: {exc}",
                "city_count": 0,
            }

    try:
        status = safe_refresh_status()
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

        def respond(status_code: int = 200):
            if is_ajax:
                # Admin actions are triggered via fetch() from the page's
                # own JS specifically so they don't navigate away -- a
                # full-page render/redirect here would reload the page and
                # discard whatever search result the user currently has on
                # screen (client-side only state, never persisted
                # server-side).
                payload = {
                    "success_message": context["success_message"],
                    "error_message": context["error_message"],
                }
                payload.update(safe_refresh_status())
                return jsonify(payload), status_code
            return render_template("tools/fire_metrics.html", **context), status_code

        if action == "refresh_all":
            started = _start_refresh(skip_climate=False, skip_crime=False)
            if started:
                context["success_message"] = "Refresh started in the background. This can take several minutes (climate risk especially, on a cold cache)."
            else:
                context["error_message"] = "A refresh is already running. Check back shortly."
            context["status"] = safe_refresh_status()
            return respond()

        if action == "refresh_live_only":
            # Population/income/home-value/employment only -- skips the slow
            # climate step and the manual/periodic crime step.
            started = _start_refresh(skip_climate=True, skip_crime=True)
            if started:
                context["success_message"] = "Refreshing live metrics (population, income, home value, employment) in the background."
            else:
                context["error_message"] = "A refresh is already running. Check back shortly."
            context["status"] = safe_refresh_status()
            return respond()

        if action == "rebuild_index":
            try:
                results = _reingest_from_disk()
                if not results:
                    context["error_message"] = "No pipeline output files found on disk yet. Run a refresh first."
                else:
                    context["success_message"] = f"Re-ingested from disk: {', '.join(results.keys())}."
            except Exception as exc:
                context["error_message"] = f"Could not re-ingest from disk: {exc}"
            context["status"] = safe_refresh_status()
            return respond()

        context["error_message"] = "Unknown action."
        return respond(status_code=400)
    except Exception as exc:
        # Last-resort guard covering the whole view, including the
        # earliest calls above (status/context/city_preview) that run
        # before respond() even exists yet: if anything in this view
        # raises unexpectedly and the caller is this page's own AJAX JS,
        # it must still get valid JSON back -- otherwise Flask's default
        # HTML error page reaches the browser and res.json() throws a
        # confusing "Unexpected token '<'" instead of showing the real
        # problem. Non-AJAX (plain GET/POST) callers keep the normal Flask
        # error behavior.
        if is_ajax:
            return jsonify({
                "success_message": None,
                "error_message": f"Unexpected error: {exc}",
                "status": "error",
                "running": False,
                "last_refresh_at": None,
                "last_refresh_error": str(exc),
                "city_count": 0,
            }), 500
        raise


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


@fire_metrics_bp.route("/debug-refresh")
@login_required
def debug_refresh():
    # TEMPORARY diagnostic route -- added specifically to inspect the raw
    # refresh_metadata table (including per-step results the normal status
    # payload doesn't surface) without needing direct Railway console/DB
    # access. Not linked from any page; remove once the climate-risk
    # never-populates investigation is resolved.
    with db_module.get_connection() as conn:
        metadata = db_module.get_metadata(conn)
    steps_raw = metadata.get("refresh_steps_json")
    try:
        parsed_steps = json.loads(steps_raw) if steps_raw else None
    except json.JSONDecodeError as exc:
        parsed_steps = f"<could not parse refresh_steps_json: {exc}>"
    return jsonify({
        "raw_metadata": metadata,
        "parsed_steps": parsed_steps,
    })


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
