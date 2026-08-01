import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from services import charts, market_data


def _fmt_num(n: float | None) -> str:
    # Turns something like 4897205003506 into 4.90B instead of a wall of digits.
    if n is None:
        return "N/A"
    if abs(n) >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.2f}K"
    return f"{n:,.0f}"


def _range_bar(current: float, low: float | None, high: float | None, width: int = 14) -> str:
    # Shows where the price sits in the 52wk range, e.g. $150.00 [████████░░░░] $210.00
    if low is None or high is None or high <= low:
        return "N/A"

    pct = max(0.0, min(1.0, (current - low) / (high - low)))
    filled = round(pct * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"${low:,.2f}  {bar}  ${high:,.2f}"


def _ansi_change_line(quote: dict, is_up: bool) -> str:
    # Discord embeds have no normal way to color text, but a fenced code block tagged
    # "ansi" renders real ANSI colors on desktop/web (mobile just shows it as plain
    # monospace text instead, still readable, just not colored there).
    esc = chr(27)  # the actual ANSI escape control character
    arrow = "▲" if is_up else "▼"
    color_code = "32" if is_up else "31"  # 32 = green, 31 = red
    reset = f"{esc}[0m"
    colored = f"{esc}[1;{color_code}m{arrow} {quote['change']:+.2f} ({quote['change_pct']:+.2f}%){reset}"
    return f"```ansi\n{colored} today, prev close ${quote['prev_close']:,.2f}\n```"


def build_quote_embed(quote: dict) -> discord.Embed:
    # Also reused by cogs/scheduler.py, so the automatic alerts look the same as /stock.
    is_up = quote["change"] >= 0
    color = discord.Color.green() if is_up else discord.Color.red()

    embed = discord.Embed(
        title=f"{quote['name']} ({quote['ticker']})",
        description=f"### ${quote['price']:,.2f}\n{_ansi_change_line(quote, is_up)}",
        color=color,
    )

    embed.add_field(
        name="Day Range",
        value=f"${quote['day_low']:,.2f} - ${quote['day_high']:,.2f}"
        if quote["day_low"] and quote["day_high"]
        else "N/A",
        inline=True,
    )
    embed.add_field(name="Volume", value=_fmt_num(quote["volume"]), inline=True)
    embed.add_field(name="Market Cap", value=f"${_fmt_num(quote['market_cap'])}", inline=True)

    embed.add_field(
        name="52-Week Range",
        value=_range_bar(quote["price"], quote["year_low"], quote["year_high"]),
        inline=False,
    )

    embed.set_footer(text="Data: Yahoo Finance (delayed)")
    return embed


async def _build_stock_response(ticker: str, range_key: str) -> tuple[discord.Embed, discord.File]:
    # Shared by the initial /stock reply and by the range dropdown below, so both build the exact
    # same embed and chart for a given ticker and range, just triggered from two different places.
    range_opts = charts.RANGE_OPTIONS[range_key]

    quote, history, trends, target = await asyncio.gather(
        market_data.get_quote(ticker),
        market_data.get_price_history(ticker, range_opts["period"], range_opts["interval"]),
        market_data.get_recommendation_trends(ticker),
        market_data.get_price_target(ticker),
    )

    embed = build_quote_embed(quote)

    consensus = market_data.summarize_recommendation(trends)
    if consensus:
        emoji, label = consensus
        mean = target.get("targetMean") if target else None
        if not mean:
            # Alpha Vantage backup since Finnhub's target needs a paid plan.
            mean = await market_data.get_price_target_average(ticker)

        target_line = ""
        if mean:
            upside = (mean - quote["price"]) / quote["price"] * 100
            direction = "upside" if upside >= 0 else "downside"
            target_line = f"\nTarget **${mean:,.2f}** ({upside:+.1f}% {direction})"

        embed.add_field(
            name="Analyst Consensus",
            value=f"{emoji} **{label}**{target_line}\n*use /rating for the full breakdown*",
            inline=False,
        )

    chart_buf = await asyncio.to_thread(charts.build_price_chart, ticker, history, range_key)
    chart_file = discord.File(chart_buf, filename="chart.png")
    embed.set_image(url="attachment://chart.png")

    return embed, chart_file


class RangeSelect(discord.ui.Select):
    # The dropdown attached under a /stock reply, lets someone flip the chart's timeframe
    # after the fact instead of having to type /stock again with a different range option.

    def __init__(self, ticker: str, current_range: str):
        self.ticker = ticker
        options = [
            discord.SelectOption(label=opts["label"], value=key, default=(key == current_range))
            for key, opts in charts.RANGE_OPTIONS.items()
        ]
        super().__init__(placeholder="Change the chart range...", options=options)

    async def callback(self, interaction: discord.Interaction):
        range_key = self.values[0]

        # Marks the newly picked option as the one shown as selected next time the dropdown opens.
        for option in self.options:
            option.default = option.value == range_key

        # Refetching and rebuilding the chart takes a couple seconds, defer avoids a failed interaction.
        await interaction.response.defer()

        try:
            embed, chart_file = await _build_stock_response(self.ticker, range_key)
        except market_data.TickerNotFoundError:
            return

        await interaction.edit_original_response(embed=embed, attachments=[chart_file], view=self.view)


class StockView(discord.ui.View):
    def __init__(self, ticker: str, current_range: str):
        super().__init__(timeout=600)
        self.message: discord.Message | None = None
        self.add_item(RangeSelect(ticker, current_range))

    async def on_timeout(self):
        # After 10 minutes of no one touching it, disable the dropdown so it doesn't sit there uselessly.
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Stocks(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="stock", description="Get a full quote, chart, and analyst take for a ticker")
    @app_commands.describe(
        ticker="Stock ticker symbol, e.g. AAPL",
        range="How far back the chart should go (defaults to 1 month)",
    )
    @app_commands.choices(
        range=[
            app_commands.Choice(name=opts["label"], value=key)
            for key, opts in charts.RANGE_OPTIONS.items()
        ]
    )
    async def stock(
        self,
        interaction: discord.Interaction,
        ticker: str,
        range: app_commands.Choice[str] = None,
    ):
        # Fetching everything takes longer than Discord's 3-second reply window, this buys more time.
        await interaction.response.defer()

        ticker = ticker.upper().strip()
        range_key = range.value if range else "1mo"

        try:
            embed, chart_file = await _build_stock_response(ticker, range_key)
        except market_data.TickerNotFoundError:
            await interaction.followup.send(
                f"Couldn't find a ticker called `{ticker}`. Double-check the symbol."
            )
            return

        view = StockView(ticker, range_key)
        await interaction.followup.send(embed=embed, file=chart_file, view=view)
        view.message = await interaction.original_response()


async def setup(bot: commands.Bot):
    await bot.add_cog(Stocks(bot))
