import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from openpyxl.drawing.image import Image as XLImage
from datetime import datetime
import io
import matplotlib.ticker as mticker
import numpy as np

from tools.mmr_report.helpers import fmt_date
from tools.mmr_report.styles import C, PALE_BLUE, _box, merge_band




_NAVY      = "#1A2744"


_BLUE      = "#4A90D9"


_FIG_W     = 7.0    # inches


_FIG_H     = 2.8    # inches — both charts identical height


_FIG_DPI   = 150




def _fig_to_xl_image(fig):
    """Render a matplotlib figure to a BytesIO PNG and return an XLImage.

    openpyxl sizes embedded images using the PNG's pixel dimensions assuming
    96 DPI. We generate at 150 DPI for crispness, so we must override width
    and height to match the intended display size (7" wide, proportional height).
    """
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=_FIG_DPI, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    img = XLImage(buf)
    # Fix display size: width = _FIG_W inches at Excel's 96 DPI, height proportional
    display_w = int(_FIG_W * 96)
    display_h = int(img.height * display_w / img.width)
    img.width  = display_w
    img.height = display_h
    return img




def add_projected_occupancy_chart(ws, entries):
    """Matplotlib line chart: % occupancy over up to 20 weeks, embedded as PNG."""
    if not entries:
        merge_band(ws, 7, 9, 16, "No projected occupancy data", font="data", fill_color=PALE_BLUE, align=C, border=_box())
        return

    rows = entries[:20]
    labels = [fmt_date(e.get("date")) for e in rows]
    values = [float(e.get("pct") or 0) for e in rows]

    fig, ax = plt.subplots(figsize=(_FIG_W, _FIG_H))
    ax.plot(range(len(labels)), values, color=_NAVY, linewidth=2)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right", fontsize=7)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0%}"))
    y_min = max(0.0, float(np.floor((min(values) - 0.02) * 100) / 100))
    y_max = min(1.0, float(np.ceil((max(values) + 0.01) * 100) / 100))
    if y_max <= y_min:
        y_max = min(1.0, y_min + 0.05)
        if y_max <= y_min:
            y_min = max(0.0, y_max - 0.05)
    ax.set_ylim(y_min, y_max)
    ax.yaxis.grid(True, alpha=0.3)
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    fig.patch.set_facecolor("white")
    plt.tight_layout()
    fig.canvas.draw()   # force label render before saving to buffer

    img = _fig_to_xl_image(fig)
    ws.add_image(img, "I7")




def add_expiring_leases_chart(ws, months):
    """Matplotlib grouped bar chart: expirations vs renewals per month, as PNG."""
    if months is None:
        merge_band(ws, 26, 9, 16, "N/A", font="data", fill_color=PALE_BLUE, align=C, border=_box())
        return
    if not months:
        merge_band(ws, 26, 9, 16, "No expiring lease data", font="data", fill_color=PALE_BLUE, align=C, border=_box())
        return

    rows = months[:10]
    labels      = []
    expirations = []
    renewals    = []
    for m in rows:
        dt = m.get("dt")
        labels.append(dt.strftime("%b %Y") if isinstance(dt, datetime) else str(dt or ""))
        expirations.append(int(m.get("expirations") or 0))
        renewals.append(int(m.get("renewals") or 0))

    x     = np.arange(len(labels))
    width = 0.35

    fig, ax = plt.subplots(figsize=(_FIG_W, _FIG_H))
    ax.bar(x - width / 2, expirations, width, label="Expirations", color=_NAVY)
    ax.bar(x + width / 2, renewals,    width, label="Renewals",    color=_BLUE)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))

    ax.yaxis.grid(True, alpha=0.3)
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_xlabel("")
    ax.set_ylabel("")
    plt.legend(loc="upper right", fontsize=8, framealpha=0.7)
    fig.patch.set_facecolor("white")
    fig.tight_layout()

    img = _fig_to_xl_image(fig)
    ws.add_image(img, "I26")
