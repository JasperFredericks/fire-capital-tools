"""
Tests for the multi-loan debt stack.

Two claims carry this phase:

  1. single-loan scenarios are completely unaffected -- byte-identical
     output, including the real Eagle Rock scenario
  2. a two-loan stack sums debt service and payoff correctly

(2) is checked against values derived independently of the code under
test: the level payment is recomputed from the annuity formula written
out here, and the exit balance is recomputed by running a month-by-month
amortization loop, which is a different method from the closed form
deal_analyzer_math uses. Agreement between two methods is evidence; a
test that calls the same function it is verifying is not.
"""

import json
import sys
import unittest
from decimal import Decimal, getcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import deal_analyzer_math as dam
from tools import underwriting_loans_math as ulm
from tools import underwriting_math as um

getcontext().prec = 40

FIXTURE = Path(__file__).parent / "fixtures" / "eagle_rock_scenario4.json"

EAGLE_ROCK = {
    "noi_year1": 384455.38,
    "opex_year1": 839216.14,
    "equity": 2586300.00,
    "levered_irr_pct": 8.11,
    "dscr": 1.12,
}

LOAN_A = {"name": "Senior", "amount": 4_000_000.0, "rate_pct": 6.0, "amort_years": 30}
LOAN_B = {"name": "Mezzanine", "amount": 1_000_000.0, "rate_pct": 9.0, "amort_years": 20}


# ── Independent reference implementations ────────────────────────────────

def ref_monthly_payment(principal, annual_rate_pct, amort_years):
    """Standard annuity payment, written out here rather than imported."""
    P = Decimal(str(principal))
    r = Decimal(str(annual_rate_pct)) / Decimal(100) / Decimal(12)
    n = amort_years * 12
    if r == 0:
        return P / n
    return P * r / (1 - (1 + r) ** -n)


def ref_balance(principal, annual_rate_pct, amort_years, months):
    """Month-by-month amortization -- deliberately a different method from
    the closed-form balance the code under test uses."""
    b = Decimal(str(principal))
    r = Decimal(str(annual_rate_pct)) / Decimal(100) / Decimal(12)
    pmt = ref_monthly_payment(principal, annual_rate_pct, amort_years)
    for _ in range(months):
        b = b - (pmt - b * r)
    return b


def load_fixture():
    d = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return d["scenario"], d["units"], d["expenses"]


class TestIndependentAgreement(unittest.TestCase):
    """The reference implementations and the code must agree, or neither
    result below means anything."""

    def test_payment_matches_reference(self):
        for loan in (LOAN_A, LOAN_B):
            with self.subTest(loan=loan["name"]):
                expected = float(ref_monthly_payment(
                    loan["amount"], loan["rate_pct"], loan["amort_years"]) * 12)
                self.assertAlmostEqual(ulm.annual_payment(loan), expected, places=6)

    def test_balance_matches_reference(self):
        for loan in (LOAN_A, LOAN_B):
            with self.subTest(loan=loan["name"]):
                expected = float(ref_balance(
                    loan["amount"], loan["rate_pct"], loan["amort_years"], 60))
                self.assertAlmostEqual(ulm.balance_after(loan, 60), expected, places=4)


