import discord
from discord import app_commands
from discord.ext import commands

from services import db


class Digest(commands.Cog):
    # /digest lets someone opt in to a personal DM every morning summarizing their watchlist,
    # the actual sending happens on a schedule in cogs/scheduler.py.

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="digest", description="Toggle a daily DM summarizing your watchlist")
    async def digest(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        user_id = interaction.user.id

        if await db.is_digest_enabled(guild_id, user_id):
            await db.disable_digest(guild_id, user_id)
            await interaction.response.send_message("Turned off your daily digest DM.")
            return

        # Sends a test DM before saving anything, so the toggle doesn't claim success for
        # someone whose DMs are actually closed to server members.
        try:
            await interaction.user.send(
                "You're signed up for Investo's daily digest. I'll DM you a summary of "
                "your watchlist here each morning. Turn it off anytime with `/digest`."
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I couldn't DM you, check that your privacy settings allow DMs from server members."
            )
            return

        await db.enable_digest(guild_id, user_id)
        await interaction.response.send_message("Daily digest DM turned on, check your DMs!")


async def setup(bot: commands.Bot):
    await bot.add_cog(Digest(bot))
