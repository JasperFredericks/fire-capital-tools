"""FIRE Capital Tools — FIRE Metric search dashboard (Flask routes)."""

from __future__ import annotations

import io
import json
import os
import tempfile
from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import login_required

from fire_metrics.fire_metrics_updater import db as db_module
from fire_metrics.fire_metrics_updater.city_search import find_city_match
from tools import fire_metrics_ai_summary as ai_summary
from tools.fire_metrics.constants import MAX_CRIME_WORKBOOK_BYTES, TOP_CITY_METRICS
from tools.fire_metrics.crime_workbook import (
    _crime_workbook_status,
    _get_crime_workbook_path,
    _validate_crime_workbook_bytes,
)
from tools.fire_metrics.services import (
    _build_fire_score_index,
    _enrich_search_payload_with_fire_score,
    _export_workbook,
    _fetch_top_cities,
    _parse_top_cities_limit,
    _refresh_status,
    _reingest_from_disk,
    _start_refresh,
    _summary_api_key,
    _summary_enabled,
    _summary_model_name,
    _summary_unavailable_response,
)


fire_metrics_bp = Blueprint("fire_metrics", __name__)


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
            "crime_workbook": _crime_workbook_status(),
            "success_message": None,
            "error_message": None,
            "search_query": "",
            "search_payload": None,
            "city_preview": [],
            "google_maps_api_key": current_app.config.get("GOOGLE_MAPS_API_KEY") or "",
            "google_maps_map_id": current_app.config.get("GOOGLE_MAPS_MAP_ID") or "",
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
                    score_index = _build_fire_score_index(conn)
                context["search_payload"] = _enrich_search_payload_with_fire_score(
                    find_city_match(query, city_index, excluded_index),
                    score_index,
                )
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
                    "crime_workbook": _crime_workbook_status(),
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
                "crime_workbook": {"exists": False, "uploaded_at": None},
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
            score_index = _build_fire_score_index(conn)
        payload = find_city_match(query, city_index, excluded_index)
        payload = _enrich_search_payload_with_fire_score(payload, score_index)
        payload["query"] = query
        payload["status_meta"] = _refresh_status()
        return jsonify(payload)
    except Exception as exc:
        return jsonify({"status": "error", "query": query, "user_message": f"Search failed: {exc}"}), 500


@fire_metrics_bp.route("/api/top-cities")
@login_required
def top_cities():
    metric = str(request.args.get("metric") or "").strip()
    try:
        limit = _parse_top_cities_limit(request.args.get("limit"))
    except ValueError as exc:
        return jsonify({
            "status": "error",
            "error_code": "invalid_limit",
            "user_message": f"Invalid limit: {exc}",
        }), 400

    if metric not in TOP_CITY_METRICS:
        return jsonify({
            "status": "error",
            "error_code": "invalid_metric",
            "user_message": "Unknown ranking metric.",
        }), 400

    try:
        with db_module.get_connection() as conn:
            spec, cities = _fetch_top_cities(conn, metric_key=metric, limit=limit)
    except Exception as exc:
        current_app.logger.exception("FIRE Metrics top-cities endpoint failed: %s", exc.__class__.__name__)
        return jsonify({
            "status": "error",
            "error_code": "top_cities_failed",
            "user_message": "Top city ranking is currently unavailable.",
        }), 500

    return jsonify({
        "status": "ready",
        "metric": metric,
        "metric_label": spec["label"],
        "direction": spec["direction"],
        "city_count": len(cities),
        "cities": cities,
    })


