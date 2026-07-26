from __future__ import annotations

import base64
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from tools.scorecard_pro.utils import money_axis, money_label


# Modal "click to enlarge" scale factor -- regenerates each chart at a
# genuinely larger figure size (more pixels at the same DPI), not a CSS
# stretch of the small dashboard image, so labels stay crisp rather than
# blurring out when displayed bigger.
LARGE_CHART_SCALE = 2.0


def build_charts(df, scale=1.0):
    if df.empty:
        return {}
    return {
        "trend": chart_trend(df, scale=scale),
        "waterfall": chart_waterfall(df, scale=scale),
        "occupancy": chart_occupancy(df, scale=scale),
        "expense_ratio": chart_expense_ratio(df, scale=scale),
    }


def chart_trend(df, scale=1.0):
    # Wider than the other three dashboard charts on purpose: this is the
    # only one carrying three series (Income/Expenses bars plus an NOI
    # line) with a label on every point across a full 12-month history --
    # the other charts' fixed size would pack labels too tightly. Height
    # is set to match the Waterfall chart's own aspect ratio (3.8/5.4)
    # at this width, since the two sit side by side in the same dashboard
    # grid row and a shorter aspect here left a blank gap under this
    # chart's card once the row stretched to match Waterfall's height.
    # Font/line/marker sizes below are scaled up from the previous
    # (11.5, 4.2) figure by the same ~1.9x factor as the height increase,
    # so the whole figure grows proportionally rather than just gaining
    # empty vertical space around unchanged-size elements.
    fig, ax = plt.subplots(figsize=(11.5 * scale, 8.1 * scale))
    x = list(range(len(df)))
    bar_width = 0.38
    # Grouped (side-by-side) bars rather than overlapping ones sharing the
    # same x position -- overlapping bars meant the Expenses bar was drawn
    # on top of the (also semi-transparent) Income bar, blending blue into
    # the orange and rendering it muddy/brownish instead of matching the
    # Waterfall chart's clean orange. Side by side, Expenses sits on its
    # own patch of white background with the exact same color and alpha
    # the Waterfall chart uses, so the rendered pixels match it exactly.
    income_bars = ax.bar([xi - bar_width / 2 for xi in x], df["Income"], width=bar_width, label="Income", color="#1e40af", alpha=0.70)
    expense_bars = ax.bar([xi + bar_width / 2 for xi in x], df["Expenses"], width=bar_width, label="Expenses", color="#f97316", alpha=0.85)
    noi_line = ax.plot(x, df["NOI"], label="NOI", color="#4cbb17", linewidth=5.0 * scale, marker="o", markersize=10 * scale)[0]
    ax.set_xticks(x, df["Month"], rotation=35, ha="right")
    ax.tick_params(labelsize=11 * scale)
    ax.yaxis.set_major_formatter(lambda val, _: money_axis(val))
    ax.grid(axis="y", alpha=0.18)

    # Data labels: dollar figure on each bar and each NOI point. Now that
    # Income/Expenses sit side by side rather than stacked at the same x,
    # their labels no longer compete for the same horizontal space either.
    # Months with no GPR data at all (Income/Expenses/NOI all genuinely
    # $0, not just small) are skipped entirely -- three stacked "$0"
    # labels add no information and were the one case dense enough to
    # actually overlap illegibly.
    # Label font stays close to the original size (only bumped slightly,
    # 7.5 -> 9) rather than scaling with the height increase -- width is
    # unchanged, and scaling text to match the height factor made
    # adjacent months' labels wide enough to collide horizontally (e.g.
    # Mar/Apr 2026's tall Income bars sit close together on the x-axis).
    has_data = df["OccupancyStatus"] != "missing_gpr"
    ax.bar_label(income_bars, labels=[money_label(v) if keep else "" for keep, v in zip(has_data, df["Income"])], padding=3 * scale, fontsize=9 * scale)
    ax.bar_label(expense_bars, labels=[money_label(v) if keep else "" for keep, v in zip(has_data, df["Expenses"])], padding=3 * scale, fontsize=9 * scale)
    for xi, yi, keep in zip(x, df["NOI"], has_data):
        if not keep:
            continue
        ax.annotate(
            money_label(yi), (xi, yi), textcoords="offset points", xytext=(0, 10 * scale),
            ha="center", fontsize=9 * scale, fontweight="bold", color="#2f6b0e",
            bbox=dict(facecolor="white", edgecolor="none", alpha=0.65, pad=1.2 * scale),
        )

    # Below the chart rather than overlapping the plotted bars/line -- a
    # tall Income month previously sat right under the upper-left legend.
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncols=3, frameon=False, fontsize=12 * scale)
    ax.set_title("Financial Performance Trend", loc="left", fontweight="bold", fontsize=14 * scale)
    fig.tight_layout()
    return fig_to_data_uri(fig)


