import discord
from discord import app_commands
from discord.ext import commands

from config import LIGHT_COOLDOWN_SECONDS, WEBSITE_URL

# Each entry becomes one embed field, grouped by feature instead of one field per command, so the whole list fits in a single embed.
SECTIONS = [
    (
        "📊 Quotes & Charts",
        "`/stock <ticker> [range]` - price, day/52wk range, volume, market cap, a price+volume "
        "chart, and the analyst consensus, all in one embed. `range` picks the chart window: "
        "1 Day, 1 Week, 1 Month (default), 3 Months, 6 Months, Year to Date, 1 Year, or 5 Years.",
    ),
    (
        "📈 Market Movers",
        "`/movers` - today's (or this week's/month's/3 months'/year's/5 years', pick from the "
        "dropdown) top gainers, top losers, and most active stocks, filtered to real, well-known "
        "companies. Toggle between the three with the buttons underneath, same data as the "
        "website's Market Movers panel.",
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
        "`/watchlist add <tickers>` - add one or more tickers to your own list, separate multiple "
        "with commas or spaces, e.g. `AAPL, MSFT, NVDA`\n"
        "`/watchlist remove <ticker>` - take one off\n"
        "`/watchlist list` - show what's on it",
    ),
    (
        "🌐 Server Tracked List (shared by everyone)",
        "`/track add <tickers>` - add one or more tickers to the server-wide list, separate multiple "
        "with commas or spaces, this is what the automatic updates below actually scan\n"
        "`/track remove <ticker>` - stop tracking a ticker\n"
        "`/track list` - show what the server is tracking",
    ),
    (
        "💼 Your Portfolio (shares you actually own)",
        "`/portfolio buy <ticker> <shares> <price>` - log a buy, blends into your average cost "
        "if you already hold some\n"
        "`/portfolio sell <ticker> <shares>` - log a sell, reduces or closes the position\n"
        "`/portfolio remove <ticker>` - fully clear a position\n"
        "`/portfolio view` - see every holding with live profit/loss, plus your totals",
    ),
    (
        "🔔 Alerts & Notifications",
        "`/alert set <ticker> <above/below> <price>` - get DM'd the moment a ticker crosses that price\n"
        "`/alert list` - show your alerts and their ID numbers\n"
        "`/alert remove <alert_id>` - cancel one\n"
        "`/notify` - toggle the Stock Alerts role, anyone with it gets pinged on the automatic updates below\n"
        "`/digest` - toggle a daily DM summarizing your watchlist, sent every morning",
    ),
    (
        "🔁 Automatic Updates",
        "Every 15 minutes, Investo checks the server's tracked list for big price moves (5% or more "
        "since yesterday's close) and posts them to a channel the server owner picked. It also "
        "watches a fixed list of well-known, popular tickers for genuinely wild moves (15% or "
        "more), even if nobody's tracking them, so something like a mega-cap suddenly spiking "
        "still gets caught. Use `/news` any time to check headlines yourself.",
    ),
    (
        "🤖 Ask Me Anything",
        "@ mention the bot with a question about a stock, investing in general, or a follow-up to "
        "something it already said (reply to its message to keep the thread going), and it'll answer "
        "using an AI that pulls in live prices for any ticker you mention.",
    ),
]


def _build_help_embed() -> discord.Embed:
    # One embed, one field per section, this is the entire /help output, no extra clicking needed.
    embed = discord.Embed(
        title="📈 Investo",
        description=f"Everything the bot can do, all in one place. Same data on the web: {WEBSITE_URL}",
        color=discord.Color.blurple(),
    )

    for name, value in SECTIONS:
        embed.add_field(name=name, value=value, inline=False)

    embed.set_footer(text="Data from Yahoo Finance, Finnhub, Alpha Vantage, and Google Gemini")
    return embed


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Shows everything Investo can do")
    @app_commands.checks.cooldown(1, LIGHT_COOLDOWN_SECONDS)
    async def help(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=_build_help_embed())


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
