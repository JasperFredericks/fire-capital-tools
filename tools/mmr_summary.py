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
extract_placeholder_box_score = _gs.extract_placeholder_box_score
parse_maple              = _gs.parse_maple
make_download_filename   = _gs.make_download_filename

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

    if source_system == "Resman":
        required = {
            "Box Score", "Delinquency", "Rent Roll",
            "Available Units", "Expiring Leases",
            "Prospect Source Summary", "Work Order Summary",
        }
        missing = required - set(wb.sheetnames)
        if missing:
            raise ValueError(f"Missing required tabs: {', '.join(sorted(missing))}")

        bs = parse_box_score(wb["Box Score"])
        dl = parse_delinquency(wb["Delinquency"])
        rr = parse_rent_roll(wb["Rent Roll"], bs["occupied"])
        au = parse_available_units(wb["Available Units"])
        el = parse_expiring_leases(wb["Expiring Leases"], bs["date_range"])
        ps = parse_prospect_sources(wb["Prospect Source Summary"])
        wo = parse_work_orders(wb["Work Order Summary"])
    elif source_system == "Placeholder(Maple)":
        maple = parse_maple(wb)
        bs = maple["box_score"]
        dl = maple["delinquency"]
        rr = maple["rent_roll"]
        au = maple["available_units"]
        el = maple["expiring_leases"]
        ps = maple["prospect_sources"]
        wo = maple["work_orders"]
    else:
        bs = extract_placeholder_box_score(wb, source_system)
        dl = {"total": 0.0}
        rr = {"total_rental": 0.0, "avg_rent": 0.0}
        au = {"ready_units": [], "prelease_count": 0}
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

    total_units  = bs["total_units"]
    total_rental = round(float(rr["total_rental"]), 2)

    # Return real stats for both Resman and Maple Valley; show dashes only for
    # truly unrecognized formats where we have no data.
    has_stats = source_system in ("Resman", "Placeholder(Maple)")

    return {
        "property_name":          bs["property_name"],
        "report_period":          bs["date_range"],
        "source_system":          source_system,
        "total_units":            total_units,
        "occupied_units":         bs["occupied"],
        "physical_occupancy_pct": round(bs["pct_occ"] * 100, 1) if has_stats else None,
        "delinquency_total":      round(float(dl["total"]), 2) if has_stats else None,
        "delinquency_count":      dl.get("count"),
        "total_rental_revenue":   total_rental if has_stats else None,
        "revenue_per_unit":       round(total_rental / total_units, 2) if has_stats and total_units else None,
        "avg_rent_per_unit":      round(float(rr["avg_rent"]), 2) if has_stats else None,
        "ready_units":            len(au["ready_units"]) if has_stats else None,
        "emergency_wo_count":     len(wo["work_orders"]) if has_stats else None,
        "download_name":          make_download_filename(bs["property_name"], bs["date_range"]),
    }

# ── Upload folder helpers ──────────────────────────────────────────────────

def _upload_dir() -> Path:
    return Path(current_app.config["UPLOAD_FOLDER"])


def _cleanup_old_uploads(max_age: int = 3600) -> None:
    """Delete orphaned upload files older than max_age seconds."""
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

    original_name: str = pending.pop(token)
    session["pending_downloads"] = pending
    session.modified = True

    file_path = _upload_dir() / f"{token}.xlsx"
    if not file_path.exists():
        abort(404)

    # Read into memory then delete from disk
    data = file_path.read_bytes()
    file_path.unlink(missing_ok=True)

    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=original_name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
