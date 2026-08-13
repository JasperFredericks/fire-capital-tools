"""
FIRE Capital Tools - Site DD PDF report.

Reuses Scorecard Pro's export mechanism -- matplotlib PdfPages, one figure
per page, 11x8.5 landscape, logo top-left, title block top-right -- rather
than introducing a second PDF stack. reportlab is not a dependency of this
project and this deliberately does not add one.

Two deviations from calling scorecard_pro.exports helpers literally:

  * add_pdf_header() hard-codes the string "Property Scorecard Report" and
    takes a scorecard-shaped pnl_data dict, so it cannot title a Site DD
    report correctly.
  * It resolves the logo through flask.current_app, and this module is
    required to have zero Flask imports so the report can be generated and
    inspected in a test with no application context.

So the header is reimplemented here at the same coordinates and colours --
visually identical output, logo path passed in by the caller.

Layout is fixed because the checklist is fixed: 6 categories, 2 per page,
means pagination is deterministic rather than something that has to be
computed. That is the direct payoff of keeping the v1 checklist
non-editable.

Known limitation, surfaced in the UI rather than hidden: matplotlib places
text at absolute coordinates with no reflow, so long notes are truncated
with a visible ellipsis instead of wrapping onto extra pages.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

from tools import site_dd_checklist as cl
from tools import site_dd_conditions as cond

PAGE_SIZE = (11, 8.5)
INK = "#1a2744"
MUTED = "#6b7280"
BODY = "#111827"
CRITICAL = "#b91c1c"

# Characters kept before the ellipsis. Deliberately conservative: the note
# column is roughly half the page width at 8pt.
NOTE_TRUNCATE_AT = 110
CATEGORIES_PER_PAGE = 2
MAX_THUMBNAILS = 12


def truncate_note(note: str | None, limit: int = NOTE_TRUNCATE_AT) -> str:
    """Trim to `limit` with a visible ellipsis. Visible on purpose -- a
    silently cut sentence reads as if that is all the inspector wrote."""
    if not note:
        return ""
    note = " ".join(str(note).split())
    if len(note) <= limit:
        return note
    return note[: limit - 1].rstrip() + "…"


def _header(fig, title: str, subtitle: str, meta: str, logo_path: Path | None) -> None:
    """Same geometry as scorecard_pro.exports.add_pdf_header -- see the
    module docstring for why it is reimplemented rather than imported."""
    if logo_path and Path(logo_path).exists():
        try:
            ax = fig.add_axes([0.06, 0.86, 0.24, 0.08])
            ax.imshow(mpimg.imread(str(logo_path)))
            ax.axis("off")
        except Exception:
            pass  # a missing/unreadable logo must never fail the report
    fig.text(0.94, 0.91, title, ha="right", fontsize=14, fontweight="bold", color=INK)
    fig.text(0.94, 0.875, subtitle, ha="right", fontsize=10, color="#4b5563")
    fig.text(0.94, 0.845, meta, ha="right", fontsize=9, color=MUTED)


def build_report(path, assessment: dict[str, Any], items: dict[str, dict[str, Any]],
                 summary: dict[str, Any], photos: list[dict[str, Any]],
                 photo_dir: Path | None = None, logo_path: Path | None = None) -> Path:
    """Write the PDF to `path` and return it.

    `summary` is the output of site_dd_conditions.summarize(); it is passed
    in rather than recomputed so the report can never disagree with what
    the screen showed.
    """
    path = Path(path)
    label = assessment.get("property_label") or "Untitled Property"
    assessed_on = assessment.get("assessed_on") or "—"
    inspector = assessment.get("inspector") or "—"
    subtitle = label
    meta = f"Inspected {assessed_on} · {inspector}"

    with PdfPages(str(path)) as pdf:
        # ── Page 1: cover / summary ──────────────────────────────────────
        fig = plt.figure(figsize=PAGE_SIZE)
        _header(fig, "Site Due Diligence Report", subtitle, meta, logo_path)

        fig.text(0.06, 0.76, "Assessment Summary", fontsize=15, fontweight="bold", color=INK)

        # Counts, not a mean -- see site_dd_conditions' docstring. The
        # report shows exactly what the screen shows, including the
        # deliberate absence of an overall score.
        tiles = [
            ("Needs Work", str(summary["work_count"]),
             CRITICAL if summary["work_count"] else BODY),
            ("To Replace", str(summary["replace_count"]),
             CRITICAL if summary["replace_count"] else BODY),
            ("To Repair", str(summary["repair_count"]), BODY),
            ("Completion", f"{summary['completion_pct']:.0f}%", BODY),
        ]
        for idx, (lbl, val, colour) in enumerate(tiles):
            x = 0.06 + idx * 0.225
            fig.text(x, 0.69, lbl, fontsize=9, color=MUTED, fontweight="bold")
            fig.text(x, 0.645, val, fontsize=17, color=colour, fontweight="bold")

        fig.text(0.06, 0.60, summary["headline"]
                             + f" · checklist v{assessment.get('checklist_version', '?')}",
                 fontsize=8.5, color=MUTED)

        # Per-category stacked counts. A stacked bar of states is the
        # honest chart for an ordinal scale: it shows the distribution
        # rather than collapsing it into a position on an invented axis.
        cats = summary["categories"]
        ax = fig.add_axes([0.10, 0.14, 0.82, 0.40])
        names = [c["name"] for c in cats]
        left = [0] * len(cats)
        for state in cond.CONDITIONS:
            widths = [c["counts"][state] for c in cats]
            if not any(widths):
                continue
            ax.barh(names, widths, left=left, height=0.62,
                    color=cond.CONDITION_COLOURS[state], alpha=0.88,
                    label=cond.CONDITION_LABELS[state])
            left = [a + b for a, b in zip(left, widths)]
        ax.invert_yaxis()
        ax.set_xlabel("Items by condition")
        ax.grid(axis="x", alpha=0.18)
        ax.legend(loc="lower right", fontsize=7.5, frameon=False, ncol=5)
        for i, c in enumerate(cats):
            if not c["assessed_count"]:
                ax.text(0.1, i, "not assessed", va="center", fontsize=8, color=MUTED)
        ax.set_title("Condition by Category", loc="left", fontweight="bold")

        if assessment.get("overall_notes"):
            fig.text(0.06, 0.075, "Overall notes: " + truncate_note(assessment["overall_notes"], 200),
                     fontsize=8.5, color=BODY, wrap=True)

        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # ── Pages 2-4: checklist detail, 2 categories per page ───────────
        groups = [cl.CATEGORIES[i:i + CATEGORIES_PER_PAGE]
                  for i in range(0, len(cl.CATEGORIES), CATEGORIES_PER_PAGE)]
        by_key = {c["key"]: c for c in cats}

        for page_no, group in enumerate(groups, start=1):
            fig = plt.figure(figsize=PAGE_SIZE)
            _header(fig, "Site Due Diligence Report", subtitle,
                    f"Checklist detail {page_no} of {len(groups)}", logo_path)
            y = 0.78
            for cat in group:
                cat_summary = by_key[cat["key"]]
                fig.text(0.06, y, cat["name"], fontsize=12.5, fontweight="bold", color=INK)
                fig.text(0.94, y, f"{cat_summary['work_count']} need work"
                                  f"   ({cat_summary['assessed_count']}/{cat_summary['item_count']} assessed)",
                         ha="right", fontsize=9.5, color=MUTED)
                y -= 0.035
                for item_key, item_label in cat["items"]:
                    row = items.get(item_key) or {}
                    value = row.get("condition")
                    if not cond.is_valid(value):
                        shown, colour = "Not assessed", MUTED
                    else:
                        shown = cond.CONDITION_LABELS[value]
                        colour = (cond.CONDITION_COLOURS[value]
                                  if cond.needs_work(value) else BODY)
                    fig.text(0.075, y, item_label, fontsize=9, color=BODY)
                    fig.text(0.42, y, shown, fontsize=9, fontweight="bold", color=colour)
                    note = truncate_note(row.get("note"))
                    if note:
                        fig.text(0.56, y, note, fontsize=8, color=MUTED)
                    y -= 0.026
                y -= 0.025
            pdf.savefig(fig, bbox_inches="tight")
            plt.close(fig)

        # ── Photo contact sheet ──────────────────────────────────────────
        if photos and photo_dir:
            shown = photos[:MAX_THUMBNAILS]
            fig = plt.figure(figsize=PAGE_SIZE)
            _header(fig, "Site Due Diligence Report", subtitle,
                    f"Photos (showing {len(shown)} of {len(photos)})", logo_path)
            cols, rows_n = 4, 3
            for idx, ph in enumerate(shown):
                r, c = divmod(idx, cols)
                ax = fig.add_axes([0.06 + c * 0.225, 0.55 - r * 0.24, 0.20, 0.20])
                ax.axis("off")
                fpath = Path(photo_dir) / ph["stored_name"]
                try:
                    ax.imshow(mpimg.imread(str(fpath)))
                except Exception:
                    # An unreadable or non-raster file must not abort the
                    # report -- show a placeholder and carry on.
                    ax.text(0.5, 0.5, "(preview\nunavailable)", ha="center", va="center",
                            fontsize=8, color=MUTED, transform=ax.transAxes)
                caption = ph.get("caption") or cl.ITEM_LABELS.get(ph.get("item_key") or "", "General")
                ax.set_title(truncate_note(caption, 34), fontsize=7.5, color=MUTED, loc="left")
            if len(photos) > MAX_THUMBNAILS:
                fig.text(0.06, 0.08,
                         f"{len(photos) - MAX_THUMBNAILS} further photo(s) not shown — "
                         f"all files remain downloadable from the assessment page.",
                         fontsize=8.5, color=MUTED)
            pdf.savefig(fig, bbox_inches="tight")
            plt.close(fig)

    return path


def report_filename(assessment: dict[str, Any]) -> str:
    """SiteDD_<label>_<date>.pdf, filesystem-safe."""
    label = (assessment.get("property_label") or "Property")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label).strip("_") or "Property"
    date = (assessment.get("assessed_on") or assessment.get("created_at") or "")[:10] or "undated"
    return f"SiteDD_{safe}_{date}.pdf"
