from __future__ import annotations

import datetime
from pathlib import Path

import pandas as pd

from tools import scorecard_history
from tools.scorecard_pro.charts import build_charts
from tools.scorecard_pro.exports import (
    create_pdf_report,
    write_export_xlsx,
    write_kpi_csv,
)
from tools.scorecard_pro.kpis import (
    KPICalculator,
    ReportGenerator,
    generate_advanced_insights,
)
from tools.scorecard_pro.parsing import PnLParser, ScorecardTargetParser
from tools.scorecard_pro.updater import ScorecardUpdater
from tools.scorecard_pro.utils import (
    _records_for_json,
    _save_record,
    _upload_dir,
    clean_for_json,
    month_sort_key,
    noi_variance_flag,
    quarter_key,
    slugify,
    summarize_dataframe,
)


def _history_months_from_kpis(kpis):
    """Build the list of per-month dicts scorecard_history.upsert_months()
    expects, using this module's own month-label parsing (month_sort_key)
    so the standalone history module stays generic and doesn't need to
    know anything about "Mon YYYY"-style labels."""
    months = []
    for month in kpis["income"]:
        months.append(
            {
                "month": month,
                "month_start": month_sort_key(month).isoformat(),
                "income": kpis["income"].get(month),
                "expenses": kpis["expenses"].get(month),
                "noi": kpis["noi"].get(month),
                "occupancy": kpis["physical_occupancy"].get(month),
                "expense_ratio": kpis["expense_ratio"].get(month),
            }
        )
    return months


def _pct_change(current, previous):
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous)


def save_history_and_build_comparison(pnl_data, kpis):
    """Save this upload's monthly KPIs to the property's history, and
    compare its most recent month against whatever was on file for this
    property before this save (across all prior uploads, not just the
    prior upload's own trailing window). Never raises -- a history/DB
    hiccup should not break the upload itself."""
    property_name = pnl_data.get("property") or "Unknown Property"
    months = _history_months_from_kpis(kpis)
    if not months:
        return [], None, None

    current_month = max(months, key=lambda m: m["month_start"])
    uploaded_at = datetime.datetime.utcnow().isoformat()

    try:
        with scorecard_history.get_connection() as conn:
            property_key = scorecard_history.normalize_property_key(property_name)
            previous_latest = scorecard_history.get_latest(conn, property_key)
            scorecard_history.upsert_months(conn, property_name, months, uploaded_at)
            full_history = scorecard_history.get_history(conn, property_key)
    except Exception as exc:
        return [], None, f"Scorecard history: could not save/compare this upload ({exc})."

    comparison = {"available": False}
    if previous_latest:
        comparison = {
            "available": True,
            "previous_month": previous_latest["month"],
            "previous_uploaded_at": previous_latest["uploaded_at"],
            "current_month": current_month["month"],
            "metrics": {
                "noi": {
                    "previous": previous_latest["noi"],
                    "current": current_month["noi"],
                    "pct_change": _pct_change(current_month["noi"], previous_latest["noi"]),
                },
                "occupancy": {
                    "previous": previous_latest["occupancy"],
                    "current": current_month["occupancy"],
                    "point_change": (
                        None
                        if current_month["occupancy"] is None or previous_latest["occupancy"] is None
                        else current_month["occupancy"] - previous_latest["occupancy"]
                    ),
                },
                "expense_ratio": {
                    "previous": previous_latest["expense_ratio"],
                    "current": current_month["expense_ratio"],
                    "point_change": (
                        None
                        if current_month["expense_ratio"] is None or previous_latest["expense_ratio"] is None
                        else current_month["expense_ratio"] - previous_latest["expense_ratio"]
                    ),
                },
            },
        }
        noi_pct = comparison["metrics"]["noi"]["pct_change"]
        if noi_pct is not None:
            direction = "up" if noi_pct >= 0 else "down"
            comparison["summary_text"] = (
                f"NOI {direction} {abs(noi_pct):.1%} since your last upload "
                f"({previous_latest['month']} -> {current_month['month']})."
            )
        else:
            comparison["summary_text"] = (
                f"Compared against your last upload ({previous_latest['month']}), "
                f"but NOI wasn't available for one of the two months."
            )
    return full_history, comparison, None


# Names that identify no actual property. "Unknown Property" is the parser's
# initial state; "Property" was the old cash-flow fallback and is listed so
# any file still carrying it is caught rather than trusted.
_PLACEHOLDER_PROPERTY_NAMES = {"", "unknown property", "property", "unknown"}


def _has_real_property_name(name) -> bool:
    return " ".join(str(name or "").strip().lower().split()) not in _PLACEHOLDER_PROPERTY_NAMES


