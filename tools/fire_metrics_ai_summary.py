from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

PROMPT_VERSION = "fire_metrics_summary_v3"
SUMMARY_SCHEMA_NAME = "fire_metrics_market_overview"

RECOMMENDATION_STRONG = "strong preliminary candidate"
RECOMMENDATION_MIXED = "selective or mixed opportunity"
RECOMMENDATION_HIGH_RISK = "higher-risk preliminary market"

LANDLORD_SCORE_MAP = {
    1: 75.0,
    0: 50.0,
    -1: 25.0,
}

BANNED_SUMMARY_TERMS: tuple[str, ...] = (
    "percentile",
    "tracked cities",
    "tracked-city",
    "better than roughly",
    "relative_market_profile_score",
    "top-performing percentile",
    "bottom quartile",
    "above/below the tracked-city average",
    "undefined",
    "null",
    "nan",
)

PROHIBITED_MARKET_CLAIMS: tuple[str, ...] = (
    "rent growth",
    "cap rate",
    "cap-rate",
    "vacancy",
    "cash flow",
    "cashflow",
    "property tax",
    "insurance price",
    "guaranteed",
)


@dataclass(frozen=True)
class HomeValueContext:
    value_label: str
    growth_label: str
    value_percentile: float | None
    growth_percentile: float | None


@dataclass(frozen=True)
class MetricDirection:
    field: str
    label: str
    favorable_when_higher: bool


COMPONENT_METRICS: tuple[MetricDirection, ...] = (
    MetricDirection("population_growth_recent", "Population growth", True),
    MetricDirection("median_income_growth_recent", "Income growth", True),
    MetricDirection("employment_growth_recent", "Employment growth", True),
    MetricDirection("climate_risk_score", "Climate risk", False),
    MetricDirection("crime_index_score", "Crime index", False),
    MetricDirection("density_adjusted_crime_score", "Density-adjusted crime", False),
)

AMBIGUOUS_METRICS: tuple[MetricDirection, ...] = (
    MetricDirection("median_home_value_current", "Median home value", True),
    MetricDirection("median_home_value_growth_recent", "Home-value growth", True),
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def as_float(value: Any) -> float | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(num) or math.isinf(num):
        return None
    return num


def city_key(city: dict[str, Any]) -> str:
    return f"{str(city.get('city', '')).strip()}|{str(city.get('state', '')).strip().upper()}"


def percentile_for_value(values: list[float], value: float) -> float:
    if not values:
        return 50.0
    less = sum(1 for item in values if item < value)
    equal = sum(1 for item in values if item == value)
    rank = less + 0.5 * equal
    return round((rank / len(values)) * 100.0, 2)


def one_sentence(text: str) -> str:
    normalized = " ".join(str(text or "").replace("<", "").replace(">", "").strip().split())
    if not normalized:
        return "Data is limited for this city."
    if normalized[-1] not in ".!?":
        normalized = f"{normalized}."
    return normalized


def count_sentences(text: str) -> int:
    if not text:
        return 0
    # Treat sentence boundaries as punctuation followed by whitespace and an
    # uppercase letter, which avoids splitting decimal numbers like 63.4.
    boundaries = re.findall(r"[.!?](?=\s+[A-Z]|$)", text)
    return len(boundaries)


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    num = as_float(value)
    if num is None:
        return False
    return math.isnan(num) or math.isinf(num)


def ordinal(n: int) -> str:
    n_abs = abs(int(n))
    last_two = n_abs % 100
    if 11 <= last_two <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n_abs % 10, "th")
    return f"{n_abs}{suffix}"


def rounded_percent(value: Any) -> int:
    num = as_float(value)
    if num is None:
        return 0
    return max(0, min(100, int(round(num))))


def favorable_percentile_phrase(value: Any) -> str:
    return f"approximately the {ordinal(rounded_percent(value))} percentile"


