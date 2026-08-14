"""
Tests for tools/waterfall_math.py.

Weighted as Phase 1 argued: effort goes to the invariant suite rather than
to feature coverage. A waterfall bug does not look wrong -- every tier
reads plausibly while the cascade order or a rounding residual quietly
misstates what someone is owed -- so the defence is conservation checks
run over a wide range of randomized inputs, not a handful of examples.

  1. Property-based: all ten invariants over randomized cash flows,
     contribution splits, pref rates and promote splits.
  2. The closed-form IRR hurdle solve, verified against npv()/irr().
  3. The degenerate 100/0 case against Underwriting's real Eagle Rock
     numbers (invariant 10).
  4. A hand-calculated two-investor waterfall, checked arithmetic by
     arithmetic rather than by asserting the invariants passed.
"""

import random
import unittest
from pathlib import Path

from tools import waterfall_math as wm
from tools.deal_analyzer_math import analyze, irr, npv

EAGLE_RENT_ROLL = r"C:/Users/jaspe/Downloads/Eagle Rock Rent Roll May 2026.xlsx"
EAGLE_T12 = r"C:/Users/jaspe/Downloads/test-files-2/Eagle Rock T12 May 2026 Profit and Loss.xlsx"


def contribs(*amounts, cls=wm.CLASS_LP):
    return [{"investor_id": i + 1, "name": f"Investor {i+1}",
             "amount": a, "investor_class": cls} for i, a in enumerate(amounts)]


def periods(*cash, sale_in_last=0.0):
    out = []
    for i, c in enumerate(cash):
        out.append({"year": i + 1, "operating_cash": c,
                    "sale_proceeds": sale_in_last if i == len(cash) - 1 else 0.0})
    return out


def terms(pref=8.0, gp=20.0):
    return {"pref_rate_pct": pref, "pref_convention": wm.PREF_CONVENTION_ACCRUAL,
            "tiers": [dict(t) for t in wm.DEFAULT_TIERS[:2]] +
                     [{"sort_order": 2, "tier_type": wm.TIER_PROMOTE,
                       "lp_share_pct": 100.0 - gp, "gp_share_pct": gp}]}


# ── 1. Property-based invariant coverage ─────────────────────────────────

