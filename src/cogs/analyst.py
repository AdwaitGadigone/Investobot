import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from services import analyst_take, market_data


class Analyst(commands.Cog):
    # /rating, our replacement for TipRanks since they don't offer a public API individual developers can sign up for.

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="rating", description="Analyst buy/hold/sell breakdown, price target, and an AI take for a ticker"
    )
    @app_commands.describe(ticker="Stock ticker symbol, e.g. AAPL")
    async def rating(self, interaction: discord.Interaction, ticker: str):
        # Defer first since gathering 4 API calls plus an AI summary takes a few seconds.
        await interaction.response.defer()
        ticker = ticker.upper().strip()

        try:
            # These 4 calls are independent, gathering them keeps the wait to the slowest one, not all 4 stacked up.
            quote, trends, target, news = await asyncio.gather(
                market_data.get_quote(ticker),
                market_data.get_recommendation_trends(ticker),
                market_data.get_price_target(ticker),
                market_data.get_company_news(ticker, days_back=5),
            )
        except market_data.TickerNotFoundError:
            await interaction.followup.send(f"`{ticker}` doesn't look like a valid ticker.")
            return

        if not trends and not target:
            await interaction.followup.send(
                "No analyst data available (check the ticker, or FINNHUB_API_KEY isn't set)."
            )
            return

        embed = discord.Embed(title=f"Analyst Ratings for {ticker}", color=discord.Color.blurple())

        if trends:
            # Adds up every analyst opinion so we can show a "Total analysts" count alongside the breakdown.
            total = (
                trends.get("strongBuy", 0)
                + trends.get("buy", 0)
                + trends.get("hold", 0)
                + trends.get("sell", 0)
                + trends.get("strongSell", 0)
            )
            embed.add_field(
                name=f"Recommendations ({trends.get('period', 'latest')})",
                value=(
                    f"Strong Buy: **{trends.get('strongBuy', 0)}**\n"
                    f"Buy: **{trends.get('buy', 0)}**\n"
                    f"Hold: **{trends.get('hold', 0)}**\n"
                    f"Sell: **{trends.get('sell', 0)}**\n"
                    f"Strong Sell: **{trends.get('strongSell', 0)}**\n"
                    f"Total analysts: **{total}**"
                ),
                inline=False,
            )

        target_mean = target.get("targetMean") if target else None
        if target:
            embed.add_field(
                name="Price Targets",
                value=(
                    f"High: **${target.get('targetHigh', 'N/A')}**\n"
                    f"Mean: **${target.get('targetMean', 'N/A')}**\n"
                    f"Median: **${target.get('targetMedian', 'N/A')}**\n"
                    f"Low: **${target.get('targetLow', 'N/A')}**"
                ),
                inline=False,
            )
        else:
            # Finnhub's price target needs a paid plan, fall back to Alpha Vantage's single average figure.
            target_mean = await market_data.get_price_target_average(ticker)
            if target_mean:
                embed.add_field(name="Average Price Target", value=f"${target_mean:,.2f}", inline=False)

        # The one field in the bot written by an actual language model instead of pulled straight from a data source.
        take = await analyst_take.generate_analyst_take(ticker, quote["name"], quote, trends, target_mean, news)
        if take:
            embed.add_field(name="Analyst Take", value=take, inline=False)

        if target and target.get("lastUpdated"):
            embed.set_footer(text=f"Price targets last updated {target['lastUpdated']}, data from Finnhub")
        else:
            embed.set_footer(text="Data from Finnhub" + (" and Alpha Vantage" if target_mean and not target else ""))

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Analyst(bot))