def directional_phrase(candidate: dict[str, Any], *, weakness: bool) -> str:
    label = str(candidate.get("label") or "Metric")
    field = str(candidate.get("field") or "")
    favorable_when_higher = bool(candidate.get("favorable_when_higher"))
    directional_pct = rounded_percent(candidate.get("directional_percentile"))

    if field == "landlord_friendliness_score":
        category = candidate.get("landlord_policy_category")
        if category == 1:
            return "a landlord-friendly state environment"
        if category == 0:
            return "a neutral or mixed landlord-policy environment"
        return "a tenant-friendly state environment"

    if favorable_when_higher:
        return f"{label} at {favorable_percentile_phrase(directional_pct)}"

    if weakness:
        worse_share = max(0, min(100, 100 - directional_pct))
        if field == "climate_risk_score":
            return f"higher climate risk than roughly {worse_share}% of tracked cities"
        if field == "crime_index_score":
            return f"higher crime than roughly {worse_share}% of tracked cities"
        if field == "density_adjusted_crime_score":
            return f"a less favorable density-adjusted crime profile than roughly {worse_share}% of tracked cities"
        return f"a less favorable {label.lower()} profile than roughly {worse_share}% of tracked cities"

    if field == "climate_risk_score":
        return f"lower climate risk than roughly {directional_pct}% of tracked cities"
    if field == "crime_index_score":
        return f"lower crime than roughly {directional_pct}% of tracked cities"
    if field == "density_adjusted_crime_score":
        return f"a more favorable density-adjusted crime profile than roughly {directional_pct}% of tracked cities"
    return f"a more favorable {label.lower()} profile than roughly {directional_pct}% of tracked cities"


def join_phrases(phrases: list[str]) -> str:
    if not phrases:
        return ""
    if len(phrases) == 1:
        return phrases[0]
    if len(phrases) == 2:
        return f"{phrases[0]} and {phrases[1]}"
    return ", ".join(phrases[:-1]) + f", and {phrases[-1]}"


def fmt_count(value: Any) -> str | None:
    num = as_float(value)
    if num is None:
        return None
    return f"{int(round(num)):,}"


def fmt_currency(value: Any) -> str | None:
    num = as_float(value)
    if num is None:
        return None
    return f"${int(round(num)):,}"


def fmt_percent(value: Any) -> str | None:
    num = as_float(value)
    if num is None:
        return None
    return f"{num * 100:.2f}%"


def fmt_score(value: Any) -> str | None:
    num = as_float(value)
    if num is None:
        return None
    rounded = round(num, 1)
    if abs(rounded - int(rounded)) < 1e-9:
        return str(int(rounded))
    return f"{rounded:.1f}"


def city_display_name(city: dict[str, Any]) -> str:
    display = str(city.get("display_name") or "").strip()
    if display:
        return display
    city_name = str(city.get("city") or "").strip() or "This city"
    state = str(city.get("state") or "").strip().upper()
    return f"{city_name}, {state}" if state else city_name


def positive_growth_phrase(label: str, value: Any) -> str | None:
    pct = fmt_percent(value)
    if pct is None:
        return None
    return f"{label} of {pct}"


def risk_growth_phrase(label: str, value: Any) -> str | None:
    num = as_float(value)
    pct = fmt_percent(value)
    if num is None or pct is None:
        return None
    if num < 0:
        return f"{label} decline of {pct}"
    if num <= 0.003:
        return f"modest {label.lower()} of {pct}"
    return f"slower {label.lower()} of {pct}"


def score_with_rating(score: Any, rating: Any) -> str | None:
    score_text = fmt_score(score)
    if score_text is None:
        return None
    rating_text = str(rating or "").strip()
    if rating_text:
        return f"{score_text} ({rating_text})"
    return score_text


def build_strength_metric_phrases(selected_city: dict[str, Any], benchmarks: dict[str, Any]) -> list[tuple[str, str]]:
    candidate_fields = [str(item.get("field") or "") for item in benchmarks.get("strength_candidates") or []]
    ordered_fields = candidate_fields + [
        "employment_growth_recent",
        "population_growth_recent",
        "median_income_current",
        "median_income_growth_recent",
        "crime_index_score",
        "median_home_value_growth_recent",
        "climate_risk_score",
        "landlord_friendliness_score",
    ]

    phrases: list[tuple[str, str]] = []
    seen: set[str] = set()
    for field in ordered_fields:
        if field in seen:
            continue
        seen.add(field)

        phrase = None
        theme = "market stability"
        if field == "employment_growth_recent":
            growth_text = positive_growth_phrase("recent employment growth", selected_city.get(field))
            employment_count = fmt_count(selected_city.get("employment_current"))
            if growth_text and employment_count:
                phrase = f"resident employment of {employment_count} with {growth_text}"
            else:
                phrase = growth_text
            theme = "housing-demand momentum"
        elif field == "population_growth_recent":
            growth_text = positive_growth_phrase("recent population growth", selected_city.get(field))
            population_count = fmt_count(selected_city.get("population_current"))
            if growth_text and population_count:
                phrase = f"population of {population_count} with {growth_text}"
            else:
                phrase = growth_text
            theme = "long-term demand support"
        elif field == "median_income_current":
            income = fmt_currency(selected_city.get(field))
            if income:
                phrase = f"median household income of {income}"
                theme = "rent-paying capacity"
        elif field == "median_income_growth_recent":
            phrase = positive_growth_phrase("recent income growth", selected_city.get(field))
            theme = "household-income momentum"
        elif field == "crime_index_score":
            scored = score_with_rating(selected_city.get("crime_index_score"), selected_city.get("crime_rating"))
            if scored:
                phrase = f"crime index score of {scored}"
                theme = "tenant appeal and operating stability"
        elif field == "climate_risk_score":
            scored = score_with_rating(selected_city.get("climate_risk_score"), selected_city.get("climate_risk_rating"))
            if scored:
                phrase = f"climate-risk score of {scored}"
                theme = "resilience planning"
        elif field == "median_home_value_growth_recent":
            phrase = positive_growth_phrase("recent home-value growth", selected_city.get(field))
            theme = "demand durability"
        elif field == "landlord_friendliness_score":
            label = str(selected_city.get("landlord_friendliness_label") or "").strip()
            if label:
                phrase = f"a {label.lower()} landlord-policy environment"
                theme = "operating flexibility"

        if phrase:
            phrases.append((phrase, theme))
        if len(phrases) >= 3:
            break

    return phrases


