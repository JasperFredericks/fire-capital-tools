import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from flask import Flask

from fire_metrics.fire_metrics_updater import db as db_module
from tools import fire_metrics as fire_metrics_routes
from tools import fire_metrics_ai_summary as summary
from tools.fire_metrics import _summary_unavailable_response, city_summary, top_cities


def make_city(
    city: str,
    state: str,
    *,
    pop_growth: float | None,
    income_growth: float | None,
    employment_growth: float | None,
    landlord: float | None,
    climate: float | None,
    crime: float | None,
    density_crime: float | None,
    home_value: float | None = None,
    home_growth: float | None = None,
) -> dict:
    return {
        "city": city,
        "state": state,
        "display_name": f"{city}, {state}",
        "population_growth_recent": pop_growth,
        "median_income_growth_recent": income_growth,
        "employment_growth_recent": employment_growth,
        "landlord_friendliness_score": landlord,
        "climate_risk_score": climate,
        "crime_index_score": crime,
        "density_adjusted_crime_score": density_crime,
        "median_home_value_current": home_value,
        "median_home_value_growth_recent": home_growth,
        "warnings": [],
    }


class FireMetricsAISummaryTests(unittest.TestCase):
    def setUp(self):
        self.cities = [
            make_city("Alpha", "AA", pop_growth=0.05, income_growth=0.06, employment_growth=0.04, landlord=80, climate=30, crime=40, density_crime=35, home_value=350000, home_growth=0.05),
            make_city("Beta", "BB", pop_growth=0.01, income_growth=0.02, employment_growth=0.01, landlord=55, climate=65, crime=70, density_crime=68, home_value=500000, home_growth=0.08),
            make_city("Gamma", "CC", pop_growth=0.03, income_growth=0.03, employment_growth=0.02, landlord=68, climate=45, crime=55, density_crime=50, home_value=420000, home_growth=0.06),
            make_city("Delta", "DD", pop_growth=None, income_growth=None, employment_growth=None, landlord=None, climate=None, crime=None, density_crime=None, home_value=None, home_growth=None),
        ]

    def _rich_city(self) -> dict:
        return {
            **self.cities[0],
            "display_name": "New York, NY",
            "population_current": 8258035,
            "employment_current": 4132450,
            "median_income_current": 79713,
            "median_home_value_current": 839000,
            "crime_index_score": 42,
            "crime_rating": "Moderate",
            "climate_risk_score": 86,
            "climate_risk_rating": "Very High",
            "density_adjusted_crime_score": 44,
            "density_adjusted_crime_rating": "Moderate",
            "landlord_friendliness_label": "Tenant-friendly",
        }

    def _seed_cities_table(self, conn: sqlite3.Connection) -> None:
        for city in self.cities:
            conn.execute(
                """
                INSERT INTO cities (
                    city, state, display_name, normalized_city, normalized_display_name, search_key,
                    include_flag,
                    population_growth_recent, median_income_growth_recent, employment_growth_recent,
                    landlord_friendliness_score, climate_risk_score, crime_index_score,
                    density_adjusted_crime_score, median_home_value_current, median_home_value_growth_recent
                ) VALUES (
                    ?, ?, ?, ?, ?, ?,
                    1,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?
                )
                """,
                (
                    city["city"],
                    city["state"],
                    city["display_name"],
                    city["city"].lower(),
                    city["display_name"].lower(),
                    f"{city['city'].lower()} {city['state'].lower()}",
                    city.get("population_growth_recent"),
                    city.get("median_income_growth_recent"),
                    city.get("employment_growth_recent"),
                    city.get("landlord_friendliness_score"),
                    city.get("climate_risk_score"),
                    city.get("crime_index_score"),
                    city.get("density_adjusted_crime_score"),
                    city.get("median_home_value_current"),
                    city.get("median_home_value_growth_recent"),
                ),
            )
        conn.commit()

    def _seed_single_city(
        self,
        conn: sqlite3.Connection,
        *,
        city: str,
        state: str,
        display_name: str,
        pop_growth: float = 0.05,
        income_growth: float = 0.06,
        employment_growth: float = 0.04,
        landlord: float = 80,
        climate: float = 30,
        crime: float = 40,
        density_crime: float = 35,
        home_value: float = 350000,
        home_growth: float = 0.05,
    ) -> None:
        conn.execute(
            """
            INSERT INTO cities (
                city, state, display_name, normalized_city, normalized_display_name, search_key,
                include_flag,
                population_growth_recent, median_income_growth_recent, employment_growth_recent,
                landlord_friendliness_score, climate_risk_score, crime_index_score,
                density_adjusted_crime_score, median_home_value_current, median_home_value_growth_recent
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                1,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?
            )
            """,
            (
                city,
                state,
                display_name,
                city.lower(),
                display_name.lower(),
                f"{city.lower()} {state.lower()}",
                pop_growth,
                income_growth,
                employment_growth,
                landlord,
                climate,
                crime,
                density_crime,
                home_value,
                home_growth,
            ),
        )
        conn.commit()

    def _call_city_summary(self, app: Flask, city: str = "Alpha", state: str = "AA", city_key: str = ""):
        with app.test_request_context(
            "/tools/fire-metrics/api/city-summary",
            method="POST",
            json={"city": city, "state": state, "city_key": city_key},
        ):
            result = city_summary.__wrapped__()
        if isinstance(result, tuple):
            response, status_code = result
        else:
            response = result
            status_code = response.status_code
        return status_code, response.get_json()

    def _call_top_cities(self, app: Flask, metric: str, limit: int | None = None):
        query = {"metric": metric}
        if limit is not None:
            query["limit"] = str(limit)
        with app.test_request_context(
            "/tools/fire-metrics/api/top-cities",
            method="GET",
            query_string=query,
        ):
            result = top_cities.__wrapped__()
        if isinstance(result, tuple):
            response, status_code = result
        else:
            response = result
            status_code = response.status_code
        return status_code, response.get_json()

    def _seed_top_cities_fixture(self, conn: sqlite3.Connection) -> None:
        rows = [
            ("Arbor", "AA", 120000, 0.040, 0.050, 0.030, 0.040, 30.0, 15.0, 10.0, 33.1, -84.3, 1),
            ("Benton", "AA", 90000, 0.020, 0.030, 0.010, 0.010, 40.0, 15.0, 25.0, 34.2, -83.3, 1),
            ("Cedar", "AA", 150000, -0.010, 0.000, -0.020, -0.010, 35.0, 15.0, 10.0, 35.3, -82.3, 1),
            ("Dover", "BB", 50000, 0.010, 0.020, 0.000, 0.020, 25.0, 22.0, 30.0, 36.4, -81.3, 1),
            ("Essex", "BB", 70000, 0.015, 0.018, 0.012, 0.015, 28.0, 35.0, 32.0, 37.5, -80.3, 1),
            ("Fairview", "CC", 80000, 0.022, 0.019, 0.011, 0.017, 22.0, 45.0, 28.0, 38.6, -79.3, 1),
            ("Grove", "CC", 85000, 0.023, 0.021, 0.013, 0.019, 27.0, 55.0, 26.0, 39.7, -78.3, 1),
            ("Harbor", "DD", 92000, 0.025, -0.005, 0.015, 0.021, 32.0, 65.0, 24.0, 40.8, -77.3, 1),
            ("Irving", "DD", 94000, 0.026, 0.010, 0.016, 0.023, 20.0, 75.0, 22.0, 41.9, -76.3, 1),
            ("Jasper", "EE", 96000, 0.027, 0.011, 0.017, 0.025, 24.0, 85.0, 20.0, 42.0, -75.3, 1),
            ("Kingston", "EE", 98000, 0.028, 0.012, 0.018, 0.027, 26.0, 95.0, 18.0, 43.1, -74.3, 1),
            ("Larkin", "FF", 99000, 0.029, 0.013, 0.019, 0.029, 29.0, 105.0, 16.0, 44.2, -73.3, 1),
            ("Monroe", "FF", 65000, 0.021, 0.022, 0.009, 0.018, 31.0, None, 19.0, 45.3, -72.3, 1),
            ("Noble", "GG", 200000, 0.090, 0.100, 0.080, 0.090, 1.0, 1.0, 1.0, 46.4, -71.3, 0),
        ]

        for city, state, population, pop_growth, income_growth, job_growth, home_growth, climate, crime, density, lat, lng, include_flag in rows:
            display = f"{city}, {state}"
            conn.execute(
                """
                INSERT INTO cities (
                    city, state, display_name, normalized_city, normalized_display_name, search_key,
                    latitude, longitude, include_flag,
                    population_current, population_growth_recent,
                    median_income_growth_recent, employment_growth_recent,
                    median_home_value_growth_recent, climate_risk_score,
                    crime_index_score, density_adjusted_crime_score,
                    climate_risk_rating, crime_rating, density_adjusted_crime_rating,
                    population_updated_at, income_updated_at, home_value_updated_at,
                    employment_updated_at, climate_updated_at, crime_updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?
                )
                """,
                (
                    city,
                    state,
                    display,
                    city.lower(),
                    display.lower(),
                    f"{city.lower()} {state.lower()}",
                    lat,
                    lng,
                    include_flag,
                    population,
                    pop_growth,
                    income_growth,
                    job_growth,
                    home_growth,
                    climate,
                    crime,
                    density,
                    "Moderate",
                    "Moderate",
                    "Moderate",
                    "2026-07-20T00:00:00+00:00",
                    "2026-07-20T00:00:00+00:00",
                    "2026-07-20T00:00:00+00:00",
                    "2026-07-20T00:00:00+00:00",
                    "2026-07-20T00:00:00+00:00",
                    "2026-07-20T00:00:00+00:00",
                ),
            )
        conn.commit()

    def _assert_metric_sorted(self, payload: dict, metric: str, direction: str) -> None:
        values = [city.get(metric) for city in payload.get("cities", [])]
        self.assertTrue(values, "Expected ranking payload with at least one city")
        if direction == "asc":
            self.assertEqual(values, sorted(values))
        else:
            self.assertEqual(values, sorted(values, reverse=True))

    def test_tracked_city_average_excludes_null_overall_scores(self):
        bench = summary.compute_benchmarks(self.cities[0], self.cities)
        # Delta has no component values and should be excluded.
        self.assertEqual(bench["tracked_city_count"], 3)
        self.assertIsNotNone(bench["tracked_city_average"])

    def test_compute_benchmarks_exposes_relative_market_profile_fields(self):
        bench = summary.compute_benchmarks(self.cities[0], self.cities)
        self.assertIn("relative_market_profile_score", bench)
        self.assertIn("tracked_city_relative_market_profile_average", bench)
        self.assertIn("relative_market_profile_percentile", bench)
        self.assertIn("recommendation_category", bench)
        self.assertIn("data_completeness", bench)
        self.assertIsInstance(bench["strength_candidates"], list)
        self.assertIsInstance(bench["weakness_candidates"], list)

    def test_percentile_calculation(self):
        pct = summary.percentile_for_value([10.0, 20.0, 30.0, 40.0], 30.0)
        self.assertAlmostEqual(pct, 62.5)

    def test_ordinal_suffixes(self):
        self.assertEqual(summary.ordinal(1), "1st")
        self.assertEqual(summary.ordinal(2), "2nd")
        self.assertEqual(summary.ordinal(3), "3rd")
        self.assertEqual(summary.ordinal(4), "4th")
        self.assertEqual(summary.ordinal(11), "11th")
        self.assertEqual(summary.ordinal(12), "12th")
        self.assertEqual(summary.ordinal(13), "13th")
        self.assertEqual(summary.ordinal(21), "21st")
        self.assertEqual(summary.ordinal(22), "22nd")
        self.assertEqual(summary.ordinal(23), "23rd")

    def test_unfavorable_metric_direction_inversion(self):
        dist = summary.metric_distributions(self.cities)
        comp = summary.component_directional_scores(self.cities[1], dist)
        # Beta climate raw percentile is high (worse), directional percentile should be lower.
        climate = comp["climate_risk_score"]
        self.assertLess(climate["directional_percentile"], 50)

    def test_fingerprint_stable_when_inputs_unchanged(self):
        bench = summary.compute_benchmarks(self.cities[0], self.cities)
        payload = summary.fingerprint_payload(
            selected_city=self.cities[0],
            benchmarks=bench,
            model_name="model-a",
            refresh_last_at="2026-07-22T00:00:00+00:00",
        )
        fp1 = summary.build_fingerprint(payload)
        fp2 = summary.build_fingerprint(payload)
        self.assertEqual(fp1, fp2)

    def test_fingerprint_changes_when_city_metric_changes(self):
        bench = summary.compute_benchmarks(self.cities[0], self.cities)
        payload1 = summary.fingerprint_payload(
            selected_city=self.cities[0],
            benchmarks=bench,
            model_name="model-a",
            refresh_last_at="2026-07-22T00:00:00+00:00",
        )
        changed_city = dict(self.cities[0])
        changed_city["employment_growth_recent"] = 0.09
        payload2 = summary.fingerprint_payload(
            selected_city=changed_city,
            benchmarks=bench,
            model_name="model-a",
            refresh_last_at="2026-07-22T00:00:00+00:00",
        )
        self.assertNotEqual(summary.build_fingerprint(payload1), summary.build_fingerprint(payload2))

    def test_fingerprint_changes_when_benchmark_changes(self):
        bench1 = summary.compute_benchmarks(self.cities[0], self.cities)
        shifted = list(self.cities)
        shifted[1] = dict(shifted[1])
        shifted[1]["crime_index_score"] = 20
        bench2 = summary.compute_benchmarks(self.cities[0], shifted)
        payload1 = summary.fingerprint_payload(
            selected_city=self.cities[0],
            benchmarks=bench1,
            model_name="model-a",
            refresh_last_at="2026-07-22T00:00:00+00:00",
        )
        payload2 = summary.fingerprint_payload(
            selected_city=self.cities[0],
            benchmarks=bench2,
            model_name="model-a",
            refresh_last_at="2026-07-22T00:00:00+00:00",
        )
        self.assertNotEqual(summary.build_fingerprint(payload1), summary.build_fingerprint(payload2))

    def test_cache_hit_and_miss_behavior(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        db_module.init_schema(conn)

        cached = db_module.fetch_cached_city_summary(
            conn,
            city="Alpha",
            state="AA",
            data_fingerprint="fp1",
            model_name="m",
            prompt_version=summary.PROMPT_VERSION,
        )
        self.assertIsNone(cached)

        db_module.upsert_city_summary_cache(
            conn,
            {
                "city": "Alpha",
                "state": "AA",
                "city_key": "Alpha|AA",
                "data_fingerprint": "fp1",
                "model_name": "m",
                "prompt_version": summary.PROMPT_VERSION,
                "summary_text": "One. Two. Three.",
                "strength_sentence": "One.",
                "weakness_sentence": "Two.",
                "comparison_sentence": "Three.",
                "generated_at": "2026-07-22T00:00:00+00:00",
            },
        )

        cached = db_module.fetch_cached_city_summary(
            conn,
            city="Alpha",
            state="AA",
            data_fingerprint="fp1",
            model_name="m",
            prompt_version=summary.PROMPT_VERSION,
        )
        self.assertIsNotNone(cached)

    def test_missing_api_configuration_uses_fallback_response(self):
        bench = summary.compute_benchmarks(self.cities[0], self.cities)
        payload = _summary_unavailable_response(
            selected_city=self.cities[0],
            benchmark_data=bench,
            reason="OPENAI_API_KEY is not configured.",
            data_refreshed_at="2026-07-22T00:00:00+00:00",
        )
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(summary.count_sentences(payload["summary"]), 3)
        self.assertIn("relative_market_profile_score", payload)
        self.assertIn("recommendation_category", payload)
        self.assertEqual(payload["data_refreshed_at"], "2026-07-22T00:00:00+00:00")

    def test_openai_error_fallback_is_three_sentences(self):
        bench = summary.compute_benchmarks(self.cities[0], self.cities)
        structured = summary.fallback_summary(self.cities[0], bench)
        text = summary.combined_summary(structured)
        self.assertEqual(summary.count_sentences(text), 3)

    def test_fallback_summary_remains_exactly_three_sentences(self):
        structured = summary.fallback_summary(
            {
                **self.cities[0],
                "display_name": "Alpha, AA",
                "population_current": 198432,
                "employment_current": 101244,
                "median_income_current": 124968,
                "crime_rating": "Low",
                "climate_risk_rating": "High",
                "landlord_friendliness_label": "Landlord Friendly",
            },
            {
                "strength_candidates": [
                    {
                        "field": "employment_growth_recent",
                    },
                    {
                        "field": "median_income_current",
                    },
                ],
                "weakness_candidates": [
                    {
                        "field": "climate_risk_score",
                    }
                ],
                "recommendation_category": summary.RECOMMENDATION_MIXED,
            },
        )
        text = summary.combined_summary(structured)
        self.assertEqual(summary.count_sentences(text), 3)

    def test_fallback_summary_uses_concrete_investor_focused_stats(self):
        city = {
            **self._rich_city(),
            "display_name": "Gilbert, AZ",
            "population_current": 273136,
            "employment_current": 141205,
            "median_income_current": 124968,
            "crime_index_score": 6,
            "crime_rating": "Very Low",
            "climate_risk_score": 100,
            "climate_risk_rating": "Very High",
            "landlord_friendliness_label": "Landlord Friendly",
        }
        structured = summary.fallback_summary(
            city,
            {
                "strength_candidates": [{"field": "employment_growth_recent"}, {"field": "median_income_current"}],
                "weakness_candidates": [{"field": "climate_risk_score"}],
                "recommendation_category": summary.RECOMMENDATION_MIXED,
            },
        )
        combined = summary.combined_summary(structured)
        self.assertIn("$124,968", combined)
        self.assertIn("4.00%", combined)
        self.assertIn("climate-risk score of 100, rated Very High", combined)
        self.assertIn("mixed preliminary investment opportunity", combined.lower())

    def test_approved_facts_structure_and_minimum_concrete_rules(self):
        city = self._rich_city()
        benchmarks = summary.compute_benchmarks(city, [city, self.cities[1], self.cities[2]])
        facts_ctx = summary.build_approved_city_facts(city, benchmarks)
        facts = facts_ctx["facts"]
        self.assertGreaterEqual(facts_ctx["usable_count"], 4)
        self.assertTrue(facts_ctx["growth_available"])
        self.assertTrue(facts_ctx["risk_available"])
        self.assertTrue(any(f["metric"] == "median_income_current" for f in facts))
        self.assertTrue(any(f["metric"] == "population_current" for f in facts))
        self.assertTrue(any(f["metric"] == "climate_risk_score" for f in facts))

    def test_population_and_employment_and_income_formatting(self):
        city = self._rich_city()
        benchmarks = summary.compute_benchmarks(city, [city, self.cities[1], self.cities[2]])
        facts = summary.build_approved_city_facts(city, benchmarks)["facts"]
        pop = next(f for f in facts if f["metric"] == "population_current")
        emp = next(f for f in facts if f["metric"] == "employment_current")
        inc = next(f for f in facts if f["metric"] == "median_income_current")
        self.assertEqual(pop["formatted_value"], "8,258,035")
        self.assertEqual(emp["formatted_value"], "4,132,450")
        self.assertEqual(inc["formatted_value"], "$79,713")

    def test_growth_risk_and_landlord_formatting(self):
        city = self._rich_city()
        benchmarks = summary.compute_benchmarks(city, [city, self.cities[1], self.cities[2]])
        facts = summary.build_approved_city_facts(city, benchmarks)["facts"]
        growth = next(f for f in facts if f["metric"] == "employment_growth_recent")
        crime = next(f for f in facts if f["metric"] == "crime_index_score")
        climate = next(f for f in facts if f["metric"] == "climate_risk_score")
        landlord = next(f for f in facts if f["metric"] == "landlord_friendliness")
        self.assertIn("%", growth["formatted_value"])
        self.assertIn("rated Moderate", crime["formatted_value"])
        self.assertIn("rated Very High", climate["formatted_value"])
        self.assertIn("Tenant-friendly", landlord["formatted_value"])

    def test_fallback_summary_removes_percentile_and_tracked_city_language(self):
        city = {**self.cities[0], "display_name": "Alpha, AA", "population_current": 150000, "employment_current": 95000, "median_income_current": 98000}
        structured = summary.fallback_summary(
            city,
            {
                "strength_candidates": [{"field": "population_growth_recent"}],
                "weakness_candidates": [{"field": "crime_index_score"}],
                "recommendation_category": summary.RECOMMENDATION_STRONG,
            },
        )
        combined = summary.combined_summary(structured).lower()
        self.assertNotIn("percentile", combined)
        self.assertNotIn("tracked cities", combined)
        self.assertNotIn("better than roughly", combined)
        self.assertNotIn("relative_market_profile_score", combined)

    def test_fallback_summary_omits_missing_values_cleanly(self):
        city = {
            "city": "Sparse",
            "state": "SS",
            "display_name": "Sparse, SS",
            "population_growth_recent": None,
            "median_income_current": None,
            "employment_growth_recent": None,
            "climate_risk_score": None,
            "crime_index_score": None,
        }
        structured = summary.fallback_summary(
            city,
            {
                "strength_candidates": [{"field": "employment_growth_recent"}],
                "weakness_candidates": [{"field": "climate_risk_score"}],
                "recommendation_category": summary.RECOMMENDATION_HIGH_RISK,
            },
        )
        combined = summary.combined_summary(structured).lower()
        self.assertNotIn("undefined", combined)
        self.assertNotIn("null", combined)
        self.assertNotIn("nan", combined)

    def test_fallback_conclusion_wording_by_recommendation_category(self):
        city = {**self.cities[0], "display_name": "Alpha, AA", "population_current": 200000, "employment_current": 100000, "median_income_current": 100000}
        strong = summary.combined_summary(
            summary.fallback_summary(city, {"strength_candidates": [{"field": "employment_growth_recent"}], "weakness_candidates": [], "recommendation_category": summary.RECOMMENDATION_STRONG})
        ).lower()
        mixed = summary.combined_summary(
            summary.fallback_summary(city, {"strength_candidates": [{"field": "median_income_current"}], "weakness_candidates": [{"field": "climate_risk_score"}], "recommendation_category": summary.RECOMMENDATION_MIXED})
        ).lower()
        high_risk = summary.combined_summary(
            summary.fallback_summary(city, {"strength_candidates": [], "weakness_candidates": [{"field": "crime_index_score"}], "recommendation_category": summary.RECOMMENDATION_HIGH_RISK})
        ).lower()
        self.assertIn("attractive for further investment underwriting", strong)
        self.assertIn("mixed preliminary investment opportunity", mixed)
        self.assertIn("higher risk", high_risk)

    def test_normalize_summary_rejects_percentile_and_prohibited_claims(self):
        benchmarks = summary.compute_benchmarks(self.cities[0], self.cities)
        normalized = summary.normalize_summary(
            {
                "strength_sentence": "Alpha is better than roughly 90% of tracked cities.",
                "weakness_sentence": "Cap rate upside is strong with rising rent growth.",
                "comparison_sentence": "Overall, this is a strong preliminary candidate.",
            },
            self.cities[0],
            benchmarks,
        )
        combined = summary.combined_summary(normalized).lower()
        self.assertNotIn("better than roughly", combined)
        self.assertNotIn("tracked cities", combined)
        self.assertNotIn("cap rate", combined)
        self.assertEqual(summary.count_sentences(summary.combined_summary(normalized)), 3)

    def test_build_prompt_input_and_version_reflect_new_methodology(self):
        benchmarks = summary.compute_benchmarks(self.cities[0], self.cities)
        prompt_data = summary.build_prompt_input(self.cities[0], benchmarks)
        self.assertIn("recommendation_category", prompt_data["benchmarks"])
        self.assertNotIn("relative_market_profile_percentile", prompt_data["benchmarks"])
        self.assertIn("approved_city_facts", prompt_data)
        self.assertGreaterEqual(len(prompt_data["approved_city_facts"]), 1)
        self.assertEqual(summary.PROMPT_VERSION, "fire_metrics_summary_v4")

    def test_prompt_source_requires_no_percentiles_and_concrete_stats(self):
        module_source = Path("tools/fire_metrics_ai_summary.py").read_text(encoding="utf-8")
        self.assertIn("Do not mention percentiles", module_source)
        self.assertIn("APPROVED CITY FACTS", module_source)
        self.assertIn("preliminary investment conclusion", module_source)

    def test_fingerprint_changes_when_prompt_version_changes(self):
        benchmarks = summary.compute_benchmarks(self.cities[0], self.cities)
        payload = summary.fingerprint_payload(
            selected_city=self.cities[0],
            benchmarks=benchmarks,
            model_name="model-a",
            refresh_last_at="2026-07-22T00:00:00+00:00",
        )
        fp_current = summary.build_fingerprint(payload)
        payload_old = dict(payload)
        payload_old["prompt_version"] = "fire_metrics_summary_v3"
        fp_old = summary.build_fingerprint(payload_old)
        self.assertNotEqual(fp_current, fp_old)

    def test_ai_output_without_enough_approved_facts_is_rejected(self):
        city = self._rich_city()
        benchmarks = summary.compute_benchmarks(city, [city, self.cities[1], self.cities[2]])
        normalized = summary.normalize_summary(
            {
                "strength_sentence": "New York has a population of 8,258,035.",
                "weakness_sentence": "The city faces underwriting tradeoffs.",
                "comparison_sentence": "Overall, New York appears selectively attractive.",
            },
            city,
            benchmarks,
        )
        merged = summary.combined_summary(normalized)
        self.assertNotEqual(merged, "New York has a population of 8,258,035. The city faces underwriting tradeoffs. Overall, New York appears selectively attractive.")
        self.assertGreaterEqual(len(summary.matched_fact_indexes(merged, summary.build_approved_city_facts(city, benchmarks)["facts"])), 3)

    def test_ai_output_with_invented_number_is_rejected(self):
        city = self._rich_city()
        benchmarks = summary.compute_benchmarks(city, [city, self.cities[1], self.cities[2]])
        normalized = summary.normalize_summary(
            {
                "strength_sentence": "New York combines population of 8,258,035 and median household income of $79,713, supporting renter demand.",
                "weakness_sentence": "The main underwriting concern is climate-risk score of 999, rated Very High.",
                "comparison_sentence": "Overall, New York presents a mixed preliminary investment opportunity.",
            },
            city,
            benchmarks,
        )
        self.assertNotIn("999", summary.combined_summary(normalized))

    def test_generic_new_york_style_output_is_rejected(self):
        city = self._rich_city()
        benchmarks = summary.compute_benchmarks(city, [city, self.cities[1], self.cities[2]])
        generic = {
            "strength_sentence": "New York, NY presents investable signals from available economic and household metrics that support additional screening.",
            "weakness_sentence": "The main tradeoffs are concentrated in risk and data-completeness factors that require property-level verification.",
            "comparison_sentence": "Overall, New York, NY appears selectively attractive for further underwriting with disciplined due diligence.",
        }
        normalized = summary.normalize_summary(generic, city, benchmarks)
        combined = summary.combined_summary(normalized).lower()
        self.assertNotIn("presents investable signals", combined)
        self.assertNotIn("available economic and household metrics", combined)
        self.assertNotIn("risk and data-completeness factors", combined)
        self.assertGreaterEqual(len(summary.matched_fact_indexes(combined, summary.build_approved_city_facts(city, benchmarks)["facts"])), 3)

    def test_ai_output_with_sufficient_approved_facts_is_accepted(self):
        city = self._rich_city()
        benchmarks = summary.compute_benchmarks(city, [city, self.cities[1], self.cities[2]])
        candidate = {
            "strength_sentence": "New York combines recent employment growth of 4.00%, median household income of $79,713, and population of 8,258,035, supporting renter demand and household stability.",
            "weakness_sentence": "The main underwriting concern is climate-risk score of 86, rated Very High, which may increase insurance and resilience costs.",
            "comparison_sentence": "Overall, New York presents a mixed preliminary investment opportunity because demand scale is strong but climate exposure remains elevated.",
        }
        normalized = summary.normalize_summary(candidate, city, benchmarks)
        self.assertEqual(summary.combined_summary(normalized), summary.combined_summary({k: summary.one_sentence(v) for k, v in candidate.items()}))

    def test_missing_data_language_only_when_fields_are_missing(self):
        city = self._rich_city()
        benchmarks = summary.compute_benchmarks(city, [city, self.cities[1], self.cities[2]])
        fallback_text = summary.combined_summary(summary.fallback_summary(city, benchmarks)).lower()
        self.assertNotIn("currently unavailable", fallback_text)

        sparse = {
            **self.cities[3],
            "city": "Sparse",
            "state": "SS",
            "display_name": "Sparse, SS",
        }
        sparse_bench = summary.compute_benchmarks(sparse, self.cities)
        sparse_text = summary.combined_summary(summary.fallback_summary(sparse, sparse_bench)).lower()
        self.assertIn("limited", sparse_text)

    def test_fallback_contains_three_or_more_real_facts_when_available(self):
        city = self._rich_city()
        benchmarks = summary.compute_benchmarks(city, [city, self.cities[1], self.cities[2]])
        fallback_struct = summary.fallback_summary(city, benchmarks)
        fallback_text = summary.combined_summary(fallback_struct)
        facts = summary.build_approved_city_facts(city, benchmarks)["facts"]
        self.assertGreaterEqual(len(summary.matched_fact_indexes(fallback_text, facts)), 3)

    def test_fallback_avoids_removed_generic_phrases(self):
        city = self._rich_city()
        benchmarks = summary.compute_benchmarks(city, [city, self.cities[1], self.cities[2]])
        text = summary.combined_summary(summary.fallback_summary(city, benchmarks)).lower()
        self.assertNotIn("presents investable signals", text)
        self.assertNotIn("available economic and household metrics", text)
        self.assertNotIn("risk and data-completeness factors", text)
        self.assertNotIn("requires disciplined due diligence", text)

    def test_unsupported_rent_cap_and_vacancy_claims_are_rejected(self):
        city = self._rich_city()
        benchmarks = summary.compute_benchmarks(city, [city, self.cities[1], self.cities[2]])
        normalized = summary.normalize_summary(
            {
                "strength_sentence": "New York combines recent employment growth of 4.00% and median household income of $79,713.",
                "weakness_sentence": "Rent growth, cap rates, and vacancy trends are favorable.",
                "comparison_sentence": "Overall, New York appears attractive for further investment underwriting.",
            },
            city,
            benchmarks,
        )
        combined = summary.combined_summary(normalized).lower()
        self.assertNotIn("rent growth", combined)
        self.assertNotIn("cap rates", combined)
        self.assertNotIn("vacancy", combined)

    def test_ai_disabled_mode_ignores_seeded_cached_summary(self):
        app = Flask(__name__)
        app.config.update(
            FIRE_METRICS_AI_SUMMARIES_ENABLED=False,
            FIRE_METRICS_SUMMARY_MODEL="model-a",
            OPENAI_API_KEY="test-key",
        )

        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="fire-metrics-ai-disabled-") as tmp:
                os.environ["FIRE_METRICS_DB_PATH"] = os.path.join(tmp, "audit.db")
                with db_module.get_connection() as conn:
                    self._seed_cities_table(conn)
                    selected = db_module.fetch_city_by_identity(conn, "Alpha", "AA")
                    all_cities = db_module.fetch_all_included_cities(conn)
                    benchmarks = summary.compute_benchmarks(selected, all_cities)
                    fp = summary.build_fingerprint(
                        summary.fingerprint_payload(
                            selected_city=selected,
                            benchmarks=benchmarks,
                            model_name="model-a",
                            refresh_last_at=None,
                        )
                    )
                    db_module.upsert_city_summary_cache(
                        conn,
                        {
                            "city": "Alpha",
                            "state": "AA",
                            "city_key": "Alpha|AA",
                            "data_fingerprint": fp,
                            "model_name": "model-a",
                            "prompt_version": summary.PROMPT_VERSION,
                            "summary_text": "Seeded cached AI summary. Seeded weakness. Seeded comparison.",
                            "strength_sentence": "Seeded cached AI summary.",
                            "weakness_sentence": "Seeded weakness.",
                            "comparison_sentence": "Seeded comparison.",
                            "generated_at": "2026-07-22T00:00:00+00:00",
                        },
                    )

                with patch.object(fire_metrics_routes.ai_summary, "openai_summary", side_effect=AssertionError("openai_summary should not be called")):
                    status_code, payload = self._call_city_summary(app)

                self.assertEqual(status_code, 200)
                self.assertEqual(payload["source"], "fallback")
                self.assertFalse(payload["cached"])
                self.assertNotEqual(payload["summary"], "Seeded cached AI summary. Seeded weakness. Seeded comparison.")
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_ai_enabled_cache_hit_still_works(self):
        app = Flask(__name__)
        app.config.update(
            FIRE_METRICS_AI_SUMMARIES_ENABLED=True,
            FIRE_METRICS_SUMMARY_MODEL="model-a",
            OPENAI_API_KEY="",
        )

        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="fire-metrics-ai-enabled-") as tmp:
                os.environ["FIRE_METRICS_DB_PATH"] = os.path.join(tmp, "audit.db")
                with db_module.get_connection() as conn:
                    self._seed_cities_table(conn)
                    selected = db_module.fetch_city_by_identity(conn, "Alpha", "AA")
                    all_cities = db_module.fetch_all_included_cities(conn)
                    benchmarks = summary.compute_benchmarks(selected, all_cities)
                    fp = summary.build_fingerprint(
                        summary.fingerprint_payload(
                            selected_city=selected,
                            benchmarks=benchmarks,
                            model_name="model-a",
                            refresh_last_at=None,
                        )
                    )
                    db_module.upsert_city_summary_cache(
                        conn,
                        {
                            "city": "Alpha",
                            "state": "AA",
                            "city_key": "Alpha|AA",
                            "data_fingerprint": fp,
                            "model_name": "model-a",
                            "prompt_version": summary.PROMPT_VERSION,
                            "summary_text": "Cached strength sentence. Cached weakness sentence. Cached comparison sentence.",
                            "strength_sentence": "Cached strength sentence.",
                            "weakness_sentence": "Cached weakness sentence.",
                            "comparison_sentence": "Cached comparison sentence.",
                            "generated_at": "2026-07-22T00:00:00+00:00",
                        },
                    )

                with patch.object(fire_metrics_routes.ai_summary, "openai_summary", side_effect=AssertionError("openai_summary should not be called on cache hit")):
                    status_code, payload = self._call_city_summary(app)

                self.assertEqual(status_code, 200)
                self.assertEqual(payload["source"], "cache")
                self.assertTrue(payload["cached"])
                self.assertEqual(payload["summary"], "Cached strength sentence. Cached weakness sentence. Cached comparison sentence.")
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_city_summary_succeeds_when_db_city_has_census_suffix(self):
        app = Flask(__name__)
        app.config.update(
            FIRE_METRICS_AI_SUMMARIES_ENABLED=False,
            FIRE_METRICS_SUMMARY_MODEL="",
            OPENAI_API_KEY="",
        )

        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="fire-metrics-la-suffix-") as tmp:
                os.environ["FIRE_METRICS_DB_PATH"] = os.path.join(tmp, "audit.db")
                with db_module.get_connection() as conn:
                    self._seed_single_city(
                        conn,
                        city="Los Angeles city",
                        state="CA",
                        display_name="Los Angeles, CA",
                    )

                status_code, payload = self._call_city_summary(app, city="Los Angeles", state="CA")

                self.assertEqual(status_code, 200)
                self.assertEqual(payload["status"], "ready")
                self.assertEqual(payload["source"], "fallback")
                self.assertFalse(payload["cached"])
                self.assertEqual(summary.count_sentences(payload["summary"]), 3)
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_stable_city_key_lookup_overrides_cleaned_city_name(self):
        app = Flask(__name__)
        app.config.update(
            FIRE_METRICS_AI_SUMMARIES_ENABLED=False,
            FIRE_METRICS_SUMMARY_MODEL="",
            OPENAI_API_KEY="",
        )

        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="fire-metrics-city-key-") as tmp:
                os.environ["FIRE_METRICS_DB_PATH"] = os.path.join(tmp, "audit.db")
                with db_module.get_connection() as conn:
                    self._seed_single_city(
                        conn,
                        city="Los Angeles city",
                        state="CA",
                        display_name="Los Angeles, CA",
                    )

                status_code, payload = self._call_city_summary(
                    app,
                    city="Los Angeles",
                    state="CA",
                    city_key="Los Angeles city|CA",
                )

                self.assertEqual(status_code, 200)
                self.assertEqual(payload["status"], "ready")
                self.assertEqual(payload["source"], "fallback")
                self.assertEqual(payload["city_key"], "Los Angeles city|CA")
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_missing_openai_configuration_does_not_block_fallback(self):
        app = Flask(__name__)
        app.config.update(
            FIRE_METRICS_AI_SUMMARIES_ENABLED=True,
            FIRE_METRICS_SUMMARY_MODEL="model-a",
            OPENAI_API_KEY="",
        )

        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="fire-metrics-no-openai-") as tmp:
                os.environ["FIRE_METRICS_DB_PATH"] = os.path.join(tmp, "audit.db")
                with db_module.get_connection() as conn:
                    self._seed_single_city(
                        conn,
                        city="Los Angeles city",
                        state="CA",
                        display_name="Los Angeles, CA",
                    )

                status_code, payload = self._call_city_summary(app, city="Los Angeles", state="CA")

                self.assertEqual(status_code, 200)
                self.assertEqual(payload["status"], "ready")
                self.assertEqual(payload["source"], "fallback")
                self.assertFalse(payload["cached"])
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_cache_read_failure_does_not_block_fallback(self):
        app = Flask(__name__)
        app.config.update(
            FIRE_METRICS_AI_SUMMARIES_ENABLED=True,
            FIRE_METRICS_SUMMARY_MODEL="model-a",
            OPENAI_API_KEY="",
        )

        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="fire-metrics-cache-read-fail-") as tmp:
                os.environ["FIRE_METRICS_DB_PATH"] = os.path.join(tmp, "audit.db")
                with db_module.get_connection() as conn:
                    self._seed_single_city(
                        conn,
                        city="Los Angeles city",
                        state="CA",
                        display_name="Los Angeles, CA",
                    )

                with patch.object(db_module, "fetch_cached_city_summary", side_effect=sqlite3.OperationalError("no such table")):
                    status_code, payload = self._call_city_summary(app, city="Los Angeles", state="CA")

                self.assertEqual(status_code, 200)
                self.assertEqual(payload["status"], "ready")
                self.assertEqual(payload["source"], "fallback")
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_unknown_city_returns_controlled_json_error(self):
        app = Flask(__name__)
        app.config.update(
            FIRE_METRICS_AI_SUMMARIES_ENABLED=False,
            FIRE_METRICS_SUMMARY_MODEL="",
            OPENAI_API_KEY="",
        )

        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="fire-metrics-unknown-city-") as tmp:
                os.environ["FIRE_METRICS_DB_PATH"] = os.path.join(tmp, "audit.db")
                with db_module.get_connection() as conn:
                    self._seed_single_city(
                        conn,
                        city="Los Angeles city",
                        state="CA",
                        display_name="Los Angeles, CA",
                    )

                status_code, payload = self._call_city_summary(app, city="Unknownville", state="CA")

                self.assertEqual(status_code, 404)
                self.assertEqual(payload["status"], "error")
                self.assertEqual(payload["error_code"], "city_not_found")
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_top_cities_lowest_crime_sorts_ascending(self):
        app = Flask(__name__)
        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="fire-metrics-top-crime-") as tmp:
                os.environ["FIRE_METRICS_DB_PATH"] = os.path.join(tmp, "audit.db")
                with db_module.get_connection() as conn:
                    self._seed_top_cities_fixture(conn)
                status_code, payload = self._call_top_cities(app, "crime_index_score")
                self.assertEqual(status_code, 200)
                self.assertEqual(payload["direction"], "asc")
                self._assert_metric_sorted(payload, "crime_index_score", "asc")
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_top_cities_lowest_density_adjusted_crime_sorts_ascending(self):
        app = Flask(__name__)
        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="fire-metrics-top-density-") as tmp:
                os.environ["FIRE_METRICS_DB_PATH"] = os.path.join(tmp, "audit.db")
                with db_module.get_connection() as conn:
                    self._seed_top_cities_fixture(conn)
                status_code, payload = self._call_top_cities(app, "density_adjusted_crime_score")
                self.assertEqual(status_code, 200)
                self.assertEqual(payload["direction"], "asc")
                self._assert_metric_sorted(payload, "density_adjusted_crime_score", "asc")
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_top_cities_highest_job_growth_sorts_descending(self):
        app = Flask(__name__)
        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="fire-metrics-top-jobs-") as tmp:
                os.environ["FIRE_METRICS_DB_PATH"] = os.path.join(tmp, "audit.db")
                with db_module.get_connection() as conn:
                    self._seed_top_cities_fixture(conn)
                status_code, payload = self._call_top_cities(app, "employment_growth_recent")
                self.assertEqual(status_code, 200)
                self.assertEqual(payload["direction"], "desc")
                self._assert_metric_sorted(payload, "employment_growth_recent", "desc")
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_top_cities_highest_population_growth_sorts_descending(self):
        app = Flask(__name__)
        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="fire-metrics-top-pop-") as tmp:
                os.environ["FIRE_METRICS_DB_PATH"] = os.path.join(tmp, "audit.db")
                with db_module.get_connection() as conn:
                    self._seed_top_cities_fixture(conn)
                status_code, payload = self._call_top_cities(app, "population_growth_recent")
                self.assertEqual(status_code, 200)
                self.assertEqual(payload["direction"], "desc")
                self._assert_metric_sorted(payload, "population_growth_recent", "desc")
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_top_cities_highest_income_growth_sorts_descending(self):
        app = Flask(__name__)
        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="fire-metrics-top-income-") as tmp:
                os.environ["FIRE_METRICS_DB_PATH"] = os.path.join(tmp, "audit.db")
                with db_module.get_connection() as conn:
                    self._seed_top_cities_fixture(conn)
                status_code, payload = self._call_top_cities(app, "median_income_growth_recent")
                self.assertEqual(status_code, 200)
                self.assertEqual(payload["direction"], "desc")
                self._assert_metric_sorted(payload, "median_income_growth_recent", "desc")
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_top_cities_highest_home_value_growth_sorts_descending(self):
        app = Flask(__name__)
        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="fire-metrics-top-home-") as tmp:
                os.environ["FIRE_METRICS_DB_PATH"] = os.path.join(tmp, "audit.db")
                with db_module.get_connection() as conn:
                    self._seed_top_cities_fixture(conn)
                status_code, payload = self._call_top_cities(app, "median_home_value_growth_recent")
                self.assertEqual(status_code, 200)
                self.assertEqual(payload["direction"], "desc")
                self._assert_metric_sorted(payload, "median_home_value_growth_recent", "desc")
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_top_cities_lowest_climate_risk_sorts_ascending(self):
        app = Flask(__name__)
        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="fire-metrics-top-climate-") as tmp:
                os.environ["FIRE_METRICS_DB_PATH"] = os.path.join(tmp, "audit.db")
                with db_module.get_connection() as conn:
                    self._seed_top_cities_fixture(conn)
                status_code, payload = self._call_top_cities(app, "climate_risk_score")
                self.assertEqual(status_code, 200)
                self.assertEqual(payload["direction"], "asc")
                self._assert_metric_sorted(payload, "climate_risk_score", "asc")
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_top_cities_excludes_null_values(self):
        app = Flask(__name__)
        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="fire-metrics-top-nulls-") as tmp:
                os.environ["FIRE_METRICS_DB_PATH"] = os.path.join(tmp, "audit.db")
                with db_module.get_connection() as conn:
                    self._seed_top_cities_fixture(conn)
                status_code, payload = self._call_top_cities(app, "crime_index_score")
                self.assertEqual(status_code, 200)
                self.assertTrue(all(city.get("crime_index_score") is not None for city in payload["cities"]))
                self.assertNotIn("Monroe", [city["city"] for city in payload["cities"]])
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_top_cities_zero_and_negative_growth_values_are_eligible(self):
        app = Flask(__name__)
        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="fire-metrics-top-negative-") as tmp:
                os.environ["FIRE_METRICS_DB_PATH"] = os.path.join(tmp, "audit.db")
                with db_module.get_connection() as conn:
                    minimal_rows = [
                        ("ZeroTown", "ZT", 50000, 0.0),
                        ("NegativeTown", "NT", 60000, -0.01),
                        ("PositiveTown", "PT", 70000, 0.02),
                    ]
                    for city, state, population, growth in minimal_rows:
                        display = f"{city}, {state}"
                        conn.execute(
                            """
                            INSERT INTO cities (
                                city, state, display_name, normalized_city, normalized_display_name, search_key,
                                include_flag, population_current, employment_growth_recent
                            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                            """,
                            (
                                city,
                                state,
                                display,
                                city.lower(),
                                display.lower(),
                                f"{city.lower()} {state.lower()}",
                                population,
                                growth,
                            ),
                        )
                    conn.commit()
                status_code, payload = self._call_top_cities(app, "employment_growth_recent", limit=10)
                self.assertEqual(status_code, 200)
                values = [city.get("employment_growth_recent") for city in payload["cities"]]
                self.assertIn(0.0, values)
                self.assertIn(-0.01, values)
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_top_cities_limit_is_capped_to_ten(self):
        app = Flask(__name__)
        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="fire-metrics-top-limit-") as tmp:
                os.environ["FIRE_METRICS_DB_PATH"] = os.path.join(tmp, "audit.db")
                with db_module.get_connection() as conn:
                    self._seed_top_cities_fixture(conn)
                status_code, payload = self._call_top_cities(app, "crime_index_score", limit=999)
                self.assertEqual(status_code, 200)
                self.assertEqual(payload["city_count"], 10)
                self.assertLessEqual(len(payload["cities"]), 10)
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_top_cities_returns_unique_city_rows(self):
        app = Flask(__name__)
        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="fire-metrics-top-unique-") as tmp:
                os.environ["FIRE_METRICS_DB_PATH"] = os.path.join(tmp, "audit.db")
                with db_module.get_connection() as conn:
                    self._seed_top_cities_fixture(conn)
                status_code, payload = self._call_top_cities(app, "population_growth_recent", limit=10)
                self.assertEqual(status_code, 200)
                keys = [city.get("city_key") for city in payload["cities"]]
                self.assertEqual(len(keys), len(set(keys)))
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_top_cities_tie_breaking_is_deterministic(self):
        app = Flask(__name__)
        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="fire-metrics-top-ties-") as tmp:
                os.environ["FIRE_METRICS_DB_PATH"] = os.path.join(tmp, "audit.db")
                with db_module.get_connection() as conn:
                    self._seed_top_cities_fixture(conn)
                status_code, payload = self._call_top_cities(app, "crime_index_score", limit=10)
                self.assertEqual(status_code, 200)
                keys = [city.get("city_key") for city in payload["cities"]]
                # Arbor/Benton/Cedar share key crime values; Cedar has higher population than Benton.
                self.assertLess(keys.index("Cedar|AA"), keys.index("Benton|AA"))
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_top_cities_invalid_metric_is_rejected(self):
        app = Flask(__name__)
        status_code, payload = self._call_top_cities(app, "landlord_friendliness_score")
        self.assertEqual(status_code, 400)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["error_code"], "invalid_metric")

    def test_top_cities_sql_injection_like_metric_is_rejected(self):
        app = Flask(__name__)
        metric = "crime_index_score DESC; DROP TABLE cities; --"
        status_code, payload = self._call_top_cities(app, metric)
        self.assertEqual(status_code, 400)
        self.assertEqual(payload["error_code"], "invalid_metric")

    def test_top_cities_payload_includes_city_key_and_coordinates(self):
        app = Flask(__name__)
        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            with tempfile.TemporaryDirectory(prefix="fire-metrics-top-payload-") as tmp:
                os.environ["FIRE_METRICS_DB_PATH"] = os.path.join(tmp, "audit.db")
                with db_module.get_connection() as conn:
                    self._seed_top_cities_fixture(conn)
                status_code, payload = self._call_top_cities(app, "climate_risk_score", limit=3)
                self.assertEqual(status_code, 200)
                self.assertEqual(payload["status"], "ready")
                self.assertEqual(payload["metric"], "climate_risk_score")
                self.assertEqual(payload["direction"], "asc")
                self.assertEqual(payload["city_count"], 3)
                first = payload["cities"][0]
                self.assertIn("city_key", first)
                self.assertIn("latitude", first)
                self.assertIn("longitude", first)
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_top_cities_route_is_login_protected(self):
        self.assertTrue(hasattr(top_cities, "__wrapped__"))
        self.assertNotEqual(top_cities, top_cities.__wrapped__)

    def test_proper_city_name_suffixes_are_preserved_in_lookup_normalization(self):
        self.assertEqual(db_module._strip_trailing_census_suffix("oklahoma city"), "oklahoma city")
        self.assertEqual(db_module._strip_trailing_census_suffix("kansas city"), "kansas city")
        self.assertEqual(db_module._strip_trailing_census_suffix("salt lake city"), "salt lake city")
        self.assertEqual(db_module._strip_trailing_census_suffix("carson city"), "carson city")

    def test_frontend_overview_renders_via_textcontent(self):
        template = Path("templates/tools/fire_metrics.html").read_text(encoding="utf-8")
        self.assertIn("aiOverviewBody.textContent = text;", template)
        self.assertIn("aiOverviewMeta.textContent = text;", template)
        self.assertIn("city_key: city.city_key || \"\"", template)
        self.assertIn("selectCurrentSearchCity", template)
        self.assertIn("fire-city-chip-list", template)
        self.assertIn("fire-city-chip-select", template)
        self.assertIn("fire-city-chip-remove", template)

    def test_frontend_quick_ranking_and_city_analytics_hooks_present(self):
        template = Path("templates/tools/fire_metrics.html").read_text(encoding="utf-8")
        self.assertIn("Quick City Rankings", template)
        self.assertIn("Load the 10 strongest tracked cities for a selected metric.", template)
        self.assertIn("quick-ranking-btn", template)
        self.assertIn("performRankingShortcut", template)
        self.assertIn("rankingRequestSequence", template)
        self.assertIn("rankingRequestController", template)
        self.assertIn("setActiveRankingMetric", template)
        self.assertIn("setCurrentSearchCities", template)
        self.assertIn("appendToCurrentSearchCities", template)
        self.assertIn("removeSearchCityByKey", template)
        self.assertIn("selectCurrentSearchCity", template)
        self.assertIn("clearSearchedCitiesWorkspace", template)
        self.assertIn("ensureCityAnalyticsCity", template)
        self.assertNotIn("mergeCitiesIntoCityAnalytics", template)
        self.assertIn("city_key", template)
        self.assertIn("Clear City Analytics", template)
        self.assertIn("Add to City Analytics", template)
        self.assertIn("Already in City Analytics", template)
        self.assertIn("City Analytics", template)
        self.assertIn("topCitiesUrl", template)
        self.assertIn("Loading", template)
        self.assertIn("aria-current", template)
        self.assertIn('setAttribute("aria-pressed"', template)
        self.assertIn("Remove", template)
        self.assertIn("fire-analytics-row-active", template)
        self.assertIn('data-city-key="', template)
        self.assertIn("scrollAnalyticsRowIntoView", template)
        self.assertIn("openCurrentCityPreview", template)
        self.assertIn("flushPendingCurrentCityPreview", template)
        stylesheet = Path("static/style.css").read_text(encoding="utf-8")
        self.assertIn(".quick-ranking-btn.active", stylesheet)
        self.assertIn('aria-pressed="true"', stylesheet)

    def test_frontend_copy_uses_fire_metrics_plural_and_city_analytics(self):
        template = Path("templates/tools/fire_metrics.html").read_text(encoding="utf-8")
        self.assertIn("FIRE Metrics", template)
        self.assertIn("City Analytics", template)
        self.assertIn("Add to City Analytics", template)
        self.assertNotIn(">FIRE Metric<", template)
        self.assertNotIn("Add to Comparison", template)
        self.assertIn("Clear searched cities", template)

    def test_frontend_search_and_multi_search_hooks_remain_present(self):
        template = Path("templates/tools/fire_metrics.html").read_text(encoding="utf-8")
        self.assertIn("splitSearchQueries", template)
        self.assertIn("performSingleSearch", template)
        self.assertIn("performMultiSearch", template)
        self.assertIn("performRankingShortcut", template)
        self.assertIn("summaryRequestSequence", template)
        self.assertIn("_strip_trailing_census_suffix", Path("fire_metrics/fire_metrics_updater/db.py").read_text(encoding="utf-8"))

    def test_frontend_old_searched_city_tabs_and_dropdown_are_removed(self):
        template = Path("templates/tools/fire_metrics.html").read_text(encoding="utf-8")
        self.assertNotIn("Searched Cities", template)
        self.assertNotIn("fire-searched-city-select", template)
        self.assertNotIn("fire-searched-cities-tabs", template)
        self.assertNotIn('role="tablist"', template)

    def test_frontend_chip_picker_has_single_text_input_and_live_regions(self):
        template = Path("templates/tools/fire_metrics.html").read_text(encoding="utf-8")
        self.assertEqual(template.count('id="fire-search-input"'), 1)
        self.assertIn('id="fire-city-picker-status"', template)
        self.assertIn('aria-live="polite"', template)
        self.assertIn('id="quick-ranking-status"', template)

    def test_frontend_city_chip_click_and_preview_sync_hooks_present(self):
        template = Path("templates/tools/fire_metrics.html").read_text(encoding="utf-8")
        self.assertIn("ensureAnalyticsRow: true", template)
        self.assertIn("openMapPreview: true", template)
        self.assertIn("scrollAnalyticsRow: true", template)
        self.assertIn("requestOverview: true", template)
        self.assertIn("selectCurrentSearchCity(key, {", template)
        self.assertIn("marker.addListener(\"click\", () => {", template)
        self.assertIn("selectCurrentSearchCity(stableCityKey(city)", template)
        self.assertIn("setActiveRankingMetric(\"\")", template)

    def test_frontend_map_first_layout_order_and_removed_scorecard_grid(self):
        template = Path("templates/tools/fire_metrics.html").read_text(encoding="utf-8")
        self.assertNotIn('id="fire-city-dashboard"', template)
        self.assertNotIn('id="metric-pop-current"', template)
        self.assertNotIn('id="metric-income-current"', template)
        self.assertNotIn('id="metric-home-current"', template)
        self.assertNotIn('id="metric-employment-current"', template)
        self.assertNotIn('id="metric-climate-score"', template)
        self.assertNotIn('id="metric-crime-score"', template)
        self.assertNotIn('id="metric-density-score"', template)
        self.assertNotIn('id="metric-landlord-score"', template)

        rankings_idx = template.index('id="quick-ranking-controls"')
        map_idx = template.index('id="fire-map-panel"')
        analytics_idx = template.index('id="comparison-table-wrap"')
        overview_idx = template.index('id="fire-ai-overview-card"')
        self.assertLess(rankings_idx, map_idx)
        self.assertLess(map_idx, analytics_idx)
        self.assertLess(analytics_idx, overview_idx)

    def test_frontend_analytics_row_and_scroll_hooks_present(self):
        template = Path("templates/tools/fire_metrics.html").read_text(encoding="utf-8")
        self.assertIn("scrollIntoView({ behavior, block: \"nearest\", inline: \"nearest\" })", template)
        self.assertIn("prefersReducedMotion", template)
        self.assertIn("cssEscapeValue", template)
        self.assertIn("comparisonWrap.getBoundingClientRect()", template)
        self.assertIn("aria-current", template)
        self.assertIn("fire-analytics-row-active", Path("static/style.css").read_text(encoding="utf-8"))

    def test_frontend_marker_preview_helpers_and_shared_state_present(self):
        template = Path("templates/tools/fire_metrics.html").read_text(encoding="utf-8")
        self.assertIn("const markerPreviewState =", template)
        self.assertIn("function markerInfoContent(city)", template)
        self.assertIn("function previewMetricRow(label, value, growth)", template)
        self.assertIn("Density-Adj. Crime", template)
        self.assertIn("function scheduleHoverOpen", template)
        self.assertIn("function scheduleHoverClose", template)
        self.assertIn("function openMarkerPreview", template)
        self.assertIn("function closeMarkerPreview", template)
        self.assertIn("supportsHoverPreview", template)
        self.assertIn("markerContent.addEventListener(\"mouseenter\"", template)
        self.assertIn("markerContent.addEventListener(\"focus\"", template)

    def test_frontend_map_dominant_css_sizing_rules_present(self):
        stylesheet = Path("static/style.css").read_text(encoding="utf-8")
        self.assertIn("height: clamp(520px, 65vh, 720px);", stylesheet)
        self.assertIn("min-height: 520px;", stylesheet)
        self.assertIn("height: clamp(380px, 58vh, 500px);", stylesheet)
        self.assertIn(".fire-map-preview-grid", stylesheet)
        self.assertIn(".fire-map-preview-row", stylesheet)
        self.assertIn(".fire-map-preview-growth", stylesheet)

    def test_model_output_html_is_sanitized(self):
        normalized = summary.normalize_summary(
            {
                "strength_sentence": "<b>Population growth is solid.</b>",
                "weakness_sentence": "<script>alert(1)</script>Crime risk is elevated.",
                "comparison_sentence": "Score is above average.",
            },
            self.cities[0],
            summary.compute_benchmarks(self.cities[0], self.cities),
        )
        self.assertNotIn("<", normalized["strength_sentence"])
        self.assertNotIn(">", normalized["weakness_sentence"])

    def test_combined_summary_is_exactly_three_sentences(self):
        combined = summary.combined_summary(
            {
                "strength_sentence": "Strengths are improving.",
                "weakness_sentence": "Risks remain present.",
                "comparison_sentence": "Overall score is near average.",
            }
        )
        self.assertEqual(summary.count_sentences(combined), 3)


if __name__ == "__main__":
    unittest.main()
