"""
Unit tests for user-configurable grading bands.

Two things carry the weight.

The first is that an unconfigured install behaves EXACTLY as it did
before this existed -- same bands, same provenance, same disclaimer
string. A settings feature that quietly changed the default grade would
have moved a number Michelle has already looked at.

The second is that out-of-order bands are refused. They do not error at
render time; they produce a grade that silently never returns yellow,
because the first matching band wins and green already caught it. That
is the failure this validation exists for.
"""

import tempfile
import unittest
from pathlib import Path

from tools import app_settings
from tools import grading_settings as gs
from tools import quick_analyzer_math as calc


class DefaultBehaviourTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "settings.db"

    def test_nothing_configured_out_of_the_box(self):
        with app_settings.get_connection(self.path) as conn:
            loaded = gs.load(conn)
        self.assertFalse(loaded["configured"])

    def test_the_bands_are_the_module_defaults_object_for_object(self):
        with app_settings.get_connection(self.path) as conn:
            self.assertEqual(gs.load(conn)["bands"], calc.GRADE_BANDS)

    def test_the_disclaimer_is_unchanged(self):
        with app_settings.get_connection(self.path) as conn:
            loaded = gs.load(conn)
        self.assertEqual(loaded["provenance"], calc.PROVENANCE_UNCONFIRMED)
        self.assertEqual(loaded["disclaimer"],
                         calc.GRADE_DISCLAIMERS[calc.PROVENANCE_UNCONFIRMED])
        self.assertIn(calc.REQUIRED_DISCLAIMER_PHRASE, loaded["disclaimer"])

    def test_grade_with_no_arguments_is_identical_to_grade_with_defaults(self):
        """The compatibility guarantee, stated directly."""
        with app_settings.get_connection(self.path) as conn:
            loaded = gs.load(conn)
        for ask in (900_000, 1_000_000, 1_200_000, None):
            with self.subTest(ask=ask):
                self.assertEqual(
                    calc.grade(ask, 1_000_000),
                    calc.grade(ask, 1_000_000, bands=loaded["bands"],
                               provenance=loaded["provenance"]))


class ValidationTests(unittest.TestCase):
    def test_ordered_values_are_accepted(self):
        self.assertEqual(gs.validate("0", "5", "15"), (0.0, 5.0, 15.0))

    def test_a_negative_green_is_allowed(self):
        """'Green means at least 3% BELOW the implied price' is a coherent
        policy, and refusing it would be this module deciding strategy."""
        self.assertEqual(gs.validate("-3", "2", "8"), (-3.0, 2.0, 8.0))

    def test_out_of_order_is_refused(self):
        with self.assertRaises(gs.InvalidThresholds):
            gs.validate("5", "2", "8")

    def test_equal_bounds_are_refused(self):
        """Two bands with the same bound means the second is unreachable
        -- an invisible failure rather than a visible one."""
        with self.assertRaises(gs.InvalidThresholds):
            gs.validate("0", "0", "8")
        with self.assertRaises(gs.InvalidThresholds):
            gs.validate("0", "5", "5")

    def test_the_message_explains_the_consequence(self):
        with self.assertRaises(gs.InvalidThresholds) as ctx:
            gs.validate("5", "2", "8")
        msg = str(ctx.exception)
        self.assertIn("could ever grade Yellow", msg)
        # It names both offending numbers, so the fix is obvious.
        self.assertIn("5%", msg)
        self.assertIn("2%", msg)

    def test_missing_and_non_numeric_are_refused(self):
        for bad in ("", None, "abc", "  "):
            with self.subTest(bad=bad):
                with self.assertRaises(gs.InvalidThresholds):
                    gs.validate(bad, "5", "15")

    def test_percent_signs_and_commas_are_tolerated(self):
        self.assertEqual(gs.validate("0%", "5 %", "1,5"), (0.0, 5.0, 15.0))

    def test_absurd_bounds_are_refused(self):
        with self.assertRaises(gs.InvalidThresholds):
            gs.validate("0", "5", "900")
        with self.assertRaises(gs.InvalidThresholds):
            gs.validate("-500", "5", "15")


class ConfiguredTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "settings.db"
        with app_settings.get_connection(self.path) as conn:
            gs.save(conn, "-3", "2", "8")

    def _loaded(self):
        with app_settings.get_connection(self.path) as conn:
            return gs.load(conn)

    def test_it_reports_as_configured(self):
        loaded = self._loaded()
        self.assertTrue(loaded["configured"])
        self.assertEqual(loaded["values"],
                         {"green": -3.0, "yellow": 2.0, "orange": 8.0})

    def test_the_provenance_becomes_user(self):
        self.assertEqual(self._loaded()["provenance"], calc.PROVENANCE_USER)

    def test_the_disclaimer_stops_hedging_but_claims_no_authority(self):
        text = self._loaded()["disclaimer"]
        self.assertNotIn(calc.REQUIRED_DISCLAIMER_PHRASE, text)
        self.assertIn("Your configured thresholds", text)
        self.assertIn("not an industry benchmark", text)
        self.assertIn("not from the", text)

    def test_the_bands_grade_against_the_new_numbers(self):
        bands = self._loaded()["bands"]
        for over, expected in ((-5.0, "green"), (-3.0, "green"), (0.0, "yellow"),
                               (2.0, "yellow"), (6.0, "orange"), (20.0, "red")):
            with self.subTest(over=over):
                g = calc.grade(1_000_000 * (1 + over / 100), 1_000_000,
                               bands=bands)
                self.assertEqual(g["key"], expected)

    def test_the_band_meanings_quote_the_configured_numbers(self):
        meanings = " ".join(b.meaning for b in self._loaded()["bands"])
        self.assertIn("8%", meanings)
        # The stock text quoted 5% and 5-15% as literals; leaving those in
        # would contradict the number beside them.
        self.assertNotIn("5–15%", meanings)

    def test_red_stays_open_ended(self):
        self.assertIsNone(self._loaded()["bands"][-1].max_over_pct)

    def test_the_band_keys_and_order_are_untouched(self):
        self.assertEqual([b.key for b in self._loaded()["bands"]],
                         [b.key for b in calc.GRADE_BANDS])

    def test_clearing_restores_the_originals_exactly(self):
        with app_settings.get_connection(self.path) as conn:
            self.assertTrue(gs.clear(conn))
            loaded = gs.load(conn)
        self.assertFalse(loaded["configured"])
        self.assertEqual(loaded["bands"], calc.GRADE_BANDS)
        self.assertEqual(loaded["provenance"], calc.PROVENANCE_UNCONFIRMED)
        self.assertIn(calc.REQUIRED_DISCLAIMER_PHRASE, loaded["disclaimer"])

    def test_clearing_twice_is_not_an_error(self):
        with app_settings.get_connection(self.path) as conn:
            self.assertTrue(gs.clear(conn))
            self.assertFalse(gs.clear(conn))

    def test_a_stored_value_that_no_longer_validates_falls_back(self):
        """Rather than raising and taking the analyzer down. The
        placeholder disclaimer that comes with the fallback is then an
        accurate description of what is being used."""
        with app_settings.get_connection(self.path) as conn:
            app_settings.set_value(conn, gs.NAMESPACE, gs.KEY_BANDS,
                                   {"green": 9, "yellow": 2, "orange": 1})
            loaded = gs.load(conn)
        self.assertFalse(loaded["configured"])
        self.assertEqual(loaded["bands"], calc.GRADE_BANDS)
        self.assertEqual(loaded["provenance"], calc.PROVENANCE_UNCONFIRMED)


class ValuationIsolationTests(unittest.TestCase):
    """The bands reach the grading layer and nothing else."""

    INPUTS = {
        "gross_potential_income": 1_343_580.0,
        "vacancy_pct": 5.0,
        "other_income": 73_120.22,
        "expenses_mode": "amount",
        "operating_expenses": 700_000.0,
        "cap_rate_pct": 6.0,
        "noi_provenance": calc.PROVENANCE_BUILDUP,
    }

    def test_the_valuation_is_bit_identical_whatever_the_bands(self):
        custom = gs.bands_from(-3.0, 2.0, 8.0)
        a = calc.analyze(dict(self.INPUTS, asking_price=9_000_000))
        b = calc.analyze(dict(self.INPUTS, asking_price=9_000_000),
                         grade_bands=custom, grade_provenance=calc.PROVENANCE_USER)
        for field in ("noi", "implied_price", "effective_gross_income",
                      "operating_expenses", "range_low", "range_high"):
            if field in a:
                with self.subTest(field):
                    self.assertEqual(a[field], b[field])

    def test_quick_analyzer_math_never_reads_a_setting(self):
        """It stays pure: handed the bands, never fetching them."""
        src = Path("tools/quick_analyzer_math.py").read_text(encoding="utf-8")
        for forbidden in ("app_settings", "grading_settings", "sqlite3",
                          "get_connection"):
            with self.subTest(forbidden):
                self.assertNotIn(forbidden, src)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "settings.db"

    def test_namespaces_do_not_collide(self):
        """Deal Readiness will want the same mechanism; it must land as a
        namespace, not a migration."""
        with app_settings.get_connection(self.path) as conn:
            app_settings.set_value(conn, "quick_analyzer_grading", "bands", {"a": 1})
            app_settings.set_value(conn, "deal_readiness", "bands", {"a": 2})
            self.assertEqual(
                app_settings.get(conn, "quick_analyzer_grading", "bands"), {"a": 1})
            self.assertEqual(
                app_settings.get(conn, "deal_readiness", "bands"), {"a": 2})

    def test_clearing_one_namespace_leaves_the_other(self):
        with app_settings.get_connection(self.path) as conn:
            app_settings.set_value(conn, "a", "k", 1)
            app_settings.set_value(conn, "b", "k", 2)
            app_settings.clear(conn, "a")
            self.assertIsNone(app_settings.get(conn, "a", "k"))
            self.assertEqual(app_settings.get(conn, "b", "k"), 2)

    def test_a_corrupt_value_reads_as_absent(self):
        with app_settings.get_connection(self.path) as conn:
            conn.execute("INSERT INTO app_settings VALUES ('n','k','not json','now')")
            conn.commit()
            self.assertIsNone(app_settings.get(conn, "n", "k"))
            self.assertEqual(app_settings.get(conn, "n", "k", "fallback"),
                             "fallback")

    def test_the_path_follows_the_env_var_pattern(self):
        import os
        old = os.environ.get("APP_SETTINGS_DB_PATH")
        try:
            os.environ["APP_SETTINGS_DB_PATH"] = str(self.path)
            self.assertEqual(app_settings.get_db_path(), self.path)
            self.assertTrue(app_settings.storage_status()["persistent"])
            os.environ["APP_SETTINGS_DB_PATH"] = ""
            self.assertFalse(app_settings.storage_status()["persistent"])
        finally:
            if old is None:
                os.environ.pop("APP_SETTINGS_DB_PATH", None)
            else:
                os.environ["APP_SETTINGS_DB_PATH"] = old


if __name__ == "__main__":
    unittest.main()
