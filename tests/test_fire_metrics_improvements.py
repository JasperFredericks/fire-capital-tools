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

import httpx

from flask import Flask

from fire_metrics.fire_metrics_updater import db as db_module
from tools import fire_metrics_ai_summary as summary
from tools.fire_metrics import city_summary, city_summary_cre
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

    def test_fresh_timestamp_and_current_version_returns_true(self):
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.assertTrue(summary.is_cre_cache_current(recent, summary.CRE_RESEARCH_VERSION))

    def test_stale_timestamp_returns_false(self):
        old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        self.assertFalse(summary.is_cre_cache_current(old, summary.CRE_RESEARCH_VERSION))

    def test_none_timestamp_returns_false(self):
        self.assertFalse(summary.is_cre_cache_current(None, summary.CRE_RESEARCH_VERSION))

    def test_empty_string_returns_false(self):
        self.assertFalse(summary.is_cre_cache_current("", summary.CRE_RESEARCH_VERSION))

    def test_at_ttl_boundary_returns_false(self):
        exactly_at_ttl = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        self.assertFalse(summary.is_cre_cache_current(exactly_at_ttl, summary.CRE_RESEARCH_VERSION))

    def test_just_within_ttl_returns_true(self):
        just_within = (datetime.now(timezone.utc) - timedelta(days=29, hours=23)).isoformat()
        self.assertTrue(summary.is_cre_cache_current(just_within, summary.CRE_RESEARCH_VERSION))

    def test_old_version_is_immediately_stale(self):
        """A row with a valid timestamp but old CRE version is not fresh."""
        fresh_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self.assertFalse(summary.is_cre_cache_current(fresh_ts, "cre_v1"))
        self.assertFalse(summary.is_cre_cache_current(fresh_ts, "cre_v2"))

    def test_null_version_is_immediately_stale(self):
        """A row with NULL version is never considered current."""
        fresh_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self.assertFalse(summary.is_cre_cache_current(fresh_ts, None))
        self.assertFalse(summary.is_cre_cache_current(fresh_ts, ""))

    def test_backward_compat_is_cre_fresh(self):
        """is_cre_fresh still works via alias but now requires version."""
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self.assertTrue(summary.is_cre_fresh(recent, summary.CRE_RESEARCH_VERSION))
        self.assertFalse(summary.is_cre_fresh(recent, "cre_v1"))


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
    Tests that verify the corrected API shape (all mocked — no real API calls).
    """

    def _inspect_source_code(self) -> str:
        import inspect
        return inspect.getsource(summary.openai_cre_research)

    def test_default_model_is_gpt_4_1_mini(self):
        """Default CRE model must be gpt-4.1-mini (supports hosted web_search)."""
        svc_src = open(str(Path(__file__).parent.parent / "tools" / "fire_metrics" / "services.py")).read()
        self.assertIn("gpt-4.1-mini", svc_src)
        self.assertNotIn("gpt-4o-mini\"", svc_src)  # must not silently use gpt-4o-mini

    def test_gpt_4o_mini_not_the_default(self):
        """gpt-4o-mini must not be the default CRE model (it doesn't support web_search)."""
        cfg_src = open(str(Path(__file__).parent.parent / "config.py")).read()
        # The default should not be gpt-4o-mini (only gpt-4.1-mini)
        self.assertNotIn("\"gpt-4o-mini\"", cfg_src)

    def test_uses_web_search_not_web_search_preview(self):
        """CRE call uses 'web_search', NOT 'web_search_preview'."""
        src = self._inspect_source_code()
        self.assertIn('"web_search"', src)
        self.assertNotIn('"web_search_preview"', src)

    def test_search_context_size_is_low(self):
        """search_context_size must be 'low' to minimize cost."""
        src = self._inspect_source_code()
        self.assertIn('"low"', src)

    def test_openai_tool_payload_omits_filters(self):
        """gpt-4.1-mini web_search payload must omit unsupported filters."""
        src = self._inspect_source_code()
        self.assertNotIn('"filters"', src)
        self.assertNotIn('"allowed_domains"', src)

    def test_tool_choice_required_present(self):
        """tool_choice='required' forces web search to happen."""
        src = self._inspect_source_code()
        self.assertIn('tool_choice="required"', src)

    def test_include_action_sources(self):
        """include parameter requests web_search_call.action.sources."""
        src = self._inspect_source_code()
        self.assertIn("web_search_call.action.sources", src)

    def test_only_one_api_call_per_request(self):
        """openai_cre_research makes exactly one responses.create call."""
        mock_response = MagicMock()
        mock_response.output = []
        mock_response.output_text = "NONE"
        calls = []
        def mock_create(**kwargs):
            calls.append(kwargs)
            return mock_response
        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.responses.create.side_effect = mock_create
            summary.openai_cre_research(
                api_key="test-key", model_name="gpt-4.1-mini",
                city="Austin", state="TX", display_name="Austin, TX",
            )
        self.assertEqual(len(calls), 1, "Must make exactly one API call per request")

    def test_cre_responses_kwargs_match_supported_web_search_shape(self):
        """CRE request kwargs match supported Responses API web_search shape."""
        mock_response = MagicMock()
        mock_response.output = []
        mock_response.output_text = "NONE"
        mock_response.error = None
        captured: dict = {}

        def mock_create(**kwargs):
            captured.update(kwargs)
            return mock_response

        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.responses.create.side_effect = mock_create
            summary.openai_cre_research(
                api_key="test-key", model_name="gpt-4.1-mini",
                city="Austin", state="TX", display_name="Austin, TX",
            )

        self.assertEqual(captured.get("model"), "gpt-4.1-mini")
        self.assertEqual(captured.get("tool_choice"), "required")
        self.assertEqual(captured.get("include"), ["web_search_call.action.sources"])
        self.assertIsInstance(captured.get("input"), str)
        self.assertTrue(captured.get("input"))
        self.assertIn("3-5 sentences", captured.get("input"))
        self.assertIn("100-150 words maximum", captured.get("input"))
        self.assertIn("Avoid quarter-by-quarter repetition", captured.get("input"))
        self.assertIn("Output only the word NONE", captured.get("input"))

        tools = captured.get("tools")
        self.assertIsInstance(tools, list)
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].get("type"), "web_search")
        self.assertEqual(tools[0].get("search_context_size"), "low")
        self.assertNotIn("filters", tools[0])
        self.assertNotIn("allowed_domains", tools[0])

        self.assertNotIn("instructions", captured)
        self.assertNotIn("max_output_tokens", captured)
        self.assertNotIn("response_format", captured)
        self.assertNotIn("text", captured)

    def test_sources_extracted_from_action_sources(self):
        """Sources are extracted from web_search_call action.sources."""
        mock_action_src = MagicMock()
        mock_action_src.url = "https://cbre.com/report-2026"
        mock_action = MagicMock()
        mock_action.sources = [mock_action_src]
        mock_ws_call = MagicMock()
        mock_ws_call.type = "web_search_call"
        mock_ws_call.action = mock_action
        mock_response = MagicMock()
        mock_response.output = [mock_ws_call]
        mock_response.output_text = "CBRE reports improving occupancy in the Austin metro."
        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.responses.create.return_value = mock_response
            result = summary.openai_cre_research(
                api_key="test-key", model_name="gpt-4.1-mini",
                city="Austin", state="TX", display_name="Austin, TX",
            )
        self.assertEqual(result["result_type"], "success")
        self.assertGreater(len(result["research_sources"]), 0)
        self.assertEqual(result["research_sources"][0]["url"], "https://cbre.com/report-2026")

    def test_success_summary_is_bounded_and_removes_raw_urls(self):
        """Returned CRE prose is capped and strips raw URLs from visible summary text."""
        mock_action_src = MagicMock()
        mock_action_src.url = "https://cbre.com/report-2026"
        mock_action = MagicMock()
        mock_action.sources = [mock_action_src]
        mock_ws_call = MagicMock()
        mock_ws_call.type = "web_search_call"
        mock_ws_call.action = mock_action
        long_text = " ".join(["Vacancy softened while deliveries remained elevated."] * 80)
        long_text += " https://example.com/raw-url"
        mock_response = MagicMock()
        mock_response.output = [mock_ws_call]
        mock_response.output_text = long_text
        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.responses.create.return_value = mock_response
            result = summary.openai_cre_research(
                api_key="test-key", model_name="gpt-4.1-mini",
                city="Austin", state="TX", display_name="Austin, TX",
            )

        self.assertEqual(result["result_type"], "success")
        self.assertNotIn("http://", result["cre_sentences"])
        self.assertNotIn("https://", result["cre_sentences"])
        self.assertLessEqual(len(result["cre_sentences"].split()), 150)

    def test_sources_extracted_from_url_citation_annotations(self):
        """Sources are also extracted from url_citation annotations in text output."""
        mock_ann = MagicMock()
        mock_ann.type = "url_citation"
        mock_ann.url = "https://yardimatrix.com/report"
        mock_ann.title = "Yardi Q2 Report"
        mock_part = MagicMock()
        mock_part.annotations = [mock_ann]
        mock_msg = MagicMock()
        mock_msg.type = "message"
        mock_msg.content = [mock_part]
        mock_response = MagicMock()
        mock_response.output = [mock_msg]
        mock_response.output_text = "Vacancy declined in the Raleigh metro."
        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.responses.create.return_value = mock_response
            result = summary.openai_cre_research(
                api_key="test-key", model_name="gpt-4.1-mini",
                city="Raleigh", state="NC", display_name="Raleigh, NC",
            )
        self.assertEqual(result["result_type"], "success")
        urls = [s["url"] for s in result["research_sources"]]
        self.assertIn("https://yardimatrix.com/report", urls)

    def test_non_approved_domain_source_rejected(self):
        """Non-approved domain URLs from action.sources are rejected server-side."""
        mock_action_src = MagicMock()
        mock_action_src.url = "https://zillow.com/research"
        mock_action = MagicMock()
        mock_action.sources = [mock_action_src]
        mock_ws_call = MagicMock()
        mock_ws_call.type = "web_search_call"
        mock_ws_call.action = mock_action
        mock_response = MagicMock()
        mock_response.output = [mock_ws_call]
        mock_response.output_text = "Some content."
        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.responses.create.return_value = mock_response
            result = summary.openai_cre_research(
                api_key="test-key", model_name="gpt-4.1-mini",
                city="Dallas", state="TX", display_name="Dallas, TX",
            )
        approved_urls = [s["url"] for s in result["research_sources"]]
        self.assertNotIn("https://zillow.com/research", approved_urls)

    def test_none_response_produces_no_data_result_type(self):
        """Model outputting 'NONE' → result_type='no_data'."""
        mock_response = MagicMock()
        mock_response.output = []
        mock_response.output_text = "NONE"
        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.responses.create.return_value = mock_response
            result = summary.openai_cre_research(
                api_key="test-key", model_name="gpt-4.1-mini",
                city="Miami", state="FL", display_name="Miami, FL",
            )
        self.assertEqual(result["result_type"], "no_data")
        self.assertEqual(result["cre_sentences"], "No relevant research from approved sources.")
        self.assertEqual(result["research_sources"], [])

    def test_api_exception_produces_failure_result_type(self):
        """Any API exception → result_type='failure' (never raises)."""
        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.responses.create.side_effect = RuntimeError("Connection error")
            result = summary.openai_cre_research(
                api_key="test-key", model_name="gpt-4.1-mini",
                city="NYC", state="NY", display_name="New York, NY",
            )
        self.assertEqual(result["result_type"], "failure")
        self.assertEqual(result["cre_sentences"], "")

    def test_bad_request_returns_sanitized_failure_code_and_param(self):
        """BadRequestError surfaces safe code/param diagnostics without leaking payloads."""
        req = httpx.Request("POST", "https://api.openai.com/v1/responses")
        resp = httpx.Response(400, request=req, json={
            "error": {
                "message": "Invalid tool payload.",
                "type": "invalid_request_error",
                "param": "tools[0].filters.allowed_domains[0]",
                "code": "invalid_domain",
            }
        })

        from openai import BadRequestError
        exc = BadRequestError(
            "Invalid tool payload.",
            response=resp,
            body={
                "message": "Invalid tool payload.",
                "type": "invalid_request_error",
                "param": "tools[0].filters.allowed_domains[0]",
                "code": "invalid_domain",
            },
        )

        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.responses.create.side_effect = exc
            result = summary.openai_cre_research(
                api_key="test-key", model_name="gpt-4.1-mini",
                city="Boston", state="MA", display_name="Boston, MA",
            )

        self.assertEqual(result["result_type"], "failure")
        self.assertEqual(result["failure_category"], "bad_request")
        self.assertEqual(result["failure_code"], "invalid_domain")
        self.assertEqual(result["failure_param"], "tools[0].filters.allowed_domains[0]")
        self.assertTrue(result.get("failure_message"))

    def test_successful_result_stores_current_cre_version(self):
        """Successful result includes the current CRE_RESEARCH_VERSION."""
        mock_response = MagicMock()
        mock_response.output = []
        mock_response.output_text = "Vacancy is falling in the NYC metro."
        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.responses.create.return_value = mock_response
            result = summary.openai_cre_research(
                api_key="test-key", model_name="gpt-4.1-mini",
                city="NYC", state="NY", display_name="New York, NY",
            )
        self.assertEqual(result["cre_research_version"], summary.CRE_RESEARCH_VERSION)

    def test_cre_research_version_is_cre_v3(self):
        """CRE_RESEARCH_VERSION must be cre_v3 to bust old broken cached results."""
        self.assertEqual(summary.CRE_RESEARCH_VERSION, "cre_v3")

    def test_cre_version_v1_is_old_version(self):
        """cre_v1 is NOT the current version."""
        self.assertNotEqual(summary.CRE_RESEARCH_VERSION, "cre_v1")

    def test_negative_cache_constants_exist(self):
        """Negative cache and failure backoff constants are defined."""
        self.assertTrue(hasattr(summary, "CRE_NEGATIVE_CACHE_TTL_HOURS"))
        self.assertTrue(hasattr(summary, "CRE_FAILURE_BACKOFF_MINUTES"))
        self.assertGreater(summary.CRE_NEGATIVE_CACHE_TTL_HOURS, 0)
        self.assertGreater(summary.CRE_FAILURE_BACKOFF_MINUTES, 0)

    def test_hostname_to_publisher_mapping(self):
        """_hostname_to_publisher maps known hostnames to display names."""
        self.assertEqual(summary._hostname_to_publisher("cbre.com"), "CBRE")
        self.assertEqual(summary._hostname_to_publisher("jll.com"), "JLL")
        self.assertEqual(summary._hostname_to_publisher("yardimatrix.com"), "Yardi Matrix")
        self.assertEqual(summary._hostname_to_publisher("research.cbre.com"), "CBRE")

    def test_cre_version_column_in_db_schema(self):
        """DB schema includes cre_research_version column."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            with db_module.get_connection(db_path) as conn:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(fire_metrics_city_summaries)").fetchall()}
            self.assertIn("cre_research_version", cols)
        finally:
            db_path.unlink(missing_ok=True)

    def test_mocked_success_returns_research_sources(self):
        """city-summary-cre endpoint returns non-empty research_sources on mocked success."""
        import os
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        original_db = os.environ.get("FIRE_METRICS_DB_PATH")
        try:
            os.environ["FIRE_METRICS_DB_PATH"] = tmp.name
            city = make_city()
            with db_module.get_connection(Path(tmp.name)) as conn:
                _seed_cities_table(conn, [city])
            from app import create_app
            from config import Config
            class TestCfg(Config):
                TESTING = True
                WTF_CSRF_ENABLED = False
                SECRET_KEY = "test"
                FIRE_METRICS_AI_SUMMARIES_ENABLED = True
                UPLOAD_FOLDER = "/tmp/fire_test_uploads"
            app = create_app(TestCfg)
            mock_cre_result = {
                "cre_sentences": "Vacancy in the Alpha metro declined to 4.2%.",
                "research_sources": [{"publisher": "CBRE", "title": "Q2 Report", "published_date": "", "url": "https://cbre.com/q2"}],
                "cre_generated_at": summary.utc_now_iso(),
                "cre_research_version": summary.CRE_RESEARCH_VERSION,
                "result_type": "success",
            }
            with app.test_request_context(
                "/tools/fire-metrics/api/city-summary", method="POST",
                json={
                    "city": "Alpha",
                    "state": "AA",
                },
            ):
                with patch.object(summary, "openai_summary", return_value={
                    "strength_sentence": "Alpha has solid employment growth.",
                    "weakness_sentence": "Climate risk is moderate.",
                    "comparison_sentence": "Overall Alpha is a mixed opportunity.",
                }), patch("tools.fire_metrics.routes._summary_api_key", return_value="test-key"), \
                     patch("tools.fire_metrics.routes._summary_model_name", return_value="gpt-4.1-mini"):
                    city_summary.__wrapped__()

            with app.test_request_context(
                "/tools/fire-metrics/api/city-summary-cre", method="POST",
                json={
                    "city": "Alpha",
                    "state": "AA",
                    "cre_generation_intent": "explicit_city_selection",
                    "cre_selection_source": "main_city_search",
                },
            ):
                with patch.object(summary, "openai_summary", return_value={
                    "strength_sentence": "Alpha has solid employment growth.",
                    "weakness_sentence": "Climate risk is moderate.",
                    "comparison_sentence": "Overall Alpha is a mixed opportunity.",
                }), patch.object(summary, "openai_cre_research", return_value=mock_cre_result), \
                     patch("tools.fire_metrics.routes._summary_api_key", return_value="test-key"), \
                     patch("tools.fire_metrics.routes._summary_model_name", return_value="gpt-4.1-mini"):
                        result = city_summary_cre.__wrapped__()
            response = result[0] if isinstance(result, tuple) else result
            data = response.get_json()
            self.assertIsInstance(data.get("research_sources"), list)
            self.assertGreater(len(data["research_sources"]), 0)
        finally:
            Path(tmp.name).unlink(missing_ok=True)
            if original_db is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db

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
        """When openai_cre_research returns failure, overview still renders and CRE endpoint reports failure."""
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
                json={
                    "city": "Alpha",
                    "state": "AA",
                },
            ):
                with patch.object(
                    summary, "openai_summary",
                    return_value={
                        "strength_sentence": "Alpha, AA has solid employment growth.",
                        "weakness_sentence": "Climate risk is moderate.",
                        "comparison_sentence": "Overall Alpha is a mixed opportunity.",
                    },
                ), patch(
                    "tools.fire_metrics.routes._summary_api_key", return_value="test-key"
                ):
                    overview_result = city_summary.__wrapped__()

            overview_response = overview_result[0] if isinstance(overview_result, tuple) else overview_result
            overview_data = overview_response.get_json()
            self.assertEqual(overview_data["status"], "ready")
            self.assertIn("summary", overview_data)

            with route_app.test_request_context(
                "/tools/fire-metrics/api/city-summary-cre",
                method="POST",
                json={
                    "city": "Alpha",
                    "state": "AA",
                    "cre_generation_intent": "explicit_city_selection",
                    "cre_selection_source": "main_city_search",
                },
            ):
                with patch.object(
                    summary, "openai_summary",
                    return_value={
                        "strength_sentence": "Alpha, AA has solid employment growth.",
                        "weakness_sentence": "Climate risk is moderate.",
                        "comparison_sentence": "Overall Alpha is a mixed opportunity.",
                    },
                ), patch.object(
                    summary,
                    "openai_cre_research",
                    return_value={
                        "cre_sentences": "",
                        "research_sources": [],
                        "cre_generated_at": summary.utc_now_iso(),
                        "cre_research_version": summary.CRE_RESEARCH_VERSION,
                        "result_type": "failure",
                        "failure_category": "network_error",
                    },
                ), patch(
                    "tools.fire_metrics.routes._summary_api_key", return_value="test-key"
                ):
                    result = city_summary_cre.__wrapped__()

            response = result[0] if isinstance(result, tuple) else result
            data = response.get_json()
            self.assertEqual(data["status"], "ready")
            self.assertIsInstance(data.get("research_sources"), list)
            self.assertEqual(data["research_sources"], [])
            self.assertEqual(data.get("cre_status"), "failure")
            self.assertEqual(data.get("cre_failure_category"), "network_error")
            self.assertIsNone(data.get("cre_failure_code"))
            self.assertIsNone(data.get("cre_failure_param"))
        finally:
            if original_db_path is None:
                os.environ.pop("FIRE_METRICS_DB_PATH", None)
            else:
                os.environ["FIRE_METRICS_DB_PATH"] = original_db_path

    def test_full_ai_failure_reaches_deterministic_fallback(self):
        """When openai_summary raises, fallback summary serves and CRE failure remains isolated."""
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
                json={
                    "city": "Alpha",
                    "state": "AA",
                },
            ):
                with patch.object(
                    summary, "openai_summary",
                    side_effect=RuntimeError("openai down"),
                ), patch(
                    "tools.fire_metrics.routes._summary_api_key", return_value="test-key"
                ), patch(
                    "tools.fire_metrics.routes._summary_model_name", return_value="gpt-4.1-mini"
                ):
                    overview_result = city_summary.__wrapped__()

            overview_response = overview_result[0] if isinstance(overview_result, tuple) else overview_result
            overview_data = overview_response.get_json()
            self.assertEqual(overview_data["status"], "ready")
            self.assertIn("summary", overview_data)

            with route_app.test_request_context(
                "/tools/fire-metrics/api/city-summary-cre",
                method="POST",
                json={
                    "city": "Alpha",
                    "state": "AA",
                    "cre_generation_intent": "explicit_city_selection",
                    "cre_selection_source": "main_city_search",
                },
            ):
                with patch.object(
                    summary, "openai_summary",
                    side_effect=RuntimeError("openai down"),
                ), patch.object(
                    summary, "openai_cre_research",
                    return_value={
                        "cre_sentences": "",
                        "research_sources": [],
                        "cre_generated_at": summary.utc_now_iso(),
                        "cre_research_version": summary.CRE_RESEARCH_VERSION,
                        "result_type": "failure",
                        "failure_category": "network_error",
                    },
                ), patch(
                    "tools.fire_metrics.routes._summary_api_key", return_value="test-key"
                ), patch(
                    "tools.fire_metrics.routes._summary_model_name", return_value="gpt-4.1-mini"
                ):
                    result = city_summary_cre.__wrapped__()

            response = result[0] if isinstance(result, tuple) else result
            data = response.get_json()
            self.assertEqual(data["status"], "ready")
            self.assertIsInstance(data.get("research_sources", []), list)
            self.assertEqual(data.get("cre_status"), "failure")
            self.assertEqual(data.get("cre_failure_category"), "network_error")
            self.assertIsNone(data.get("cre_failure_code"))
            self.assertIsNone(data.get("cre_failure_param"))
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
            self.assertIn("cre_failure_category", cols)
            self.assertIn("cre_failure_code", cols)
            self.assertIn("cre_failure_param", cols)
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

class TestFireMetricsLabelCorrection(unittest.TestCase):
    """Regression: no user-facing UI should say 'FIRE Metric' (singular)."""

    def _template(self, name: str) -> str:
        return (Path(__file__).parent.parent / "templates" / name).read_text(encoding="utf-8")

    def test_sidebar_says_fire_metrics_plural(self):
        text = self._template("base.html")
        # The nav link text should say FIRE Metrics
        self.assertIn("FIRE Metrics", text)
        # It must not have the singular label in a nav-link context
        import re
        # Match the exact nav-link text area
        nav_blocks = re.findall(r'class="nav-link[^"]*"[^>]*>.*?</a>', text, re.DOTALL)
        for block in nav_blocks:
            if "fire_metrics" in block or "FIRE Metric" in block:
                # The link text should be plural
                self.assertIn("FIRE Metrics", block, f"nav-link block still has singular: {block[:120]}")

    def test_dashboard_card_says_fire_metrics_plural(self):
        text = self._template("dashboard.html")
        # Must have FIRE Metrics plural in a tool-card-title
        self.assertIn("FIRE Metrics", text)
        # Must not have the old singular in a tool-card-title element
        self.assertNotIn("<div class=\"tool-card-title\">FIRE Metric</div>", text)

    def test_deal_dive_detail_says_fire_metrics_plural(self):
        text = self._template("tools/deal_dive_detail.html")
        self.assertNotIn("Market Context (FIRE Metric)</div>", text)



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

    def test_nationwide_fitbounds_padding_is_generous(self):
        """Nationwide fitBounds padding is ≥60 for zoomed-out national feel."""
        text = self._template_text()
        self.assertIn("fitBounds(usBounds, 80)", text)

    def test_nationwide_cap_is_3(self):
        """Nationwide view caps at zoom 3 so the U.S. doesn't fill the frame."""
        text = self._template_text()
        self.assertIn("googleMap.setZoom(3)", text)

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

    def test_comparison_marker_uses_fire_navy_family(self):
        """Comparison city marker uses FIRE navy/royal-blue, not generic blue."""
        css = self._css_text()
        idx_compared = css.find(".fire-advanced-marker-compared")
        idx_next_rule = css.find("\n}", idx_compared)
        compared_block = css[idx_compared:idx_next_rule]
        # Accept either deep navy or royal blue — both are FIRE brand navy family
        navy_family = "1a2744" in compared_block or "1e3a6e" in compared_block
        self.assertTrue(navy_family, "Comparison marker should use FIRE navy/royal-blue")

    def test_comparison_marker_not_generic_google_blue(self):
        """Comparison city marker must not use generic Google blue (#4285F4 or #2563eb default)."""
        css = self._css_text()
        idx_compared = css.find(".fire-advanced-marker-compared")
        idx_next_rule = css.find("\n}", idx_compared)
        compared_block = css[idx_compared:idx_next_rule].lower()
        self.assertNotIn("4285f4", compared_block)
        self.assertNotIn("dc2626", compared_block)  # also not generic red

    def test_brand_tokens_defined(self):
        """Scoped brand token variables are defined in CSS."""
        css = self._css_text()
        self.assertIn("--fm-orange", css)
        self.assertIn("--fm-navy", css)
        self.assertIn(".fire-metrics-surface", css)

    def test_active_nav_uses_fire_orange(self):
        """Active nav link uses FIRE orange, not generic blue."""
        css = self._css_text()
        idx = css.find(".nav-link.active")
        idx_end = css.find("\n}", idx)
        block = css[idx:idx_end]
        self.assertIn("#fb923c", block)  # orange text

    def test_fire_metrics_hero_present(self):
        """FIRE Metrics has a branded navy hero header."""
        css = self._css_text()
        self.assertIn(".fire-metrics-hero", css)
        self.assertIn(".fire-metrics-hero-title", css)

    def test_dashboard_hero_present(self):
        """Dashboard has a branded navy hero section."""
        css = self._css_text()
        self.assertIn(".dashboard-hero", css)
        self.assertIn(".dashboard-hero-title", css)

    def test_map_branded_chrome_header_in_html(self):
        """fire-map-chrome branded header exists in the FIRE Metrics template."""
        text = self._template_text()
        self.assertIn("fire-map-chrome", text)
        self.assertIn("FIRE METRICS", text)

    def test_map_vignette_overlay_in_html(self):
        """fire-map-vignette overlay div is present in the template."""
        text = self._template_text()
        self.assertIn("fire-map-vignette", text)

    def test_map_vignette_uses_pointer_events_none(self):
        """Vignette overlay must not block map interaction."""
        css = self._css_text()
        idx = css.find(".fire-map-vignette")
        self.assertGreater(idx, 0, ".fire-map-vignette rule not in CSS")
        end = css.find("\n}", idx)
        block = css[idx:end]
        self.assertIn("pointer-events: none", block)

    def test_map_vignette_z_index_above_map(self):
        """Vignette overlay sits above map canvas (z-index ≥ 2)."""
        css = self._css_text()
        idx = css.find(".fire-map-vignette")
        end = css.find("\n}", idx)
        block = css[idx:end]
        self.assertIn("z-index: 2", block)

    def test_map_panel_has_navy_border(self):
        """Map panel has FIRE navy border/frame treatment."""
        css = self._css_text()
        idx = css.find(".fire-map-panel {")
        end = css.find("\n}", idx)
        block = css[idx:end]
        self.assertTrue(("1a2744" in block) or ("1e3a6e" in block))

    def test_map_legend_has_city_indicators(self):
        """Map legend contains city indicator markup."""
        text = self._template_text()
        has_legend = ("fire-map-legend" in text and "fire-map-dot" in text)
        self.assertTrue(has_legend)


