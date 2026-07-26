from __future__ import annotations

import io
import os  # noqa: F401 (preserved from original module imports)
import secrets
from pathlib import Path
from typing import Any  # noqa: F401 (preserved from original module imports)

from flask import (
    Blueprint,
    abort,
    jsonify,
    render_template,
    request,
    send_file,
    session,
)
from flask_login import login_required
from matplotlib.patches import Polygon  # noqa: F401 (preserved from original module imports)
from werkzeug.utils import secure_filename

from tools.scorecard_pro.charts import LARGE_CHART_SCALE, _LARGE_CHART_BUILDERS
from tools.scorecard_pro.constants import (
    ALLOWED_PNL_EXT,
    ALLOWED_SCORECARD_EXT,
    MAX_PENDING,
)
from tools.scorecard_pro.processing import (
    build_kpi_dataframe,
    build_payload,
    process_scorecard,
)
from tools.scorecard_pro.utils import (
    _assert_pending_token,
    _cleanup_old_uploads,
    _delete_token_files,
    _load_record,
    _mimetype_for_kind,
    _upload_dir,
)


scorecard_bp = Blueprint("scorecard", __name__)


@scorecard_bp.route("/")
@login_required
def index():
    return render_template("tools/scorecard_pro.html")


@scorecard_bp.route("/upload", methods=["POST"])
@login_required
def upload():
    _cleanup_old_uploads()

    if "pnl_file" not in request.files:
        return jsonify({"error": "No P&L file included in the request."}), 400

    pnl_file = request.files["pnl_file"]
    if not pnl_file or not pnl_file.filename:
        return jsonify({"error": "No P&L file selected."}), 400

    pnl_name = secure_filename(pnl_file.filename)
    pnl_ext = Path(pnl_name).suffix.lower()
    if pnl_ext not in ALLOWED_PNL_EXT:
        return jsonify({"error": "P&L upload must be a .csv, .xlsx, or .xlsm file."}), 400

    scorecard_file = request.files.get("scorecard_file")
    scorecard_name = ""
    if scorecard_file and scorecard_file.filename:
        scorecard_name = secure_filename(scorecard_file.filename)
        if Path(scorecard_name).suffix.lower() not in ALLOWED_SCORECARD_EXT:
            return jsonify({"error": "Scorecard upload must be an .xlsx or .xlsm file."}), 400

    token = secrets.token_urlsafe(16)
    upload_dir = _upload_dir()
    pnl_path = upload_dir / f"{token}_pnl{pnl_ext}"
    scorecard_path = None

    try:
        pnl_file.save(str(pnl_path))
        if scorecard_file and scorecard_name:
            scorecard_path = upload_dir / f"{token}_scorecard{Path(scorecard_name).suffix.lower()}"
            scorecard_file.save(str(scorecard_path))
    except Exception as exc:
        _delete_token_files(token)
        return jsonify({"error": f"Could not save upload: {exc}"}), 500

    try:
        record = process_scorecard(token, pnl_path, pnl_name, scorecard_path, scorecard_name)
    except ValueError as exc:
        _delete_token_files(token)
        return jsonify({"error": str(exc)}), 422
    except Exception as exc:
        _delete_token_files(token)
        return jsonify({"error": f"Processing failed: {exc}"}), 500

    pending = session.get("pending_scorecard_downloads", {})
    if len(pending) >= MAX_PENDING:
        oldest = next(iter(pending))
        _delete_token_files(oldest)
        del pending[oldest]
    pending[token] = record["download_names"]
    session["pending_scorecard_downloads"] = pending
    session.modified = True

    return jsonify({"token": token, "original_name": pnl_name, "analysis": build_payload(record)})


@scorecard_bp.route("/analysis/<token>", methods=["POST"])
@login_required
def analysis(token):
    _assert_pending_token(token)
    record = _load_record(token)
    payload = request.get_json(silent=True) or {}
    selected_months = payload.get("months")
    return jsonify({"analysis": build_payload(record, selected_months)})


@scorecard_bp.route("/analysis/<token>/chart/<chart_name>", methods=["POST"])
@login_required
def large_chart(token, chart_name):
    """Renders one chart fresh at LARGE_CHART_SCALE for the click-to-
    enlarge modal -- a real higher-resolution regeneration (bigger figure,
    same DPI, so more actual pixels), not a CSS stretch of the small
    dashboard image, which is why this is its own endpoint rather than
    bundling a second full set of chart images into every /analysis
    response most page loads never need."""
    _assert_pending_token(token)
    builder = _LARGE_CHART_BUILDERS.get(chart_name)
    if builder is None:
        abort(404)

    record = _load_record(token)
    kpis = record["kpis"]
    months = list(kpis["income"].keys())
    payload = request.get_json(silent=True) or {}
    selected_months = payload.get("months")
    selected = [month for month in (selected_months or months) if month in months]
    if not selected:
        selected = months

    df_full = build_kpi_dataframe(kpis)
    df_filtered = df_full[df_full["Month"].isin(selected)]
    if df_filtered.empty:
        return jsonify({"chart": None})
    return jsonify({"chart": builder(df_filtered, scale=LARGE_CHART_SCALE)})


@scorecard_bp.route("/download/<token>/<kind>")
@login_required
def download(token, kind):
    _assert_pending_token(token)
    record = _load_record(token)
    files = record.get("files", {})
    download_names = record.get("download_names", {})
    if kind not in files or kind not in download_names:
        abort(404)

    file_path = _upload_dir() / files[kind]
    if not file_path.exists():
        abort(404)

    return send_file(
        io.BytesIO(file_path.read_bytes()),
        as_attachment=True,
        download_name=download_names[kind],
        mimetype=_mimetype_for_kind(kind, file_path),
    )
