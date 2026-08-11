"""
Tests for the API & service cost inventory.

This page has no arithmetic to get wrong, so these tests guard the thing
that can actually go wrong instead: the data quietly becoming dishonest.
The failure mode here isn't a crash, it's a plausible-looking dollar
figure nobody confirmed, or a "live" badge on a number that is actually
six months stale. Both would be believed.
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import service_costs  # noqa: E402
from tools.service_costs import SERVICES, TBD  # noqa: E402

LIVE_COUNTER_KEYS = {"rentcast", "google_places"}


class ServiceInventoryTests(unittest.TestCase):
    def test_nine_services_inventoried(self):
        self.assertEqual(len(SERVICES), 9)

    def test_keys_are_unique(self):
        keys = [s.key for s in SERVICES]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_service_has_purpose_and_consumer(self):
        for s in SERVICES:
            with self.subTest(s.name):
                self.assertTrue(s.purpose.strip())
                self.assertTrue(s.used_by)
                self.assertTrue(s.pricing_model.strip())

    def test_only_the_two_measurable_services_claim_a_live_counter(self):
        """Google Maps JS bills on client-side loads and OpenAI has no
        local counter. If a future edit attaches a counter to either, it
        is claiming a measurement that does not exist."""
        live = {s.key for s in SERVICES if s.live_counter}
        self.assertEqual(live, LIVE_COUNTER_KEYS)
        for s in SERVICES:
            if s.live_counter:
                self.assertIn(s.live_counter, LIVE_COUNTER_KEYS)


class HonestyTests(unittest.TestCase):
    """The rules that keep this page from overstating what is known."""

    def test_unconfirmed_costs_use_the_exact_tbd_marker(self):
        """A cost that isn't confirmed must be the TBD string verbatim --
        not "unknown", not "~$20", not an empty cell. The template styles
        and counts on this exact value."""
        for s in SERVICES:
            with self.subTest(s.name):
                self.assertTrue(s.monthly_cost.strip())
                if s.is_tbd:
                    self.assertEqual(s.monthly_cost, TBD)

    def test_no_fabricated_dollar_amounts(self):
        """The only dollar figure allowed to appear is $0, and only on a
        service actually marked free (or a free tier). Any other concrete
        amount means someone typed a number that was never confirmed."""
        money = re.compile(r"\$[\d,]+(?:\.\d{2})?")
        for s in SERVICES:
            with self.subTest(s.name):
                for found in money.findall(s.monthly_cost):
                    self.assertEqual(
                        found, "$0",
                        f"{s.name} shows a concrete cost {found!r} that is not $0; "
                        "if it was confirmed, relax this test deliberately",
                    )

    def test_free_services_are_zero_and_not_tbd(self):
        for s in SERVICES:
            if s.free:
                with self.subTest(s.name):
                    self.assertFalse(s.is_tbd)
                    self.assertIn("$0", s.monthly_cost)

    def test_railway_cost_is_not_guessed(self):
        """Explicitly pinned: hosting is the largest likely line and the
        most tempting to estimate. It must stay TBD until invoiced."""
        railway = next(s for s in SERVICES if s.key == "railway")
        self.assertTrue(railway.is_tbd)

    def test_google_places_note_flags_its_estimate(self):
        """The ~1,000/month allowance is researched, not confirmed. The
        page must say so -- the counter is exact but its denominator is
        not."""
        gp = next(s for s in SERVICES if s.key == "google_places")
        self.assertRegex(gp.notes.lower(), r"estimate|not confirmed")

    def test_client_side_and_untracked_costs_are_disclosed(self):
        maps = next(s for s in SERVICES if s.key == "google_maps_js")
        self.assertRegex(maps.notes.lower(), r"not measurable|browser|client")
        search = next(s for s in SERVICES if s.key == "openai_web_search")
        self.assertRegex(search.notes.lower(), r"no local counter")

    def test_openai_lines_are_separate(self):
        """Token spend and the per-call web-search fee bill separately, so
        collapsing them into one row would understate the latter."""
        openai = [s for s in SERVICES if s.configured_key == "OPENAI_API_KEY"]
        self.assertEqual(len(openai), 2)

    def test_last_verified_is_an_iso_date(self):
        for s in SERVICES:
            with self.subTest(s.name):
                self.assertRegex(s.last_verified, r"^\d{4}-\d{2}-\d{2}$")


class RenderShapeTests(unittest.TestCase):
    def test_services_for_attaches_usage_only_where_a_counter_exists(self):
        live = {
            "rentcast": {"used": 3, "threshold": 45, "at_cap": False, "limit": 50},
            "google_places": {"used": 7, "threshold": 100, "at_cap": False},
        }
        rows = service_costs.services_for(live)
        self.assertEqual(len(rows), len(SERVICES))
        with_usage = [r for r in rows if r["usage"]]
        self.assertEqual({r["key"] for r in with_usage}, LIVE_COUNTER_KEYS)
        self.assertEqual(
            next(r for r in rows if r["key"] == "rentcast")["usage"]["used"], 3)

    def test_services_for_works_with_no_live_data(self):
        """The page must still render if the cache is unreachable rather
        than 500ing over a missing counter."""
        rows = service_costs.services_for(None)
        self.assertEqual(len(rows), len(SERVICES))
        self.assertTrue(all(r["usage"] is None for r in rows))

    def test_tbd_count_matches_the_flagged_rows(self):
        rows = service_costs.services_for({})
        self.assertEqual(
            service_costs.tbd_count(rows),
            sum(1 for s in SERVICES if s.is_tbd),
        )

    def test_no_secret_values_are_exposed(self):
        """Rows carry the env var NAME so the page can show a
        configured/not-configured badge; they must never carry a value."""
        previous = os.environ.get("RENTCAST_API_KEY")
        os.environ["RENTCAST_API_KEY"] = "sk-test-should-never-render"
        try:
            rows = service_costs.services_for({})
            blob = repr(rows)
            self.assertNotIn("sk-test-should-never-render", blob)
            self.assertIn("RENTCAST_API_KEY", blob)
        finally:
            # Restore rather than unset -- a real key may be configured in
            # this environment and other tests may depend on it.
            if previous is None:
                os.environ.pop("RENTCAST_API_KEY", None)
            else:
                os.environ["RENTCAST_API_KEY"] = previous


if __name__ == "__main__":
    unittest.main()