class TestInvariantsRandomized(unittest.TestCase):
    """The highest-value test in this tool. run_waterfall() asserts all ten
    invariants internally and raises on any failure, so a case that returns
    at all has already proven conservation -- these add independent checks
    on top and drive a wide input range through the cascade."""

    CASES = 400

    def test_invariants_hold_across_randomized_waterfalls(self):
        rng = random.Random(20260810)
        checked = 0
        shapes = {"no_cash": 0, "capital_unreturned": 0, "promote_reached": 0,
                  "negative_year": 0}

        for _ in range(self.CASES):
            n_lp = rng.randint(1, 5)
            amounts = [round(rng.uniform(10_000, 3_000_000), 2) for _ in range(n_lp)]
            n_years = rng.randint(1, 10)
            # deliberately include zero, NEGATIVE and very large years so
            # the cascade is exercised at both ends, not just in the
            # comfortable middle. A negative year is the ordinary shape of
            # a value-add deal that does not cover debt service at first,
            # and it used to crash the cascade outright.
            cash = [round(rng.choice([0.0,
                                      -rng.uniform(0, 120_000),
                                      rng.uniform(0, 400_000)]), 2)
                    for _ in range(n_years)]
            sale = round(rng.choice([0.0, rng.uniform(0, 12_000_000)]), 2)
            pref = rng.choice([0.0, 6.0, 8.0, 10.0, 12.5])
            gp = rng.choice([0.0, 10.0, 20.0, 30.0, 50.0])

            res = wm.run_waterfall(contribs(*amounts),
                                   periods(*cash, sale_in_last=sale),
                                   terms(pref=pref, gp=gp))
            checked += 1
            c = res["_cents"]

            # 1 + 2, restated independently of the module's own checks
            self.assertEqual(sum(c["lp_received"]) + c["gp_received"],
                             c["total_distributed"], "money created or destroyed")
            # every cent of available cash is either distributed, explicitly
            # left over, or was never there (a shortfall the property did
            # not cover). The shortfall term is zero on every deal that
            # never goes negative, so this is the identity it always was.
            self.assertEqual(
                c["total_distributed"]
                + sum(r["undistributed"] for r in c["period_rows"])
                - sum(r["shortfall"] for r in c["period_rows"]),
                c["total_cash"])
            # a period the property did not cover distributes nothing, and
            # nobody is ever allocated a negative amount
            for r in c["period_rows"]:
                if r["cash_available"] < 0:
                    self.assertEqual(sum(r["lp"]) + r["gp"], 0)
                    self.assertEqual(r["shortfall"], -r["cash_available"])
                else:
                    self.assertEqual(r["shortfall"], 0)
            # 5 -- read exact cents; the dollar-rounded tier_totals is for
            # display only and round-tripping it re-rounds by a cent
            rt = c["tier_totals"][wm.TIER_RETURN_OF_CAPITAL]
            roc = rt["lp"] + rt["gp"]
            self.assertLessEqual(roc, sum(c["contrib"]))
            # 6
            self.assertEqual(sum(c["contrib"]) - sum(c["unreturned"]), roc)
            # no negative allocation anywhere
            for r in c["period_rows"]:
                self.assertTrue(all(x >= 0 for x in r["lp"]))
                self.assertGreaterEqual(r["gp"], 0)

            if c["total_cash"] == 0:
                shapes["no_cash"] += 1
            if sum(c["unreturned"]) > 0:
                shapes["capital_unreturned"] += 1
            if res["tier_totals"][wm.TIER_PROMOTE]["total"] > 0:
                shapes["promote_reached"] += 1
            if any(r["cash_available"] < 0 for r in c["period_rows"]):
                shapes["negative_year"] += 1

        self.assertEqual(checked, self.CASES)
        # the range actually exercised, not just the count
        for shape, count in shapes.items():
            self.assertGreater(count, 0, f"randomized range never produced: {shape}")
        print(f"\n    [property-based] {checked} randomized waterfalls; "
              f"shapes hit: {shapes}")

    def test_invariant_7_no_promote_before_pref_randomized(self):
        rng = random.Random(7)
        promote_cases = 0
        for _ in range(200):
            res = wm.run_waterfall(
                contribs(*[round(rng.uniform(50_000, 1_000_000), 2) for _ in range(rng.randint(1, 3))]),
                periods(*[round(rng.uniform(0, 300_000), 2) for _ in range(rng.randint(2, 8))],
                        sale_in_last=round(rng.uniform(0, 8_000_000), 2)),
                terms(pref=rng.choice([6.0, 8.0, 12.0]), gp=20.0))
            due = 0
            for r in res["_cents"]["period_rows"]:
                due += r["accrued_pref"]
                due -= sum(t["lp"] for t in r["tiers"] if t["tier_type"] == wm.TIER_PREF)
                gp_this = sum(t["gp"] for t in r["tiers"] if t["tier_type"] == wm.TIER_PROMOTE)
                if gp_this:
                    promote_cases += 1
                    self.assertLessEqual(due, 0, "GP promoted while pref outstanding")
        self.assertGreater(promote_cases, 0, "promote tier never reached")

    def test_invariant_8_lp_irr_meets_pref_when_settled(self):
        """Only meaningful once capital and pref are both fully paid. The
        accrual base includes unpaid pref, which is what makes this true."""
        rng = random.Random(88)
        settled = 0
        for _ in range(200):
            amounts = [round(rng.uniform(100_000, 1_000_000), 2)]
            pref = rng.choice([6.0, 8.0, 10.0])
            res = wm.run_waterfall(
                contribs(*amounts),
                periods(*[round(rng.uniform(0, 120_000), 2) for _ in range(rng.randint(2, 6))],
                        sale_in_last=round(rng.uniform(2_000_000, 9_000_000), 2)),
                terms(pref=pref, gp=20.0))
            inv = res["investors"][0]
            if wm.to_cents(inv["unreturned_capital"]) == 0 and wm.to_cents(inv["unpaid_pref"]) == 0:
                settled += 1
                self.assertGreaterEqual(res["lp_aggregate"]["irr"], pref / 100.0 - 1e-9)
        self.assertGreater(settled, 0, "no fully-settled case generated")

    def test_pro_rata_never_leaks_a_cent(self):
        rng = random.Random(3)
        for _ in range(2000):
            amount = rng.randint(0, 10_000_000)
            weights = [rng.randint(0, 5_000_000) for _ in range(rng.randint(1, 6))]
            shares = wm.split_pro_rata(amount, weights)
            if sum(weights) > 0:
                self.assertEqual(sum(shares), amount)
                self.assertTrue(all(s >= 0 for s in shares))
            else:
                self.assertEqual(sum(shares), 0)

    def test_invariant_violation_raises_and_is_not_softened(self):
        """A conservation failure must raise, not warn."""
        res = wm.run_waterfall(contribs(100_000.0), periods(10_000.0, sale_in_last=200_000.0), terms())
        res["_cents"]["lp_received"][0] += 1        # inject a phantom cent
        with self.assertRaises(wm.WaterfallInvariantError):
            wm.check_invariants(res)