class TestTwoLoanStack(unittest.TestCase):
    """Hand-calculated: $4.0m @ 6% / 30yr plus $1.0m @ 9% / 20yr, 5-year hold."""

    EXPECTED_DEBT_SERVICE = 395751.3667753419
    EXPECTED_BALANCE_60 = 4609244.7629592714
    EXPECTED_TOTAL = 5_000_000.0

    def setUp(self):
        self.summary = ulm.summarize([LOAN_A, LOAN_B], hold_years=5,
                                     noi_year1=600_000.0, purchase_price=10_000_000.0)

    def test_total_amount_is_the_sum(self):
        self.assertAlmostEqual(self.summary["loan_amount"], self.EXPECTED_TOTAL, places=6)

    def test_debt_service_is_the_sum_of_each_loans_own_payment(self):
        self.assertAlmostEqual(self.summary["annual_debt_service"],
                               self.EXPECTED_DEBT_SERVICE, places=6)

    def test_balance_at_exit_is_the_sum_of_each_loans_own_balance(self):
        self.assertAlmostEqual(self.summary["balance_at_exit"],
                               self.EXPECTED_BALANCE_60, places=4)

    def test_per_loan_figures(self):
        a, b = self.summary["loans"]
        self.assertAlmostEqual(a["annual_debt_service"], 287784.2520733211, places=6)
        self.assertAlmostEqual(b["annual_debt_service"], 107967.1147020208, places=6)
        self.assertAlmostEqual(a["balance_at_exit"], 3722174.2729127824, places=4)
        self.assertAlmostEqual(b["balance_at_exit"], 887070.4900464890, places=4)

    def test_implied_ltv_is_computed_from_the_stack(self):
        self.assertAlmostEqual(self.summary["implied_ltv_pct"], 50.0, places=9)

    def test_combined_dscr_is_noi_over_total_debt_service(self):
        self.assertAlmostEqual(self.summary["combined_dscr"], 1.5161034184, places=8)

    def test_per_loan_dscr_is_never_below_combined(self):
        """The stated risk is inverted, and this pins the real relation:
        each per-loan DSCR divides the same NOI by a smaller slice of debt
        service, so it can only be larger. Combined is the binding one."""
        combined = self.summary["combined_dscr"]
        for loan in self.summary["loans"]:
            self.assertGreaterEqual(loan["dscr"], combined - 1e-12, loan["name"])

    def test_cumulative_dscr_walks_the_stack_downward(self):
        cums = [l["cumulative_dscr"] for l in self.summary["loans"]]
        self.assertEqual(cums, sorted(cums, reverse=True))
        self.assertAlmostEqual(cums[-1], self.summary["combined_dscr"], places=12)

    def test_one_loan_equals_the_equivalent_ltv_scenario(self):
        """A single loan entered as a stack must produce exactly what the
        LTV path produces -- the two modes cannot disagree about one loan."""
        price = 10_000_000.0
        loan = {"name": "Only", "amount": 6_500_000.0, "rate_pct": 6.5, "amort_years": 30}
        stack = ulm.summarize([loan], hold_years=5, purchase_price=price)
        self.assertAlmostEqual(stack["annual_debt_service"],
                               dam.monthly_payment(6_500_000.0, 0.065, 30) * 12, places=9)
        self.assertAlmostEqual(stack["balance_at_exit"],
                               dam.remaining_balance(6_500_000.0, 0.065, 30, 60), places=9)
        self.assertAlmostEqual(stack["implied_ltv_pct"], 65.0, places=9)


