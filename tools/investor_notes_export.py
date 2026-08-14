"""
FIRE Capital Tools - investor update export.

PDF via matplotlib PdfPages and XLSX via openpyxl, the same two
mechanisms every other export in this app uses. Both libraries are
already dependencies.

WORD IS NOT OFFERED, AND THAT IS A DEPENDENCY DECISION

Michelle asked for Word. python-docx is NOT installed and is not in
requirements.txt, so a .docx export would mean a new dependency. It is a
small, pure-Python, well-maintained one and adding it would be
reasonable -- but adding a dependency is a decision for a person, not
something to slip into a feature branch. Flagged rather than assumed.

Meanwhile the PDF is the shareable artifact and the XLSX carries the
same content as text a reader can paste into Word themselves, which
covers the need without the decision.

WHY THE PDF WRAPS TEXT BY HAND

PdfPages draws figures, not documents: matplotlib has no flow layout, so
a long paragraph does not reflow onto a second page by itself. The text
is wrapped and paginated here explicitly. That is the same approach
site_dd_report takes, and the reason both truncate visibly rather than
silently.
"""

from __future__ import annotations

import datetime
import textwrap
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages   # noqa: E402

PAGE_SIZE = (8.5, 11)          # portrait: this is prose, not a dashboard
INK = "#1a2744"
MUTED = "#6b7280"
BODY = "#111827"

WRAP_AT = 96                   # characters per line at 9.5pt on portrait
LINES_PER_PAGE = 46
TOP = 0.90
LINE_STEP = 0.0185


def _lines_for(update: dict[str, Any], sections: list[dict[str, Any]],
               transcripts: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """The whole document as (style, text) lines, ready to paginate."""
    out: list[tuple[str, str]] = []
    for section in sections:
        out.append(("h2", section["name"]))
        if section.get("empty"):
            out.append(("muted", section.get("empty_text") or "Not discussed."))
            out.append(("blank", ""))
            continue
        for point in section["points"]:
            wrapped = textwrap.wrap(point["text"], WRAP_AT - 2) or [""]
            out.append(("bullet", "• " + wrapped[0]))
            for cont in wrapped[1:]:
                out.append(("body", "  " + cont))
            out.append(("cite", f"    — {point.get('title')}, {point.get('date')}"))
        out.append(("blank", ""))

    out.append(("h2", "Sources"))
    for t in transcripts:
        out.append(("body",
                    f"  {t.get('transcript_date')}  "
                    f"{t.get('title') or t.get('original_name') or 'Untitled'}"))
    out.append(("blank", ""))
    out.append(("muted",
                "Every statement above is drawn from the meetings listed. "
                "Figures mentioned are as discussed on those calls and are not "
                "accounting records."))
    return out


def build_pdf(path, update: dict[str, Any], sections: list[dict[str, Any]],
              transcripts: list[dict[str, Any]]) -> Path:
    path = Path(path)
    label = update.get("property_label") or "Property"
    period = f"{update.get('period_start')} to {update.get('period_end')}"
    generated = (update.get("generated_at") or "")[:10]

    lines = _lines_for(update, sections, transcripts)
    pages = [lines[i:i + LINES_PER_PAGE]
             for i in range(0, len(lines), LINES_PER_PAGE)] or [[]]

    with PdfPages(str(path)) as pdf:
        for page_no, page in enumerate(pages, start=1):
            fig = plt.figure(figsize=PAGE_SIZE)
            fig.text(0.06, 0.955, "Investor Update", fontsize=16,
                     fontweight="bold", color=INK)
            fig.text(0.06, 0.930, label, fontsize=11, color="#4b5563")
            fig.text(0.94, 0.955, period, ha="right", fontsize=9.5, color=MUTED)
            fig.text(0.94, 0.930, f"Generated {generated}", ha="right",
                     fontsize=8.5, color=MUTED)

            y = TOP
            for style, text in page:
                if style == "blank":
                    y -= LINE_STEP
                    continue
                if style == "h2":
                    y -= LINE_STEP * 0.6
                    fig.text(0.06, y, text, fontsize=11.5, fontweight="bold",
                             color=INK)
                elif style == "cite":
                    fig.text(0.06, y, text, fontsize=7.8, color=MUTED,
                             style="italic")
                elif style == "muted":
                    fig.text(0.06, y, text, fontsize=8.5, color=MUTED)
                else:
                    fig.text(0.06, y, text, fontsize=9.5, color=BODY)
                y -= LINE_STEP

            fig.text(0.94, 0.04, f"Page {page_no} of {len(pages)}",
                     ha="right", fontsize=8, color=MUTED)
            pdf.savefig(fig)
            plt.close(fig)
    return path


def build_xlsx(path, update: dict[str, Any], sections: list[dict[str, Any]],
               transcripts: list[dict[str, Any]]) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    path = Path(path)
    wb = Workbook()
    ws = wb.active
    ws.title = "Investor Update"

    bold = Font(bold=True)
    wrap = Alignment(wrap_text=True, vertical="top")

    ws.append(["Investor Update"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([update.get("property_label") or ""])
    ws.append([f"{update.get('period_start')} to {update.get('period_end')}"])
    ws.append([f"Generated {(update.get('generated_at') or '')[:19]}"])
    ws.append([])

    ws.append(["Section", "Point", "Source meeting", "Date"])
    for cell in ws[ws.max_row]:
        cell.font = bold

    for section in sections:
        if section.get("empty"):
            ws.append([section["name"],
                       section.get("empty_text") or "Not discussed.", "", ""])
            continue
        for point in section["points"]:
            ws.append([section["name"], point["text"],
                       point.get("title"), point.get("date")])

    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 90
    ws.column_dimensions["C"].width = 34
    ws.column_dimensions["D"].width = 12
    for row in ws.iter_rows(min_row=6, max_col=4):
        row[1].alignment = wrap

    src = wb.create_sheet("Sources")
    src.append(["Date", "Meeting", "Source", "File"])
    for cell in src[1]:
        cell.font = bold
    for t in transcripts:
        src.append([t.get("transcript_date"),
                    t.get("title") or "Untitled",
                    t.get("source"), t.get("original_name")])
    for col, width in zip("ABCD", (12, 40, 14, 40)):
        src.column_dimensions[col].width = width

    note = wb.create_sheet("Note")
    note["A1"] = ("Figures mentioned here are as discussed on the meetings "
                  "listed. They are narrative, not accounting records, and "
                  "nothing here has been written into Underwriting, Deal Dive "
                  "or any other tool.")
    note["A1"].alignment = wrap
    note.column_dimensions["A"].width = 100

    wb.save(str(path))
    return path


def suggested_filename(update: dict[str, Any], ext: str) -> str:
    label = "".join(c if c.isalnum() or c in " -_" else ""
                    for c in (update.get("property_label") or "property"))
    label = "-".join(label.split()).lower()[:48] or "property"
    return f"investor-update-{label}-{update.get('period_start')}.{ext}"