def build_risk_metric_phrases(selected_city: dict[str, Any], benchmarks: dict[str, Any]) -> list[tuple[str, str]]:
    candidate_fields = [str(item.get("field") or "") for item in benchmarks.get("weakness_candidates") or []]
    ordered_fields = candidate_fields + [
        "climate_risk_score",
        "crime_index_score",
        "density_adjusted_crime_score",
        "employment_growth_recent",
        "population_growth_recent",
        "median_income_growth_recent",
        "median_home_value_current",
        "median_home_value_growth_recent",
        "landlord_friendliness_score",
    ]

    phrases: list[tuple[str, str]] = []
    seen: set[str] = set()
    for field in ordered_fields:
        if field in seen:
            continue
        seen.add(field)

        phrase = None
        impact = "warrant additional property-level diligence"
        if field == "climate_risk_score":
            scored = score_with_rating(selected_city.get("climate_risk_score"), selected_city.get("climate_risk_rating"))
            climate_num = as_float(selected_city.get("climate_risk_score"))
            should_include = field in candidate_fields or (climate_num is not None and climate_num >= 50)
            if scored and should_include:
                phrase = f"climate-risk score of {scored}"
                impact = "increase resilience and operating-cost uncertainty"
        elif field == "crime_index_score":
            scored = score_with_rating(selected_city.get("crime_index_score"), selected_city.get("crime_rating"))
            crime_num = as_float(selected_city.get("crime_index_score"))
            should_include = field in candidate_fields or (crime_num is not None and crime_num >= 50)
            if scored and should_include:
                phrase = f"crime index score of {scored}"
                impact = "require tighter property and submarket screening"
        elif field == "density_adjusted_crime_score":
            scored = score_with_rating(selected_city.get("density_adjusted_crime_score"), selected_city.get("density_adjusted_crime_rating"))
            density_num = as_float(selected_city.get("density_adjusted_crime_score"))
            should_include = field in candidate_fields or (density_num is not None and density_num >= 50)
            if scored and should_include:
                phrase = f"density-adjusted crime score of {scored}"
                impact = "add location-selection and tenant-risk complexity"
        elif field == "employment_growth_recent":
            phrase = risk_growth_phrase("Recent employment", selected_city.get(field))
            impact = "signal softer near-term housing-demand momentum"
        elif field == "population_growth_recent":
            phrase = risk_growth_phrase("Recent population", selected_city.get(field))
            impact = "limit long-run rental-demand expansion"
        elif field == "median_income_growth_recent":
            phrase = risk_growth_phrase("Recent income", selected_city.get(field))
            impact = "temper household spending and rent-growth resilience"
        elif field == "median_home_value_current":
            value = fmt_currency(selected_city.get(field))
            if value:
                phrase = f"median home value of {value}"
                impact = "raise acquisition-cost pressure"
        elif field == "median_home_value_growth_recent":
            growth = fmt_percent(selected_city.get(field))
            if growth:
                phrase = f"recent home-value growth of {growth}"
                impact = "tighten entry pricing for new acquisitions"
        elif field == "landlord_friendliness_score":
            label = str(selected_city.get("landlord_friendliness_label") or "").strip().lower()
            if label and "tenant" in label:
                phrase = f"a {label} landlord-policy environment"
                impact = "increase regulatory and management complexity"

        if phrase:
            phrases.append((phrase, impact))
        if len(phrases) >= 2:
            break

    return phrases


def pick_opening(options: list[str], seed_key: str) -> str:
    if not options:
        return ""
    digest = hashlib.sha256(seed_key.encode("utf-8")).hexdigest()
    idx = int(digest[:8], 16) % len(options)
    return options[idx]