# ── 2. Closed-form IRR hurdle ────────────────────────────────────────────

class TestIRRHurdleSolve(unittest.TestCase):
    def test_solved_amount_hits_target_rate(self):
        rng = random.Random(11)
        for _ in range(200):
            n = rng.randint(2, 8)
            flows = [-round(rng.uniform(100_000, 2_000_000), 2)]
            flows += [round(rng.uniform(0, 200_000), 2) for _ in range(n - 1)]
            flows.append(0.0)
            target = rng.choice([0.06, 0.08, 0.10, 0.15])
            T = len(flows) - 1
            amount = wm.amount_to_reach_irr(flows, T, target)
            if amount <= 0:
                continue                     # hurdle already cleared
            solved = list(flows); solved[T] = amount
            rate, reason = irr(solved)
            self.assertIsNone(reason)
            self.assertAlmostEqual(rate, target, places=6)
            self.assertLess(abs(npv(target, solved)), 1e-6)

    def test_matches_phase1_worked_example(self):
        flows = [-1_000_000.0, 60_000.0, 70_000.0, 80_000.0, 90_000.0, 0.0]
        amount = wm.amount_to_reach_irr(flows, 5, 0.08)
        self.assertAlmostEqual(amount, 1_109_006.90, places=2)
        solved = list(flows); solved[5] = amount
        self.assertAlmostEqual(irr(solved)[0], 0.08, places=10)

    def test_out_of_range_period_rejected(self):
        with self.assertRaises(wm.WaterfallError):
            wm.amount_to_reach_irr([-100.0, 50.0], 5, 0.08)


# ── 3. Degenerate case: invariant 10 ─────────────────────────────────────

class TestDegenerateReproducesPropertyIRR(unittest.TestCase):
    """With one LP funding all the equity and a 100/0 promote, every dollar
    follows the property, so the LP must see exactly the property's levered
    IRR. If this drifts, the cascade is losing or reordering money."""

    def _scenario(self):
        return analyze({"purchase_price": 6_990_000.0, "closing_costs_pct": 2.0,
                        "ltv_pct": 65.0, "interest_rate_pct": 6.5, "amort_years": 30,
                        "noi_year1": 384_455.38, "noi_growth_pct": 3.0, "hold_years": 5,
                        "exit_cap_pct": 6.25, "selling_costs_pct": 2.0})

    def test_lp_irr_equals_property_levered_irr(self):
        r = self._scenario()
        res = wm.run_waterfall(
            contribs(r["equity_invested"]),
            wm.periods_from_underwriting(r),
            {"pref_rate_pct": 8.0, "pref_convention": wm.PREF_CONVENTION_ACCRUAL,
             "tiers": [dict(t) for t in wm.DEFAULT_TIERS[:2]] +
                      [{"sort_order": 2, "tier_type": wm.TIER_PROMOTE,
                        "lp_share_pct": 100.0, "gp_share_pct": 0.0}]})
        checks = wm.verify_against_source(
            res, r["total_distributions"], r["levered_irr"],
            source_levered_cashflows=r["levered_cashflows"])
        self.assertTrue(all(c["passed"] for c in checks))
        # exact: the LP's flows ARE the property's, cent for cent
        for a, b in zip(res["lp_aggregate"]["cashflows"], r["levered_cashflows"]):
            self.assertEqual(wm.to_cents(a), wm.to_cents(b))
        # IRR agrees to within the stated cent-quantization bound
        self.assertLess(abs(res["lp_aggregate"]["irr"] - r["levered_irr"]),
                        wm.IRR_QUANTIZATION_BOUND)
        self.assertEqual(res["totals"]["gp_distributed"], 0.0)

    def test_invariant_9_total_matches_source(self):
        r = self._scenario()
        res = wm.run_waterfall(contribs(r["equity_invested"]),
                               wm.periods_from_underwriting(r), terms())
        # bound is one cent per period, from cent-rounding the input
        diff = abs(wm.to_cents(res["totals"]["distributed"]) - wm.to_cents(r["total_distributions"]))
        self.assertLessEqual(diff, len(res["periods"]))
        wm.verify_against_source(res, r["total_distributions"])

    def test_periods_from_underwriting_separates_sale(self):
        r = self._scenario()
        p = wm.periods_from_underwriting(r)
        self.assertEqual(len(p), len(r["years"]))
        self.assertEqual(p[-1]["sale_proceeds"], r["net_sale_proceeds"])
        self.assertTrue(all(x["sale_proceeds"] == 0.0 for x in p[:-1]))
        recon = [x["operating_cash"] + x["sale_proceeds"] for x in p]
        expected = list(r["levered_cashflows"][1:])
        for a, b in zip(recon, expected):
            self.assertAlmostEqual(a, b, places=6)


# ── 4. Hand-calculated two-investor waterfall ────────────────────────────

class TestHandCalculated(unittest.TestCase):
    """Every figure below is derived by hand in the comments, then asserted.
    Passing invariants proves internal consistency; this proves the numbers
    are the right ones."""

    def test_two_investors_hand_checked(self):
        # LP A 750,000 (75%), LP B 250,000 (25%). Pref 8% compounding.
        # Promote 80/20. Cash: yr1 100,000, yr2 100,000, yr3 1,400,000.
        res = wm.run_waterfall(contribs(750_000.0, 250_000.0),
                               periods(100_000.0, 100_000.0, 1_400_000.0),
                               terms(pref=8.0, gp=20.0))
        p = res["periods"]

        # Year 1: accrue 8% on 1,000,000 = 80,000. RoC tier takes all
        # 100,000 (capital outstanding 1,000,000 > cash).
        self.assertAlmostEqual(p[0]["accrued_pref"], 80_000.00, places=2)
        self.assertAlmostEqual(p[0]["tiers"][0]["paid"], 100_000.00, places=2)
        self.assertAlmostEqual(p[0]["lp"][0], 75_000.00, places=2)   # 75%
        self.assertAlmostEqual(p[0]["lp"][1], 25_000.00, places=2)   # 25%
        self.assertAlmostEqual(p[0]["gp"], 0.00, places=2)

        # Year 2: base = unreturned 900,000 + unpaid pref 80,000 = 980,000
        #         accrual = 78,400.  RoC takes all 100,000 again.
        self.assertAlmostEqual(p[1]["accrued_pref"], 78_400.00, places=2)
        self.assertAlmostEqual(p[1]["tiers"][0]["paid"], 100_000.00, places=2)
        self.assertAlmostEqual(p[1]["gp"], 0.00, places=2)

        # Year 3: base = unreturned 800,000 + unpaid 158,400 = 958,400
        #         accrual = 76,672 -> unpaid pref 235,072
        #         RoC pays remaining 800,000; pref pays 235,072;
        #         residual 1,400,000 - 800,000 - 235,072 = 364,928
        #         GP 20% = 72,985.60 ; LP 80% = 291,942.40
        self.assertAlmostEqual(p[2]["accrued_pref"], 76_672.00, places=2)
        self.assertAlmostEqual(p[2]["tiers"][0]["paid"], 800_000.00, places=2)
        self.assertAlmostEqual(p[2]["tiers"][1]["paid"], 235_072.00, places=2)
        self.assertAlmostEqual(p[2]["tiers"][2]["gp"], 72_985.60, places=2)
        self.assertAlmostEqual(p[2]["tiers"][2]["lp"], 291_942.40, places=2)

        # Totals: cash in 1,600,000 all distributed
        self.assertAlmostEqual(res["totals"]["cash_available"], 1_600_000.00, places=2)
        self.assertAlmostEqual(res["totals"]["distributed"], 1_600_000.00, places=2)
        self.assertAlmostEqual(res["totals"]["gp_distributed"], 72_985.60, places=2)
        self.assertAlmostEqual(res["totals"]["lp_distributed"], 1_527_014.40, places=2)

        # Capital fully returned, pref fully paid
        self.assertAlmostEqual(res["tier_totals"]["return_of_capital"]["total"], 1_000_000.00, places=2)
        self.assertAlmostEqual(res["tier_totals"]["pref"]["total"], 235_072.00, places=2)

        # Pro rata: A 75% of every LP dollar
        a, b = res["investors"]
        self.assertAlmostEqual(a["distributed"], 1_527_014.40 * 0.75, places=2)
        self.assertAlmostEqual(b["distributed"], 1_527_014.40 * 0.25, places=2)
        self.assertAlmostEqual(a["contributed"] + b["contributed"], 1_000_000.00, places=2)

        # invariant 8 holds: fully settled, so LP IRR >= 8%
        self.assertGreaterEqual(res["lp_aggregate"]["irr"], 0.08 - 1e-9)

    def test_gp_gets_nothing_when_pref_unmet(self):
        res = wm.run_waterfall(contribs(1_000_000.0), periods(50_000.0, 50_000.0), terms())
        self.assertAlmostEqual(res["totals"]["gp_distributed"], 0.0, places=2)
        self.assertGreater(res["investors"][0]["unreturned_capital"], 0)

    def test_zero_pref_still_conserves(self):
        res = wm.run_waterfall(contribs(500_000.0), periods(600_000.0), terms(pref=0.0))
        self.assertAlmostEqual(res["totals"]["distributed"], 600_000.00, places=2)
        self.assertAlmostEqual(res["tier_totals"]["pref"]["total"], 0.0, places=2)
        self.assertAlmostEqual(res["totals"]["gp_distributed"], 20_000.00, places=2)  # 20% of 100k