def process_scorecard(token, pnl_path, pnl_name, scorecard_path=None, scorecard_name=""):
    parser = PnLParser(pnl_path)
    parser.parse()
    pnl_data = parser.get_data()
    if not pnl_data["accounts"]:
        raise ValueError("No recognizable accounts were parsed from the P&L CSV.")

    # Refuse to proceed without a real property identity rather than filing
    # the upload under a placeholder. The history table is keyed on
    # (property_key, month), so a placeholder name is not a cosmetic problem:
    # two different properties sharing one would overwrite each other's
    # months silently, and the trend comparison would measure one building
    # against another. Better to stop and ask than to guess.
    if not _has_real_property_name(parser.property_name):
        raise ValueError(
            "Could not identify which property this file is for — the export "
            "has no recognizable property name in its header. Re-export it with "
            "a single property selected, or rename the file's 'Properties:' line, "
            "so the trend history is filed against the right property."
        )

    calc = KPICalculator(pnl_data)
    kpis = calc.calculate()
    report_text = ReportGenerator(kpis).generate()

    targets = {"UW": {}, "PM": {}}
    target_diagnostics = None
    update_diagnostics = None
    files = {}

    if scorecard_path:
        target_parser = ScorecardTargetParser(scorecard_path)
        target_parser.parse()
        targets = target_parser.get_data()
        target_diagnostics = target_parser.get_diagnostics()

        updated_ext = Path(scorecard_path).suffix.lower()
        updated_path = _upload_dir() / f"{token}_updated_scorecard{updated_ext}"
        updater = ScorecardUpdater(scorecard_path, pnl_data)
        updated = updater.update(updated_path)
        update_diagnostics = updater.get_diagnostics()
        if updated:
            files["scorecard"] = updated_path.name

    full_df = build_kpi_dataframe(kpis)
    property_slug = slugify(pnl_data.get("property") or Path(pnl_name).stem or "property")
    base_name = f"{property_slug}_scorecard"

    report_path = _upload_dir() / f"{token}_report.txt"
    report_path.write_text(report_text, encoding="utf-8")
    files["report"] = report_path.name

    csv_path = _upload_dir() / f"{token}_kpi_data.csv"
    write_kpi_csv(csv_path, full_df)
    files["csv"] = csv_path.name

    xlsx_path = _upload_dir() / f"{token}_scorecard_data.xlsx"
    write_export_xlsx(xlsx_path, full_df, pnl_data["accounts"], targets)
    files["xlsx"] = xlsx_path.name

    pdf_path = _upload_dir() / f"{token}_scorecard_report.pdf"
    create_pdf_report(pdf_path, pnl_data, kpis, targets, generate_advanced_insights(full_df, pnl_data["accounts"], targets), full_df)
    files["pdf"] = pdf_path.name

    download_names = {
        "report": f"{base_name}_report.txt",
        "csv": f"{base_name}_kpi_data.csv",
        "xlsx": f"{base_name}_data.xlsx",
        "pdf": f"{base_name}_report.pdf",
    }
    if "scorecard" in files:
        ext = Path(scorecard_name or files["scorecard"]).suffix.lower() or ".xlsx"
        download_names["scorecard"] = f"{base_name}_updated{ext}"

    history_trend, history_comparison, history_error = save_history_and_build_comparison(pnl_data, kpis)

    warnings = list(pnl_data.get("meta", {}).get("warnings", []))
    if target_diagnostics:
        warnings.extend(target_diagnostics.get("warnings", []))
    if update_diagnostics:
        warnings.extend(update_diagnostics.get("warnings", []))
    if history_error:
        warnings.append(history_error)

    record = {
        "token": token,
        "pnl_name": pnl_name,
        "scorecard_name": scorecard_name,
        "pnl_data": pnl_data,
        "kpis": kpis,
        "targets": targets,
        "target_diagnostics": target_diagnostics,
        "update_diagnostics": update_diagnostics,
        "report_text": report_text,
        "files": files,
        "download_names": download_names,
        "warnings": warnings,
        "history_trend": history_trend,
        "history_comparison": history_comparison,
        "created_at": datetime.datetime.utcnow().isoformat(),
    }
    _save_record(token, record)
    return record


