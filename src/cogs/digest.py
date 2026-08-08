import discord
from discord import app_commands
from discord.ext import commands

from config import LIGHT_COOLDOWN_SECONDS
from services import db


class Digest(commands.Cog):
    # Just the opt-in toggle, the actual daily DM is sent on a schedule in cogs/scheduler.py.

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="digest", description="Toggle your daily DM, or change what it includes")
    @app_commands.checks.cooldown(1, LIGHT_COOLDOWN_SECONDS)
    @app_commands.describe(content="What the daily DM should include, leave blank to just toggle on/off")
    @app_commands.choices(
        content=[
            app_commands.Choice(name="Watchlist", value="watchlist"),
            app_commands.Choice(name="Portfolio", value="portfolio"),
            app_commands.Choice(name="Both", value="both"),
        ]
    )
    async def digest(self, interaction: discord.Interaction, content: app_commands.Choice[str] = None):
        guild_id = interaction.guild_id
        user_id = interaction.user.id
        was_enabled = await db.is_digest_enabled(guild_id, user_id)

        # No content given and already on: bare /digest is the plain off-switch, same as before this option existed.
        if content is None and was_enabled:
            await db.disable_digest(guild_id, user_id)
            await interaction.response.send_message("Turned off your daily digest DM.")
            return

        if not was_enabled:
            # Sends a test DM before saving anything, so this can't claim success for someone whose DMs are closed.
            try:
                await interaction.user.send(
                    "You're signed up for Investo's daily digest. I'll DM you a summary "
                    "here each morning. Turn it off anytime with `/digest`."
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I couldn't DM you, check that your privacy settings allow DMs from server members."
                )
                return
            await db.enable_digest(guild_id, user_id, content.value if content else "watchlist")
            await interaction.response.send_message(
                f"Daily digest turned on ({content.name if content else 'Watchlist'}), check your DMs!"
            )
            return

        # Already on, content given: update the preference in place instead of toggling off.
        await db.set_digest_content(guild_id, user_id, content.value)
        await interaction.response.send_message(f"Daily digest now includes: **{content.name}**.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Digest(bot))
