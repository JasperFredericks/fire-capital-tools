from __future__ import annotations

import re

import pandas as pd

from tools.scorecard_pro.utils import (
    format_percent,
    month_sort_key,
    noi_variance_flag,
)


# Diagnostic tolerance for flagging a "Total Operating Income/Expense" override
# row (9998/9999) that doesn't match the sum of its own detail rows. This is
# used ONLY to surface a Parsing Notes warning -- it never changes any KPI.
# A discrepancy is flagged only when it exceeds BOTH an absolute floor ($1, to
# ignore sub-dollar float/rounding from summing many rows) AND a relative floor
# (1% of the larger side, to ignore immaterial proportional noise). Real cases
# (~$1k-15k/month, ~10-50% of the monthly total) clear both by a wide margin;
# ordinary rounding does not.
_OVERRIDE_MISMATCH_ABS = 1.0
_OVERRIDE_MISMATCH_PCT = 0.01


def _override_mismatch(total: float, detail: float) -> bool:
    diff = abs(float(total) - float(detail))
    return diff > _OVERRIDE_MISMATCH_ABS and diff > _OVERRIDE_MISMATCH_PCT * max(abs(total), abs(detail))


class KPICalculator:
    def __init__(self, pnl_data):
        self.accounts = pnl_data["accounts"]
        available_months = set()
        for acc in self.accounts.values():
            available_months.update(acc["data"].keys())
        self.available_months = sorted(list(available_months), key=month_sort_key)
        self.expense_fallback_codes = sorted(
            code for code in self.accounts if re.fullmatch(r"6\d{3}", str(code))
        )

        # Additional top-level income categories beyond GPR/NRI (4000) and
        # Other Income (4300) — e.g. a tree-report P&L with a sibling income
        # line like "4580 High Risk Fee" that isn't nested under either.
        # Scoped by tree depth (the column an account code was found in, set
        # by parse_resman()) rather than by code range, since a leaf code
        # can numerically fall outside 4300's range while still being a
        # nested sub-line already counted in 4300's own total (e.g. "4500
        # Credit Builder" nested one level deeper than the 4580 sibling).
        # Formats without depth info (flat CSVs) leave this empty, which
        # preserves the exact previous nri + other_income behavior for them.
        income_depths = [
            acc.get("depth")
            for code, acc in self.accounts.items()
            if re.fullmatch(r"4\d{3}", str(code)) and acc.get("depth") is not None
        ]
        if income_depths:
            shallowest_income_depth = min(income_depths)
            self.income_fallback_codes = sorted(
                code
                for code, acc in self.accounts.items()
                if re.fullmatch(r"4\d{3}", str(code))
                and acc.get("depth") == shallowest_income_depth
                and code not in ("4000", "4300")
            )
        else:
            self.income_fallback_codes = []

        # OXPT-specific: Asset Management Fees (code 7210) is treated as a
        # below-NOI item in the app's own NOI math, per Michelle's explicit
        # decision — the exported Scorecard spreadsheet is unaffected (7210
        # still gets written to its existing row by ScorecardUpdater).
        # Scoped to OXPT by property name, not by bare code number: Canyon's
        # chart of accounts also happens to use code 7210 for the same
        # concept, but that's a separate decision Michelle hasn't made yet,
        # and Eagle Rock uses a different code (7270) entirely.
        self.below_noi_codes = set()
        property_name = str(pnl_data.get("property") or "").strip().lower()
        if "oxford pointe" in property_name:
            self.below_noi_codes.add("7210")

        # Honest per-month sums of the file's own visible detail rows under
        # each Total Operating Income/Expense section (from the parser, which
        # is the only place the raw rows are seen). Used solely by the
        # override-mismatch diagnostic below; empty for formats that don't
        # provide it, in which case the diagnostic is skipped rather than
        # comparing against the keyword-bucketed codes (which mis-classify
        # some rows -- see calculate()).
        detail_totals = pnl_data.get("detail_totals") or {}
        self.detail_income_totals = detail_totals.get("income") or {}
        self.detail_expense_totals = detail_totals.get("expense") or {}

        # Per-month diagnostics (populated by calculate()): months where the
        # file's Total Operating Income/Expense override row is used but does
        # not match the sum of that month's detail rows. Advisory only.
        self.override_mismatches: list = []

    # ── Itemized category breakdown (used by Underwriting) ───────────────
    #
    # Added for the Underwriting tool, which needs the *line items* behind
    # income/expense rather than the monthly totals calculate() produces.
    # Deliberately a separate method on this class rather than a parallel
    # aggregation elsewhere: a second implementation of "which accounts roll
    # into what" would eventually disagree with Scorecard Pro about the same
    # T12, and the disagreement would be silent.
    #
    # The hard part is that a tree-format P&L contains BOTH rollup parents
    # and their children. Summing every account double-counts massively --
    # on a real Eagle Rock T12 a naive sum of all 6xxx+7xxx gives 3,414,662
    # against true controllable opex of ~436,000, roughly 8x. So only leaves
    # are summed.

    # Non-operating lines that must not land in an operating expense total.
    # Debt service is modeled separately by the loan, so counting it here
    # too would double-charge it; capital items are not operating expense.
    # Matched on the account NAME rather than a code range because code
    # ranges differ between charts of accounts, while these labels do not.
    _NON_OPERATING_NAME_PATTERNS = (
        "debt service", "mortgage", "interest payment", "principal payment",
        "loan payment", "escrow",
    )
    _CAPEX_NAME_PATTERNS = (
        "rehab", "replacement", "capital", "capex", "improvement",
        "renovation", "reserve",
    )

    @staticmethod
    def _classify_line(name: str) -> str:
        low = " ".join(str(name or "").strip().lower().split())
        for pat in KPICalculator._NON_OPERATING_NAME_PATTERNS:
            if pat in low:
                return "non_operating"
        for pat in KPICalculator._CAPEX_NAME_PATTERNS:
            if pat in low:
                return "capex"
        return "operating"

    def _leaf_codes(self):
        """Codes with no children, plus the category each belongs to.

        Hierarchy comes from `depth` (the column the account was found in)
        read in document order: an account is a parent when the account
        immediately after it sits deeper. A leaf's category is its nearest
        ancestor one level below the shallowest expense/income grouping --
        i.e. the row a human would read as the category heading ("Utilities",
        "Salaries & Payroll Related", "Property Taxes"), taken from the file
        itself rather than a hardcoded prefix table that would have to be
        re-guessed for every new chart of accounts.

        Flat formats carry no depth at all (the cash-flow parser), in which
        case every account is its own leaf and its own category -- which is
        correct for a file with no rollup rows to double-count.
        """
        ordered = list(self.accounts.items())
        depths = [a.get("depth") for _, a in ordered]
        has_depth = any(d is not None for d in depths)

        if not has_depth:
            return [
                {"code": c, "name": a["name"], "depth": None,
                 "category_code": c, "category_name": a["name"]}
                for c, a in ordered
            ]

        known = [d for d in depths if d is not None]
        category_depth = min(known) + 1 if known else 0

        leaves = []
        for idx, (code, acc) in enumerate(ordered):
            d = acc.get("depth")
            if d is None:
                continue
            nxt = next((depths[j] for j in range(idx + 1, len(ordered))
                        if depths[j] is not None), None)
            if nxt is not None and nxt > d:
                continue  # has children -- a rollup row, not a line item
            # nearest preceding account at exactly category_depth
            cat_code, cat_name = code, acc["name"]
            if d > category_depth:
                for j in range(idx - 1, -1, -1):
                    jd = ordered[j][1].get("depth")
                    if jd is not None and jd == category_depth:
                        cat_code, cat_name = ordered[j][0], ordered[j][1]["name"]
                        break
            leaves.append({"code": code, "name": acc["name"], "depth": d,
                           "category_code": cat_code, "category_name": cat_name})
        return leaves

    def category_breakdown(self, code_pattern=r"[67]\d{3}"):
        """Leaf line items grouped by category, with rollup diagnostics.

        Returns line items (12-month totals plus monthly detail), category
        subtotals, and -- where the file also carries a rollup parent for a
        category -- the difference between that parent and the sum of its
        own leaves. On a real Eagle Rock T12 those disagree by about 4,339,
        so the discrepancy is reported rather than silently resolved: the
        leaves are authoritative (they are what the user edits, and they must
        add up to what is displayed), and the parent is shown alongside as a
        check.
        """
        rx = re.compile(code_pattern)
        leaves = [l for l in self._leaf_codes() if rx.fullmatch(str(l["code"]))]

        def total(code):
            return sum(v or 0.0 for v in self.accounts[code]["data"].values())

        lines = []
        for l in leaves:
            kind = self._classify_line(l["name"])
            lines.append({
                **l,
                "annual_total": total(l["code"]),
                "monthly": dict(self.accounts[l["code"]]["data"]),
                "line_kind": kind,
                "is_included_default": kind == "operating",
            })

        cats: dict = {}
        for ln in lines:
            c = cats.setdefault(ln["category_code"], {
                "category_code": ln["category_code"],
                "category_name": ln["category_name"],
                "lines": [], "leaf_total": 0.0, "operating_total": 0.0,
            })
            c["lines"].append(ln)
            c["leaf_total"] += ln["annual_total"]
            if ln["is_included_default"]:
                c["operating_total"] += ln["annual_total"]

        discrepancies = []
        for code, c in cats.items():
            if code in self.accounts and not any(l["code"] == code for l in leaves):
                parent = total(code)
                if abs(parent - c["leaf_total"]) > 1.0:
                    discrepancies.append({
                        "category_code": code, "category_name": c["category_name"],
                        "parent_total": parent, "leaf_total": c["leaf_total"],
                        "difference": parent - c["leaf_total"],
                    })

        return {
            "lines": lines,
            "categories": sorted(cats.values(), key=lambda c: c["category_code"]),
            "discrepancies": discrepancies,
            "operating_total": sum(l["annual_total"] for l in lines if l["is_included_default"]),
            "excluded_total": sum(l["annual_total"] for l in lines if not l["is_included_default"]),
        }

    def get_val(self, code, month):
        if code in self.accounts:
            return float(self.accounts[code]["data"].get(month, 0.0) or 0.0)
        return 0.0

    def calculate(self):
        self.override_mismatches = []
        kpis = {
            "income": {},
            "expenses": {},
            "noi": {},
            "physical_occupancy": {},
            "economic_occupancy": {},
            "expense_ratio": {},
            "noi_margin": {},
            "occupancy_status": {},
            "expense_fallback_codes": self.expense_fallback_codes,
            "income_fallback_codes": self.income_fallback_codes,
            "override_mismatches": self.override_mismatches,
        }

        for month in self.available_months:
            gpr = self.get_val("4110", month)
            vacancy_loss = self.get_val("4220", month)
            nri = self.get_val("4000", month)
            other_income = self.get_val("4300", month)

            # Only reconstruct NRI from GPR + Vacancy Loss when code 4000
            # was never captured in this file at all — a genuinely-parsed
            # 4000 value of exactly 0 (a real accounting outcome some
            # months) must be trusted, not silently overridden.
            if "4000" not in self.accounts and gpr != 0:
                nri = gpr + vacancy_loss

            override_income = self.get_val("9998", month)
            if override_income != 0:
                total_income = override_income
            else:
                additional_income = sum(self.get_val(code, month) for code in self.income_fallback_codes)
                total_income = nri + other_income + additional_income

            controllable = self.get_val("6000", month)
            non_controllable = self.get_val("7000", month)
            for code in self.below_noi_codes:
                non_controllable -= self.get_val(code, month)
            override_expenses = self.get_val("9999", month)
            if override_expenses != 0:
                total_expenses = override_expenses
            else:
                if controllable == 0 and non_controllable == 0:
                    for code in self.expense_fallback_codes:
                        controllable += self.get_val(code, month)
                total_expenses = controllable + non_controllable

            # Diagnostic only (never changes total_income/total_expenses above):
            # when a Total Operating Income/Expense override row (9998/9999) is
            # used as the authoritative total, compare it against the sum of the
            # file's OWN visible detail rows for this month (self.detail_*_totals,
            # provided by the parser) and record a mismatch so Parsing Notes can
            # flag an edited-detail-but-stale-total file. This compares against
            # the real file rows -- NOT the keyword-bucketed account codes, which
            # can mis-classify a row (e.g. "Management Fees" into income 4300) and
            # produce false positives. If the parser didn't supply honest detail
            # sums for this month, the comparison is skipped rather than guessed.
            if override_income != 0 and month in self.detail_income_totals:
                detail_income = self.detail_income_totals[month]
                if _override_mismatch(override_income, detail_income):
                    self.override_mismatches.append({
                        "month": month,
                        "kind": "income",
                        "total": override_income,
                        "detail": detail_income,
                    })
            if override_expenses != 0 and month in self.detail_expense_totals:
                detail_expenses = self.detail_expense_totals[month]
                if _override_mismatch(override_expenses, detail_expenses):
                    self.override_mismatches.append({
                        "month": month,
                        "kind": "expense",
                        "total": override_expenses,
                        "detail": detail_expenses,
                    })

            noi = total_income - total_expenses

            if gpr == 0:
                phys_occ = None
                econ_occ = None
                occ_status = "missing_gpr"
            else:
                phys_occ = 1 - (abs(vacancy_loss) / gpr)
                econ_occ = nri / gpr
                occ_status = "zero" if phys_occ == 0 else "ok"

            exp_ratio = total_expenses / total_income if total_income != 0 else None
            noi_margin = noi / total_income if total_income != 0 else None

            kpis["income"][month] = total_income
            kpis["expenses"][month] = total_expenses
            kpis["noi"][month] = noi
            kpis["physical_occupancy"][month] = phys_occ
            kpis["economic_occupancy"][month] = econ_occ
            kpis["expense_ratio"][month] = exp_ratio
            kpis["noi_margin"][month] = noi_margin
            kpis["occupancy_status"][month] = occ_status

        return kpis


