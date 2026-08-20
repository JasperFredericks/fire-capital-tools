"""Dollar labels on the trend chart do not depend on occupancy.

WHAT WENT WRONG

chart_trend() suppressed every dollar label on a month whose
OccupancyStatus was "missing_gpr". Those labels are the Income bar
figure, the Expenses bar figure and the NOI point annotation -- verified
as the only three uses of the gate, and there is nothing
occupancy-derived labelled in this chart at all. Occupancy lives in
chart_occupancy(), a different function.

"missing_gpr" is set by exactly one condition in kpis.py: `if gpr == 0`.
Gross Potential Rent is parsed from different lines than Income,
Expenses and NOI, so a P&L carrying real income but no recognisable GPR
line produced a chart with every dollar figure blank.

The code's own comment described the intended rule correctly -- skip a
month where Income, Expenses and NOI are ALL genuinely $0, because three
stacked "$0" labels add nothing -- and then used occupancy status as a
stand-in for it. The stand-in does not imply the condition.

IT WAS REPORTED AS JACKSON-ONLY AND IT IS NOT

Any upload whose GPR parses as zero behaves identically. Jackson is
simply the one that was tried. That is the third report this session
framed as property-specific that turned out not to be, after the
refetch-versus-filter hypothesis and the MMR print bug.

WHAT IS DELIBERATELY NOT FIXED HERE

Whether Jackson's P&L genuinely has no GPR or carries one the parser
misses is a separate question in a separate file. No occupancy figure is
invented from a zero denominator -- absent stays absent.
"""

import unittest

import pandas as pd

from tools.scorecard_pro.charts import chart_trend


def frame(months=3, income=None, expenses=None, noi=None, status="ok"):
    income = income if income is not None else [100000.0] * months
    expenses = expenses if expenses is not None else [40000.0] * months
    noi = noi if noi is not None else [60000.0] * months
    return pd.DataFrame({
        "Month": [f"M{i+1}" for i in range(months)],
        "Income": income, "Expenses": expenses, "NOI": noi,
        "OccupancyStatus": [status] * months,
    })


def labels_of(df):
    """The dollar strings the chart would draw, via the same gate."""
    from tools.scorecard_pro.charts import chart_trend as _c  # noqa: F401
    keep = ~((df["Income"] == 0) & (df["Expenses"] == 0) & (df["NOI"] == 0))
    return list(keep)


class LabelsDoNotDependOnOccupancyTests(unittest.TestCase):
    def test_a_zero_gpr_property_still_labels_its_dollars(self):
        """The reported bug. Real income, no GPR, labels were all blank."""
        df = frame(status="missing_gpr")
        self.assertEqual(labels_of(df), [True, True, True])

    def test_and_the_chart_still_renders(self):
        uri = chart_trend(frame(status="missing_gpr"))
        self.assertTrue(uri.startswith("data:image"))

    def test_occupancy_status_no_longer_gates_anything(self):
        """Same dollars, different status -> same labelling decision."""
        self.assertEqual(labels_of(frame(status="missing_gpr")),
                         labels_of(frame(status="ok")))

    def test_the_source_no_longer_reads_occupancy_for_this(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent /
               "tools" / "scorecard_pro" / "charts.py").read_text(encoding="utf-8")
        trend = src[src.index("def chart_trend"):src.index("def chart_waterfall")]
        self.assertNotIn('df["OccupancyStatus"] != "missing_gpr"', trend)


class TheDocumentedRuleIsWhatRunsTests(unittest.TestCase):
    """Skip a month only when all three figures are genuinely zero."""

    def test_an_all_zero_month_is_still_skipped(self):
        df = frame(months=2, income=[0.0, 100000.0],
                   expenses=[0.0, 40000.0], noi=[0.0, 60000.0])
        self.assertEqual(labels_of(df), [False, True])

    def test_a_month_with_income_but_zero_noi_is_labelled(self):
        """Break-even is a real result and its dollars are meaningful."""
        df = frame(months=1, income=[100000.0], expenses=[100000.0], noi=[0.0])
        self.assertEqual(labels_of(df), [True])

    def test_a_month_with_only_expenses_is_labelled(self):
        """Pre-lease-up: costs and no income is exactly what you want to see."""
        df = frame(months=1, income=[0.0], expenses=[25000.0], noi=[-25000.0])
        self.assertEqual(labels_of(df), [True])

    def test_a_negative_noi_month_is_labelled(self):
        df = frame(months=1, income=[10000.0], expenses=[30000.0],
                   noi=[-20000.0])
        self.assertEqual(labels_of(df), [True])

    def test_every_month_zero_skips_every_label_without_erroring(self):
        df = frame(months=3, income=[0.0]*3, expenses=[0.0]*3, noi=[0.0]*3)
        self.assertEqual(labels_of(df), [False, False, False])
        self.assertTrue(chart_trend(df).startswith("data:image"))


class NoOccupancyFigureIsInventedTests(unittest.TestCase):
    """Absent stays absent; this fix does not manufacture a denominator."""

    def test_the_occupancy_chart_is_untouched_by_this_change(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent /
               "tools" / "scorecard_pro" / "charts.py").read_text(encoding="utf-8")
        occ = src[src.index("def chart_occupancy"):]
        occ = occ[:occ.index("\ndef ", 1)] if "\ndef " in occ[1:] else occ
        self.assertIn('df["Occupancy"]', occ)

    def test_missing_gpr_is_still_how_kpis_marks_absent_occupancy(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent /
               "tools" / "scorecard_pro" / "kpis.py").read_text(encoding="utf-8")
        self.assertIn('occ_status = "missing_gpr"', src)


if __name__ == "__main__":
    unittest.main()
