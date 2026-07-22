from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

PROMPT_VERSION = "fire_metrics_summary_v1"
SUMMARY_SCHEMA_NAME = "fire_metrics_market_overview"


@dataclass(frozen=True)
class MetricDirection:
    field: str
    label: str
    favorable_when_higher: bool


COMPONENT_METRICS: tuple[MetricDirection, ...] = (
    MetricDirection("population_growth_recent", "Population growth", True),
    MetricDirection("median_income_growth_recent", "Income growth", True),
    MetricDirection("employment_growth_recent", "Employment growth", True),
    MetricDirection("landlord_friendliness_score", "Landlord friendliness", True),
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
        if overall is None:
            continue
        city_overall_scores.append((city_key(city), overall))

    selected_overall = overall_score_for_city(selected_city, distributions)
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
        comparison_rows.append(row)

    strengths = sorted(
        [r for r in comparison_rows if r["delta_directional_percentile"] > 0],
        key=lambda r: (r["delta_directional_percentile"], r["directional_percentile"]),
        reverse=True,
    )[:2]
    weaknesses = sorted(
        [r for r in comparison_rows if r["delta_directional_percentile"] < 0],
        key=lambda r: (r["delta_directional_percentile"], r["directional_percentile"]),
    )[:2]

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
        "selected_overall_score": selected_overall,
        "tracked_city_average": tracked_city_average,
        "tracked_city_count": len(overall_values),
        "selected_percentile": percentile,
        "strength_candidates": strengths,
        "weakness_candidates": weaknesses,
        "ambiguous_metric_context": ambiguous_rows,
        "component_scores": directional_for_city,
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
            "selected_overall_score": benchmarks.get("selected_overall_score"),
            "tracked_city_average": benchmarks.get("tracked_city_average"),
            "tracked_city_count": benchmarks.get("tracked_city_count"),
            "selected_percentile": benchmarks.get("selected_percentile"),
            "strength_candidates": benchmarks.get("strength_candidates") or [],
            "weakness_candidates": benchmarks.get("weakness_candidates") or [],
            "ambiguous_metric_context": benchmarks.get("ambiguous_metric_context") or [],
        },
    }


def build_fingerprint(payload: dict[str, Any]) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def fallback_summary(selected_city: dict[str, Any], benchmarks: dict[str, Any]) -> dict[str, str]:
    strength_sentence = "The strongest currently available signals are limited by missing values."
    weakness_sentence = "The largest currently available risks are limited by missing values."

    strengths = benchmarks.get("strength_candidates") or []
    weaknesses = benchmarks.get("weakness_candidates") or []

    if strengths:
        pieces = [directional_phrase(item, weakness=False) for item in strengths[:2]]
        if len(pieces) == 1:
            strength_sentence = f"The strongest available signal is {pieces[0]}."
        else:
            strength_sentence = f"The strongest available signals are {join_phrases(pieces)}."

    if weaknesses:
        pieces = [directional_phrase(item, weakness=True) for item in weaknesses[:2]]
        if len(pieces) == 1:
            weakness_sentence = f"The biggest current weakness is {pieces[0]}."
        else:
            weakness_sentence = f"The biggest current weaknesses are {join_phrases(pieces)}."

    overall = benchmarks.get("selected_overall_score")
    avg = benchmarks.get("tracked_city_average")
    count = benchmarks.get("tracked_city_count")
    pct = benchmarks.get("selected_percentile")

    if overall is None or avg is None or not count:
        comparison_sentence = "The computed FIRE Metrics composite score is limited because too many component values are missing."
    else:
        direction = "above" if overall >= avg else "below"
        if pct is None:
            comparison_sentence = (
                f"Its computed FIRE Metrics composite score is {overall:.1f} versus a tracked-city average computed composite score of {avg:.1f} across {count} cities, placing it {direction} average."
            )
        else:
            comparison_sentence = (
                f"Its computed FIRE Metrics composite score is {overall:.1f} versus a tracked-city average computed composite score of {avg:.1f} across {count} cities, placing it {direction} average at about the {round(pct)}th percentile."
            )

    return {
        "strength_sentence": one_sentence(strength_sentence),
        "weakness_sentence": one_sentence(weakness_sentence),
        "comparison_sentence": one_sentence(comparison_sentence),
    }


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
                    "raw_percentile": item.get("raw_percentile"),
                    "directional_percentile": item.get("directional_percentile"),
                    "favorable_percentile": item.get("directional_percentile"),
                    "delta_directional_percentile": item.get("delta_directional_percentile"),
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
            "median_home_value_growth_recent": selected_city.get("median_home_value_growth_recent"),
            "employment_current": selected_city.get("employment_current"),
            "employment_growth_recent": selected_city.get("employment_growth_recent"),
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
            "computed_composite_score": benchmarks.get("selected_overall_score"),
            "tracked_city_average": benchmarks.get("tracked_city_average"),
            "tracked_city_count": benchmarks.get("tracked_city_count"),
            "computed_composite_percentile": benchmarks.get("selected_percentile"),
            "strength_candidates": prompt_candidate_rows(benchmarks.get("strength_candidates") or []),
            "weakness_candidates": prompt_candidate_rows(benchmarks.get("weakness_candidates") or []),
            "ambiguous_metric_context": benchmarks.get("ambiguous_metric_context") or [],
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
        "You are generating a concise city investment screening overview from provided FIRE Metrics data only. "
        "Return valid JSON matching the schema with exactly three fields. "
        "Each field must be exactly one sentence. "
        "Use only supplied values and benchmark comparisons. "
        "Mention at most two strengths and at most two weaknesses. "
        "Third sentence must include computed FIRE Metrics composite score, tracked-city average computed composite score, above/below average direction, and percentile when available. "
        "For each strength/weakness candidate, raw_percentile is the raw metric percentile and favorable_percentile/directional_percentile is adjusted for whether higher values are favorable. "
        "Do not describe unfavorable-when-higher metrics as if the raw metric itself were at the favorable percentile. "
        "Do not mention data not present in input. "
        "Do not mention rent growth, cap rates, vacancy, taxes, insurance, supply pipelines, neighborhood quality, future appreciation, or returns. "
        "Avoid promotional language and avoid making recommendations. "
        "If key values are missing, state the assessment is limited."
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

    return out


def combined_summary(structured: dict[str, str]) -> str:
    return " ".join(
        [
            one_sentence(structured.get("strength_sentence", "")),
            one_sentence(structured.get("weakness_sentence", "")),
            one_sentence(structured.get("comparison_sentence", "")),
        ]
    )