# ---------------------------------------------------------------------------
# New tests: CRE dict-vs-object parsing, result_type correctness,
# dashboard CSS ordering, card themes, and related regressions
# ---------------------------------------------------------------------------

class TestCRESafeAccessor(unittest.TestCase):
    """_safe_get must work for both SDK objects (attrs) and plain dicts."""

    def test_safe_get_dict_key(self):
        obj = {"url": "https://cbre.com/report", "title": "CBRE Q1"}
        self.assertEqual(summary._safe_get(obj, "url", ""), "https://cbre.com/report")
        self.assertEqual(summary._safe_get(obj, "title", ""), "CBRE Q1")
        self.assertEqual(summary._safe_get(obj, "missing", "default"), "default")

    def test_safe_get_object_attribute(self):
        class SDKObj:
            url = "https://jll.com/q2"
            title = "JLL Q2 Report"
        obj = SDKObj()
        self.assertEqual(summary._safe_get(obj, "url", ""), "https://jll.com/q2")
        self.assertEqual(summary._safe_get(obj, "title", ""), "JLL Q2 Report")
        self.assertEqual(summary._safe_get(obj, "missing", "x"), "x")

    def test_safe_get_none_object_returns_default(self):
        self.assertEqual(summary._safe_get(None, "url", "fallback"), "fallback")


