from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from services import market_data


class News(commands.Cog):
    # /news, shows the most recent headlines Finnhub has for a ticker.

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="news", description="Latest headlines for a ticker")
    @app_commands.describe(ticker="Stock ticker symbol, e.g. AAPL")
    async def news(self, interaction: discord.Interaction, ticker: str):
        await interaction.response.defer()
        ticker = ticker.upper().strip()

        articles = await market_data.get_company_news(ticker, days_back=5)
        if not articles:
            await interaction.followup.send(
                f"No recent news found for **{ticker}** (or FINNHUB_API_KEY isn't set)."
            )
            return

        # Finnhub doesn't guarantee newest-first order, so we sort by timestamp ourselves.
        articles = sorted(articles, key=lambda a: a.get("datetime", 0), reverse=True)[:5]

        embed = discord.Embed(title=f"Recent News for {ticker}", color=discord.Color.gold())

        # Each headline becomes its own field in the embed, with a link if one's available.
        for a in articles:
            when = datetime.fromtimestamp(a["datetime"], tz=timezone.utc).strftime("%b %d, %Y")
            headline = a.get("headline", "Untitled")
            url = a.get("url", "")
            source = a.get("source", "Unknown source")

            embed.add_field(
                # Discord field names cap out at 256 characters.
                name=headline[:250],
                value=f"{source}, {when}, [Read more]({url})" if url else f"{source}, {when}",
                inline=False,
            )

        embed.set_footer(text="Data from Finnhub")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(News(bot))
