import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask

from fire_metrics.fire_metrics_updater import db as db_module
from tools import fire_metrics as fire_metrics_routes
from tools import fire_metrics_ai_summary as summary
from tools.fire_metrics import _summary_unavailable_response, city_summary


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

    def _call_city_summary(self, app: Flask, city: str = "Alpha", state: str = "AA"):
        with app.test_request_context(
            "/tools/fire-metrics/api/city-summary",
            method="POST",
            json={"city": city, "state": state},
        ):
            result = city_summary.__wrapped__()
        if isinstance(result, tuple):
            response, status_code = result
        else:
            response = result
            status_code = response.status_code
        return status_code, response.get_json()

    def test_tracked_city_average_excludes_null_overall_scores(self):
        bench = summary.compute_benchmarks(self.cities[0], self.cities)
        # Delta has no component values and should be excluded.
        self.assertEqual(bench["tracked_city_count"], 3)
        self.assertIsNotNone(bench["tracked_city_average"])

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
        )
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(summary.count_sentences(payload["summary"]), 3)

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
        self.assertIn("The strongest available signal is", structured["strength_sentence"])
        self.assertNotIn("The strongest available signals are", structured["strength_sentence"])

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
        self.assertIn("The biggest current weaknesses are", structured["weakness_sentence"])
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
        self.assertIn("The biggest current weakness is", structured["weakness_sentence"])
        self.assertNotIn("The biggest current weaknesses are", structured["weakness_sentence"])

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
