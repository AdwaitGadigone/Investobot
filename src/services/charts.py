import io

import matplotlib

# No GUI window, this only ever saves straight to an image file.
matplotlib.use("Agg")

import matplotlib.dates as mdates
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

# Colors from a colorblind-checked palette, not picked by eye.
COLOR_GOOD = "#0ca30c"
COLOR_CRITICAL = "#d03b3b"
COLOR_GRID = "#383835"
COLOR_TEXT = "#c3c2b7"

# Maps each /stock range choice to the period/interval pair yfinance wants, plus a label.
# This mirrors the same 1D/1W/1M/3M/6M/YTD/1Y/5Y tabs most stock apps show.
RANGE_OPTIONS = {
    "1d": {"period": "1d", "interval": "5m", "label": "1 Day"},
    "1w": {"period": "5d", "interval": "15m", "label": "1 Week"},
    "1mo": {"period": "1mo", "interval": "1d", "label": "1 Month"},
    "3mo": {"period": "3mo", "interval": "1d", "label": "3 Months"},
    "6mo": {"period": "6mo", "interval": "1d", "label": "6 Months"},
    "ytd": {"period": "ytd", "interval": "1d", "label": "Year to Date"},
    "1y": {"period": "1y", "interval": "1d", "label": "1 Year"},
    "5y": {"period": "5y", "interval": "1wk", "label": "5 Years"},
}


def build_price_chart(ticker: str, history, range_key: str) -> io.BytesIO:
    closes = history["Close"]
    is_up = closes.iloc[-1] >= closes.iloc[0]
    line_color = COLOR_GOOD if is_up else COLOR_CRITICAL

    # Built directly instead of pyplot, whose shared "current figure" breaks under concurrent requests.
    fig = Figure(figsize=(7.5, 4), dpi=160)

    # Transparent so the PNG blends into Discord's embed background instead of showing a white box.
    fig.patch.set_alpha(0.0)

    gs = fig.add_gridspec(nrows=2, ncols=1, height_ratios=[3, 1], hspace=0.06)
    ax_price = fig.add_subplot(gs[0])
    ax_vol = fig.add_subplot(gs[1], sharex=ax_price)

    x = history.index

    ax_price.plot(x, closes, color=line_color, linewidth=2, solid_capstyle="round")

    # Soft area fill under the line, like most stock apps use.
    ax_price.fill_between(x, closes, closes.min(), color=line_color, alpha=0.12)

    ax_price.set_facecolor("none")
    ax_price.set_ylabel("")
    ax_price.tick_params(axis="y", colors=COLOR_TEXT, labelsize=8)
    ax_price.tick_params(axis="x", labelbottom=False, bottom=False)
    ax_price.yaxis.set_major_locator(MaxNLocator(nbins=5))
    ax_price.yaxis.set_major_formatter(lambda val, pos: f"${val:,.0f}")

    # A few hint lines only, not a full grid.
    ax_price.grid(axis="y", color=COLOR_GRID, linewidth=0.6, alpha=0.5)

    for spine in ax_price.spines.values():
        spine.set_visible(False)

    if "Volume" in history.columns:
        volume = history["Volume"]

        # Green if that day closed higher than the day before, red if lower.
        day_colors = [
            COLOR_GOOD if c >= o else COLOR_CRITICAL
            for o, c in zip(closes.shift(1).fillna(closes.iloc[0]), closes)
        ]
        ax_vol.bar(x, volume, color=day_colors, alpha=0.45, width=(x[-1] - x[0]) / max(len(x), 1) * 0.8)

    ax_vol.set_facecolor("none")
    ax_vol.tick_params(axis="y", colors=COLOR_TEXT, labelsize=7)
    ax_vol.tick_params(axis="x", colors=COLOR_TEXT, labelsize=8)
    ax_vol.yaxis.set_major_locator(MaxNLocator(nbins=3))
    ax_vol.yaxis.set_major_formatter(lambda val, pos: _fmt_volume(val))

    for spine in ax_vol.spines.values():
        spine.set_visible(False)
    ax_vol.grid(False)

    # Caps ticks at 6 so date labels stop overlapping each other.
    locator = mdates.AutoDateLocator(minticks=4, maxticks=6)
    ax_vol.xaxis.set_major_locator(locator)
    ax_vol.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    fig.autofmt_xdate(rotation=0, ha="center")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=True, bbox_inches="tight", pad_inches=0.15)
    buf.seek(0)
    return buf


def _fmt_volume(val: float) -> str:
    if val >= 1_000_000:
        return f"{val / 1_000_000:.0f}M"
    if val >= 1_000:
        return f"{val / 1_000:.0f}K"
    return f"{val:.0f}"
