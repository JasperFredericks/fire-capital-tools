from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .city_search import build_city_aliases, make_search_key, normalize_city_tokens, normalize_query, normalize_state_token


INDEX_VERSION = 1
POPULATION_THRESHOLD = 100_000


FIELD_PATTERNS = {
    "city": [["city"], ["place", "name"]],
    "state": [["state"], ["state", "abbr"], ["state", "code"]],
    "population_current": [["population", "2025"], ["population", "latest"], ["population", "current"]],
    "population_rank": [["rank", "population"], ["rank", "2025"], ["rank"]],
    "population_growth_2020_2025": [["population", "growth", "2020", "2025"], ["population", "change", "2020", "2025"]],
    "population_growth_recent": [["population", "growth", "recent"], ["population", "change", "2024", "2025"], ["population", "change", "latest"]],
    "median_income_current": [["median", "income", "current"], ["median", "household", "income", "2024"], ["median", "income", "latest"]],
    "median_income_growth_2021_2024": [["median", "income", "growth", "2021", "2024"], ["income", "change", "2021", "2024"]],
    "median_income_growth_recent": [["median", "income", "growth", "recent"], ["income", "change", "latest"], ["income", "change", "2023", "2024"]],
    "median_home_value_current": [["home", "value", "current"], ["owner", "occupied", "home", "value", "latest"], ["median", "home", "value", "2024"]],
    "median_home_value_growth_2021_2024": [["home", "value", "growth", "2021", "2024"], ["home", "value", "change", "2021", "2024"]],
    "median_home_value_growth_recent": [["home", "value", "growth", "recent"], ["home", "value", "change", "latest"]],
    "employment_current": [["employment", "current"], ["resident", "employment", "latest"], ["employment", "2025"]],
    "employment_growth_2021_2025": [["employment", "growth", "2021", "2025"], ["employment", "change", "2021", "2025"]],
    "employment_growth_recent": [["employment", "growth", "recent"], ["employment", "change", "latest"], ["employment", "change", "2024", "2025"]],
    "climate_risk_score": [["climate", "risk", "score"], ["fema", "risk", "score"], ["nri", "score"]],
    "climate_risk_rating": [["climate", "risk", "rating"], ["fema", "risk", "rating"], ["nri", "rating"]],
    "landlord_friendliness_score": [["landlord", "friendliness", "score"], ["landlord", "score"]],
    "landlord_friendliness_label": [["landlord", "friendliness", "label"], ["landlord", "label"]],
    "crime_index_score": [["crime", "index", "score"], ["crime", "score"]],
    "crime_rating": [["crime", "rating"]],
    "density_adjusted_crime_score": [["density", "adjusted", "crime", "score"], ["density", "crime", "score"]],
    "density_adjusted_crime_rating": [["density", "adjusted", "crime", "rating"], ["density", "crime", "rating"]],
    "crime_manual_review": [["crime", "manual", "review"], ["manual", "review"]],
    "include_flag": [["include"], ["included"], ["threshold", "include"]],
    "threshold_reason": [["threshold", "reason"], ["exclusion", "reason"]],
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_header(header: Any) -> str:
    text = str(header or "").strip().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _pattern_match(column: str, pattern_tokens: list[str]) -> bool:
    return all(token in column for token in pattern_tokens)


def _find_column(columns: list[str], patterns: list[list[str]]) -> str | None:
    for pattern in patterns:
        for col in columns:
            if _pattern_match(col, pattern):
                return col
    return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if pd.isna(value):
            return None
        return float(value)

    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "").replace("$", "").replace("%", "")
    text = re.sub(r"[^0-9.\-]", "", text)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _as_bool(value: Any) -> bool | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "included"}:
        return True
    if text in {"0", "false", "no", "n", "excluded"}:
        return False
    return None


