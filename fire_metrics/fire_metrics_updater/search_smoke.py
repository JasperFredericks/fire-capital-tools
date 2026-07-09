from __future__ import annotations

from city_search import find_city_match


def _sample_indexes():
    city_index = {
        "cities": [
            {
                "city": "St. Louis",
                "state": "MO",
                "display_name": "St. Louis, MO",
                "normalized_city": "st louis",
                "normalized_display_name": "st louis mo",
                "search_keys": ["st louis", "st louis mo", "saint louis", "saint louis mo"],
            },
            {
                "city": "New York",
                "state": "NY",
                "display_name": "New York, NY",
                "normalized_city": "new york",
                "normalized_display_name": "new york ny",
                "search_keys": ["new york", "new york ny", "nyc", "new york city"],
            },
            {
                "city": "Los Angeles",
                "state": "CA",
                "display_name": "Los Angeles, CA",
                "normalized_city": "los angeles",
                "normalized_display_name": "los angeles ca",
                "search_keys": ["los angeles", "los angeles ca", "la"],
            },
            {
                "city": "Springfield",
                "state": "MO",
                "display_name": "Springfield, MO",
                "normalized_city": "springfield",
                "normalized_display_name": "springfield mo",
                "search_keys": ["springfield", "springfield mo"],
            },
            {
                "city": "Springfield",
                "state": "IL",
                "display_name": "Springfield, IL",
                "normalized_city": "springfield",
                "normalized_display_name": "springfield il",
                "search_keys": ["springfield", "springfield il"],
            },
        ]
    }

    excluded_index = {
        "excluded": [
            {
                "city": "Santa Barbara",
                "state": "CA",
                "latest_population": 88000,
                "threshold_reason": "Below 100,000 population threshold.",
                "normalized_city": "santa barbara",
                "normalized_key": "santa barbara ca",
            }
        ]
    }
    return city_index, excluded_index


def run_smoke_tests() -> None:
    city_index, excluded_index = _sample_indexes()

    assert find_city_match("St Louis", city_index, excluded_index)["status"] == "found"
    assert find_city_match("Saint Louis", city_index, excluded_index)["status"] == "found"
    assert find_city_match("st. louis", city_index, excluded_index)["status"] == "found"

    ny = find_city_match("NYC", city_index, excluded_index)
    assert ny["status"] == "found" and ny["city"]["display_name"] == "New York, NY"

    nyc = find_city_match("New York City", city_index, excluded_index)
    assert nyc["status"] == "found" and nyc["city"]["display_name"] == "New York, NY"

    la = find_city_match("LA", city_index, excluded_index)
    assert la["status"] == "found" and la["city"]["display_name"] == "Los Angeles, CA"

    not_found = find_city_match("qwertyzzzz", city_index, excluded_index)
    assert not_found["status"] == "not_found"

    excluded = find_city_match("Santa Barbara", city_index, excluded_index)
    assert excluded["status"] == "excluded"

    ambiguous = find_city_match("Springfield", city_index, excluded_index)
    assert ambiguous["status"] == "suggestions"

    print("fire_metrics city search smoke tests passed")


if __name__ == "__main__":
    run_smoke_tests()
