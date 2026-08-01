import asyncio
import re

import discord
from discord import app_commands
from discord.ext import commands

from services import db, market_data


def _parse_tickers(raw: str, limit: int = 25) -> list[str]:
    # Splits on commas, spaces, or both, so "AAPL, MSFT NVDA" and "AAPL,MSFT,NVDA" both work.
    parts = re.split(r"[,\s]+", raw.strip().upper())
    seen: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return seen[:limit]


async def _validate_tickers(tickers: list[str]) -> tuple[list[str], list[str]]:
    # Checks every ticker at once instead of one at a time, so adding 10 tickers doesn't
    # take 10x as long as adding 1.
    results = await asyncio.gather(
        *(market_data.get_quote(t) for t in tickers), return_exceptions=True
    )

    valid, invalid = [], []
    for ticker, result in zip(tickers, results):
        if isinstance(result, Exception):
            invalid.append(ticker)
        else:
            valid.append(ticker)
    return valid, invalid


class Watchlist(commands.Cog):
    # /watchlist is a private per-user list, /track is the shared server-wide list the scheduler watches.

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # app_commands.Group lets us make "slash command with subcommands", so instead of one
    # giant /watchlist command with a million options, people get /watchlist add, etc.
    watchlist_group = app_commands.Group(
        name="watchlist", description="Manage your personal stock watchlist"
    )
    track_group = app_commands.Group(
        name="track",
        description="Manage the server's shared tracked-ticker list (used for auto updates)",
    )

    @watchlist_group.command(name="add", description="Add one or more tickers to your personal watchlist")
    @app_commands.describe(tickers="One or more tickers, separated by commas or spaces, e.g. AAPL, MSFT, NVDA")
    async def watchlist_add(self, interaction: discord.Interaction, tickers: str):
        await interaction.response.defer()
        requested = _parse_tickers(tickers)
        if not requested:
            await interaction.followup.send("Didn't catch any tickers in that, try something like `AAPL, MSFT, NVDA`.")
            return

        valid, invalid = await _validate_tickers(requested)

        added, already = [], []
        for ticker in valid:
            # interaction.guild_id and interaction.user.id scope the watchlist to this server and person.
            if await db.add_to_watchlist(interaction.guild_id, interaction.user.id, ticker):
                added.append(ticker)
            else:
                already.append(ticker)

        lines = []
        if added:
            lines.append(f"Added **{', '.join(added)}** to your watchlist.")
        if already:
            lines.append(f"Already on your watchlist: {', '.join(already)}")
        if invalid:
            lines.append(f"Not valid tickers, skipped: {', '.join(invalid)}")
        await interaction.followup.send("\n".join(lines))

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

    @track_group.command(name="add", description="Add one or more tickers to the server's shared tracked list")
    @app_commands.describe(tickers="One or more tickers, separated by commas or spaces, e.g. AAPL, MSFT, NVDA")
    async def track_add(self, interaction: discord.Interaction, tickers: str):
        await interaction.response.defer()
        requested = _parse_tickers(tickers)
        if not requested:
            await interaction.followup.send("Didn't catch any tickers in that, try something like `AAPL, MSFT, NVDA`.")
            return

        valid, invalid = await _validate_tickers(requested)

        # Unlike the watchlist above, this list isn't tied to one user, everyone in the server shares it.
        added, already = [], []
        for ticker in valid:
            if await db.add_tracked(interaction.guild_id, ticker, interaction.user.id):
                added.append(ticker)
            else:
                already.append(ticker)

        lines = []
        if added:
            lines.append(f"Now tracking **{', '.join(added)}**, I'll post big moves for them.")
        if already:
            lines.append(f"Already tracked: {', '.join(already)}")
        if invalid:
            lines.append(f"Not valid tickers, skipped: {', '.join(invalid)}")
        await interaction.followup.send("\n".join(lines))

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
