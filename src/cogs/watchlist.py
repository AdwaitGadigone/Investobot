import discord
from discord import app_commands
from discord.ext import commands

from services import db, market_data


class Watchlist(commands.Cog):
    # /watchlist is a private per-user list, /track is the shared server-wide list the scheduler watches.

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # app_commands.Group turns /watchlist into a parent command with sub-commands like /watchlist add.
    watchlist_group = app_commands.Group(
        name="watchlist", description="Manage your personal stock watchlist"
    )
    track_group = app_commands.Group(
        name="track",
        description="Manage the server's shared tracked-ticker list (used for auto updates)",
    )

    @watchlist_group.command(name="add", description="Add a ticker to your personal watchlist")
    async def watchlist_add(self, interaction: discord.Interaction, ticker: str):
        ticker = ticker.upper().strip()

        try:
            # Sanity check so we don't save a typo that never returns data later.
            await market_data.get_quote(ticker)
        except market_data.TickerNotFoundError:
            await interaction.response.send_message(f"`{ticker}` doesn't look like a valid ticker.")
            return

        # interaction.guild_id and interaction.user.id scope the watchlist to this specific server and person.
        added = await db.add_to_watchlist(interaction.guild_id, interaction.user.id, ticker)
        msg = f"Added **{ticker}** to your watchlist." if added else f"**{ticker}** is already on your watchlist."
        await interaction.response.send_message(msg)

    @watchlist_group.command(name="remove", description="Remove a ticker from your personal watchlist")
    async def watchlist_remove(self, interaction: discord.Interaction, ticker: str):
        ticker = ticker.upper().strip()
        removed = await db.remove_from_watchlist(interaction.guild_id, interaction.user.id, ticker)
        msg = f"Removed **{ticker}**." if removed else f"**{ticker}** wasn't on your watchlist."
        await interaction.response.send_message(msg)

    @watchlist_group.command(name="list", description="Show your personal watchlist")
    async def watchlist_list(self, interaction: discord.Interaction):
        tickers = await db.get_watchlist(interaction.guild_id, interaction.user.id)
        if not tickers:
            await interaction.response.send_message("Your watchlist is empty. Add one with `/watchlist add`.")
            return
        await interaction.response.send_message(f"**Your watchlist:** {', '.join(tickers)}")

    @track_group.command(name="add", description="Add a ticker to the server's shared tracked list")
    async def track_add(self, interaction: discord.Interaction, ticker: str):
        ticker = ticker.upper().strip()

        try:
            await market_data.get_quote(ticker)
        except market_data.TickerNotFoundError:
            await interaction.response.send_message(f"`{ticker}` doesn't look like a valid ticker.")
            return

        # Unlike the watchlist above, this list isn't tied to one user, everyone in the server shares it.
        added = await db.add_tracked(interaction.guild_id, ticker, interaction.user.id)
        if added:
            await interaction.response.send_message(
                f"**{ticker}** is now tracked server-wide, I'll post big moves and news for it."
            )
        else:
            await interaction.response.send_message(f"**{ticker}** is already tracked.")

    @track_group.command(name="remove", description="Remove a ticker from the server's shared tracked list")
    async def track_remove(self, interaction: discord.Interaction, ticker: str):
        ticker = ticker.upper().strip()
        removed = await db.remove_tracked(interaction.guild_id, ticker)
        msg = f"Stopped tracking **{ticker}**." if removed else f"**{ticker}** wasn't being tracked."
        await interaction.response.send_message(msg)

    @track_group.command(name="list", description="Show the server's shared tracked list")
    async def track_list(self, interaction: discord.Interaction):
        tickers = await db.get_tracked(interaction.guild_id)
        if not tickers:
            await interaction.response.send_message("Nothing tracked yet. Add one with `/track add`.")
            return
        await interaction.response.send_message(f"**Server tracked list:** {', '.join(tickers)}")


# This function name and signature are required by discord.py, it's called automatically when the cog loads.
async def setup(bot: commands.Bot):
    await bot.add_cog(Watchlist(bot))
