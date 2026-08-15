"""
FIRE Capital Tools - Site DD capex export.

Turns an assessment's findings into a capital budget: PDF via matplotlib
PdfPages and XLSX via openpyxl, the same two mechanisms every other
export here uses.

THE PROVENANCE COLUMN IS THE POINT

Every line says where its number came from:

    Inspector estimate    somebody stood in the room and judged it
    Researched average    a national figure from tools/site_dd_reference_costs
    No estimate           nothing priced it, and the line says so

Those are three different kinds of claim and a budget that rendered them
identically would let a national average acquire the authority of a
site visit. The three are separated in the totals as well as the rows,
because "of this $84,000, $61,000 is national averages nobody has
checked" is the sentence that decides whether the number is usable.

WHAT IS NOT INCLUDED

A finding with no cost contributes nothing to the total, but IS still
listed with "no estimate". Dropping it would make the budget look
complete when it is not, which is the failure mode that matters: an
underlying repair that never got priced is invisible, and the total
reads as the whole job.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                       # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages   # noqa: E402

from tools import site_dd_checklist as cl              # noqa: E402
from tools import site_dd_costs as costs               # noqa: E402
from tools import site_dd_reference_costs as refcosts  # noqa: E402

PAGE_SIZE = (11, 8.5)
INK = "#1a2744"
MUTED = "#6b7280"
BODY = "#111827"
WARN = "#b45309"

ROWS_PER_PAGE = 26

SOURCE_COLUMN = {
    costs.SOURCE_MANUAL: "Inspector estimate",
    costs.SOURCE_REFERENCE: "Researched average",
    costs.SOURCE_NONE: "No estimate",
}


def build_lines(findings: list[dict[str, Any]], labels: dict[str, str] | None = None
                ) -> list[dict[str, Any]]:
    """One budget row per finding that has a condition worth costing.

    Quantity comes from the finding's own instance, so two sinks are two
    rows rather than one -- the capex export groups by category, not by
    item, and a reader adding them up should see each object.
    """
    labels = labels or {}
    out = []
    for f in findings or []:
        described = costs.describe(f)
        cat = costs.capex_category(f)
        out.append({
            "item_key": f.get("item_key"),
            "label": (f.get("instance_label")
                      or labels.get(f.get("item_key"))
                      or f.get("item_key")),
            "category": cat,
            "category_name": cl.CATEGORY_NAMES.get(cat, "Uncategorised"),
            "condition": f.get("condition"),
            "scope": f.get("scope"),
            "unit_cost": described["cost"],
            "source": described["source"],
            "source_label": SOURCE_COLUMN[described["source"]],
            "quantity": 1.0,
            "total": described["cost"] or 0.0,
            "reason": (refcosts.reason(f.get("item_key"))
                       if described["cost"] is None else ""),
        })
    return out


def summarize(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Totals, split by where the money came from.

    Deliberately three totals rather than one. A single figure would
    average a site visit and a national average into a number that
    describes neither.
    """
    by_source = {k: 0.0 for k in SOURCE_COLUMN}
    by_category: dict[str, float] = {}
    unpriced: list[dict[str, Any]] = []
    for l in lines:
        by_source[l["source"]] += l["total"]
        if l["total"]:
            by_category[l["category_name"]] = (
                by_category.get(l["category_name"], 0.0) + l["total"])
        if l["unit_cost"] is None:
            unpriced.append(l)
    total = sum(by_source.values())
    return {
        "total": total,
        "by_source": by_source,
        "by_category": dict(sorted(by_category.items(),
                                   key=lambda kv: -kv[1])),
        "unpriced": unpriced,
        "unpriced_count": len(unpriced),
        "line_count": len(lines),
        "researched_pct": (by_source[costs.SOURCE_REFERENCE] / total * 100)
                          if total else 0.0,
        "researched_on": refcosts.RESEARCHED_ON,
    }


def _money(value: Any) -> str:
    return "—" if value in (None, "") else f"${float(value):,.0f}"