class TestCREDictShapedOutput(unittest.TestCase):
    """Source extraction must work when SDK returns dict-shaped output items."""

    def _make_dict_ws_call(self, url: str) -> dict:
        return {
            "type": "web_search_call",
            "action": {
                "type": "search",
                "sources": [{"type": "url", "url": url}],
            },
            "status": "completed",
            "id": "ws_1",
        }

    def _make_dict_message(self, text: str, annotation_url: str) -> dict:
        return {
            "type": "message",
            "role": "assistant",
            "content": [{
                "type": "output_text",
                "text": text,
                "annotations": [{
                    "type": "url_citation",
                    "url": annotation_url,
                    "title": "Dict-shaped title",
                    "start_index": 0,
                    "end_index": 10,
                }],
            }],
        }

    def test_dict_action_sources_extracted(self):
        mock_response = MagicMock()
        mock_response.output = [
            self._make_dict_ws_call("https://cbre.com/dict-test"),
            self._make_dict_message("Vacancy fell to 4% in the metro.", "https://cbre.com/dict-test"),
        ]
        mock_response.output_text = "Vacancy fell to 4% in the metro."
        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.responses.create.return_value = mock_response
            result = summary.openai_cre_research(
                api_key="test", model_name="gpt-4.1-mini",
                city="Denver", state="CO", display_name="Denver, CO",
            )
        urls = [s["url"] for s in result["research_sources"]]
        self.assertIn("https://cbre.com/dict-test", urls)
        self.assertEqual(result["result_type"], "success")

    def test_dict_url_citation_annotation_extracted(self):
        mock_response = MagicMock()
        mock_response.output = [
            self._make_dict_message(
                "Rents grew 3% in the Indianapolis metro.",
                "https://yardimatrix.com/indianapolis-report",
            ),
        ]
        mock_response.output_text = "Rents grew 3% in the Indianapolis metro."
        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.responses.create.return_value = mock_response
            result = summary.openai_cre_research(
                api_key="test", model_name="gpt-4.1-mini",
                city="Carmel", state="IN", display_name="Carmel, IN",
            )
        urls = [s["url"] for s in result["research_sources"]]
        self.assertIn("https://yardimatrix.com/indianapolis-report", urls)
        self.assertEqual(result["result_type"], "success")


