"""
Unit tests for the shared OpenAI usage counter.

Two properties matter more than the arithmetic.

The first is that a cache hit must never increment. That is what makes
the number mean "spend" rather than "page views", and it is the same
guarantee the RentCast and Google Places counters make. It is enforced by
WHERE the call is recorded -- inside the function that makes the request,
after the response comes back -- so a caller cannot forget to count and a
cached path cannot accidentally count.

The second is that recording must never raise. A counter is bookkeeping.
A failure to write it should cost a row in a report, not turn a working
market summary into an error page.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools import openai_usage as ou


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "usage.db"

    def test_the_table_follows_the_existing_counter_shape(self):
        with ou.get_connection(self.path) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(openai_usage)")]
        for name in ("year_month", "feature", "calls",
                     "prompt_tokens", "completion_tokens"):
            self.assertIn(name, cols)

    def test_month_and_feature_together_are_the_key(self):
        with ou.get_connection(self.path) as conn:
            pk = [r[1] for r in conn.execute("PRAGMA table_info(openai_usage)") if r[5]]
        self.assertEqual(sorted(pk), ["feature", "year_month"])

    def test_init_is_idempotent(self):
        for _ in range(4):
            with ou.get_connection(self.path) as conn:
                pass
        with ou.get_connection(self.path) as conn:
            n = conn.execute("SELECT COUNT(*) FROM openai_usage").fetchone()[0]
        self.assertEqual(n, 0)

    def test_the_path_follows_the_env_var_pattern(self):
        import os
        old = os.environ.get("OPENAI_USAGE_DB_PATH")
        try:
            os.environ["OPENAI_USAGE_DB_PATH"] = str(self.path)
            self.assertEqual(ou.get_db_path(), self.path)
            os.environ["OPENAI_USAGE_DB_PATH"] = ""
            self.assertEqual(ou.get_db_path().name, "openai_usage.db")
        finally:
            if old is None:
                os.environ.pop("OPENAI_USAGE_DB_PATH", None)
            else:
                os.environ["OPENAI_USAGE_DB_PATH"] = old


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "usage.db"

    def _row(self, feature, ym="2026-08"):
        with ou.get_connection(self.path) as conn:
            r = conn.execute(
                "SELECT * FROM openai_usage WHERE year_month = ? AND feature = ?",
                (ym, feature)).fetchone()
        return dict(r) if r else None

    def test_one_call_is_one_call(self):
        ou.record(ou.FEATURE_FIRE_METRICS_SUMMARY, year_month="2026-08",
                  db_path=self.path)
        self.assertEqual(self._row(ou.FEATURE_FIRE_METRICS_SUMMARY)["calls"], 1)

    def test_calls_accumulate(self):
        for _ in range(5):
            ou.record(ou.FEATURE_FIRE_METRICS_CRE, year_month="2026-08",
                      db_path=self.path)
        self.assertEqual(self._row(ou.FEATURE_FIRE_METRICS_CRE)["calls"], 5)

    def test_features_are_counted_separately(self):
        ou.record(ou.FEATURE_FIRE_METRICS_SUMMARY, year_month="2026-08", db_path=self.path)
        ou.record(ou.FEATURE_FIRE_METRICS_CRE, year_month="2026-08", db_path=self.path)
        ou.record(ou.FEATURE_FIRE_METRICS_CRE, year_month="2026-08", db_path=self.path)
        self.assertEqual(self._row(ou.FEATURE_FIRE_METRICS_SUMMARY)["calls"], 1)
        self.assertEqual(self._row(ou.FEATURE_FIRE_METRICS_CRE)["calls"], 2)

    def test_months_are_counted_separately(self):
        ou.record("x", year_month="2026-07", db_path=self.path)
        ou.record("x", year_month="2026-08", db_path=self.path)
        self.assertEqual(self._row("x", "2026-07")["calls"], 1)
        self.assertEqual(self._row("x", "2026-08")["calls"], 1)

    def test_tokens_accumulate(self):
        ou.record("x", prompt_tokens=100, completion_tokens=20,
                  year_month="2026-08", db_path=self.path)
        ou.record("x", prompt_tokens=50, completion_tokens=5,
                  year_month="2026-08", db_path=self.path)
        row = self._row("x")
        self.assertEqual(row["prompt_tokens"], 150)
        self.assertEqual(row["completion_tokens"], 25)
        self.assertEqual(row["calls"], 2)

    def test_a_call_with_no_token_data_still_counts(self):
        """A response whose usage block moved or is absent must not be
        dropped -- an uncounted call is worse than an untokened one."""
        ou.record("x", year_month="2026-08", db_path=self.path)
        row = self._row("x")
        self.assertEqual(row["calls"], 1)
        self.assertEqual(row["prompt_tokens"], 0)

    def test_an_unknown_feature_is_stored_not_rejected(self):
        """A new feature must be able to record before anyone updates the
        label table. Losing a spend record to keep a vocabulary tidy is
        the wrong trade."""
        ou.record("some_future_tool", year_month="2026-08", db_path=self.path)
        self.assertEqual(self._row("some_future_tool")["calls"], 1)

    def test_an_empty_feature_becomes_unattributed_rather_than_blank(self):
        ou.record("", year_month="2026-08", db_path=self.path)
        self.assertEqual(self._row("unattributed")["calls"], 1)

    def test_a_long_feature_key_is_bounded(self):
        ou.record("z" * 500, year_month="2026-08", db_path=self.path)
        with ou.get_connection(self.path) as conn:
            keys = [r["feature"] for r in conn.execute(
                "SELECT feature FROM openai_usage")]
        self.assertTrue(all(len(k) <= ou.MAX_FEATURE_LEN for k in keys))

    def test_recording_never_raises(self):
        """The whole contract. An unwritable path must not take down the
        feature that was trying to count."""
        bad = Path(tempfile.mkdtemp()) / "nope.db"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("this is not a database", encoding="utf-8")
        try:
            ou.record("x", year_month="2026-08", db_path=bad)
        except Exception as exc:  # pragma: no cover
            self.fail(f"record() raised {type(exc).__name__}: {exc}")

    def test_a_broken_response_object_never_raises(self):
        class Exploding:
            @property
            def usage(self):
                raise RuntimeError("boom")
        try:
            ou.record("x", Exploding(), year_month="2026-08", db_path=self.path)
        except Exception as exc:  # pragma: no cover
            self.fail(f"record() raised {type(exc).__name__}: {exc}")


class TokenExtractionTests(unittest.TestCase):
    """The SDKs disagree on field names and have changed them before."""

    def test_responses_api_field_names(self):
        class U:
            input_tokens, output_tokens = 120, 30
        class R:
            usage = U()
        self.assertEqual(ou.tokens_from_response(R()), (120, 30))

    def test_chat_completions_field_names(self):
        class U:
            prompt_tokens, completion_tokens = 90, 15
        class R:
            usage = U()
        self.assertEqual(ou.tokens_from_response(R()), (90, 15))

    def test_a_plain_dict_response(self):
        self.assertEqual(
            ou.tokens_from_response({"usage": {"input_tokens": 7, "output_tokens": 3}}),
            (7, 3))

    def test_no_usage_block_is_zero_not_an_error(self):
        self.assertEqual(ou.tokens_from_response(object()), (0, 0))
        self.assertEqual(ou.tokens_from_response(None), (0, 0))

    def test_a_non_numeric_token_count_is_zero(self):
        self.assertEqual(
            ou.tokens_from_response({"usage": {"input_tokens": "lots"}}), (0, 0))


class BreakdownTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "usage.db"

    def test_every_known_feature_appears_even_at_zero(self):
        """'Nothing spent' and 'not being counted' must not look the
        same on the page."""
        with ou.get_connection(self.path) as conn:
            out = ou.usage_for_month(conn, "2026-08")
        keys = [r["feature"] for r in out["rows"]]
        for known in ou.KNOWN_FEATURES:
            self.assertIn(known, keys)
        self.assertFalse(out["any_usage"])

    def test_totals_add_up(self):
        ou.record(ou.FEATURE_FIRE_METRICS_SUMMARY, prompt_tokens=100,
                  completion_tokens=10, year_month="2026-08", db_path=self.path)
        ou.record(ou.FEATURE_FIRE_METRICS_CRE, prompt_tokens=900,
                  completion_tokens=90, year_month="2026-08", db_path=self.path)
        with ou.get_connection(self.path) as conn:
            out = ou.usage_for_month(conn, "2026-08")
        self.assertEqual(out["total_calls"], 2)
        self.assertEqual(out["total_tokens"], 1100)
        self.assertTrue(out["any_usage"])

    def test_an_unrecognised_feature_is_surfaced_not_hidden(self):
        ou.record("mystery_spender", year_month="2026-08", db_path=self.path)
        with ou.get_connection(self.path) as conn:
            out = ou.usage_for_month(conn, "2026-08")
        row = next(r for r in out["rows"] if r["feature"] == "mystery_spender")
        self.assertFalse(row["known"])
        self.assertEqual(row["calls"], 1)

    def test_known_features_are_listed_before_unknown_ones(self):
        ou.record("aaa_unknown", year_month="2026-08", db_path=self.path)
        with ou.get_connection(self.path) as conn:
            rows = ou.usage_for_month(conn, "2026-08")["rows"]
        self.assertEqual([r["feature"] for r in rows[:len(ou.KNOWN_FEATURES)]],
                         list(ou.KNOWN_FEATURES))

    def test_another_months_usage_is_not_counted(self):
        ou.record(ou.FEATURE_FIRE_METRICS_CRE, year_month="2026-07",
                  db_path=self.path)
        with ou.get_connection(self.path) as conn:
            out = ou.usage_for_month(conn, "2026-08")
        self.assertEqual(out["total_calls"], 0)

    def test_get_usage_mirrors_the_rentcast_helper(self):
        ou.record("x", year_month="2026-08", db_path=self.path)
        with ou.get_connection(self.path) as conn:
            self.assertEqual(ou.get_usage(conn, "x", "2026-08"), 1)
            self.assertEqual(ou.get_usage(conn, "never_used", "2026-08"), 0)


class WiringTests(unittest.TestCase):
    """The counter is only worth anything if it is actually called from
    the places that spend."""

    def test_both_fire_metrics_call_sites_record(self):
        import inspect
        from tools import fire_metrics_ai_summary as ai

        summary_src = inspect.getsource(ai.openai_summary)
        self.assertIn("openai_usage.record", summary_src)
        self.assertIn("FEATURE_FIRE_METRICS_SUMMARY", summary_src)

        cre_src = inspect.getsource(ai.openai_cre_research)
        self.assertIn("openai_usage.record", cre_src)
        self.assertIn("FEATURE_FIRE_METRICS_CRE", cre_src)

    def test_recording_happens_after_the_request_not_before(self):
        """Counting before the call would count attempts that never
        happened; the figure has to mean money actually spent."""
        import inspect
        from tools import fire_metrics_ai_summary as ai
        for fn in (ai.openai_summary, ai.openai_cre_research):
            src = inspect.getsource(fn)
            with self.subTest(fn.__name__):
                self.assertLess(src.index("client.responses.create"),
                                src.index("openai_usage.record"))

    def test_the_costs_page_no_longer_claims_openai_is_uncounted(self):
        from tools import service_costs
        self.assertNotIn("OpenAI has no local counter", service_costs.__doc__)


if __name__ == "__main__":
    unittest.main()


class StorageStatusTests(unittest.TestCase):
    """A monthly counter that silently resets is worse than no counter:
    it reads as authoritative while under-reporting. The state has to be
    visible, not assumed."""

    def setUp(self):
        import os
        self.old = os.environ.get("OPENAI_USAGE_DB_PATH")

    def tearDown(self):
        import os
        if self.old is None:
            os.environ.pop("OPENAI_USAGE_DB_PATH", None)
        else:
            os.environ["OPENAI_USAGE_DB_PATH"] = self.old

    def test_an_unset_path_reports_as_not_persistent(self):
        import os
        os.environ.pop("OPENAI_USAGE_DB_PATH", None)
        st = ou.storage_status()
        self.assertFalse(st["persistent"])
        self.assertFalse(st["configured"])
        self.assertEqual(st["env_var"], "OPENAI_USAGE_DB_PATH")

    def test_a_configured_path_reports_as_persistent(self):
        import os
        os.environ["OPENAI_USAGE_DB_PATH"] = "/data/openai_usage.db"
        st = ou.storage_status()
        self.assertTrue(st["persistent"])
        # Compared as a Path: str() uses the host separator, and this test
        # runs on Windows as well as in the Linux container.
        self.assertEqual(Path(st["path"]), Path("/data/openai_usage.db"))
