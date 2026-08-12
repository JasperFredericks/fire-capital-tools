"""
Tests for the GP promote split among named partners.

Structured like tests/test_waterfall_math.py, and for the same reason:
a conservation property is only worth asserting if it is asserted over a
wide input range, not on a handful of tidy examples.

  1. Property-based: invariant 11 over randomized partner counts (1-7)
     and randomized share splits, 400 cases.
  2. Regression: a scenario with no partners is byte-identical to what
     the waterfall reported before this feature existed.
  3. Rounding: the residual convention is the one already used for LP
     pro-rata, not a second one.
"""

import json
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import gp_split_math as gps
from tools import waterfall_math as wm


def contribs(*amounts):
    return [{"investor_id": i + 1, "name": f"LP {i + 1}", "amount": a,
             "investor_class": wm.CLASS_LP}
            for i, a in enumerate(amounts)]


def periods(*cash, sale_in_last=0.0):
    out = []
    for i, c in enumerate(cash):
        out.append({"year": i + 1, "operating_cash": c,
                    "sale_proceeds": sale_in_last if i == len(cash) - 1 else 0.0})
    return out


def terms(pref=8.0, gp=30.0):
    return {"pref_rate_pct": pref, "pref_convention": wm.PREF_CONVENTION_ACCRUAL,
            "tiers": [
                {"sort_order": 0, "tier_type": wm.TIER_RETURN_OF_CAPITAL,
                 "lp_share_pct": 100.0, "gp_share_pct": 0.0},
                {"sort_order": 1, "tier_type": wm.TIER_PREF,
                 "lp_share_pct": 100.0, "gp_share_pct": 0.0},
                {"sort_order": 2, "tier_type": wm.TIER_PROMOTE,
                 "lp_share_pct": 100.0 - gp, "gp_share_pct": gp},
            ]}


def partners(*shares):
    return [{"name": f"Partner {i + 1}", "share_pct": s, "sort_order": i}
            for i, s in enumerate(shares)]


def random_shares(rng, n):
    """n shares summing to exactly 100.00, in whole cents of a percent."""
    total = 10_000                       # 100.00% in cents
    cuts = sorted(rng.randint(1, total - 1) for _ in range(n - 1))
    parts, prev = [], 0
    for c in cuts + [total]:
        parts.append(c - prev)
        prev = c
    # a zero-width slice is a legitimate 0% partner, but shift it to 1 cent
    # so every partner is a real holder and the range stays meaningful
    for i, v in enumerate(parts):
        if v == 0:
            parts[i] = 1
    excess = sum(parts) - total
    parts[parts.index(max(parts))] -= excess
    return [p / 100.0 for p in parts]


class TestPropertyBased(unittest.TestCase):
    """Invariant 11 over a wide range of partner sets and cascades."""

    CASES = 400

    def test_invariant_11_holds_across_randomized_splits(self):
        rng = random.Random(20260812)
        checked = 0
        shapes = {"no_promote": 0, "promote_paid": 0,
                  "single_partner": 0, "many_partners": 0, "uneven_split": 0}

        for _ in range(self.CASES):
            n_lp = rng.randint(1, 4)
            amounts = [round(rng.uniform(10_000, 3_000_000), 2) for _ in range(n_lp)]
            n_years = rng.randint(1, 10)
            cash = [round(rng.choice([0.0, rng.uniform(0, 400_000)]), 2)
                    for _ in range(n_years)]
            sale = round(rng.choice([0.0, rng.uniform(0, 12_000_000)]), 2)
            result = wm.run_waterfall(
                contribs(*amounts), periods(*cash, sale_in_last=sale),
                terms(pref=rng.choice([0.0, 6.0, 8.0, 12.5]),
                      gp=rng.choice([10.0, 20.0, 30.0, 50.0])))

            n_partners = rng.randint(1, 7)
            shares = random_shares(rng, n_partners)
            split = gps.allocate(result, partners(*shares))
            checked += 1

            gp_cents = result["_cents"]["gp_received"]
            totals = split["_cents"]["totals"]

            # invariant 11, restated here independently of the module
            self.assertEqual(sum(totals), gp_cents,
                             "partner split created or destroyed GP cash")
            self.assertTrue(all(t >= 0 for t in totals), "negative allocation")
            self.assertEqual(len(totals), n_partners)

            # every period reconciles too, not merely the grand total
            for row, src in zip(split["periods"], result["_cents"]["period_rows"]):
                self.assertEqual(sum(row["shares_cents"]), src["gp"],
                                 f"year {row['year']} partner split != period GP")

            # a partner's reported total is the sum of their own periods
            for i, p in enumerate(split["partners"]):
                self.assertEqual(
                    p["distributed_cents"],
                    sum(r["shares_cents"][i] for r in split["periods"]))

            shapes["no_promote" if gp_cents == 0 else "promote_paid"] += 1
            shapes["single_partner" if n_partners == 1 else "many_partners"] += 1
            if n_partners > 1 and max(shares) - min(shares) > 20.0:
                shapes["uneven_split"] += 1

        self.assertEqual(checked, self.CASES)
        for shape, count in shapes.items():
            self.assertGreater(count, 0, f"randomized range never produced: {shape}")
        print(f"\n    [property-based] {checked} randomized GP splits; "
              f"shapes hit: {shapes}")

    def test_partner_counts_one_to_seven_all_conserve(self):
        rng = random.Random(77)
        result = wm.run_waterfall(contribs(1_000_000.0, 500_000.0),
                                  periods(120_000.0, 130_000.0, 140_000.0,
                                          sale_in_last=4_000_000.0),
                                  terms())
        gp_cents = result["_cents"]["gp_received"]
        self.assertGreater(gp_cents, 0, "fixture must actually pay a promote")
        for n in range(1, 8):
            with self.subTest(partners=n):
                split = gps.allocate(result, partners(*random_shares(rng, n)))
                self.assertEqual(sum(split["_cents"]["totals"]), gp_cents)
                self.assertEqual(split["partner_count"], n)