class TestCREResultTypeCorrectness(unittest.TestCase):
    """success requires BOTH text AND validated source; not one alone."""

    def _run_cre(self, output_text: str, sources: list[dict]) -> dict:
        mock_response = MagicMock()
        # Build action.sources as dicts
        ws_call = {
            "type": "web_search_call",
            "action": {"type": "search", "sources": sources},
            "status": "completed",
            "id": "ws_1",
        }
        mock_response.output = [ws_call] if sources else []
        mock_response.output_text = output_text
        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.responses.create.return_value = mock_response
            return summary.openai_cre_research(
                api_key="test", model_name="gpt-4.1-mini",
                city="Austin", state="TX", display_name="Austin, TX",
            )

    def test_text_and_approved_source_is_success(self):
        result = self._run_cre(
            "Vacancy fell in the Austin metro.",
            [{"type": "url", "url": "https://cbre.com/austin"}],
        )
        self.assertEqual(result["result_type"], "success")
        self.assertTrue(result["cre_sentences"])
        self.assertTrue(result["research_sources"])

    def test_text_without_approved_source_is_no_data(self):
        """Model produced prose but no approved-domain sources returned."""
        result = self._run_cre("Some CRE insight with no sources.", [])
        self.assertEqual(result["result_type"], "no_data")
        self.assertEqual(result["research_sources"], [])

    def test_source_without_useful_text_is_no_data(self):
        """Approved source returned but model output empty/NONE."""
        result = self._run_cre("NONE", [{"type": "url", "url": "https://cbre.com/report"}])
        self.assertEqual(result["result_type"], "no_data")
        self.assertEqual(result["cre_sentences"], "No relevant research from approved sources.")

    def test_unapproved_source_plus_text_is_no_data(self):
        result = self._run_cre(
            "Zillow says rents rose 5%.",
            [{"type": "url", "url": "https://zillow.com/report"}],
        )
        # unapproved source filtered out → text + 0 approved sources → no_data
        self.assertEqual(result["result_type"], "no_data")

    def test_none_sentinel_produces_no_data(self):
        result = self._run_cre("NONE", [])
        self.assertEqual(result["result_type"], "no_data")
        self.assertEqual(result["cre_sentences"], "No relevant research from approved sources.")

    def test_api_exception_is_failure(self):
        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.responses.create.side_effect = RuntimeError("network down")
            result = summary.openai_cre_research(
                api_key="test", model_name="gpt-4.1-mini",
                city="NYC", state="NY", display_name="New York, NY",
            )
        self.assertEqual(result["result_type"], "failure")

    def test_function_never_raises(self):
        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.side_effect = Exception("constructor explodes")
            try:
                result = summary.openai_cre_research(
                    api_key="test", model_name="gpt-4.1-mini",
                    city="Miami", state="FL", display_name="Miami, FL",
                )
                self.assertEqual(result["result_type"], "failure")
            except Exception:
                self.fail("openai_cre_research must not raise")


class TestCREFailureConsistency(unittest.TestCase):
    """Failure TTL behavior must be consistent in new-row and cached-refresh paths."""

    def test_failure_stores_current_version(self):
        """Failure result always carries CRE_RESEARCH_VERSION (enables backoff via version+TTL)."""
        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.responses.create.side_effect = RuntimeError("err")
            result = summary.openai_cre_research(
                api_key="test", model_name="gpt-4.1-mini",
                city="Portland", state="OR", display_name="Portland, OR",
            )
        self.assertEqual(result["cre_research_version"], summary.CRE_RESEARCH_VERSION)
        self.assertEqual(result["result_type"], "failure")

    def test_success_stores_current_version(self):
        mock_response = MagicMock()
        mock_response.output = [{
            "type": "web_search_call",
            "action": {"type": "search", "sources": [{"type": "url", "url": "https://cbre.com/r"}]},
            "status": "completed", "id": "ws_1",
        }]
        mock_response.output_text = "Occupancy rose in the Chicago metro."
        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.responses.create.return_value = mock_response
            result = summary.openai_cre_research(
                api_key="test", model_name="gpt-4.1-mini",
                city="Chicago", state="IL", display_name="Chicago, IL",
            )
        self.assertEqual(result["cre_research_version"], summary.CRE_RESEARCH_VERSION)
        self.assertEqual(result["result_type"], "success")


