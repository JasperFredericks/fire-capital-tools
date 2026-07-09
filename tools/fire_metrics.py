"""
FIRE Capital Tools — FIRE Metric updater tool.

Standalone Flask blueprint wrapper around fire_metrics/update_fire_metrics.py.
"""

from __future__ import annotations

import json
import importlib.util
import io
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file, session
from flask_login import login_required
from werkzeug.utils import secure_filename

from fire_metrics.fire_metrics_updater.city_search import find_city_match
from fire_metrics.fire_metrics_updater.index_builder import build_indexes_from_workbook, read_json, write_json


fire_metrics_bp = Blueprint("fire_metrics", __name__)

ALLOWED_EXT = {".xlsx"}
MAX_PENDING = 8
MISSING_CENSUS_KEY_MESSAGE = (
    "Missing CENSUS_API_KEY environment variable. Add it in Railway Variables before running FIRE Metric updates."
)


def _upload_dir() -> Path:
    path = Path(current_app.config["UPLOAD_FOLDER"]) / "fire_metrics"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_updater_module():
    updater_path = Path(__file__).parent.parent / "fire_metrics" / "update_fire_metrics.py"
    spec = importlib.util.spec_from_file_location("_fire_metrics_updater", updater_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _runtime_dir() -> Path:
    configured = os.getenv("FIRE_METRICS_DATA_DIR", "").strip()
    if configured:
        path = Path(configured)
    else:
        path = Path(current_app.instance_path) / "fire_metrics"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _runtime_paths() -> dict[str, Path]:
    runtime = _runtime_dir()
    return {
        "runtime": runtime,
        "latest_uploaded": runtime / "latest_uploaded.xlsx",
        "latest_updated": runtime / "latest_updated.xlsx",
        "city_index": runtime / "city_metrics_index.json",
        "excluded_index": runtime / "city_excluded_index.json",
        "metadata": runtime / "source_metadata.json",
        "lock": runtime / "refresh.lock",
    }


def _read_metadata() -> dict:
    metadata = read_json(_runtime_paths()["metadata"], default={})
    if not metadata:
        metadata = {
            "status": "missing",
            "last_checked_at": None,
            "last_refresh_at": None,
            "last_index_built_at": None,
            "source_workbook": None,
            "source_last_updated": None,
            "notes": "No FIRE Metric runtime data is available yet.",
        }
    return metadata


def _write_metadata(metadata: dict) -> None:
    write_json(_runtime_paths()["metadata"], metadata)


def _update_metadata(**kwargs) -> dict:
    metadata = _read_metadata()
    metadata.update(kwargs)
    _write_metadata(metadata)
    return metadata


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _refresh_status(metadata: dict) -> dict:
    stale_hours = int(os.getenv("FIRE_METRICS_STALE_HOURS", "24"))
    now_ts = datetime.now(timezone.utc)
    source_workbook = metadata.get("source_workbook")
    source_last_updated = metadata.get("source_last_updated")
    last_index_built_at = metadata.get("last_index_built_at")

    status = metadata.get("status", "missing")
    message = "Runtime data is ready."

    if not source_workbook:
        status = "missing"
        message = "Upload or refresh a workbook to build the FIRE Metric search dashboard."
    elif last_index_built_at:
        try:
            built_ts = datetime.fromisoformat(last_index_built_at)
            age_hours = (now_ts - built_ts).total_seconds() / 3600
            if age_hours > stale_hours:
                status = "stale"
                message = "Newer source data may be available. Run Refresh All Data."
            else:
                status = "current"
                message = "Search index is current."
        except ValueError:
            status = "stale"
            message = "Unable to verify freshness. Run Check for Updates."

    return {
        "status": status,
        "message": message,
        "last_checked_at": metadata.get("last_checked_at"),
        "last_refresh_at": metadata.get("last_refresh_at"),
        "last_index_built_at": last_index_built_at,
        "source_last_updated": source_last_updated,
        "source_workbook": source_workbook,
        "city_count": metadata.get("city_count"),
        "excluded_count": metadata.get("excluded_count"),
    }


def _latest_workbook_path(paths: dict[str, Path]) -> Path | None:
    if paths["latest_updated"].exists():
        return paths["latest_updated"]
    if paths["latest_uploaded"].exists():
        return paths["latest_uploaded"]
    return None


def _load_index_payloads(paths: dict[str, Path]) -> tuple[dict, dict]:
    city_index = read_json(paths["city_index"], default={"cities": []})
    excluded = read_json(paths["excluded_index"], default={"excluded": []})
    return city_index, excluded


def _run_update(input_path: Path, output_path: Path, options: dict) -> dict:
    updater = _load_updater_module()
    return updater.run_update(str(input_path), str(output_path), options)


def _build_index_from_workbook(workbook_path: Path, paths: dict[str, Path]) -> dict:
    result = build_indexes_from_workbook(
        workbook_path=workbook_path,
        city_index_path=paths["city_index"],
        excluded_index_path=paths["excluded_index"],
        metadata_path=paths["metadata"],
    )
    _update_metadata(last_index_built_at=_utc_now(), status="current", notes="Search index rebuilt successfully.")
    return result


def _acquire_refresh_lock(lock_path: Path) -> bool:
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_utc_now())
        return True
    except FileExistsError:
        return False