class TestNoPartnersIsUnchanged(unittest.TestCase):
    """The regression that matters: an unconfigured scenario reports
    exactly what it reported before this feature existed."""

    def setUp(self):
        self.result = wm.run_waterfall(
            contribs(1_000_000.0, 500_000.0),
            periods(120_000.0, 130_000.0, 140_000.0, sale_in_last=4_000_000.0),
            terms())

    def test_none_and_empty_both_give_one_hundred_percent_bucket(self):
        for value in (None, []):
            with self.subTest(partners=value):
                split = gps.allocate(self.result, value)
                self.assertTrue(split["is_default"])
                self.assertEqual(split["partner_count"], 1)
                self.assertEqual(split["partners"][0]["share_pct"], 100.0)
                self.assertEqual(split["partners"][0]["name"], gps.DEFAULT_PARTNER_NAME)

    def test_default_bucket_receives_exactly_the_gp_total(self):
        split = gps.allocate(self.result, None)
        self.assertEqual(split["partners"][0]["distributed_cents"],
                         self.result["_cents"]["gp_received"])
        self.assertEqual(split["gp_distributed"],
                         self.result["totals"]["gp_distributed"])

    def test_default_bucket_flows_equal_the_gp_flows(self):
        split = gps.allocate(self.result, None)
        self.assertEqual(split["partners"][0]["cashflows"],
                         self.result["gp"]["cashflows"])

    def test_allocating_does_not_mutate_the_waterfall(self):
        before = json.dumps(self.result, sort_keys=True, default=str)
        gps.allocate(self.result, partners(50.0, 50.0))
        after = json.dumps(self.result, sort_keys=True, default=str)
        self.assertEqual(before, after,
                         "the split must be downstream and read-only")

    def test_waterfall_gp_total_identical_with_and_without_partners(self):
        """The cascade's own GP figure cannot move -- the split is
        downstream of a number already fixed."""
        a = gps.allocate(self.result, None)["gp_distributed_cents"]
        b = gps.allocate(self.result, partners(33.0, 33.0, 34.0))["gp_distributed_cents"]
        self.assertEqual(a, b)
        self.assertEqual(a, self.result["_cents"]["gp_received"])


class TestRounding(unittest.TestCase):
    """The residual convention must be the existing one."""

    def test_indivisible_cent_goes_to_the_largest_holder(self):
        """1 cent across 60/40 floors to 0/0 with 1 left over; the existing
        LP convention hands it to the largest weight."""
        self.assertEqual(wm.split_pro_rata(1, [wm.to_cents(60.0), wm.to_cents(40.0)]),
                         [1, 0])

    def test_split_uses_split_pro_rata_semantics(self):
        result = wm.run_waterfall(contribs(1_000_000.0),
                                  periods(0.0, 0.0, sale_in_last=3_000_000.0),
                                  terms(pref=0.0, gp=30.0))
        split = gps.allocate(result, partners(60.0, 40.0))
        for row, src in zip(split["periods"], result["_cents"]["period_rows"]):
            self.assertEqual(row["shares_cents"],
                             wm.split_pro_rata(src["gp"],
                                               [wm.to_cents(60.0), wm.to_cents(40.0)]))

    def test_near_thirds_lose_nothing(self):
        """Shares are entered to two decimal places, so an exact one-third
        split is not expressible -- 33.33 x3 totals 99.99%. A three-way
        partnership types 33.33/33.33/33.34, and that must conserve every
        cent of an awkward promote."""
        result = wm.run_waterfall(contribs(1_000_000.0),
                                  periods(0.0, sale_in_last=3_000_001.0),
                                  terms(pref=0.0, gp=30.0))
        split = gps.allocate(result, partners(33.33, 33.33, 33.34))
        self.assertEqual(sum(split["_cents"]["totals"]),
                         result["_cents"]["gp_received"])

    def test_exact_thirds_are_rejected_rather_than_silently_absorbed(self):
        """100/3 three times is 99.99%, and the missing cent of a percent
        is refused rather than quietly handed to someone. Renormalizing
        would pay each partner slightly more than their agreement says."""
        with self.assertRaises(gps.GPSplitError) as ctx:
            gps.validate(gps.normalize(partners(100 / 3, 100 / 3, 100 / 3)))
        self.assertIn("99.99", str(ctx.exception))


