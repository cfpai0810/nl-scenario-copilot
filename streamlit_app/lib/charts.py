# =============================================================================
# lib/charts.py - chart builders for the two result surfaces
# =============================================================================
# One builder per chart, each returning a matplotlib Figure. Two sinks:
#   Web:  st.pyplot(fig); plt.close(fig)
#   PDF:  chart_png_bytes(fig) -> embed as reportlab Image
#
# Chart 1 (single what-if): base vs scenario EBIT across Q1-Q4, with the
#   actuals/forecast boundary marked.
# Chart 2 (three-case): pessimistic / realistic / optimistic EBIT by month,
#   forming a widening fan.
# =============================================================================

import matplotlib
matplotlib.use("Agg")

from matplotlib import pyplot as plt
import matplotlib.ticker as mticker
from io import BytesIO

from config import EBIT_LABEL, CURRENCY_SYMBOL

DARK_BLUE  = "#1A3A5C"
MID_BLUE   = "#2D6A9F"
MUTED      = "#898781"
GREEN      = "#1D6B0F"
FLAG_RED   = "#A32D2D"
LIGHT_BLUE = "#EAF2FB"
RULE       = "#D3D1C7"

_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _thousands(x, _pos):
    return "{:,.0f}".format(x)


def extract_quarterly_ebit(quarterly):
    """Pull the EBIT line from the full quarterly structure and return a
    chart-ready dict with only the four quarter columns (no FY)."""
    ebit = next(l for l in quarterly["lines"] if l["line"] == EBIT_LABEL)
    cols = [c for c in quarterly["columns"] if c["kind"] != "fy"]
    return {
        "labels": [c["key"] for c in cols],
        "kinds":  [c["kind"] for c in cols],
        "base":     [ebit["base"][c["key"]] for c in cols],
        "scenario": [ebit["scenario"][c["key"]] for c in cols],
    }


def build_quarterly_chart(quarterly_ebit):
    """Base-vs-scenario EBIT across quarters, actuals/forecast boundary marked.

    quarterly_ebit: {labels, kinds, base, scenario} as from
    extract_quarterly_ebit or stored in the example.

    Returns a matplotlib Figure (caller must close it).
    """
    labels   = quarterly_ebit["labels"]
    kinds    = quarterly_ebit["kinds"]
    base     = quarterly_ebit["base"]
    scenario = quarterly_ebit["scenario"]
    short    = [l.split()[0] for l in labels]

    fig, ax = plt.subplots(figsize=(6.5, 3.0), dpi=150)
    xs = list(range(len(labels)))

    ax.plot(xs, base, color=MUTED, linestyle="--", linewidth=1.5,
            marker="o", markersize=5, label="Base", zorder=3)
    ax.plot(xs, scenario, color=MID_BLUE, linestyle="-", linewidth=2,
            marker="o", markersize=5, label="Scenario", zorder=3)

    boundary = None
    for i in range(len(kinds) - 1):
        if kinds[i] == "actual" and kinds[i + 1] == "forecast":
            boundary = i + 0.5
            break

    if boundary is not None:
        ax.axvspan(-0.5, boundary, color=LIGHT_BLUE, alpha=0.35, zorder=0)
        ax.axvline(boundary, color=RULE, linestyle="--", linewidth=1,
                   zorder=1)
        mid_a = (boundary - 0.5) / 2
        mid_f = (boundary + len(labels) - 0.5) / 2
        ax.text(mid_a, 0.97, "Actuals", transform=ax.get_xaxis_transform(),
                ha="center", va="top", fontsize=7.5, color=MUTED)
        ax.text(mid_f, 0.97, "Forecast", transform=ax.get_xaxis_transform(),
                ha="center", va="top", fontsize=7.5, color=MUTED)

    ax.set_xticks(xs)
    ax.set_xticklabels(short, fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_thousands))
    ax.tick_params(axis="y", labelsize=8)
    ax.set_ylabel("EBIT ({})".format(CURRENCY_SYMBOL), fontsize=9, color=DARK_BLUE)
    ax.legend(fontsize=8, loc="upper left", framealpha=0.9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color=RULE, linewidth=0.5, alpha=0.5)

    fig.tight_layout()
    return fig


def build_three_case_chart(case_monthly):
    """Three-case EBIT fan by month (pessimistic / realistic / optimistic).

    case_monthly: {periods, pessimistic, realistic, optimistic} where each
    case is a list of EBIT values aligned with periods.

    Returns a matplotlib Figure (caller must close it).
    """
    periods = case_monthly["periods"]
    pess    = case_monthly["pessimistic"]
    real    = case_monthly["realistic"]
    opti    = case_monthly["optimistic"]
    month_labels = [_MONTH_ABBR[int(p[5:7]) - 1] for p in periods]

    fig, ax = plt.subplots(figsize=(6.5, 3.0), dpi=150)
    xs = list(range(len(periods)))

    ax.fill_between(xs, pess, opti, color=LIGHT_BLUE, alpha=0.4, zorder=0)

    ax.plot(xs, pess, color=FLAG_RED, linewidth=1.5, marker="o",
            markersize=4, label="Pessimistic", zorder=3)
    ax.plot(xs, real, color=DARK_BLUE, linewidth=2, marker="o",
            markersize=5, label="Realistic (base)", zorder=3)
    ax.plot(xs, opti, color=GREEN, linewidth=1.5, marker="o",
            markersize=4, label="Optimistic", zorder=3)

    ax.set_xticks(xs)
    ax.set_xticklabels(month_labels, fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_thousands))
    ax.tick_params(axis="y", labelsize=8)
    ax.set_ylabel("EBIT ({})".format(CURRENCY_SYMBOL), fontsize=9, color=DARK_BLUE)
    ax.legend(fontsize=8, loc="upper left", framealpha=0.9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color=RULE, linewidth=0.5, alpha=0.5)

    fig.tight_layout()
    return fig


def chart_png_bytes(fig):
    """Render a figure to PNG bytes for embedding in a PDF."""
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf
