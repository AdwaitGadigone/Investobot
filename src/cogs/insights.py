import discord
from discord import app_commands
from discord.ext import commands

from config import HEAVY_COOLDOWN_SECONDS, LIGHT_COOLDOWN_SECONDS
from services import market_data
from services.sentiment import generate_sentiment


class Insights(commands.Cog):
    # Extra market-data commands beyond the core /stock and /rating, grouped together since they were added as a batch.

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="company_overview", description="Company description, sector, and valuation metrics for a ticker"
    )
    @app_commands.checks.cooldown(1, HEAVY_COOLDOWN_SECONDS)
    @app_commands.describe(ticker="Stock ticker symbol, e.g. AAPL")
    async def company_overview(self, interaction: discord.Interaction, ticker: str):
        await interaction.response.defer()
        ticker = ticker.upper().strip()

        try:
            overview = await market_data.get_company_overview(ticker)
        except market_data.TickerNotFoundError:
            await interaction.followup.send(f"`{ticker}` doesn't look like a valid ticker.")
            return
        except market_data.MarketDataTimeoutError:
            await interaction.followup.send("Yahoo Finance is being slow right now, try again in a bit.")
            return

        embed = discord.Embed(title=overview["name"], color=discord.Color.blurple())

        if overview["summary"]:
            # Embed descriptions cap at 4096 characters, business summaries never get close, but stay safe anyway.
            summary = overview["summary"]
            embed.description = summary[:497] + "..." if len(summary) > 500 else summary

        if overview["sector"] or overview["industry"]:
            embed.add_field(
                name="Sector / Industry",
                value=f"{overview['sector'] or 'N/A'} / {overview['industry'] or 'N/A'}",
                inline=False,
            )

        metrics = []
        if overview["pe_ratio"]:
            metrics.append(f"P/E: **{overview['pe_ratio']:.1f}**")
        if overview["forward_pe"]:
            metrics.append(f"Forward P/E: **{overview['forward_pe']:.1f}**")
        if overview["price_to_book"]:
            metrics.append(f"P/B: **{overview['price_to_book']:.1f}**")
        if overview["dividend_yield_pct"]:
            metrics.append(f"Dividend Yield: **{overview['dividend_yield_pct']:.2f}%**")
        if overview["beta"]:
            metrics.append(f"Beta: **{overview['beta']:.2f}**")
        if metrics:
            embed.add_field(name="Valuation", value="\n".join(metrics), inline=False)

        if overview["employees"]:
            embed.add_field(name="Employees", value=f"{overview['employees']:,}", inline=True)
        if overview["website"]:
            embed.add_field(name="Website", value=overview["website"], inline=True)

        embed.set_footer(text="Data from Yahoo Finance")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="fgi", description="Crypto market's Fear & Greed Index right now")
    @app_commands.checks.cooldown(1, LIGHT_COOLDOWN_SECONDS)
    async def fgi(self, interaction: discord.Interaction):
        await interaction.response.defer()

        result = await market_data.get_crypto_fear_greed()
        if not result:
            await interaction.followup.send("Couldn't reach the Fear & Greed Index right now, try again shortly.")
            return

        value = result["value"]
        if value <= 25:
            emoji, color = "😨", discord.Color.red()
        elif value <= 45:
            emoji, color = "😟", discord.Color.orange()
        elif value <= 55:
            emoji, color = "😐", discord.Color.greyple()
        elif value <= 75:
            emoji, color = "🙂", discord.Color.green()
        else:
            emoji, color = "🤑", discord.Color.gold()

        embed = discord.Embed(
            title=f"{emoji} Crypto Fear & Greed Index: {value}/100",
            description=f"Currently **{result['classification']}**.",
            color=color,
        )
        embed.set_footer(text="Data from alternative.me, updates once daily")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="sentiment", description="AI read on current news sentiment for a ticker")
    @app_commands.checks.cooldown(1, HEAVY_COOLDOWN_SECONDS)
    @app_commands.describe(ticker="Stock ticker symbol, e.g. AAPL")
    async def sentiment(self, interaction: discord.Interaction, ticker: str):
        await interaction.response.defer()
        ticker = ticker.upper().strip()

        try:
            await market_data.get_quote(ticker)
        except market_data.TickerNotFoundError:
            await interaction.followup.send(f"`{ticker}` doesn't look like a valid ticker.")
            return
        except market_data.MarketDataTimeoutError:
            await interaction.followup.send("Yahoo Finance is being slow right now, try again in a bit.")
            return

        news = await market_data.get_company_news(ticker, days_back=5)
        if not news:
            await interaction.followup.send(f"No recent news found for **{ticker}** to gauge sentiment from.")
            return

        read = await generate_sentiment(ticker, news)
        if not read:
            await interaction.followup.send("Couldn't generate a sentiment read right now, try again in a bit.")
            return

        embed = discord.Embed(title=f"📰 Sentiment: {ticker}", description=read, color=discord.Color.blurple())
        embed.set_footer(text=f"Based on {min(len(news), 8)} recent headlines, AI-generated, not financial advice")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Insights(bot))