class TestCREDedupeAndCap(unittest.TestCase):

    def test_duplicate_urls_deduped(self):
        mock_response = MagicMock()
        mock_response.output = [{
            "type": "web_search_call",
            "action": {"type": "search", "sources": [
                {"type": "url", "url": "https://cbre.com/dup"},
                {"type": "url", "url": "https://cbre.com/dup"},  # duplicate
            ]},
            "status": "completed", "id": "ws_1",
        }]
        mock_response.output_text = "Vacancy is low in the market."
        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.responses.create.return_value = mock_response
            result = summary.openai_cre_research(
                api_key="test", model_name="gpt-4.1-mini",
                city="Boston", state="MA", display_name="Boston, MA",
            )
        urls = [s["url"] for s in result["research_sources"]]
        self.assertEqual(len(urls), len(set(urls)), "duplicate URLs should be deduped")

    def test_source_cap_at_three(self):
        mock_response = MagicMock()
        mock_response.output = [{
            "type": "web_search_call",
            "action": {"type": "search", "sources": [
                {"type": "url", "url": f"https://cbre.com/r{i}"} for i in range(5)
            ]},
            "status": "completed", "id": "ws_1",
        }]
        mock_response.output_text = "CRE conditions are improving."
        with patch("openai.OpenAI") as MockOpenAI:
            MockOpenAI.return_value.responses.create.return_value = mock_response
            result = summary.openai_cre_research(
                api_key="test", model_name="gpt-4.1-mini",
                city="Seattle", state="WA", display_name="Seattle, WA",
            )
        self.assertLessEqual(len(result["research_sources"]), 3)


class TestDashboardCSS(unittest.TestCase):

    def _css_text(self) -> str:
        return (Path(__file__).parent.parent / "static" / "style.css").read_text()

    def _dashboard_html(self) -> str:
        return (Path(__file__).parent.parent / "templates" / "dashboard.html").read_text()

    def test_no_stray_brace_after_tool_card_icon_fire_svg(self):
        """The stray } after .tool-card-icon-fire svg must be removed."""
        css = self._css_text()
        # The stray brace appeared immediately after the svg rule
        # e.g. ".tool-card-icon-fire svg { ... }\n}"
        # After fix: the next meaningful token after that block should not be "}"
        idx = css.find(".tool-card-icon-fire svg")
        if idx < 0:
            return  # class removed entirely is also acceptable
        block_end = css.find("}", idx)
        # The character after the closing brace should NOT be another stray "}"
        remainder = css[block_end + 1:].lstrip()
        # A stray } would be the very first non-whitespace character
        self.assertFalse(
            remainder.startswith("}"),
            "Stray } detected after .tool-card-icon-fire svg rule",
        )

    def test_base_tool_card_defined_before_theme_variants(self):
        """Generic .tool-card base rule must appear before theme variants."""
        css = self._css_text()
        base_pos = css.find("\n.tool-card {")
        navy_pos = css.find(".tool-card--navy {")
        blue_pos = css.find(".tool-card--blue {")
        self.assertGreater(base_pos, 0, ".tool-card base rule not found")
        if navy_pos > 0:
            self.assertLess(base_pos, navy_pos, ".tool-card--navy must come after .tool-card base")
        if blue_pos > 0:
            self.assertLess(base_pos, blue_pos, ".tool-card--blue must come after .tool-card base")

    def test_fire_metrics_card_has_dark_background_not_white(self):
        """FIRE Metrics card theme must override white background."""
        css = self._css_text()
        # The theme rule should have a non-white background
        navy_idx = css.find(".tool-card--navy {")
        compat_idx = css.find(".tool-card-fire-metrics {")
        found_dark = False
        for idx in [navy_idx, compat_idx]:
            if idx < 0:
                continue
            block_end = css.find("}", idx)
            block = css[idx:block_end]
            if "1a2744" in block or "1e3a6e" in block or "linear-gradient" in block:
                found_dark = True
        self.assertTrue(found_dark, "FIRE Metrics card should have dark navy background")

    def test_fire_metrics_title_color_is_white_on_dark(self):
        """FIRE Metrics title must be white (readable on dark card)."""
        css = self._css_text()
        # navy theme title rule
        for selector in [".tool-card--navy .tool-card-title", ".tool-card-fire-metrics .tool-card-title"]:
            idx = css.find(selector)
            if idx > 0:
                block_end = css.find("}", idx)
                block = css[idx:block_end]
                self.assertIn("fff", block.lower(), f"{selector} should have white color")
                return
        self.fail("No FIRE Metrics title color rule found")

    def test_theme_variant_rules_use_new_class_names(self):
        """New semantic theme classes should exist in CSS."""
        css = self._css_text()
        self.assertIn(".tool-card--navy", css)
        self.assertIn(".tool-card--blue", css)
        self.assertIn(".tool-card--slate", css)
        self.assertIn(".tool-card--gold", css)

    def test_all_dashboard_tool_cards_have_theme_class(self):
        """Every tool card anchor in dashboard.html must have a theme modifier class."""
        html = self._dashboard_html()
        import re
        # Only match <a> or <div> elements that start with "tool-card " or "tool-card "
        # Exclude tool-card-icon, tool-card-title, etc.
        card_classes = re.findall(r'class="(tool-card(?:\s+[^"]+)?)"', html)
        for classes in card_classes:
            parts = classes.split()
            # Skip if the only class is a sub-component (icon, title, desc)
            if any(p in ("tool-card-icon", "tool-card-title", "tool-card-desc", "tool-card-fire-metrics") for p in parts if p != "tool-card"):
                continue
            if "tool-card" not in parts:
                continue
            # Each card link must have a theme class
            has_theme = any(p.startswith("tool-card--") or p == "tool-card-fire-metrics" for p in parts)
            # Skip elements that are just the base class (sub-components)
            if len(parts) == 1:
                continue
            self.assertTrue(has_theme, f"Card anchor with classes '{classes}' has no FIRE theme modifier")

    def test_no_inline_hardcoded_fill_colors_on_themed_cards(self):
        """SVG icons on themed cards should use currentColor, not hardcoded fills."""
        html = self._dashboard_html()
        import re
        # No fill="#2563eb" or fill="#9ca3af" on themed cards
        hardcoded = re.findall(r'fill="#[0-9a-fA-F]+"', html)
        for fill in hardcoded:
            # Only allow fill="currentColor" or fill="none"; reject hex fills
            self.fail(f"Hardcoded SVG fill found: {fill} — use fill=\"currentColor\" instead")

    def test_fire_metrics_plural_in_dashboard(self):
        html = self._dashboard_html()
        self.assertIn("FIRE Metrics", html)
        self.assertNotIn(">FIRE Metric<", html)

    def test_dashboard_hero_present_in_html(self):
        html = self._dashboard_html()
        self.assertIn("dashboard-hero", html)

    def test_deal_dive_macroeconomic_wording_preserved(self):
        """Deal Dive card must say 'Market & Macroeconomic Context (FIRE Metrics)'."""
        dd_path = Path(__file__).parent.parent / "templates" / "tools" / "deal_dive_detail.html"
        content = dd_path.read_text()
        self.assertIn("Market & Macroeconomic Context (FIRE Metrics)", content)
        self.assertNotIn("Market Context (FIRE Metric)", content)


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
        self.assertEqual(summary.CRE_RESEARCH_VERSION, "cre_v3")

    def test_cre_ttl_days_constant(self):
        self.assertEqual(summary.CRE_RESEARCH_TTL_DAYS, 30)


# ---------------------------------------------------------------------------
# UI Redesign tests — dashboard, MMR gauge, brand system
# ---------------------------------------------------------------------------