def contains_banned_summary_language(text: str) -> bool:
    lower = text.lower()
    return any(term in lower for term in BANNED_SUMMARY_TERMS)


def contains_prohibited_claims(text: str) -> bool:
    lower = text.lower()
    return any(term in lower for term in PROHIBITED_MARKET_CLAIMS)


def metric_distributions(cities: list[dict[str, Any]]) -> dict[str, list[float]]:
    output: dict[str, list[float]] = {}
    for metric in COMPONENT_METRICS + AMBIGUOUS_METRICS:
        vals = [as_float(city.get(metric.field)) for city in cities]
        output[metric.field] = sorted([v for v in vals if v is not None])
    return output


def directional_score(metric: MetricDirection, raw_percentile: float) -> float:
    if metric.favorable_when_higher:
        return raw_percentile
    return round(100.0 - raw_percentile, 2)


def component_directional_scores(city: dict[str, Any], distributions: dict[str, list[float]]) -> dict[str, dict[str, Any]]:
    scores: dict[str, dict[str, Any]] = {}
    for metric in COMPONENT_METRICS:
        value = as_float(city.get(metric.field))
        if value is None:
            continue
        dist = distributions.get(metric.field, [])
        if not dist:
            continue
        raw_pct = percentile_for_value(dist, value)
        scores[metric.field] = {
            "field": metric.field,
            "label": metric.label,
            "value": value,
            "raw_percentile": raw_pct,
            "directional_percentile": directional_score(metric, raw_pct),
            "favorable_when_higher": metric.favorable_when_higher,
        }
    return scores


def landlord_policy_category(value: Any) -> int | None:
    num = as_float(value)
    if num is None:
        return None
    if num > 0:
        return 1
    if num < 0:
        return -1
    return 0


def landlord_component(city: dict[str, Any]) -> dict[str, Any] | None:
    category = landlord_policy_category(city.get("landlord_friendliness_score"))
    if category is None:
        return None

    if category == 1:
        text = "landlord-friendly state environment"
    elif category == 0:
        text = "neutral or mixed landlord-policy environment"
    else:
        text = "tenant-friendly state environment"

    contribution = LANDLORD_SCORE_MAP[category]
    return {
        "field": "landlord_friendliness_score",
        "label": "Landlord policy environment",
        "value": category,
        "raw_percentile": None,
        "directional_percentile": contribution,
        "favorable_when_higher": True,
        "is_categorical": True,
        "landlord_policy_category": category,
        "landlord_policy_label": text,
        "average_directional_percentile": 50.0,
        "delta_directional_percentile": round(contribution - 50.0, 2),
    }


def home_value_context(city: dict[str, Any], distributions: dict[str, list[float]]) -> HomeValueContext:
    current_value = as_float(city.get("median_home_value_current"))
    growth_value = as_float(city.get("median_home_value_growth_recent"))
    current_dist = distributions.get("median_home_value_current", [])
    growth_dist = distributions.get("median_home_value_growth_recent", [])

    value_pct = percentile_for_value(current_dist, current_value) if current_value is not None and current_dist else None
    growth_pct = percentile_for_value(growth_dist, growth_value) if growth_value is not None and growth_dist else None

    if value_pct is None:
        value_label = "home-value levels are not available"
    elif value_pct >= 70:
        value_label = "home-value levels suggest elevated acquisition-cost pressure"
    elif value_pct <= 30:
        value_label = "home-value levels suggest lower acquisition-cost pressure"
    else:
        value_label = "home-value levels are near the tracked-city midpoint"

    if growth_pct is None:
        growth_label = "recent home-value momentum is unavailable"
    elif growth_pct >= 70:
        growth_label = "recent home-value growth has been relatively strong without implying guaranteed future appreciation"
    elif growth_pct <= 30:
        growth_label = "recent home-value growth has been relatively weak"
    else:
        growth_label = "recent home-value growth is near the tracked-city midpoint"

    return HomeValueContext(
        value_label=value_label,
        growth_label=growth_label,
        value_percentile=value_pct,
        growth_percentile=growth_pct,
    )


