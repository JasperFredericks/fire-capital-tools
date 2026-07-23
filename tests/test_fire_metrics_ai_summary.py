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

    def test_fallback_uses_favorable_metric_wording(self):
        structured = summary.fallback_summary(
            self.cities[0],
            {
                "strength_candidates": [
                    {
                        "field": "population_growth_recent",
                        "label": "Population growth",
                        "favorable_when_higher": True,
                        "directional_percentile": 83.2,
                    }
                ],
                "weakness_candidates": [],
                "selected_overall_score": 70.0,
                "tracked_city_average": 60.0,
                "tracked_city_count": 3,
                "selected_percentile": 72.0,
            },
        )
        self.assertIn("Population growth at approximately the 83rd percentile", structured["strength_sentence"])

    def test_fallback_uses_unfavorable_metric_plain_language(self):
        structured = summary.fallback_summary(
            self.cities[0],
            {
                "strength_candidates": [
                    {
                        "field": "climate_risk_score",
                        "label": "Climate risk",
                        "favorable_when_higher": False,
                        "raw_percentile": 17.0,
                        "directional_percentile": 83.0,
                    }
                ],
                "weakness_candidates": [],
                "selected_overall_score": 70.0,
                "tracked_city_average": 60.0,
                "tracked_city_count": 3,
                "selected_percentile": 72.0,
            },
        )
        self.assertIn("lower climate risk than roughly 83% of tracked cities", structured["strength_sentence"])
        self.assertNotIn("Climate risk at approximately the 83rd percentile", structured["strength_sentence"])

    def test_two_strengths_joined_with_and(self):
        structured = summary.fallback_summary(
            self.cities[0],
            {
                "strength_candidates": [
                    {
                        "field": "population_growth_recent",
                        "label": "Population growth",
                        "favorable_when_higher": True,
                        "directional_percentile": 83.0,
                    },
                    {
                        "field": "climate_risk_score",
                        "label": "Climate risk",
                        "favorable_when_higher": False,
                        "directional_percentile": 83.0,
                    },
                ],
                "weakness_candidates": [],
                "selected_overall_score": 70.0,
                "tracked_city_average": 60.0,
                "tracked_city_count": 3,
                "selected_percentile": 72.0,
            },
        )
        self.assertIn(" and ", structured["strength_sentence"])
        self.assertNotIn(", lower climate risk than roughly", structured["strength_sentence"])

    def test_one_strength_uses_singular_grammar(self):
        structured = summary.fallback_summary(
            self.cities[0],
            {
                "strength_candidates": [
                    {
                        "field": "population_growth_recent",
                        "label": "Population growth",
                        "favorable_when_higher": True,
                        "directional_percentile": 83.0,
                    }
                ],
                "weakness_candidates": [],
                "selected_overall_score": 70.0,
                "tracked_city_average": 60.0,
                "tracked_city_count": 3,
                "selected_percentile": 72.0,
            },
        )
        self.assertIn("The strongest relative signal is", structured["strength_sentence"])
        self.assertNotIn("The strongest relative signals are", structured["strength_sentence"])

    def test_two_weaknesses_use_plural_grammar(self):
        structured = summary.fallback_summary(
            self.cities[0],
            {
                "strength_candidates": [],
                "weakness_candidates": [
                    {
                        "field": "crime_index_score",
                        "label": "Crime index",
                        "favorable_when_higher": False,
                        "directional_percentile": 22.0,
                    },
                    {
                        "field": "density_adjusted_crime_score",
                        "label": "Density-adjusted crime",
                        "favorable_when_higher": False,
                        "directional_percentile": 35.0,
                    },
                ],
                "selected_overall_score": 70.0,
                "tracked_city_average": 60.0,
                "tracked_city_count": 3,
                "selected_percentile": 72.0,
            },
        )
        self.assertIn("The main weaknesses are", structured["weakness_sentence"])
        self.assertIn(" and ", structured["weakness_sentence"])

    def test_one_weakness_uses_singular_grammar(self):
        structured = summary.fallback_summary(
            self.cities[0],
            {
                "strength_candidates": [],
                "weakness_candidates": [
                    {
                        "field": "crime_index_score",
                        "label": "Crime index",
                        "favorable_when_higher": False,
                        "directional_percentile": 22.0,
                    }
                ],
                "selected_overall_score": 70.0,
                "tracked_city_average": 60.0,
                "tracked_city_count": 3,
                "selected_percentile": 72.0,
            },
        )
        self.assertIn("The main weakness is", structured["weakness_sentence"])
        self.assertNotIn("The main weaknesses are", structured["weakness_sentence"])

    def test_no_duplicate_tracked_city_phrase_in_strength_sentence(self):
        structured = summary.fallback_summary(
            self.cities[0],
            {
                "strength_candidates": [
                    {
                        "field": "climate_risk_score",
                        "label": "Climate risk",
                        "favorable_when_higher": False,
                        "directional_percentile": 83.0,
                    }
                ],
                "weakness_candidates": [],
                "selected_overall_score": 70.0,
                "tracked_city_average": 60.0,
                "tracked_city_count": 3,
                "selected_percentile": 72.0,
            },
        )
        self.assertNotIn("tracked cities among tracked cities", structured["strength_sentence"])

    def test_no_duplicate_tracked_city_phrase_in_weakness_sentence(self):
        structured = summary.fallback_summary(
            self.cities[0],
            {
                "strength_candidates": [],
                "weakness_candidates": [
                    {
                        "field": "crime_index_score",
                        "label": "Crime index",
                        "favorable_when_higher": False,
                        "directional_percentile": 22.0,
                    }
                ],
                "selected_overall_score": 70.0,
                "tracked_city_average": 60.0,
                "tracked_city_count": 3,
                "selected_percentile": 72.0,
            },
        )
        self.assertNotIn("tracked cities relative to tracked cities", structured["weakness_sentence"])

    def test_fallback_summary_remains_exactly_three_sentences(self):
        structured = summary.fallback_summary(
            self.cities[0],
            {
                "strength_candidates": [
                    {
                        "field": "population_growth_recent",
                        "label": "Population growth",
                        "favorable_when_higher": True,
                        "directional_percentile": 83.0,
                    },
                    {
                        "field": "climate_risk_score",
                        "label": "Climate risk",
                        "favorable_when_higher": False,
                        "directional_percentile": 83.0,
                    },
                ],
                "weakness_candidates": [
                    {
                        "field": "crime_index_score",
                        "label": "Crime index",
                        "favorable_when_higher": False,
                        "directional_percentile": 22.0,
                    }
                ],
                "selected_overall_score": 63.4,
                "tracked_city_average": 55.1,
                "tracked_city_count": 120,
                "selected_percentile": 68.0,
            },
        )
        text = summary.combined_summary(structured)
        self.assertEqual(summary.count_sentences(text), 3)

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
        self.assertIn("setCurrentSearchCities", template)
        self.assertIn("appendToCurrentSearchCities", template)
        self.assertIn("removeSearchCityByKey", template)
        self.assertIn("selectCurrentSearchCity", template)
        self.assertIn("clearSearchedCitiesWorkspace", template)
        self.assertIn("mergeCitiesIntoCityAnalytics", template)
        self.assertIn("city_key", template)
        self.assertIn("Clear City Analytics", template)
        self.assertIn("Add to City Analytics", template)
        self.assertIn("Already in City Analytics", template)
        self.assertIn("City Analytics", template)
        self.assertIn("topCitiesUrl", template)
        self.assertIn("Loading", template)
        self.assertIn("aria-current", template)
        self.assertIn("Remove", template)

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