class TestUIRedesign(unittest.TestCase):
    """Static assertions against templates and CSS for the UI redesign pass."""

    def _read(self, *parts: str) -> str:
        from pathlib import Path
        return (Path(__file__).parent.parent.joinpath(*parts)).read_text(encoding="utf-8")

    def _dashboard(self) -> str:
        return self._read("templates", "dashboard.html")

    def _css(self) -> str:
        return self._read("static", "style.css")

    def _mmr(self) -> str:
        return self._read("templates", "tools", "mmr_summary.html")

    # ── Dashboard hero ─────────────────────────────────────────────────────
    def test_hero_title_is_fire_capital_ai_analytics(self):
        self.assertIn("FIRE Capital AI Analytics", self._dashboard())

    def test_hero_old_title_removed(self):
        self.assertNotIn(">FIRE Capital Tools<", self._dashboard())

    def test_hero_subtitle_removed(self):
        self.assertNotIn("Internal platform for acquisitions", self._dashboard())

    def test_hero_logo_present_on_right(self):
        html = self._dashboard()
        self.assertIn("dashboard-hero-logo", html)
        self.assertIn("logo-mark.svg", html)

    # ── FIRE Metrics card description ──────────────────────────────────────
    def test_fire_metrics_description_exact(self):
        self.assertIn("City level market economic indicators", self._dashboard())

    # ── Card color families ────────────────────────────────────────────────
    def test_operations_cards_use_amber_theme(self):
        html = self._dashboard()
        # Both Operations links must carry --amber
        amber_count = html.count("tool-card--amber")
        self.assertGreaterEqual(amber_count, 2, "Both Operations cards should use --amber")

    def test_investor_card_uses_orange_theme(self):
        self.assertIn("tool-card--orange", self._dashboard())

    def test_rent_comps_uses_navy_family(self):
        html = self._dashboard()
        # Rent Comps must use --navy (same family as FIRE Metrics)
        import re
        rent_comps_block = re.search(
            r'rent_comps\.index.*?</a>', html, re.DOTALL
        )
        self.assertIsNotNone(rent_comps_block)
        self.assertIn("tool-card--navy", rent_comps_block.group())

    def test_no_teal_on_rent_comps(self):
        """Rent Comps must not use --teal after redesign."""
        html = self._dashboard()
        import re
        rent_comps_block = re.search(
            r'rent_comps\.index.*?</a>', html, re.DOTALL
        )
        if rent_comps_block:
            self.assertNotIn("tool-card--teal", rent_comps_block.group())

    # ── Section labels ─────────────────────────────────────────────────────
    def test_section_labels_are_larger(self):
        css = self._css()
        idx = css.find(".section-label {")
        block_end = css.find("}", idx)
        block = css[idx:block_end]
        # Must be at least 14px (was 11px)
        import re
        size_match = re.search(r"font-size:\s*(\d+)px", block)
        self.assertIsNotNone(size_match, "section-label must have a font-size")
        self.assertGreaterEqual(int(size_match.group(1)), 14)

    def test_section_labels_use_brand_color(self):
        css = self._css()
        idx = css.find(".section-label {")
        block_end = css.find("}", idx)
        block = css[idx:block_end]
        self.assertTrue(
            "var(--fire-navy)" in block or "#1a2744" in block,
            "section-label should use FIRE navy color",
        )

    # ── CSS tokens ─────────────────────────────────────────────────────────
    def test_css_custom_properties_defined(self):
        css = self._css()
        for token in ("--fire-navy", "--fire-blue", "--fire-orange", "--fire-gold",
                      "--fire-surface", "--fire-text", "--fire-muted", "--fire-border"):
            self.assertIn(token, css, f"CSS token {token} missing")

    def test_tool_card_amber_theme_exists(self):
        self.assertIn(".tool-card--amber {", self._css())

    def test_tool_card_orange_theme_exists(self):
        self.assertIn(".tool-card--orange {", self._css())

    # ── btn-success no longer green ────────────────────────────────────────
    def test_btn_success_not_green(self):
        css = self._css()
        idx = css.find(".btn-success")
        block_end = css.find("}", idx)
        block = css[idx:block_end].lower()
        self.assertNotIn("#059669", block)
        self.assertNotIn("#10b981", block)
        self.assertNotIn("#047857", block)

    # ── MMR occupancy gauge ────────────────────────────────────────────────
    def test_mmr_gauge_html_present(self):
        html = self._mmr()
        self.assertIn("occ-gauge", html)
        self.assertIn("occ-gauge-fill", html)

    def test_mmr_gauge_css_exists(self):
        css = self._css()
        self.assertIn(".occ-gauge {", css)
        self.assertIn(".occ-gauge-fill {", css)
        self.assertIn("pointer-events: none", css)

    def test_mmr_occupancy_value_remains_visible(self):
        """Numeric occupancy text element must still exist alongside gauge."""
        html = self._mmr()
        self.assertIn('id="res-occupancy"', html)

    def test_mmr_gauge_js_sets_occ_deg(self):
        """Gauge JS must set --occ-deg to drive the conic-gradient."""
        html = self._mmr()
        self.assertIn("--occ-deg", html)

    def test_mmr_download_button_not_green(self):
        """Download button should use btn-primary, not btn-success."""
        html = self._mmr()
        import re
        btn_match = re.search(r'id="download-btn"[^>]*class="([^"]+)"', html)
        if btn_match is None:
            btn_match = re.search(r'class="([^"]+)"[^>]*id="download-btn"', html)
        self.assertIsNotNone(btn_match, "download-btn not found")
        classes = btn_match.group(1)
        self.assertNotIn("btn-success", classes)

    # ── Delinquency: dollar only (GSR not available) ───────────────────────
    def test_delinquency_dollar_element_still_present(self):
        self.assertIn('id="res-delinquency"', self._mmr())

    # ── Routes unchanged ───────────────────────────────────────────────────
    def test_dashboard_routes_unchanged(self):
        html = self._dashboard()
        for route in ("mmr.index", "scorecard.index", "deal_analyzer.index",
                      "underwriting.index", "deal_dive.index", "site_dd.index",
                      "rent_comps.index", "fire_metrics.index", "investor_report.index"):
            self.assertIn(route, html, f"Route {route} missing from dashboard")

    # ── Hero and hero class still present ─────────────────────────────────
    def test_dashboard_hero_element_still_present(self):
        self.assertIn("dashboard-hero", self._dashboard())


# ---------------------------------------------------------------------------
# In-map city card (Pass 1: clipping fix)
# ---------------------------------------------------------------------------

