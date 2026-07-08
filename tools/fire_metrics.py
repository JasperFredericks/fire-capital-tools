"""
FIRE Capital Tools — FIRE Metric updater tool.

Standalone Flask blueprint wrapper around fire_metrics/update_fire_metrics.py.
"""

from __future__ import annotations

import importlib.util
import io
import secrets
import time
from pathlib import Path

from flask import Blueprint, abort, current_app, render_template, request, send_file, session
from flask_login import login_required
from werkzeug.utils import secure_filename


fire_metrics_bp = Blueprint("fire_metrics", __name__)

ALLOWED_EXT = {".xlsx"}
MAX_PENDING = 8


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

    context = {
        "run_result": None,
        "error_message": None,
        "download_token": None,
        "selected": {"refresh_all": True, "format_only": False, "dry_run": False},
    }

    if request.method == "GET":
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

    opts = {
        "refresh_all": bool(request.form.get("refresh_all")),
        "format_only": bool(request.form.get("format_only")),
        "dry_run": bool(request.form.get("dry_run")),
    }
    context["selected"] = opts

    token = secrets.token_urlsafe(16)
    input_path = _upload_dir() / f"{token}_input.xlsx"
    output_path = _upload_dir() / f"{token}_output.xlsx"

    try:
        file.save(str(input_path))
    except Exception as exc:
        context["error_message"] = f"Could not save uploaded file: {exc}"
        return render_template("tools/fire_metrics.html", **context), 500

    try:
        updater = _load_updater_module()
        result = updater.run_update(str(input_path), str(output_path), opts)
    except Exception as exc:
        input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        text = str(exc)
        if "Missing CENSUS_API_KEY environment variable" in text:
            context["error_message"] = (
                "Missing CENSUS_API_KEY environment variable. Add it in Railway Variables before running FIRE Metric updates."
            )
        else:
            context["error_message"] = f"FIRE Metric update failed: {text}"
        return render_template("tools/fire_metrics.html", **context), 500

    context["run_result"] = result

    if not opts.get("dry_run") and output_path.exists():
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