def build_pdf(path, assessment: dict[str, Any], lines: list[dict[str, Any]],
              summary: dict[str, Any]) -> Path:
    path = Path(path)
    label = assessment.get("property_label") or "Property"
    pages = [lines[i:i + ROWS_PER_PAGE]
             for i in range(0, len(lines), ROWS_PER_PAGE)] or [[]]

    with PdfPages(str(path)) as pdf:
        for page_no, page in enumerate(pages, start=1):
            fig = plt.figure(figsize=PAGE_SIZE)
            fig.text(0.06, 0.94, "Capital Budget", fontsize=16,
                     fontweight="bold", color=INK)
            fig.text(0.06, 0.915, label, fontsize=11, color="#4b5563")
            fig.text(0.94, 0.94, _money(summary["total"]), ha="right",
                     fontsize=15, fontweight="bold", color=INK)
            fig.text(0.94, 0.915,
                     f"{summary['line_count']} items · "
                     f"{summary['unpriced_count']} unpriced",
                     ha="right", fontsize=9, color=MUTED)

            if page_no == 1:
                y = 0.865
                fig.text(0.06, y, "Where these numbers come from",
                         fontsize=10, fontweight="bold", color=INK)
                y -= 0.028
                for key in (costs.SOURCE_MANUAL, costs.SOURCE_REFERENCE,
                            costs.SOURCE_NONE):
                    amount = summary["by_source"][key]
                    fig.text(0.06, y, f"{SOURCE_COLUMN[key]}", fontsize=9,
                             color=BODY)
                    fig.text(0.34, y, _money(amount) if key != costs.SOURCE_NONE
                             else f"{summary['unpriced_count']} item(s), not costed",
                             fontsize=9, color=BODY)
                    y -= 0.024
                y -= 0.012
                fig.text(0.06, y,
                         f"{summary['researched_pct']:.0f}% of this total is "
                         f"researched national averages ({summary['researched_on']}), "
                         f"not quotes for this building.",
                         fontsize=8.5, color=WARN)
                top = y - 0.045
            else:
                top = 0.865

            cols = (0.06, 0.30, 0.50, 0.64, 0.78, 0.94)
            heads = ("Item", "Category", "Condition", "Source", "Unit", "Total")
            for x, head, align in zip(cols, heads,
                                      ("left",) * 5 + ("right",)):
                fig.text(x, top, head, fontsize=8.5, fontweight="bold",
                         color=MUTED, ha=align)
            y = top - 0.024

            for row in page:
                fig.text(cols[0], y, textwrap.shorten(str(row["label"]), 30,
                                                      placeholder="…"),
                         fontsize=8.5, color=BODY)
                fig.text(cols[1], y, textwrap.shorten(row["category_name"], 26,
                                                      placeholder="…"),
                         fontsize=8, color=MUTED)
                fig.text(cols[2], y, (row["condition"] or "—").title(),
                         fontsize=8, color=MUTED)
                fig.text(cols[3], y, row["source_label"], fontsize=8,
                         color=WARN if row["source"] == costs.SOURCE_REFERENCE
                         else (MUTED if row["source"] == costs.SOURCE_NONE else BODY))
                fig.text(cols[4], y, _money(row["unit_cost"]), fontsize=8.5,
                         color=BODY)
                fig.text(cols[5], y, _money(row["total"]) if row["total"] else "—",
                         fontsize=8.5, color=BODY, ha="right")
                y -= 0.026

            fig.text(0.06, 0.05,
                     "Researched averages are national figures for budgeting, "
                     "not quotes. Items with no estimate are listed but "
                     "contribute nothing to the total.",
                     fontsize=7.5, color=MUTED)
            fig.text(0.94, 0.05, f"Page {page_no} of {len(pages)}",
                     ha="right", fontsize=8, color=MUTED)
            pdf.savefig(fig)
            plt.close(fig)
    return path


def build_xlsx(path, assessment: dict[str, Any], lines: list[dict[str, Any]],
               summary: dict[str, Any]) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    path = Path(path)
    wb = Workbook()
    ws = wb.active
    ws.title = "Capital Budget"
    bold = Font(bold=True)

    ws.append(["Capital Budget"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([assessment.get("property_label") or ""])
    ws.append([f"Inspected {assessment.get('assessed_on') or '—'}"])
    ws.append([])
    ws.append(["Total", summary["total"]])
    for key in (costs.SOURCE_MANUAL, costs.SOURCE_REFERENCE, costs.SOURCE_NONE):
        ws.append([SOURCE_COLUMN[key],
                   summary["by_source"][key] if key != costs.SOURCE_NONE
                   else f"{summary['unpriced_count']} item(s), not costed"])
    ws.append([f"{summary['researched_pct']:.0f}% of the total is researched "
               f"national averages ({summary['researched_on']}), not quotes."])
    ws.append([])

    header = ["Item", "Category", "Scope", "Condition", "Cost source",
              "Unit cost", "Qty", "Total", "Why no estimate"]
    ws.append(header)
    for cell in ws[ws.max_row]:
        cell.font = bold

    for l in lines:
        ws.append([l["label"], l["category_name"], l["scope"],
                   (l["condition"] or ""), l["source_label"],
                   l["unit_cost"], l["quantity"],
                   l["total"] or None, l["reason"]])

    for col, width in zip("ABCDEFGHI", (30, 26, 10, 12, 20, 12, 6, 12, 60)):
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=11, min_col=9, max_col=9):
        row[0].alignment = Alignment(wrap_text=True, vertical="top")

    # The reference table itself, so a reader can audit any figure that
    # appeared above without leaving the file.
    ref = wb.create_sheet("Reference costs")
    ref.append(["Item", "Unit cost", "Unit", "Sources", "How it was derived"])
    for cell in ref[1]:
        cell.font = bold
    for key in sorted(refcosts.REFERENCE_COSTS):
        c = refcosts.REFERENCE_COSTS[key]
        ref.append([c.key, c.unit_cost, refcosts.UNIT_LABELS[c.unit],
                    ", ".join(c.sources), c.note])
    for col, width in zip("ABCDE", (26, 12, 14, 42, 80)):
        ref.column_dimensions[col].width = width
    for row in ref.iter_rows(min_row=2, min_col=5, max_col=5):
        row[0].alignment = Alignment(wrap_text=True, vertical="top")

    # And what has NO figure, with the reason. This sheet is the ask.
    un = wb.create_sheet("Not priced")
    un.append(["Item", "Why it has no researched figure"])
    for cell in un[1]:
        cell.font = bold
    for row in refcosts.unpriced_report():
        un.append([row["label"], row["reason"]])
    un.column_dimensions["A"].width = 30
    un.column_dimensions["B"].width = 90
    for r in un.iter_rows(min_row=2, min_col=2, max_col=2):
        r[0].alignment = Alignment(wrap_text=True, vertical="top")

    wb.save(str(path))
    return path


def suggested_filename(assessment: dict[str, Any], ext: str) -> str:
    label = "".join(ch if ch.isalnum() or ch in " -_" else ""
                    for ch in (assessment.get("property_label") or "property"))
    label = "-".join(label.split()).lower()[:48] or "property"
    return f"capex-budget-{label}.{ext}"
