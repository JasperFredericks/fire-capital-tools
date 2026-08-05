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

BAND_COLOURS = {
    "Low": "#059669",
    "Moderate": "#2563eb",
    "Elevated": "#f59e0b",
    "High": "#b91c1c",
    cl.NOT_ASSESSED: "#9ca3af",
}


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


def _fmt_score(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def build_report(path, assessment: dict[str, Any], items: dict[str, dict[str, Any]],
                 scores: dict[str, Any], photos: list[dict[str, Any]],
                 photo_dir: Path | None = None, logo_path: Path | None = None) -> Path:
    """Write the PDF to `path` and return it.

    `scores` is the output of site_dd_checklist.score_assessment(); it is
    passed in rather than recomputed so the report can never disagree with
    what the screen showed.
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

        band = scores["risk_band"]
        fig.text(0.06, 0.76, "Assessment Summary", fontsize=15, fontweight="bold", color=INK)

        tiles = [
            ("Overall Score", _fmt_score(scores["overall"]) + " / 5", BODY),
            ("Risk Band", band, BAND_COLOURS.get(band, BODY)),
            ("Critical Findings", str(scores["critical_count"]),
             CRITICAL if scores["critical_count"] else BODY),
            ("Completion", f"{scores['completion_pct']:.0f}%", BODY),
        ]
        for idx, (lbl, val, colour) in enumerate(tiles):
            x = 0.06 + idx * 0.225
            fig.text(x, 0.69, lbl, fontsize=9, color=MUTED, fontweight="bold")
            fig.text(x, 0.645, val, fontsize=17, color=colour, fontweight="bold")

        fig.text(0.06, 0.60, f"{scores['scored_count']} of {scores['total_items']} items scored"
                             f" · {scores['na_count']} marked N/A"
                             f" · checklist v{assessment.get('checklist_version', '?')}",
                 fontsize=8.5, color=MUTED)

        # Per-category bar chart
        cats = scores["categories"]
        ax = fig.add_axes([0.10, 0.14, 0.82, 0.40])
        names = [c["name"] for c in cats]
        vals = [c["score"] if c["score"] is not None else 0 for c in cats]
        colours = ["#d1d5db" if c["score"] is None else
                   ("#b91c1c" if c["score"] < 2.5 else
                    "#f59e0b" if c["score"] < 3.5 else
                    "#2563eb" if c["score"] < 4.5 else "#059669") for c in cats]
        bars = ax.barh(names, vals, color=colours, alpha=0.85)
        ax.set_xlim(0, 5)
        ax.invert_yaxis()
        ax.set_xlabel("Category score (1–5)")
        ax.grid(axis="x", alpha=0.18)
        for bar, c in zip(bars, cats):
            txt = "not scored" if c["score"] is None else f"{c['score']:.2f}"
            ax.text(min(bar.get_width() + 0.08, 4.9), bar.get_y() + bar.get_height() / 2,
                    txt, va="center", fontsize=8.5, color=MUTED)
        ax.set_title("Category Scores", loc="left", fontweight="bold")

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
                summary = by_key[cat["key"]]
                fig.text(0.06, y, cat["name"], fontsize=12.5, fontweight="bold", color=INK)
                fig.text(0.94, y, f"{_fmt_score(summary['score'])} / 5"
                                  f"   ({summary['scored_count']}/{summary['item_count']} scored)",
                         ha="right", fontsize=9.5, color=MUTED)
                y -= 0.035
                for item_key, item_label in cat["items"]:
                    row = items.get(item_key) or {}
                    score = row.get("score")
                    if score is None:
                        shown, colour = "N/A", MUTED
                    else:
                        shown = str(score)
                        colour = CRITICAL if score <= cl.CRITICAL_THRESHOLD else BODY
                    fig.text(0.075, y, item_label, fontsize=9, color=BODY)
                    fig.text(0.44, y, shown, fontsize=9, fontweight="bold", color=colour)
                    note = truncate_note(row.get("note"))
                    if note:
                        fig.text(0.49, y, note, fontsize=8, color=MUTED)
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