def overall_score_for_city(city: dict[str, Any], distributions: dict[str, list[float]]) -> float | None:
    comp = component_directional_scores(city, distributions)
    values = [item["directional_percentile"] for item in comp.values()]
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def compute_benchmarks(selected_city: dict[str, Any], cities: list[dict[str, Any]]) -> dict[str, Any]:
    distributions = metric_distributions(cities)

    city_overall_scores: list[tuple[str, float]] = []
    for city in cities:
        overall = overall_score_for_city(city, distributions)
        landlord = landlord_component(city)
        values = []
        if overall is not None:
            values.append(overall)
        if landlord is not None:
            values.append(float(landlord["directional_percentile"]))
        if values:
            overall = round(sum(values) / len(values), 2)
        if overall is None:
            continue
        city_overall_scores.append((city_key(city), overall))

    selected_overall_cont = overall_score_for_city(selected_city, distributions)
    selected_landlord = landlord_component(selected_city)
    selected_parts: list[float] = []
    if selected_overall_cont is not None:
        selected_parts.append(selected_overall_cont)
    if selected_landlord is not None:
        selected_parts.append(float(selected_landlord["directional_percentile"]))
    selected_overall = round(sum(selected_parts) / len(selected_parts), 2) if selected_parts else None

    overall_values = [score for _, score in city_overall_scores]
    tracked_city_average = round(sum(overall_values) / len(overall_values), 2) if overall_values else None
    percentile = None
    if selected_overall is not None and overall_values:
        percentile = round(percentile_for_value(sorted(overall_values), selected_overall), 1)

    directional_for_city = component_directional_scores(selected_city, distributions)

    comparison_rows = []
    for metric in COMPONENT_METRICS:
        dist = distributions.get(metric.field, [])
        value = as_float(selected_city.get(metric.field))
        if value is None or not dist:
            continue
        average_raw = round(sum(dist) / len(dist), 4)
        average_raw_pct = percentile_for_value(dist, average_raw)
        row = directional_for_city.get(metric.field)
        if not row:
            continue
        row = dict(row)
        row["average_raw"] = average_raw
        row["average_directional_percentile"] = directional_score(metric, average_raw_pct)
        row["delta_directional_percentile"] = round(
            row["directional_percentile"] - row["average_directional_percentile"],
            2,
        )
        row["is_categorical"] = False
        comparison_rows.append(row)

    if selected_landlord is not None:
        comparison_rows.append(selected_landlord)

    # Potential strengths/weaknesses using directional percentile thresholds.
    strengths = sorted(
        [
            r for r in comparison_rows
            if r.get("directional_percentile") is not None
            and float(r["directional_percentile"]) >= 60.0
        ],
        key=lambda r: (
            float(r.get("directional_percentile") or 0),
            float(r.get("delta_directional_percentile") or 0),
        ),
        reverse=True,
    )

    weaknesses = sorted(
        [
            r for r in comparison_rows
            if r.get("directional_percentile") is not None
            and float(r["directional_percentile"]) <= 40.0
        ],
        key=lambda r: (
            float(r.get("directional_percentile") or 100),
            float(r.get("delta_directional_percentile") or 0),
        ),
    )

    # If no candidates cross threshold, keep best/worst relative indicators for cautious wording.
    if not strengths and comparison_rows:
        strengths = sorted(
            comparison_rows,
            key=lambda r: (
                float(r.get("directional_percentile") or 0),
                float(r.get("delta_directional_percentile") or 0),
            ),
            reverse=True,
        )[:2]

    if not weaknesses and comparison_rows:
        weaknesses = sorted(
            comparison_rows,
            key=lambda r: (
                float(r.get("directional_percentile") or 100),
                float(r.get("delta_directional_percentile") or 0),
            ),
        )[:2]

    for row in strengths + weaknesses:
        directional = float(row.get("directional_percentile") or 50)
        row["is_material"] = directional >= 75 or directional <= 25
        row["is_major_risk"] = False
        if row.get("field") in {"climate_risk_score", "crime_index_score", "density_adjusted_crime_score"}:
            raw_pct = as_float(row.get("raw_percentile"))
            if raw_pct is not None and raw_pct >= 75:
                row["is_major_risk"] = True

    strength_candidates = strengths[:2]
    weakness_candidates = weaknesses[:2]

    home_context = home_value_context(selected_city, distributions)

    available_components = [
        *[v for v in directional_for_city.values()],
    ]
    if selected_landlord is not None:
        available_components.append(selected_landlord)

    completeness = round(len(available_components) / 7.0, 3)
    qualifying_strengths = sum(1 for c in strength_candidates if float(c.get("directional_percentile") or 0) >= 60)
    material_weaknesses = sum(1 for c in weakness_candidates if c.get("is_material") or c.get("is_major_risk"))
    severe_risk_count = sum(1 for c in weakness_candidates if c.get("is_major_risk"))

    recommendation_category = RECOMMENDATION_MIXED
    if selected_overall is not None:
        if (
            selected_overall >= 60
            and qualifying_strengths >= 2
            and material_weaknesses <= 1
            and completeness >= 0.6
        ):
            recommendation_category = RECOMMENDATION_STRONG
        elif (
            selected_overall < 40
            or material_weaknesses >= 2
            or (severe_risk_count >= 1 and material_weaknesses >= qualifying_strengths)
        ):
            recommendation_category = RECOMMENDATION_HIGH_RISK

    missing_required_fields = [
        metric.field
        for metric in COMPONENT_METRICS
        if metric.field not in selected_city or selected_city.get(metric.field) is None
    ]
    landlord_missing = selected_city.get("landlord_friendliness_score") is None

    ambiguous_rows = []
    for metric in AMBIGUOUS_METRICS:
        value = as_float(selected_city.get(metric.field))
        dist = distributions.get(metric.field, [])
        if value is None or not dist:
            continue
        ambiguous_rows.append(
            {
                "label": metric.label,
                "value": value,
                "percentile": percentile_for_value(dist, value),
                "average": round(sum(dist) / len(dist), 4),
            }
        )

    return {
        "relative_market_profile_score": selected_overall,
        "tracked_city_relative_market_profile_average": tracked_city_average,
        "relative_market_profile_percentile": percentile,
        "tracked_city_count": len(overall_values),
        "strength_candidates": strength_candidates,
        "weakness_candidates": weakness_candidates,
        "ambiguous_metric_context": ambiguous_rows,
        "home_value_context": {
            "value_label": home_context.value_label,
            "growth_label": home_context.growth_label,
            "value_percentile": home_context.value_percentile,
            "growth_percentile": home_context.growth_percentile,
        },
        "recommendation_category": recommendation_category,
        "data_completeness": completeness,
        "available_component_count": len(available_components),
        "missing_required_fields": missing_required_fields,
        "landlord_missing": landlord_missing,
        "component_scores": directional_for_city,
        # Backward-compatible aliases.
        "selected_overall_score": selected_overall,
        "tracked_city_average": tracked_city_average,
        "selected_percentile": percentile,
    }