class TestValidation(unittest.TestCase):
    def test_shares_must_total_one_hundred(self):
        with self.assertRaises(gps.GPSplitError) as ctx:
            gps.validate(gps.normalize(partners(50.0, 40.0)))
        self.assertIn("100%", str(ctx.exception))

    def test_over_one_hundred_rejected(self):
        with self.assertRaises(gps.GPSplitError):
            gps.validate(gps.normalize(partners(60.0, 60.0)))

    def test_negative_share_rejected(self):
        with self.assertRaises(gps.GPSplitError):
            gps.validate(gps.normalize(partners(-10.0, 110.0)))

    def test_too_many_partners_rejected(self):
        many = partners(*([100.0 / (gps.MAX_PARTNERS + 1)] * (gps.MAX_PARTNERS + 1)))
        with self.assertRaises(gps.GPSplitError):
            gps.validate(gps.normalize(many))

    def test_blank_share_row_is_dropped_not_zero(self):
        rows = [{"name": "Real", "share_pct": 100.0},
                {"name": "Unfilled", "share_pct": None}]
        self.assertEqual(len(gps.normalize(rows)), 1)

    def test_empty_set_validates_as_the_default(self):
        gps.validate([])          # must not raise

    def test_error_names_the_partner(self):
        with self.assertRaises(gps.GPSplitError) as ctx:
            gps.validate(gps.normalize([{"name": "Beckett", "share_pct": -5.0},
                                        {"name": "Other", "share_pct": 105.0}]))
        self.assertIn("Beckett", str(ctx.exception))

    def test_invariant_error_is_raised_not_returned(self):
        result = wm.run_waterfall(contribs(1_000_000.0),
                                  periods(0.0, sale_in_last=3_000_000.0),
                                  terms(pref=0.0, gp=30.0))
        split = gps.allocate(result, partners(50.0, 50.0))
        split["_cents"]["totals"][0] += 1        # break conservation
        with self.assertRaises(gps.GPSplitInvariantError):
            gps.check_invariants(split)


class TestPromoteDefaultIsSeventyThirty(unittest.TestCase):
    """Part C. The default moves; stored scenarios do not."""

    def test_waterfall_default_tier_is_seventy_thirty(self):
        promote = [t for t in wm.DEFAULT_TIERS
                   if t["tier_type"] == wm.TIER_PROMOTE][0]
        self.assertEqual(promote["gp_share_pct"], 30.0)
        self.assertEqual(promote["lp_share_pct"], 70.0)

    def test_db_default_constants(self):
        from tools import investor_report_db as irdb
        self.assertEqual(irdb.DEFAULT_PROMOTE_GP_PCT, 30.0)
        self.assertEqual(irdb.DEFAULT_PROMOTE_LP_PCT, 70.0)
        tiers = irdb.default_tiers()
        promote = [t for t in tiers if t["tier_type"] == wm.TIER_PROMOTE][0]
        self.assertEqual(promote["gp_share_pct"], 30.0)

    def test_an_explicit_split_still_wins(self):
        """Configurable per scenario -- only the default changed."""
        from tools import investor_report_db as irdb
        tiers = irdb.default_tiers(90.0, 10.0)
        promote = [t for t in tiers if t["tier_type"] == wm.TIER_PROMOTE][0]
        self.assertEqual(promote["gp_share_pct"], 10.0)
        self.assertEqual(promote["lp_share_pct"], 90.0)

    def test_stored_tiers_are_used_over_the_default(self):
        result = wm.run_waterfall(contribs(1_000_000.0),
                                  periods(0.0, sale_in_last=3_000_000.0),
                                  terms(pref=0.0, gp=20.0))
        promote = result["tier_totals"][wm.TIER_PROMOTE]
        paid = promote["lp"] + promote["gp"]
        self.assertAlmostEqual(promote["gp"] / paid, 0.20, places=6,
                               msg="an 80/20 scenario must stay 80/20")


class TestPurity(unittest.TestCase):
    def test_gp_split_math_is_pure(self):
        import ast
        tree = ast.parse(Path(gps.__file__).read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        self.assertNotIn("flask", roots)
        self.assertNotIn("sqlite3", roots)

    def test_waterfall_math_does_not_depend_on_the_split(self):
        """The dependency runs one way only: the split reads the cascade,
        never the reverse."""
        src = Path(wm.__file__).read_text(encoding="utf-8")
        self.assertNotIn("gp_split_math", src)


if __name__ == "__main__":
    unittest.main()
