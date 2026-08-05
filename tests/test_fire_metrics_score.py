import sqlite3
import unittest

from fire_metrics.fire_metrics_updater import db as db_module
from tools import fire_metrics as routes
from tools import fire_metrics_score as score


def make_city(
    city: str,
    state: str,
    *,
    include_flag: int = 1,
    pop_growth: float | None = None,
    income_growth: float | None = None,
    home_growth: float | None = None,
    employment_growth: float | None = None,
    climate: float | None = None,
    crime_index: float | None = None,
    density_crime: float | None = None,
    landlord: float | None = None,
    population: float | None = None,
    city_key: str | None = None,
) -> dict:
    return {
        "city": city,
        "state": state,
        "city_key": city_key or f"{city}|{state}",
        "include_flag": include_flag,
        "population_growth_recent": pop_growth,
        "median_income_growth_recent": income_growth,
        "median_home_value_growth_recent": home_growth,
        "employment_growth_recent": employment_growth,
        "climate_risk_score": climate,
        "crime_index_score": crime_index,
        "density_adjusted_crime_score": density_crime,
        "landlord_friendliness_score": landlord,
        "population_current": population,
    }


class FireMetricsScoreUnitTests(unittest.TestCase):
    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(score.FIRE_SCORE_WEIGHTS.values()), 1.0)

    def test_percentile_min_max_middle(self):
        rows = [
            make_city("A", "AA", pop_growth=-0.02),
            make_city("B", "BB", pop_growth=0.00),
            make_city("C", "CC", pop_growth=0.03),
        ]
        mapping, n = score.percentile_map_for_field(rows, "population_growth_recent")
        self.assertEqual(n, 3)
        self.assertAlmostEqual(mapping["A|AA"], 0.0)
        self.assertAlmostEqual(mapping["B|BB"], 50.0)
        self.assertAlmostEqual(mapping["C|CC"], 100.0)

    def test_percentile_tie_average_rank(self):
        rows = [
            make_city("A", "AA", pop_growth=1.0),
            make_city("B", "BB", pop_growth=1.0),
            make_city("C", "CC", pop_growth=3.0),
            make_city("D", "DD", pop_growth=5.0),
        ]
        mapping, n = score.percentile_map_for_field(rows, "population_growth_recent")
        self.assertEqual(n, 4)
        self.assertAlmostEqual(mapping["A|AA"], 16.666666666666668)
        self.assertAlmostEqual(mapping["B|BB"], 16.666666666666668)
        self.assertAlmostEqual(mapping["C|CC"], 66.66666666666667)
        self.assertAlmostEqual(mapping["D|DD"], 100.0)

    def test_percentile_single_value_returns_fifty(self):
        rows = [make_city("A", "AA", pop_growth=1.0)]
        mapping, n = score.percentile_map_for_field(rows, "population_growth_recent")
        self.assertEqual(n, 1)
        self.assertEqual(mapping["A|AA"], 50.0)

    def test_percentile_excludes_nulls_and_keeps_zero_and_negative(self):
        rows = [
            make_city("A", "AA", pop_growth=-0.05),
            make_city("B", "BB", pop_growth=0.0),
            make_city("C", "CC", pop_growth=None),
        ]
        mapping, n = score.percentile_map_for_field(rows, "population_growth_recent")
        self.assertEqual(n, 2)
        self.assertIn("A|AA", mapping)
        self.assertIn("B|BB", mapping)
        self.assertNotIn("C|CC", mapping)

    def test_lower_is_better_components_are_inverted(self):
        rows = [
            make_city("A", "AA", crime_index=10, density_crime=10, climate=10, landlord=1, pop_growth=0.1, income_growth=0.1, home_growth=0.1, employment_growth=0.1),
            make_city("B", "BB", crime_index=90, density_crime=90, climate=90, landlord=-1, pop_growth=-0.1, income_growth=-0.1, home_growth=-0.1, employment_growth=-0.1),
        ]
        index = score.build_fire_score_index(rows)
        a = index["scores_by_city_key"]["A|AA"]
        b = index["scores_by_city_key"]["B|BB"]
        self.assertGreater(a["fire_score_components"]["crime"]["score"], b["fire_score_components"]["crime"]["score"])
        self.assertGreater(a["fire_score_components"]["climate_risk"]["score"], b["fire_score_components"]["climate_risk"]["score"])

    def test_full_data_weighted_score_bounds(self):
        rows = [
            make_city("A", "AA", pop_growth=0.2, income_growth=0.2, home_growth=0.2, employment_growth=0.2, climate=10, crime_index=10, density_crime=10, landlord=1),
            make_city("B", "BB", pop_growth=-0.2, income_growth=-0.2, home_growth=-0.2, employment_growth=-0.2, climate=90, crime_index=90, density_crime=90, landlord=-1),
        ]
        index = score.build_fire_score_index(rows)
        self.assertEqual(index["scores_by_city_key"]["A|AA"]["fire_score"], 100.0)
        self.assertEqual(index["scores_by_city_key"]["B|BB"]["fire_score"], 0.0)

    def test_missing_metrics_reweighting(self):
        rows = [
            make_city("A", "AA", pop_growth=0.2, income_growth=0.2, home_growth=0.2, employment_growth=0.2, climate=10, crime_index=10, density_crime=10, landlord=1),
            make_city("B", "BB", pop_growth=0.1, income_growth=0.1, home_growth=0.1, employment_growth=0.1, climate=20, crime_index=20, density_crime=20, landlord=0),
            make_city("C", "CC", pop_growth=0.05, income_growth=None, home_growth=0.05, employment_growth=0.05, climate=30, crime_index=30, density_crime=30, landlord=0),
        ]
        index = score.build_fire_score_index(rows)
        c_payload = index["scores_by_city_key"]["C|CC"]
        self.assertIsNotNone(c_payload["fire_score"])
        self.assertGreaterEqual(c_payload["fire_score_coverage"], 70.0)
        self.assertFalse(c_payload["fire_score_components"]["income_growth"]["available"])

    def test_under_seventy_percent_coverage_returns_null(self):
        city = make_city("A", "AA", pop_growth=0.1, employment_growth=0.1)
        rows = [city, make_city("B", "BB", pop_growth=0.2, employment_growth=0.2)]
        payload = score.build_fire_score_index(rows)["scores_by_city_key"]["A|AA"]
        self.assertIsNone(payload["fire_score"])
        self.assertEqual(payload["fire_score_label"], "Insufficient data")

    def test_fewer_than_four_components_returns_null(self):
        rows = [
            make_city("A", "AA", pop_growth=0.1, income_growth=0.1, employment_growth=0.1),
            make_city("B", "BB", pop_growth=0.2, income_growth=0.2, employment_growth=0.2),
        ]
        payload = score.build_fire_score_index(rows)["scores_by_city_key"]["A|AA"]
        self.assertIsNone(payload["fire_score"])

    def test_requires_at_least_one_economic_and_one_risk_component(self):
        rows = [
            make_city("A", "AA", pop_growth=0.1, income_growth=0.1, home_growth=0.1, employment_growth=0.1),
            make_city("B", "BB", pop_growth=0.2, income_growth=0.2, home_growth=0.2, employment_growth=0.2),
        ]
        payload = score.build_fire_score_index(rows)["scores_by_city_key"]["A|AA"]
        self.assertIsNone(payload["fire_score"])

        rows = [
            make_city("A", "AA", climate=10, crime_index=10, density_crime=10, landlord=1),
            make_city("B", "BB", climate=20, crime_index=20, density_crime=20, landlord=0),
        ]
        payload = score.build_fire_score_index(rows)["scores_by_city_key"]["A|AA"]
        self.assertIsNone(payload["fire_score"])

    def test_coverage_label_boundaries(self):
        self.assertEqual(score.coverage_label(100.0), "Complete")
        self.assertEqual(score.coverage_label(99.9), "High coverage")
        self.assertEqual(score.coverage_label(85.0), "High coverage")
        self.assertEqual(score.coverage_label(70.0), "Moderate coverage")
        self.assertEqual(score.coverage_label(69.9), "Insufficient data")

    def test_fire_score_label_boundaries(self):
        self.assertEqual(score.fire_score_label(80.0), "Strong preliminary candidate")
        self.assertEqual(score.fire_score_label(79.9), "Favorable preliminary profile")
        self.assertEqual(score.fire_score_label(65.0), "Favorable preliminary profile")
        self.assertEqual(score.fire_score_label(50.0), "Selective or mixed opportunity")
        self.assertEqual(score.fire_score_label(35.0), "Cautious preliminary profile")
        self.assertEqual(score.fire_score_label(34.9), "Higher-risk preliminary profile")
        self.assertEqual(score.fire_score_label(None), "Insufficient data")

    def test_landlord_mapping(self):
        rows = [
            make_city("L1", "AA", landlord=1, pop_growth=0.1, income_growth=0.1, home_growth=0.1, employment_growth=0.1, climate=20, crime_index=20, density_crime=20),
            make_city("L0", "BB", landlord=0, pop_growth=0.2, income_growth=0.2, home_growth=0.2, employment_growth=0.2, climate=30, crime_index=30, density_crime=30),
            make_city("L-1", "CC", landlord=-1, pop_growth=0.3, income_growth=0.3, home_growth=0.3, employment_growth=0.3, climate=40, crime_index=40, density_crime=40),
            make_city("LM", "DD", landlord=None, pop_growth=0.4, income_growth=0.4, home_growth=0.4, employment_growth=0.4, climate=50, crime_index=50, density_crime=50),
        ]
        index = score.build_fire_score_index(rows)["scores_by_city_key"]
        self.assertEqual(index["L1|AA"]["fire_score_components"]["landlord_friendliness"]["score"], 100.0)
        self.assertEqual(index["L0|BB"]["fire_score_components"]["landlord_friendliness"]["score"], 50.0)
        self.assertEqual(index["L-1|CC"]["fire_score_components"]["landlord_friendliness"]["score"], 0.0)
        self.assertFalse(index["LM|DD"]["fire_score_components"]["landlord_friendliness"]["available"])

    def test_deterministic_and_order_independent(self):
        rows = [
            make_city("A", "AA", pop_growth=0.1, income_growth=0.1, home_growth=0.1, employment_growth=0.1, climate=20, crime_index=20, density_crime=20, landlord=1),
            make_city("B", "BB", pop_growth=0.2, income_growth=0.2, home_growth=0.2, employment_growth=0.2, climate=30, crime_index=30, density_crime=30, landlord=0),
            make_city("C", "CC", pop_growth=0.3, income_growth=0.3, home_growth=0.3, employment_growth=0.3, climate=40, crime_index=40, density_crime=40, landlord=-1),
        ]
        idx1 = score.build_fire_score_index(rows)
        idx2 = score.build_fire_score_index(list(reversed(rows)))
        self.assertEqual(idx1["scores_by_city_key"], idx2["scores_by_city_key"])

    def test_city_key_identity_and_dedup_and_exclusion(self):
        rows = [
            make_city("A", "AA", city_key="same|AA", pop_growth=0.1),
            make_city("A2", "AA", city_key="same|AA", pop_growth=0.2),
            make_city("X", "XX", include_flag=0, pop_growth=0.9),
            make_city("", "", city_key=" ", pop_growth=0.5),
        ]
        idx = score.build_fire_score_index(rows)
        self.assertEqual(idx["comparison_city_count"], 1)
        self.assertIn("same|AA", idx["scores_by_city_key"])
        self.assertNotIn("X|XX", idx["scores_by_city_key"])

    def test_selected_crime_source_is_explicit_and_not_mixed(self):
        rows = []
        for i in range(10):
            rows.append(
                make_city(
                    f"C{i}",
                    "AA",
                    pop_growth=0.1 + i,
                    income_growth=0.1 + i,
                    home_growth=0.1 + i,
                    employment_growth=0.1 + i,
                    climate=20 + i,
                    crime_index=10 + i,
                    density_crime=(50 + i) if i < 5 else None,
                    landlord=1,
                )
            )
        idx = score.build_fire_score_index(rows)
        self.assertEqual(idx["crime_source"], "crime_index_score")
        for payload in idx["scores_by_city_key"].values():
            self.assertEqual(payload["fire_score_methodology"]["crime_source"], "crime_index_score")

    def test_density_adjusted_selected_when_majority_present(self):
        rows = []
        for i in range(10):
            rows.append(
                make_city(
                    f"D{i}",
                    "AA",
                    pop_growth=0.1 + i,
                    income_growth=0.1 + i,
                    home_growth=0.1 + i,
                    employment_growth=0.1 + i,
                    climate=20 + i,
                    crime_index=10 + i,
                    density_crime=50 + i,
                    landlord=1,
                )
            )
        idx = score.build_fire_score_index(rows)
        self.assertEqual(idx["crime_source"], "density_adjusted_crime_score")

    def test_display_rounding_does_not_change_sort_value(self):
        rows = [
            make_city("A", "AA", pop_growth=0.111111, income_growth=0.111111, home_growth=0.111111, employment_growth=0.111111, climate=11.1111, crime_index=11.1111, density_crime=11.1111, landlord=1),
            make_city("B", "BB", pop_growth=0.111112, income_growth=0.111112, home_growth=0.111112, employment_growth=0.111112, climate=11.1112, crime_index=11.1112, density_crime=11.1112, landlord=1),
            make_city("C", "CC", pop_growth=0.1, income_growth=0.1, home_growth=0.1, employment_growth=0.1, climate=12, crime_index=12, density_crime=12, landlord=1),
        ]
        idx = score.build_fire_score_index(rows)
        a = idx["scores_by_city_key"]["A|AA"]["fire_score"]
        b = idx["scores_by_city_key"]["B|BB"]["fire_score"]
        a_sort = idx["sort_score_by_city_key"]["A|AA"]
        b_sort = idx["sort_score_by_city_key"]["B|BB"]
        self.assertIsNotNone(a_sort)
        self.assertIsNotNone(b_sort)
        self.assertEqual(a, round(a_sort, 1))
        self.assertEqual(b, round(b_sort, 1))


class FireMetricsFireScoreRankingTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db_module.init_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def _insert_city(self, city: str, state: str, *, include_flag: int, pop_growth: float | None, income_growth: float | None, home_growth: float | None, job_growth: float | None, climate: float | None, crime: float | None, density: float | None, landlord: float | None, population: float):
        display = f"{city}, {state}"
        self.conn.execute(
            """
            INSERT INTO cities (
                city, state, display_name, normalized_city, normalized_display_name, search_key,
                include_flag, population_current,
                population_growth_recent, median_income_growth_recent,
                median_home_value_growth_recent, employment_growth_recent,
                climate_risk_score, crime_index_score, density_adjusted_crime_score,
                landlord_friendliness_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                city,
                state,
                display,
                city.lower(),
                display.lower(),
                f"{city.lower()} {state.lower()}",
                include_flag,
                population,
                pop_growth,
                income_growth,
                home_growth,
                job_growth,
                climate,
                crime,
                density,
                landlord,
            ),
        )
        self.conn.commit()

    def test_top_cities_fire_score_desc_and_excludes_null(self):
        self._insert_city("Alpha", "AA", include_flag=1, pop_growth=0.30, income_growth=0.30, home_growth=0.30, job_growth=0.30, climate=10, crime=10, density=10, landlord=1, population=200000)
        self._insert_city("Beta", "BB", include_flag=1, pop_growth=0.20, income_growth=0.20, home_growth=0.20, job_growth=0.20, climate=20, crime=20, density=20, landlord=0, population=150000)
        self._insert_city("Gamma", "CC", include_flag=1, pop_growth=None, income_growth=None, home_growth=None, job_growth=None, climate=None, crime=None, density=None, landlord=None, population=120000)

        spec, cities = routes._fetch_top_cities(self.conn, metric_key="fire_score", limit=10)
        self.assertEqual(spec["label"], "Highest FIRE Score")
        self.assertGreaterEqual(len(cities), 2)
        self.assertNotIn("Gamma", [c["city"] for c in cities])
        scores = [c["fire_score"] for c in cities]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_top_cities_fire_score_tie_break_population_then_state_then_city(self):
        self._insert_city("Bravo", "AA", include_flag=1, pop_growth=0.10, income_growth=0.10, home_growth=0.10, job_growth=0.10, climate=10, crime=10, density=10, landlord=1, population=90000)
        self._insert_city("Alpha", "AA", include_flag=1, pop_growth=0.10, income_growth=0.10, home_growth=0.10, job_growth=0.10, climate=10, crime=10, density=10, landlord=1, population=100000)
        self._insert_city("Charlie", "BB", include_flag=1, pop_growth=0.10, income_growth=0.10, home_growth=0.10, job_growth=0.10, climate=10, crime=10, density=10, landlord=1, population=100000)
        self._insert_city("Delta", "CC", include_flag=1, pop_growth=0.00, income_growth=0.00, home_growth=0.00, job_growth=0.00, climate=20, crime=20, density=20, landlord=0, population=80000)

        _, cities = routes._fetch_top_cities(self.conn, metric_key="fire_score", limit=10)
        names = [f"{c['city']},{c['state']}" for c in cities[:3]]
        self.assertEqual(names[0], "Alpha,AA")
        self.assertEqual(names[1], "Charlie,BB")
        self.assertEqual(names[2], "Bravo,AA")


if __name__ == "__main__":
    unittest.main()