def fingerprint_payload(
    *,
    selected_city: dict[str, Any],
    benchmarks: dict[str, Any],
    model_name: str,
    refresh_last_at: str | None,
) -> dict[str, Any]:
    fields = [
        "population_rank",
        "population_current",
        "population_growth_2020_2025",
        "population_growth_recent",
        "median_income_current",
        "median_income_growth_2021_2024",
        "median_income_growth_recent",
        "median_home_value_current",
        "median_home_value_growth_2021_2024",
        "median_home_value_growth_recent",
        "employment_current",
        "employment_growth_2021_2025",
        "employment_growth_recent",
        "climate_risk_score",
        "climate_risk_rating",
        "crime_index_score",
        "crime_rating",
        "density_adjusted_crime_score",
        "density_adjusted_crime_rating",
        "landlord_friendliness_score",
        "landlord_friendliness_label",
        "population_updated_at",
        "income_updated_at",
        "home_value_updated_at",
        "employment_updated_at",
        "climate_updated_at",
        "crime_updated_at",
    ]

    city_data = {name: selected_city.get(name) for name in fields}
    city_data["warnings"] = selected_city.get("warnings") or []
    city_data["city"] = selected_city.get("city")
    city_data["state"] = selected_city.get("state")

    return {
        "prompt_version": PROMPT_VERSION,
        "model_name": model_name,
        "refresh_last_at": refresh_last_at,
        "selected_city": city_data,
        "benchmarks": {
            "relative_market_profile_score": benchmarks.get("relative_market_profile_score"),
            "tracked_city_relative_market_profile_average": benchmarks.get("tracked_city_relative_market_profile_average"),
            "tracked_city_count": benchmarks.get("tracked_city_count"),
            "relative_market_profile_percentile": benchmarks.get("relative_market_profile_percentile"),
            "strength_candidates": benchmarks.get("strength_candidates") or [],
            "weakness_candidates": benchmarks.get("weakness_candidates") or [],
            "ambiguous_metric_context": benchmarks.get("ambiguous_metric_context") or [],
            "home_value_context": benchmarks.get("home_value_context") or {},
            "recommendation_category": benchmarks.get("recommendation_category"),
            "data_completeness": benchmarks.get("data_completeness"),
        },
    }


