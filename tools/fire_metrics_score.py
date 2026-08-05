from __future__ import annotations

from itertools import groupby
from typing import Any

FIRE_SCORE_VERSION = "fire_score_v1"

FIRE_SCORE_WEIGHTS: dict[str, float] = {
    "population_growth": 0.17,
    "income_growth": 0.15,
    "home_value_growth": 0.15,
    "crime": 0.11,
    "employment_growth": 0.20,
    "landlord_friendliness": 0.12,
    "climate_risk": 0.10,
}

COMPONENT_FIELDS: dict[str, str] = {
    "population_growth": "population_growth_recent",
    "income_growth": "median_income_growth_recent",
    "home_value_growth": "median_home_value_growth_recent",
    "employment_growth": "employment_growth_recent",
    "landlord_friendliness": "landlord_friendliness_score",
    "climate_risk": "climate_risk_score",
}

HIGHER_IS_BETTER = {
    "population_growth",
    "income_growth",
    "home_value_growth",
    "employment_growth",
}

LOWER_IS_BETTER = {
    "crime",
    "climate_risk",
}

ECONOMIC_COMPONENTS = {
    "population_growth",
    "income_growth",
    "home_value_growth",
    "employment_growth",
}

RISK_COMPONENTS = {
    "crime",
    "climate_risk",
    "landlord_friendliness",
}

CRIME_TREND_FIELD_CANDIDATES = (
    "crime_trend_recent",
    "crime_trend_score",
    "crime_growth_recent",
)

LANDLORD_SCORE_MAP = {
    -1: (0.0, "Tenant-friendly"),
    0: (50.0, "Neutral or mixed"),
    1: (100.0, "Landlord-friendly"),
}


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num or num in (float("inf"), float("-inf")):
        return None
    return num


def clamp_0_100(value: float | None) -> float | None:
    if value is None:
        return None
    if value < 0.0:
        return 0.0
    if value > 100.0:
        return 100.0
    return value


def stable_city_key(city: dict[str, Any]) -> str:
    existing = str(city.get("city_key") or "").strip()
    if existing:
        return existing
    city_name = str(city.get("city") or "").strip()
    state = str(city.get("state") or "").strip().upper()
    if not city_name or not state:
        return ""
    return f"{city_name}|{state}"


def _is_included(city: dict[str, Any]) -> bool:
    include_flag = city.get("include_flag")
    if include_flag is None:
        return True
    return bool(include_flag)