class ReportGenerator:
    def __init__(self, kpis):
        self.kpis = kpis
        self.months = list(kpis["income"].keys())

    def generate(self):
        total_income = sum(float(v or 0.0) for v in self.kpis["income"].values())
        total_noi = sum(float(v or 0.0) for v in self.kpis["noi"].values())
        valid_occupancies = [
            value
            for month, value in self.kpis["physical_occupancy"].items()
            if isinstance(value, (int, float)) and self.kpis["occupancy_status"].get(month) != "missing_gpr"
        ]
        avg_occ = sum(valid_occupancies) / len(valid_occupancies) if valid_occupancies else None

        report = []
        report.append("=== PROPERTY FINANCIAL SCORECARD REPORT ===")
        report.append(f"Period Analysis: {len(self.months)} Months")
        report.append("\n-- KEY METRICS --")
        report.append(f"Total Income: ${total_income:,.2f}")
        report.append(f"Total NOI:    ${total_noi:,.2f}")
        report.append(f"Avg Physical Occupancy: {format_percent(avg_occ)}")

        report.append("\n-- MONTHLY TRENDS --")
        header = f"{'Month':<10} {'Income':<15} {'NOI':<15} {'Occ%':<10}"
        report.append(header)
        report.append("-" * len(header))
        for month in self.months:
            inc = self.kpis["income"][month]
            noi = self.kpis["noi"][month]
            occ = self.kpis["physical_occupancy"][month]
            occ_text = "No GPR" if self.kpis["occupancy_status"].get(month) == "missing_gpr" else format_percent(occ)
            report.append(f"{month:<10} ${inc:,.0f}       ${noi:,.0f}       {occ_text:<10}")

        q1_months = [m for m in self.months if m.split()[0] in ["Jan", "Feb", "Mar"]]
        q4_months = [m for m in self.months if m.split()[0] in ["Oct", "Nov", "Dec"]]

        if q1_months and q4_months:
            q1_noi = sum(self.kpis["noi"][month] for month in q1_months)
            q4_noi = sum(self.kpis["noi"][month] for month in q4_months)

            report.append("\n-- TREND ANALYSIS --")
            report.append(f"Q1 Total NOI: ${q1_noi:,.0f}")
            report.append(f"Q4 Total NOI: ${q4_noi:,.0f}")
            delta = q4_noi - q1_noi
            report.append(f"Change: {'+' if delta >= 0 else ''}${delta:,.0f}")

        report.append("\n-- RECOMMENDATIONS --")
        if avg_occ is not None and avg_occ < 0.90:
            report.append("1. Focus on leasing strategies to boost occupancy above 90%.")
        if total_noi < 0:
            report.append("2. CRITICAL: Review expenses immediately, NOI is negative.")
        if report[-1] == "\n-- RECOMMENDATIONS --":
            report.append("1. Continue monitoring monthly performance against budget.")

        return "\n".join(report)