def build_fingerprint(payload: dict[str, Any]) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def fallback_summary(selected_city: dict[str, Any], benchmarks: dict[str, Any]) -> dict[str, str]:
    display = city_display_name(selected_city)
    seed_key = city_key(selected_city) or display

    strength_metrics = build_strength_metric_phrases(selected_city, benchmarks)
    strength_values = [item[0] for item in strength_metrics[:3]]
    strength_themes = [item[1] for item in strength_metrics[:3]]
    if strength_values:
        opening = pick_opening(
            [
                f"{display} combines",
                f"The investment case in {display} is supported by",
                f"{display} benefits from",
            ],
            f"{seed_key}:strength",
        )
        theme_tail = "housing demand and renter stability"
        if strength_themes:
            theme_tail = join_phrases(sorted(set(strength_themes))[:2])
        strength_sentence = f"{opening} {join_phrases(strength_values)}, supporting {theme_tail}."
    else:
        strength_sentence = (
            f"{display} has limited high-confidence upside signals in the current dataset, so the investment case depends on property-level fundamentals."
        )

    risk_metrics = build_risk_metric_phrases(selected_city, benchmarks)
    risk_values = [item[0] for item in risk_metrics[:2]]
    risk_impacts = [item[1] for item in risk_metrics[:2]]
    if risk_values:
        concern_noun = "concern is" if len(risk_values) == 1 else "concerns are"
        impact_text = join_phrases(sorted(set(risk_impacts))[:2]) if risk_impacts else "warrant additional property-level diligence"
        weakness_sentence = f"The primary underwriting {concern_noun} {join_phrases(risk_values)}, which may {impact_text}."
    else:
        weakness_sentence = (
            "The main tradeoff is limited risk visibility in several metrics, so underwriting should emphasize property-specific due diligence."
        )

    category = str(benchmarks.get("recommendation_category") or RECOMMENDATION_MIXED)
    priority_themes: list[str] = []
    for _, theme in strength_metrics:
        lowered = theme.lower()
        if "demand" in lowered:
            priority_themes.append("demand growth")
        elif "income" in lowered or "household" in lowered:
            priority_themes.append("household strength")
        elif "stability" in lowered:
            priority_themes.append("market stability")
        elif "flexibility" in lowered:
            priority_themes.append("operating flexibility")
    priorities = join_phrases(list(dict.fromkeys(priority_themes))[:2]) or "balanced market fundamentals"

    if category == RECOMMENDATION_STRONG:
        comparison_sentence = (
            f"Overall, {display} appears attractive for further underwriting, particularly for investors prioritizing {priorities}, while still requiring property-level due diligence."
        )
    elif category == RECOMMENDATION_HIGH_RISK:
        comparison_sentence = (
            f"Overall, {display} appears higher risk and may be less attractive for investors seeking stable demand without substantial property-level diligence."
        )
    else:
        comparison_sentence = (
            f"Overall, {display} appears selectively attractive for investors prioritizing {priorities}, but the identified tradeoffs warrant additional property-level diligence."
        )

    structured = {
        "strength_sentence": one_sentence(strength_sentence),
        "weakness_sentence": one_sentence(weakness_sentence),
        "comparison_sentence": one_sentence(comparison_sentence),
    }
    if contains_banned_summary_language(combined_summary(structured)):
        structured = {
            "strength_sentence": one_sentence(f"{display} presents investable signals from available economic and household metrics that support additional screening."),
            "weakness_sentence": one_sentence("The main tradeoffs are concentrated in risk and data-completeness factors that require property-level verification."),
            "comparison_sentence": one_sentence(f"Overall, {display} appears selectively attractive for further underwriting with disciplined due diligence."),
        }
    return structured


def build_prompt_input(selected_city: dict[str, Any], benchmarks: dict[str, Any]) -> dict[str, Any]:
    def prompt_candidate_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in items:
            rows.append(
                {
                    "field": item.get("field"),
                    "label": item.get("label"),
                    "value": item.get("value"),
                    "favorable_when_higher": bool(item.get("favorable_when_higher")),
                    "is_categorical": bool(item.get("is_categorical")),
                    "landlord_policy_category": item.get("landlord_policy_category"),
                    "landlord_policy_label": item.get("landlord_policy_label"),
                    "is_material": bool(item.get("is_material")),
                    "is_major_risk": bool(item.get("is_major_risk")),
                }
            )
        return rows

    return {
        "city": {
            "city": selected_city.get("city"),
            "state": selected_city.get("state"),
            "display_name": selected_city.get("display_name"),
            "population_current": selected_city.get("population_current"),
            "population_growth_recent": selected_city.get("population_growth_recent"),
            "median_income_current": selected_city.get("median_income_current"),
            "median_income_growth_recent": selected_city.get("median_income_growth_recent"),
            "median_home_value_current": selected_city.get("median_home_value_current"),
            "median_home_value_growth_2021_2024": selected_city.get("median_home_value_growth_2021_2024"),
            "median_home_value_growth_recent": selected_city.get("median_home_value_growth_recent"),
            "employment_current": selected_city.get("employment_current"),
            "employment_growth_2021_2025": selected_city.get("employment_growth_2021_2025"),
            "employment_growth_recent": selected_city.get("employment_growth_recent"),
            "population_growth_2020_2025": selected_city.get("population_growth_2020_2025"),
            "median_income_growth_2021_2024": selected_city.get("median_income_growth_2021_2024"),
            "climate_risk_score": selected_city.get("climate_risk_score"),
            "climate_risk_rating": selected_city.get("climate_risk_rating"),
            "crime_index_score": selected_city.get("crime_index_score"),
            "crime_rating": selected_city.get("crime_rating"),
            "density_adjusted_crime_score": selected_city.get("density_adjusted_crime_score"),
            "density_adjusted_crime_rating": selected_city.get("density_adjusted_crime_rating"),
            "landlord_friendliness_score": selected_city.get("landlord_friendliness_score"),
            "landlord_friendliness_label": selected_city.get("landlord_friendliness_label"),
            "warnings": selected_city.get("warnings") or [],
        },
        "benchmarks": {
            "recommendation_category": benchmarks.get("recommendation_category"),
            "strength_candidates": prompt_candidate_rows(benchmarks.get("strength_candidates") or []),
            "weakness_candidates": prompt_candidate_rows(benchmarks.get("weakness_candidates") or []),
            "data_completeness": benchmarks.get("data_completeness"),
        },
    }


