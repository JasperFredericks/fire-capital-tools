from __future__ import annotations

import difflib
import re
from typing import Any

KNOWN_QUERY_ALIASES = {
    "nyc": "new york ny",
    "new york city": "new york ny",
    "la": "los angeles ca",
    "dc": "washington dc",
    "washington dc": "washington dc",
}

STATE_ABBR = {
    "alabama": "al",
    "alaska": "ak",
    "arizona": "az",
    "arkansas": "ar",
    "california": "ca",
    "colorado": "co",
    "connecticut": "ct",
    "delaware": "de",
    "district of columbia": "dc",
    "florida": "fl",
    "georgia": "ga",
    "hawaii": "hi",
    "idaho": "id",
    "illinois": "il",
    "indiana": "in",
    "iowa": "ia",
    "kansas": "ks",
    "kentucky": "ky",
    "louisiana": "la",
    "maine": "me",
    "maryland": "md",
    "massachusetts": "ma",
    "michigan": "mi",
    "minnesota": "mn",
    "mississippi": "ms",
    "missouri": "mo",
    "montana": "mt",
    "nebraska": "ne",
    "nevada": "nv",
    "new hampshire": "nh",
    "new jersey": "nj",
    "new mexico": "nm",
    "new york": "ny",
    "north carolina": "nc",
    "north dakota": "nd",
    "ohio": "oh",
    "oklahoma": "ok",
    "oregon": "or",
    "pennsylvania": "pa",
    "rhode island": "ri",
    "south carolina": "sc",
    "south dakota": "sd",
    "tennessee": "tn",
    "texas": "tx",
    "utah": "ut",
    "vermont": "vt",
    "virginia": "va",
    "washington": "wa",
    "west virginia": "wv",
    "wisconsin": "wi",
    "wyoming": "wy",
    "puerto rico": "pr",
}


def _collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_city_tokens(text: str) -> str:
    value = text.lower().strip()
    value = value.replace("&", " and ")
    value = re.sub(r"[._,]", " ", value)
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = _collapse_spaces(value)

    # Common city prefix normalization.
    value = re.sub(r"\bsaint\b", "st", value)
    value = re.sub(r"\bst\b", "st", value)
    value = re.sub(r"\bft\b", "fort", value)
    value = re.sub(r"\bmt\b", "mount", value)

    return _collapse_spaces(value)


def normalize_state_token(state_value: str) -> str:
    value = normalize_city_tokens(state_value)
    if value in STATE_ABBR:
        return STATE_ABBR[value]
    return value


def normalize_query(text: str) -> str:
    value = normalize_city_tokens(text)
    if value in KNOWN_QUERY_ALIASES:
        value = KNOWN_QUERY_ALIASES[value]
    return value


def make_search_key(city: str, state: str) -> str:
    city_key = normalize_city_tokens(city)
    state_key = normalize_state_token(state)
    return _collapse_spaces(f"{city_key} {state_key}")


def build_city_aliases(city: str, state: str) -> set[str]:
    state_key = normalize_state_token(state)
    city_key = normalize_city_tokens(city)
    aliases = {
        city_key,
        f"{city_key} {state_key}".strip(),
    }

    saint = re.sub(r"\bst\b", "saint", city_key)
    short = re.sub(r"\bsaint\b", "st", saint)
    fort = re.sub(r"\bfort\b", "ft", city_key)
    mount = re.sub(r"\bmount\b", "mt", city_key)

    for variant in {saint, short, fort, mount}:
        aliases.add(_collapse_spaces(variant))
        aliases.add(_collapse_spaces(f"{variant} {state_key}"))

    display_key = normalize_query(f"{city}, {state}")
    aliases.add(display_key)

    return {a for a in aliases if a}