def _as_str(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def _score_sheet(df: pd.DataFrame) -> int:
    normalized_columns = [_normalize_header(c) for c in df.columns]
    city_col = _find_column(normalized_columns, FIELD_PATTERNS["city"])
    state_col = _find_column(normalized_columns, FIELD_PATTERNS["state"])
    if not city_col or not state_col:
        return -1

    score = 100
    for field, patterns in FIELD_PATTERNS.items():
        if field in {"city", "state"}:
            continue
        if _find_column(normalized_columns, patterns):
            score += 1
    return score


def _best_city_sheet(workbook_path: Path) -> tuple[str, pd.DataFrame]:
    workbook = pd.read_excel(workbook_path, sheet_name=None)
    best_name = ""
    best_df: pd.DataFrame | None = None
    best_score = -1

    for name, df in workbook.items():
        if df is None or df.empty:
            continue
        score = _score_sheet(df)
        if score > best_score:
            best_score = score
            best_name = str(name)
            best_df = df

    if best_df is None:
        raise ValueError("Could not find a worksheet with city/state columns for search indexing.")

    return best_name, best_df


def _resolve_column_map(df: pd.DataFrame) -> dict[str, str]:
    original = { _normalize_header(c): str(c) for c in df.columns }
    normalized_columns = list(original.keys())

    mapping: dict[str, str] = {}
    for field, patterns in FIELD_PATTERNS.items():
        column = _find_column(normalized_columns, patterns)
        if column:
            mapping[field] = original[column]
    return mapping


def _safe_pct_change(old: float | None, new: float | None) -> float | None:
    if old is None or new is None or old == 0:
        return None
    return ((new - old) / old) * 100.0


def _find_year_column(df: pd.DataFrame, base_term: str, year: int) -> str | None:
    target_terms = [base_term, str(year)]
    for col in df.columns:
        norm = _normalize_header(col)
        if all(term in norm for term in target_terms):
            return str(col)
    return None


def _build_city_row(record: dict[str, Any], column_map: dict[str, str], sheet_df: pd.DataFrame, source_last_updated: str) -> dict[str, Any] | None:
    city_col = column_map.get("city")
    state_col = column_map.get("state")
    city = _as_str(record.get(city_col)) if city_col else None
    state = _as_str(record.get(state_col)) if state_col else None
    if not city or not state:
        return None

    state_abbr = normalize_state_token(state).upper()
    display_name = f"{city}, {state_abbr}"

    def val(name: str) -> Any:
        col = column_map.get(name)
        return record.get(col) if col else None

    pop_current = _as_float(val("population_current"))
    pop_2020_col = _find_year_column(sheet_df, "population", 2020)
    pop_latest_col = _find_year_column(sheet_df, "population", 2025) or _find_year_column(sheet_df, "population", 2024)
    pop_2020 = _as_float(record.get(pop_2020_col)) if pop_2020_col else None
    pop_latest = _as_float(record.get(pop_latest_col)) if pop_latest_col else pop_current

    income_2021_col = _find_year_column(sheet_df, "income", 2021)
    income_latest_col = _find_year_column(sheet_df, "income", 2024)
    income_2021 = _as_float(record.get(income_2021_col)) if income_2021_col else None
    income_latest = _as_float(record.get(income_latest_col)) if income_latest_col else _as_float(val("median_income_current"))

    home_2021_col = _find_year_column(sheet_df, "home", 2021)
    home_latest_col = _find_year_column(sheet_df, "home", 2024)
    home_2021 = _as_float(record.get(home_2021_col)) if home_2021_col else None
    home_latest = _as_float(record.get(home_latest_col)) if home_latest_col else _as_float(val("median_home_value_current"))

    employment_2021_col = _find_year_column(sheet_df, "employment", 2021)
    employment_latest_col = _find_year_column(sheet_df, "employment", 2025)
    employment_2021 = _as_float(record.get(employment_2021_col)) if employment_2021_col else None
    employment_latest = _as_float(record.get(employment_latest_col)) if employment_latest_col else _as_float(val("employment_current"))

    warnings: list[str] = []
    if pop_current is None and pop_latest is None:
        warnings.append("Population value missing in source workbook row.")

    normalized_city = normalize_city_tokens(city)
    normalized_display = normalize_query(display_name)
    search_keys = sorted(build_city_aliases(city, state_abbr))

    return {
        "city": city,
        "state": state_abbr,
        "display_name": display_name,
        "normalized_city": normalized_city,
        "normalized_display_name": normalized_display,
        "search_key": make_search_key(city, state_abbr),
        "search_keys": search_keys,
        "population_current": pop_current,
        "population_rank": _as_float(val("population_rank")),
        "population_growth_2020_2025": _as_float(val("population_growth_2020_2025")) or _safe_pct_change(pop_2020, pop_latest),
        "population_growth_recent": _as_float(val("population_growth_recent")),
        "median_income_current": _as_float(val("median_income_current")) or income_latest,
        "median_income_growth_2021_2024": _as_float(val("median_income_growth_2021_2024")) or _safe_pct_change(income_2021, income_latest),
        "median_income_growth_recent": _as_float(val("median_income_growth_recent")),
        "median_home_value_current": _as_float(val("median_home_value_current")) or home_latest,
        "median_home_value_growth_2021_2024": _as_float(val("median_home_value_growth_2021_2024")) or _safe_pct_change(home_2021, home_latest),
        "median_home_value_growth_recent": _as_float(val("median_home_value_growth_recent")),
        "employment_current": _as_float(val("employment_current")) or employment_latest,
        "employment_growth_2021_2025": _as_float(val("employment_growth_2021_2025")) or _safe_pct_change(employment_2021, employment_latest),
        "employment_growth_recent": _as_float(val("employment_growth_recent")),
        "climate_risk_score": _as_float(val("climate_risk_score")),
        "climate_risk_rating": _as_str(val("climate_risk_rating")),
        "landlord_friendliness_score": _as_float(val("landlord_friendliness_score")),
        "landlord_friendliness_label": _as_str(val("landlord_friendliness_label")),
        "crime_index_score": _as_float(val("crime_index_score")),
        "crime_rating": _as_str(val("crime_rating")),
        "density_adjusted_crime_score": _as_float(val("density_adjusted_crime_score")),
        "density_adjusted_crime_rating": _as_str(val("density_adjusted_crime_rating")),
        "crime_manual_review": _as_str(val("crime_manual_review")),
        "source_last_updated": source_last_updated,
        "warnings": warnings,
    }


def _included_flag(city_row: dict[str, Any], record: dict[str, Any], column_map: dict[str, str]) -> bool:
    include_col = column_map.get("include_flag")
    if include_col:
        include_value = _as_bool(record.get(include_col))
        if include_value is not None:
            return include_value

    pop = city_row.get("population_current")
    return bool(pop is not None and pop >= POPULATION_THRESHOLD)


def _build_excluded_entry(city_row: dict[str, Any], record: dict[str, Any], column_map: dict[str, str]) -> dict[str, Any]:
    reason_col = column_map.get("threshold_reason")
    reason = _as_str(record.get(reason_col)) if reason_col else None

    return {
        "city": city_row.get("city"),
        "state": city_row.get("state"),
        "latest_population": city_row.get("population_current"),
        "threshold_reason": reason or "Below 100,000 population threshold.",
        "normalized_city": city_row.get("normalized_city"),
        "normalized_key": city_row.get("search_key"),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return default or {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default or {}


def build_indexes_from_workbook(
    workbook_path: Path,
    city_index_path: Path,
    excluded_index_path: Path,
    metadata_path: Path,
) -> dict[str, Any]:
    workbook_path = Path(workbook_path)
    if not workbook_path.exists():
        raise FileNotFoundError(f"Workbook not found: {workbook_path}")

    sheet_name, sheet_df = _best_city_sheet(workbook_path)
    column_map = _resolve_column_map(sheet_df)

    source_last_updated = datetime.fromtimestamp(workbook_path.stat().st_mtime, tz=timezone.utc).isoformat()

    cities: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for _, row in sheet_df.iterrows():
        record = row.to_dict()
        city_row = _build_city_row(record, column_map, sheet_df, source_last_updated)
        if not city_row:
            continue

        if _included_flag(city_row, record, column_map):
            cities.append(city_row)
        else:
            excluded.append(_build_excluded_entry(city_row, record, column_map))

    cities.sort(key=lambda item: (item.get("city", ""), item.get("state", "")))
    excluded.sort(key=lambda item: (item.get("city", ""), item.get("state", "")))

    city_payload = {
        "index_version": INDEX_VERSION,
        "generated_at": _utc_now(),
        "source_workbook": str(workbook_path),
        "source_sheet": sheet_name,
        "source_last_updated": source_last_updated,
        "city_count": len(cities),
        "cities": cities,
    }

    excluded_payload = {
        "index_version": INDEX_VERSION,
        "generated_at": _utc_now(),
        "source_workbook": str(workbook_path),
        "excluded_count": len(excluded),
        "excluded": excluded,
    }

    metadata = read_json(metadata_path, default={})
    metadata.update(
        {
            "last_index_built_at": _utc_now(),
            "source_workbook": str(workbook_path),
            "source_last_updated": source_last_updated,
            "city_count": len(cities),
            "excluded_count": len(excluded),
            "status": "current",
            "notes": "Search index built from latest workbook.",
        }
    )

    write_json(city_index_path, city_payload)
    write_json(excluded_index_path, excluded_payload)
    write_json(metadata_path, metadata)

    return {
        "city_index_path": str(city_index_path),
        "excluded_index_path": str(excluded_index_path),
        "metadata_path": str(metadata_path),
        "city_count": len(cities),
        "excluded_count": len(excluded),
        "source_sheet": sheet_name,
        "source_last_updated": source_last_updated,
    }