class TestSingleLoanUnaffected(unittest.TestCase):
    """The whole point of the phase: existing scenarios must not move."""

    def setUp(self):
        self.scenario, self.units, self.expenses = load_fixture()

    def test_eagle_rock_unchanged_with_no_loans(self):
        r = um.analyze_scenario(self.scenario, self.units, self.expenses)
        self.assertAlmostEqual(r["projection"]["years"][0]["noi"],
                               EAGLE_ROCK["noi_year1"], places=2)
        self.assertAlmostEqual(r["operating_expenses_year1"],
                               EAGLE_ROCK["opex_year1"], places=2)
        self.assertAlmostEqual(r["returns"]["equity_invested"],
                               EAGLE_ROCK["equity"], places=2)
        self.assertAlmostEqual(r["returns"]["levered_irr"] * 100,
                               EAGLE_ROCK["levered_irr_pct"], places=2)
        self.assertAlmostEqual(r["returns"]["dscr"], EAGLE_ROCK["dscr"], places=2)

    def test_debt_stack_is_none_in_single_loan_mode(self):
        for loans in (None, []):
            with self.subTest(loans=loans):
                r = um.analyze_scenario(self.scenario, self.units, self.expenses, loans)
                self.assertIsNone(r["debt_stack"])

    def test_none_and_empty_list_are_byte_identical(self):
        a = um.analyze_scenario(self.scenario, self.units, self.expenses, None)
        b = um.analyze_scenario(self.scenario, self.units, self.expenses, [])
        self.assertEqual(json.dumps(a, sort_keys=True, default=str),
                         json.dumps(b, sort_keys=True, default=str))

    def test_engine_default_path_identical_with_and_without_override_arg(self):
        """analyze_noi_series(debt=None) must equal the pre-override call."""
        inputs = {
            "purchase_price": 6_990_000.0, "closing_costs_pct": 2.0, "ltv_pct": 65.0,
            "interest_rate_pct": 6.5, "amort_years": 30, "hold_years": 5,
            "exit_cap_pct": 6.0, "selling_costs_pct": 2.0,
            "noi_year1": 1.0, "noi_growth_pct": 0.0,
        }
        series = [384455.38, 400185.0, 416492.0, 433395.0, 450915.0]
        a = dam.analyze_noi_series(inputs, series, 469_000.0)
        b = dam.analyze_noi_series(inputs, series, 469_000.0, debt=None)
        self.assertEqual(json.dumps(a, sort_keys=True, default=str),
                         json.dumps(b, sort_keys=True, default=str))

    def test_explicit_override_reproduces_the_ltv_path(self):
        """Handing the engine exactly what it would have derived must
        change nothing -- proving the override is a substitution, not a
        different calculation."""
        inputs = {
            "purchase_price": 6_990_000.0, "closing_costs_pct": 2.0, "ltv_pct": 65.0,
            "interest_rate_pct": 6.5, "amort_years": 30, "hold_years": 5,
            "exit_cap_pct": 6.0, "selling_costs_pct": 2.0,
            "noi_year1": 1.0, "noi_growth_pct": 0.0,
        }
        series = [384455.38, 400185.0, 416492.0, 433395.0, 450915.0]
        loan = 6_990_000.0 * 0.65
        equivalent = {
            "loan_amount": loan,
            "annual_debt_service": dam.monthly_payment(loan, 0.065, 30) * 12,
            "balance_at_exit": dam.remaining_balance(loan, 0.065, 30, 60),
        }
        a = dam.analyze_noi_series(inputs, series, 469_000.0)
        b = dam.analyze_noi_series(inputs, series, 469_000.0, debt=equivalent)
        self.assertEqual(json.dumps(a, sort_keys=True, default=str),
                         json.dumps(b, sort_keys=True, default=str))


class TestEagleRockWithATwoLoanStack(unittest.TestCase):
    """The real scenario re-financed with a stack, to prove the wiring
    reaches the returns rather than only the summary card."""

    def setUp(self):
        self.scenario, self.units, self.expenses = load_fixture()
        self.loans = [
            {"name": "Senior", "amount": 4_000_000.0, "rate_pct": 6.0, "amort_years": 30},
            {"name": "Supplemental", "amount": 543_500.0, "rate_pct": 8.0, "amort_years": 20},
        ]

    def test_returns_use_the_summed_debt_service(self):
        r = um.analyze_scenario(self.scenario, self.units, self.expenses, self.loans)
        stack = r["debt_stack"]
        self.assertAlmostEqual(r["returns"]["annual_debt_service"],
                               stack["annual_debt_service"], places=9)
        self.assertAlmostEqual(r["returns"]["loan_amount"], 4_543_500.0, places=6)
        self.assertAlmostEqual(r["returns"]["loan_balance_at_exit"],
                               stack["balance_at_exit"], places=9)

    def test_dscr_headline_is_the_combined_figure(self):
        r = um.analyze_scenario(self.scenario, self.units, self.expenses, self.loans)
        self.assertAlmostEqual(r["returns"]["dscr"],
                               r["debt_stack"]["combined_dscr"], places=12)

    def test_ltv_is_computed_not_taken_from_the_scenario(self):
        """The scenario's stored ltv_pct is 65; the stack here happens to
        total the same 65% of price, so the check is that the engine used
        the computed value rather than coincidentally agreeing."""
        r = um.analyze_scenario(self.scenario, self.units, self.expenses, self.loans)
        self.assertAlmostEqual(r["debt_stack"]["implied_ltv_pct"], 65.0, places=9)
        self.assertAlmostEqual(r["returns"]["inputs"]["ltv_pct"], 65.0, places=9)

        heavier = list(self.loans) + [
            {"name": "Third", "amount": 699_000.0, "rate_pct": 10.0, "amort_years": 10}]
        r2 = um.analyze_scenario(self.scenario, self.units, self.expenses, heavier)
        self.assertAlmostEqual(r2["debt_stack"]["implied_ltv_pct"], 75.0, places=9)
        self.assertAlmostEqual(r2["returns"]["inputs"]["ltv_pct"], 75.0, places=9)
        self.assertEqual(self.scenario["ltv_pct"], 65.0, "fixture must be unmodified")

    def test_equity_reflects_the_stack_not_the_stored_ltv(self):
        heavier = list(self.loans) + [
            {"name": "Third", "amount": 699_000.0, "rate_pct": 10.0, "amort_years": 10}]
        r = um.analyze_scenario(self.scenario, self.units, self.expenses, heavier)
        # price - debt + closing costs
        expected = 6_990_000.0 - 5_242_500.0 + 6_990_000.0 * 0.02
        self.assertAlmostEqual(r["returns"]["equity_invested"], expected, places=6)

    def test_noi_is_untouched_by_financing(self):
        """Financing must not move the property's income."""
        base = um.analyze_scenario(self.scenario, self.units, self.expenses)
        stacked = um.analyze_scenario(self.scenario, self.units, self.expenses, self.loans)
        self.assertEqual(base["projection"]["noi_series"],
                         stacked["projection"]["noi_series"])
        self.assertEqual(base["egi"], stacked["egi"])
        self.assertAlmostEqual(stacked["projection"]["years"][0]["noi"],
                               EAGLE_ROCK["noi_year1"], places=2)