def eligible_cities(cities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for city in cities:
        if not isinstance(city, dict):
            continue
        if not _is_included(city):
            continue
        key = stable_city_key(city)
        if not key:
            continue
        if key in deduped:
            continue
        copy_city = dict(city)
        copy_city["city_key"] = key
        deduped[key] = copy_city
    return [deduped[key] for key in sorted(deduped.keys())]


def landlord_category(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"1", "+1", "landlord-friendly", "landlord friendly"}:
            return 1
        if token in {"0", "neutral", "mixed", "neutral or mixed"}:
            return 0
        if token in {"-1", "tenant-friendly", "tenant friendly"}:
            return -1
    num = as_float(value)
    if num is None:
        return None
    rounded = int(round(num))
    if rounded in LANDLORD_SCORE_MAP and abs(num - rounded) <= 1e-9:
        return rounded
    return None


def _field_coverage_ratio(cities: list[dict[str, Any]], field: str) -> float:
    if not cities:
        return 0.0
    valid = 0
    for city in cities:
        if as_float(city.get(field)) is not None:
            valid += 1
    return valid / len(cities)


def select_crime_source(cities: list[dict[str, Any]]) -> tuple[str, str]:
    if not cities:
        return "density_adjusted_crime_score", "empty_universe_default"

    known_fields = set()
    for city in cities:
        known_fields.update(city.keys())

    for candidate in CRIME_TREND_FIELD_CANDIDATES:
        if candidate not in known_fields:
            continue
        coverage = _field_coverage_ratio(cities, candidate)
        if coverage >= 0.70:
            return candidate, "crime_trend_field_present_with_majority_coverage"

    density_coverage = _field_coverage_ratio(cities, "density_adjusted_crime_score")
    if density_coverage >= 0.70:
        return "density_adjusted_crime_score", "density_adjusted_crime_score_has_majority_coverage"

    return "crime_index_score", "density_adjusted_crime_score_coverage_too_low"


def percentile_map_for_field(cities: list[dict[str, Any]], field: str) -> tuple[dict[str, float], int]:
    values: list[tuple[float, str]] = []
    for city in cities:
        key = city["city_key"]
        num = as_float(city.get(field))
        if num is None:
            continue
        values.append((num, key))

    n = len(values)
    if n == 0:
        return {}, 0
    if n == 1:
        return {values[0][1]: 50.0}, 1

    values.sort(key=lambda item: (item[0], item[1]))

    score_by_key: dict[str, float] = {}
    rank_start = 1
    for raw_value, group in groupby(values, key=lambda item: item[0]):
        grouped = list(group)
        group_count = len(grouped)
        avg_rank = (rank_start + (rank_start + group_count - 1)) / 2.0
        percentile = 100.0 * (avg_rank - 1.0) / (n - 1.0)
        percentile = clamp_0_100(percentile)
        for _, city_key in grouped:
            score_by_key[city_key] = float(percentile)
        rank_start += group_count

    return score_by_key, n


def coverage_label(coverage_percent: float) -> str:
    if coverage_percent >= 100.0:
        return "Complete"
    if coverage_percent >= 85.0:
        return "High coverage"
    if coverage_percent >= 70.0:
        return "Moderate coverage"
    return "Insufficient data"


def fire_score_label(score: float | None) -> str:
    if score is None:
        return "Insufficient data"
    if score >= 80.0:
        return "Strong preliminary candidate"
    if score >= 65.0:
        return "Favorable preliminary profile"
    if score >= 50.0:
        return "Selective or mixed opportunity"
    if score >= 35.0:
        return "Cautious preliminary profile"
    return "Higher-risk preliminary profile"


def _component_template(raw_value: Any, weight: float, comparison_count: int) -> dict[str, Any]:
    return {
        "raw_value": raw_value,
        "score": None,
        "weight": round(weight * 100.0, 1),
        "weighted_contribution": None,
        "available": False,
        "comparison_count": comparison_count,
    }


def _sanitize_json_number(value: float | None, digits: int | None = None) -> float | None:
    value = clamp_0_100(value) if value is not None else None
    if value is None:
        return None
    if digits is not None:
        return round(value, digits)
    return float(value)


def build_fire_score_index(
    rows: list[dict[str, Any]],
    *,
    comparison_universe: str = "Included FIRE Metrics cities with stable city_key identity, deduplicated by city_key.",
) -> dict[str, Any]:
    universe = eligible_cities(rows)
    crime_source, crime_reason = select_crime_source(universe)

    continuous_component_fields = {
        "population_growth": COMPONENT_FIELDS["population_growth"],
        "income_growth": COMPONENT_FIELDS["income_growth"],
        "home_value_growth": COMPONENT_FIELDS["home_value_growth"],
        "employment_growth": COMPONENT_FIELDS["employment_growth"],
        "crime": crime_source,
        "climate_risk": COMPONENT_FIELDS["climate_risk"],
    }

    percentile_maps: dict[str, dict[str, float]] = {}
    comparison_counts: dict[str, int] = {}
    for component, field in continuous_component_fields.items():
        by_key, n = percentile_map_for_field(universe, field)
        percentile_maps[component] = by_key
        comparison_counts[component] = n

    landlord_valid_count = 0
    for city in universe:
        if landlord_category(city.get(COMPONENT_FIELDS["landlord_friendliness"])) is not None:
            landlord_valid_count += 1
    comparison_counts["landlord_friendliness"] = landlord_valid_count

    scores_by_key: dict[str, dict[str, Any]] = {}
    sort_score_by_key: dict[str, float | None] = {}

    for city in universe:
        city_key = city["city_key"]
        components: dict[str, dict[str, Any]] = {}

        for component, field in continuous_component_fields.items():
            raw_value = as_float(city.get(field))
            entry = _component_template(raw_value, FIRE_SCORE_WEIGHTS[component], comparison_counts[component])
            percentile = percentile_maps[component].get(city_key)
            if raw_value is not None and percentile is not None:
                score = percentile if component in HIGHER_IS_BETTER else (100.0 - percentile)
                score = clamp_0_100(score)
                entry["score"] = _sanitize_json_number(score)
                entry["available"] = True
            components[component] = entry

        landlord_field = COMPONENT_FIELDS["landlord_friendliness"]
        landlord_raw = city.get(landlord_field)
        landlord_cat = landlord_category(landlord_raw)
        landlord_entry = _component_template(landlord_raw, FIRE_SCORE_WEIGHTS["landlord_friendliness"], comparison_counts["landlord_friendliness"])
        if landlord_cat is not None:
            landlord_score, landlord_label = LANDLORD_SCORE_MAP[landlord_cat]
            landlord_entry["score"] = _sanitize_json_number(landlord_score)
            landlord_entry["available"] = True
            landlord_entry["qualitative_label"] = landlord_label
            landlord_entry["raw_value"] = landlord_cat
        else:
            landlord_entry["qualitative_label"] = None
            landlord_entry["raw_value"] = None if landlord_raw is None else landlord_raw
        components["landlord_friendliness"] = landlord_entry

        available_components = [name for name, detail in components.items() if detail["available"]]
        coverage_weight = sum(FIRE_SCORE_WEIGHTS[name] for name in available_components)
        coverage_percent = clamp_0_100(coverage_weight * 100.0) or 0.0

        has_economic = any(name in ECONOMIC_COMPONENTS for name in available_components)
        has_risk = any(name in RISK_COMPONENTS for name in available_components)
        enough_components = len(available_components) >= 4
        enough_coverage = coverage_weight >= 0.70

        fire_score_unrounded: float | None = None
        if enough_coverage and enough_components and has_economic and has_risk and coverage_weight > 0.0:
            weighted_sum = 0.0
            for name in available_components:
                weighted_sum += FIRE_SCORE_WEIGHTS[name] * float(components[name]["score"])
            fire_score_unrounded = clamp_0_100(weighted_sum / coverage_weight)

        for name, detail in components.items():
            if detail["available"] and coverage_weight > 0.0:
                effective_weight = FIRE_SCORE_WEIGHTS[name] / coverage_weight
                contribution = float(detail["score"]) * effective_weight
                detail["weighted_contribution"] = round(contribution, 4)
            else:
                detail["weighted_contribution"] = None

        fire_score_display = round(fire_score_unrounded, 1) if fire_score_unrounded is not None else None

        payload = {
            "fire_score": fire_score_display,
            "fire_score_label": fire_score_label(fire_score_display),
            "fire_score_version": FIRE_SCORE_VERSION,
            "fire_score_coverage": round(coverage_percent, 1),
            "fire_score_coverage_label": coverage_label(coverage_percent),
            "fire_score_components": components,
            "fire_score_methodology": {
                "comparison_universe": comparison_universe,
                "crime_source": crime_source,
                "crime_source_reason": crime_reason,
            },
        }

        if fire_score_unrounded is None:
            payload["fire_score"] = None
            payload["fire_score_label"] = "Insufficient data"

        scores_by_key[city_key] = payload
        sort_score_by_key[city_key] = fire_score_unrounded

    return {
        "fire_score_version": FIRE_SCORE_VERSION,
        "comparison_city_count": len(universe),
        "crime_source": crime_source,
        "crime_source_reason": crime_reason,
        "scores_by_city_key": scores_by_key,
        "sort_score_by_city_key": sort_score_by_key,
    }


def enrich_city_with_fire_score(city: dict[str, Any], score_index: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(city)
    key = stable_city_key(enriched)
    if key:
        enriched["city_key"] = key
    score_payload = score_index.get("scores_by_city_key", {}).get(key)
    if score_payload:
        enriched.update(score_payload)
    else:
        enriched.update({
            "fire_score": None,
            "fire_score_label": "Insufficient data",
            "fire_score_version": FIRE_SCORE_VERSION,
            "fire_score_coverage": 0.0,
            "fire_score_coverage_label": "Insufficient data",
            "fire_score_components": {},
            "fire_score_methodology": {
                "comparison_universe": "Included FIRE Metrics cities with stable city_key identity, deduplicated by city_key.",
                "crime_source": score_index.get("crime_source", "density_adjusted_crime_score"),
                "crime_source_reason": score_index.get("crime_source_reason", "unavailable"),
            },
        })
    return enriched


def enrich_cities_with_fire_score(cities: list[dict[str, Any]], score_index: dict[str, Any]) -> list[dict[str, Any]]:
    return [enrich_city_with_fire_score(city, score_index) for city in cities]
