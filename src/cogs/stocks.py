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


def build_quote_embed(quote: dict) -> discord.Embed:
    # Also reused by cogs/scheduler.py, so the automatic alerts look the same as /stock.
    is_up = quote["change"] >= 0
    color = discord.Color.green() if is_up else discord.Color.red()
    arrow = "▲" if is_up else "▼"

    embed = discord.Embed(
        title=f"{quote['name']} ({quote['ticker']})",
        description=(
            f"### ${quote['price']:,.2f}\n"
            f"{arrow} **{quote['change']:+.2f} ({quote['change_pct']:+.2f}%)** today, "
            f"prev close ${quote['prev_close']:,.2f}"
        ),
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
        range_opts = charts.RANGE_OPTIONS[range_key]

        try:
            # These 4 calls don't depend on each other, gathering them keeps the wait to the slowest one.
            quote, history, trends, target = await asyncio.gather(
                market_data.get_quote(ticker),
                market_data.get_price_history(ticker, range_opts["period"], range_opts["interval"]),
                market_data.get_recommendation_trends(ticker),
                market_data.get_price_target(ticker),
            )
        except market_data.TickerNotFoundError:
            await interaction.followup.send(
                f"Couldn't find a ticker called `{ticker}`. Double-check the symbol."
            )
            return

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

        await interaction.followup.send(embed=embed, file=chart_file)


async def setup(bot: commands.Bot):
    await bot.add_cog(Stocks(bot))