# ── input validation ─────────────────────────────────────────────────────

class TestValidation(unittest.TestCase):
    def test_no_lp_rejected(self):
        with self.assertRaises(wm.WaterfallError):
            wm.run_waterfall(contribs(100.0, cls=wm.CLASS_GP), periods(10.0), terms())

    def test_no_periods_rejected(self):
        with self.assertRaises(wm.WaterfallError):
            wm.run_waterfall(contribs(100.0), [], terms())

    def test_zero_capital_rejected(self):
        with self.assertRaises(wm.WaterfallError):
            wm.run_waterfall(contribs(0.0), periods(10.0), terms())

    def test_irr_lookback_convention_rejected_not_silently_accrued(self):
        with self.assertRaises(wm.WaterfallError):
            wm.run_waterfall(contribs(100_000.0), periods(10_000.0),
                             {"pref_rate_pct": 8.0, "pref_convention": "irr_lookback",
                              "tiers": list(wm.DEFAULT_TIERS)})

    def test_irr_hurdle_tier_rejected_not_ignored(self):
        with self.assertRaises(wm.WaterfallError):
            wm.run_waterfall(contribs(100_000.0), periods(10_000.0),
                             {"pref_rate_pct": 8.0, "pref_convention": "accrual",
                              "tiers": [{"sort_order": 0, "tier_type": wm.TIER_IRR_HURDLE,
                                         "hurdle_rate_pct": 12.0}]})


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestNegativeCashPeriods(unittest.TestCase):
    """A period the property did not cover distributes nothing.

    This replaces four tests that asserted the opposite -- that such a
    period CRASHED with WaterfallInvariantError. It did, on every deal
    shaped like an ordinary value-add: year 1 does not cover debt service,
    and the Investor Report fell over instead of showing a year with no
    distribution.

    The cause was a sign error in invariant 4. The cascade itself was
    already correct -- it pays nothing when cash is not positive -- but
    the check read `paid > cash_available`, and zero is arithmetically
    greater than a negative number, so it fired on exactly the cases it
    should have passed.
    """

    def _run(self, *cash, sale=3_000_000.0, equity=2_586_300.0):
        return wm.run_waterfall(contribs(equity), periods(*cash, sale_in_last=sale),
                                terms(pref=8.0, gp=20.0))

    # ── the four that used to assert a crash ─────────────────────────────

    def test_a_small_negative_year_computes(self):
        r = self._run(-468.0, 40_000.0, 40_000.0, 40_000.0, 40_000.0)
        self.assertEqual(r["periods"][0]["lp_total"], 0.0)
        self.assertEqual(r["periods"][0]["gp"], 0.0)

    def test_a_large_negative_year_computes(self):
        r = self._run(-50_000.0, 40_000.0, 40_000.0, 40_000.0, 40_000.0)
        self.assertEqual(r["periods"][0]["lp_total"], 0.0)
        self.assertEqual(r["periods"][0]["gp"], 0.0)

    def test_a_positive_year_is_unaffected(self):
        r = self._run(40_000.0, 40_000.0, 40_000.0, 40_000.0, 40_000.0)
        self.assertGreater(r["periods"][0]["lp_total"], 0.0)
        self.assertEqual(r["periods"][0]["shortfall"], 0.0)

    def test_a_zero_year_is_unaffected(self):
        r = self._run(0.0, 40_000.0, 40_000.0, 40_000.0, 40_000.0)
        self.assertEqual(r["periods"][0]["lp_total"], 0.0)
        self.assertEqual(r["periods"][0]["shortfall"], 0.0)

    # ── the behaviour that replaces "it raises" ──────────────────────────

    def test_every_tier_pays_zero_in_an_uncovered_period(self):
        r = self._run(-50_000.0, 40_000.0, 40_000.0, 40_000.0, 40_000.0)
        for tier in r["periods"][0]["tiers"]:
            self.assertEqual(tier["paid"], 0.0, tier["tier_type"])

    def test_the_shortfall_is_recorded_not_allocated(self):
        r = self._run(-50_000.0, 40_000.0, 40_000.0, 40_000.0, 40_000.0)
        self.assertEqual(r["periods"][0]["shortfall"], 50_000.0)
        self.assertEqual(r["totals"]["shortfall"], 50_000.0)

    def test_undistributed_never_goes_negative(self):
        """It did: undistributed and shortfall were one signed number, so a
        deal with an uncovered year reported negative cash left over, which
        reads as money having gone missing."""
        r = self._run(-50_000.0, 40_000.0, 40_000.0, 40_000.0, 40_000.0)
        self.assertEqual(r["periods"][0]["undistributed"], 0.0)
        self.assertGreaterEqual(r["totals"]["undistributed"], 0.0)

    def test_no_investor_is_allocated_a_negative_amount(self):
        r = self._run(-250_000.0, 40_000.0, 40_000.0, 40_000.0, 40_000.0)
        for p in r["periods"]:
            self.assertTrue(all(x >= 0 for x in p["lp"]))
            self.assertGreaterEqual(p["gp"], 0.0)

    def test_the_lp_still_gets_paid_in_the_years_that_do_cover(self):
        r = self._run(-50_000.0, 40_000.0, 40_000.0, 40_000.0, 40_000.0)
        self.assertGreater(r["periods"][1]["lp_total"], 0.0)
        self.assertGreater(r["investors"][0]["distributed"], 0.0)

    def test_consecutive_uncovered_years(self):
        r = self._run(-50_000.0, -30_000.0, -10_000.0, 40_000.0, 40_000.0)
        self.assertEqual([p["shortfall"] for p in r["periods"][:3]],
                         [50_000.0, 30_000.0, 10_000.0])
        self.assertEqual(r["totals"]["shortfall"], 90_000.0)

    def test_every_year_uncovered_and_no_sale(self):
        r = self._run(-50_000.0, -30_000.0, sale=0.0)
        self.assertEqual(r["totals"]["distributed"], 0.0)
        self.assertEqual(r["totals"]["shortfall"], 80_000.0)
        self.assertEqual(r["totals"]["undistributed"], 0.0)

    # ── the invariants are honoured, not bypassed ────────────────────────

    def test_all_invariants_still_run_and_pass(self):
        r = self._run(-50_000.0, 40_000.0, 40_000.0, 40_000.0, 40_000.0)
        checks = wm.check_invariants(r)
        self.assertTrue(checks)
        self.assertFalse([c for c in checks if c["passed"] is False])
        self.assertTrue([c for c in checks if c["n"] == 4 and c["passed"]])

    def test_invariant_1_identity_holds_on_the_uncovered_period(self):
        r = self._run(-50_000.0, 40_000.0, 40_000.0, 40_000.0, 40_000.0)
        row = r["_cents"]["period_rows"][0]
        self.assertEqual(sum(row["lp"]) + row["gp"] + row["undistributed"]
                         - row["shortfall"], row["cash_available"])

    def test_invariant_4_still_catches_a_period_that_over_distributes(self):
        """The fix must not have turned invariant 4 off."""
        r = self._run(40_000.0, 40_000.0, 40_000.0, 40_000.0, 40_000.0)
        rows = [dict(x) for x in r["_cents"]["period_rows"]]
        rows[0] = {**rows[0],
                   "tiers": [{"tier_type": wm.TIER_PROMOTE, "lp": 10 ** 9,
                              "gp": 0, "paid": 10 ** 9}]}
        tampered = {**r, "_cents": {**r["_cents"], "period_rows": rows}}
        with self.assertRaises(wm.WaterfallInvariantError):
            wm.check_invariants(tampered)

    def test_a_negative_period_that_paid_out_is_still_rejected(self):
        """The new clause. max(available, 0) on its own would accept it."""
        r = self._run(-50_000.0, 40_000.0, 40_000.0, 40_000.0, 40_000.0)
        rows = [dict(x) for x in r["_cents"]["period_rows"]]
        rows[0] = {**rows[0], "lp": [100], "gp": 0,
                   "tiers": [{"tier_type": wm.TIER_PROMOTE, "lp": 100,
                              "gp": 0, "paid": 100}]}
        tampered = {**r, "_cents": {**r["_cents"], "period_rows": rows}}
        with self.assertRaises(wm.WaterfallInvariantError):
            wm.check_invariants(tampered)

    def test_invariant_9_pins_the_difference_to_the_shortfall(self):
        """Not a widened tolerance: distributed - shortfall must equal the
        source total to the same cent bound as before."""
        r = self._run(-50_000.0, 40_000.0, 40_000.0, 40_000.0, 40_000.0)
        source_total = r["totals"]["distributed"] - r["totals"]["shortfall"]
        checks = wm.verify_against_source(r, source_total)
        self.assertTrue([c for c in checks if c["n"] == 9 and c["passed"]])
        with self.assertRaises(wm.WaterfallInvariantError):
            wm.verify_against_source(r, source_total + 100.0)

    def test_invariant_10_is_not_applicable_rather_than_silently_skipped(self):
        """An LP takes no negative distribution, so with a shortfall its
        flows cannot match the property's. Reported as n/a with a reason,
        the way invariant 8 already handles an unsettled pref."""
        r = self._run(-50_000.0, 40_000.0, 40_000.0, 40_000.0, 40_000.0)
        source_total = r["totals"]["distributed"] - r["totals"]["shortfall"]
        checks = wm.verify_against_source(r, source_total, source_levered_irr=0.09)
        tenth = [c for c in checks if c["n"] == 10]
        self.assertTrue(tenth)
        self.assertIsNone(tenth[0]["passed"])
        self.assertIn("not applicable", tenth[0]["detail"])

    def test_invariant_10_still_enforced_when_there_is_no_shortfall(self):
        r = self._run(40_000.0, 40_000.0, 40_000.0, 40_000.0, 40_000.0)
        with self.assertRaises(wm.WaterfallInvariantError):
            wm.verify_against_source(r, r["totals"]["distributed"],
                                     source_levered_irr=0.99)