@fire_metrics_bp.route("/api/city-summary", methods=["POST"])
@login_required
def city_summary():
    payload = request.get_json(silent=True) or {}
    city_key = str(payload.get("city_key") or "").strip()
    city = str(payload.get("city") or "").strip()
    state = str(payload.get("state") or "").strip().upper()

    if not city_key and (not city or not state):
        return jsonify({
            "status": "error",
            "error_code": "invalid_city_identifier",
            "user_message": "City identifier is required.",
        }), 400

    try:
        with db_module.get_connection() as conn:
            selected_city = db_module.fetch_city_by_summary_identity(
                conn,
                city_key=city_key or None,
                city=city or None,
                state=state or None,
            )
            if not selected_city:
                return jsonify({
                    "status": "error",
                    "error_code": "city_not_found",
                    "user_message": "City not found in tracked FIRE Metrics data.",
                }), 404

            all_cities = db_module.fetch_all_included_cities(conn)
            metadata = db_module.get_metadata(conn)

            benchmarks = ai_summary.compute_benchmarks(selected_city, all_cities)

            if not _summary_enabled():
                return jsonify(_summary_unavailable_response(
                    selected_city=selected_city,
                    benchmark_data=benchmarks,
                    reason="AI summaries are disabled.",
                    data_refreshed_at=metadata.get("last_refresh_at"),
                ))

            model_name = _summary_model_name()
            fingerprint_input = ai_summary.fingerprint_payload(
                selected_city=selected_city,
                benchmarks=benchmarks,
                model_name=model_name,
                refresh_last_at=metadata.get("last_refresh_at"),
            )
            data_fingerprint = ai_summary.build_fingerprint(fingerprint_input)

            try:
                cache_row = db_module.fetch_cached_city_summary(
                    conn,
                    city=selected_city["city"],
                    state=selected_city["state"],
                    data_fingerprint=data_fingerprint,
                    model_name=model_name,
                    prompt_version=ai_summary.PROMPT_VERSION,
                )
            except Exception as exc:
                current_app.logger.warning(
                    "FIRE Metrics city-summary cache read failed: %s",
                    exc.__class__.__name__,
                )
                cache_row = None
            if cache_row:
                return jsonify({
                    "status": "ready",
                    "summary": cache_row["summary_text"],
                    "summary_structured": {
                        "strength_sentence": cache_row["strength_sentence"],
                        "weakness_sentence": cache_row["weakness_sentence"],
                        "comparison_sentence": cache_row["comparison_sentence"],
                    },
                    "generated_at": cache_row["generated_at"],
                    "data_refreshed_at": metadata.get("last_refresh_at"),
                    "cached": True,
                    "city_key": cache_row["city_key"],
                    "relative_market_profile_score": benchmarks.get("relative_market_profile_score"),
                    "relative_market_profile_percentile": benchmarks.get("relative_market_profile_percentile"),
                    "tracked_city_relative_market_profile_average": benchmarks.get("tracked_city_relative_market_profile_average"),
                    "recommendation_category": benchmarks.get("recommendation_category"),
                    "score": benchmarks.get("selected_overall_score"),
                    "computed_composite_score": benchmarks.get("selected_overall_score"),
                    "tracked_city_average": benchmarks.get("tracked_city_average"),
                    "tracked_city_composite_average": benchmarks.get("tracked_city_average"),
                    "tracked_city_count": benchmarks.get("tracked_city_count"),
                    "percentile": benchmarks.get("selected_percentile"),
                    "source": "cache",
                })

            generated_at = ai_summary.utc_now_iso()
            api_key = _summary_api_key()
            if not api_key:
                return jsonify(_summary_unavailable_response(
                    selected_city=selected_city,
                    benchmark_data=benchmarks,
                    reason="OPENAI_API_KEY is not configured.",
                    data_refreshed_at=metadata.get("last_refresh_at"),
                ))

            if not model_name:
                return jsonify(_summary_unavailable_response(
                    selected_city=selected_city,
                    benchmark_data=benchmarks,
                    reason="FIRE_METRICS_SUMMARY_MODEL is not configured.",
                    data_refreshed_at=metadata.get("last_refresh_at"),
                ))

            try:
                structured = ai_summary.openai_summary(
                    api_key=api_key,
                    model_name=model_name,
                    selected_city=selected_city,
                    benchmarks=benchmarks,
                )
                structured = ai_summary.normalize_summary(structured, selected_city, benchmarks)
            except Exception:
                structured = ai_summary.fallback_summary(selected_city, benchmarks)

            summary_text = ai_summary.combined_summary(structured)
            cache_payload = {
                "city": selected_city["city"],
                "state": selected_city["state"],
                "city_key": ai_summary.city_key(selected_city),
                "data_fingerprint": data_fingerprint,
                "model_name": model_name,
                "prompt_version": ai_summary.PROMPT_VERSION,
                "summary_text": summary_text,
                "strength_sentence": structured["strength_sentence"],
                "weakness_sentence": structured["weakness_sentence"],
                "comparison_sentence": structured["comparison_sentence"],
                "generated_at": generated_at,
            }

            try:
                db_module.upsert_city_summary_cache(conn, cache_payload)
            except Exception as exc:
                current_app.logger.warning(
                    "FIRE Metrics city-summary cache write failed: %s",
                    exc.__class__.__name__,
                )

            return jsonify({
                "status": "ready",
                "summary": summary_text,
                "summary_structured": structured,
                "generated_at": generated_at,
                "data_refreshed_at": metadata.get("last_refresh_at"),
                "cached": False,
                "city_key": cache_payload["city_key"],
                "relative_market_profile_score": benchmarks.get("relative_market_profile_score"),
                "relative_market_profile_percentile": benchmarks.get("relative_market_profile_percentile"),
                "tracked_city_relative_market_profile_average": benchmarks.get("tracked_city_relative_market_profile_average"),
                "recommendation_category": benchmarks.get("recommendation_category"),
                "score": benchmarks.get("selected_overall_score"),
                "computed_composite_score": benchmarks.get("selected_overall_score"),
                "tracked_city_average": benchmarks.get("tracked_city_average"),
                "tracked_city_composite_average": benchmarks.get("tracked_city_average"),
                "tracked_city_count": benchmarks.get("tracked_city_count"),
                "percentile": benchmarks.get("selected_percentile"),
                "source": "generated",
            })
    except Exception as exc:
        current_app.logger.exception("FIRE Metrics city-summary endpoint failed: %s", exc.__class__.__name__)
        response = _summary_unavailable_response(
            selected_city=None,
            benchmark_data=None,
            reason="Summary generation is currently unavailable.",
            data_refreshed_at=None,
        )
        response["error_code"] = "summary_endpoint_failed"
        return jsonify(response), 500