def chart_waterfall(df, scale=1.0):
    total_inc = float(df["Income"].sum())
    total_exp = float(df["Expenses"].sum())
    total_noi = float(df["NOI"].sum())
    fig, ax = plt.subplots(figsize=(5.4 * scale, 3.8 * scale))
    labels = ["Income", "Expenses", "NOI"]
    # Floating bars: Income stands at full height, Expenses is drawn as a
    # positive-magnitude segment bridging down from Income to NOI (the
    # portion of Income it consumes), and NOI stands at its own full
    # height as the remaining value -- not a negative expense bar summed
    # against some other total.
    bottoms = [0, total_noi, 0]
    heights = [total_inc, total_exp, total_noi]
    colors = ["#1e40af", "#f97316", "#4cbb17" if total_noi >= 0 else "#dc2626"]
    bars = ax.bar(labels, heights, bottom=bottoms, color=colors, alpha=0.85)
    ax.bar_label(
        bars, labels=[money_label(v) for v in [total_inc, total_exp, total_noi]],
        label_type="center", fontsize=10 * scale, fontweight="bold", color="white",
    )
    ax.axhline(0, color="#1f2937", linewidth=0.8 * scale)
    ax.yaxis.set_major_formatter(lambda val, _: money_axis(val))
    ax.grid(axis="y", alpha=0.18)
    ax.tick_params(labelsize=10 * scale)
    ax.set_title("NOI Breakdown", loc="left", fontweight="bold", fontsize=13 * scale)
    fig.tight_layout()
    return fig_to_data_uri(fig)


def chart_occupancy(df, scale=1.0):
    fig, ax = plt.subplots(figsize=(6.4 * scale, 3.8 * scale))
    values = [None if pd.isna(value) else value * 100 for value in df["Occupancy"]]
    is_missing = [value is None for value in values]
    colors = ["#d1d5db" if missing else ("#dc2626" if value < 80 else "#f59e0b" if value < 90 else "#059669") for missing, value in zip(is_missing, values)]
    # A month with no GPR data at all gets a small fixed-height hatched
    # placeholder bar rather than the real value's height (there is no
    # real value) -- a plain 0-height bar renders no visible area
    # regardless of its fill color, which made "no data available" look
    # identical to "no bar drawn here" instead of standing out as its own
    # distinct case from a genuine 0% (fully vacant) month.
    placeholder_height = 5
    display_values = [placeholder_height if missing else value for missing, value in zip(is_missing, values)]
    bars = ax.bar(df["Month"], display_values, color=colors, alpha=0.78)
    for bar, missing in zip(bars, is_missing):
        if missing:
            bar.set_hatch("///")
            bar.set_edgecolor("#6b7280")
            bar.set_linewidth(0.8 * scale)
    labels = ["No Data" if missing else f"{value:.1f}%" for missing, value in zip(is_missing, values)]
    ax.bar_label(bars, labels=labels, padding=3 * scale, fontsize=7 * scale)
    ax.axhline(90, color="#1f2937", linestyle="--", linewidth=1 * scale)
    ax.set_ylim(0, 112)
    ax.set_ylabel("Occupancy %", fontsize=10 * scale)
    ax.tick_params(axis="x", rotation=35, labelsize=9 * scale)
    ax.tick_params(axis="y", labelsize=9 * scale)
    ax.grid(axis="y", alpha=0.18)
    ax.set_title("Occupancy Health", loc="left", fontweight="bold", fontsize=13 * scale)
    fig.tight_layout()
    return fig_to_data_uri(fig)


def chart_expense_ratio(df, scale=1.0):
    fig, ax = plt.subplots(figsize=(5.4 * scale, 3.8 * scale))
    ratios = [None if pd.isna(value) else value * 100 for value in df["ExpenseRatio"]]
    ax.plot(df["Month"], ratios, color="#7c3aed", linewidth=2.6 * scale, marker="o", markersize=6 * scale)
    for xi, yi in zip(df["Month"], ratios):
        if yi is None:
            continue
        ax.annotate(
            f"{yi:.1f}%", (xi, yi), textcoords="offset points", xytext=(0, 8 * scale),
            ha="center", fontsize=7 * scale, fontweight="bold", color="#5b21b6",
            bbox=dict(facecolor="white", edgecolor="none", alpha=0.65, pad=1 * scale),
        )
    ax.axhline(55, color="#f59e0b", linestyle="--", linewidth=1 * scale)
    ax.margins(y=0.15)
    ax.set_ylabel("Expense Ratio %", fontsize=10 * scale)
    ax.tick_params(axis="x", rotation=35, labelsize=9 * scale)
    ax.tick_params(axis="y", labelsize=9 * scale)
    ax.grid(axis="y", alpha=0.18)
    ax.set_title("Expense Ratio Analysis", loc="left", fontweight="bold", fontsize=13 * scale)
    fig.tight_layout()
    return fig_to_data_uri(fig)


_LARGE_CHART_BUILDERS = {
    "trend": chart_trend,
    "waterfall": chart_waterfall,
    "occupancy": chart_occupancy,
    "expense_ratio": chart_expense_ratio,
}


def fig_to_data_uri(fig):
    output = io.BytesIO()
    fig.savefig(output, format="png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
