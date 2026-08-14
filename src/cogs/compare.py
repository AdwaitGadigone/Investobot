import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from config import HEAVY_COOLDOWN_SECONDS
from services import market_data
from services.ticker_search import ticker_autocomplete


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


def _consensus_line(trends: dict | None) -> str:
    if not trends:
        return "No analyst data"
    buy = trends.get("strongBuy", 0) + trends.get("buy", 0)
    sell = trends.get("sell", 0) + trends.get("strongSell", 0)
    hold = trends.get("hold", 0)
    return f"{buy} Buy / {hold} Hold / {sell} Sell"


def _ticker_field(ticker: str, quote: dict, trends: dict | None) -> str:
    up = quote["change"] >= 0
    arrow = "▲" if up else "▼"
    lines = [
        f"**${quote['price']:,.2f}**",
        f"{arrow} {quote['change']:+.2f} ({quote['change_pct']:+.2f}%)",
        f"Mkt Cap: {_fmt_num(quote['market_cap'])}",
        _consensus_line(trends),
    ]
    return "\n".join(lines)


class Compare(commands.Cog):
    # /compare, side by side, closes a gap most other stock bots already cover.

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="compare", description="Compare two tickers side by side")
    @app_commands.checks.cooldown(1, HEAVY_COOLDOWN_SECONDS)
    @app_commands.describe(
        ticker1="First stock ticker symbol, e.g. AAPL",
        ticker2="Second stock ticker symbol, e.g. MSFT",
    )
    @app_commands.autocomplete(ticker1=ticker_autocomplete, ticker2=ticker_autocomplete)
    async def compare(self, interaction: discord.Interaction, ticker1: str, ticker2: str):
        await interaction.response.defer()
        ticker1 = ticker1.upper().strip()
        ticker2 = ticker2.upper().strip()

        if ticker1 == ticker2:
            await interaction.followup.send("Give two different tickers to compare.")
            return

        # Each ticker's own quote+trends fetched together, and both tickers fetched concurrently, so one
        # slow/bad ticker doesn't stack its wait time on top of the other's.
        results = await asyncio.gather(
            market_data.get_quote(ticker1),
            market_data.get_recommendation_trends(ticker1),
            market_data.get_quote(ticker2),
            market_data.get_recommendation_trends(ticker2),
            return_exceptions=True,
        )
        quote1, trends1, quote2, trends2 = results

        for ticker, quote in ((ticker1, quote1), (ticker2, quote2)):
            if isinstance(quote, market_data.TickerNotFoundError):
                await interaction.followup.send(f"`{ticker}` doesn't look like a valid ticker.")
                return
            if isinstance(quote, market_data.MarketDataTimeoutError):
                await interaction.followup.send("Yahoo Finance is being slow right now, try again in a bit.")
                return
            if isinstance(quote, Exception):
                raise quote

        trends1 = trends1 if not isinstance(trends1, Exception) else None
        trends2 = trends2 if not isinstance(trends2, Exception) else None

        embed = discord.Embed(
            title=f"{ticker1} vs {ticker2}",
            color=discord.Color.blurple(),
        )
        embed.add_field(name=f"{quote1['name']} ({ticker1})", value=_ticker_field(ticker1, quote1, trends1), inline=True)
        embed.add_field(name=f"{quote2['name']} ({ticker2})", value=_ticker_field(ticker2, quote2, trends2), inline=True)
        embed.set_footer(text="Data: Yahoo Finance, Finnhub")

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Compare(bot))
