from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

PROMPT_VERSION = "fire_metrics_summary_v4"
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

GENERIC_PLACEHOLDER_PHRASES: tuple[str, ...] = (
    "presents investable signals",
    "available economic and household metrics",
    "support additional screening",
    "risk and data-completeness factors",
    "positive fundamentals",
    "favorable indicators",
    "mixed signals",
    "economic momentum",
    "market fundamentals",
    "requires disciplined due diligence",
)

STRENGTH_PRIORITY: dict[str, int] = {
    "employment_growth_recent": 1,
    "population_growth_recent": 2,
    "median_income_current": 3,
    "median_income_growth_recent": 4,
    "population_current": 5,
    "employment_current": 5,
    "crime_index_score": 6,
    "density_adjusted_crime_score": 6,
    "median_home_value_growth_recent": 7,
    "landlord_friendliness": 8,
    "median_home_value_current": 9,
    "climate_risk_score": 10,
}

RISK_PRIORITY: dict[str, int] = {
    "crime_index_score": 1,
    "density_adjusted_crime_score": 2,
    "climate_risk_score": 3,
    "employment_growth_recent": 4,
    "population_growth_recent": 5,
    "median_income_growth_recent": 6,
    "median_home_value_current": 7,
    "median_home_value_growth_recent": 8,
    "landlord_friendliness": 9,
    "missing_critical_risk_data": 10,
}

NUMBER_TOKEN_RE = re.compile(r"(?<![A-Za-z])\$?-?\d[\d,]*(?:\.\d+)?%?")


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


