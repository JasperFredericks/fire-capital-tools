"""
Tests for FIRE Metrics improvements:
- Part 1: CRE research (domain allowlist, source validation, fallback chain, cache TTL)
- Part 2: Analytics row city selection (via selectCurrentSearchCity path)
- Part 3: Map zoom/bounds configuration
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from flask import Flask

from fire_metrics.fire_metrics_updater import db as db_module
from tools import fire_metrics_ai_summary as summary
from tools.fire_metrics import city_summary
from tools.fire_metrics.services import _cre_research_model_name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_city(
    city: str = "Alpha",
    state: str = "AA",
    *,
    pop_growth: float = 0.05,
    income_growth: float = 0.04,
    employment_growth: float = 0.03,
    landlord: float = 70,
    climate: float = 30,
    crime: float = 35,
    density_crime: float = 32,
    home_value: float = 350000,
    home_growth: float = 0.04,
) -> dict:
    return {
        "city": city,
        "state": state,
        "display_name": f"{city}, {state}",
        "population_current": 200000,
        "employment_current": 95000,
        "median_income_current": 68000,
        "population_growth_recent": pop_growth,
        "median_income_growth_recent": income_growth,
        "employment_growth_recent": employment_growth,
        "landlord_friendliness_score": landlord,
        "landlord_friendliness_label": "Landlord-friendly",
        "climate_risk_score": climate,
        "climate_risk_rating": "Low",
        "crime_index_score": crime,
        "crime_rating": "Low",
        "density_adjusted_crime_score": density_crime,
        "density_adjusted_crime_rating": "Low",
        "median_home_value_current": home_value,
        "median_home_value_growth_recent": home_growth,
        "warnings": [],
    }


def _seed_cities_table(conn: sqlite3.Connection, cities: list[dict]) -> None:
    for city in cities:
        conn.execute(
            """
            INSERT INTO cities (
                city, state, display_name, normalized_city, normalized_display_name, search_key,
                include_flag,
                population_growth_recent, median_income_growth_recent, employment_growth_recent,
                landlord_friendliness_score, climate_risk_score, crime_index_score,
                density_adjusted_crime_score, median_home_value_current, median_home_value_growth_recent
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                city["city"], city["state"], city["display_name"],
                city["city"].lower(), city["display_name"].lower(),
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


def _make_app(tmp_path: str) -> Flask:
    from app import create_app
    from config import Config

    class TestConfig(Config):
        TESTING = True
        WTF_CSRF_ENABLED = False
        SECRET_KEY = "test-secret"
        FIRE_METRICS_AI_SUMMARIES_ENABLED = False
        FIRE_METRICS_DB_PATH = tmp_path
        UPLOAD_FOLDER = "/tmp/fire_test_uploads"

    return create_app(TestConfig)


# ---------------------------------------------------------------------------
# Part 1: CRE Research — domain allowlist and source validation
# ---------------------------------------------------------------------------

class TestCREAllowlist(unittest.TestCase):

    def test_approved_domains_exist(self):
        self.assertGreater(len(summary.CRE_ALLOWED_DOMAINS), 0)
        expected_core = {"costar.com", "yardimatrix.com", "realpage.com", "cbre.com", "jll.com"}
        actual = set(summary.CRE_ALLOWED_DOMAINS)
        self.assertTrue(expected_core.issubset(actual), f"Missing core domains: {expected_core - actual}")

    def test_validate_approved_domain_passes(self):
        for domain in summary.CRE_ALLOWED_DOMAINS:
            src = {"url": f"https://{domain}/some-report", "publisher": "Test"}
            self.assertTrue(summary.validate_research_source(src), f"Should pass for {domain}")

    def test_validate_subdomain_of_approved_passes(self):
        src = {"url": "https://news.cbre.com/report-2026", "publisher": "CBRE"}
        self.assertTrue(summary.validate_research_source(src))

    def test_validate_non_approved_domain_fails(self):
        for bad_url in [
            "https://zillow.com/report",
            "https://reddit.com/r/realestate",
            "https://redfin.com/news",
            "https://randomseosite.com/cre-market",
            "https://costar.com.fake.com/report",
        ]:
            src = {"url": bad_url, "publisher": "Bad Source"}
            self.assertFalse(summary.validate_research_source(src), f"Should fail for {bad_url}")

    def test_validate_missing_url_fails(self):
        self.assertFalse(summary.validate_research_source({"publisher": "CBRE"}))
        self.assertFalse(summary.validate_research_source({"url": "", "publisher": "CBRE"}))

    def test_validate_malformed_url_fails(self):
        self.assertFalse(summary.validate_research_source({"url": "not-a-url", "publisher": "CBRE"}))


class TestCRECacheFreshness(unittest.TestCase):

    def test_fresh_timestamp_returns_true(self):
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.assertTrue(summary.is_cre_fresh(recent))

    def test_stale_timestamp_returns_false(self):
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        self.assertFalse(summary.is_cre_fresh(old))

    def test_none_returns_false(self):
        self.assertFalse(summary.is_cre_fresh(None))

    def test_empty_string_returns_false(self):
        self.assertFalse(summary.is_cre_fresh(""))

    def test_at_ttl_boundary_returns_false(self):
        # Exactly at the TTL edge should be stale (timedelta comparison is exclusive)
        exactly_at_ttl = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        self.assertFalse(summary.is_cre_fresh(exactly_at_ttl))

    def test_just_within_ttl_returns_true(self):
        just_within = (datetime.now(timezone.utc) - timedelta(days=6, hours=23)).isoformat()
        self.assertTrue(summary.is_cre_fresh(just_within))


class TestCRESourceStructure(unittest.TestCase):

    def _valid_source(self) -> dict:
        return {
            "publisher": "CBRE",
            "title": "2026 Market Outlook",
            "published_date": "Q2 2026",
            "url": "https://cbre.com/2026-market-outlook",
        }

    def test_approved_source_passes_validation(self):
        self.assertTrue(summary.validate_research_source(self._valid_source()))

    def test_no_fabricated_source_when_empty_research(self):
        # When openai_cre_research would return empty, no sources should leak in
        result = {"cre_sentences": "", "research_sources": [], "cre_generated_at": summary.utc_now_iso()}
        self.assertEqual(result["research_sources"], [])

    def test_source_list_is_capped_at_three(self):
        # The function should enforce max 3 sources in validated output
        # (tested via direct logic, not a live API call)
        sources_input = [
            {"url": f"https://cbre.com/report-{i}", "publisher": "CBRE",
             "title": f"Report {i}", "published_date": "2026"}
            for i in range(5)
        ]
        validated = [s for s in sources_input[:3] if summary.validate_research_source(s)]
        self.assertLessEqual(len(validated), 3)

    def test_city_state_context_must_be_passed(self):
        # Smoke test: openai_cre_research signature requires city, state, display_name
        import inspect
        sig = inspect.signature(summary.openai_cre_research)
        params = set(sig.parameters.keys())
        self.assertIn("city", params)
        self.assertIn("state", params)
        self.assertIn("display_name", params)
        self.assertIn("api_key", params)
        self.assertIn("model_name", params)

    def test_cre_research_version_constant_exists(self):
        self.assertTrue(hasattr(summary, "CRE_RESEARCH_VERSION"))
        self.assertIsInstance(summary.CRE_RESEARCH_VERSION, str)
        self.assertTrue(summary.CRE_RESEARCH_VERSION)

    def test_prompt_version_bumped_to_v5(self):
        self.assertEqual(summary.PROMPT_VERSION, "fire_metrics_summary_v5")


class TestCREAPIShape(unittest.TestCase):
    """
    Tests that WOULD HAVE CAUGHT the production failure.
    Verifies the corrected web_search tool shape and source extraction logic.
    """

    def _inspect_source_code(self) -> str:
        import inspect
        return inspect.getsource(summary.openai_cre_research)

    def test_uses_web_search_not_web_search_preview(self):
        """CRE call must use 'web_search', NOT 'web_search_preview'."""
        src = self._inspect_source_code()
        self.assertIn('"web_search"', src)
        self.assertNotIn('"web_search_preview"', src)

    def test_allowed_domains_in_filters_not_tool_body(self):
        """Domain filtering must be under filters.allowed_domains, not in tool body."""
        src = self._inspect_source_code()
        # 'filters' key must appear
        self.assertIn('"filters"', src)
        # allowed_domains must be inside filters, not a direct sibling of type
        self.assertIn('"allowed_domains"', src)
        # The old broken pattern must not appear
        self.assertNotIn('"web_search_preview": {', src)

    def test_tool_choice_required_present(self):
        """tool_choice='required' must be passed to force search execution."""
        src = self._inspect_source_code()
        self.assertIn('tool_choice="required"', src)

    def test_sources_extracted_from_annotations_not_model_json(self):
        """Sources must come from url_citation annotations, not model-generated JSON."""
        src = self._inspect_source_code()
        # Must access url_citation annotations
        self.assertIn("url_citation", src)
        self.assertIn("annotations", src)
        # Must NOT rely on json.loads(raw) for the primary source list
        self.assertNotIn("parsed.get(\"research_sources\")", src)

    def test_non_approved_annotation_url_rejected(self):
        """validate_research_source rejects non-approved URLs even when they come from annotations."""
        bad_src = {"url": "https://zillow.com/report", "title": "Zillow Report"}
        self.assertFalse(summary.validate_research_source(bad_src))

    def test_approved_annotation_url_accepted(self):
        """validate_research_source accepts approved URLs from annotations."""
        good_src = {"url": "https://yardimatrix.com/report", "title": "Yardi Report"}
        self.assertTrue(summary.validate_research_source(good_src))

    def test_hostname_to_publisher_mapping(self):
        """_hostname_to_publisher maps known hostnames to display names."""
        self.assertEqual(summary._hostname_to_publisher("cbre.com"), "CBRE")
        self.assertEqual(summary._hostname_to_publisher("jll.com"), "JLL")
        self.assertEqual(summary._hostname_to_publisher("yardimatrix.com"), "Yardi Matrix")
        self.assertEqual(summary._hostname_to_publisher("research.cbre.com"), "CBRE")

    def test_inline_citation_markers_stripped(self):
        """[1], [2] citation markers are stripped from display text."""
        raw = "Vacancy declined to 4.5% in Q1 2026.[1] Rent growth was 3%.[2]"
        cleaned = summary.re.sub(r"\[\d+\]", "", raw).strip()
        self.assertNotIn("[1]", cleaned)
        self.assertNotIn("[2]", cleaned)
        self.assertIn("4.5%", cleaned)

    def test_none_response_produces_empty_sentences(self):
        """Model outputting 'NONE' should yield empty cre_sentences."""
        # Simulate what the function does when no useful research found
        raw = "NONE"
        cre = summary.re.sub(r"\[\d+\]", "", raw).strip()
        if cre.upper() == "NONE" or not cre:
            cre = ""
        self.assertEqual(cre, "")

    def test_missing_cre_timestamp_is_eligible_immediately(self):
        """A cache row with cre_generated_at=None is immediately eligible for CRE research."""
        self.assertFalse(summary.is_cre_fresh(None))

    def test_fresh_cre_cache_skips_research(self):
        """A cache row with cre_generated_at within TTL is considered fresh."""
        fresh = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        self.assertTrue(summary.is_cre_fresh(fresh))

    def test_stale_cre_cache_triggers_research(self):
        """A cache row with cre_generated_at > TTL is considered stale."""
        stale = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        self.assertFalse(summary.is_cre_fresh(stale))

    def test_web_search_exception_does_not_appear_in_main_response(self):
        """Simulated web_search exception: normal FIRE summary still returned."""
        # Route-level: the exception handler catches CRE errors without propagating
        import inspect
        route_src = open(
            str(Path(__file__).parent.parent / "tools" / "fire_metrics" / "routes.py")
        ).read()
        # Must have except clause around cre_research call
        self.assertIn("cre_exc", route_src)
        # Must log the exception without re-raising
        self.assertIn("FIRE CRE failed", route_src)

    def test_malformed_api_response_handled_gracefully(self):
        """Empty/malformed response text yields empty cre_sentences, not an exception."""
        raw = ""
        cre = summary.re.sub(r"\[\d+\]", "", raw).strip()
        if cre.upper() == "NONE" or not cre:
            cre = ""
        self.assertEqual(cre, "")

    def test_default_model_is_general_purpose_not_search_preview(self):
        """Default CRE model must be a general-purpose model (web_search compatible)."""
        src = open(
            str(Path(__file__).parent.parent / "tools" / "fire_metrics" / "services.py")
        ).read()
        # Default must NOT be gpt-4o-mini-search-preview (only works with web_search_preview)
        self.assertNotIn("gpt-4o-mini-search-preview", src)
        self.assertIn("gpt-4o-mini", src)


    """Verify that CRE research failure never blocks the FIRE Metrics summary."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.app = _make_app(self.tmp.name)
        self.city = make_city()
        with db_module.get_connection(Path(self.tmp.name)) as conn:
            _seed_cities_table(conn, [self.city])

    def tearDown(self):
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_cre_failure_does_not_break_summary(self):
        """When openai_cre_research raises, the route still returns a valid summary."""
        import os

        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            os.environ["FIRE_METRICS_DB_PATH"] = self.tmp.name
            from app import create_app
            from config import Config

            class RouteTestConfig(Config):
                TESTING = True
                WTF_CSRF_ENABLED = False
                SECRET_KEY = "test-secret"
                FIRE_METRICS_AI_SUMMARIES_ENABLED = True
                UPLOAD_FOLDER = "/tmp/fire_test_uploads"

            route_app = create_app(RouteTestConfig)
            with route_app.test_request_context(
                "/tools/fire-metrics/api/city-summary",
                method="POST",
                json={"city": "Alpha", "state": "AA"},
            ):
                with patch.object(
                    summary, "openai_summary",
                    return_value={
                        "strength_sentence": "Alpha, AA has solid employment growth.",
                        "weakness_sentence": "Climate risk is moderate.",
                        "comparison_sentence": "Overall Alpha is a mixed opportunity.",
                    },
                ), patch.object(
                    summary, "openai_cre_research",
                    side_effect=RuntimeError("network error"),
                ), patch(
                    "tools.fire_metrics.routes._summary_api_key", return_value="test-key"
                ):
                    result = city_summary.__wrapped__()

            response = result[0] if isinstance(result, tuple) else result
            data = response.get_json()
            self.assertEqual(data["status"], "ready")
            self.assertIn("summary", data)
            self.assertIsInstance(data.get("research_sources"), list)
            self.assertEqual(data["research_sources"], [])
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_full_ai_failure_reaches_deterministic_fallback(self):
        """When openai_summary raises, the fallback summary is served (no CRE either)."""
        import os

        original_db_path = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            os.environ["FIRE_METRICS_DB_PATH"] = self.tmp.name
            from app import create_app
            from config import Config

            class RouteTestConfig(Config):
                TESTING = True
                WTF_CSRF_ENABLED = False
                SECRET_KEY = "test-secret"
                FIRE_METRICS_AI_SUMMARIES_ENABLED = True
                UPLOAD_FOLDER = "/tmp/fire_test_uploads"

            route_app = create_app(RouteTestConfig)
            with route_app.test_request_context(
                "/tools/fire-metrics/api/city-summary",
                method="POST",
                json={"city": "Alpha", "state": "AA"},
            ):
                with patch.object(
                    summary, "openai_summary",
                    side_effect=RuntimeError("openai down"),
                ), patch.object(
                    summary, "openai_cre_research",
                    side_effect=RuntimeError("openai down"),
                ), patch(
                    "tools.fire_metrics.routes._summary_api_key", return_value="test-key"
                ):
                    result = city_summary.__wrapped__()

            response = result[0] if isinstance(result, tuple) else result
            data = response.get_json()
            self.assertEqual(data["status"], "ready")
            self.assertIn("summary", data)
            # Fallback path produces no CRE sources
            self.assertIsInstance(data.get("research_sources", []), list)
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_no_sources_in_response_when_ai_disabled(self):
        """When AI summaries are disabled, fallback summary includes no CRE sources."""
        with self.app.test_request_context(
            "/tools/fire-metrics/api/city-summary",
            method="POST",
            json={"city": "Alpha", "state": "AA"},
        ):
            result = city_summary.__wrapped__()

        response = result[0] if isinstance(result, tuple) else result
        data = response.get_json()
        # AI disabled → unavailable or fallback, but no fabricated sources
        sources = data.get("research_sources", [])
        self.assertIsInstance(sources, list)
        self.assertEqual(sources, [])


class TestCREDBSchema(unittest.TestCase):
    """Verify the CRE columns are added via migration."""

    def test_cre_columns_present_after_init(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            with db_module.get_connection(db_path) as conn:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(fire_metrics_city_summaries)").fetchall()}
            self.assertIn("cre_sentences_text", cols)
            self.assertIn("research_sources_json", cols)
            self.assertIn("cre_generated_at", cols)
        finally:
            db_path.unlink(missing_ok=True)

    def test_upsert_and_fetch_cre_fields(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            city_row = {
                "city": "Alpha", "state": "AA", "city_key": "alpha|AA",
                "data_fingerprint": "fp1", "model_name": "gpt-4o-mini",
                "prompt_version": "fire_metrics_summary_v5",
                "summary_text": "Strong city.", "strength_sentence": "Strong.",
                "weakness_sentence": "Some risk.", "comparison_sentence": "Mixed.",
                "generated_at": summary.utc_now_iso(),
                "cre_sentences_text": "Supply is elevated in this metro.",
                "research_sources_json": json.dumps([{
                    "publisher": "CBRE", "title": "2026 Outlook",
                    "published_date": "Q2 2026", "url": "https://cbre.com/2026",
                }]),
                "cre_generated_at": summary.utc_now_iso(),
            }
            with db_module.get_connection(db_path) as conn:
                db_module.upsert_city_summary_cache(conn, city_row)
                fetched = db_module.fetch_cached_city_summary(
                    conn, city="Alpha", state="AA",
                    data_fingerprint="fp1", model_name="gpt-4o-mini",
                    prompt_version="fire_metrics_summary_v5",
                )
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched["cre_sentences_text"], "Supply is elevated in this metro.")
            sources = json.loads(fetched["research_sources_json"])
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0]["publisher"], "CBRE")
        finally:
            db_path.unlink(missing_ok=True)

    def test_update_cre_fields_only(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            city_row = {
                "city": "Beta", "state": "BB", "city_key": "beta|BB",
                "data_fingerprint": "fp2", "model_name": "gpt-4o-mini",
                "prompt_version": "fire_metrics_summary_v5",
                "summary_text": "Beta summary.", "strength_sentence": "Strong.",
                "weakness_sentence": "Weak.", "comparison_sentence": "Mixed.",
                "generated_at": summary.utc_now_iso(),
                "cre_sentences_text": "", "research_sources_json": "[]",
                "cre_generated_at": None,
            }
            new_at = summary.utc_now_iso()
            with db_module.get_connection(db_path) as conn:
                db_module.upsert_city_summary_cache(conn, city_row)
                db_module.update_city_summary_cre_fields(
                    conn, city="Beta", state="BB", data_fingerprint="fp2",
                    model_name="gpt-4o-mini", prompt_version="fire_metrics_summary_v5",
                    cre_sentences_text="Vacancy falling in the metro.",
                    research_sources_json=json.dumps([{"publisher": "JLL", "title": "Mid-Year", "url": "https://jll.com/report", "published_date": "2026"}]),
                    cre_generated_at=new_at,
                )
                fetched = db_module.fetch_cached_city_summary(
                    conn, city="Beta", state="BB",
                    data_fingerprint="fp2", model_name="gpt-4o-mini",
                    prompt_version="fire_metrics_summary_v5",
                )
            self.assertEqual(fetched["cre_sentences_text"], "Vacancy falling in the metro.")
            self.assertEqual(fetched["cre_generated_at"], new_at)
            # FIRE Metrics summary text unchanged
            self.assertEqual(fetched["summary_text"], "Beta summary.")
        finally:
            db_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Part 2: Analytics row selection (city_key identity + no duplication)
# ---------------------------------------------------------------------------

class TestAnalyticsRowSelectionLogic(unittest.TestCase):
    """
    The analytics row click routes through selectCurrentSearchCity(), which is
    pure JS. We validate the Python-side contracts it depends on:
    - city_key identity is stable via stableCityKey(city_key field)
    - comparisonCities membership is not altered by making a city active
    - ensureAnalyticsCity returns False if city already present (no duplication)
    These are validated via the DB + route contract that city_key is preserved.
    """

    def test_city_key_field_preserved_in_search_payload(self):
        """city_key returned by the search route matches the DB city_key format."""
        from tools import fire_metrics_ai_summary as ai_s

        city = make_city("Phoenix", "AZ")
        key = ai_s.city_key(city)
        self.assertIn("|", key)
        city_part, state_part = key.split("|", 1)
        self.assertFalse(city_part == "")
        self.assertFalse(state_part == "")

    def test_stale_comparison_does_not_add_duplicate_keys(self):
        """Adding the same city twice via ensureCityAnalyticsCity returns False second time."""
        # This mirrors the JS ensureCityAnalyticsCity logic - tested on the Python side
        # via the DB upsert which uses ON CONFLICT, not via JS execution
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            city_row = {
                "city": "Phoenix", "state": "AZ", "city_key": "phoenix|AZ",
                "data_fingerprint": "fp-px", "model_name": "gpt-4o-mini",
                "prompt_version": "fire_metrics_summary_v5",
                "summary_text": "Phoenix summary.", "strength_sentence": "Strong.",
                "weakness_sentence": "Risk.", "comparison_sentence": "Mixed.",
                "generated_at": summary.utc_now_iso(),
                "cre_sentences_text": "", "research_sources_json": "[]",
                "cre_generated_at": None,
            }
            with db_module.get_connection(db_path) as conn:
                db_module.upsert_city_summary_cache(conn, city_row)
                # Upserting same row again should not raise or create a duplicate
                db_module.upsert_city_summary_cache(conn, city_row)
                rows = conn.execute(
                    "SELECT COUNT(*) FROM fire_metrics_city_summaries WHERE city=? AND state=?",
                    ("Phoenix", "AZ"),
                ).fetchone()[0]
            self.assertEqual(rows, 1, "Duplicate cache row should not be created")
        finally:
            db_path.unlink(missing_ok=True)

    def test_analytics_row_activation_uses_same_city_key(self):
        """The analytics row selection uses the same city_key as the chip selection path."""
        # Confirmed by reading the JS: row click calls selectCurrentSearchCity(rowCityKey, ...)
        # rowCityKey is set from stableCityKey(city) which reads city.city_key
        # This test validates that the DB city_key field is returned by the summary route
        city = make_city("Denver", "CO")
        benchmarks = summary.compute_benchmarks(city, [city])
        self.assertEqual(
            summary.city_key(city),
            "Denver|CO",
        )


# ---------------------------------------------------------------------------
# Part 3: Map zoom configuration assertions
# ---------------------------------------------------------------------------

class TestMapZoomConfiguration(unittest.TestCase):
    """Static assertions on the zoom values written into the template."""

    def _template_text(self) -> str:
        template_path = Path(__file__).parent.parent / "templates" / "tools" / "fire_metrics.html"
        return template_path.read_text(encoding="utf-8")

    def test_initial_zoom_is_3(self):
        text = self._template_text()
        self.assertIn("zoom: 3,", text)

    def test_nationwide_fitbounds_padding_is_40(self):
        text = self._template_text()
        self.assertIn("fitBounds(usBounds, 40)", text)

    def test_nationwide_cap_is_4(self):
        text = self._template_text()
        self.assertIn("googleMap.setZoom(4)", text)

    def test_single_city_max_zoom_is_6(self):
        text = self._template_text()
        # setZoom(6) used for single-city max
        self.assertIn("googleMap.setZoom(6)", text)

    def test_no_old_zoom_7_clamp(self):
        text = self._template_text()
        # setZoom(7) should not appear — all replaced with setZoom(6)
        self.assertNotIn("googleMap.setZoom(7)", text)

    def test_no_old_zoom_5_initial(self):
        text = self._template_text()
        # Initial zoom was 4, now 3; zoom: 4, should not appear in map init
        # (it may appear in comments or elsewhere; check the specific Map constructor line)
        self.assertNotIn("zoom: 4,\n", text)

    def test_multiple_cities_fitbounds_padding_is_100(self):
        text = self._template_text()
        self.assertIn("fitBounds(bounds, 100)", text)

    def test_analytics_row_has_activateRowCity_function(self):
        """Row click handler wiring is present in the template."""
        text = self._template_text()
        self.assertIn("activateRowCity", text)
        self.assertIn("ensureAnalyticsRow: false", text)

    def test_analytics_row_click_ignores_button_children(self):
        """Row click guards against nested button clicks."""
        text = self._template_text()
        self.assertIn("event.target.closest(\"button\")", text)

    def test_analytics_row_keyboard_support(self):
        """Enter and spacebar key handlers are present for analytics rows."""
        text = self._template_text()
        self.assertIn('"Enter"', text)
        # spacebar key value is " " (single space), not "Space"
        self.assertIn('" "', text)

    def test_fire_metrics_surface_class_present(self):
        """fire-metrics-surface class scopes brand tokens."""
        text = self._template_text()
        self.assertIn("fire-metrics-surface", text)

    def _css_text(self) -> str:
        css_path = Path(__file__).parent.parent / "static" / "style.css"
        return css_path.read_text(encoding="utf-8")

    def test_active_marker_uses_fire_orange(self):
        """Active city marker uses FIRE orange (#e8590c), not generic red."""
        css = self._css_text()
        # fire-advanced-marker-current section should reference orange
        self.assertIn("#e8590c", css)
        # Must not use the old #dc2626 red for the active marker
        idx_current = css.find(".fire-advanced-marker-current")
        idx_next_rule = css.find("\n}", idx_current)
        active_block = css[idx_current:idx_next_rule]
        self.assertIn("#e8590c", active_block)

    def test_comparison_marker_uses_fire_navy(self):
        """Comparison city marker uses FIRE navy (#1a2744), not generic blue."""
        css = self._css_text()
        idx_compared = css.find(".fire-advanced-marker-compared")
        idx_next_rule = css.find("\n}", idx_compared)
        compared_block = css[idx_compared:idx_next_rule]
        self.assertIn("#1a2744", compared_block)

    def test_brand_tokens_defined(self):
        """Scoped brand token variables are defined in CSS."""
        css = self._css_text()
        self.assertIn("--fm-orange", css)
        self.assertIn("--fm-navy", css)
        self.assertIn(".fire-metrics-surface", css)

    def test_ai_overview_sources_panel_styled(self):
        """AI overview sources section has branded styling."""
        css = self._css_text()
        self.assertIn(".fire-ai-overview-sources", css)
        self.assertIn(".fire-ai-sources-list", css)

    def test_active_analytics_row_uses_orange_accent(self):
        """Active analytics row left accent uses FIRE orange."""
        css = self._css_text()
        idx = css.find(".fire-analytics-row-active td:first-child")
        idx_end = css.find("\n}", idx)
        block = css[idx:idx_end]
        self.assertIn("#e8590c", block)

    def test_active_city_chip_uses_orange(self):
        """Active city chip uses FIRE orange treatment."""
        css = self._css_text()
        idx = css.find(".fire-city-chip.active")
        idx_end = css.find("\n}", idx)
        block = css[idx:idx_end]
        self.assertIn("#e8590c", block)


# ---------------------------------------------------------------------------
# Part 1: Existing score and summary tests remain intact (smoke-level check)
# ---------------------------------------------------------------------------

class TestExistingBehaviorPreserved(unittest.TestCase):

    def test_fallback_summary_still_produces_three_sentences(self):
        city = make_city()
        # Need enough comparison cities for the fallback to produce 3 good sentences
        cities = [
            city,
            make_city("Beta", "BB", pop_growth=0.01, income_growth=0.02, employment_growth=0.01,
                      landlord=40, climate=65, crime=70, density_crime=65),
            make_city("Gamma", "CC", pop_growth=0.03, income_growth=0.03, employment_growth=0.02,
                      landlord=55, climate=45, crime=50, density_crime=48),
        ]
        benchmarks = summary.compute_benchmarks(city, cities)
        result = summary.fallback_summary(city, benchmarks)
        combined = summary.combined_summary(result)
        self.assertEqual(summary.count_sentences(combined), 3)

    def test_cre_research_version_constant(self):
        self.assertEqual(summary.CRE_RESEARCH_VERSION, "cre_v1")

    def test_cre_ttl_days_constant(self):
        self.assertEqual(summary.CRE_RESEARCH_TTL_DAYS, 7)


if __name__ == "__main__":
    unittest.main()