def generate_advanced_insights(df_filtered, accounts, targets=None):
    def get_category_metrics(code_prefixes, name):
        relevant_codes = [code for code in accounts.keys() if any(str(code).startswith(prefix) for prefix in code_prefixes)]
        if not relevant_codes or df_filtered.empty:
            return None

        series = []
        for month in df_filtered["Month"]:
            value = sum(accounts[code]["data"].get(month, 0) for code in relevant_codes)
            series.append(value)
        series_pd = pd.Series(series, dtype="float64")
        total_val = float(series_pd.sum())

        if len(series_pd) >= 2:
            mid_point = len(series_pd) // 2
            first_half_avg = series_pd.iloc[:mid_point].mean()
            last_half_avg = series_pd.iloc[mid_point:].mean()
            pct_change = (last_half_avg - first_half_avg) / abs(first_half_avg) if first_half_avg != 0 else 0.0
        else:
            pct_change = 0.0

        return {"name": name, "total": total_val, "pct_change": float(pct_change)}

    categories = [
        (["4000", "4100", "4110"], "Rental Income"),
        (["4300"], "Other Income"),
        (["6600", "66"], "Utilities"),
        (["6500", "65"], "Contract Services & R&M"),
        (["6400", "64"], "Payroll"),
        (["6300"], "Marketing"),
        (["6100", "6200"], "Admin & Professional"),
    ]

    analyzed_cats = [get_category_metrics(prefixes, name) for prefixes, name in categories]
    analyzed_cats = [cat for cat in analyzed_cats if cat]

    key_trends = []
    for cat in analyzed_cats:
        change = cat["pct_change"]
        if abs(change) >= 0.03:
            direction = "increased" if change > 0 else "decreased"
            is_income = "Income" in cat["name"]
            is_good = (change > 0) if is_income else (change < 0)
            key_trends.append((f"{cat['name']} {direction} by {abs(change):.1%}.", is_good))

    green_flags = []
    red_flags = []
    occ_values = df_filtered["Occupancy"].dropna() if "Occupancy" in df_filtered else pd.Series(dtype="float64")
    occ_avg = float(occ_values.mean()) if not occ_values.empty else None
    if occ_avg is not None and occ_avg >= 0.93:
        green_flags.append(f"Excellent Occupancy: {occ_avg:.1%}")
    elif occ_avg is not None and occ_avg < 0.90:
        red_flags.append(f"Low Occupancy: {occ_avg:.1%}")

    income_sum = df_filtered["Income"].sum() if "Income" in df_filtered else 0
    noi_margin = df_filtered["NOI"].sum() / income_sum if income_sum else 0
    if noi_margin > 0.55:
        green_flags.append(f"Strong NOI Margin: {noi_margin:.1%}")
    elif noi_margin < 0.40:
        red_flags.append(f"Low NOI Margin: {noi_margin:.1%}")

    # Aggregate (sum expenses / sum income) rather than averaging the monthly
    # ExpenseRatio column directly — matches the NOI Margin calc above, and
    # avoids a single near-zero-income lease-up month from dominating the
    # average the way a mean-of-ratios would (confirmed against real OXPT
    # data: a mean-of-ratios gave 348% off one such month vs. a real 65%).
    expenses_sum = df_filtered["Expenses"].sum() if "Expenses" in df_filtered else 0
    expense_ratio_avg = (expenses_sum / income_sum) if income_sum else None
    if expense_ratio_avg is not None and expense_ratio_avg > 0.65:
        red_flags.append(f"High Expense Ratio: {expense_ratio_avg:.1%}")
    elif expense_ratio_avg is not None and expense_ratio_avg < 0.50:
        green_flags.append(f"Low Expense Ratio: {expense_ratio_avg:.1%}")

    # NOI vs UW/PM Budget, rolled up over the selected months (same +/-10%
    # red / +/-3% green thresholds used for the per-month Comparison table
    # flags — see noi_variance_flag() — applied here to the period total).
    months_count = len(df_filtered) if not df_filtered.empty else 0
    actual_noi_total = float(df_filtered["NOI"].sum()) if "NOI" in df_filtered and months_count else 0.0
    for label, target_dict in (("UW Budget", (targets or {}).get("UW") or {}), ("PM Budget", (targets or {}).get("PM") or {})):
        noi_target_monthly = float(target_dict.get("NOI") or 0.0)
        if not noi_target_monthly or not months_count:
            continue
        noi_target_total = noi_target_monthly * months_count
        flag = noi_variance_flag(actual_noi_total - noi_target_total, noi_target_total)
        variance_pct = (actual_noi_total - noi_target_total) / abs(noi_target_total)
        if flag == "red":
            red_flags.append(f"NOI vs {label} off by {variance_pct:+.1%}")
        elif flag == "green":
            green_flags.append(f"NOI on track vs {label} ({variance_pct:+.1%})")

    for cat in analyzed_cats:
        if "Utilities" in cat["name"] and cat["pct_change"] > 0.10:
            red_flags.append(f"Utilities spiked {cat['pct_change']:.1%}")
        if "Payroll" in cat["name"] and cat["pct_change"] > 0.10:
            red_flags.append(f"Payroll up {cat['pct_change']:.1%}")
        if "Rental Income" in cat["name"] and cat["pct_change"] > 0.05:
            green_flags.append(f"Rental Income up {cat['pct_change']:.1%}")

    recommendations = []
    if occ_avg is not None and occ_avg < 0.90:
        recommendations.append(f"Leasing: Increase marketing outreach and referral incentives (avg occupancy {occ_avg:.1%}).")
    elif occ_avg is not None and occ_avg > 0.95:
        recommendations.append(f"Revenue: Test modest rent increases or premium add-ons (avg occupancy {occ_avg:.1%}).")

    for cat in analyzed_cats:
        change = cat["pct_change"]
        if "Utilities" in cat["name"] and change > 0.05:
            recommendations.append(f"Utilities: Audit water/HVAC usage and validate vendor billing (trend {change:+.1%}).")
        if "Contract" in cat["name"] and change > 0.10:
            recommendations.append(f"Maintenance: Validate CapEx vs OpEx coding and review vendor scope (trend {change:+.1%}).")
        if "Other Income" in cat["name"] and change < -0.05:
            recommendations.append(f"Ancillary: Audit fee collections and enforce add-on compliance (trend {change:+.1%}).")

    if not recommendations:
        recommendations.append("General: Monitor weekly leasing traffic.")

    return {
        "trends": key_trends,
        "green_flags": green_flags,
        "red_flags": red_flags,
        "recommendations": recommendations,
        "analyzed_cats": analyzed_cats,
    }