@fire_metrics_bp.route("/refresh-status")
@login_required
def refresh_status():
    return jsonify(_refresh_status())


@fire_metrics_bp.route("/upload-crime-workbook", methods=["POST"])
@login_required
def upload_crime_workbook():
    def safe_crime_workbook_status() -> dict:
        try:
            return _crime_workbook_status()
        except Exception:
            return {"exists": False, "uploaded_at": None}

    def respond(success: bool, message: str, status_code: int = 200):
        return jsonify({
            "success": success,
            "message": message,
            "crime_workbook": safe_crime_workbook_status(),
        }), status_code

    try:
        file = request.files.get("crime_workbook")
        if file is None or not file.filename:
            return respond(False, "No file selected.", 400)

        if not file.filename.lower().endswith(".xlsx"):
            return respond(False, "File must be a .xlsx workbook.", 400)

        data = file.read()
        if not data:
            return respond(False, "File is empty. Upload the .xlsx workbook exactly as downloaded from the FBI.", 400)

        if len(data) > MAX_CRIME_WORKBOOK_BYTES:
            size_mb = len(data) / (1024 * 1024)
            return respond(
                False,
                f"File is too large ({size_mb:.1f} MB) -- the real FBI Table 8 workbook is "
                f"only a few MB. Check you selected the right file.",
                400,
            )

        validation_error = _validate_crime_workbook_bytes(data)
        if validation_error:
            return respond(False, validation_error, 400)

        # Uses the same resolver the crime pipeline uses. In production
        # this should be FBI_CRIME_WORKBOOK_PATH on the persistent /data
        # volume, so the file survives redeploys the same way the SQLite
        # DB now does via FIRE_METRICS_DB_PATH.
        target_path = _get_crime_workbook_path()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile("wb", dir=target_path.parent, delete=False) as tmp:
                tmp.write(data)
                tmp_path = Path(tmp.name)
            os.replace(tmp_path, target_path)
        finally:
            if tmp_path and tmp_path.exists():
                tmp_path.unlink()

        return respond(
            True,
            "Crime workbook uploaded. It will be picked up the next time you run "
            "\"Refresh All Data\".",
        )
    except Exception as exc:
        return respond(False, f"Unexpected error while uploading: {exc}", 500)


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