class TestValidation(unittest.TestCase):
    def test_blank_amount_is_rejected(self):
        with self.assertRaises(ulm.LoanValidationError):
            ulm.validate([{"name": "X", "amount": None, "rate_pct": 6.0, "amort_years": 30}])

    def test_negative_amount_is_rejected(self):
        with self.assertRaises(ulm.LoanValidationError):
            ulm.validate([{"name": "X", "amount": -1, "rate_pct": 6.0, "amort_years": 30}])

    def test_zero_amort_is_rejected(self):
        with self.assertRaises(ulm.LoanValidationError):
            ulm.validate([{"name": "X", "amount": 100.0, "rate_pct": 6.0, "amort_years": 0}])

    def test_implausible_rate_is_rejected(self):
        with self.assertRaises(ulm.LoanValidationError):
            ulm.validate([{"name": "X", "amount": 100.0, "rate_pct": 650.0, "amort_years": 30}])

    def test_too_many_loans_rejected(self):
        many = [dict(LOAN_A) for _ in range(ulm.MAX_LOANS + 1)]
        with self.assertRaises(ulm.LoanValidationError):
            ulm.validate(many)

    def test_error_names_the_loan(self):
        with self.assertRaises(ulm.LoanValidationError) as ctx:
            ulm.validate([{"name": "Mezzanine", "amount": None,
                           "rate_pct": 6.0, "amort_years": 30}])
        self.assertIn("Mezzanine", str(ctx.exception))

    def test_zero_rate_loan_is_straight_line(self):
        loan = {"name": "Seller note", "amount": 120_000.0,
                "rate_pct": 0.0, "amort_years": 10}
        self.assertAlmostEqual(ulm.annual_payment(loan), 12_000.0, places=9)
        self.assertAlmostEqual(ulm.balance_after(loan, 60), 60_000.0, places=6)

    def test_zero_amount_loan_contributes_nothing(self):
        summary = ulm.summarize(
            [{"name": "Undrawn", "amount": 0.0, "rate_pct": 6.0, "amort_years": 30}],
            hold_years=5, noi_year1=100_000.0, purchase_price=1_000_000.0)
        self.assertEqual(summary["annual_debt_service"], 0.0)
        self.assertEqual(summary["balance_at_exit"], 0.0)
        self.assertIsNone(summary["combined_dscr"])
        self.assertIsNone(summary["loans"][0]["dscr"])

    def test_implied_ltv_none_without_a_price(self):
        self.assertIsNone(ulm.implied_ltv_pct([LOAN_A], None))
        self.assertIsNone(ulm.implied_ltv_pct([LOAN_A], 0))


class TestPurity(unittest.TestCase):
    def test_loans_math_has_no_flask_or_db_import(self):
        import ast
        tree = ast.parse(Path(ulm.__file__).read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        self.assertNotIn("flask", roots)
        self.assertNotIn("sqlite3", roots)


if __name__ == "__main__":
    unittest.main()