def _dedupe_suggestions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = item.get("display_name", "")
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def find_city_match(query: str, city_index: dict[str, Any], excluded_index: dict[str, Any]) -> dict[str, Any]:
    if not query or not query.strip():
        return {
            "status": "error",
            "user_message": "Enter a city name to search FIRE Metric data.",
        }

    rows = city_index.get("cities", [])
    if not rows:
        return {
            "status": "error",
            "user_message": "Search index is not available yet. Rebuild the search index first.",
        }

    normalized_query = normalize_query(query)

    exact_matches: list[dict[str, Any]] = []
    city_only_matches: list[dict[str, Any]] = []

    for row in rows:
        search_keys = set(row.get("search_keys", []))
        normalized_city = row.get("normalized_city", "")
        normalized_display = row.get("normalized_display_name", "")
        if normalized_query in search_keys or normalized_query == normalized_display:
            exact_matches.append(row)
        elif normalized_query == normalized_city:
            city_only_matches.append(row)

    if len(exact_matches) == 1:
        match = exact_matches[0]
        return {
            "status": "found",
            "city": match,
            "user_message": f"Showing FIRE Metric results for {match.get('display_name', 'the selected city')}.",
        }

    if len(exact_matches) > 1:
        suggestions = [
            {
                "display_name": item.get("display_name"),
                "city": item.get("city"),
                "state": item.get("state"),
            }
            for item in exact_matches
        ]
        return {
            "status": "suggestions",
            "suggestions": _dedupe_suggestions(suggestions),
            "user_message": "Multiple cities matched that query. Pick the city and state you meant.",
        }

    if len(city_only_matches) == 1:
        match = city_only_matches[0]
        return {
            "status": "found",
            "city": match,
            "user_message": f"Showing FIRE Metric results for {match.get('display_name', 'the selected city')}.",
        }

    if len(city_only_matches) > 1:
        suggestions = [
            {
                "display_name": item.get("display_name"),
                "city": item.get("city"),
                "state": item.get("state"),
            }
            for item in city_only_matches
        ]
        return {
            "status": "suggestions",
            "suggestions": _dedupe_suggestions(suggestions),
            "user_message": "That city name exists in multiple states. Choose one suggestion.",
        }

    excluded_rows = excluded_index.get("excluded", [])
    excluded_map = {
        item.get("normalized_key", ""): item
        for item in excluded_rows
        if item.get("normalized_key")
    }

    excluded_match = excluded_map.get(normalized_query)
    if not excluded_match:
        # If the query is just a city and state is omitted, find first excluded city name match.
        for item in excluded_rows:
            if item.get("normalized_city") == normalized_query:
                excluded_match = item
                break

    if excluded_match:
        city = excluded_match.get("city", "This city")
        state = excluded_match.get("state", "")
        population = excluded_match.get("latest_population")
        reason = excluded_match.get("threshold_reason")
        if population is not None:
            message = f"{city}, {state} is not included because its latest Census population is below the 100,000 population threshold."
        elif reason:
            message = f"{city}, {state} is not included: {reason}"
        else:
            message = (
                "This city is not included in the current 100K+ city list. "
                "It may be below the population threshold or missing from the current source data."
            )
        return {
            "status": "excluded",
            "excluded": excluded_match,
            "user_message": message,
        }

    fuzzy_pool: dict[str, dict[str, Any]] = {}
    for row in rows:
        display_name = row.get("display_name") or ""
        candidate = {
            "display_name": display_name,
            "city": row.get("city"),
            "state": row.get("state"),
        }
        for key in row.get("search_keys", []):
            fuzzy_pool[key] = candidate
        if display_name:
            fuzzy_pool[normalize_query(display_name)] = candidate

    close_keys = difflib.get_close_matches(normalized_query, list(fuzzy_pool.keys()), n=6, cutoff=0.72)
    if close_keys:
        suggestions = _dedupe_suggestions([fuzzy_pool[key] for key in close_keys])
        return {
            "status": "suggestions",
            "suggestions": suggestions,
            "user_message": "No exact match found. Try one of these close matches.",
        }

    return {
        "status": "not_found",
        "user_message": (
            "This city is not included in the current 100K+ city list. "
            "It may be below the population threshold or missing from the current source data."
        ),
    }
