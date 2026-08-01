import discord
from discord import app_commands
from discord.ext import commands

from services import market_data

PERIOD_LABELS = {
    "day": "Today",
    "week": "This Week",
    "month": "This Month",
    "three_month": "Last 3 Months",
    "year": "This Year",
    "five_year": "Last 5 Years",
}

CATEGORY_TITLES = {"gainers": "📈 Top Gainers", "losers": "📉 Top Losers", "active": "🔥 Most Active"}
CATEGORY_COLORS = {
    "gainers": discord.Color.green(),
    "losers": discord.Color.red(),
    "active": discord.Color.blurple(),
}


def _fmt_row(row: dict, index: int) -> str:
    pct = row.get("change_pct")
    change = row.get("change")
    up = pct is not None and pct >= 0
    arrow = "🟢" if up else "🔴"
    pct_str = f"{pct:+.2f}%" if pct is not None else "N/A"
    change_str = f" (${change:+.2f})" if change is not None else ""
    return f"**{index}. {row['ticker']}** — ${row['price']:,.2f}\n{arrow} {pct_str}{change_str} · {row['name']}"


def _build_embed(data: dict, period: str, category: str) -> discord.Embed:
    rows = data.get(period, {}).get(category, [])

    embed = discord.Embed(
        title=CATEGORY_TITLES[category],
        description=(
            "\n\n".join(_fmt_row(r, i + 1) for i, r in enumerate(rows))
            if rows
            else "Not enough data right now, try again shortly."
        ),
        color=CATEGORY_COLORS[category],
    )
    footer = f"{PERIOD_LABELS[period]} · Data: Yahoo Finance (delayed)"
    if category == "active" and period != "day":
        footer += " · Most Active is always today, volume isn't tracked back further yet"
    embed.set_footer(text=footer)
    return embed


class PeriodSelect(discord.ui.Select):
    def __init__(self, current_period: str):
        options = [
            discord.SelectOption(label=label, value=key, default=(key == current_period))
            for key, label in PERIOD_LABELS.items()
        ]
        super().__init__(placeholder="Change the time span...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        view: MoversView = self.view
        view.period = self.values[0]
        for option in self.options:
            option.default = option.value == view.period
        embed = _build_embed(view.data, view.period, view.category)
        await interaction.response.edit_message(embed=embed, view=view)


class MoversView(discord.ui.View):
    def __init__(self, data: dict, period: str = "day", category: str = "gainers"):
        super().__init__(timeout=600)
        self.data = data
        self.period = period
        self.category = category
        self.message: discord.Message | None = None
        self.add_item(PeriodSelect(period))
        self._sync_buttons()

    def _sync_buttons(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.style = (
                    discord.ButtonStyle.primary if child.custom_id == self.category else discord.ButtonStyle.secondary
                )

    async def _switch_category(self, interaction: discord.Interaction, category: str):
        self.category = category
        self._sync_buttons()
        embed = _build_embed(self.data, self.period, self.category)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Top Gainers", style=discord.ButtonStyle.primary, custom_id="gainers", row=1)
    async def gainers_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._switch_category(interaction, "gainers")

    @discord.ui.button(label="Top Losers", style=discord.ButtonStyle.secondary, custom_id="losers", row=1)
    async def losers_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._switch_category(interaction, "losers")

    @discord.ui.button(label="Most Active", style=discord.ButtonStyle.secondary, custom_id="active", row=1)
    async def active_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._switch_category(interaction, "active")

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Movers(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="movers", description="Today's (or this week's/month's/year's) top gainers, losers, and most active"
    )
    async def movers(self, interaction: discord.Interaction):
        # Defer buys 15 minutes since a cold cache means fetching 25 tickers of history.
        await interaction.response.defer()

        data = await market_data.get_market_movers()
        embed = _build_embed(data, "day", "gainers")
        view = MoversView(data, "day", "gainers")
        await interaction.followup.send(embed=embed, view=view)
        view.message = await interaction.original_response()


async def setup(bot: commands.Bot):
    await bot.add_cog(Movers(bot))
