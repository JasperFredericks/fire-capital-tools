"""
FIRE Capital Tools — MMR Summary Tool
Flask Blueprint + processing logic.

The parsing and sheet-building functions are loaded from the standalone CLI
script (mmr-summary/generate_summary.py) via importlib so there is a single
source of truth for the algorithm.
"""

from __future__ import annotations

import importlib.util
import io
import os
import secrets
import time
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
    send_file,
    session,
)
from flask_login import login_required
from werkzeug.utils import secure_filename

# ── Load the standalone generate_summary module ────────────────────────────

def _load_gs():
    path = Path(__file__).parent.parent / "mmr-summary" / "generate_summary.py"
    spec = importlib.util.spec_from_file_location("_gs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_gs = _load_gs()

# Re-export the functions we use so they're type-checkable
parse_box_score       = _gs.parse_box_score
parse_delinquency     = _gs.parse_delinquency
parse_rent_roll       = _gs.parse_rent_roll
parse_available_units = _gs.parse_available_units
parse_expiring_leases = _gs.parse_expiring_leases
parse_prospect_sources = _gs.parse_prospect_sources
parse_work_orders     = _gs.parse_work_orders
build_summary         = _gs.build_summary
detect_source_system  = _gs.detect_source_system
default_box_score     = _gs.default_box_score
extract_appfolio_box_score = _gs.extract_appfolio_box_score
parse_appfolio           = _gs.parse_appfolio
make_download_filename   = _gs.make_download_filename
sheet_by_name            = _gs.sheet_by_name

import openpyxl  # already required by generate_summary

# ── Core processing ────────────────────────────────────────────────────────

def process_mmr(filepath: Path) -> dict:
    """
    Open an MMR workbook, add a Summary sheet, save in-place, and return
    key stats as a plain dict for JSON serialisation.
    """
    wb = openpyxl.load_workbook(str(filepath), data_only=True)
    source_system = detect_source_system(wb)
    if source_system == "Unrecognized Format":
        print("WARNING: Workbook format is not recognized. Summary will contain placeholder values.")

    missing_sheets: list[str] = []

    def parse_resman_section(sheet_name: str, parser, default_value, *args):
        ws = sheet_by_name(wb, sheet_name)
        if ws is None:
            print(f"WARNING: Missing tab '{sheet_name}' - using blank defaults.")
            missing_sheets.append(sheet_name)
            return default_value
        try:
            return parser(ws, *args)
        except Exception as exc:
            print(f"WARNING: Could not parse tab '{sheet_name}' ({exc}) - using blank defaults.")
            missing_sheets.append(f"{sheet_name} (parse failed)")
            return default_value

    if source_system == "Resman":
        bs = parse_resman_section("Box Score", parse_box_score, default_box_score())
        dl = parse_resman_section("Delinquency", parse_delinquency, {"total": None, "count": None})
        rr = parse_resman_section("Rent Roll", parse_rent_roll, {"total_rental": None, "avg_rent": None}, bs["occupied"])
        au = parse_resman_section("Available Units", parse_available_units, {"ready_units": None, "prelease_count": None, "holding_count": None, "eviction_count": None})
        el = parse_resman_section("Expiring Leases", parse_expiring_leases, None, bs["date_range"])
        ps = parse_resman_section("Prospect Source Summary", parse_prospect_sources, None)
        wo = parse_resman_section("Work Order Summary", parse_work_orders, {"work_orders": None, "issue_counts": {}})
    elif source_system == "Appfolio":
        appfolio = parse_appfolio(wb)
        bs = appfolio["box_score"]
        dl = appfolio["delinquency"]
        rr = appfolio["rent_roll"]
        au = appfolio["available_units"]
        el = appfolio["expiring_leases"]
        ps = appfolio["prospect_sources"]
        wo = appfolio["work_orders"]
    else:
        bs = extract_appfolio_box_score(wb, source_system)
        dl = {"total": 0.0}
        rr = {"total_rental": 0.0, "avg_rent": 0.0}
        au = {"ready_units": [], "prelease_count": 0, "holding_count": 0, "eviction_count": 0}
        el = []
        ps = {}
        wo = {"work_orders": [], "issue_counts": {}}

    data = {
        "box_score":        bs,
        "delinquency":      dl,
        "rent_roll":        rr,
        "available_units":  au,
        "expiring_leases":  el,
        "prospect_sources": ps,
        "work_orders":      wo,
        "source_system":    source_system,
    }

    build_summary(wb, data)
    wb.save(str(filepath))

    def round_number(value, digits=2):
        try:
            return round(float(value), digits)
        except (TypeError, ValueError):
            return None

    total_units  = bs["total_units"]
    total_rental = round_number(rr.get("total_rental"))
    avg_rent = round_number(rr.get("avg_rent"))
    delinquency_total = round_number(dl.get("total"))
    ready_units = au.get("ready_units")
    work_orders = wo.get("work_orders")

    # Return real stats for both Resman and Appfolio; show dashes only for
    # truly unrecognized formats where we have no data.
    has_stats = source_system in ("Resman", "Appfolio")

    return {
        "property_name":          bs["property_name"],
        "report_period":          bs["date_range"],
        "source_system":          source_system,
        "total_units":            total_units,
        "occupied_units":         bs["occupied"],
        "physical_occupancy_pct": round(bs["pct_occ"] * 100, 1) if has_stats else None,
        "delinquency_total":      delinquency_total if has_stats else None,
        "delinquency_count":      dl.get("count"),
        "total_rental_revenue":   total_rental if has_stats else None,
        "revenue_per_unit":       round(total_rental / total_units, 2) if has_stats and total_units and total_rental is not None else None,
        "avg_rent_per_unit":      avg_rent if has_stats else None,
        "ready_units":            len(ready_units) if has_stats and ready_units is not None else None,
        "emergency_wo_count":     len(work_orders) if has_stats and work_orders is not None else None,
        "missing_sheets":         missing_sheets,
        "download_name":          make_download_filename(bs["property_name"], bs["date_range"], bs.get("printed", "")),
    }

# ── Upload folder helpers ──────────────────────────────────────────────────

def _upload_dir() -> Path:
    return Path(current_app.config["UPLOAD_FOLDER"])


def _cleanup_old_uploads(max_age: int | None = None) -> None:
    """Delete orphaned upload files older than max_age seconds."""
    if max_age is None:
        max_age = int(current_app.permanent_session_lifetime.total_seconds())
    cutoff = time.time() - max_age
    for f in _upload_dir().glob("*.xlsx"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
        except OSError:
            pass

# ── Blueprint ──────────────────────────────────────────────────────────────

mmr_bp = Blueprint("mmr", __name__)

ALLOWED_EXT = {".xlsx"}
MAX_PENDING = 10   # max simultaneous downloads per session


@mmr_bp.route("/")
@login_required
def index():
    return render_template("tools/mmr_summary.html")


@mmr_bp.route("/upload", methods=["POST"])
@login_required
def upload():
    _cleanup_old_uploads()

    if "file" not in request.files:
        return jsonify({"error": "No file included in the request."}), 400

    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"error": "No file selected."}), 400

    original_name = secure_filename(file.filename)
    if Path(original_name).suffix.lower() not in ALLOWED_EXT:
        return jsonify({"error": "Only .xlsx files are accepted."}), 400

    # Save to uploads/ under a random name
    token = secrets.token_urlsafe(16)
    save_path = _upload_dir() / f"{token}.xlsx"

    try:
        file.save(str(save_path))
    except Exception as exc:
        return jsonify({"error": f"Could not save file: {exc}"}), 500

    # Process
    try:
        stats = process_mmr(save_path)
    except ValueError as exc:
        save_path.unlink(missing_ok=True)
        return jsonify({"error": str(exc)}), 422
    except Exception as exc:
        save_path.unlink(missing_ok=True)
        return jsonify({"error": f"Processing failed: {exc}"}), 500

    # Store token in session so only this user can download it
    pending: dict = session.get("pending_downloads", {})
    if len(pending) >= MAX_PENDING:
        # Evict the oldest token (first inserted)
        oldest = next(iter(pending))
        (save_path.parent / f"{oldest}.xlsx").unlink(missing_ok=True)
        del pending[oldest]
    pending[token] = stats.get("download_name") or original_name
    session["pending_downloads"] = pending
    session.modified = True

    return jsonify({"token": token, "original_name": original_name, "stats": stats})


@mmr_bp.route("/download/<token>")
@login_required
def download(token: str):
    pending: dict = session.get("pending_downloads", {})
    if token not in pending:
        abort(403)

    original_name: str = pending[token]
    session["pending_downloads"] = pending
    session.modified = True

    file_path = _upload_dir() / f"{token}.xlsx"
    if not file_path.exists():
        abort(404)

    # Keep the file available for repeat downloads during the session.
    data = file_path.read_bytes()

    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=original_name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
