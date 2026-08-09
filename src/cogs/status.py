import discord
from discord import app_commands
from discord.ext import commands

from config import ALPHA_VANTAGE_API_KEY, FINNHUB_API_KEY, GEMINI_API_KEY, LIGHT_COOLDOWN_SECONDS
from services import db, gemini_limiter, market_data


def _mark(ok: bool) -> str:
    return "🟢 Online" if ok else "🔴 Not responding"


class Status(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="status", description="Check whether Investo and its data sources are responding")
    @app_commands.checks.cooldown(1, LIGHT_COOLDOWN_SECONDS)
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer()

        db_ok = True
        try:
            await db.ping()
        except Exception:
            db_ok = False

        # AAPL always exists, this is purely a "can we reach Yahoo at all right now" check.
        yahoo_ok = True
        try:
            await market_data.get_quote("AAPL")
        except Exception:
            yahoo_ok = False

        rpm_used, rpm_limit, rpd_used, rpd_limit = await gemini_limiter.get_usage()

        embed = discord.Embed(
            title="📡 Investo Status",
            color=discord.Color.green() if (db_ok and yahoo_ok) else discord.Color.orange(),
        )
        embed.add_field(name="Discord", value=f"🟢 {round(self.bot.latency * 1000)}ms", inline=True)
        embed.add_field(name="Database", value=_mark(db_ok), inline=True)
        embed.add_field(name="Yahoo Finance", value=_mark(yahoo_ok), inline=True)
        embed.add_field(name="Finnhub", value="🟢 Configured" if FINNHUB_API_KEY else "⚪ Not set up", inline=True)
        embed.add_field(
            name="Alpha Vantage", value="🟢 Configured" if ALPHA_VANTAGE_API_KEY else "⚪ Not set up", inline=True
        )
        embed.add_field(
            name="Gemini AI",
            value=f"🟢 {rpm_used}/{rpm_limit} per min, {rpd_used}/{rpd_limit} today" if GEMINI_API_KEY else "⚪ Not set up",
            inline=True,
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Status(bot))