def build_payload(record, selected_months=None):
    pnl_data = record["pnl_data"]
    kpis = record["kpis"]
    months = list(kpis["income"].keys())
    selected = [month for month in (selected_months or months) if month in months]
    if not selected:
        selected = months

    df_full = build_kpi_dataframe(kpis)
    df_filtered = df_full[df_full["Month"].isin(selected)]
    targets = record.get("targets") or {"UW": {}, "PM": {}}
    insights = generate_advanced_insights(df_filtered, pnl_data["accounts"], targets)
    comparison_rows = build_comparison_rows(kpis, targets, selected)

    payload = {
        "property": pnl_data.get("property", "Property"),
        "period": pnl_data.get("period", "Period"),
        "format": pnl_data.get("meta", {}).get("format", "Unknown"),
        "warnings": record.get("warnings", []),
        "months": months,
        "selected_months": selected,
        "summary": summarize_dataframe(df_filtered, kpis),
        "latest_quarter": latest_full_quarter(df_full),
        "kpi_rows": _records_for_json(df_filtered),
        "accounts": build_account_payload(pnl_data["accounts"], selected),
        "comparison": comparison_rows,
        "targets": targets,
        "target_diagnostics": record.get("target_diagnostics"),
        "update_diagnostics": record.get("update_diagnostics"),
        "insights": insights,
        "report_text": record.get("report_text", ""),
        "downloads": sorted(record.get("download_names", {}).keys()),
        "charts": build_charts(df_filtered),
        "history": {
            "trend": record.get("history_trend") or [],
            "comparison": record.get("history_comparison"),
        },
    }
    return clean_for_json(payload)


def build_kpi_dataframe(kpis):
    months = list(kpis["income"].keys())
    return pd.DataFrame(
        {
            "Month": months,
            "Income": [kpis["income"].get(month, 0.0) for month in months],
            "Expenses": [kpis["expenses"].get(month, 0.0) for month in months],
            "NOI": [kpis["noi"].get(month, 0.0) for month in months],
            "Occupancy": [kpis["physical_occupancy"].get(month) for month in months],
            "EconomicOccupancy": [kpis["economic_occupancy"].get(month) for month in months],
            "ExpenseRatio": [kpis["expense_ratio"].get(month) for month in months],
            "NOIMargin": [kpis["noi_margin"].get(month) for month in months],
            "OccupancyStatus": [kpis["occupancy_status"].get(month, "ok") for month in months],
        }
    )


def latest_full_quarter(df):
    if df.empty:
        return None

    working = df.copy()
    working["QuarterKey"] = working["Month"].apply(quarter_key)
    working = working[working["QuarterKey"].notna()]
    if working.empty:
        return None

    counts = working.groupby("QuarterKey")["Month"].count()
    complete = [key for key, count in counts.items() if count == 3]
    if not complete:
        return None

    latest_key = sorted(complete)[-1]
    q_data = working[working["QuarterKey"] == latest_key]
    occ_values = q_data["Occupancy"].dropna()
    return {
        "label": f"Q{latest_key[1]} {latest_key[0]}",
        "income": float(q_data["Income"].sum()),
        "expenses": float(q_data["Expenses"].sum()),
        "noi": float(q_data["NOI"].sum()),
        "occupancy": float(occ_values.mean()) if not occ_values.empty else None,
    }


def build_account_payload(accounts, selected_months):
    rows = []
    for code in sorted(accounts):
        data = accounts[code]
        monthly = [{"month": month, "amount": float(data["data"].get(month, 0.0) or 0.0)} for month in selected_months]
        amounts = [item["amount"] for item in monthly]
        rows.append(
            {
                "code": code,
                "name": data.get("name", code),
                "total": float(sum(amounts)),
                "average": float(sum(amounts) / len(amounts)) if amounts else 0.0,
                "monthly": monthly,
            }
        )
    return rows


def build_comparison_rows(kpis, targets, selected_months):
    rows = []
    for month in selected_months:
        for metric, key in (("Revenue", "income"), ("Expenses", "expenses"), ("NOI", "noi")):
            target_key = "Income" if metric == "Revenue" else metric
            actual = float(kpis[key].get(month, 0.0) or 0.0)
            uw = float((targets.get("UW") or {}).get(target_key, 0.0) or 0.0)
            pm = float((targets.get("PM") or {}).get(target_key, 0.0) or 0.0)
            variance_uw = actual - uw
            variance_pm = actual - pm
            row = {
                "month": month,
                "metric": metric,
                "actual": actual,
                "uw": uw,
                "pm": pm,
                "variance_uw": variance_uw,
                "variance_pm": variance_pm,
            }
            # Flags are scoped to NOI (the metric Michelle asked to have
            # flagged against budget) — Revenue/Expenses variance is shown
            # but intentionally left unflagged here.
            if metric == "NOI":
                row["flag_uw"] = noi_variance_flag(variance_uw, uw)
                row["flag_pm"] = noi_variance_flag(variance_pm, pm)
            rows.append(row)
    return rows