def normalize_text(text: str) -> str:
    lowered = str(text or "").lower()
    lowered = lowered.replace("\u2019", "'")
    lowered = lowered.replace("\u2013", "-")
    lowered = lowered.replace("\u2014", "-")
    lowered = re.sub(r"[^a-z0-9$%.,\-\s]", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def value_variants(metric: str, raw_value: float, formatted_value: str) -> set[str]:
    variants = {formatted_value.lower()}
    if metric.endswith("_growth_recent"):
        pct = raw_value * 100.0
        variants.add(f"{pct:.1f}%".lower())
        variants.add(f"{pct:.2f}%".lower())
    elif metric.endswith("_current") and formatted_value.startswith("$"):
        variants.add(f"${raw_value:,.2f}".lower())
        variants.add(f"${int(round(raw_value))}".lower())
    elif metric.endswith("_current"):
        variants.add(f"{int(round(raw_value))}".lower())
    return variants


def add_fact(
    facts: list[dict[str, Any]],
    *,
    metric: str,
    label: str,
    raw_value: Any,
    formatted_value: str,
    phrase: str,
    interpretation: str,
    direction: str,
    families: tuple[str, ...],
    eligible_case: bool = True,
    eligible_risk: bool = False,
) -> None:
    numeric_value = as_float(raw_value)
    variants = {formatted_value.lower()}
    if numeric_value is not None:
        variants |= value_variants(metric, numeric_value, formatted_value)
    match_tokens = {phrase.lower(), f"{label.lower()} {formatted_value.lower()}"}
    match_tokens |= variants
    facts.append(
        {
            "metric": metric,
            "label": label,
            "raw_value": raw_value,
            "formatted_value": formatted_value,
            "phrase": phrase,
            "interpretation": interpretation,
            "direction": direction,
            "strength_priority": STRENGTH_PRIORITY.get(metric, 99),
            "risk_priority": RISK_PRIORITY.get(metric, 99),
            "families": list(families),
            "eligible_case": eligible_case,
            "eligible_risk": eligible_risk,
            "match_tokens": sorted(match_tokens),
            "numeric_value": numeric_value,
        }
    )


def build_approved_city_facts(selected_city: dict[str, Any], benchmarks: dict[str, Any]) -> dict[str, Any]:
    facts: list[dict[str, Any]] = []

    def growth_direction(value: float) -> str:
        if value < 0:
            return "unfavorable"
        if value <= 0.003:
            return "neutral"
        return "favorable"

    for metric, label, interp in (
        ("employment_growth_recent", "Recent employment growth", "supporting near-term housing demand"),
        ("population_growth_recent", "Recent population growth", "supporting longer-run renter demand"),
        ("median_income_growth_recent", "Recent income growth", "supporting improving rent-paying capacity"),
        ("median_home_value_growth_recent", "Recent home-value growth", "signaling continued demand but potentially higher entry pricing"),
    ):
        raw = as_float(selected_city.get(metric))
        pct = fmt_percent(selected_city.get(metric))
        if raw is None or pct is None:
            continue
        add_fact(
            facts,
            metric=metric,
            label=label,
            raw_value=raw,
            formatted_value=pct,
            phrase=f"{label.lower()} of {pct}",
            interpretation=interp,
            direction=growth_direction(raw),
            families=("growth", "economic"),
            eligible_case=True,
            eligible_risk=True,
        )

    population = fmt_count(selected_city.get("population_current"))
    if population:
        add_fact(
            facts,
            metric="population_current",
            label="Population",
            raw_value=selected_city.get("population_current"),
            formatted_value=population,
            phrase=f"population of {population}",
            interpretation="providing market scale for renter demand",
            direction="neutral",
            families=("scale", "household"),
            eligible_case=True,
            eligible_risk=False,
        )

    employment = fmt_count(selected_city.get("employment_current"))
    if employment:
        add_fact(
            facts,
            metric="employment_current",
            label="Resident employment",
            raw_value=selected_city.get("employment_current"),
            formatted_value=employment,
            phrase=f"resident employment of {employment}",
            interpretation="adding labor-market scale that can support occupancy",
            direction="neutral",
            families=("scale", "economic"),
            eligible_case=True,
            eligible_risk=False,
        )

    income = fmt_currency(selected_city.get("median_income_current"))
    if income:
        add_fact(
            facts,
            metric="median_income_current",
            label="Median household income",
            raw_value=selected_city.get("median_income_current"),
            formatted_value=income,
            phrase=f"median household income of {income}",
            interpretation="supporting household rent-paying capacity",
            direction="favorable",
            families=("household", "economic"),
            eligible_case=True,
            eligible_risk=False,
        )

    home_value = fmt_currency(selected_city.get("median_home_value_current"))
    if home_value:
        home_value_num = as_float(selected_city.get("median_home_value_current")) or 0.0
        direction = "unfavorable" if home_value_num >= 900000 else "neutral"
        add_fact(
            facts,
            metric="median_home_value_current",
            label="Median home value",
            raw_value=selected_city.get("median_home_value_current"),
            formatted_value=home_value,
            phrase=f"median home value of {home_value}",
            interpretation="shaping acquisition-cost pressure for new purchases",
            direction=direction,
            families=("housing", "household"),
            eligible_case=True,
            eligible_risk=True,
        )

    def risk_direction_from_score(value: float) -> str:
        if value >= 65:
            return "unfavorable"
        if value <= 35:
            return "favorable"
        return "neutral"

    for metric, label, rating_key, interp in (
        ("crime_index_score", "Crime score", "crime_rating", "increase tenant-stability and operating complexity risk"),
        ("density_adjusted_crime_score", "Density-adjusted crime score", "density_adjusted_crime_rating", "increase block-level tenant and management risk"),
        ("climate_risk_score", "Climate-risk score", "climate_risk_rating", "increase insurance, resilience planning, and operating uncertainty"),
    ):
        score_text = fmt_score(selected_city.get(metric))
        score_num = as_float(selected_city.get(metric))
        if score_text is None or score_num is None:
            continue
        rating = str(selected_city.get(rating_key) or "").strip()
        rated_value = f"{score_text}, rated {rating}" if rating else score_text
        add_fact(
            facts,
            metric=metric,
            label=label,
            raw_value=score_num,
            formatted_value=rated_value,
            phrase=f"{label.lower()} of {rated_value}",
            interpretation=interp,
            direction=risk_direction_from_score(score_num),
            families=("risk", "operating"),
            eligible_case=True,
            eligible_risk=True,
        )

    landlord_label = str(selected_city.get("landlord_friendliness_label") or "").strip()
    if landlord_label:
        lowered = landlord_label.lower()
        direction = "neutral"
        if "tenant" in lowered:
            direction = "unfavorable"
        elif "landlord" in lowered:
            direction = "favorable"
        add_fact(
            facts,
            metric="landlord_friendliness",
            label="Landlord friendliness",
            raw_value=selected_city.get("landlord_friendliness_score"),
            formatted_value=landlord_label,
            phrase=f"a {lowered} operating environment",
            interpretation="shape leasing and operating flexibility",
            direction=direction,
            families=("risk", "operating"),
            eligible_case=True,
            eligible_risk=True,
        )

    missing_categories: list[str] = []
    if as_float(selected_city.get("crime_index_score")) is None:
        missing_categories.append("crime")
    if as_float(selected_city.get("climate_risk_score")) is None:
        missing_categories.append("climate-risk")
    if as_float(selected_city.get("density_adjusted_crime_score")) is None:
        missing_categories.append("density-adjusted crime")
    if not landlord_label:
        missing_categories.append("landlord-policy")

    usable_count = len(facts)
    growth_available = any("growth" in fact["families"] for fact in facts)
    risk_available = any("risk" in fact["families"] for fact in facts)

    approved_for_prompt = [
        {
            "metric": fact["metric"],
            "label": fact["label"],
            "formatted_value": fact["formatted_value"],
            "phrase": fact["phrase"],
            "interpretation": fact["interpretation"],
            "direction": fact["direction"],
            "strength_priority": fact["strength_priority"],
            "risk_priority": fact["risk_priority"],
            "eligible_case": fact["eligible_case"],
            "eligible_risk": fact["eligible_risk"],
        }
        for fact in facts
    ]

    return {
        "facts": facts,
        "approved_for_prompt": approved_for_prompt,
        "usable_count": usable_count,
        "growth_available": growth_available,
        "risk_available": risk_available,
        "missing_risk_categories": missing_categories,
        "recommendation_category": benchmarks.get("recommendation_category") or RECOMMENDATION_MIXED,
    }


def fact_matches_sentence(fact: dict[str, Any], sentence: str) -> bool:
    sentence_norm = normalize_text(sentence)
    for token in fact.get("match_tokens") or []:
        token_norm = normalize_text(str(token))
        if token_norm and token_norm in sentence_norm:
            return True
    return False


def matched_fact_indexes(text: str, facts: list[dict[str, Any]]) -> set[int]:
    matches: set[int] = set()
    for idx, fact in enumerate(facts):
        if fact_matches_sentence(fact, text):
            matches.add(idx)
    return matches


def split_three_sentences(text: str) -> list[str]:
    segments = re.findall(r"[^.!?]+[.!?]", text)
    return [seg.strip() for seg in segments if seg.strip()]


def parse_number_token(token: str) -> tuple[str, float] | None:
    raw = token.strip()
    if not raw:
        return None
    token_type = "number"
    if raw.endswith("%"):
        token_type = "percent"
    elif raw.startswith("$"):
        token_type = "currency"
    cleaned = raw.replace("$", "").replace("%", "").replace(",", "")
    num = as_float(cleaned)
    if num is None:
        return None
    return (token_type, num)


def extract_number_tokens(text: str) -> list[tuple[str, float]]:
    parsed: list[tuple[str, float]] = []
    for token in NUMBER_TOKEN_RE.findall(text):
        item = parse_number_token(token)
        if item is not None:
            parsed.append(item)
    return parsed


def contains_unapproved_numbers(text: str, facts: list[dict[str, Any]]) -> bool:
    approved_numbers: dict[str, list[float]] = {"percent": [], "currency": [], "number": []}
    for fact in facts:
        raw = as_float(fact.get("raw_value"))
        if raw is None:
            continue
        metric = str(fact.get("metric") or "")
        if metric.endswith("_growth_recent"):
            approved_numbers["percent"].append(raw * 100.0)
        elif metric.endswith("_current") and str(fact.get("formatted_value") or "").startswith("$"):
            approved_numbers["currency"].append(raw)
        else:
            approved_numbers["number"].append(raw)

    for token_type, value in extract_number_tokens(text):
        candidates = approved_numbers.get(token_type, [])
        if not candidates:
            return True
        tolerance = 1.0 if token_type != "percent" else 0.2
        if not any(abs(value - candidate) <= tolerance for candidate in candidates):
            return True
    return False


def sentence_has_generic_phrase_without_facts(sentence: str, facts: list[dict[str, Any]]) -> bool:
    lower = sentence.lower()
    if not any(phrase in lower for phrase in GENERIC_PLACEHOLDER_PHRASES):
        return False
    return not any(fact_matches_sentence(fact, sentence) for fact in facts)


def validate_summary_with_facts(structured: dict[str, str], selected_city: dict[str, Any], benchmarks: dict[str, Any]) -> bool:
    summary_text = combined_summary(structured)
    if count_sentences(summary_text) != 3:
        return False

    if contains_banned_summary_language(summary_text) or contains_prohibited_claims(summary_text):
        return False

    facts_ctx = build_approved_city_facts(selected_city, benchmarks)
    facts = facts_ctx["facts"]
    usable_count = int(facts_ctx["usable_count"])

    sentences = [
        one_sentence(structured.get("strength_sentence", "")),
        one_sentence(structured.get("weakness_sentence", "")),
        one_sentence(structured.get("comparison_sentence", "")),
    ]
    if any(count_sentences(sentence) != 1 for sentence in sentences):
        return False

    if any(sentence_has_generic_phrase_without_facts(sentence, facts) for sentence in sentences):
        return False

    matches = matched_fact_indexes(summary_text, facts)
    if usable_count >= 4:
        if len(matches) < 3:
            return False
    elif usable_count >= 2:
        if len(matches) < 2:
            return False
    elif usable_count == 1:
        if len(matches) < 1:
            return False
        if "limited" not in summary_text.lower():
            return False

    if facts_ctx["growth_available"]:
        if not any("growth" in facts[i]["families"] for i in matches):
            return False

    if facts_ctx["risk_available"]:
        if not any("risk" in facts[i]["families"] for i in matches):
            return False

    if contains_unapproved_numbers(summary_text, facts):
        return False

    return True


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
    facts_ctx = build_approved_city_facts(selected_city, benchmarks)
    facts = list(facts_ctx["facts"])

    case_candidates = sorted(
        [fact for fact in facts if fact.get("eligible_case")],
        key=lambda fact: (
            0 if fact.get("direction") == "favorable" else (1 if fact.get("direction") == "neutral" else 2),
            int(fact.get("strength_priority") or 99),
        ),
    )
    growth_candidates = [fact for fact in case_candidates if "growth" in fact.get("families", [])]
    case_facts: list[dict[str, Any]] = []
    if growth_candidates:
        case_facts.append(growth_candidates[0])
    for fact in case_candidates:
        if fact in case_facts:
            continue
        case_facts.append(fact)
        if len(case_facts) >= 3:
            break

    usable_count = int(facts_ctx["usable_count"])
    target_case_count = 3 if usable_count >= 3 else (2 if usable_count >= 2 else 1)
    case_facts = case_facts[:target_case_count]

    if case_facts:
        interpretations = [str(fact.get("interpretation") or "") for fact in case_facts if fact.get("interpretation")]
        interpretation_tail = join_phrases(list(dict.fromkeys(interpretations))[:2]) or "supporting initial demand underwriting"
        strength_sentence = f"{display} combines {join_phrases([fact['phrase'] for fact in case_facts])}, {interpretation_tail}."
        if target_case_count == 1:
            strength_sentence = f"{display} has {case_facts[0]['phrase']}, but limited additional city statistics constrain the initial demand assessment."
    else:
        strength_sentence = f"{display} has limited city statistics, so early screening is constrained by limited data."

    risk_candidates = sorted(
        [fact for fact in facts if fact.get("eligible_risk")],
        key=lambda fact: (
            0 if fact.get("direction") == "unfavorable" else (1 if fact.get("direction") == "neutral" else 2),
            int(fact.get("risk_priority") or 99),
        ),
    )
    risk_facts = risk_candidates[:2]

    if risk_facts:
        risk_interpretations = [str(fact.get("interpretation") or "") for fact in risk_facts if fact.get("interpretation")]
        risk_tail = join_phrases(list(dict.fromkeys(risk_interpretations))[:2]) or "warranting focused property-level review"
        risk_intro = "concern is" if len(risk_facts) == 1 else "concerns are"
        if all(fact.get("direction") == "favorable" for fact in risk_facts):
            weakness_sentence = f"Operating-risk indicators are comparatively contained with {join_phrases([fact['phrase'] for fact in risk_facts])}, while {risk_tail}."
        else:
            weakness_sentence = f"The main underwriting {risk_intro} {join_phrases([fact['phrase'] for fact in risk_facts])}, which may {risk_tail}."
    else:
        missing = list(facts_ctx.get("missing_risk_categories") or [])
        if missing:
            missing_text = join_phrases([f"{item} data" for item in missing[:2]])
            weakness_sentence = f"{missing_text.capitalize()} are currently unavailable, limiting a complete operating-risk assessment."
        else:
            weakness_sentence = "Operating-risk data is limited, so risk assessment remains preliminary."

    category = str(facts_ctx.get("recommendation_category") or RECOMMENDATION_MIXED)
    case_labels = join_phrases([fact["label"].lower() for fact in case_facts[:2]]) if case_facts else "limited demand indicators"
    risk_labels = join_phrases([fact["label"].lower() for fact in risk_facts[:2]]) if risk_facts else "limited operating-risk visibility"

    if category == RECOMMENDATION_STRONG:
        comparison_sentence = (
            f"Overall, {display} appears attractive for further investment underwriting, with {case_labels} supporting demand while {risk_labels} still warrant targeted diligence."
        )
    elif category == RECOMMENDATION_HIGH_RISK:
        comparison_sentence = (
            f"Overall, {display} appears higher risk and warrants substantial caution because {risk_labels} outweigh {case_labels} in preliminary screening."
        )
    else:
        comparison_sentence = (
            f"Overall, {display} presents a mixed preliminary investment opportunity: {case_labels} support demand, but {risk_labels} favor selective property-level underwriting."
        )

    structured = {
        "strength_sentence": one_sentence(strength_sentence),
        "weakness_sentence": one_sentence(weakness_sentence),
        "comparison_sentence": one_sentence(comparison_sentence),
    }
    return structured


def build_prompt_input(selected_city: dict[str, Any], benchmarks: dict[str, Any]) -> dict[str, Any]:
    facts_ctx = build_approved_city_facts(selected_city, benchmarks)

    return {
        "city": {
            "city": selected_city.get("city"),
            "state": selected_city.get("state"),
            "display_name": selected_city.get("display_name"),
            "usable_fact_count": facts_ctx.get("usable_count"),
            "growth_fact_available": facts_ctx.get("growth_available"),
            "risk_fact_available": facts_ctx.get("risk_available"),
        },
        "benchmarks": {
            "recommendation_category": benchmarks.get("recommendation_category"),
            "data_completeness": benchmarks.get("data_completeness"),
            "missing_risk_categories": facts_ctx.get("missing_risk_categories") or [],
        },
        "approved_city_facts": facts_ctx.get("approved_for_prompt") or [],
        "requirements": {
            "must_use_exact_fact_values": True,
            "minimum_concrete_facts_when_four_plus_available": 3,
            "minimum_concrete_facts_when_two_or_three_available": 2,
            "must_include_growth_fact_when_available": True,
            "must_include_risk_fact_when_available": True,
            "must_return_exactly_three_sentences": True,
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
        "Use only values listed under APPROVED CITY FACTS. "
        "Sentence 1 must explain the investment case using two or three concrete city statistics when available. "
        "Sentence 2 must explain risks or tradeoffs using one or two concrete risk statistics when available. "
        "Sentence 3 must provide a clear preliminary investment conclusion aligned with recommendation_category and tied to cited facts. "
        "When four or more approved facts are available, use at least three distinct facts. "
        "When two or three approved facts are available, use at least two distinct facts. "
        "When only one approved fact is available, include it and explicitly note limited data. "
        "Include at least one growth fact when available and at least one risk fact when available. "
        "Use exact supplied statistics and omit missing values rather than fabricating numbers. "
        "Do not mention percentiles, tracked-city comparisons, relative market profile scores, quartiles, or internal scoring weights. "
        "Do not refer vaguely to available metrics or data completeness unless naming specific missing categories. "
        "Do not invent rent growth, cap rates, vacancy, taxes, insurance prices, future returns, or guaranteed outcomes. "
        "Keep a neutral professional tone and end with a preliminary investment conclusion."
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

    if not validate_summary_with_facts(out, selected_city, benchmarks):
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
