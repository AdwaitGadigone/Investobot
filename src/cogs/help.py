import discord
from discord import app_commands
from discord.ext import commands

CATEGORIES = [
    {
        "key": "quotes",
        "label": "Quotes & Charts",
        "emoji": "📊",
        "short": "/stock — price, chart, consensus",
        "fields": [
            (
                "/stock <ticker> [range]",
                "Price, day/52wk range, volume, market cap, a price+volume chart, and the analyst "
                "consensus, all in one embed. `range` picks the chart window: 1 Day, 5 Days, 1 Month "
                "(default), 6 Months, 1 Year, or 5 Years.",
            ),
        ],
    },
    {
        "key": "ratings",
        "label": "Analyst Ratings",
        "emoji": "⭐",
        "short": "/rating — the full breakdown plus an AI take",
        "fields": [
            (
                "/rating <ticker>",
                "The full buy/hold/sell breakdown from Wall Street analysts, the average price "
                "target, and an AI-written summary explaining *why* the rating looks the way it "
                "does and what to watch for next. This is Investo's replacement for TipRanks.",
            ),
        ],
    },
    {
        "key": "news",
        "label": "News",
        "emoji": "📰",
        "short": "/news — recent headlines with links",
        "fields": [
            ("/news <ticker>", "The 5 most recent headlines for a ticker, each with a link to read more."),
        ],
    },
    {
        "key": "watchlist",
        "label": "Your Watchlist",
        "emoji": "👀",
        "short": "/watchlist — your own private list",
        "fields": [
            ("/watchlist add <ticker>", "Add a ticker to your own private watchlist, visible only to you."),
            ("/watchlist remove <ticker>", "Take a ticker off your watchlist."),
            ("/watchlist list", "Show everything currently on your watchlist."),
        ],
    },
    {
        "key": "tracked",
        "label": "Server Tracked List",
        "emoji": "🌐",
        "short": "/track — the shared list everyone watches",
        "fields": [
            (
                "/track add <ticker>",
                "Add a ticker to the server-wide list. This is the exact list the automatic "
                "updates below scan for big moves and news.",
            ),
            ("/track remove <ticker>", "Stop tracking a ticker server-wide."),
            ("/track list", "Show everything the server is currently tracking."),
        ],
    },
    {
        "key": "alerts",
        "label": "Alerts & Notifications",
        "emoji": "🔔",
        "short": "/alert and /notify — personal + role pings",
        "fields": [
            ("/alert set <ticker> <above/below> <price>", "Get DM'd the moment a ticker crosses that price."),
            ("/alert list", "Show your active alerts along with their ID numbers."),
            ("/alert remove <alert_id>", "Cancel one of your alerts."),
            (
                "/notify",
                "Toggles the Stock Alerts role for you. Anyone with that role gets pinged whenever "
                "the automatic updates below post something.",
            ),
        ],
    },
    {
        "key": "about",
        "label": "About Investo",
        "emoji": "ℹ️",
        "short": "How the automatic updates work",
        "fields": [
            (
                "Automatic Updates",
                "Every 15 minutes, Investo checks the server's tracked list for big moves (5% or "
                "more since yesterday's close) and fresh news, and posts them to a channel the "
                "server owner picked, pinging the Stock Alerts role if anyone's opted in with /notify.",
            ),
            ("Data Sources", "Yahoo Finance for prices and charts, Finnhub for ratings and news, Alpha Vantage for price targets, Claude for the AI take."),
        ],
    },
]


def _build_home_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📈 Investo",
        description=(
            "A stock tracking bot for the server. Pick a category from the dropdown below to see "
            "exactly what each command does."
        ),
        color=discord.Color.blurple(),
    )
    for cat in CATEGORIES:
        embed.add_field(name=f"{cat['emoji']} {cat['label']}", value=cat["short"], inline=False)
    embed.set_footer(text="Data from Yahoo Finance, Finnhub, and Alpha Vantage")
    return embed


def _build_category_embed(cat: dict) -> discord.Embed:
    embed = discord.Embed(title=f"{cat['emoji']} {cat['label']}", color=discord.Color.blurple())
    for name, value in cat["fields"]:
        field_name = f"`{name}`" if name.startswith("/") else name  # command names get code formatting, plain headers don't
        embed.add_field(name=field_name, value=value, inline=False)
    embed.set_footer(text="Data from Yahoo Finance, Finnhub, and Alpha Vantage")
    return embed


class HelpCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=cat["label"], description=cat["short"], emoji=cat["emoji"], value=cat["key"])
            for cat in CATEGORIES
        ]
        super().__init__(placeholder="Pick a category for more detail...", options=options)

    async def callback(self, interaction: discord.Interaction):
        cat = next(c for c in CATEGORIES if c["key"] == self.values[0])
        await interaction.response.edit_message(embed=_build_category_embed(cat), view=self.view)


class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.message: discord.Message | None = None
        self.add_item(HelpCategorySelect())

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Shows everything Investo can do")
    async def help(self, interaction: discord.Interaction):
        view = HelpView()
        await interaction.response.send_message(embed=_build_home_embed(), view=view)
        view.message = await interaction.original_response()


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