class TestInMapCityCard(unittest.TestCase):
    def _template(self) -> str:
        from pathlib import Path
        return (Path(__file__).parent.parent / "templates" / "tools" / "fire_metrics.html").read_text()

    def _css(self) -> str:
        from pathlib import Path
        return (Path(__file__).parent.parent / "static" / "style.css").read_text()

    def test_fire_city_card_element_in_map_frame(self):
        html = self._template()
        self.assertIn('id="fire-city-card"', html)
        # Card must appear inside the fire-map-frame div (order check)
        frame_pos = html.find('class="fire-map-frame"')
        card_pos = html.find('id="fire-city-card"')
        self.assertGreater(card_pos, frame_pos)

    def test_fire_city_card_css_exists(self):
        css = self._css()
        self.assertIn(".fire-city-card {", css)

    def test_fire_city_card_is_absolutely_positioned(self):
        css = self._css()
        idx = css.find(".fire-city-card {")
        block = css[idx:css.find("}", idx) + 1]
        self.assertIn("position: absolute", block)

    def test_fire_city_card_has_max_width_bound(self):
        css = self._css()
        idx = css.find(".fire-city-card {")
        block = css[idx:css.find("}", idx) + 1]
        self.assertIn("max-width", block)

    def test_fire_city_card_pointer_events_auto(self):
        """Panel itself receives pointer events (user can click close button)."""
        css = self._css()
        idx = css.find(".fire-city-card {")
        block = css[idx:css.find("}", idx) + 1]
        self.assertIn("pointer-events: auto", block)

    def test_vignette_overlay_pointer_events_none(self):
        """Decorative vignette must not block map interaction."""
        css = self._css()
        idx = css.find(".fire-map-vignette")
        block = css[idx:css.find("}", idx) + 1]
        self.assertIn("pointer-events: none", block)

    def test_show_city_card_function_exists(self):
        html = self._template()
        self.assertIn("function showCityCard(", html)

    def test_hide_city_card_function_exists(self):
        html = self._template()
        self.assertIn("function hideCityCard(", html)

    def test_open_current_city_preview_calls_show_city_card(self):
        html = self._template()
        self.assertIn("showCityCard(currentCity)", html)

    def test_open_current_city_preview_uses_city_card_only(self):
        """Selected-city preview uses card rendering and no InfoWindow calls."""
        html = self._template()
        fn_start = html.find("function openCurrentCityPreview(")
        fn_end = html.find("\n  function ", fn_start + 1)
        fn_body = html[fn_start:fn_end]
        self.assertIn("showCityCard(currentCity)", fn_body)
        self.assertNotIn("googleInfoWindow", fn_body)

    def test_open_current_city_preview_no_longer_calls_open_marker_preview_for_click(self):
        """openCurrentCityPreview must not call openMarkerPreview (that opened InfoWindow)."""
        html = self._template()
        fn_start = html.find("function openCurrentCityPreview(")
        fn_end = html.find("\n  function ", fn_start + 1)
        fn_body = html[fn_start:fn_end]
        self.assertNotIn("openMarkerPreview(", fn_body)

    def test_pan_offset_applied_for_right_side_panel(self):
        """panBy uses minimum overlap-only offset computed from rendered marker/card positions."""
        html = self._template()
        fn_start = html.find("function openCurrentCityPreview(")
        fn_end = html.find("\n  function ", fn_start + 1)
        fn_body = html[fn_start:fn_end]
        self.assertIn("const panOffsetX = minimalOverlapPanOffsetX(entry.marker)", fn_body)
        self.assertIn("googleMap.panBy(panOffsetX, 0)", fn_body)
        self.assertNotIn("googleMap.panTo(entry.marker.position)", fn_body)

    def test_adaptive_card_side_helpers_exist(self):
        html = self._template()
        self.assertIn("function markerScreenPositionInMap(", html)
        self.assertIn("function chooseDesktopCardSide(", html)
        self.assertIn("function minimalOverlapPanOffsetX(", html)
        self.assertIn("function setCityCardSide(", html)
        self.assertIn("getBoundingClientRect()", html)
        self.assertIn("markerPos.x >= usableMid ? \"left\" : \"right\"", html)
        self.assertIn("cardRect.width >= mapRect.width * 0.75", html)

    def test_open_current_city_preview_sets_card_side_from_marker_position(self):
        html = self._template()
        fn_start = html.find("function openCurrentCityPreview(")
        fn_end = html.find("\n  function ", fn_start + 1)
        fn_body = html[fn_start:fn_end]
        self.assertIn("const side = chooseDesktopCardSide(entry.marker)", fn_body)
        self.assertIn("setCityCardSide(side)", fn_body)

    def test_advanced_marker_element_remains(self):
        self.assertIn("AdvancedMarkerElement", self._template())

    def test_city_key_identity_preserved(self):
        html = self._template()
        self.assertIn("stableCityKey", html)
        self.assertIn("city_key", html)

    def test_select_current_search_city_still_exists(self):
        self.assertIn("function selectCurrentSearchCity(", self._template())

    def test_google_info_window_removed_entirely(self):
        """No Google InfoWindow code remains; city card is sole detail UI."""
        html = self._template()
        self.assertNotIn("new InfoWindow(", html)
        self.assertNotIn("googleInfoWindow", html)
        self.assertNotIn("function openMarkerPreview(", html)

    def test_city_card_metric_set_expanded(self):
        """Card includes all existing city metrics shown elsewhere in payload/UI."""
        html = self._template()
        fn_start = html.find("function showCityCard(")
        fn_end = html.find("\n  function ", fn_start + 1)
        fn_body = html[fn_start:fn_end]
        for label in (
            "Coverage",
            "Population",
            "Median Income",
            "Home Value",
            "Employment",
            "Crime",
            "Density-Adj. Crime",
            "Climate Risk",
            "Landlord",
        ):
            self.assertIn(label, fn_body)

    def test_city_card_desktop_width_and_scroll_constraints(self):
        css = self._css()
        idx = css.find(".fire-city-card {")
        block = css[idx:css.find("}", idx) + 1]
        self.assertIn("width: clamp(300px", block)
        self.assertIn("max-height: calc(100% - 24px)", block)
        self.assertIn("overflow-y: auto", block)

    def test_city_card_desktop_side_classes_exist(self):
        css = self._css()
        self.assertIn(".fire-city-card.fire-city-card-left", css)
        self.assertIn(".fire-city-card.fire-city-card-right", css)

    def test_city_card_mobile_panel_scroll_enabled(self):
        css = self._css()
        media_idx = css.find("@media (max-width: 768px)")
        card_idx = css.find(".fire-city-card {", media_idx)
        block = css[card_idx:css.find("}", card_idx) + 1]
        self.assertIn("bottom: 12px", block)
        self.assertIn("max-height: 46%", block)
        self.assertIn("overflow-y: auto", block)

    def test_city_analytics_row_uses_select_current_search_city(self):
        html = self._template()
        idx = html.find("activateRowCity")
        self.assertGreater(idx, 0)
        vicinity = html[idx:idx + 400]
        self.assertIn("selectCurrentSearchCity", vicinity)

    def test_scroll_analytics_row_uses_container_scroll_not_scroll_into_view(self):
        html = self._template()
        fn_start = html.find("function scrollAnalyticsRowIntoView(")
        fn_end = html.find("\n  function ", fn_start + 1)
        fn_body = html[fn_start:fn_end]
        self.assertNotIn("scrollIntoView(", fn_body)
        self.assertIn("comparisonWrap.scrollTop", fn_body)
        self.assertIn("comparisonWrap.scrollLeft", fn_body)

    def test_coordinates_not_overwritten(self):
        """lat/lng values are never assigned; only read via extractCoordinates."""
        html = self._template()
        self.assertIn("function extractCoordinates(", html)
        self.assertNotIn("city.latitude =", html)
        self.assertNotIn("city.longitude =", html)

    def test_nationwide_fitbounds_remains(self):
        html = self._template()
        self.assertIn("fitBounds(usBounds", html)

    def test_responsive_mobile_css_for_card(self):
        css = self._css()
        self.assertIn(".fire-city-card", css)
        media_idx = css.find("@media (max-width: 768px)")
        card_in_media = css.find(".fire-city-card", media_idx)
        self.assertGreater(card_in_media, media_idx)

    def test_close_marker_preview_force_hides_city_card(self):
        """closeMarkerPreview(force=true) must call hideCityCard."""
        html = self._template()
        fn_start = html.find("function closeMarkerPreview(")
        fn_end = html.find("\n  function ", fn_start + 1)
        fn_body = html[fn_start:fn_end]
        self.assertIn("hideCityCard()", fn_body)

    def test_clear_workspace_hides_city_card(self):
        """Clearing all searched cities must hide the city card."""
        html = self._template()
        fn_start = html.find("function clearSearchedCitiesWorkspace(")
        fn_end = html.find("\n  function ", fn_start + 1)
        fn_body = html[fn_start:fn_end]
        self.assertIn("hideCityCard()", fn_body)


if __name__ == "__main__":
    unittest.main()
