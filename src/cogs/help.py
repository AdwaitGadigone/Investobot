import discord
from discord import app_commands
from discord.ext import commands

# Each entry becomes one embed field below, grouped by feature instead of one field per command.
# This keeps the whole command list to a single embed instead of a wall of separate fields.
SECTIONS = [
    (
        "📊 Quotes & Charts",
        "`/stock <ticker> [range]` - price, day/52wk range, volume, market cap, a price+volume "
        "chart, and the analyst consensus, all in one embed. `range` picks the chart window: "
        "1 Day, 5 Days, 1 Month (default), 6 Months, 1 Year, or 5 Years.",
    ),
    (
        "⭐ Analyst Ratings",
        "`/rating <ticker>` - the full buy/hold/sell breakdown from Wall Street analysts, the "
        "average price target, and an AI-written take explaining why the rating looks the way it "
        "does. This is Investo's replacement for TipRanks.",
    ),
    (
        "📰 News",
        "`/news <ticker>` - the 5 most recent headlines for a ticker, each with a link to read more.",
    ),
    (
        "👀 Your Watchlist (private, just for you)",
        "`/watchlist add <ticker>` - add a ticker to your own list\n"
        "`/watchlist remove <ticker>` - take one off\n"
        "`/watchlist list` - show what's on it",
    ),
    (
        "🌐 Server Tracked List (shared by everyone)",
        "`/track add <ticker>` - add a ticker to the server-wide list, this is what the automatic "
        "updates below actually scan\n"
        "`/track remove <ticker>` - stop tracking a ticker\n"
        "`/track list` - show what the server is tracking",
    ),
    (
        "🔔 Alerts & Notifications",
        "`/alert set <ticker> <above/below> <price>` - get DM'd the moment a ticker crosses that price\n"
        "`/alert list` - show your alerts and their ID numbers\n"
        "`/alert remove <alert_id>` - cancel one\n"
        "`/notify` - toggle the Stock Alerts role, anyone with it gets pinged on the automatic updates below",
    ),
    (
        "🔁 Automatic Updates",
        "Every 15 minutes, Investo checks the server's tracked list for big moves (5% or more since "
        "yesterday's close) and fresh news, and posts them to a channel the server owner picked.",
    ),
]


def _build_help_embed() -> discord.Embed:
    # One embed, one field per section, this is the entire /help output, no extra clicking needed.
    embed = discord.Embed(
        title="📈 Investo",
        description="Everything the bot can do, all in one place.",
        color=discord.Color.blurple(),
    )

    for name, value in SECTIONS:
        embed.add_field(name=name, value=value, inline=False)

    embed.set_footer(text="Data from Yahoo Finance, Finnhub, Alpha Vantage, and Claude")
    return embed


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Shows everything Investo can do")
    async def help(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=_build_help_embed())


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