def openai_summary(
    *,
    api_key: str,
    model_name: str,
    selected_city: dict[str, Any],
    benchmarks: dict[str, Any],
) -> dict[str, str]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)

    instructions = (
        "You are a concise real estate market analyst writing an investor screening memo for one city. "
        "Return valid JSON matching the schema with exactly three fields, and each field must be exactly one sentence. "
        "Sentence 1 must explain the investment case using two or three concrete city statistics when available. "
        "Sentence 2 must explain the key risks or tradeoffs using one or two concrete city statistics or ratings. "
        "Sentence 3 must provide a clear preliminary investment conclusion aligned with recommendation_category. "
        "Use only provided city statistics and candidate metrics, and omit missing values rather than fabricating numbers. "
        "Do not mention percentiles, tracked-city comparisons, relative market profile scores, quartiles, or internal scoring weights. "
        "Do not invent rent growth, cap rates, vacancy, taxes, insurance prices, future returns, or guaranteed outcomes. "
        "Keep a neutral professional tone, avoid promotional language, and include a property-level due diligence caveat when appropriate."
    )

    schema = {
        "type": "object",
        "properties": {
            "strength_sentence": {"type": "string"},
            "weakness_sentence": {"type": "string"},
            "comparison_sentence": {"type": "string"},
        },
        "required": ["strength_sentence", "weakness_sentence", "comparison_sentence"],
        "additionalProperties": False,
    }

    prompt_input = build_prompt_input(selected_city, benchmarks)

    response = client.responses.create(
        model=model_name,
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": instructions}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": json.dumps(prompt_input, ensure_ascii=True)}],
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": SUMMARY_SCHEMA_NAME,
                "schema": schema,
                "strict": True,
            }
        },
    )

    parsed = json.loads(response.output_text)
    return {
        "strength_sentence": one_sentence(parsed.get("strength_sentence", "")),
        "weakness_sentence": one_sentence(parsed.get("weakness_sentence", "")),
        "comparison_sentence": one_sentence(parsed.get("comparison_sentence", "")),
    }


def normalize_summary(structured: dict[str, str], selected_city: dict[str, Any], benchmarks: dict[str, Any]) -> dict[str, str]:
    out = {
        "strength_sentence": one_sentence(structured.get("strength_sentence", "")),
        "weakness_sentence": one_sentence(structured.get("weakness_sentence", "")),
        "comparison_sentence": one_sentence(structured.get("comparison_sentence", "")),
    }

    # If model output violates the one-sentence contract, fall back.
    if any(count_sentences(value) != 1 for value in out.values()):
        return fallback_summary(selected_city, benchmarks)

    merged = combined_summary(out)
    if count_sentences(merged) != 3:
        return fallback_summary(selected_city, benchmarks)

    if contains_banned_summary_language(merged):
        return fallback_summary(selected_city, benchmarks)

    if contains_prohibited_claims(merged):
        return fallback_summary(selected_city, benchmarks)

    conclusion_markers = (
        "appears attractive",
        "appears selectively attractive",
        "mixed preliminary",
        "higher risk",
        "warrants additional property-level diligence",
        "further underwriting",
    )
    if not any(marker in out["comparison_sentence"].lower() for marker in conclusion_markers):
        return fallback_summary(selected_city, benchmarks)

    return out


def combined_summary(structured: dict[str, str]) -> str:
    return " ".join(
        [
            one_sentence(structured.get("strength_sentence", "")),
            one_sentence(structured.get("weakness_sentence", "")),
            one_sentence(structured.get("comparison_sentence", "")),
        ]
    )
