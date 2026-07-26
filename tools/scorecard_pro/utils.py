from __future__ import annotations

import datetime
import json
import math
import re
import time
from pathlib import Path

import pandas as pd
from flask import abort, current_app, session

from tools.scorecard_pro.constants import MONTH_INDEX


def summarize_dataframe(df, kpis):
    if df.empty:
        return {
            "total_income": 0.0,
            "total_expenses": 0.0,
            "total_noi": 0.0,
            "avg_occupancy": None,
            "avg_expense_ratio": None,
            "missing_gpr_months": [],
            "zero_occupancy_months": [],
            "expense_fallback_codes": kpis.get("expense_fallback_codes", []),
        }
    occ_values = df["Occupancy"].dropna()
    ratio_values = df["ExpenseRatio"].dropna()
    return {
        "total_income": float(df["Income"].sum()),
        "total_expenses": float(df["Expenses"].sum()),
        "total_noi": float(df["NOI"].sum()),
        "avg_occupancy": float(occ_values.mean()) if not occ_values.empty else None,
        "avg_expense_ratio": float(ratio_values.mean()) if not ratio_values.empty else None,
        "missing_gpr_months": df.loc[df["OccupancyStatus"] == "missing_gpr", "Month"].tolist(),
        "zero_occupancy_months": df.loc[df["OccupancyStatus"] == "zero", "Month"].tolist(),
        "expense_fallback_codes": kpis.get("expense_fallback_codes", []),
    }


def noi_variance_flag(variance, target):
    """Red beyond +/-10% of budget, green within +/-3%, otherwise unflagged."""
    if not target:
        return None
    pct = variance / abs(target)
    if abs(pct) > 0.10:
        return "red"
    if abs(pct) <= 0.03:
        return "green"
    return None


def _records_for_json(df):
    return df.where(pd.notna(df), None).to_dict(orient="records")


def month_sort_key(month_str):
    try:
        parts = str(month_str).split()
        if len(parts) == 2:
            mon = parts[0]
            year = int(parts[1])
            return datetime.date(year, MONTH_INDEX[mon], 1)
    except Exception:
        pass
    return datetime.date(1900, 1, 1)


def quarter_key(month_str):
    try:
        parts = str(month_str).split()
        if len(parts) != 2:
            return None
        mon = parts[0]
        year = int(parts[1])
        quarter = ((MONTH_INDEX[mon] - 1) // 3) + 1
        return (year, quarter)
    except Exception:
        return None


def format_currency(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"${float(value):,.0f}"


def format_percent(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/A"
    return f"{float(value) * 100:.1f}%"


def money_axis(value):
    value = float(value)
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1_000_000:
        return f"{sign}${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{sign}${value / 1_000:.0f}K"
    return f"{sign}${value:.0f}"


def money_label(value):
    """Full-precision, comma-formatted dollar figure for on-chart data
    labels -- unlike money_axis's K/M abbreviation (meant for axis ticks),
    this matches the exact formatting already used for dollar figures
    elsewhere in the tool (report text, dashboard summary cards)."""
    value = float(value)
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}"


def slugify(value):
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip()).strip("_").lower()
    return slug or "property"


def clean_for_json(value):
    if isinstance(value, dict):
        return {key: clean_for_json(val) for key, val in value.items()}
    if isinstance(value, list):
        return [clean_for_json(item) for item in value]
    if isinstance(value, tuple):
        return [clean_for_json(item) for item in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _upload_dir():
    path = Path(current_app.config["UPLOAD_FOLDER"]) / "scorecard-pro"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _record_path(token):
    return _upload_dir() / f"{token}_analysis.json"


def _save_record(token, record):
    _record_path(token).write_text(json.dumps(clean_for_json(record), ensure_ascii=False), encoding="utf-8")


def _load_record(token):
    path = _record_path(token)
    if not path.exists():
        abort(404)
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_pending_token(token):
    pending = session.get("pending_scorecard_downloads", {})
    if token not in pending:
        abort(403)


def _cleanup_old_uploads(max_age=None):
    if max_age is None:
        max_age = int(current_app.permanent_session_lifetime.total_seconds())
    cutoff = time.time() - max_age
    for file_path in _upload_dir().glob("*"):
        if not file_path.is_file():
            continue
        try:
            if file_path.stat().st_mtime < cutoff:
                file_path.unlink(missing_ok=True)
        except OSError:
            pass


def _delete_token_files(token):
    for file_path in _upload_dir().glob(f"{token}_*"):
        if not file_path.is_file():
            continue
        try:
            file_path.unlink(missing_ok=True)
        except OSError:
            pass


def _mimetype_for_kind(kind, file_path):
    if kind == "pdf":
        return "application/pdf"
    if kind == "csv":
        return "text/csv"
    if kind == "report":
        return "text/plain"
    if file_path.suffix.lower() == ".xlsm":
        return "application/vnd.ms-excel.sheet.macroEnabled.12"
    return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