def _release_refresh_lock(lock_path: Path) -> None:
    lock_path.unlink(missing_ok=True)


def _cleanup_old_uploads(max_age: int | None = None) -> None:
    if max_age is None:
        max_age = int(current_app.permanent_session_lifetime.total_seconds())
    cutoff = time.time() - max_age
    for f in _upload_dir().glob("*.xlsx"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
        except OSError:
            pass


@fire_metrics_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    _cleanup_old_uploads()
    paths = _runtime_paths()

    metadata = _read_metadata()
    status = _refresh_status(metadata)

    context = {
        "run_result": None,
        "error_message": None,
        "success_message": None,
        "download_token": None,
        "selected": {"refresh_all": True, "format_only": False, "dry_run": False},
        "status": status,
        "city_preview": [],
        "search_query": "",
        "search_payload": None,
    }

    city_index, _ = _load_index_payloads(paths)
    context["city_preview"] = city_index.get("cities", [])[:5]

    if request.method == "GET":
        query = request.args.get("q", "").strip()
        if query:
            context["search_query"] = query
            _, excluded_index = _load_index_payloads(paths)
            context["search_payload"] = find_city_match(query, city_index, excluded_index)
        return render_template("tools/fire_metrics.html", **context)

    action = request.form.get("action", "run_upload")
    context["selected"] = {
        "refresh_all": bool(request.form.get("refresh_all")),
        "format_only": bool(request.form.get("format_only")),
        "dry_run": bool(request.form.get("dry_run")),
    }

    if action == "check_updates":
        metadata = _update_metadata(
            last_checked_at=_utc_now(),
            status=_refresh_status(_read_metadata())["status"],
            notes="Freshness check completed.",
        )
        context["status"] = _refresh_status(metadata)
        context["success_message"] = context["status"]["message"]
        return render_template("tools/fire_metrics.html", **context)

    if action == "rebuild_index":
        workbook = _latest_workbook_path(paths)
        if not workbook:
            context["error_message"] = "No runtime workbook is available. Upload a workbook or run a refresh first."
            return render_template("tools/fire_metrics.html", **context), 400
        try:
            result = _build_index_from_workbook(workbook, paths)
            context["success_message"] = (
                f"Search index rebuilt. Cities indexed: {result['city_count']}. Excluded rows: {result['excluded_count']}."
            )
            context["status"] = _refresh_status(_read_metadata())
            city_index, _ = _load_index_payloads(paths)
            context["city_preview"] = city_index.get("cities", [])[:5]
        except Exception as exc:
            context["error_message"] = f"Could not rebuild search index: {exc}"
            return render_template("tools/fire_metrics.html", **context), 500
        return render_template("tools/fire_metrics.html", **context)

    if action in {"refresh_all_latest", "format_only_latest", "dry_run_latest"}:
        source = _latest_workbook_path(paths)
        if not source:
            context["error_message"] = "No runtime workbook found. Upload a workbook before running refresh actions."
            return render_template("tools/fire_metrics.html", **context), 400

        if not _acquire_refresh_lock(paths["lock"]):
            context["error_message"] = "A refresh is already running. Try again in a minute."
            return render_template("tools/fire_metrics.html", **context), 409

        opts = {
            "refresh_all": action == "refresh_all_latest",
            "format_only": action == "format_only_latest",
            "dry_run": action == "dry_run_latest",
        }
        token = secrets.token_urlsafe(16)
        output_path = _upload_dir() / f"{token}_output.xlsx"
        try:
            result = _run_update(source, output_path, opts)
            context["run_result"] = result
            if not opts["dry_run"] and output_path.exists():
                data = output_path.read_bytes()
                paths["latest_updated"].write_bytes(data)
                output_path.unlink(missing_ok=True)
                index_result = _build_index_from_workbook(paths["latest_updated"], paths)
                summary = context["run_result"].setdefault("summary_lines", [])
                summary.append(
                    f"Search index rebuilt with {index_result['city_count']} cities and {index_result['excluded_count']} excluded rows."
                )
                _update_metadata(last_refresh_at=_utc_now(), last_checked_at=_utc_now(), status="current")
                context["success_message"] = "Refresh complete. Latest workbook and search index were updated."
            else:
                _update_metadata(last_checked_at=_utc_now(), status="current")
                context["success_message"] = "Dry run complete. No workbook changes were written."
        except Exception as exc:
            text = str(exc)
            if MISSING_CENSUS_KEY_MESSAGE in text:
                context["error_message"] = MISSING_CENSUS_KEY_MESSAGE
            else:
                context["error_message"] = f"FIRE Metric refresh failed: {text}"
            return render_template("tools/fire_metrics.html", **context), 500
        finally:
            _release_refresh_lock(paths["lock"])

        context["status"] = _refresh_status(_read_metadata())
        city_index, _ = _load_index_payloads(paths)
        context["city_preview"] = city_index.get("cities", [])[:5]
        return render_template("tools/fire_metrics.html", **context)

    if "file" not in request.files:
        context["error_message"] = "No file included in the request."
        return render_template("tools/fire_metrics.html", **context), 400

    file = request.files["file"]
    if not file or not file.filename:
        context["error_message"] = "No file selected."
        return render_template("tools/fire_metrics.html", **context), 400

    original_name = secure_filename(file.filename)
    if Path(original_name).suffix.lower() not in ALLOWED_EXT:
        context["error_message"] = "Only .xlsx files are accepted."
        return render_template("tools/fire_metrics.html", **context), 400

    opts = context["selected"]

    token = secrets.token_urlsafe(16)
    input_path = _upload_dir() / f"{token}_input.xlsx"
    output_path = _upload_dir() / f"{token}_output.xlsx"

    try:
        file.save(str(input_path))
    except Exception as exc:
        context["error_message"] = f"Could not save uploaded file: {exc}"
        return render_template("tools/fire_metrics.html", **context), 500

    try:
        result = _run_update(input_path, output_path, opts)
    except Exception as exc:
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        text = str(exc)
        if MISSING_CENSUS_KEY_MESSAGE in text:
            context["error_message"] = MISSING_CENSUS_KEY_MESSAGE
        else:
            context["error_message"] = f"FIRE Metric update failed: {text}"
        return render_template("tools/fire_metrics.html", **context), 500

    context["run_result"] = result

    # Keep latest uploaded source workbook for future refresh/index actions.
    try:
        paths["latest_uploaded"].write_bytes(input_path.read_bytes())
    except OSError:
        pass

    if not opts.get("dry_run") and output_path.exists():
        try:
            paths["latest_updated"].write_bytes(output_path.read_bytes())
            index_result = _build_index_from_workbook(paths["latest_updated"], paths)
            summary = context["run_result"].setdefault("summary_lines", [])
            summary.append(
                f"Search index rebuilt with {index_result['city_count']} cities and {index_result['excluded_count']} excluded rows."
            )
            _update_metadata(last_refresh_at=_utc_now(), last_checked_at=_utc_now(), status="current")
        except Exception as exc:
            warning = context["run_result"].setdefault("warnings", [])
            warning.append(f"Workbook processed, but search index rebuild failed: {exc}")

        pending: dict = session.get("pending_fire_metrics_downloads", {})
        if len(pending) >= MAX_PENDING:
            oldest = next(iter(pending))
            (_upload_dir() / f"{oldest}_input.xlsx").unlink(missing_ok=True)
            (_upload_dir() / f"{oldest}_output.xlsx").unlink(missing_ok=True)
            del pending[oldest]

        download_name = f"fire_metrics_updated_{original_name}"
        pending[token] = download_name
        session["pending_fire_metrics_downloads"] = pending
        session.modified = True
        context["download_token"] = token

    context["status"] = _refresh_status(_read_metadata())
    city_index, _ = _load_index_payloads(paths)
    context["city_preview"] = city_index.get("cities", [])[:5]

    return render_template("tools/fire_metrics.html", **context)


@fire_metrics_bp.route("/download/<token>")
@login_required
def download(token: str):
    pending: dict = session.get("pending_fire_metrics_downloads", {})
    if token not in pending:
        abort(403)

    output_path = _upload_dir() / f"{token}_output.xlsx"
    input_path = _upload_dir() / f"{token}_input.xlsx"
    if not output_path.exists():
        abort(404)

    download_name = pending[token]
    data = output_path.read_bytes()

    # One-time download cleanup.
    del pending[token]
    session["pending_fire_metrics_downloads"] = pending
    session.modified = True

    output_path.unlink(missing_ok=True)
    input_path.unlink(missing_ok=True)

    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=download_name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@fire_metrics_bp.route("/download-latest")
@login_required
def download_latest():
    paths = _runtime_paths()
    latest = _latest_workbook_path(paths)
    if not latest or not latest.exists():
        abort(404)

    return send_file(
        io.BytesIO(latest.read_bytes()),
        as_attachment=True,
        download_name=latest.name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@fire_metrics_bp.route("/search")
@login_required
def search():
    query = request.args.get("q", "").strip()
    paths = _runtime_paths()
    city_index, excluded = _load_index_payloads(paths)

    try:
        payload = find_city_match(query, city_index, excluded)
        payload["query"] = query
        payload["status_meta"] = _refresh_status(_read_metadata())
        return jsonify(payload)
    except Exception as exc:
        return jsonify(
            {
                "status": "error",
                "query": query,
                "user_message": f"Search failed: {exc}",
            }
        ), 500
